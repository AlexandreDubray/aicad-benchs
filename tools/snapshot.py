#!/usr/bin/env python3
"""Measures one build of ``aicad`` and writes its snapshot, ``data/<commit>.json``.

    cargo build --release --bin icount
    python tools/snapshot.py --aicad ../aicad                 # writes data/<commit of ../aicad>.json

A snapshot is what the timeline page plots, one point per metric. It holds:

- ``schema``: the version of this format (1);
- ``commit``: the hash, date, subject and parents of the ``aicad`` commit, read with ``git`` in
  ``--aicad``; the working tree must be clean, since the numbers are filed under that commit;
- ``build``: ``aicad::version()`` of the measured binary (version, commit, rustc, profile, ...);
- ``environment``: what else can move the numbers: valgrind, the runner image, the catalogue and
  the version of this repository;
- ``suites``: for each family of metrics, its unit and whether lower is better;
- ``metrics``: ``suite/series/metric -> number``. Here ``constraints/<shape>/<function>`` is the
  instructions executed per call of a constraint function. A new family of metrics only needs to
  add its entries and its suite.
"""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icount  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = 1

SUITES = {
    "constraints": {
        "unit": "instructions per call",
        "better": "lower",
        "description": "Instructions executed per call of a constraint function, on shapes of the XCSP3 catalogue.",
    }
}


def git(repo, *args):
    run = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if run.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed in {repo}: {run.stderr.strip()}")
    return run.stdout.rstrip("\n")


def commit_info(repo):
    if git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit(f"{repo} has uncommitted changes: benchmarks are filed under a commit, run them on a clean copy")
    sha, date, subject, parents = git(repo, "log", "-1", "--format=%H%x1f%cI%x1f%s%x1f%P").split("\x1f")
    return {"sha": sha, "date": date, "subject": subject, "parents": parents.split()}


def tool_version(*cmd):
    try:
        run = subprocess.run(cmd, capture_output=True, text=True)
        return (run.stdout or run.stderr).strip().splitlines()[0]
    except (OSError, IndexError):
        return ""


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--aicad", default=os.path.join(ROOT, "..", "aicad"), help="the aicad checkout that was built")
    p.add_argument("--binary", default=os.path.join(ROOT, "target", "release", "icount"))
    p.add_argument("--catalogue", default=os.path.join(ROOT, "catalogue", "ci_shapes.json"))
    p.add_argument("--loops", type=int, default=20)
    p.add_argument("--kind", help="only this kind of constraint (for a quick try)")
    p.add_argument("--out", help="default: data/<commit>.json")
    p.add_argument("--allow-mismatch", action="store_true", help="accept a binary built from another commit")
    opts = p.parse_args()

    commit = commit_info(opts.aicad)
    info = json.loads(subprocess.run([opts.binary, "--info"], capture_output=True, text=True, check=True).stdout)
    if info.get("commit") != commit["sha"] and not opts.allow_mismatch:
        raise SystemExit(
            f"the binary was built from commit {info.get('commit')} but {opts.aicad} is at {commit['sha']}: "
            "rebuild it (or --allow-mismatch)")

    failures = []
    values = icount.measure(opts.binary, opts.catalogue, opts.loops, opts.kind,
                            lambda shape, f, msg: failures.append(f"{icount.label(shape)} {f}: {msg}"))
    if failures:
        raise SystemExit("could not measure:\n" + "\n".join(failures))
    metrics = {f"constraints/{icount.label(shape)}/{f}": round(v, 3) for shape, f, v in values}

    catalogue = open(opts.catalogue, "rb").read()
    snapshot = {
        "schema": SCHEMA,
        "commit": commit,
        "build": info,
        "environment": {
            "valgrind": tool_version("valgrind", "--version"),
            "runner_image": os.environ.get("ImageVersion", ""),
            "os": platform.platform(),
            "catalogue_sha256": hashlib.sha256(catalogue).hexdigest()[:16],
            "loops": opts.loops,
            "benchs_commit": git(ROOT, "rev-parse", "HEAD") if os.path.isdir(os.path.join(ROOT, ".git")) else "",
        },
        "suites": SUITES,
        "metrics": metrics,
    }
    out = opts.out or os.path.join(ROOT, "data", commit["sha"] + ".json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(snapshot, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"{len(metrics)} metrics for {commit['sha'][:12]} ({commit['subject']}) -> {out}")


if __name__ == "__main__":
    main()
