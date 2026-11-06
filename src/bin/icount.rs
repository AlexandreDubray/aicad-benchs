//! Runs one function of one shape in a loop, for `tools/icount.py` to count its instructions
//! under valgrind.
//!
//! `icount --info` prints the build of `aicad` it was linked with (see `aicad::version`) as JSON.
//!
//! `icount <catalogue> <shape id> <function> <loops>` builds the workload, then calls the
//! function over every input `loops` times (0 loops measures the setup alone). Prints the number
//! of calls per loop, so that the caller can divide.

use aicad::modelling::ConstraintProperty;
use aicad_benchs::instance::build;
use aicad_benchs::shape::load;
use aicad_benchs::workload::{Config, Workload};
use std::hash::{DefaultHasher, Hasher};
use std::hint::black_box;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() == 2 && args[1] == "--info" {
        println!("{}", aicad::version().to_json());
        return;
    }
    if args.len() != 5 {
        eprintln!("usage: icount <catalogue> <shape id> <function> <loops>");
        std::process::exit(2);
    }
    let id: usize = args[2].parse().expect("shape id must be an integer");
    let loops: usize = args[4].parse().expect("loops must be an integer");
    let shapes = load(&args[1]).unwrap_or_else(|e| panic!("{e}"));
    let shape = shapes.iter().find(|s| s.id == id).unwrap_or_else(|| panic!("no shape {id} in {}", args[1]));
    let instance = build(shape).unwrap_or_else(|e| panic!("cannot build {}: {e}", shape.label()));
    let w = Workload::build(&instance, &Config::default());
    let con = &instance.constraint;
    let calls = match args[3].as_str() {
        "update" => {
            for _ in 0..loops {
                for e in &w.edges {
                    let mut p = con.identity_property();
                    p.update(&*e.parent, e.value, e.in_scope);
                    black_box(&p);
                }
            }
            w.edges.len()
        }
        "update_backward" => {
            for _ in 0..loops {
                for e in &w.edges {
                    let mut p = con.identity_property();
                    p.update_backward(&*e.child, e.value, e.in_scope);
                    black_box(&p);
                }
            }
            w.edges.len()
        }
        "is_assignment_invalid" => {
            for _ in 0..loops {
                for e in &w.edges {
                    black_box(con.is_assignment_invalid(&*e.parent, &*e.child, e.layer, e.value));
                }
            }
            w.edges.len()
        }
        "merge" => {
            for _ in 0..loops {
                for (a, o) in &w.merge_pairs {
                    let mut m = a.clone();
                    m.merge(&**o);
                    black_box(&m);
                }
            }
            w.merge_pairs.len()
        }
        "eq" => {
            for _ in 0..loops {
                for (a, o) in &w.eq_pairs {
                    black_box(ConstraintProperty::eq(&**a, &**o));
                }
            }
            w.eq_pairs.len()
        }
        "hash" => {
            for _ in 0..loops {
                for s in &w.states {
                    let mut h = DefaultHasher::new();
                    ConstraintProperty::hash(&**s, &mut h);
                    black_box(h.finish());
                }
            }
            w.states.len()
        }
        "order_key" => {
            for _ in 0..loops {
                for s in &w.states {
                    black_box(s.order_key());
                }
            }
            w.states.len()
        }
        "is_satisfied" => {
            for _ in 0..loops {
                for a in &w.assignments {
                    black_box(con.is_satisfied(a));
                }
            }
            w.assignments.len()
        }
        other => {
            eprintln!("unknown function `{other}`");
            std::process::exit(2);
        }
    };
    println!("{calls}");
}
