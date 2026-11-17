"use strict";
/* FinPilot frontend: vanilla JS, no build step, no external requests. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x, d = 1) => x == null ? "—" : (x * 100).toFixed(d) + "%";
const spct = (x, d = 2) => x == null ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%";
const num = (x, d = 2) => x == null ? "—" : Number(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const PALETTE = ["#4c8dff", "#3ddc97", "#ffb84d", "#8f7bff", "#35c6d6", "#e08ad6", "#9db4d8", "#6fa8dc", "#c9d36a", "#f08a5d"];

async function api(path, opts) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch { /* non-JSON */ }
  if (!r.ok) {
    const d = body && body.detail;
    throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : r.statusText);
  }
  return body;
}

const PRESETS = { "3M": 3, "6M": 6, "1Y": 12, "3Y": 36, "5Y": 60, "MAX": null };
const PROMPTS = [
  "What is driving my portfolio risk?",
  "Simulate a 10% market decline.",
  "Why is risk rising and what could make losses worse?",
  "Compare a +100bp rate shock with a 15% technology selloff.",
];
const S = { dash: null, selected: null, preset: "1Y", start: null, end: null, allocMode: "holdings",
            job: null, runId: null, timer: null, lastEvidence: [], lastQuestion: "" };

/* ------------------------------------------------------------------ banner / routing */
function banner(msg) { const b = $("#banner"); b.textContent = msg || ""; b.classList.toggle("hidden", !msg); }
const TITLES = { overview: "Overview", portfolio: "Portfolio Analysis", research: "Research Workspace", scenario: "Scenario Lab", reports: "Reports & History", activity: "Agent Activity" };
function showView(name) {
  const base = ["portfolio", "research"].includes(name) ? "overview" : name;
  $$(".view").forEach(v => v.classList.toggle("hidden", v.id !== "view-" + base));
  $$("#nav button").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  $("#viewTitle").textContent = TITLES[name] || "Overview";
  if (name === "portfolio") $("#analytics").scrollIntoView({ behavior: "smooth" });
  if (name === "research") { $("#ask").scrollIntoView({ behavior: "smooth" }); $("#q").focus(); }
  if (name === "reports") loadHistory();
  if (name === "activity") renderActivity();
  if (name === "scenario") fillSectors();
}
$("#nav").addEventListener("click", e => { const b = e.target.closest("button"); if (b) showView(b.dataset.view); });

/* ------------------------------------------------------------------ dashboard */
let reloadT = null;
const reloadSoon = () => { clearTimeout(reloadT); reloadT = setTimeout(loadDash, 250); };

async function loadDash() {
  const q = new URLSearchParams();
  if (S.start) q.set("start", S.start);
  if (S.end) q.set("end", S.end);
  if (S.selected) q.set("tickers", [...S.selected].join(","));
  try {
    S.dash = await api("/api/dashboard?" + q);
    banner("");
  } catch (e) {
    banner("Dashboard data unavailable: " + e.message);
    return;
  }
  if (!S.selected) S.selected = new Set(S.dash.universe.map(u => u.ticker));
  renderDash(S.dash);
}

function presetRange(key) {
  const b = S.dash.bounds;
  S.preset = key; S.end = b.max;
  if (PRESETS[key] == null) { S.start = b.min; return; }
  const d = new Date(b.max + "T00:00:00Z");
  d.setUTCMonth(d.getUTCMonth() - PRESETS[key]);
  const s = d.toISOString().slice(0, 10);
  S.start = s < b.min ? b.min : s;
}

function renderDash(d) {
  const sb = $("#srcBadge");
  sb.textContent = d.is_sample ? "SAMPLE DATA (synthetic)" : "Market data: " + d.data_source;
  sb.className = "badge " + (d.is_sample ? "sample" : "live");
  $("#asOfBadge").textContent = "Prices as of " + d.as_of;
  renderStrip(d); renderCards(d); renderToolbar(d); renderPerf(d); renderAlloc(d); renderHeat(d); renderRiskTable(d);
}

function renderStrip(d) {
  const m = d.market, open = m.status === "OPEN";
  $("#strip").innerHTML = d.market_strip.map(t => `
    <div class="tick" title="${esc(t.name)}">
      <div class="t-sym">${esc(t.ticker)}</div>
      <div class="t-px mono">${num(t.price)}</div>
      <div class="t-chg mono ${t.change_pct >= 0 ? "up" : "down"}">${spct(t.change_pct)} 1d</div>
      <div class="t-ts">close ${esc(t.as_of)}</div>
    </div>`).join("") + `
    <div class="tick status ${open ? "open" : ""}" title="${esc(m.note || "")}">
      <div class="t-sym"><span class="dot"></span>US market ${esc(m.status)}</div>
      <div class="t-ts">${esc(m.as_of_et || "")}</div><div class="t-ts">end-of-day data</div>
    </div>`;
}

function renderCards(d) {
  const c = d.cards, p = d.period;
  const per = `<div class="per">${esc(p.start)} → ${esc(p.end)} · ${p.days} trading days<br>data as of ${esc(d.as_of)}</div>`;
  const val = c.portfolio_value;
  const cards = [
    { cls: "mint", label: "Portfolio value",
      main: val.amount != null ? "$" + num(val.amount, 0) : "Index " + num(val.index_level, 1),
      sub: esc(val.basis) + (val.amount == null ? ". No quantities supplied, so no currency value." : "") },
    { cls: "", label: "Annualized volatility", main: pct(c.volatility.value),
      sub: esc(c.volatility.basis) + ". Historical estimate, not a forecast of future losses." },
    { cls: "warn", label: "Maximum drawdown", main: pct(c.max_drawdown.value),
      sub: `Peak ${esc(c.max_drawdown.peak_date)} → trough ${esc(c.max_drawdown.trough_date)}. Current: ${pct(c.max_drawdown.current)}. Realized in this window only.` },
    { cls: "", label: "Largest risk contributor", main: esc(c.top_contributor.ticker),
      sub: `${pct(c.top_contributor.pct_of_risk)} of portfolio volatility at a ${pct(c.top_contributor.weight)} weight.` },
  ];
  $("#cards").innerHTML = cards.map(k => `<div class="card ${k.cls}"><div class="lbl">${k.label}</div><div class="val mono">${k.main}</div><div class="sub">${k.sub}</div>${per}</div>`).join("");
}

function renderToolbar(d) {
  $("#presets").innerHTML = Object.keys(PRESETS).map(k => `<button data-p="${k}" class="${S.preset === k ? "active" : ""}">${k}</button>`).join("");
  $("#dFrom").min = $("#dTo").min = d.bounds.min; $("#dFrom").max = $("#dTo").max = d.bounds.max;
  $("#dFrom").value = d.period.start; $("#dTo").value = d.period.end;
  $("#assets").innerHTML = d.universe.map(u => `<button class="asset ${S.selected.has(u.ticker) ? "on" : ""}" data-t="${esc(u.ticker)}" title="${esc(u.name)} · ${esc(u.sector)}">${esc(u.ticker)}</button>`).join("");
  const notes = [];
  if (d.dropped_holdings.length) notes.push(`Excluded (no data for the full range): ${d.dropped_holdings.join(", ")}.`);
  notes.push("Weights are re-normalised across the selected assets and held constant (daily rebalanced).");
  $("#rangeNote").textContent = notes.join(" ");
}
$("#presets").addEventListener("click", e => { const b = e.target.closest("button"); if (b && S.dash) { presetRange(b.dataset.p); loadDash(); } });
["dFrom", "dTo"].forEach(id => $("#" + id).addEventListener("change", () => { S.preset = null; S.start = $("#dFrom").value; S.end = $("#dTo").value; reloadSoon(); }));
$("#assets").addEventListener("click", e => {
  const b = e.target.closest(".asset"); if (!b) return;
  const t = b.dataset.t;
  if (S.selected.has(t)) { if (S.selected.size <= 2) return banner("Keep at least two assets selected."); S.selected.delete(t); } else S.selected.add(t);
  banner(""); reloadSoon();
});

/* ------------------------------------------------------------------ charts */
function renderPerf(d) {
  const p = d.performance, n = p.dates.length, W = 800, H = 250, m = { l: 42, r: 10, t: 8, b: 20 };
  $("#perfLegend").innerHTML = `<span><i style="background:var(--mint)"></i>Portfolio (selected assets)</span><span><i style="background:var(--blue)"></i>${esc(p.benchmark_name)}</span><span>rebased to 100</span>`;
  const all = p.portfolio.concat(p.benchmark);
  let lo = Math.min(...all), hi = Math.max(...all); const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  const x = i => m.l + i * (W - m.l - m.r) / Math.max(1, n - 1), y = v => m.t + (hi - v) * (H - m.t - m.b) / (hi - lo);
  const path = vs => vs.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1)).join("");
  let g = "";
  for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4; g += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="#22304d" stroke-width="1"/><text x="${m.l - 6}" y="${y(v) + 3}" text-anchor="end">${v.toFixed(0)}</text>`; }
  for (let k = 0; k <= 4; k++) { const i = Math.round(k * (n - 1) / 4); g += `<text x="${x(i)}" y="${H - 5}" text-anchor="${k === 0 ? "start" : k === 4 ? "end" : "middle"}">${p.dates[i]}</text>`; }
  $("#perf").innerHTML = `<svg id="perfSvg" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Historical performance chart">${g}
    <path d="${path(p.benchmark)}" fill="none" stroke="#4c8dff" stroke-width="1.6"/><path d="${path(p.portfolio)}" fill="none" stroke="#3ddc97" stroke-width="2"/>
    <line id="cross" y1="${m.t}" y2="${H - m.b}" stroke="#8193b5" stroke-dasharray="3 3" class="hidden"/></svg>`;
  const svg = $("#perfSvg"), tip = $("#perfTip"), cross = $("#cross");
  svg.addEventListener("pointermove", e => {
    const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * W / r.width;
    const i = Math.max(0, Math.min(n - 1, Math.round((px - m.l) / (W - m.l - m.r) * (n - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.classList.remove("hidden");
    tip.innerHTML = `${p.dates[i]}<br><span class="up mono" style="color:var(--mint)">Portfolio ${num(p.portfolio[i], 1)}</span><br><span class="mono" style="color:#9dc0ff">${esc(p.benchmark_name)} ${num(p.benchmark[i], 1)}</span>`;
    tip.style.left = Math.max(70, Math.min(r.width - 70, x(i) / W * r.width)) + "px"; tip.classList.remove("hidden");
  });
  svg.addEventListener("pointerleave", () => { tip.classList.add("hidden"); cross.classList.add("hidden"); });
}

function renderAlloc(d) {
  const items = S.allocMode === "holdings"
    ? d.allocation.holdings.map(h => ({ label: h.ticker, w: h.weight, tip: h.sector }))
    : d.allocation.sectors.map(s => ({ label: s.sector, w: s.weight, tip: "" }));
  const R = 56, C = 2 * Math.PI * R; let acc = 0;
  const arcs = items.map((it, i) => { const a = `<circle r="${R}" cx="80" cy="80" fill="none" stroke="${PALETTE[i % PALETTE.length]}" stroke-width="22" stroke-dasharray="${it.w * C} ${C}" stroke-dashoffset="${-acc * C}" transform="rotate(-90 80 80)"><title>${esc(it.label)} ${pct(it.w)}</title></circle>`; acc += it.w; return a; }).join("");
  $("#alloc").innerHTML = `<svg width="150" height="150" viewBox="0 0 160 160" role="img" aria-label="Asset allocation">${arcs}</svg>
    <ul>${items.map((it, i) => `<li title="${esc(it.tip)}"><span><i style="background:${PALETTE[i % PALETTE.length]}"></i>${esc(it.label)}</span><span class="mono">${pct(it.w)}</span></li>`).join("")}</ul>`;
}
$("#allocMode").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; S.allocMode = b.dataset.m; $$("#allocMode button").forEach(x => x.classList.toggle("active", x === b)); if (S.dash) renderAlloc(S.dash); });

function renderHeat(d) {
  const t = d.correlation.tickers, M = d.correlation.matrix;
  let h = `<div class="heat" style="grid-template-columns:34px repeat(${t.length},1fr)"><div class="hd"></div>${t.map(x => `<div class="hd">${esc(x)}</div>`).join("")}`;
  M.forEach((row, i) => { h += `<div class="hd">${esc(t[i])}</div>` + row.map((v, j) => {
    const a = Math.min(1, Math.abs(v)).toFixed(2), bg = i === j ? "#1b2744" : v >= 0 ? `rgba(76,141,255,${a})` : `rgba(255,184,77,${a})`;
    return `<div style="background:${bg}" title="${esc(t[i])} / ${esc(t[j])}: ${v}">${i === j ? "" : v.toFixed(2)}</div>`; }).join(""); });
  $("#heat").innerHTML = h + `</div><div class="muted small" style="margin-top:6px">Blue = positive, amber = negative correlation.</div>`;
}

function renderRiskTable(d) {
  const max = Math.max(...d.risk_table.map(r => Math.abs(r.pct_contribution)), 0.0001);
  $("#riskTable").innerHTML = `<thead><tr><th>Asset</th><th class="l">Sector</th><th>Weight</th><th>Stand-alone vol</th><th>Vol contribution</th><th>% of risk</th></tr></thead><tbody>` +
    d.risk_table.map(r => `<tr><td title="${esc(r.name)}"><b>${esc(r.ticker)}</b></td><td class="l muted">${esc(r.sector)}</td><td class="mono">${pct(r.weight)}</td><td class="mono">${pct(r.standalone_vol)}</td><td class="mono">${pct(r.contribution_vol, 2)}</td>
      <td class="mono">${pct(r.pct_contribution)}<span class="bar" style="width:${Math.round(Math.abs(r.pct_contribution) / max * 60)}px"></span></td></tr>`).join("") + "</tbody>";
}

/* ------------------------------------------------------------------ markdown + evidence links */
const CITE = /\[((?:CALC|NEWS)-\d{3})(?::(\d+))?\]/g;
function md(src, runId) {
  const inline = t => esc(t).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(CITE, (_, id, ix) => `<button class="cite" data-run="${esc(runId || "")}" data-id="${id}" data-idx="${ix || ""}">${id}${ix ? ":" + ix : ""}</button>`);
  let out = "", list = null, tbl = [];
  const endList = () => { if (list) { out += `</${list}>`; list = null; } };
  const endTbl = () => {
    if (!tbl.length) return;
    const rows = tbl.filter(r => !/^\|?\s*:?-{2,}/.test(r)).map(r => r.replace(/^\||\|$/g, "").split("|").map(c => c.trim()));
    out += "<div class='table-wrap'><table><thead><tr>" + rows[0].map(c => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
      rows.slice(1).map(r => "<tr>" + r.map(c => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table></div>";
    tbl = [];
  };
  for (const raw of String(src).split(/\r?\n/)) {
    const l = raw.trim();
    if (l.startsWith("|")) { endList(); tbl.push(l); continue; }
    endTbl();
    let m;
    if (!l) { endList(); continue; }
    if ((m = l.match(/^(#{1,3})\s+(.*)/))) { endList(); out += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`; }
    else if (/^---+$/.test(l)) { endList(); out += "<hr>"; }
    else if ((m = l.match(/^>\s?(.*)/))) { endList(); out += `<blockquote>${inline(m[1])}</blockquote>`; }
    else if ((m = l.match(/^[-*+]\s+(.*)/))) { if (list !== "ul") { endList(); out += "<ul>"; list = "ul"; } out += `<li>${inline(m[1])}</li>`; }
    else if ((m = l.match(/^\d+\.\s+(.*)/))) { if (list !== "ol") { endList(); out += "<ol>"; list = "ol"; } out += `<li>${inline(m[1])}</li>`; }
    else { endList(); out += `<p>${inline(l)}</p>`; }
  }
  endList(); endTbl();
  return out;
}
function section(markdown, name) {
  const lines = markdown.split(/\r?\n/); let on = false; const out = [];
  for (const l of lines) {
    if (/^#{1,3}\s/.test(l)) { if (on) break; on = l.toLowerCase().includes(name); continue; }
    if (on) out.push(l);
  }
  return out.join("\n").trim();
}

document.addEventListener("click", e => { const c = e.target.closest(".cite, .ev[data-id]"); if (c) openEvidence(c.dataset.run || S.runId, c.dataset.id, c.dataset.idx); });
$("#drawerClose").addEventListener("click", () => $("#drawer").classList.add("hidden"));
$("#drawer").addEventListener("click", e => { if (e.target.id === "drawer") $("#drawer").classList.add("hidden"); });
document.addEventListener("keydown", e => { if (e.key === "Escape") $("#drawer").classList.add("hidden"); });

async function openEvidence(runId, id, idx) {
  const body = $("#drawerBody"); $("#drawerTitle").textContent = "Evidence " + id + (idx ? ":" + idx : "");
  $("#drawer").classList.remove("hidden");
  if (!runId) { body.innerHTML = `<p class="muted">Evidence is available once the investigation has finished.</p>`; return; }
  body.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const ev = await api(`/api/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(id)}`);
    let h = `<p><b>${esc(ev.tool)}</b> <span class="muted small">${esc(ev.kind)} · ${esc(ev.created_at)}${ev.data_source ? " · data: " + esc(ev.data_source) : ""}</span></p>
      <p class="muted small">Arguments</p><pre>${esc(JSON.stringify(ev.args, null, 2))}</pre>`;
    if (ev.kind === "NEWS") {
      h += `<p class="muted small">Items with verified publication dates (${esc(ev.result.retrieved_at || "")}). Query: ${esc(ev.result.query)}</p><ul class="news">` +
        (ev.result.items || []).map(it => `<li class="${String(it.index) === String(idx) ? "hl" : ""}"><b>#${it.index}</b> <a href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">${esc(it.title)}</a><br>
          <span class="muted small">${esc(it.source)} · ${esc(it.published_utc)} UTC · ${it.matched_holdings.length ? "names " + esc(it.matched_holdings.join(", ")) : "no holding named: any link is a hypothesis"}</span></li>`).join("") + "</ul>";
    } else h += `<p class="muted small">Result</p><pre>${esc(JSON.stringify(ev.result, null, 2))}</pre>`;
    body.innerHTML = h;
  } catch (err) { body.innerHTML = `<p class="muted">Could not load evidence: ${esc(err.message)}</p>`; }
}

/* ------------------------------------------------------------------ Ask FinPilot */
$("#prompts").innerHTML = PROMPTS.map(p => `<button type="button">${esc(p)}</button>`).join("");
$("#prompts").addEventListener("click", e => { const b = e.target.closest("button"); if (b) { $("#q").value = b.textContent; $("#q").focus(); } });
$("#q").addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) startInvestigation(); });
$("#run").addEventListener("click", startInvestigation);

const setState = (t, cls) => { const p = $("#askState"); p.textContent = t; p.className = "pill " + (cls || ""); };
const feedHtml = ev => ev.map(e => `<button class="ev ${e.kind}" data-id="${esc(e.id)}" title="${esc(e.tool)}">${esc(e.id)} ${esc(e.tool)}</button>`).join("");

async function startInvestigation() {
  const q = $("#q").value.trim();
  if (!q) return $("#askHint").textContent = "Type a question or pick a suggestion.";
  $("#askHint").textContent = ""; $("#findings").innerHTML = ""; S.runId = null; S.lastQuestion = q;
  try { S.job = (await api("/api/investigate/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) })).job_id; }
  catch (e) { return $("#askHint").textContent = e.message; }
  $("#run").disabled = true; $("#progress").classList.remove("hidden"); setState("Running", "run");
  poll();
}

async function poll() {
  clearTimeout(S.timer);
  let j;
  try { j = await api("/api/jobs/" + S.job); }
  catch (e) { setState("Error", "err"); $("#run").disabled = false; return $("#askHint").textContent = e.message; }
  S.lastEvidence = j.evidence;
  const mm = String(Math.floor(j.elapsed_s / 60)).padStart(2, "0"), ss = String(Math.floor(j.elapsed_s % 60)).padStart(2, "0");
  if (j.status === "queued" || j.status === "running") {
    $("#progText").textContent = `${j.phase || "Starting"} · ${j.evidence.length} evidence items · ${mm}:${ss}${j.tool_errors ? ` · ${j.tool_errors} tool error(s)` : ""}`;
    $("#feed").innerHTML = feedHtml(j.evidence);
    S.timer = setTimeout(poll, 2000); return;
  }
  $("#run").disabled = false; $("#progress").classList.add("hidden");
  if (j.status === "error") { setState("Failed", "err"); $("#findings").innerHTML = `<div class="verdict bad">Investigation failed: ${esc(j.error)}</div><p class="muted small">Common causes: Gemini model busy or unavailable (retry), missing API key, or market data unavailable.</p>`; return; }
  const r = j.result; S.runId = r.run_id; setState("Done", "ok");
  const v = r.verification, ok = v.passed;
  const key = section(r.report_markdown, "executive summary") || r.report_markdown.slice(0, 900);
  $("#findings").innerHTML = `
    <div class="verdict ${ok ? "ok" : "bad"}">${ok ? "Verified" : "Not fully verified"}<span class="muted small">${ok ? `${v.sentences_with_citations}/${v.sentences_checked} sentences cited; every figure matched its evidence.` : `${v.issues} issue(s); see the appendix. Treat flagged claims as unconfirmed.`}${r.repairs_used ? ` Repairs used: ${r.repairs_used}.` : ""}</span></div>
    <div class="findings"><h3>Key findings</h3><div class="md">${md(key, r.run_id)}</div>
    <h3>Supporting evidence</h3><div class="feed">${feedHtml(j.evidence)}</div>
    <details><summary>Full report</summary><div class="md">${md(r.report_markdown, r.run_id)}</div></details></div>`;
  $("#findings").querySelectorAll(".ev").forEach(b => b.dataset.run = r.run_id);
}

/* ------------------------------------------------------------------ Scenario Lab */
function fillSectors() {
  if (!S.dash) return;
  const secs = [...new Set(S.dash.universe.map(u => u.sector))].sort();
  const el = $("#scnSector"); if (!el.options.length) el.innerHTML = secs.map(s => `<option>${esc(s)}</option>`).join("");
}
function scnFields() { const k = $("#scnKind").value; $$("#scnForm [data-for]").forEach(l => l.classList.toggle("hidden", !l.dataset.for.split(" ").includes(k))); }
$("#scnKind").addEventListener("change", scnFields); scnFields();
$("#scnForm").addEventListener("submit", async e => {
  e.preventDefault();
  const kind = $("#scnKind").value;
  const body = { kind, equity_move_pct: +$("#scnEq").value, rate_bp: +$("#scnBp").value, sector: $("#scnSector").value, loss_pct: +$("#scnLoss").value, spillover: $("#scnSpill").checked };
  $("#scnOut").innerHTML = `<p class="muted">Running…</p>`;
  try {
    const r = await api("/api/scenario", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("#scnOut").innerHTML = `<div class="kpi"><div><span class="muted small">Estimated portfolio impact</span><b class="mono">${spct(r.portfolio_impact)}</b></div><div><span class="muted small">Scenario</span><b>${esc(r.scenario.replace("_", " "))}</b></div><div><span class="muted small">Data</span><b>${esc(r.data_source)}</b></div></div>
      <div class="table-wrap"><table><thead><tr><th>Asset</th><th>Weight</th><th>Asset impact</th><th>Portfolio contribution</th></tr></thead><tbody>${r.holdings.map(h => `<tr><td><b>${esc(h.ticker)}</b></td><td class="mono">${pct(h.weight)}</td><td class="mono">${spct(h.asset_impact)}</td><td class="mono">${spct(h.portfolio_contribution)}</td></tr>`).join("")}</tbody></table></div>
      <h3 class="muted small" style="margin-top:12px">ASSUMPTIONS</h3><ul>${r.assumptions.map(a => `<li>${esc(a)}</li>`).join("")}</ul><p class="note">Hypothetical sensitivity, not a prediction.</p>`;
  } catch (err) { $("#scnOut").innerHTML = `<div class="banner">${esc(err.message)}</div>`; }
});

/* ------------------------------------------------------------------ Reports + Activity */
async function loadHistory() {
  const el = $("#history");
  try {
    const rows = await api("/api/history");
    el.innerHTML = rows.length ? rows.map(r => `<button class="hist" data-r="${esc(r.run_id)}">${esc(r.question || "(question not recorded)")}<small>${esc(r.run_id)} · ${r.passed === true ? "verified" : r.passed === false ? "not verified" : ""}</small></button>`).join("")
      : `<p class="muted">No saved reports yet. Run an investigation from Overview.</p>`;
  } catch (e) { el.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}
$("#history").addEventListener("click", async e => {
  const b = e.target.closest(".hist"); if (!b) return;
  $$(".hist").forEach(x => x.classList.toggle("active", x === b));
  try { const r = await api("/api/history/" + b.dataset.r); S.runId = r.run_id; $("#reportTitle").textContent = r.question || r.run_id; $("#reportBody").innerHTML = md(r.report_markdown, r.run_id); }
  catch (err) { $("#reportBody").textContent = err.message; }
});
function renderActivity() {
  const ev = S.lastEvidence;
  $("#activity").innerHTML = ev.length
    ? `<p class="muted small">Latest investigation: ${esc(S.lastQuestion)}</p><div class="table-wrap"><table><thead><tr><th>ID</th><th class="l">Tool</th><th class="l">Arguments</th></tr></thead><tbody>${ev.map(e => `<tr><td><button class="ev ${e.kind}" data-id="${esc(e.id)}" data-run="${esc(S.runId || "")}">${esc(e.id)}</button></td><td class="l">${esc(e.tool)}</td><td class="l mono muted">${esc(JSON.stringify(e.args || {}))}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="muted">No investigation has run in this session. Per-run audit files (evidence.json, trace.json, verification.json) are saved under outputs/.</p>`;
}

/* ------------------------------------------------------------------ boot */
loadDash();
