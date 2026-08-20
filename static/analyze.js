// Новый user-flow: Brand -> Platforms -> Advanced Settings -> Analyze -> Results.
// Все данные результата приходят из GET /api/analysis/{id} - никаких захардкоженных цифр в HTML.

const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];

const state = { result: null };

function show(viewId) {
  ["view-landing", "view-progress", "view-results"].forEach((id) => {
    document.getElementById(id).style.display = id === viewId ? "flex" : "none";
  });
}

// ---------------------------------------------------------------------------
// Экран 1: landing - advanced settings toggle + сбор AnalysisConfig
// ---------------------------------------------------------------------------
document.getElementById("advanced-toggle").addEventListener("click", () => {
  const panel = document.getElementById("advanced-panel");
  panel.classList.toggle("open");
});

function splitCsv(value) {
  return (value || "").split(",").map((s) => s.trim()).filter(Boolean);
}

function collectConfig() {
  const sizes = Array.from(document.querySelectorAll(".cfg-size:checked")).map((el) => el.value);
  const minFollowersRaw = document.getElementById("cfg-min-followers").value;
  const minConfidenceRaw = document.getElementById("cfg-min-confidence").value;
  return {
    date_range: document.getElementById("cfg-date-range").value,
    geo: splitCsv(document.getElementById("cfg-geo").value),
    creator_size: sizes,
    min_followers: minFollowersRaw ? parseInt(minFollowersRaw, 10) : null,
    include_topics: splitCsv(document.getElementById("cfg-include-topics").value),
    exclude_topics: splitCsv(document.getElementById("cfg-exclude-topics").value),
    confirmed_only: document.getElementById("cfg-confirmed-only").checked,
    include_manual_review: document.getElementById("cfg-include-manual-review").checked,
    min_integration_confidence: minConfidenceRaw ? parseFloat(minConfidenceRaw) : 0.5,
  };
}

function collectPlatforms() {
  return Array.from(document.querySelectorAll(".az-platform-row input[type=checkbox]:checked"))
    .map((el) => el.value)
    .filter((v) => ["youtube", "instagram", "tiktok"].includes(v));
}

document.getElementById("analyze-btn").addEventListener("click", async () => {
  const brand = document.getElementById("brand-input").value.trim();
  const errorEl = document.getElementById("landing-error");
  errorEl.textContent = "";

  if (!brand) {
    errorEl.textContent = "Введите имя бренда или ссылку на его аккаунт.";
    return;
  }
  const platforms = collectPlatforms();
  if (!platforms.length) {
    errorEl.textContent = "Выберите хотя бы одну платформу.";
    return;
  }

  const payload = { brand, platforms, settings: collectConfig() };
  document.getElementById("progress-brand").textContent = brand;
  show("view-progress");
  runProgressAnimation();

  try {
    const analyzeResp = await fetch("/api/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (!analyzeResp.ok) {
      const detail = await analyzeResp.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${analyzeResp.status}`);
    }
    const { analysis_id } = await analyzeResp.json();
    const resultResp = await fetch(`/api/analysis/${analysis_id}`);
    if (!resultResp.ok) throw new Error(`HTTP ${resultResp.status}`);
    state.result = await resultResp.json();
    finishProgressAnimation();
    renderResults();
    show("view-results");
  } catch (err) {
    show("view-landing");
    errorEl.textContent = "Не удалось выполнить анализ: " + err.message;
  }
});

// ---------------------------------------------------------------------------
// Экран 2: progress — визуальная последовательность шагов (не связана с
// реальным async-статусом backend - orchestration pipeline синхронный;
// анимация просто отражает реальные стадии pipeline, см. app/analysis/pipeline.py)
// ---------------------------------------------------------------------------
let progressTimer = null;
function runProgressAnimation() {
  const steps = Array.from(document.querySelectorAll("#progress-steps li"));
  steps.forEach((el) => el.classList.remove("active", "done"));
  let i = 0;
  const advance = () => {
    if (i > 0) steps[i - 1].classList.add("done");
    if (i < steps.length) { steps[i].classList.add("active"); i++; }
    else clearInterval(progressTimer);
  };
  advance();
  progressTimer = setInterval(advance, 700);
}
function finishProgressAnimation() {
  clearInterval(progressTimer);
  document.querySelectorAll("#progress-steps li").forEach((el) => { el.classList.remove("active"); el.classList.add("done"); });
}

document.getElementById("new-analysis-btn").addEventListener("click", () => show("view-landing"));

// ---------------------------------------------------------------------------
// Экран 3: results
// ---------------------------------------------------------------------------
function renderResults() {
  renderTopbar();
  renderOverview();
  renderMarketMap();
  renderDna();
  renderNextMove();
  renderWhiteSpace();
  renderOurMove();
}

function renderTopbar() {
  const r = state.result;
  document.getElementById("results-brand-name").textContent = r.brand.canonical_name;

  const pillsEl = document.getElementById("coverage-pills");
  pillsEl.innerHTML = (r.coverage.platforms || []).map((p) => `
    <span class="coverage-pill status-${p.status}"><span class="dot"></span>${p.platform}: ${p.status}</span>
  `).join("");

  const limSlot = document.getElementById("limitations-slot");
  limSlot.innerHTML = (r.limitations || []).map((text) => `<div class="limitation-banner">⚠ ${text}</div>`).join("");
}

function renderOverview() {
  const r = state.result;
  const grid = document.getElementById("overview-stats");
  const tiles = [
    ["Integrations found", r.summary.integrations_found],
    ["Creators used", r.summary.creators_used],
    ["Creator universe size", r.summary.creator_universe_size],
    ["Платформы", r.platforms.join(", ")],
  ];
  grid.innerHTML = tiles.map(([label, value]) => `
    <div class="stat-tile"><div class="value">${value}</div><div class="label">${label}</div></div>
  `).join("");

  const cov = document.getElementById("overview-coverage");
  cov.innerHTML = (r.coverage.platforms || []).map((p) => `
    <div style="display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--gridline); font-size:13px;">
      <span><span class="coverage-pill status-${p.status}"><span class="dot"></span>${p.platform}</span></span>
      <span style="color:var(--text-secondary); max-width:60%; text-align:right;">
        ${p.status === "ok" ? `${p.items_collected} материалов собрано` : (p.reason || "—")}
      </span>
    </div>
  `).join("") || `<div class="empty-state">нет платформ</div>`;
}

function barRow(label, value, max, color, ev) {
  const pct = max ? Math.round((value / max) * 100) : 0;
  const evBtn = ev && ev.length ? `<button class="link-btn" onclick='showEvidence(${JSON.stringify(ev)})'>why</button>` : "";
  return `<div class="bar-row">
    <div class="label" title="${label}">${label}</div>
    <div class="bar-track"><div class="bar-fill" style="width:${pct}%; background:${color}"></div></div>
    <div class="bar-value">${value}</div>
    ${evBtn}
  </div>`;
}

function renderMarketMap() {
  const mm = state.result.market_map || {};
  const compGrid = document.getElementById("mm-competitors");
  compGrid.innerHTML = (mm.competitors || []).map((c) => `
    <div class="card">
      <h3 style="margin:0 0 6px;">${c.name}</h3>
      <p class="subtitle" style="margin:0 0 10px;">
        ${c.total_integrations} интеграций (в наблюдаемой выборке) · ${c.unique_creators} уникальных креаторов ·
        repeat rate ${((c.repeat_creator_rate || 0) * 100).toFixed(0)}%
        <button class="link-btn" onclick='showEvidence(${JSON.stringify(c.evidence_ids || [])})'>why</button>
      </p>
      ${Object.entries(c.platform_distribution || {}).map(([k, v], idx) => barRow(k, v, Math.max(1, ...Object.values(c.platform_distribution)), SERIES[idx % SERIES.length])).join("")}
      ${Object.entries(c.topic_distribution || {}).slice(0, 6).map(([k, v]) => `<span class="chip">${k}: ${v}</span>`).join("")}
    </div>
  `).join("") || `<div class="empty-state">Нет данных в наблюдаемой выборке (см. limitations выше)</div>`;
}

function renderDna() {
  const grid = document.getElementById("dna-cards");
  grid.innerHTML = (state.result.competitor_dna || []).map((d) => `
    <div class="card">
      <h3 style="margin:0 0 6px;">${d.competitor}</h3>
      ${(d.insufficient_data || []).length ? `<div class="insufficient-tag">insufficient_data: ${d.insufficient_data.join(", ")}</div>` : ""}
      ${(d.observed_patterns || []).map((p) => `
        <div style="margin-bottom:10px;">
          <div style="font-size:13px;">${p.statement}
            <button class="link-btn" onclick='showEvidence(${JSON.stringify(p.evidence_ids || [])})'>why</button>
          </div>
          <div class="confidence-bar-track"><div class="confidence-bar-fill" style="width:${Math.round(p.confidence * 100)}%"></div></div>
        </div>
      `).join("") || `<div class="empty-state">нет наблюдаемых паттернов в этой выборке</div>`}
    </div>
  `).join("") || `<div class="empty-state">нет данных по бренду</div>`;
}

function renderNextMove() {
  const container = document.getElementById("nm-tables");
  container.innerHTML = (state.result.next_move || []).map((nm) => {
    if (!nm.candidates || !nm.candidates.length) {
      return `<div class="card empty-state">Нет кандидатов в наблюдаемом creator universe (insufficient_data: ${(nm.insufficient_data || []).join(", ") || "—"})</div>`;
    }
    const rows = nm.candidates.slice(0, 10).map((c) => `
      <tr>
        <td>${c.candidate}</td>
        <td><strong>${c.similarity_score}</strong>/100</td>
        <td>${(c.why || []).filter((f) => f.factor_score > 0).map((f) => `<span class="chip">${f.factor}: ${(f.factor_score * 100).toFixed(0)}%</span>`).join("")}</td>
        <td><button class="link-btn" onclick='showEvidence(${JSON.stringify(c.evidence_ids || [])})'>evidence</button></td>
      </tr>
    `).join("");
    return `
      <p class="subtitle">Creator соответствует наблюдаемому профилю закупки бренда (Strategy Match, не Prediction Probability) - см. why/evidence.</p>
      <div class="card">
        <table>
          <thead><tr><th>Creator (из независимого creator universe)</th><th>Strategy Match</th><th>Why</th><th>Evidence</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }).join("") || `<div class="empty-state">нет данных</div>`;
}

function renderWhiteSpace() {
  const ws = state.result.white_space || {};
  const segments = (ws.segments || []).filter((s) => s.our_relevance > 0).slice(0, 9);
  const body = document.getElementById("ws-body");
  if (!segments.length) {
    body.innerHTML = `<div class="empty-state">Нет white space сегментов, релевантных нашему профилю в наблюдаемой выборке</div>`;
    return;
  }
  body.innerHTML = `<div class="card-grid">${segments.map((s) => `
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <h3 style="margin:0;">${s.segment.label}</h3>
        <div class="opportunity-score" style="color:${s.opportunity_score >= 60 ? 'var(--status-good)' : 'var(--text-secondary)'}">${s.opportunity_score}</div>
      </div>
      <p class="subtitle" style="margin:2px 0 10px;">opportunity score / 100 · низкая конкурентная насыщенность в наблюдаемой выборке: ${s.saturation_score}/100</p>
      ${s.insufficient_data ? `<div class="insufficient-tag">insufficient_data: ${s.insufficient_data_reason}</div>` : ""}
      <div style="margin-top:8px;"><button class="link-btn" onclick='showEvidence(${JSON.stringify(s.evidence_ids || [])})'>why / evidence</button></div>
    </div>
  `).join("")}</div>`;
}

function renderOurMove() {
  const grid = document.getElementById("om-cards");
  grid.innerHTML = (state.result.our_move?.opportunities || []).map((op) => `
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <h3 style="margin:0 0 6px;">${op.title}</h3>
        <span class="badge badge-${op.priority}">${op.priority}</span>
      </div>
      <p style="font-size:13px;">${op.why_now}</p>
      <p style="font-size:12px; color:var(--text-secondary);"><strong>Suggested test:</strong> ${op.suggested_test}</p>
      <div class="confidence-bar-track" style="margin-top:10px;"><div class="confidence-bar-fill" style="width:${Math.round(op.confidence * 100)}%"></div></div>
      <p class="subtitle" style="font-size:11px; margin-top:4px;">confidence ${(op.confidence * 100).toFixed(0)}%
        ${op.evidence && op.evidence.length ? `<button class="link-btn" onclick='showEvidence(${JSON.stringify(op.evidence)})'>evidence</button>` : ""}
      </p>
    </div>
  `).join("") || `<div class="empty-state">нет гипотез в наблюдаемой выборке</div>`;
}

// ---------------------------------------------------------------------------
// Evidence modal (переиспользует /api/evidence/{id})
// ---------------------------------------------------------------------------
async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

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
// Навигация по вкладкам результатов
// ---------------------------------------------------------------------------
const TITLES = {
  overview: "Overview", "market-map": "Market Map", "competitor-dna": "Competitor DNA",
  "next-move": "Next Move", "white-space": "White Space", "our-move": "Our Move",
};
document.querySelectorAll("#view-results .nav-item[data-section]").forEach((el) => {
  el.addEventListener("click", () => goToSection(el.dataset.section));
});
function goToSection(key) {
  document.querySelectorAll("#view-results .nav-item[data-section]").forEach((el) => el.classList.toggle("active", el.dataset.section === key));
  document.querySelectorAll("#view-results .section").forEach((el) => el.classList.remove("active"));
  document.getElementById(`section-${key}`).classList.add("active");
  document.getElementById("page-title").textContent = TITLES[key];
}

show("view-landing");
