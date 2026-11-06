"use strict";
(function () {
  const D = JSON.parse(document.getElementById("data").textContent);
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const N = D.commits.length;

  // ---- metric names: suite/series/metric -------------------------------------------------
  const parse = (name) => {
    const p = name.split("/");
    return { suite: p[0], series: p.slice(1, -1).join("/"), metric: p[p.length - 1], name };
  };
  const parsed = Object.keys(D.metrics).map(parse);
  const suites = [...new Set(parsed.map((m) => m.suite))];
  const better = (suite) => (D.suites[suite] && D.suites[suite].better) || "lower";
  const unit = (suite) => (D.suites[suite] && D.suites[suite].unit) || "";

  const state = { suite: suites[0], metric: null, filter: "", relative: false, log: false, threshold: 2, off: new Set(), pinned: null, moverKind: "all", moverLimit: 15 };

  const metricsOf = (suite) => [...new Set(parsed.filter((m) => m.suite === suite).map((m) => m.metric))];
  const seriesOf = (suite, metric) => parsed.filter((m) => m.suite === suite && m.metric === metric).map((m) => m.series);
  state.metric = state.suite ? metricsOf(state.suite)[0] : null;

  const fmt = (v) => (v === null || v === undefined ? "–" : Math.abs(v) >= 100 ? Math.round(v).toLocaleString("en") : (Math.round(v * 10) / 10).toLocaleString("en"));
  const pct = (r) => Math.abs(r - 1) < 1e-9 ? "0.0%" : (r >= 1 ? "+" : "−") + Math.abs((r - 1) * 100).toFixed(1) + "%";
  const short = (sha) => sha.slice(0, 7);
  const day = (iso) => iso.slice(0, 10);
  const link = (c) => `https://github.com/${D.repo}/commit/${c.sha}`;
  const isGood = (suite, ratio) => (better(suite) === "lower" ? ratio < 1 : ratio > 1);

  // ---- summary ---------------------------------------------------------------------------
  function renderSummary() {
    if (!N) { $("summary").textContent = "No benchmark data yet."; return; }
    const last = D.commits[N - 1];
    $("summary").textContent = `${N} commit${N > 1 ? "s" : ""} from ${day(D.commits[0].date)} to ${day(last.date)}, ${parsed.length} metrics. Latest: ${short(last.sha)} (aicad ${last.version}).`;
  }

  // ---- steps -----------------------------------------------------------------------------
  function stepsOf(name) {
    const vals = D.metrics[name], out = [];
    let prev = null;
    for (let i = 0; i < N; i++) {
      const v = vals[i];
      if (v === null || v === undefined) continue;
      if (prev !== null) {
        const before = vals[prev];
        const ratio = before === 0 ? (v === 0 ? 1 : Infinity) : v / before;
        out.push({ i, prev, before, after: v, ratio });
      }
      prev = i;
    }
    return out;
  }

  // ---- biggest steps ---------------------------------------------------------------------
  function renderMovers() {
    const ctl = $("mover-controls");
    ctl.innerHTML = `<label>Show <select id="mk"><option value="all">all steps</option><option value="good">improvements</option><option value="bad">regressions</option></select></label>`;
    $("mk").value = state.moverKind;
    $("mk").onchange = (e) => { state.moverKind = e.target.value; state.moverLimit = 15; renderMovers(); };
    const rows = [];
    for (const m of parsed) {
      if (m.suite !== state.suite) continue;
      for (const s of stepsOf(m.name)) {
        if (Math.abs(s.ratio - 1) * 100 <= state.threshold) continue;
        const good = isGood(m.suite, s.ratio);
        if (state.moverKind === "good" && !good) continue;
        if (state.moverKind === "bad" && good) continue;
        rows.push({ m, s, good });
      }
    }
    rows.sort((a, b) => Math.abs(Math.log(b.s.ratio)) - Math.abs(Math.log(a.s.ratio)));
    if (!rows.length) { $("movers").innerHTML = `<p class="empty">No step larger than ${state.threshold}%${N < 2 ? " (needs at least two commits)" : ""}.</p>`; return; }
    const shown = rows.slice(0, state.moverLimit);
    $("movers").innerHTML = `<table><thead><tr><th>Metric</th><th>Commit</th><th class="num">Before</th><th class="num">After</th><th class="num">Change</th></tr></thead><tbody>` +
      shown.map((r, k) => {
        const c = D.commits[r.s.i];
        return `<tr class="click" data-k="${k}"><td>${esc(r.m.series)} · <code>${esc(r.m.metric)}</code></td>` +
          `<td><a href="${link(c)}" target="_blank" rel="noopener">${short(c.sha)}</a> ${esc(c.subject)}</td>` +
          `<td class="num">${fmt(r.s.before)}</td><td class="num">${fmt(r.s.after)}</td>` +
          `<td class="num ${r.good ? "good" : "bad"}">${isFinite(r.s.ratio) ? pct(r.s.ratio) : "new"}</td></tr>`;
      }).join("") + `</tbody></table>` +
      (rows.length > shown.length ? `<p><button id="more">Show ${Math.min(30, rows.length - shown.length)} more of ${rows.length - shown.length}</button></p>` : "");
    $("movers").querySelectorAll("tr.click").forEach((tr) => tr.addEventListener("click", (e) => {
      if (e.target.tagName === "A") return;
      const r = shown[+tr.dataset.k];
      state.metric = r.m.metric; state.filter = r.m.series; state.off.clear(); state.pinned = r.s.i;
      renderControls(); renderTimeline();
      $("controls").scrollIntoView({ behavior: "smooth", block: "start" });
    }));
    const more = $("more");
    if (more) more.onclick = () => { state.moverLimit += 30; renderMovers(); };
  }

  // ---- timeline controls -----------------------------------------------------------------
  function renderControls() {
    if (!state.suite) { $("controls").innerHTML = ""; return; }
    const opt = (list, cur) => list.map((x) => `<option${x === cur ? " selected" : ""}>${esc(x)}</option>`).join("");
    $("controls").innerHTML =
      (suites.length > 1 ? `<label>Suite <select id="cs">${opt(suites, state.suite)}</select></label>` : "") +
      `<label>Metric <select id="cm">${opt(metricsOf(state.suite), state.metric)}</select></label>` +
      `<label>Filter <input type="text" id="cf" value="${esc(state.filter)}" placeholder="e.g. sum"></label>` +
      `<button id="cr" aria-pressed="${state.relative}" title="Divide each line by its first value">relative to first</button>` +
      `<button id="cl" aria-pressed="${state.log}" title="Logarithmic y axis">log scale</button>` +
      `<label>Step threshold <input type="number" id="ct" min="0" step="0.5" value="${state.threshold}">%</label>`;
    const cs = $("cs"); if (cs) cs.onchange = (e) => { state.suite = e.target.value; state.metric = metricsOf(state.suite)[0]; state.filter = ""; state.off.clear(); state.pinned = null; renderAll(); };
    $("cm").onchange = (e) => { state.metric = e.target.value; state.off.clear(); renderTimeline(); };
    $("cf").oninput = (e) => { state.filter = e.target.value; renderTimeline(); };
    $("cr").onclick = () => { state.relative = !state.relative; $("cr").setAttribute("aria-pressed", state.relative); renderTimeline(); };
    $("cl").onclick = () => { state.log = !state.log; $("cl").setAttribute("aria-pressed", state.log); renderTimeline(); };
    $("ct").onchange = (e) => { state.threshold = Math.max(0, +e.target.value || 0); renderMovers(); renderTimeline(); };
  }

  // ---- timeline chart --------------------------------------------------------------------
  const W = 900, H = 380, M = { l: 66, r: 16, t: 14, b: 44 };
  const x = (i) => M.l + (N === 1 ? (W - M.l - M.r) / 2 : (i * (W - M.l - M.r)) / (N - 1));

  function niceTicks(lo, hi, n) {
    if (hi <= lo) hi = lo + 1;
    const raw = (hi - lo) / n, p = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / p, step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * p;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toPrecision(12));
    return out;
  }

  let view = null; // what the chart currently shows, for the hover handlers

  function renderTimeline() {
    const svg = $("chart"), legend = $("legend");
    if (!state.suite || !N) { svg.innerHTML = ""; legend.innerHTML = ""; $("detail").innerHTML = ""; return; }
    const all = seriesOf(state.suite, state.metric);
    const f = state.filter.trim().toLowerCase();
    const matching = all.filter((s) => !f || s.toLowerCase().includes(f));
    const colorOf = (s) => `var(--c${all.indexOf(s) % 10})`;
    legend.innerHTML = matching.map((s) => `<span class="chip${state.off.has(s) ? " off" : ""}" data-s="${esc(s)}"><i style="background:${colorOf(s)}"></i>${esc(s)}</span>`).join("");
    legend.querySelectorAll(".chip").forEach((el) => el.onclick = () => { const s = el.dataset.s; state.off.has(s) ? state.off.delete(s) : state.off.add(s); renderTimeline(); });
    const visible = matching.filter((s) => !state.off.has(s));

    const lines = visible.map((s) => {
      const raw = D.metrics[`${state.suite}/${s}/${state.metric}`];
      const base = raw.find((v) => v !== null && v !== undefined);
      const vals = raw.map((v) => (v === null || v === undefined ? null : state.relative ? (base ? (v / base) * 100 : 100) : v));
      return { s, raw, vals, color: colorOf(s) };
    });
    const flat = lines.flatMap((l) => l.vals.filter((v) => v !== null));
    if (!flat.length) { svg.setAttribute("viewBox", `0 0 ${W} 120`); svg.innerHTML = `<text x="${W / 2}" y="60" text-anchor="middle">nothing to show</text>`; view = null; return; }

    const log = state.log && !state.relative && flat.every((v) => v > 0);
    let lo = Math.min(...flat), hi = Math.max(...flat);
    if (!log && !state.relative) lo = Math.min(0, lo);
    if (state.relative) { const pad = Math.max((hi - lo) * 0.08, 1); lo -= pad; hi += pad; }
    if (log) { lo = Math.pow(10, Math.floor(Math.log10(lo))); hi = Math.pow(10, Math.ceil(Math.log10(hi))); if (hi === lo) hi = lo * 10; }
    else if (hi === lo) hi = lo + 1;
    const ty = (v) => (log ? (Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo)) : (v - lo) / (hi - lo));
    const y = (v) => H - M.b - ty(v) * (H - M.t - M.b);

    let ticks = log ? [] : niceTicks(lo, hi, 5);
    if (log) for (let e = Math.log10(lo); e <= Math.log10(hi) + 1e-9; e++) ticks.push(Math.pow(10, e));
    let g = "";
    for (const t of ticks) g += `<line class="grid" x1="${M.l}" x2="${W - M.r}" y1="${y(t)}" y2="${y(t)}"/><text x="${M.l - 6}" y="${y(t) + 4}" text-anchor="end">${esc(state.relative ? t.toFixed(0) + "%" : fmt(t))}</text>`;
    const nx = Math.min(N, 6);
    for (let k = 0; k < nx; k++) {
      const i = nx === 1 ? 0 : Math.round((k * (N - 1)) / (nx - 1)), c = D.commits[i];
      const anchor = k === nx - 1 && nx > 1 ? "end" : "middle";
      g += `<text x="${x(i)}" y="${H - M.b + 16}" text-anchor="${anchor}">${short(c.sha)}</text><text x="${x(i)}" y="${H - M.b + 29}" text-anchor="${anchor}">${day(c.date)}</text>`;
    }
    g += `<line class="axis" x1="${M.l}" x2="${M.l}" y1="${M.t}" y2="${H - M.b}"/><line class="axis" x1="${M.l}" x2="${W - M.r}" y1="${H - M.b}" y2="${H - M.b}"/>`;
    g += `<text x="14" y="${(H - M.b + M.t) / 2}" text-anchor="middle" transform="rotate(-90 14 ${(H - M.b + M.t) / 2})">${esc(state.relative ? "% of first value" : unit(state.suite))}</text>`;
    D.commits.forEach((c, i) => { if (c.breaks.length) g += `<line class="brk" x1="${x(i)}" x2="${x(i)}" y1="${M.t}" y2="${H - M.b}"><title>${esc(short(c.sha))}: the measurement setup changed (${esc(c.breaks.join(", "))})</title></line>`; });

    for (const l of lines) {
      let d = "", pen = false;
      l.vals.forEach((v, i) => { if (v === null) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`; pen = true; });
      g += `<path d="${d}" fill="none" stroke="${l.color}" stroke-width="1.8" stroke-linejoin="round"/>`;
      if (N <= 60) l.vals.forEach((v, i) => { if (v !== null) g += `<circle cx="${x(i)}" cy="${y(v)}" r="2.4" fill="${l.color}"/>`; });
      let prev = null;
      l.raw.forEach((v, i) => {
        if (v === null || v === undefined) return;
        if (prev !== null) {
          const b = l.raw[prev], r = b === 0 ? (v === 0 ? 1 : Infinity) : v / b;
          if (Math.abs(r - 1) * 100 > state.threshold) g += `<circle cx="${x(i)}" cy="${y(l.vals[i])}" r="6" fill="none" stroke="var(--${isGood(state.suite, r) ? "good" : "bad"})" stroke-width="2"/>`;
        }
        prev = i;
      });
    }
    g += `<line id="cross" class="axis" y1="${M.t}" y2="${H - M.b}" visibility="hidden" stroke-dasharray="2 2"/><line id="pin" y1="${M.t}" y2="${H - M.b}" stroke="var(--accent)" stroke-width="1.5" visibility="hidden"/>`;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = g;
    view = { lines };
    placePin();
    renderDetail();
  }

  function indexAt(ev) {
    const r = $("chart").getBoundingClientRect(), vx = ((ev.clientX - r.left) / r.width) * W;
    if (N === 1) return 0;
    return Math.max(0, Math.min(N - 1, Math.round(((vx - M.l) / (W - M.l - M.r)) * (N - 1))));
  }

  function placePin() {
    const pin = $("pin"); if (!pin) return;
    if (state.pinned === null || state.pinned >= N) { pin.setAttribute("visibility", "hidden"); return; }
    pin.setAttribute("x1", x(state.pinned)); pin.setAttribute("x2", x(state.pinned)); pin.setAttribute("visibility", "visible");
  }

  function renderDetail() {
    const el = $("detail");
    if (state.pinned === null || !view) { el.innerHTML = ""; return; }
    const i = state.pinned, c = D.commits[i];
    const rows = view.lines.map((l) => {
      const v = l.raw[i]; let p = null; for (let k = i - 1; k >= 0; k--) if (l.raw[k] !== null && l.raw[k] !== undefined) { p = l.raw[k]; break; }
      const r = v === null || p === null ? null : p === 0 ? (v === 0 ? 1 : Infinity) : v / p;
      return `<tr><td><span class="chip" style="cursor:default"><i style="background:${l.color}"></i>${esc(l.s)}</span></td><td class="num">${fmt(p)}</td><td class="num">${fmt(v)}</td><td class="num ${r === null || r === 1 ? "" : isGood(state.suite, r) ? "good" : "bad"}">${r === null ? "" : isFinite(r) ? pct(r) : "new"}</td></tr>`;
    }).join("");
    el.innerHTML = `<p><b><a href="${link(c)}" target="_blank" rel="noopener">${short(c.sha)}</a></b> ${esc(c.subject)}</p>` +
      `<p class="hint">${esc(c.date)} · aicad ${esc(c.version)} · ${esc(c.rustc)} · ${esc(c.profile)} opt-level ${esc(c.opt_level)}${c.valgrind ? " · " + esc(c.valgrind) : ""}</p>` +
      (c.breaks.length ? `<p class="bad">The measurement setup changed at this commit (${esc(c.breaks.join(", "))}).</p>` : "") +
      `<table><thead><tr><th>Series</th><th class="num">Previous</th><th class="num">At this commit</th><th class="num">Change</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  function wireChart() {
    const svg = $("chart"), tip = $("tooltip");
    svg.addEventListener("mousemove", (ev) => {
      if (!view) return;
      const i = indexAt(ev), c = D.commits[i], cross = $("cross");
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      const rows = view.lines.filter((l) => l.raw[i] !== null && l.raw[i] !== undefined).sort((a, b) => b.raw[i] - a.raw[i]).slice(0, 10)
        .map((l) => `<div class="row"><span><span style="color:${l.color}">●</span> ${esc(l.s)}</span><span>${fmt(l.raw[i])}</span></div>`).join("");
      tip.innerHTML = `<b>${short(c.sha)} · ${day(c.date)}</b>${esc(c.subject)}${rows}`;
      tip.hidden = false;
      const r = svg.getBoundingClientRect(), w = $("chart-wrap").getBoundingClientRect();
      const px = (x(i) / W) * r.width + (r.left - w.left);
      tip.style.left = Math.min(px + 12, w.width - tip.offsetWidth - 4) + "px";
      tip.style.top = "8px";
    });
    svg.addEventListener("mouseleave", () => { tip.hidden = true; const cr = $("cross"); if (cr) cr.setAttribute("visibility", "hidden"); });
    svg.addEventListener("click", (ev) => { if (!view) return; state.pinned = indexAt(ev); placePin(); renderDetail(); });
  }

  // ---- commits ---------------------------------------------------------------------------
  function renderCommits() {
    $("commits").innerHTML = `<table><thead><tr><th>Commit</th><th>Date</th><th>Subject</th><th class="hide-small">aicad</th><th class="hide-small">Setup</th></tr></thead><tbody>` +
      D.commits.slice().reverse().map((c) => `<tr><td><a href="${link(c)}" target="_blank" rel="noopener">${short(c.sha)}</a></td><td>${day(c.date)}</td><td>${esc(c.subject)}</td><td class="hide-small">${esc(c.version)}</td><td class="hide-small">${c.breaks.length ? esc("changed: " + c.breaks.join(", ")) : ""}</td></tr>`).join("") + `</tbody></table>`;
  }

  function renderAll() { renderSummary(); renderMovers(); renderControls(); renderTimeline(); renderCommits(); }
  wireChart();
  renderAll();
})();
