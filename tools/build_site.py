#!/usr/bin/env python3
"""Builds the benchmark page from the snapshots of ``data/``.

    python tools/build_site.py --data data --out site-out --repo AlexandreDubray/aicad

The page is one self-contained ``index.html`` (style, script and data inlined, no library to
load) plus ``data.json``, the same data. It is served by the documentation of ``aicad`` under
``/bench/``; open the file directly to look at it locally. The source of the page is in ``site/``.

Snapshots are ordered by the date of their commit. A commit whose measurement setup differs from
the previous one (compiler, valgrind, runner image, catalogue, profile) is flagged on the page as a
discontinuity, so that a step caused by the setup is not read as a change in the code.
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare import differences  # noqa: E402

SUPPORTED_SCHEMA = 1


def load_snapshots(data_dir):
    snapshots = []
    for path in sorted(glob.glob(os.path.join(data_dir, "*.json"))):
        with open(path) as f:
            snap = json.load(f)
        if snap.get("schema") != SUPPORTED_SCHEMA:
            print(f"skipping {path}: schema {snap.get('schema')}, expected {SUPPORTED_SCHEMA}", file=sys.stderr)
            continue
        snapshots.append(snap)
    snapshots.sort(key=lambda s: (s["commit"]["date"], s["commit"]["sha"]))
    return snapshots


def build_payload(snapshots, repo):
    commits, suites = [], {}
    for i, snap in enumerate(snapshots):
        c, b, e = snap["commit"], snap["build"], snap["environment"]
        commits.append({
            "sha": c["sha"], "date": c["date"], "subject": c["subject"],
            "version": b.get("version", ""), "rustc": b.get("rustc", ""),
            "profile": b.get("profile", ""), "opt_level": b.get("opt_level", ""),
            "features": b.get("features", []), "valgrind": e.get("valgrind", ""),
            "breaks": differences(snapshots[i - 1], snap) if i else [],
        })
        suites.update(snap.get("suites", {}))
    names = sorted({name for snap in snapshots for name in snap["metrics"]})
    metrics = {name: [snap["metrics"].get(name) for snap in snapshots] for name in names}
    return {"repo": repo, "commits": commits, "suites": suites, "metrics": metrics}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default=os.path.join(ROOT, "data"))
    p.add_argument("--out", required=True)
    p.add_argument("--repo", default="AlexandreDubray/aicad", help="owner/name of the benchmarked repository, for commit links")
    p.add_argument("--site", default=os.path.join(ROOT, "site"), help="the page sources")
    opts = p.parse_args()

    payload = build_payload(load_snapshots(opts.data), opts.repo)
    read = lambda name: open(os.path.join(opts.site, name), encoding="utf-8").read()
    # "</" inside a <script> block would end it early.
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = (read("index.html")
            .replace("/*__STYLE__*/", read("style.css"))
            .replace("/*__SCRIPT__*/", read("app.js"))
            .replace("__DATA__", data))
    os.makedirs(opts.out, exist_ok=True)
    with open(os.path.join(opts.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(opts.out, "data.json"), "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"{len(payload['commits'])} commits, {len(payload['metrics'])} metrics -> {opts.out}/index.html")


if __name__ == "__main__":
    main()
