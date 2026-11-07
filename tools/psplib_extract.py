#!/usr/bin/env python3
"""Extracts disjunctive (unary resource) shapes from PSPLIB RCPSP instances.

    python tools/psplib_extract.py --psplib ../rcpsp --out catalogue/psplib_shapes.json

PSPLIB instances (``.sm`` files: j30, j60, j90, j120) have precedences and cumulative resources, not
a single machine. A *disjunctive* set of jobs is a set that cannot overlap in time, and such a set
exists in every instance: two jobs cannot overlap if their demands exceed the capacity of some
resource, or if one is a (transitive) predecessor of the other. The jobs of the largest such set
(a maximum clique of the "cannot overlap" graph) are the jobs of one shape per instance.

For the jobs of the clique, with the project's own precedence graph:

- ``release`` is the head of the job: the longest path from the start of the project to its start.
- ``tail`` is the longest path from its end to the end of the project.
- ``duration`` is the duration of the job.
- ``sgs_makespan`` is the makespan of a serial schedule (priority: latest finish time), a valid
  upper bound of the project. A shape with horizon ``T >= sgs_makespan`` has the deadline
  ``T - tail`` for every job and is satisfiable: ``order`` (the jobs of the clique by start time in
  that schedule) is a feasible permutation. ``select_psplib.py`` picks the horizons.
- ``induced_precedences`` are the precedences between the jobs of the clique that the project
  imposes (the transitive reduction of the induced relation); a disjunctive without precedences
  ignores them, and a precedence-aware one will use them.

Jobs are numbered from 0 in the order of their number in the instance. Only the standard library is
needed. The output is deterministic.
"""
import argparse
import glob
import hashlib
import json
import os
import re


def parse(path):
    """(successors, durations, demands, capacities) of one ``.sm`` file; jobs are numbered from 1."""
    lines = open(path).read().split("\n")

    def after(prefix):
        return next(k for k, l in enumerate(lines) if l.startswith(prefix))

    succ = {}
    k = after("PRECEDENCE RELATIONS") + 2
    while not lines[k].startswith("***"):
        t = lines[k].split()
        succ[int(t[0])] = [int(x) for x in t[3:3 + int(t[2])]]
        k += 1
    dur, dem = {}, {}
    k = after("REQUESTS/DURATIONS") + 3
    while not lines[k].startswith("***"):
        t = lines[k].split()
        dur[int(t[0])] = int(t[2])
        dem[int(t[0])] = [int(x) for x in t[3:]]
        k += 1
    capacity = [int(x) for x in lines[after("RESOURCEAVAILABILITIES") + 2].split()]
    return succ, dur, dem, capacity


def topological(succ):
    indeg = {j: 0 for j in succ}
    for j in succ:
        for s in succ[j]:
            indeg[s] += 1
    ready = sorted(j for j in succ if indeg[j] == 0)
    order = []
    while ready:
        j = ready.pop(0)
        order.append(j)
        for s in succ[j]:
            indeg[s] -= 1
            if indeg[s] == 0:
                ready.append(s)
                ready.sort()
    if len(order) != len(succ):
        raise ValueError("the precedence graph has a cycle")
    return order


def heads_tails(succ, dur, order):
    """Longest path from the start of the project to the start of each job, and from its end to the end."""
    pred = {j: [] for j in succ}
    for j in succ:
        for s in succ[j]:
            pred[s].append(j)
    head = {}
    for j in order:
        head[j] = max((head[p] + dur[p] for p in pred[j]), default=0)
    tail = {}
    for j in reversed(order):
        tail[j] = max((dur[s] + tail[s] for s in succ[j]), default=0)
    return head, tail, pred


def serial_schedule(succ, dur, dem, capacity, order, tail):
    """Serial schedule generation: jobs by increasing latest finish time, each as early as possible."""
    pred = {j: [] for j in succ}
    for j in succ:
        for s in succ[j]:
            pred[s].append(j)
    horizon = sum(dur.values()) + 1
    used = [[0] * horizon for _ in capacity]
    start = {}
    longest = max(tail[j] + dur[j] for j in succ)
    # priority: smallest latest finish time = (a bound of the makespan) - tail
    pending = set(succ)
    while pending:
        eligible = [j for j in pending if all(p in start for p in pred[j])]
        j = min(eligible, key=lambda j: (longest - tail[j], j))
        t = max((start[p] + dur[p] for p in pred[j]), default=0)
        while any(used[r][u] + dem[j][r] > capacity[r] for r in range(len(capacity)) for u in range(t, t + dur[j])):
            t += 1
        for r in range(len(capacity)):
            for u in range(t, t + dur[j]):
                used[r][u] += dem[j][r]
        start[j] = t
        pending.remove(j)
    return start, max(start[j] + dur[j] for j in succ)


def max_clique(adj):
    """A maximum clique (Bron-Kerbosch with pivoting), deterministic."""
    best = []

    def expand(r, p, x):
        nonlocal best
        if not p and not x:
            if len(r) > len(best):
                best = r[:]
            return
        if len(r) + len(p) <= len(best):
            return
        pivot = max(sorted(p | x), key=lambda v: len(adj[v] & p))
        for v in sorted(p - adj[pivot]):
            expand(r + [v], p & adj[v], x & adj[v])
            p = p - {v}
            x = x | {v}

    expand([], set(adj), set())
    return sorted(best)


def closure(succ, order):
    reach = {j: set() for j in succ}
    for j in reversed(order):
        for s in succ[j]:
            reach[j] |= {s} | reach[s]
    return reach


def shape_of(path, family):
    succ, dur, dem, capacity = parse(path)
    order = topological(succ)
    head, tail, _ = heads_tails(succ, dur, order)
    reach = closure(succ, order)
    jobs = [j for j in sorted(succ) if dur[j] > 0]
    adj = {j: set() for j in jobs}
    for a in jobs:
        for b in jobs:
            if a == b:
                continue
            if (any(dem[a][r] + dem[b][r] > capacity[r] for r in range(len(capacity)))
                    or b in reach[a] or a in reach[b]):
                adj[a].add(b)
    clique = max_clique(adj)
    start, makespan = serial_schedule(succ, dur, dem, capacity, order, tail)
    index = {j: k for k, j in enumerate(clique)}
    # transitive reduction of the relation induced on the clique
    later = {j: [s for s in clique if s in reach[j]] for j in clique}
    precedences = []
    for a in clique:
        for b in later[a]:
            if not any(b in reach[c] for c in later[a] if c != b):
                precedences.append([index[a], index[b]])
    sequence = [index[j] for j in sorted(clique, key=lambda j: start[j])]
    release = [head[j] for j in clique]
    duration = [dur[j] for j in clique]
    deadline = [makespan - tail[j] for j in clique]
    # the sequence must be feasible at the horizon of the schedule: check it
    c = 0
    for j in sequence:
        c = max(c, release[j]) + duration[j]
        assert c <= deadline[j], f"{path}: the reference order misses a deadline"
    name = os.path.splitext(os.path.basename(path))[0]
    return {
        "kind": "disjunctive",
        "supported": True,
        "arity": len(clique),
        "domain": {"u": len(clique), "lo": 0, "hi": len(clique) - 1, "dmin": len(clique), "dmax": len(clique)},
        "params": {
            "release": release,
            "tail": [tail[j] for j in clique],
            "duration": duration,
            "sgs_makespan": makespan,
            "order": sequence,
            "induced_precedences": sorted(precedences),
        },
        "source": {"family": family, "instance": name, "jobs": clique},
        "families": [family],
        "instances": 1,
        "weight": 1.0,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--psplib", required=True, help="directory with the families (j30, j60, ...) as subdirectories")
    p.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                  "catalogue", "psplib_shapes.json"))
    p.add_argument("--min-jobs", type=int, default=4, help="skip instances whose clique is smaller")
    opts = p.parse_args()
    shapes = []
    for family in sorted(d for d in os.listdir(opts.psplib) if re.fullmatch(r"j\d+", d)):
        files = sorted(glob.glob(os.path.join(opts.psplib, family, "*.sm")))
        for path in files:
            s = shape_of(path, family)
            if s["arity"] >= opts.min_jobs:
                shapes.append(s)
        print(f"{family}: {len(files)} instances")
    for i, s in enumerate(shapes):
        s["id"] = i
    json.dump({"source": "psplib", "supported_kinds": ["disjunctive"], "shapes": shapes},
              open(opts.out, "w"), separators=(",", ":"))
    sizes = sorted(s["arity"] for s in shapes)
    print(f"{len(shapes)} shapes, jobs {sizes[0]}..{sizes[-1]}, median {sizes[len(sizes) // 2]} -> {opts.out}")


if __name__ == "__main__":
    main()
