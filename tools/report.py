#!/usr/bin/env python3
"""Turns the output of ``cargo bench`` (``target/criterion``) into a table of ns per call.

    python tools/report.py                       # markdown table on stdout
    python tools/report.py --json results/x.json # also a machine-readable file, to compare runs
    python tools/report.py --compare results/old.json   # ratio against an earlier run

A benchmark times a loop over its whole workload; criterion records the number of calls in the
loop as the throughput, and this script divides by it, so the numbers are nanoseconds per call.
"""
import argparse
import glob
import json
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDER = ["identity_property", "structural_key", "update(+identity)", "update_backward(+identity)",
         "is_assignment_invalid", "clone", "clone+merge", "eq", "hash", "order_key", "is_single_state",
         "is_satisfied"]


def collect(criterion_dir):
    out = defaultdict(dict)
    for bench in glob.glob(os.path.join(criterion_dir, "*", "*", "new", "benchmark.json")):
        b = json.load(open(bench))
        est = json.load(open(os.path.join(os.path.dirname(bench), "estimates.json")))
        calls = (b.get("throughput") or {}).get("Elements") or 1
        group = b["group_id"]
        func = b.get("value_str") or b["function_id"]
        out[group][func] = est["median"]["point_estimate"] / calls
    return out


def fmt(ns):
    return f"{ns:,.0f}" if ns >= 100 else f"{ns:.1f}"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--criterion", default=os.path.join(ROOT, "target", "criterion"))
    p.add_argument("--json")
    p.add_argument("--compare", help="an earlier --json file; cells show new/old")
    opts = p.parse_args()
    data = collect(opts.criterion)
    old = json.load(open(opts.compare)) if opts.compare else {}
    print("| shape | " + " | ".join(ORDER) + " |")
    print("|---|" + "---:|" * len(ORDER))
    for group in sorted(data, key=lambda g: (g.split("#")[0], int(g.split("#")[1].split("/")[0]))):
        cells = []
        for f in ORDER:
            v = data[group].get(f)
            if v is None:
                cells.append("")
            elif f in old.get(group, {}):
                cells.append(f"{v / old[group][f]:.2f}x")
            else:
                cells.append(fmt(v))
        print(f"| {group} | " + " | ".join(cells) + " |")
    if opts.json:
        json.dump(data, open(opts.json, "w"), indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
