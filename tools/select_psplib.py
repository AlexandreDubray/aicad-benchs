#!/usr/bin/env python3
"""Picks the disjunctive shapes the CI benchmarks run, from the catalogue of ``psplib_extract.py``.

    python tools/select_psplib.py [--catalogue catalogue/psplib_shapes.json] [--ci catalogue/ci_shapes.json]

The instances are binned by their number of jobs and ``--per-bin`` of them are taken from each bin
(at evenly spaced quantiles, so that the pick does not depend on the order of a directory). Each
instance gives one shape per horizon level: the horizon ``T`` is the makespan of the reference
serial schedule scaled by ``1 + slack``, and the deadline of a job is ``T - tail``.

    tight   slack 0    the reference order just fits: almost no other permutation is feasible
    medium  slack 0.5  some permutations are feasible (a few percent to most, depending on the instance)
    loose   slack 2.0  every permutation is feasible: the time bounds never filter

The jobs of a clique fill most of the project's makespan (the sum of their durations is close to
it), so the windows only leave room to reorder them once the horizon is much larger than the
makespan: that is why the levels are far apart.

One instance per bin is enough today: the cost of a call does not depend on the instance for fewer than 65
jobs (the state is a fixed-size record), so more shapes would only repeat the same line. ``--per-bin 2`` adds
a second instance per bin, as new series, if a change of the constraint starts to depend on the data.

The shapes replace the disjunctive ones of ``ci_shapes.json``; the others are left as they are. A
shape's ``key`` is a hash of its content (release, deadline, duration, precedences), so a metric
keeps its name for as long as the shape does not change, whatever its position in the file.

Every instance and level gives two variants (``--variants``, both by default): ``plain``, a
disjunctive without precedences, and ``precedences``, the same jobs and windows with the precedences
the project imposes between them (``induced_precedences`` of the catalogue). The variants have
different keys, so each has its own series. The plain ones come first and keep their ids and keys when
the other variant is added or removed: their series show what the code for precedences costs when
there are none.
"""
import argparse
import hashlib
import json
import math
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEVELS = {"tight": 0.0, "medium": 0.5, "loose": 2.0}
BINS = [(6, 9), (10, 12), (13, 15), (16, 19), (20, 99)]
ID_BASE = 100_000


def key_of(shape):
    p = shape["params"]
    text = json.dumps([shape["kind"], p["release"], p["deadline"], p["duration"], p.get("precedences", [])],
                      separators=(",", ":"))
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def feasible_fraction(p, samples=400):
    """Share of random permutations that respect the precedences and whose as-early-as-possible schedule meets every deadline."""
    rng = random.Random(1)
    n = len(p["duration"])
    ok = 0
    for _ in range(samples):
        order = list(range(n))
        rng.shuffle(order)
        position = {j: k for k, j in enumerate(order)}
        if any(position[a] > position[b] for a, b in p.get("precedences", [])):
            continue
        c = 0
        for j in order:
            c = max(c, p["release"][j]) + p["duration"][j]
            if c > p["deadline"][j]:
                break
        else:
            ok += 1
    return ok / samples


def pick(pool, count, last_bin):
    """``count`` instances at evenly spaced quantiles of the sorted pool (the largest one, for the last bin)."""
    pool = sorted(pool, key=lambda s: (s["arity"], s["source"]["family"], s["source"]["instance"]))
    if len(pool) <= count:
        return pool
    if last_bin:
        return [pool[round((i + 1) * (len(pool) - 1) / count)] for i in range(count)]
    return [pool[round((2 * i + 1) * len(pool) / (2 * count))] for i in range(count)]


def make_shape(s, level, slack, variant):
    """The shape of catalogue entry ``s`` at the horizon of ``level``, with or without precedences."""
    q = s["params"]
    horizon = math.ceil(q["sgs_makespan"] * (1 + slack) - 1e-9)
    params = {
        "release": q["release"],
        "deadline": [horizon - t for t in q["tail"]],
        "duration": q["duration"],
        "order": q["order"],
    }
    if variant == "precedences":
        params["precedences"] = q["induced_precedences"]
    shape = {
        "kind": "disjunctive", "supported": True, "arity": s["arity"], "domain": s["domain"],
        "params": params,
        "source": {**s["source"], "level": level, "horizon": horizon, "variant": variant},
        "families": s["families"], "instances": 1, "weight": 1.0,
    }
    shape["key"] = key_of(shape)
    return shape


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalogue", default=os.path.join(ROOT, "catalogue", "psplib_shapes.json"))
    p.add_argument("--ci", default=os.path.join(ROOT, "catalogue", "ci_shapes.json"))
    p.add_argument("--per-bin", type=int, default=1)
    p.add_argument("--variants", default="plain,precedences", help="comma-separated: plain, precedences")
    opts = p.parse_args()
    pool = json.load(open(opts.catalogue))["shapes"]
    ci = json.load(open(opts.ci))
    chosen = []
    for k, (lo, hi) in enumerate(BINS):
        chosen += pick([s for s in pool if lo <= s["arity"] <= hi], opts.per_bin, k == len(BINS) - 1)
    variants = [v.strip() for v in opts.variants.split(",") if v.strip()]
    if not variants or any(v not in ("plain", "precedences") for v in variants):
        raise SystemExit("--variants takes `plain` and/or `precedences`")
    out = []
    for variant in variants:
        for s in chosen:
            for level, slack in LEVELS.items():
                out.append(make_shape(s, level, slack, variant))
    for i, s in enumerate(out):
        s["id"] = ID_BASE + i
    kept = [s for s in ci["shapes"] if s["kind"] != "disjunctive"]
    json.dump({"source": ci["source"], "shapes": kept + out}, open(opts.ci, "w"), indent=1)
    print(f"{len(out)} disjunctive shapes ({len(chosen)} instances x {len(LEVELS)} horizons x {len(variants)} variants) -> {opts.ci}")
    for s in out:
        src = s["source"]
        print(f"  #{s['id']} {src['family']:5s} {src['instance']:10s} n={s['arity']:<3d} {src['variant']:11s} {src['level']:7s} horizon {src['horizon']:<4d}"
              f" feasible permutations {feasible_fraction(s['params']):.1%}")


if __name__ == "__main__":
    main()
