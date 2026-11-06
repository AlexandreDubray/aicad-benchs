//! The inputs of every benchmarked function, drawn from seeded random paths.
//!
//! A path picks a value of the domain at every layer. Folding `update` from the root along a path
//! gives the top-down property of each node it crosses; folding `update_backward` from the sink
//! gives the bottom-up ones. Everything goes through the public traits, so the workload needs no
//! knowledge of the constraint, except that a shape may list assignments that satisfy it
//! ([`Instance::guides`], the tuples of a table) for the paths to follow.
//!
//! To bound memory, only [`Config::layers`] layers are kept (evenly spaced), with
//! [`Config::paths`] paths each, however large the arity.

use crate::instance::Instance;
use crate::util::Rng;
use aicad::modelling::ConstraintProperty;

type Prop = Box<dyn ConstraintProperty>;

/// How much data a workload holds.
#[derive(Clone, Copy, Debug)]
pub struct Config {
    /// Seed of the paths; the same seed gives the same workload.
    pub seed: u64,
    /// Random paths drawn per instance.
    pub paths: usize,
    /// Layers kept (all of them if the arity is smaller).
    pub layers: usize,
    /// Number of properties joined to make a merged state.
    pub merge_width: usize,
    /// Random complete assignments for `is_satisfied`.
    pub assignments: usize,
}

impl Default for Config {
    fn default() -> Self {
        Config { seed: 0x41_49_43_41_44, paths: 16, layers: 64, merge_width: 4, assignments: 32 }
    }
}

/// One edge of a path at a kept layer: the property above it, the property below it, its value.
pub struct Edge {
    /// Layer of the edge.
    pub layer: usize,
    /// Value of the edge.
    pub value: isize,
    /// Whether the layer is in the scope of the constraint.
    pub in_scope: bool,
    /// Top-down property of the parent.
    pub parent: Prop,
    /// Bottom-up property of the child.
    pub child: Prop,
}

/// All the inputs for one instance.
pub struct Workload {
    /// Edges, for `update`, `update_backward` and `is_assignment_invalid`.
    pub edges: Vec<Edge>,
    /// Pairs of properties of the same layer, for `merge`.
    pub merge_pairs: Vec<(Prop, Prop)>,
    /// Pairs for `eq`: half are different paths, half a property against its own copy.
    pub eq_pairs: Vec<(Prop, Prop)>,
    /// Top-down, bottom-up and merged properties, for `hash`, `order_key` and `is_single_state`.
    pub states: Vec<Prop>,
    /// Complete assignments, for `is_satisfied`.
    pub assignments: Vec<Vec<isize>>,
}

/// The kept layers: all of them, or `count` evenly spaced from the first to the last.
fn kept_layers(arity: usize, count: usize) -> Vec<usize> {
    if arity <= count {
        return (0..arity).collect();
    }
    (0..count).map(|i| i * (arity - 1) / (count - 1).max(1)).collect()
}

impl Workload {
    /// Draws the workload of `instance`. Panics if `config` asks for no path or no layer.
    pub fn build(instance: &Instance, config: &Config) -> Workload {
        assert!(config.paths > 0 && config.layers > 0, "a workload needs a path and a layer");
        let c = &instance.constraint;
        let n = instance.arity;
        let mut rng = Rng::new(config.seed ^ (n as u64).wrapping_mul(0x9E37_79B9));
        let kept = kept_layers(n, config.layers);
        let mut is_kept = vec![usize::MAX; n + 1];
        for (k, &l) in kept.iter().enumerate() {
            is_kept[l] = k;
        }
        // down[k][path]: property above layer kept[k]; up[k][path]: property below it.
        let mut down: Vec<Vec<Prop>> = (0..kept.len()).map(|_| Vec::new()).collect();
        let mut up: Vec<Vec<Prop>> = (0..kept.len()).map(|_| Vec::new()).collect();
        let mut values = vec![vec![0isize; n]; config.paths];
        for path in values.iter_mut() {
            for v in path.iter_mut() {
                *v = instance.domain[rng.below(instance.domain.len())];
            }
        }
        // With guides (the tuples of a table), uniformly random values leave the table at the
        // first layer and every state is empty. A third of the paths follow a tuple up to a random
        // layer and continue at random (their top-down states are populated until they leave the
        // table), a third are random up to a layer and then follow a tuple (their bottom-up states
        // are), and the rest are random.
        if !instance.guides.is_empty() {
            for (k, path) in values.iter_mut().enumerate() {
                let guide = &instance.guides[rng.below(instance.guides.len())];
                let cut = rng.below(n + 1);
                match k % 3 {
                    0 => path[..cut].copy_from_slice(&guide[..cut]),
                    1 => path[cut..].copy_from_slice(&guide[cut..]),
                    _ => {}
                }
            }
        }
        for path in &values {
            let mut p = c.empty_property();
            for layer in 0..n {
                if is_kept[layer] != usize::MAX {
                    down[is_kept[layer]].push(p.clone());
                }
                let mut next = c.identity_property();
                next.update(&*p, path[layer], c.is_layer_in_scope(layer));
                p = next;
            }
            let mut q = c.empty_property_backward();
            for layer in (0..n).rev() {
                if is_kept[layer] != usize::MAX {
                    up[is_kept[layer]].push(q.clone());
                }
                let mut next = c.identity_property();
                next.update_backward(&*q, path[layer], c.is_layer_in_scope(layer));
                q = next;
            }
        }
        // `up[k]` holds the property below layer kept[k]'s *edge*, i.e. of the child; the loop
        // above stores it before absorbing the edge at `layer`, so it is the child's.
        let mut edges = Vec::new();
        let mut merge_pairs = Vec::new();
        let mut eq_pairs = Vec::new();
        let mut states: Vec<Prop> = Vec::new();
        for (k, &layer) in kept.iter().enumerate() {
            let in_scope = c.is_layer_in_scope(layer);
            for path in 0..config.paths {
                edges.push(Edge {
                    layer,
                    value: values[path][layer],
                    in_scope,
                    parent: down[k][path].clone(),
                    child: up[k][path].clone(),
                });
                let other = (path + 1) % config.paths;
                merge_pairs.push((down[k][path].clone(), down[k][other].clone()));
                eq_pairs.push(if path % 2 == 0 {
                    (down[k][path].clone(), down[k][other].clone())
                } else {
                    (down[k][path].clone(), down[k][path].clone())
                });
                states.push(down[k][path].clone());
                states.push(up[k][path].clone());
            }
            for first in (0..config.paths).step_by(config.merge_width.max(1)) {
                let mut merged = down[k][first].clone();
                for other in 1..config.merge_width {
                    merged.merge(&*down[k][(first + other) % config.paths]);
                }
                states.push(merged);
            }
        }
        let assignments = (0..config.assignments)
            .map(|k| {
                if !instance.guides.is_empty() && k % 2 == 0 {
                    return instance.guides[rng.below(instance.guides.len())].clone();
                }
                (0..n).map(|_| instance.domain[rng.below(instance.domain.len())]).collect()
            })
            .collect();
        Workload { edges, merge_pairs, eq_pairs, states, assignments }
    }
}
