// Influence Intelligence Agent — dashboard logic.
// Все данные тянутся с backend (/api/*) - в HTML нет захардкоженных цифр.

const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];

const state = {
  overview: null, marketMap: null, dna: null, nextMoves: null, whiteSpace: null, ourMove: null, health: null,
};

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

async function loadAll() {
  const [overview, marketMap, dna, nextMoves, whiteSpace, ourMove, health] = await Promise.all([
    fetchJson("/api/overview"),
    fetchJson("/api/market-map"),
    fetchJson("/api/competitor-dna"),
    fetchJson("/api/next-moves"),
    fetchJson("/api/white-space"),
    fetchJson("/api/our-move"),
    fetchJson("/api/health"),
  ]);
  Object.assign(state, { overview, marketMap, dna, nextMoves, whiteSpace, ourMove, health });
  renderAll();
}

function renderAll() {
  renderTopbar();
  renderOverview();
  renderMarketMap();
  renderDna();
  renderNextMove();
  renderWhiteSpace();
  renderOurMove();
}

// ---------------------------------------------------------------------------
// Topbar: mode badge + health pills + limitation banner
// ---------------------------------------------------------------------------
function renderTopbar() {
  const badge = document.getElementById("mode-badge");
  badge.textContent = (state.overview.mode || "demo").toUpperCase();

  const pillsEl = document.getElementById("health-pills");
  pillsEl.innerHTML = "";
  (state.health || []).forEach((h) => {
    const pill = document.createElement("div");
    pill.className = "pill";
    const dotClass = h.status === "ok" ? "dot-ok" : (h.status === "degraded" ? "dot-degraded" : "dot-unavailable");
    pill.innerHTML = `<span class="dot ${dotClass}"></span> ${h.source}`;
    pill.title = h.detail || "";
    pillsEl.appendChild(pill);
  });

  const limSlot = document.getElementById("limitations-slot");
  limSlot.innerHTML = "";
  if (state.overview.is_synthetic_data) {
    const banner = document.createElement("div");
    banner.className = "limitation-banner";
    banner.textContent = "⚠ Показаны synthetic/demo данные (не реальные интеграции конкурентов). Помечено явно во всех записях (is_synthetic).";
    limSlot.appendChild(banner);
  }
  if ((state.overview.degraded_sources || []).length) {
    const banner = document.createElement("div");
    banner.className = "limitation-banner";
    banner.textContent = `⚠ Источники degraded/unavailable: ${state.overview.degraded_sources.join(", ")}. Результат может быть неполным по этим источникам.`;
    limSlot.appendChild(banner);
  }
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------
function renderOverview() {
  const grid = document.getElementById("overview-stats");
  const o = state.overview;
  const tiles = [
    ["Integrations analyzed", o.integrations_analyzed],
    ["Creators analyzed", o.creators_analyzed],
    ["Competitors analyzed", o.competitors_analyzed],
    ["Active sources", (o.active_sources || []).length],
    ["Degraded sources", (o.degraded_sources || []).length],
  ];
  grid.innerHTML = tiles.map(([label, value]) => `
    <div class="stat-tile"><div class="value">${value}</div><div class="label">${label}</div></div>
  `).join("");

  const src = document.getElementById("overview-sources");
  src.innerHTML = `<p class="subtitle">Обновлено: ${new Date(o.last_updated).toLocaleString("ru-RU")}</p>` +
    (state.health || []).map((h) => `
      <div style="display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid var(--gridline); font-size:13px;">
        <span><span class="dot ${h.status === 'ok' ? 'dot-ok' : (h.status === 'degraded' ? 'dot-degraded' : 'dot-unavailable')}" style="display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;"></span>${h.source}</span>
        <span style="color:var(--text-secondary)">${h.detail || ""}</span>
      </div>
    `).join("");
}

// ---------------------------------------------------------------------------
// Market Map
// ---------------------------------------------------------------------------
function barRow(label, value, max, color, ev) {
  const pct = max ? Math.round((value / max) * 100) : 0;
  const evBtn = ev ? `<button class="link-btn" onclick="showEvidence(${JSON.stringify(ev)})">why</button>` : "";
  return `<div class="bar-row">
    <div class="label" title="${label}">${label}</div>
    <div class="bar-track"><div class="bar-fill" style="width:${pct}%; background:${color}"></div></div>
    <div class="bar-value">${value}</div>
    ${evBtn}
  </div>`;
}

function renderMarketMap() {
  const mm = state.marketMap;
  const sat = (mm.market?.segment_saturation || []).slice(0, 10);
  const maxSat = Math.max(1, ...sat.map((s) => s.saturation_score));
  document.getElementById("mm-saturation").innerHTML = sat.length
    ? sat.map((s) => barRow(`${s.label} (${s.unique_competitors} конк.)`, s.saturation_score, 100, "var(--series-1)", s.evidence_ids)).join("")
    : `<div class="empty-state">Недостаточно данных</div>`;

  const platform = mm.market?.share_by_platform || {};
  const maxP = Math.max(1, ...Object.values(platform));
  document.getElementById("mm-platform").innerHTML = Object.entries(platform)
    .map(([k, v], idx) => barRow(k, v, maxP, SERIES[idx % SERIES.length])).join("") || `<div class="empty-state">нет данных</div>`;

  const size = mm.market?.share_by_creator_size || {};
  const maxS = Math.max(1, ...Object.values(size));
  document.getElementById("mm-size").innerHTML = Object.entries(size)
    .map(([k, v], idx) => barRow(k, v, maxS, SERIES[(idx + 3) % SERIES.length])).join("") || `<div class="empty-state">нет данных</div>`;

  const compGrid = document.getElementById("mm-competitors");
  compGrid.innerHTML = (mm.competitors || []).map((c) => `
    <div class="card">
      <h3 style="margin:0 0 6px;">${c.name}</h3>
      <p class="subtitle" style="margin:0 0 10px;">
        ${c.total_integrations} интеграций · ${c.unique_creators} уникальных креаторов ·
        repeat rate ${(c.repeat_creator_rate * 100).toFixed(0)}%
        <button class="link-btn" onclick='showEvidence(${JSON.stringify(c.evidence_ids)})'>why</button>
      </p>
      ${Object.entries(c.topic_distribution || {}).slice(0, 5).map(([k, v]) => `<span class="chip">${k}: ${v}</span>`).join("")}
    </div>
  `).join("") || `<div class="empty-state">нет конкурентов</div>`;
}

// ---------------------------------------------------------------------------
// Competitor DNA
// ---------------------------------------------------------------------------
function renderDna() {
  const grid = document.getElementById("dna-cards");
  grid.innerHTML = (state.dna || []).map((d) => `
    <div class="card">
      <h3 style="margin:0 0 6px;">${d.competitor}</h3>
      ${(d.insufficient_data || []).length ? `<div class="insufficient-tag">insufficient_data: ${d.insufficient_data.join(", ")}</div>` : ""}
      <h4 style="margin:12px 0 6px; font-size:12px; color:var(--text-secondary); text-transform:uppercase;">Наблюдаемые паттерны</h4>
      ${(d.observed_patterns || []).map((p) => `
        <div style="margin-bottom:10px;">
          <div style="font-size:13px;">${p.statement}
            <button class="link-btn" onclick='showEvidence(${JSON.stringify(p.evidence_ids)})'>why</button>
          </div>
          <div class="confidence-bar-track"><div class="confidence-bar-fill" style="width:${Math.round(p.confidence * 100)}%"></div></div>
          <span class="subtitle" style="font-size:11px;">${p.type.toUpperCase()} · confidence ${(p.confidence * 100).toFixed(0)}%</span>
        </div>
      `).join("") || `<div class="empty-state">нет паттернов</div>`}
      ${(d.recent_shifts || []).length ? `
        <h4 style="margin:12px 0 6px; font-size:12px; color:var(--text-secondary); text-transform:uppercase;">Recent shift</h4>
        ${d.recent_shifts.map((s) => `<div style="font-size:13px; margin-bottom:6px;">📈 ${s.statement} <button class="link-btn" onclick='showEvidence(${JSON.stringify(s.evidence_ids)})'>why</button></div>`).join("")}
      ` : ""}
    </div>
  `).join("") || `<div class="empty-state">нет данных по конкурентам</div>`;
}

// ---------------------------------------------------------------------------
// Next Move
// ---------------------------------------------------------------------------
function renderNextMove() {
  const container = document.getElementById("nm-tables");
  container.innerHTML = (state.nextMoves || []).map((nm) => {
    if (!nm.candidates || !nm.candidates.length) {
      return `<h2>${nm.competitor}</h2><div class="card empty-state">Нет кандидатов (insufficient_data: ${(nm.insufficient_data || []).join(", ") || "—"})</div>`;
    }
    const rows = nm.candidates.slice(0, 8).map((c) => `
      <tr>
        <td>${c.candidate}</td>
        <td>${nm.competitor}</td>
        <td><strong>${c.similarity_score}</strong>/100</td>
        <td>${c.why.filter((f) => f.factor_score > 0).map((f) => `<span class="chip">${f.factor}: ${(f.factor_score * 100).toFixed(0)}%</span>`).join("")}</td>
        <td><button class="link-btn" onclick='showEvidence(${JSON.stringify(c.evidence_ids)})'>evidence</button></td>
      </tr>
    `).join("");
    return `
      <h2>${nm.competitor} <span class="subtitle">(Strategy Match, не Prediction Probability)</span></h2>
      <div class="card">
        <table>
          <thead><tr><th>Creator</th><th>Competitor</th><th>Strategy Match</th><th>Why</th><th>Evidence</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }).join("") || `<div class="empty-state">нет данных</div>`;
}

// ---------------------------------------------------------------------------
// White Space
// ---------------------------------------------------------------------------
function renderWhiteSpace() {
  const grid = document.getElementById("ws-cards");
  const segments = (state.whiteSpace?.segments || []).filter((s) => s.our_relevance > 0).slice(0, 9);
  grid.innerHTML = segments.map((s) => `
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <h3 style="margin:0;">${s.segment.label}</h3>
        <div class="opportunity-score" style="color:${s.opportunity_score >= 60 ? 'var(--status-good)' : 'var(--text-secondary)'}">${s.opportunity_score}</div>
      </div>
      <p class="subtitle" style="margin:2px 0 10px;">opportunity score / 100</p>
      ${s.insufficient_data ? `<div class="insufficient-tag">insufficient_data: ${s.insufficient_data_reason}</div>` : ""}
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:6px; font-size:12px; margin-bottom:8px;">
        <div>Creator supply: <strong>${s.available_creators}</strong></div>
        <div>Competitor integrations: <strong>${s.competitor_integrations}</strong></div>
        <div>Unique competitors: <strong>${s.unique_competitors}</strong></div>
        <div>Saturation: <strong>${s.saturation_score}</strong>/100</div>
      </div>
      <div style="font-size:12px; color:var(--text-secondary); margin-bottom:8px;">Our relevance: ${s.our_relevance}/100 (${s.our_relevance_notes})</div>
      <div>${s.top_creators.slice(0, 4).map((c) => `<span class="chip">${c.name}${c.already_used_by_competitor ? " (used)" : ""}</span>`).join("")}</div>
      <div style="margin-top:8px;"><button class="link-btn" onclick='showEvidence(${JSON.stringify(s.evidence_ids)})'>why / evidence</button></div>
    </div>
  `).join("") || `<div class="empty-state">Нет white space сегментов, релевантных нашему профилю</div>`;
}

// ---------------------------------------------------------------------------
// Our Move
// ---------------------------------------------------------------------------
function renderOurMove() {
  const grid = document.getElementById("om-cards");
  grid.innerHTML = (state.ourMove?.opportunities || []).map((op) => `
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <h3 style="margin:0 0 6px;">${op.title}</h3>
        <span class="badge badge-${op.priority}">${op.priority}</span>
      </div>
      <p style="font-size:13px;">${op.why_now}</p>
      <p style="font-size:12px; color:var(--text-secondary);"><strong>Suggested test:</strong> ${op.suggested_test}</p>
      ${op.creators.length ? `<div>${op.creators.map((c) => `<span class="chip">${c}</span>`).join("")}</div>` : ""}
      <div class="confidence-bar-track" style="margin-top:10px;"><div class="confidence-bar-fill" style="width:${Math.round(op.confidence * 100)}%"></div></div>
      <p class="subtitle" style="font-size:11px; margin-top:4px;">confidence ${(op.confidence * 100).toFixed(0)}%
        ${op.evidence.length ? `<button class="link-btn" onclick='showEvidence(${JSON.stringify(op.evidence)})'>evidence</button>` : ""}
      </p>
    </div>
  `).join("") || `<div class="empty-state">нет гипотез</div>`;
}

// ---------------------------------------------------------------------------
// Evidence modal
// ---------------------------------------------------------------------------
async function showEvidence(ids) {
  const modal = document.getElementById("evidence-modal");
  const body = document.getElementById("evidence-body");
  if (!ids || !ids.length) {
    body.innerHTML = `<div class="empty-state">Evidence ids не переданы</div>`;
  } else {
    const items = await Promise.all(ids.map((id) => fetchJson(`/api/evidence/${id}`).catch(() => null)));
    body.innerHTML = items.filter(Boolean).map((ev) => `
      <div class="evidence-item">
        <span class="evidence-type ${ev.type}">${ev.type}</span>
        <div style="margin-top:6px; font-size:13px;"><strong>${ev.field}</strong>: ${JSON.stringify(ev.value)}</div>
        ${ev.confidence != null ? `<div style="font-size:12px; color:var(--text-secondary);">confidence: ${ev.confidence}</div>` : ""}
        ${ev.raw_fragment ? `<div style="font-size:12px; color:var(--text-secondary); margin-top:4px;">${ev.raw_fragment}</div>` : ""}
        ${ev.source_url ? `<div style="font-size:11px; margin-top:4px;"><a href="${ev.source_url}" target="_blank">${ev.source_url}</a></div>` : ""}
      </div>
    `).join("") || `<div class="empty-state">Evidence не найден</div>`;
  }
  modal.classList.add("active");
}
document.getElementById("evidence-close").addEventListener("click", () => {
  document.getElementById("evidence-modal").classList.remove("active");
});

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
const TITLES = {
  overview: "Overview", "market-map": "Market Map", "competitor-dna": "Competitor DNA",
  "next-move": "Next Move", "white-space": "White Space", "our-move": "Our Move",
};
document.querySelectorAll(".nav-item[data-section]").forEach((el) => {
  el.addEventListener("click", () => goToSection(el.dataset.section));
});
function goToSection(key) {
  document.querySelectorAll(".nav-item[data-section]").forEach((el) => el.classList.toggle("active", el.dataset.section === key));
  document.querySelectorAll(".section").forEach((el) => el.classList.remove("active"));
  document.getElementById(`section-${key}`).classList.add("active");
  document.getElementById("page-title").textContent = TITLES[key];
}

// ---------------------------------------------------------------------------
// Demo Flow — 5 шагов live-demo (раздел 16 мастер-промпта)
// ---------------------------------------------------------------------------
let demoFlowStep = 0;
function demoFlowSteps() {
  const o = state.overview;
  const topDna = (state.dna || []).find((d) => (d.observed_patterns || []).length) || state.dna?.[0];
  const topNm = (state.nextMoves || []).flatMap((nm) => (nm.candidates || []).map((c) => ({ ...c, competitor: nm.competitor })))
    .sort((a, b) => b.similarity_score - a.similarity_score)[0];
  const topWs = (state.whiteSpace?.segments || []).filter((s) => s.our_relevance > 0)
    .sort((a, b) => b.opportunity_score - a.opportunity_score)[0];
  const topMoves = (state.ourMove?.opportunities || []).slice(0, 3);

  return [
    {
      label: "Шаг 1 / 5 — Overview",
      html: `<h3>Масштаб данных</h3>
        <p>Проанализировано <strong>${o.integrations_analyzed}</strong> интеграций,
        <strong>${o.creators_analyzed}</strong> креаторов, <strong>${o.competitors_analyzed}</strong> конкурентов.</p>
        <p class="subtitle">Агент превращает сотни разрозненных influencer-интеграций конкурентов в актуальную карту стратегии рынка.</p>`,
    },
    {
      label: "Шаг 2 / 5 — Competitor DNA",
      html: topDna ? `<h3>${topDna.competitor}</h3>
        ${(topDna.observed_patterns || []).slice(0, 4).map((p) => `<p style="font-size:13px;">• ${p.statement} <em>(confidence ${(p.confidence*100).toFixed(0)}%)</em></p>`).join("") || "<p>Нет паттернов</p>"}` : "<p>Нет данных</p>",
    },
    {
      label: "Шаг 3 / 5 — Next Move",
      html: topNm ? `<h3>${topNm.candidate}</h3>
        <p>Strategy Match <strong>${topNm.similarity_score}/100</strong> с наблюдаемым профилем ${topNm.competitor}, но конкурент ещё не работал с этим креатором.</p>` : "<p>Нет кандидатов</p>",
    },
    {
      label: "Шаг 4 / 5 — White Space",
      html: topWs ? `<h3>${topWs.segment.label}</h3>
        <p>Opportunity score <strong>${topWs.opportunity_score}/100</strong>, saturation всего ${topWs.saturation_score}/100.
        Доступно ${topWs.available_creators} релевантных креаторов.</p>
        <div>${topWs.top_creators.slice(0,4).map(c => `<span class="chip">${c.name}</span>`).join("")}</div>` : "<p>Нет white space</p>",
    },
    {
      label: "Шаг 5 / 5 — Our Move",
      html: topMoves.map((m) => `<p style="font-size:13px;">• <strong>${m.title}</strong> — ${m.why_now}</p>`).join("") || "<p>Нет гипотез</p>",
    },
  ];
}

function renderDemoFlowStep() {
  const steps = demoFlowSteps();
  const step = steps[demoFlowStep];
  const card = document.getElementById("demo-flow-card");
  card.innerHTML = `
    <div class="demo-flow-step-label">${step.label}</div>
    <div style="margin-top:10px;">${step.html}</div>
    <div class="demo-flow-nav">
      <button class="btn-secondary" id="demo-flow-close">Закрыть</button>
      <div>
        ${demoFlowStep > 0 ? '<button class="btn-secondary" id="demo-flow-prev">← назад</button>' : ""}
        ${demoFlowStep < steps.length - 1 ? '<button class="btn-primary" id="demo-flow-next">далее →</button>' : '<button class="btn-primary" id="demo-flow-done">Готово</button>'}
      </div>
    </div>
  `;
  document.getElementById("demo-flow-close").addEventListener("click", closeDemoFlow);
  const prev = document.getElementById("demo-flow-prev");
  if (prev) prev.addEventListener("click", () => { demoFlowStep--; renderDemoFlowStep(); });
  const next = document.getElementById("demo-flow-next");
  if (next) next.addEventListener("click", () => { demoFlowStep++; renderDemoFlowStep(); });
  const done = document.getElementById("demo-flow-done");
  if (done) done.addEventListener("click", closeDemoFlow);
}
function closeDemoFlow() {
  document.getElementById("demo-flow-overlay").classList.remove("active");
}
document.getElementById("demo-flow-btn").addEventListener("click", () => {
  demoFlowStep = 0;
  renderDemoFlowStep();
  document.getElementById("demo-flow-overlay").classList.add("active");
});

loadAll().catch((err) => {
  document.getElementById("page-subtitle").textContent = "Ошибка загрузки данных: " + err.message;
});
