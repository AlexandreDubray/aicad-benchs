#!/usr/bin/env python3
"""Lists the metrics that moved between two snapshots.

    python tools/compare.py data/<old>.json data/<new>.json
    python tools/compare.py --data data --new data/<new>.json      # against the previous commit
    python tools/compare.py --data data --new data/<new>.json --github

``--data`` picks, as the old snapshot, the latest one that is older than ``--new`` by commit
date. A metric is reported when it changed by more than ``--threshold`` percent (instruction
counts are exact, so 2% is a real change). The exit status is always 0: a regression is
sometimes the price of a fix, and the timeline page shows it with its commit.

``--github`` also writes the table to ``$GITHUB_STEP_SUMMARY`` and one ``::warning::`` annotation
for the biggest regressions, so that they show on the workflow run without failing it.
"""
import argparse
import glob
import json
import os
import sys


def load(path):
    with open(path) as f:
        return json.load(f)


def previous(data_dir, new):
    """The snapshot of ``data_dir`` that precedes ``new`` by commit date, or None."""
    older = []
    for path in glob.glob(os.path.join(data_dir, "*.json")):
        snap = load(path)
        if snap["commit"]["sha"] != new["commit"]["sha"] and snap["commit"]["date"] <= new["commit"]["date"]:
            older.append((snap["commit"]["date"], path))
    return load(max(older)[1]) if older else None


def differences(old, new):
    """What differs between two builds besides the code: reasons why a step may not be a real change."""
    reasons = []
    for key in ("rustc", "profile", "opt_level", "target", "features"):
        if old["build"].get(key) != new["build"].get(key):
            reasons.append(key)
    for key in ("valgrind", "runner_image", "catalogue_sha256", "loops"):
        if old["environment"].get(key) != new["environment"].get(key):
            reasons.append(key)
    return reasons


def changes(old, new, threshold):
    rows = []
    for name, value in new["metrics"].items():
        before = old["metrics"].get(name)
        if before is None:
            continue
        if before == 0:
            ratio = 1.0 if value == 0 else float("inf")
        else:
            ratio = value / before
        if abs(ratio - 1) * 100 > threshold:
            rows.append((name, before, value, ratio))
    return sorted(rows, key=lambda r: -abs(r[3] - 1))


def table(rows, limit=40):
    lines = ["| metric | before | after | change |", "|---|---:|---:|---:|"]
    for name, before, after, ratio in rows[:limit]:
        lines.append(f"| `{name}` | {before:,.0f} | {after:,.0f} | {(ratio - 1) * 100:+.1f}% |")
    if len(rows) > limit:
        lines.append(f"| … {len(rows) - limit} more | | | |")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("snapshots", nargs="*", help="OLD NEW")
    p.add_argument("--data")
    p.add_argument("--new")
    p.add_argument("--threshold", type=float, default=2.0, help="percent")
    p.add_argument("--github", action="store_true")
    opts = p.parse_args()

    if len(opts.snapshots) == 2:
        old, new = load(opts.snapshots[0]), load(opts.snapshots[1])
    elif opts.data and opts.new and not opts.snapshots:
        new = load(opts.new)
        old = previous(opts.data, new)
        if old is None:
            print("no earlier snapshot to compare with")
            return
    else:
        p.error("give OLD NEW, or --data and --new")

    head = f"{old['commit']['sha'][:12]} → {new['commit']['sha'][:12]} ({new['commit']['subject']})"
    rows = changes(old, new, opts.threshold)
    notes = differences(old, new)
    out = [f"### Benchmarks {head}", ""]
    if notes:
        out += [f"The measurement setup also changed ({', '.join(notes)}): some steps may come from it, not from the code.", ""]
    out += [table(rows) if rows else f"No metric moved by more than {opts.threshold:g}%."]
    text = "\n".join(out)
    print(text)
    if opts.github:
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as f:
                f.write(text + "\n")
        worse = [r for r in rows if r[3] > 1][:5]
        if worse:
            body = "; ".join(f"{n} {(r - 1) * 100:+.1f}%" for n, _, _, r in worse)
            print(f"::warning title=Benchmarks: {len([r for r in rows if r[3] > 1])} metrics got worse::{body}")


if __name__ == "__main__":
    sys.exit(main())
