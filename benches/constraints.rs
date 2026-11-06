//! Criterion benchmarks of the constraint functions, one group per shape.
//!
//! Environment variables:
//! - `BENCH_CATALOGUE`: catalogue to read (default `catalogue/ci_shapes.json`).
//! - `BENCH_KIND`: only shapes of this kind.
//! - `BENCH_QUICK=1`: a short measurement, for a smoke test.
//!
//! Each benchmark runs a function over every input of the workload and reports the time per
//! call (criterion's throughput is in elements). `update(+identity)` includes the allocation of
//! the fresh property that the compiler also makes; `clone+merge` includes the clone, and
//! `clone` alone is reported so that it can be subtracted.

use aicad::modelling::ConstraintProperty;
use aicad_benchs::instance::build;
use aicad_benchs::shape::load;
use aicad_benchs::workload::{Config, Workload};
use criterion::{BenchmarkId, Criterion, Throughput, criterion_group, criterion_main};
use std::hash::{DefaultHasher, Hasher};
use std::hint::black_box;
use std::time::Duration;

fn benches(c: &mut Criterion) {
    let path = std::env::var("BENCH_CATALOGUE").unwrap_or_else(|_| format!("{}/catalogue/ci_shapes.json", env!("CARGO_MANIFEST_DIR")));
    let kind = std::env::var("BENCH_KIND").ok();
    let quick = std::env::var("BENCH_QUICK").is_ok();
    let shapes = load(&path).unwrap_or_else(|e| panic!("{e}"));
    for shape in shapes.iter().filter(|s| s.supported && kind.as_ref().is_none_or(|k| *k == s.kind)) {
        let instance = match build(shape) {
            Ok(i) => i,
            Err(e) => {
                eprintln!("skip {}: {e}", shape.label());
                continue;
            }
        };
        let w = Workload::build(&instance, &Config::default());
        let con = &instance.constraint;
        let mut g = c.benchmark_group(shape.label());
        if quick {
            g.warm_up_time(Duration::from_millis(50)).measurement_time(Duration::from_millis(100)).sample_size(10);
        } else {
            g.warm_up_time(Duration::from_millis(300)).measurement_time(Duration::from_secs(1)).sample_size(20);
        }

        g.throughput(Throughput::Elements(1));
        g.bench_function("identity_property", |b| b.iter(|| black_box(con.identity_property())));
        g.bench_function("structural_key", |b| b.iter(|| black_box(con.structural_key(&instance.problem))));

        g.throughput(Throughput::Elements(w.edges.len() as u64));
        g.bench_with_input(BenchmarkId::from_parameter("update(+identity)"), &w.edges, |b, edges| {
            b.iter(|| {
                for e in edges {
                    let mut p = con.identity_property();
                    p.update(&*e.parent, e.value, e.in_scope);
                    black_box(&p);
                }
            })
        });
        g.bench_with_input(BenchmarkId::from_parameter("update_backward(+identity)"), &w.edges, |b, edges| {
            b.iter(|| {
                for e in edges {
                    let mut p = con.identity_property();
                    p.update_backward(&*e.child, e.value, e.in_scope);
                    black_box(&p);
                }
            })
        });
        g.bench_with_input(BenchmarkId::from_parameter("is_assignment_invalid"), &w.edges, |b, edges| {
            b.iter(|| {
                for e in edges {
                    black_box(con.is_assignment_invalid(&*e.parent, &*e.child, e.layer, e.value));
                }
            })
        });

        g.throughput(Throughput::Elements(w.merge_pairs.len() as u64));
        g.bench_with_input(BenchmarkId::from_parameter("clone"), &w.merge_pairs, |b, pairs| {
            b.iter(|| {
                for (a, _) in pairs {
                    black_box(a.clone());
                }
            })
        });
        g.bench_with_input(BenchmarkId::from_parameter("clone+merge"), &w.merge_pairs, |b, pairs| {
            b.iter(|| {
                for (a, o) in pairs {
                    let mut m = a.clone();
                    m.merge(&**o);
                    black_box(&m);
                }
            })
        });

        g.throughput(Throughput::Elements(w.eq_pairs.len() as u64));
        g.bench_with_input(BenchmarkId::from_parameter("eq"), &w.eq_pairs, |b, pairs| {
            b.iter(|| {
                for (a, o) in pairs {
                    black_box(ConstraintProperty::eq(&**a, &**o));
                }
            })
        });

        g.throughput(Throughput::Elements(w.states.len() as u64));
        g.bench_with_input(BenchmarkId::from_parameter("hash"), &w.states, |b, states| {
            b.iter(|| {
                for s in states {
                    let mut h = DefaultHasher::new();
                    ConstraintProperty::hash(&**s, &mut h);
                    black_box(h.finish());
                }
            })
        });
        g.bench_with_input(BenchmarkId::from_parameter("order_key"), &w.states, |b, states| {
            b.iter(|| {
                for s in states {
                    black_box(s.order_key());
                }
            })
        });
        g.bench_with_input(BenchmarkId::from_parameter("is_single_state"), &w.states, |b, states| {
            b.iter(|| {
                for s in states {
                    black_box(s.is_single_state());
                }
            })
        });

        g.throughput(Throughput::Elements(w.assignments.len() as u64));
        g.bench_with_input(BenchmarkId::from_parameter("is_satisfied"), &w.assignments, |b, asg| {
            b.iter(|| {
                for a in asg {
                    black_box(con.is_satisfied(a));
                }
            })
        });
        g.finish();
    }
}

criterion_group!(constraints, benches);
criterion_main!(constraints);
