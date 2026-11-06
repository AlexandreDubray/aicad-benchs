# aicad-benchs

Benchmarks of [`aicad`](https://github.com/AlexandreDubray/aicad) that live outside the main repository.
Today: **micro-benchmarks of the constraint functions**, on constraint shapes that occur in the
XCSP3 benchmarks. Macro-benchmarks (compiling whole instances) will come in the same repository.

## Why constraint shapes from XCSP3

XCSP3 benchmarks contains a lot of different constraints from different problem families. Hence, it span many
problem shapes and structure. Using this as a basis allows having diversity in the constraint used to evaluate
the efficiency of the code (e.g., bitsets of less than 64 values are inlined, so we need to evaluate the impact
compare to big-domain variables).

## Layout

```
supported_constraints.json   kinds that aicad can build; the others are catalogued but not benchmarked
catalogue/xcsp3_shapes.json  every distinct shape found (all kinds), with `supported`, families and weight
catalogue/ci_shapes.json     ~27 shapes picked for CI (quantiles of size + both sides of the 64-value limit)
tools/xcsp_extract.py        XCSP3 instances -> raw per-instance constraint records
tools/build_catalogue.py     raw records + supported list -> xcsp3_shapes.json
tools/select_ci.py           xcsp3_shapes.json -> ci_shapes.json
tools/report.py              cargo bench output -> table of ns per call (and ratio against a past run)
tools/icount.py              instruction counts per call under valgrind
tools/snapshot.py            measures one build of aicad -> data/<commit>.json (the CI step)
tools/compare.py             what moved between two snapshots (table, GitHub annotations)
tools/build_site.py          data/*.json -> the timeline page (site/ holds its sources)
data/                        one snapshot per aicad commit, filed by the CI of aicad
src/, benches/, src/bin/     the Rust crate (shape -> constraint -> random-path workload -> benchmarks)
results/icount_baseline.json instruction counts per call of the CI shapes (see below)
```

`supported_constraints.json` is the only file to edit when `aicad` gains a constraint: add the
kind, then teach `src/instance.rs` and `tools/build_catalogue.py` (`classify`) to build it.
Other XCSP3 kinds (`extension`, `element`, `cumulative`, `intension:*`, ...) stay in the catalogue
with their weight, which says what real models ask for.

## Running

```
cargo bench --bench constraints                 # criterion, wall clock (BENCH_KIND=sum, BENCH_QUICK=1)
python tools/report.py --json results/me.json   # ns per call, one row per shape
python tools/report.py --compare results/old.json

cargo build --release --bin icount              # needs valgrind (Linux)
python tools/icount.py --loops 20 --json results/new.json
```

Times vary with the machine; compare two commits on the same machine, back to back. Instruction
counts are deterministic and comparable across machines: use them for CI and for regressions of
a few percent. The `aicad` checkout is expected at `../aicad`.

## Continuous benchmarking

Every push to `main` of `aicad` runs `.github/workflows/bench.yml` there. It checks out this
repository, builds `icount` against that commit, runs `tools/snapshot.py`, files the result as
`data/<commit>.json` here (skipping commits that already have one) and rebuilds the `aicad`
documentation, which runs `tools/build_site.py` on `data/` and publishes the timeline at
`https://alexandredubray.github.io/aicad/bench/`. The workflows only link the two repositories:
the measuring, the comparison and the page all live here.

A snapshot is `{schema, commit, build, environment, suites, metrics}`. `commit` is the hash, date,
subject and parents of the aicad commit; `build` is `aicad::version()` of the measured binary;
`environment` records valgrind, the runner image and the catalogue, which can also move the
numbers; `metrics` maps `suite/series/metric` to a number, and `suites` gives each suite's unit and
whether lower is better. The page needs nothing else: **a new family of metrics (MDD node counts
after a relaxed compilation, refinement iterations, ...) only has to add its entries and its suite
to a snapshot** and it appears in the timeline.

The timeline plots one line per series across commits (ordered by commit date). It flags the steps
larger than a threshold, lists the biggest ones with the commit that caused them, and marks the
commits where the measurement setup (rustc, valgrind, runner image, catalogue, profile) changed, so
that a step due to the setup is not read as a change of the code. A regression never fails the CI:
it is sometimes the price of a fix, and shows up as a step with its commit message.

To measure by hand: build `icount` against a clean checkout of a commit, then
`python tools/snapshot.py --aicad ../aicad` (the working tree must be clean, since the numbers are
filed under the commit) and `python tools/build_site.py --out /tmp/bench` to look at the page. To
measure a commit that CI skipped (a push that arrives while another is measured replaces the one
waiting), run the *Benchmarks* workflow by hand and give its hash.

CI needs the secret `BENCH_DEPLOY_KEY` in the `aicad` repository: the private key of a deploy key of
this repository with write access.

## Rebuilding the catalogue

```
python tools/xcsp_extract.py --src <xcsp3 dir> --out <raw dir>           # sample: 3 instances per variant
python tools/xcsp_extract.py --src <xcsp3 dir> --out <raw dir> --all     # every instance (hours)
python tools/build_catalogue.py --raw <raw dir>
python tools/select_ci.py --raw <raw dir>    # also copies the tuples of the chosen tables to catalogue/tables/
```

The committed catalogue comes from the sample: 833 instances, 7098 shapes. It is resumable per
family (`--budget` bounds the seconds).

## How the inputs are made

`workload.rs` draws seeded random paths (16 per shape, at 64 evenly spaced layers) and folds
`update` / `update_backward` along them through the public traits, giving top-down and bottom-up
states; several are merged to make joined states. Every function runs over those states, so a
state's size follows the shape. Caveats:

- Random paths are not the states of a real diagram (which concentrate and are width-limited);
  numbers rank commits, they do not predict compilation time.
- `merge` joins random states, usually far apart; merges of near states may cost less.
- `is_satisfied` runs on uniformly random assignments, which mostly violate the constraint.
- The sample only has `regular` from Nonogram (binary alphabets, up to 33 states) and no
  `at_least`; run the extraction with `--all` to widen it.
- Tables are the positive `extension` constraints of XCSP3 (negative ones, `conflicts`, and short
  ones with `*` are catalogued but not supported). A table of up to 5,000 tuples keeps its real
  tuples (`catalogue/tables/<hash>.json`, only for the CI shapes); a larger one is `synthetic`:
  the benchmark draws that many distinct random tuples (at most 20,000, seeded by the hash), which
  has little structure to share and so a larger table diagram than most real ones. Uniformly
  random paths leave a table at the first layer, so for tables a third of the paths follow a
  tuple up to a random layer, a third follow one from a random layer, and half of the assignments
  of `is_satisfied` are tuples.
- Domains are rebuilt as `lo..lo+u` (holes in the original domain are lost); `regular` uses the
  alphabet of its automaton.

## First observations (instructions per call, `results/icount_baseline.json`)

- `all_different` `update` costs 412 instructions up to 64 values and about 1,020 above:
  the inline/heap bitset boundary is visible in the numbers.
- `sum` and `among` states are constant-size: `update` costs 245-290 whatever
  the shape.
- `gcc` and `regular` grow with the number of values / states; `gcc` `hash` is the most
  expensive function measured (3,400-7,600 instructions).
- `hash` of `not_equal` over 10,000 values costs ~11,000 instructions, against 180 for 4 values.
