#!/usr/bin/env python3
"""Picks the few shapes the CI benchmarks run from the full catalogue.

    python tools/select_ci.py --catalogue catalogue/xcsp3_shapes.json --out catalogue/ci_shapes.json

For every supported kind, shapes are ranked by their *size* (arity x domain size, which drives the
cost of a state) and ``--per-kind`` of them are taken at evenly spaced quantiles of that ranking;
the list is completed with the shapes closest to the 64-value bitset limit on both sides (a
property stores its values inline up to 64 and on the heap above), and with the largest shape that
stays under ``--max-cells`` so that memory stays bounded. Shapes with an arity under 2 are ignored.

Tables are ranked by their number of tuples times their arity, and do not look at the 64-value
limit (their states are sets of nodes of the table's own diagram). The picks are the real tables at
quantiles of that ranking (two binary ones, three wider ones) plus the smallest and the largest synthetic ones; the tuples of the real
tables are copied from ``<raw>/tables`` (written by ``build_catalogue.py``) to
``catalogue/tables`` next to the output, which is where the benchmarks read them.
"""
import argparse
import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMIT = 64


def size(s):
    """Cells of a state: arity x domain, and for a regular x the states of the automaton."""
    if s["kind"] == "table":
        return s["arity"] * s["params"]["ntuples"]
    if s["kind"] == "regular":
        a = s["params"]["automaton"]
        return s["arity"] * len(a["table"]) * len(a["symbols"])
    return s["arity"] * (s["domain"].get("u") or 1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalogue", default=os.path.join(ROOT, "catalogue", "xcsp3_shapes.json"))
    p.add_argument("--out", default=os.path.join(ROOT, "catalogue", "ci_shapes.json"))
    p.add_argument("--per-kind", type=int, default=3)
    p.add_argument("--max-cells", type=int, default=20000, help="largest arity x domain a CI shape may have")
    p.add_argument("--max-table-cells", type=int, default=60000, help="largest arity x tuples a table may have")
    p.add_argument("--raw", help="the directory given to build_catalogue.py, which holds tables/")
    opts = p.parse_args()
    doc = json.load(open(opts.catalogue))
    picked = {}
    for kind in doc["supported_kinds"]:
        limit = opts.max_table_cells if kind == "table" else opts.max_cells
        pool = [s for s in doc["shapes"] if s["kind"] == kind and s["arity"] >= 2
                and s["domain"].get("u") and size(s) <= limit]
        pool.sort(key=lambda s: (size(s), s["id"]))
        if kind == "table":
            pool = [s for s in pool if s["params"]["ntuples"] > 0]
            # A table of one or two tuples is a constant: not worth benchmarking.
            real = [s for s in pool if not s["params"]["synthetic"] and s["params"]["ntuples"] >= 4]
            synthetic = [s for s in pool if s["params"]["synthetic"]]
            # Most tables are binary: take the binary and the wider ones separately.
            def quantiles(pool, count):
                n = min(count, len(pool))
                return [pool[round(i * (len(pool) - 1) / max(n - 1, 1))] for i in range(n)]
            chosen = quantiles([s for s in real if s["arity"] == 2], 2) + quantiles([s for s in real if s["arity"] > 2], 3)
            if synthetic:
                chosen += [synthetic[0], synthetic[-1]]
        else:
            if not pool:
                continue
            n = min(opts.per_kind, len(pool))
            chosen = [pool[round(i * (len(pool) - 1) / max(n - 1, 1))] for i in range(n)]
            under = [s for s in pool if s["domain"]["u"] <= LIMIT]
            over = [s for s in pool if s["domain"]["u"] > LIMIT]
            if under:
                chosen.append(max(under, key=lambda s: (s["domain"]["u"], s["arity"])))
            if over:
                chosen.append(min(over, key=lambda s: (s["domain"]["u"], s["arity"])))
        for s in chosen:
            picked[s["id"]] = s
    shapes = sorted(picked.values(), key=lambda s: (s["kind"], size(s)))
    tables = [s for s in shapes if s["kind"] == "table" and not s["params"]["synthetic"]]
    if tables:
        if not opts.raw:
            raise SystemExit("the catalogue has real tables: give --raw, the directory that holds tables/")
        out_tables = os.path.join(os.path.dirname(os.path.abspath(opts.out)), "tables")
        os.makedirs(out_tables, exist_ok=True)
        for s in tables:
            shutil.copyfile(os.path.join(opts.raw, "tables", s["params"]["hash"] + ".json"),
                            os.path.join(out_tables, s["params"]["hash"] + ".json"))
    used = {s["params"]["hash"] + ".json" for s in tables}
    tables_dir = os.path.join(os.path.dirname(os.path.abspath(opts.out)), "tables")
    stale = sorted(set(os.listdir(tables_dir)) - used) if os.path.isdir(tables_dir) else []
    if stale:
        print(f"warning: {len(stale)} table file(s) in {tables_dir} are no longer used, delete them: {' '.join(stale)}")
    json.dump({"source": os.path.basename(opts.catalogue), "shapes": shapes}, open(opts.out, "w"), indent=1)
    print(f"{len(shapes)} CI shapes -> {opts.out}")
    for s in shapes:
        print(f"  #{s['id']:<5} {s['kind']:14s} arity {s['arity']:<6} u {s['domain']['u']:<5} families {len(s['families'])}")


if __name__ == "__main__":
    main()
