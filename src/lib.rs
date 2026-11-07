//! Micro-benchmarks of the `aicad` constraint functions, on shapes seen in the XCSP3 benchmarks
//! (and, for the disjunctive constraint, in the RCPSP instances of PSPLIB).
//!
//! # What is measured
//!
//! Every constraint of `aicad` is a [`Constraint`](aicad::modelling::Constraint) that produces
//! [`ConstraintProperty`](aicad::modelling::ConstraintProperty) values: the states that the MDD
//! compiler folds along the edges, merges, compares and hashes. These functions run millions of
//! times per compilation, so their cost is what a regression first shows in. This crate times
//! each of them in isolation, for the constraints of the catalogue.
//!
//! # How it works
//!
//! 1. [`shape`] reads the catalogue of `catalogue/*.json` (see the README to rebuild it): the
//!    kind, arity, domain and parameters of constraints extracted from the XCSP3 instances, or the
//!    jobs of a unary resource extracted from a PSPLIB project.
//! 2. [`instance`] builds a real `aicad` constraint of that shape on a synthetic problem.
//! 3. [`workload`] draws seeded random paths through the layers of the constraint and folds
//!    `update` (top-down) and `update_backward` (bottom-up) along them, using only the public
//!    trait API. The properties met on the way, and the merges of some of them, are the
//!    inputs of every function. The same seed gives the same inputs, so two commits are
//!    compared on the same data.
//!
//! # Design notes
//!
//! Random paths are not the states of a real compilation: they are spread out where a diagram
//! concentrates states, and they never come from a width-limited merge. They exercise the
//! code paths of each function with realistic sizes, which is what a regression benchmark needs;
//! they are not a prediction of compilation time. `is_satisfied` is called on uniformly random
//! assignments, which most of the time violate the constraint and may return early.
pub mod instance;
pub mod shape;
pub mod util;
pub mod workload;
