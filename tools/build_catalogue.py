#!/usr/bin/env python3
"""Merges the raw per-family records of ``xcsp_extract.py`` into the catalogue of constraint shapes.

    python tools/build_catalogue.py --raw <raw-dir> --out catalogue/xcsp3_shapes.json

Every constraint of every instance is classified into a *kind* (``all_different``, ``sum``,
``intension:ne``, ``element``, ...). A kind is *supported* when it is listed in
``supported_constraints.json``: only those can be built by ``aicad`` and benchmarked. The others
stay in the catalogue, with how often they occur, as a record of what real models ask for.

Identical shapes (same kind, arity, domain and parameters) are merged: a shape records the
families it occurs in and how many constraints (weighted) carry it.

Tables (``extension`` constraints with positive supports, no ``*``) become the kind ``table``. A
table whose tuples were recorded (see ``--max-tuples`` of ``xcsp_extract.py``) is identified by a
hash of its sorted tuples, and its tuples are written to ``<raw>/tables/<hash>.json`` (outside the
catalogue: ``select_ci.py`` copies the ones that CI uses). A larger table is recorded with its
number of tuples and flagged ``synthetic``: the benchmarks draw random tuples for it. Negative
tables (``conflicts``) and short tables (``*``) are kinds of their own and are not supported.
"""
import argparse
import glob
import hashlib
import json
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def classify(tag, arity, x):
    """(kind, params) of a raw constraint record; params are what the builder needs."""
    if tag == "allDifferent":
        if x.get("matrix") or x.get("nlists"):
            return "all_different_vector", {}
        if x.get("except"):
            return "all_different_except", {}
        return "all_different", {}
    if tag == "intension":
        fn = x.get("fn", "?")
        if fn == "ne" and arity == 2:
            return "not_equal", {}
        return "intension:" + fn, {}
    cond = x.get("cond")
    if tag == "sum":
        if x.get("coeffs") in ("ones", "none") and cond and cond[0] == "eq" and cond[1] == "const":
            return "sum", {"target": cond[2]}
        return "sum_other", {}
    if tag in ("count", "atLeast", "atMost", "exactly", "among"):
        vals = x.get("values")
        if vals and cond and cond[1] in ("const", "range") and tag == "count":
            op, kind, k = cond
            if kind == "range":
                return "among", {"values": vals, "lb": k[0], "ub": k[1]}
            if op == "eq":
                return "among", {"values": vals, "lb": k, "ub": k}
            if op == "le":
                return "among", {"values": vals, "lb": 0, "ub": k}
            if op == "lt":
                return "among", {"values": vals, "lb": 0, "ub": k - 1} if k > 0 else ("count_other", {})
            if op == "ge":
                return "at_least", {"values": vals, "lb": k}
            if op == "gt":
                return "at_least", {"values": vals, "lb": k + 1}
        return "count_other" if tag == "count" else tag, {}
    if tag == "cardinality":
        if x.get("values") and x.get("bounds") and len(x["values"]) == len(x["bounds"]):
            return "gcc", {"values": x["values"], "bounds": x["bounds"]}
        return "cardinality_other", {}
    if tag == "extension":
        if x.get("kind") != "supports":
            return "extension_conflicts", {}
        if x.get("star"):
            return "extension_short", {}
        rows = x.get("table")
        if rows is not None:
            rows = sorted({tuple(r) for r in rows})
            digest = hashlib.sha1(json.dumps(rows).encode()).hexdigest()[:16]
            return "table", {"ntuples": len(rows), "synthetic": False, "hash": digest, "_rows": rows}
        count = x.get("ntuples") or 0
        digest = hashlib.sha1(f"synthetic/{arity}/{x.get('u')}/{count}".encode()).hexdigest()[:16]
        return "table", {"ntuples": count, "synthetic": True, "hash": digest}
    if tag == "regular":
        if x.get("automaton"):
            return "regular", {"automaton": x["automaton"]}
        return "regular_other", {}
    return tag, {}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--raw", required=True)
    p.add_argument("--out", default=os.path.join(ROOT, "catalogue", "xcsp3_shapes.json"))
    p.add_argument("--supported", default=os.path.join(ROOT, "supported_constraints.json"))
    p.add_argument("--tables-dir", help="where the tuples of the tables go (default: <raw>/tables)")
    opts = p.parse_args()
    tables_dir = opts.tables_dir or os.path.join(opts.raw, "tables")
    os.makedirs(tables_dir, exist_ok=True)
    supported = set(json.load(open(opts.supported))["supported"])

    shapes = {}
    kinds = defaultdict(lambda: {"weight": 0.0, "shapes": 0})
    instances, skipped = 0, 0
    for path in sorted(glob.glob(os.path.join(opts.raw, "*.jsonl"))):
        family = os.path.basename(path)[:-6]
        for line in open(path):
            rec = json.loads(line)
            if "cons" not in rec:
                skipped += 1
                continue
            instances += 1
            for tag, arity, x, w in rec["cons"]:
                kind, params = classify(tag, arity, x)
                rows = params.pop("_rows", None)
                if rows is not None:
                    target = os.path.join(tables_dir, params["hash"] + ".json")
                    if not os.path.exists(target):
                        with open(target, "w") as f:
                            json.dump(rows, f, separators=(",", ":"))
                dom = {k: x.get(k) for k in ("u", "lo", "hi", "dmin", "dmax")}
                key = json.dumps([kind, arity, dom, params], sort_keys=True)
                s = shapes.get(key)
                if s is None:
                    s = shapes[key] = {"key": hashlib.sha1(key.encode()).hexdigest()[:10], "kind": kind, "supported": kind in supported, "arity": arity,
                                       "domain": dom, "params": params, "families": set(),
                                       "instances": 0, "weight": 0.0}
                s["families"].add(family)
                s["instances"] += 1
                s["weight"] += w
                kinds[kind]["weight"] += w
    out = []
    for i, s in enumerate(sorted(shapes.values(), key=lambda s: (s["kind"], s["arity"], json.dumps(s["domain"])))):
        s["families"] = sorted(s["families"])
        s["weight"] = round(s["weight"], 1)
        s["id"] = i
        kinds[s["kind"]]["shapes"] += 1
        out.append(s)
    doc = {"instances": instances, "skipped_instances": skipped, "supported_kinds": sorted(supported),
           "kinds": {k: {"supported": k in supported, "weight": round(v["weight"], 1), "shapes": v["shapes"]}
                     for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]["weight"])},
           "shapes": out}
    os.makedirs(os.path.dirname(opts.out), exist_ok=True)
    with open(opts.out, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(f"{instances} instances ({skipped} skipped), {len(out)} shapes -> {opts.out}")
    for k, v in doc["kinds"].items():
        print(f"  {'*' if v['supported'] else ' '} {k:28s} weight {v['weight']:>14,.0f}  shapes {v['shapes']}")


if __name__ == "__main__":
    main()
