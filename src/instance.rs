//! Builds a real `aicad` constraint from a [`Shape`].

use crate::shape::Shape;
use crate::util::Rng;
use aicad::constraints as builders;
use aicad::modelling::{Constraint, ConstraintIndex, Problem, VariableIndex};
use serde_json::Value;
use std::panic::{AssertUnwindSafe, catch_unwind};

/// A constraint of a shape, ready to produce properties: its variables are `0..arity` in layer
/// order and its ordering is set.
pub struct Instance {
    /// The problem that holds the variables (kept alive for `structural_key`).
    pub problem: Problem,
    /// The constraint, with its variable ordering set.
    pub constraint: Box<dyn Constraint + Send + Sync>,
    /// Number of layers of the scope.
    pub arity: usize,
    /// The values that every variable can take.
    pub domain: Vec<isize>,
    /// Complete assignments that satisfy the constraint, if the shape lists some (the tuples of a
    /// table), and an empty list otherwise. The workload walks along them so that the states it
    /// builds are not all the empty state that uniformly random values lead to.
    pub guides: Vec<Vec<isize>>,
    /// True if the scope is a sequence of distinct values (the order of the jobs of a
    /// disjunctive): the workload then draws permutations of the domain instead of independent
    /// values, which would repeat a job within a few layers.
    pub permutation: bool,
}

fn ints(v: &Value, what: &str) -> Result<Vec<isize>, String> {
    v.as_array()
        .ok_or_else(|| format!("`{what}` is not a list"))?
        .iter()
        .map(|x| x.as_i64().map(|i| i as isize).ok_or_else(|| format!("`{what}` holds a non-integer")))
        .collect()
}

fn uint(v: &Value, what: &str) -> Result<usize, String> {
    v.as_u64().map(|i| i as usize).ok_or_else(|| format!("`{what}` is not a non-negative integer"))
}

/// A synthetic table never has more tuples than this: the table of a very large real constraint
/// would take most of the benchmark's memory and time to build.
const SYNTHETIC_TUPLES: usize = 20_000;

/// The tuples of a `table` shape: read from `<catalogue>/tables/<hash>.json` for a real table,
/// drawn at random (seeded by the hash, so always the same) for a synthetic one.
fn table_tuples(shape: &Shape, domain: &[isize]) -> Result<Vec<Vec<isize>>, String> {
    let p = &shape.params;
    let hash = p["hash"].as_str().ok_or("table: no hash")?;
    if hash.is_empty() || !hash.chars().all(|c| c.is_ascii_alphanumeric()) {
        return Err(format!("table: invalid hash `{hash}`"));
    }
    if p["synthetic"].as_bool() != Some(true) {
        let path = shape.base_dir.join("tables").join(format!("{hash}.json"));
        let text = std::fs::read_to_string(&path)
            .map_err(|e| format!("table: cannot read {}: {e}", path.display()))?;
        let rows: Vec<Vec<isize>> = serde_json::from_str(&text)
            .map_err(|e| format!("table: {} is not a list of tuples: {e}", path.display()))?;
        if rows.iter().any(|r| r.len() != shape.arity) {
            return Err(format!("table: {} has a tuple that is not of arity {}", path.display(), shape.arity));
        }
        return Ok(rows);
    }
    // Distinct tuples drawn uniformly: little structure to share, so a table diagram of about the
    // size of the table (the worst case for the constraint).
    let wanted = uint(&p["ntuples"], "ntuples")?.min(SYNTHETIC_TUPLES);
    let capacity = (domain.len() as f64).powi(shape.arity as i32);
    let wanted = wanted.min(capacity.min(SYNTHETIC_TUPLES as f64) as usize);
    let mut rng = Rng::new(u64::from_str_radix(&hash[..hash.len().min(15)], 16).unwrap_or(1));
    let mut seen = rustc_hash::FxHashSet::default();
    let mut rows = Vec::with_capacity(wanted);
    let mut attempts = 0;
    while rows.len() < wanted && attempts < wanted * 50 + 100 {
        attempts += 1;
        let row: Vec<isize> = (0..shape.arity).map(|_| domain[rng.below(domain.len())]).collect();
        if seen.insert(row.clone()) {
            rows.push(row);
        }
    }
    Ok(rows)
}

/// True if processing `order` (job numbers, each once), every job as early as possible after its
/// release date, ends every job by its deadline.
fn meets_deadlines(order: &[isize], release: &[isize], deadline: &[isize], duration: &[isize]) -> bool {
    let mut end = isize::MIN;
    for &job in order {
        let j = job as usize;
        end = end.max(release[j]) + duration[j];
        if end > deadline[j] {
            return false;
        }
    }
    true
}

/// How many neighbours of the reference order a disjunctive shape adds to its guides.
const DISJUNCTIVE_GUIDES: usize = 8;

/// The guides of a `disjunctive` shape: the reference order of the catalogue, which meets every
/// deadline, and a few orders reached from it by swapping neighbours while the deadlines are still
/// met (seeded by the shape's key, so always the same). On a tight shape no swap is feasible and
/// they are all the reference order.
fn disjunctive_guides(shape: &Shape, release: &[isize], deadline: &[isize], duration: &[isize]) -> Result<Vec<Vec<isize>>, String> {
    let n = shape.arity;
    let mut order = ints(&shape.params["order"], "order")?;
    let mut sorted = order.clone();
    sorted.sort_unstable();
    if sorted != (0..n as isize).collect::<Vec<_>>() {
        return Err("disjunctive: `order` is not a permutation of the jobs".into());
    }
    if !meets_deadlines(&order, release, deadline, duration) {
        return Err("disjunctive: the reference `order` misses a deadline".into());
    }
    let seed = shape.key.get(..15.min(shape.key.len())).and_then(|k| u64::from_str_radix(k, 16).ok()).unwrap_or(1);
    let mut rng = Rng::new(seed);
    let mut guides = vec![order.clone()];
    for step in 0..DISJUNCTIVE_GUIDES * 50 {
        let i = rng.below(n - 1);
        order.swap(i, i + 1);
        if !meets_deadlines(&order, release, deadline, duration) {
            order.swap(i, i + 1);
        }
        if step % 50 == 49 {
            guides.push(order.clone());
        }
    }
    Ok(guides)
}

/// Builds the constraint of `shape`. Returns a message instead of panicking when the shape cannot
/// be built (an unsupported kind, a malformed parameter, a constructor guard).
pub fn build(shape: &Shape) -> Result<Instance, String> {
    if shape.arity < 2 {
        return Err("arity under 2".into());
    }
    let mut domain: Vec<isize> = (0..shape.u as isize).map(|k| shape.lo + k).collect();
    let p = &shape.params;
    // Regular reads its alphabet from the domains: use the symbols of its automaton.
    if shape.kind == "regular" {
        domain = ints(&p["automaton"]["symbols"], "automaton.symbols")?;
        if domain.is_empty() {
            return Err("empty alphabet".into());
        }
    }
    let mut guides: Vec<Vec<isize>> = Vec::new();
    let mut permutation = false;
    let mut problem = Problem::default();
    let vars: Vec<VariableIndex> = problem.add_variables(shape.arity, domain.clone());
    let made = catch_unwind(AssertUnwindSafe(|| -> Result<ConstraintIndex, String> {
        let vars = vars.clone();
        Ok(match shape.kind.as_str() {
            "all_different" => builders::all_different(&mut problem, vars),
            "not_equal" => {
                if shape.arity != 2 {
                    return Err("not_equal needs arity 2".into());
                }
                builders::not_equals(&mut problem, vars[0], vars[1])
            }
            "sum" => {
                let target = p["target"].as_i64().ok_or("sum: no target")? as isize;
                builders::sum(&mut problem, vars, target)
            }
            "among" => {
                let (lb, ub) = (uint(&p["lb"], "lb")?, uint(&p["ub"], "ub")?);
                if lb > ub || lb > shape.arity {
                    return Err(format!("among: unsatisfiable bounds {lb}..{ub}"));
                }
                builders::among(&mut problem, vars, ints(&p["values"], "values")?, lb, ub.min(shape.arity))
            }
            "at_least" => {
                let lb = uint(&p["lb"], "lb")?;
                if lb > shape.arity {
                    return Err(format!("at_least: lb {lb} above the arity"));
                }
                builders::at_least(&mut problem, vars, ints(&p["values"], "values")?, lb)
            }
            "gcc" => {
                let values = ints(&p["values"], "values")?;
                let bounds = p["bounds"].as_array().ok_or("gcc: no bounds")?;
                if values.len() != bounds.len() {
                    return Err("gcc: values and bounds differ in length".into());
                }
                let mut seen = rustc_hash::FxHashSet::default();
                let mut list = Vec::new();
                for (v, b) in values.iter().zip(bounds) {
                    let (lo, hi) = (uint(&b[0], "bound")?, uint(&b[1], "bound")?);
                    if lo > hi {
                        return Err("gcc: lo above hi".into());
                    }
                    if seen.insert(*v) {
                        list.push((*v, lo, hi));
                    }
                }
                builders::gcc(&mut problem, vars, list)
            }
            "table" => {
                let tuples = table_tuples(shape, &domain)?;
                if tuples.is_empty() {
                    return Err("table: no tuple".into());
                }
                guides = tuples.clone();
                builders::table(&mut problem, vars, tuples)
            }
            "disjunctive" => {
                if shape.u != shape.arity || shape.lo != 0 {
                    return Err("disjunctive: the domain must be the job numbers 0..arity".into());
                }
                if p["precedences"].as_array().is_some_and(|l| !l.is_empty()) {
                    return Err("disjunctive: precedences are not supported by this benchmark yet".into());
                }
                let (release, deadline, duration) =
                    (ints(&p["release"], "release")?, ints(&p["deadline"], "deadline")?, ints(&p["duration"], "duration")?);
                if [release.len(), deadline.len(), duration.len()].iter().any(|&l| l != shape.arity) {
                    return Err("disjunctive: release, deadline and duration need one entry per job".into());
                }
                if duration.iter().any(|&d| d < 0) {
                    return Err("disjunctive: negative duration".into());
                }
                guides = disjunctive_guides(shape, &release, &deadline, &duration)?;
                permutation = true;
                builders::disjunctive(&mut problem, vars, release, deadline, duration)
            }
            "regular" => {
                let a = &p["automaton"];
                if a["deterministic"].as_bool() != Some(true) {
                    return Err("regular: automaton is not deterministic".into());
                }
                let table = a["table"].as_array().ok_or("regular: no table")?;
                let rows: Vec<Vec<Option<usize>>> = table
                    .iter()
                    .map(|r| r.as_array().map(|r| r.iter().map(|c| c.as_u64().map(|c| c as usize)).collect()))
                    .collect::<Option<_>>()
                    .ok_or("regular: malformed table")?;
                let finals: Vec<usize> = ints(&a["final"], "final")?.into_iter().map(|f| f as usize).collect();
                builders::regular(&mut problem, vars, rows, uint(&a["start"], "start")?, finals)
            }
            other => return Err(format!("unsupported kind `{other}`")),
        })
    }));
    let index = match made {
        Ok(r) => r?,
        Err(_) => return Err("the aicad constructor rejected the shape".into()),
    };
    let mut constraint = problem[index].clone();
    constraint.update_variable_ordering(&(0..shape.arity).map(VariableIndex).collect::<Vec<_>>());
    Ok(Instance { problem, constraint, arity: shape.arity, domain, guides, permutation })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::path::PathBuf;

    fn disjunctive(release: &[i64], deadline: &[i64], duration: &[i64], order: &[i64]) -> Shape {
        let n = release.len();
        Shape {
            id: 0,
            key: "0123456789".into(),
            kind: "disjunctive".into(),
            supported: true,
            arity: n,
            u: n,
            lo: 0,
            params: json!({"release": release, "deadline": deadline, "duration": duration, "order": order}),
            base_dir: PathBuf::new(),
        }
    }

    #[test]
    fn the_guides_of_a_disjunctive_satisfy_it() {
        // Four jobs with wide windows: several orders are feasible.
        let shape = disjunctive(&[0, 0, 0, 0], &[40, 40, 40, 40], &[3, 2, 4, 2], &[0, 1, 2, 3]);
        let instance = build(&shape).expect("the shape builds");
        assert!(instance.permutation);
        assert_eq!(instance.guides.len(), DISJUNCTIVE_GUIDES + 1);
        for guide in &instance.guides {
            assert!(instance.constraint.is_satisfied(guide), "{guide:?} should satisfy the constraint");
        }
        // Wide windows let the neighbour swaps move the order away from the reference.
        assert!(instance.guides.iter().any(|g| g != &instance.guides[0]));
    }

    #[test]
    fn a_tight_disjunctive_keeps_the_reference_order() {
        // Each job fits only in its own slot, so no neighbour swap is feasible.
        let shape = disjunctive(&[0, 2, 5], &[2, 5, 6], &[2, 3, 1], &[0, 1, 2]);
        let instance = build(&shape).expect("the shape builds");
        assert!(instance.guides.iter().all(|g| g == &vec![0, 1, 2]));
    }

    #[test]
    fn a_reference_order_that_misses_a_deadline_is_rejected() {
        let shape = disjunctive(&[0, 0], &[3, 3], &[2, 2], &[0, 1]);
        assert!(build(&shape).is_err());
    }

    #[test]
    fn a_disjunctive_with_precedences_is_rejected_until_aicad_supports_them() {
        let mut shape = disjunctive(&[0, 0], &[9, 9], &[2, 2], &[0, 1]);
        shape.params["precedences"] = json!([[0, 1]]);
        assert!(build(&shape).is_err());
    }
}
