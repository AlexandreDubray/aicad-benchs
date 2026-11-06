#!/usr/bin/env python3
"""Reads XCSP3 instances (``.xml.lzma``) and records the shape of every constraint they contain.

A *shape* is what a constraint looks like to a solver, without the identity of its variables: the
kind, the arity, the values its variables take, and the parameters of the kind (the bounds of a
sum, the automaton of a regular, ...). One output line per instance, in ``<out>/<family>.jsonl``;
``build_catalogue.py`` merges them.

    python tools/xcsp_extract.py --src <xcsp-bench> --out catalogue/raw            # a sample
    python tools/xcsp_extract.py --src <xcsp-bench> --out catalogue/raw --all      # every instance

The sample takes, in every variant of every family, the smallest instance, the median one and the
largest one under ``--max-compressed`` bytes. A family is skipped when its output file exists, so a
run can be resumed (and ``--budget`` bounds the seconds it is allowed to take). Instances whose XML
is larger than ``--max-xml`` bytes are recorded as skipped.

Groups of constraints (``<group>`` with many ``<args>``) are examined on at most ``--group-sample``
of their ``args``, evenly spaced, and the others counted by weight.
"""
import argparse
import copy
import itertools
import json
import lzma
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from multiprocessing import Pool

IDX = re.compile(r"\[([^\]]*)\]")
VAR = re.compile(r"^([A-Za-z_][\w.]*)((?:\[[^\]]*\])*)$")
SUB = re.compile(r"%(\d+|\.\.\.)")
INT = re.compile(r"-?\d+")
SCOPE_TAGS = {"list", "matrix", "vars", "scope", "index"}
MAX_AUTOMATON_CELLS = 4096   # larger automata are counted but not recorded
MAX_VALUES = 64              # explicit value lists longer than this are only counted


def parse_domain(text):
    """The intervals of a domain, or None if it is unbounded or symbolic."""
    text = (text or "").strip()
    if not text or text == "*":
        return None
    items = []
    for tok in text.split():
        try:
            if ".." in tok:
                a, b = tok.split("..")
                items.append((int(a), int(b)))
            else:
                items.append((int(tok), int(tok)))
        except ValueError:
            return None
    return tuple(sorted(items))


def union_size(domains):
    """The number of distinct values in the union of the domains, and its (lo, hi)."""
    ivs = sorted(iv for d in domains for iv in d)
    total, cur_lo, cur_hi = 0, None, None
    for lo, hi in ivs:
        if cur_hi is None or lo > cur_hi + 1:
            if cur_hi is not None:
                total += cur_hi - cur_lo + 1
            cur_lo, cur_hi = lo, hi
        else:
            cur_hi = max(cur_hi, hi)
    if cur_hi is not None:
        total += cur_hi - cur_lo + 1
    return total, (ivs[0][0], max(h for _, h in ivs))


def dsize(d):
    return sum(h - l + 1 for l, h in d)


def int_list(text, limit=MAX_VALUES):
    """The integers of a list of ints and ``a..b`` ranges, or None if it holds something else or
    is longer than ``limit``."""
    out = []
    for tok in (text or "").split():
        if INT.fullmatch(tok):
            out.append(int(tok))
        elif re.fullmatch(r"-?\d+\.\.-?\d+", tok):
            a, b = tok.split("..")
            if int(b) - int(a) + 1 + len(out) > limit:
                return None
            out.extend(range(int(a), int(b) + 1))
        else:
            return None
        if len(out) > limit:
            return None
    return out


def parse_tuples(text, arity, limit):
    """(number of tuples, the tuples or None) of the text of ``<supports>``/``<conflicts>``.

    The tuples are returned only if they are all integers, have ``arity`` values and there are at
    most ``limit`` of them; the number is counted either way. A unary constraint lists plain values.
    """
    if "(" in text:
        raw = re.findall(r"\(([^()]*)\)", text)
        count = len(raw)
        if count > limit:
            return count, None
        rows = []
        for r in raw:
            try:
                row = [int(v) for v in r.split(",")]
            except ValueError:
                return count, None
            if len(row) != arity:
                return count, None
            rows.append(row)
        return count, rows
    toks = text.split()
    if arity != 1 or len(toks) > limit or not all(INT.fullmatch(t) for t in toks):
        return len(toks), None
    return len(toks), [[int(t)] for t in toks]


class Arr:
    __slots__ = ("dims", "default", "over")

    def __init__(self, dims, default):
        self.dims, self.default, self.over = dims, default, {}


class Model:
    """The variables of an instance: arrays with a default domain and per-variable overrides."""

    def __init__(self):
        self.arrs, self.nvars = {}, 0

    def add_vars(self, root):
        for e in root:
            if e.tag == "var":
                self.arrs[e.get("id")] = Arr([], parse_domain(e.text))
                self.nvars += 1
            elif e.tag == "array":
                dims = [int(x) for x in IDX.findall(e.get("size"))]
                arr = Arr(dims, parse_domain(e.text))
                self.arrs[e.get("id")] = arr
                n = 1
                for d in dims:
                    n *= d
                self.nvars += n
                for dom in e.findall("domain"):
                    d = parse_domain(dom.text)
                    for tok in dom.get("for", "").split():
                        for flat in self.flats(tok):
                            arr.over[flat] = d

    @staticmethod
    def ranges(arr, idxs):
        out = []
        for k, dim in enumerate(arr.dims):
            if k >= len(idxs) or idxs[k] == "":
                out.append(range(dim))
            elif ".." in idxs[k]:
                a, b = idxs[k].split("..")
                out.append(range(int(a), int(b) + 1))
            else:
                out.append(range(int(idxs[k]), int(idxs[k]) + 1))
        return out

    def flats(self, tok):
        m = VAR.match(tok)
        arr = self.arrs.get(m.group(1)) if m else None
        if arr is None:
            return
        for combo in itertools.product(*self.ranges(arr, IDX.findall(m.group(2)))):
            f = 0
            for d, i in zip(arr.dims, combo):
                f = f * d + i
            yield f

    def scope(self, text):
        """(arity, set of domains, number of tokens, number of distinct arrays) of a scope."""
        arity, doms, ntok, arrays = 0, set(), 0, set()
        for tok in (text or "").split():
            m = VAR.match(tok)
            arr = self.arrs.get(m.group(1)) if m else None
            if arr is None:
                continue
            ntok += 1
            arrays.add(m.group(1))
            idxs = IDX.findall(m.group(2))
            if not arr.over:
                n = 1
                for r in self.ranges(arr, idxs):
                    n *= len(r)
                arity += n
                doms.add(arr.default)
            else:
                for combo in itertools.product(*self.ranges(arr, idxs)):
                    f = 0
                    for d, i in zip(arr.dims, combo):
                        f = f * d + i
                    arity += 1
                    doms.add(arr.over.get(f, arr.default))
        return arity, doms, ntok, len(arrays)


def condition(elem):
    """(operator, kind, constant) of a ``<condition>``: kind is const, range, set or var."""
    cd = elem.find("condition")
    m = re.match(r"\s*\((\w+),\s*(.*)\)\s*$", (cd.text if cd is not None else "") or "")
    if not m:
        return None
    op, rhs = m.group(1), m.group(2).strip()
    if INT.fullmatch(rhs):
        return [op, "const", int(rhs)]
    if re.fullmatch(r"-?\d+\.\.-?\d+", rhs):
        a, b = rhs.split("..")
        return [op, "range", [int(a), int(b)]]
    return [op, "set" if rhs.startswith("{") else "var", None]


def automaton(elem):
    """The automaton of a regular constraint as a transition table over its sorted numeric
    alphabet, or None if it is not numeric or too large."""
    tr = elem.find("transitions")
    trs = re.findall(r"\(([^,()]+),([^,()]+),([^,()]+)\)", (tr.text or "") if tr is not None else "")
    start = elem.find("start")
    final = elem.find("final")
    if not trs or start is None or final is None:
        return None, len(trs)
    states = {}
    for a, _, c in trs:
        for s in (a.strip(), c.strip()):
            states.setdefault(s, len(states))
    try:
        symbols = sorted({int(s.strip()) for _, s, _ in trs})
    except ValueError:
        return None, len(trs)
    if len(states) * len(symbols) > MAX_AUTOMATON_CELLS or start.text.strip() not in states:
        return None, len(trs)
    col = {s: i for i, s in enumerate(symbols)}
    table = [[None] * len(symbols) for _ in states]
    deterministic = True
    for a, s, c in trs:
        cell = table[states[a.strip()]]
        j = col[int(s.strip())]
        if cell[j] is not None:
            deterministic = False
        cell[j] = states[c.strip()]
    fin = [states[s] for s in (final.text or "").split() if s in states]
    return {"symbols": symbols, "table": table, "start": states[start.text.strip()],
            "final": sorted(fin), "deterministic": deterministic}, len(trs)


def process(model, elem, weight, out, max_tuples=5000):
    """Appends (tag, arity, extra, weight) for a constraint element."""
    tag = elem.tag
    if tag in ("instantiation", "annotations"):
        return
    texts = [c.text or "" for c in elem if c.tag in SCOPE_TAGS]
    direct = (elem.text or "").strip()
    if not texts and direct and tag != "intension":
        texts.append(direct)
    extra = {}
    if tag == "intension":
        expr = (elem.text or "").strip()
        names = re.findall(r"[A-Za-z_][\w.]*(?:\[[^\]]*\])*(?=[,)\s]|$)", expr)
        toks = [t for t in names if VAR.match(t) and t.split("[")[0] in model.arrs]
        texts = [" ".join(sorted(set(toks)))]
        extra["fn"] = expr.split("(")[0] if "(" in expr else expr
        extra["nops"] = expr.count("(")
    arity, doms, ntok, narr = 0, set(), 0, 0
    for t in texts:
        a, d, k, n = model.scope(t)
        arity += a
        doms |= d
        ntok += k
        narr += n
    extra["ntok"], extra["narr"] = ntok, narr
    if tag == "sum":
        co = elem.find("coeffs")
        ct = (co.text or "").split() if co is not None else None
        if ct is None:
            extra["coeffs"] = "none"
        else:
            vals = set(ct)
            extra["coeffs"] = "ones" if vals == {"1"} else ("int" if all(INT.fullmatch(v) for v in vals) else "var")
        extra["cond"] = condition(elem)
    elif tag in ("count", "nValues", "cardinality", "atLeast", "atMost", "exactly", "among"):
        v = elem.find("values")
        vt = (v.text or "").split() if v is not None else []
        extra["nvalues"] = len(vt)
        explicit = int_list(v.text) if v is not None else None
        extra["values"] = explicit
        extra["cond"] = condition(elem)
        oc = elem.find("occurs")
        if oc is not None:
            ot = (oc.text or "").split()
            los, his = [], []
            for x in ot:
                if INT.fullmatch(x):
                    los.append(int(x)); his.append(int(x))
                elif re.fullmatch(r"-?\d+\.\.-?\d+", x):
                    a, b = x.split(".."); los.append(int(a)); his.append(int(b))
            extra["occurs"] = "const" if len(los) == len(ot) else "var"
            if len(los) == len(ot) and len(los) <= MAX_VALUES:
                extra["bounds"] = [[a, b] for a, b in zip(los, his)]
            if los and len(los) == len(ot):
                extra["occ_lo"] = round(sum(los) / len(los), 2)
                extra["occ_hi"] = round(sum(his) / len(his), 2)
        if elem.get("closed"):
            extra["closed"] = elem.get("closed")
    elif tag in ("regular", "mdd"):
        if tag == "regular":
            extra["automaton"], extra["trans"] = automaton(elem)
            extra["states"] = len(extra["automaton"]["table"]) if extra["automaton"] else None
        else:
            tr = elem.find("transitions")
            trs = re.findall(r"\(([^,()]+),([^,()]+),([^,()]+)\)", (tr.text or "") if tr is not None else "")
            extra["states"] = len({t[0] for t in trs} | {t[2] for t in trs})
            extra["trans"] = len(trs)
    elif tag == "extension":
        sup, con = elem.find("supports"), elem.find("conflicts")
        node = sup if sup is not None else con
        extra["kind"] = "supports" if sup is not None else "conflicts"
        text = (node.text or "") if node is not None else ""
        extra["star"] = "*" in text
        rows = parse_tuples(text, arity, max_tuples)
        extra["ntuples"] = rows[0]
        if rows[1] is not None and extra["kind"] == "supports" and not extra["star"]:
            extra["table"] = rows[1]
    elif tag == "allDifferent":
        if elem.find("except") is not None:
            extra["except"] = True
        if elem.find("matrix") is not None:
            extra["matrix"] = True
        if len(elem.findall("list")) > 1:
            extra["nlists"] = len(elem.findall("list"))
    if doms and None not in doms:
        union, (lo, hi) = union_size(doms)
        extra.update(u=union, lo=lo, hi=hi, dmin=min(dsize(d) for d in doms), dmax=max(dsize(d) for d in doms))
    elif doms:
        extra["u"] = None
    out.append((tag, arity, json.dumps(extra, sort_keys=True), weight))


def expand_group(model, group, out, sample_size, max_tuples=5000):
    templates = [c for c in group if c.tag != "args"]
    args = [(c.text or "").split() for c in group if c.tag == "args"]
    if not args or not templates:
        return
    n = len(args)
    sample = args if n <= sample_size else [args[i * (n - 1) // (sample_size - 1)] for i in range(sample_size)]
    weight = n / len(sample)

    def fill(a):
        return lambda m: " ".join(a) if m.group(1) == "..." else (a[int(m.group(1))] if int(m.group(1)) < len(a) else "")

    for a in sample:
        for t in templates:
            e = copy.deepcopy(t)
            for sub in e.iter():
                if sub.text and "%" in sub.text:
                    sub.text = SUB.sub(fill(a), sub.text)
            process(model, e, weight, out, max_tuples)


def walk(model, node, out, sample_size, max_tuples=5000):
    for c in node:
        if c.tag == "block":
            walk(model, c, out, sample_size, max_tuples)
        elif c.tag == "group":
            expand_group(model, c, out, sample_size, max_tuples)
        elif c.tag == "slide":
            lst = c.find("list")
            out.append(("slide", model.scope(lst.text if lst is not None else "")[0], "{}", 1))
        else:
            process(model, c, 1, out, max_tuples)


def analyse(path, opts):
    raw = lzma.open(path).read(opts.max_xml + 1)
    if len(raw) > opts.max_xml:
        return {"skipped": "xml larger than --max-xml"}
    root = ET.fromstring(raw)
    model = Model()
    if root.find("variables") is not None:
        model.add_vars(root.find("variables"))
    out = []
    if root.find("constraints") is not None:
        walk(model, root.find("constraints"), out, opts.group_sample, opts.max_tuples)
    agg = Counter()
    for tag, arity, extra, w in out:
        agg[(tag, arity, extra)] += w
    return {"type": root.get("type"), "nvars": model.nvars, "objective": root.find("objectives") is not None,
            "cons": [[t, a, json.loads(e), round(w, 2)] for (t, a, e), w in agg.items()]}


def pick_files(family_dir, opts):
    chosen = []
    for dp, _, fns in os.walk(family_dir):
        files = sorted((os.path.join(dp, f) for f in fns if f.endswith(".lzma")), key=lambda p: (os.path.getsize(p), p))
        if not files:
            continue
        if opts.all:
            chosen += files
            continue
        picks = {files[0], files[len(files) // 2]}
        small = [f for f in files if os.path.getsize(f) <= opts.max_compressed]
        if small:
            picks.add(small[-1])
        chosen += sorted(picks)
    return chosen


def do_family(job):
    family, opts = job
    target = os.path.join(opts.out, family + ".jsonl")
    if os.path.exists(target):
        return family, "cached", 0
    start, n = time.time(), 0
    with open(target + ".tmp", "w") as f:
        for path in pick_files(os.path.join(opts.src, family), opts):
            try:
                rec = analyse(path, opts)
            except Exception as e:  # recorded, so that a bad file does not stop a long run
                rec = {"error": f"{type(e).__name__}: {e}"[:200]}
            rec["file"] = os.path.relpath(path, opts.src)
            rec["size"] = os.path.getsize(path)
            f.write(json.dumps(rec) + "\n")
            n += 1
    os.replace(target + ".tmp", target)
    return family, "done", n, round(time.time() - start, 1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--src", required=True, help="the XCSP3 benchmark directory (one folder per family)")
    p.add_argument("--out", required=True, help="where the per-family .jsonl files go")
    p.add_argument("--all", action="store_true", help="every instance instead of a sample per variant")
    p.add_argument("--families", nargs="*", help="only these families")
    p.add_argument("--max-xml", type=int, default=14_000_000)
    p.add_argument("--max-compressed", type=int, default=1_500_000)
    p.add_argument("--group-sample", type=int, default=60)
    p.add_argument("--max-tuples", type=int, default=5000, help="largest table whose tuples are recorded")
    p.add_argument("--jobs", type=int, default=2)
    p.add_argument("--budget", type=float, default=float("inf"), help="stop starting families after this many seconds")
    opts = p.parse_args()
    os.makedirs(opts.out, exist_ok=True)
    families = sorted(d for d in os.listdir(opts.src) if os.path.isdir(os.path.join(opts.src, d)))
    if opts.families:
        families = [f for f in families if f in opts.families]
    todo = [f for f in families if not os.path.exists(os.path.join(opts.out, f + ".jsonl"))]
    print(f"{len(families)} families, {len(todo)} to do", flush=True)
    deadline = time.time() + opts.budget
    with Pool(opts.jobs) as pool:
        for result in pool.imap_unordered(do_family, [(f, opts) for f in todo]):
            print(result, flush=True)
            if time.time() > deadline:
                pool.terminate()
                print("budget spent: run again to continue", flush=True)
                return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
