#!/usr/bin/env python3
"""Counts the instructions each constraint function executes, under valgrind (callgrind).

    cargo build --release --bin icount
    python tools/icount.py [--catalogue catalogue/ci_shapes.json] [--loops 20] [--json out.json]

Instruction counts do not depend on the machine's load or frequency, so they are comparable
between runs on shared CI runners, where wall-clock times are not. For each shape and function the
script runs ``icount`` with 0 and ``--loops`` loops: the difference is the cost of the loops, and
dividing by the loops and the calls gives instructions per call. Needs ``valgrind``.
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUNCTIONS = ["update", "update_backward", "is_assignment_invalid", "merge", "eq", "hash", "order_key", "is_satisfied"]


def collected(binary, catalogue, shape, function, loops):
    run = subprocess.run(
        ["valgrind", "--tool=callgrind", "--callgrind-out-file=/dev/null", binary, catalogue, str(shape), function, str(loops)],
        capture_output=True, text=True)
    m = re.search(r"Collected\s*:\s*(\d+)", run.stderr)
    if run.returncode != 0 or not m:
        raise RuntimeError(run.stderr[-400:])
    return int(m.group(1)), int(run.stdout.strip())


def label(shape):
    """The name of a shape in reports and metrics: ``kind#key(n<arity>,u<values>)``.

    The key is a hash of the shape's content, so the name of a metric does not change when the
    catalogue grows (a position in the catalogue, the ``id``, would).
    """
    return f"{shape['kind']}#{shape.get('key', shape['id'])}(n{shape['arity']},u{shape['domain']['u']})"


def measure(binary, catalogue, loops=20, kind=None, on_error=None):
    """Instructions per call of every function of every shape of ``catalogue``.

    Returns a list of ``(shape, function, instructions_per_call)``. A function that cannot be
    measured is reported to ``on_error(shape, function, message)`` and left out.
    """
    out = []
    for shape in json.load(open(catalogue))["shapes"]:
        if kind and shape["kind"] != kind:
            continue
        for f in FUNCTIONS:
            try:
                base, calls = collected(binary, catalogue, shape["id"], f, 0)
                full, _ = collected(binary, catalogue, shape["id"], f, loops)
            except RuntimeError as e:
                if on_error:
                    on_error(shape, f, str(e))
                continue
            out.append((shape, f, (full - base) / loops / calls))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalogue", default=os.path.join(ROOT, "catalogue", "ci_shapes.json"))
    p.add_argument("--binary", default=os.path.join(ROOT, "target", "release", "icount"))
    p.add_argument("--loops", type=int, default=20)
    p.add_argument("--kind")
    p.add_argument("--json")
    opts = p.parse_args()
    errors = lambda shape, f, msg: print(f"# {label(shape)} {f}: {msg}", file=sys.stderr)
    rows = {}
    for shape, f, value in measure(opts.binary, opts.catalogue, opts.loops, opts.kind, errors):
        rows.setdefault(label(shape), {})[f] = value
    print("| shape | " + " | ".join(FUNCTIONS) + " |")
    print("|---|" + "---:|" * len(FUNCTIONS))
    for name, values in rows.items():
        cells = [f"{values[f]:,.0f}" if f in values else "err" for f in FUNCTIONS]
        print(f"| {name} | " + " | ".join(cells) + " |")
    if opts.json:
        json.dump(rows, open(opts.json, "w"), indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
