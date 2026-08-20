// Новый dark-premium frontend поверх уже существующего real-analysis API.
//
// КРИТИЧЕСКОЕ ПРАВИЛО: единственный источник данных для этого UI - новый
// real-analysis flow:
//   POST /api/analyze            -> { analysis_id }
//   GET  /api/analysis/{id}      -> полный AnalysisResult
// Никакие legacy/demo endpoints (/api/overview, /api/market-map,
// /api/competitor-dna, /api/next-moves, /api/white-space, /api/our-move) НЕ
// используются здесь - они принадлежат /demo.html + app.js (отдельный явный
// demo-режим) и не должны быть fallback-источником для этого интерфейса.
// Все карточки/counts/coverage/evidence строятся только из AnalysisResult.

const state = { result: null };

// ---------------------------------------------------------------------------
// Платформы / статусы источников - человеческие подписи (раздел 9, 12)
// ---------------------------------------------------------------------------
const PLATFORM_LABEL = { youtube: "YouTube", instagram: "Instagram", tiktok: "TikTok", articles: "Статьи" };
const CLOUD_PLATFORMS = new Set(["youtube", "articles"]);

function sourceDisplay(platformCov) {
  const status = platformCov.status;
  if (CLOUD_PLATFORMS.has(platformCov.platform)) {
    if (status === "ok") return { label: "Live", dot: "good" };
    if (status === "degraded") return { label: "Частично", dot: "warn" };
    return { label: "Недоступно", dot: "bad" };
  }
  if (status === "ok") return { label: "Подключён", dot: "good" };
  if (status === "manual_intervention_required") return { label: "Нужен вход", dot: "warn" };
  return { label: "Не подключён", dot: "bad" };
}

// ---------------------------------------------------------------------------
// Площадки (chip toggles) - минимум одна выбрана всегда
// ---------------------------------------------------------------------------
let selectedPlatforms = new Set(["youtube"]);

function renderPlatformChips() {
  document.querySelectorAll("#platform-chips .chip-toggle").forEach((chip) => {
    chip.classList.toggle("active", selectedPlatforms.has(chip.dataset.platform));
  });
}

document.getElementById("platform-chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip-toggle");
  if (!chip) return;
  const key = chip.dataset.platform;
  if (selectedPlatforms.has(key)) {
    if (selectedPlatforms.size > 1) selectedPlatforms.delete(key); // хотя бы одна платформа должна остаться
  } else {
    selectedPlatforms.add(key);
  }
  renderPlatformChips();
  updateEstimateHint();
});

// ---------------------------------------------------------------------------
// Клиентская оценка времени - честно приблизительная, зависит только от
// того, что реально влияет на объём работы: число выбранных площадок и
// выбранный период. Никакого отдельного backend-поля "depth" не существует
// (AnalysisConfig его не поддерживает) - оценка считается целиком на фронте.
// ---------------------------------------------------------------------------
const DATE_RANGE_TIME_BASE = {
  "7d": [1, 2],
  "30d": [2, 4],
  "90d": [3, 6],
};

function computeEstimate() {
  const dateRange = document.getElementById("cfg-date-range").value || "90d";
  let [lo, hi] = DATE_RANGE_TIME_BASE[dateRange] || DATE_RANGE_TIME_BASE["90d"];
  const extra = Math.max(0, selectedPlatforms.size - 1);
  lo += extra;
  hi += extra * 2;
  return { lo, hi };
}

function updateEstimateHint() {
  const est = computeEstimate();
  const hint = document.getElementById("estimate-hint");
  if (hint) hint.textContent = `при текущих настройках — обычно около ${est.lo}–${est.hi} мин`;
}

document.getElementById("cfg-date-range").addEventListener("change", updateEstimateHint);

// ---------------------------------------------------------------------------
// Advanced toggle ("Настроить поиск")
// ---------------------------------------------------------------------------
document.getElementById("advanced-toggle").addEventListener("click", () => {
  document.getElementById("advanced-panel").classList.toggle("open");
  document.getElementById("advanced-toggle").classList.toggle("open");
});

renderPlatformChips();
updateEstimateHint();

// ---------------------------------------------------------------------------
// Сбор AnalysisConfig из advanced-панели
// ---------------------------------------------------------------------------
function splitCsv(value) {
  return (value || "").split(",").map((s) => s.trim()).filter(Boolean);
}

function collectConfig() {
  const sizes = Array.from(document.querySelectorAll(".cfg-size:checked")).map((el) => el.value);
  const minViewsRaw = document.getElementById("cfg-min-views").value;
  return {
    date_range: document.getElementById("cfg-date-range").value,
    creator_size: sizes,
    min_avg_views: minViewsRaw ? parseFloat(minViewsRaw) : null,
    include_topics: splitCsv(document.getElementById("cfg-include-topics").value),
    exclude_topics: splitCsv(document.getElementById("cfg-exclude-topics").value),
    confirmed_only: document.getElementById("cfg-confirmed-only").checked,
    include_manual_review: document.getElementById("cfg-include-manual-review").checked,
  };
}

// ---------------------------------------------------------------------------
// Hero: заголовок / статус-строка / орб — единая последовательность состояний
// ---------------------------------------------------------------------------
const STATUS_PHASES = [
  "Ищу упоминания бренда",
  "Проверяю интеграции",
  "Собираю похожих авторов",
  "Сравниваю сегменты",
  "Формирую выводы",
];

let dotsTimer = null;
let phaseTimer = null;

function startProgressAnimation() {
  const headline = document.getElementById("hero-headline");
  const eyebrow = document.getElementById("hero-eyebrow");
  const eyebrowText = document.getElementById("hero-eyebrow-text");
  const statusLine = document.getElementById("hero-status-line");
  const est = computeEstimate();

  document.getElementById("hero-search-block").style.display = "none";
  eyebrow.style.display = "flex";
  eyebrowText.textContent = `обычно это занимает около ${est.lo}–${est.hi} мин`;
  statusLine.style.display = "block";

  headline.innerHTML = 'Собираю информацию<span id="hero-dots">...</span>';
  let dots = 0;
  dotsTimer = setInterval(() => {
    dots = (dots + 1) % 3;
    const el = document.getElementById("hero-dots");
    if (el) el.textContent = ".".repeat(dots + 1);
  }, 480);

  let phaseIdx = 0;
  const setPhase = () => {
    statusLine.style.opacity = 0;
    setTimeout(() => {
      statusLine.textContent = STATUS_PHASES[phaseIdx % STATUS_PHASES.length];
      statusLine.style.opacity = 1;
      phaseIdx++;
    }, 220);
  };
  setPhase();
  phaseTimer = setInterval(setPhase, 1700);
}

function stopProgressAnimation() {
  clearInterval(dotsTimer);
  clearInterval(phaseTimer);
  document.getElementById("hero-eyebrow").style.display = "none";
  document.getElementById("hero-status-line").style.display = "none";
}

function showFinishedHero(result) {
  const headline = document.getElementById("hero-headline");
  const sub = document.getElementById("hero-sub");
  headline.textContent = "Готово!";
  sub.textContent = buildFinishedSummary(result);
}

function resetHero() {
  document.getElementById("hero-headline").textContent = "Привет, что для тебя найти?";
  document.getElementById("hero-sub").textContent = "";
  document.getElementById("hero-search-block").style.display = "block";
}

// ---------------------------------------------------------------------------
// Русская плюрализация счётчиков
// ---------------------------------------------------------------------------
function pluralRu(n, forms) {
  const mod100 = Math.abs(n) % 100;
  const mod10 = mod100 % 10;
  if (mod100 > 10 && mod100 < 20) return forms[2];
  if (mod10 > 1 && mod10 < 5) return forms[1];
  if (mod10 === 1) return forms[0];
  return forms[2];
}

function interestingSegmentsCount(result) {
  const segments = (result.white_space && result.white_space.segments) || [];
  return segments.filter((s) => !s.insufficient_data && (s.opportunity_score || 0) >= 50).length;
}

function buildFinishedSummary(result) {
  const integrations = result.summary.integrations_found || 0;
  const creators = result.summary.creators_used || 0;
  const segments = interestingSegmentsCount(result);
  const intWord = pluralRu(integrations, ["интеграцию", "интеграции", "интеграций"]);
  const creatorWord = pluralRu(creators, ["автора", "авторов", "авторов"]);
  const segWord = pluralRu(segments, ["интересный сегмент", "интересных сегмента", "интересных сегментов"]);
  return `Нашёл ${integrations} ${intWord}, ${creators} ${creatorWord} и ${segments} ${segWord}.`;
}

// ---------------------------------------------------------------------------
// Запуск анализа
// ---------------------------------------------------------------------------
document.getElementById("analyze-btn").addEventListener("click", async () => {
  const brand = document.getElementById("brand-input").value.trim();
  const errorEl = document.getElementById("landing-error");
  errorEl.textContent = "";

  if (!brand) {
    errorEl.textContent = "Введите название бренда или ссылку на его аккаунт.";
    return;
  }
  if (!selectedPlatforms.size) {
    errorEl.textContent = "Выберите хотя бы одну площадку в настройках поиска.";
    return;
  }

  const payload = { brand, platforms: Array.from(selectedPlatforms), settings: collectConfig() };
  startProgressAnimation();

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

    stopProgressAnimation();
    showFinishedHero(state.result);
    setTimeout(() => {
      renderResults();
      document.getElementById("view-hero").style.display = "none";
      document.getElementById("view-results").style.display = "flex";
    }, 1100);
  } catch (err) {
    stopProgressAnimation();
    resetHero();
    errorEl.textContent = "Не удалось выполнить анализ: " + err.message;
  }
});

document.getElementById("new-analysis-btn").addEventListener("click", () => {
  document.getElementById("view-results").style.display = "none";
  document.getElementById("view-hero").style.display = "flex";
  resetHero();
  document.getElementById("landing-error").textContent = "";
});

// ---------------------------------------------------------------------------
// Результаты — все данные строго из state.result (GET /api/analysis/{id})
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

  const sourcesEl = document.getElementById("sources-compact");
  sourcesEl.innerHTML = (r.coverage.platforms || []).map((p) => {
    const { label, dot } = sourceDisplay(p);
    return `<div class="source-line"><span class="dot ${dot}"></span><span class="name">${PLATFORM_LABEL[p.platform] || p.platform}</span><span>${label}</span></div>`;
  }).join("");

  const limSlot = document.getElementById("limitations-slot");
  limSlot.innerHTML = (r.limitations || []).map((text) => `<div class="limitation-banner">${text}</div>`).join("");
}

function topDnaPattern(dna) {
  let best = null;
  (dna || []).forEach((d) => (d.observed_patterns || []).forEach((p) => {
    if (!best || (p.confidence || 0) > best.confidence) best = { ...p, competitor: d.competitor };
  }));
  return best;
}
function topNextMoveCandidate(nextMove) {
  let best = null;
  (nextMove || []).forEach((nm) => (nm.candidates || []).forEach((c) => {
    if (!best || (c.similarity_score || 0) > best.similarity_score) best = c;
  }));
  return best;
}
function topWhiteSpaceSegment(ws) {
  let best = null;
  ((ws && ws.segments) || []).forEach((s) => {
    if (s.insufficient_data) return;
    if (!best || (s.opportunity_score || 0) > best.opportunity_score) best = s;
  });
  return best;
}

function lowerFirst(s) { return s ? s.charAt(0).toLowerCase() + s.slice(1) : s; }

function buildLead(result) {
  const pattern = topDnaPattern(result.competitor_dna);
  if (pattern) return `В наблюдаемой выборке у ${pattern.competitor} чаще всего виден такой паттерн: ${lowerFirst(pattern.statement)}`;
  return "Пока недостаточно данных, чтобы выделить устойчивый паттерн.";
}

function renderHighlights(result) {
  const pattern = topDnaPattern(result.competitor_dna);
  const candidate = topNextMoveCandidate(result.next_move);
  const segment = topWhiteSpaceSegment(result.white_space);

  const cards = [
    {
      kicker: "Главный паттерн",
      body: pattern ? `<strong>${pattern.competitor}:</strong> ${pattern.statement}` : "Недостаточно данных в этой выборке.",
      ev: pattern ? pattern.evidence_ids : null,
    },
    {
      kicker: "Лучший кандидат",
      body: candidate ? `<strong>${candidate.candidate}</strong> — совпадение ${candidate.similarity_score}/100 с наблюдаемым профилем закупки бренда.` : "Недостаточно данных в этой выборке.",
      ev: candidate ? candidate.evidence_ids : null,
    },
    {
      kicker: "Сильный сегмент",
      body: segment ? `<strong>${segment.segment.label}</strong> — оценка возможности ${segment.opportunity_score}/100, конкуренция в наблюдаемой выборке: ${competitionLabel(segment.saturation_score)}.` : "Недостаточно данных в этой выборке.",
      ev: segment ? segment.evidence_ids : null,
    },
  ];

  return cards.map((c, i) => `
    <div class="highlight-card" style="animation-delay:${i * 0.08}s">
      <div class="hc-kicker">${c.kicker}</div>
      <div class="hc-text">${c.body}</div>
      ${c.ev && c.ev.length ? `<div style="margin-top:10px;"><button class="text-btn" onclick='showEvidence(${JSON.stringify(c.ev)})'>Почему?</button></div>` : ""}
    </div>
  `).join("");
}

function renderOverview() {
  const r = state.result;
  document.getElementById("overview-lead").textContent = buildLead(r);

  const grid = document.getElementById("overview-stats");
  const tiles = [
    [String(r.summary.integrations_found), "интеграций найдено"],
    [String(r.summary.creators_used), "авторов учтено"],
    [String(r.summary.creator_universe_size), "авторов в базе для сравнения"],
  ];
  grid.innerHTML = tiles.map(([value, label], i) => `
    <div class="stat-tile" style="animation-delay:${i * 0.06}s"><div class="value">${value}</div><div class="label">${label}</div></div>
  `).join("");

  document.getElementById("overview-highlights").innerHTML = renderHighlights(r);
}

function barRow(label, value, max, color, ev) {
  const pct = max ? Math.round((value / max) * 100) : 0;
  const evBtn = ev && ev.length ? `<button class="text-btn" onclick='showEvidence(${JSON.stringify(ev)})'>Почему?</button>` : "";
  return `<div class="bar-row">
    <div class="label" title="${label}">${label}</div>
    <div class="bar-track"><div class="bar-fill" style="width:${pct}%; background:${color}"></div></div>
    <div class="bar-value">${value}</div>
    ${evBtn}
  </div>`;
}

const SERIES = ["#4c8fe0", "#d97a4c", "#33ab6f", "#d9a13d", "#c77fb0", "#4caf7d", "#8a7fd9", "#dd6a63"];

function renderMarketMap() {
  const mm = state.result.market_map || {};
  const compGrid = document.getElementById("mm-competitors");
  compGrid.innerHTML = (mm.competitors || []).map((c, idx) => `
    <div class="card" style="animation-delay:${idx * 0.07}s">
      <h3>${c.name}</h3>
      <p class="subtitle">
        ${c.total_integrations} интеграций в наблюдаемой выборке · ${c.unique_creators} уникальных авторов ·
        повтор ${((c.repeat_creator_rate || 0) * 100).toFixed(0)}%
        <button class="text-btn" onclick='showEvidence(${JSON.stringify(c.evidence_ids || [])})'>Почему?</button>
      </p>
      ${Object.entries(c.platform_distribution || {}).map(([k, v], i) => barRow(PLATFORM_LABEL[k] || k, v, Math.max(1, ...Object.values(c.platform_distribution)), SERIES[i % SERIES.length])).join("")}
      ${Object.entries(c.topic_distribution || {}).slice(0, 6).map(([k, v]) => `<span class="chip">${k}: ${v}</span>`).join("")}
    </div>
  `).join("") || `<div class="empty-state">Нет данных в наблюдаемой выборке (см. предупреждения выше)</div>`;
}

function renderDna() {
  const grid = document.getElementById("dna-cards");
  grid.innerHTML = (state.result.competitor_dna || []).map((d, idx) => `
    <div class="card" style="animation-delay:${idx * 0.07}s">
      <h3>${d.competitor}</h3>
      ${(d.insufficient_data || []).length ? `<div class="insufficient-tag">недостаточно данных: ${d.insufficient_data.join(", ")}</div>` : ""}
      ${(d.observed_patterns || []).map((p) => `
        <div style="margin-bottom:12px;">
          <div style="font-size:13.5px; color:var(--text-secondary);">${p.statement}
            <button class="text-btn" onclick='showEvidence(${JSON.stringify(p.evidence_ids || [])})'>Почему?</button>
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
      return `<div class="card empty-state">Нет кандидатов в наблюдаемой базе авторов (недостаточно данных: ${(nm.insufficient_data || []).join(", ") || "—"})</div>`;
    }
    const rows = nm.candidates.slice(0, 10).map((c) => `
      <tr>
        <td>${c.candidate}</td>
        <td><strong style="color:var(--text-primary);">${c.similarity_score}</strong>/100</td>
        <td>${(c.why || []).filter((f) => f.factor_score > 0).map((f) => `<span class="chip">${f.factor}: ${(f.factor_score * 100).toFixed(0)}%</span>`).join("")}</td>
        <td><button class="text-btn" onclick='showEvidence(${JSON.stringify(c.evidence_ids || [])})'>Почему?</button></td>
      </tr>
    `).join("");
    return `
      <p class="card subtitle" style="animation:none; opacity:1;">Совпадение с наблюдаемым профилем закупки бренда — не гарантия, а сигнал для проверки.</p>
      <div class="card">
        <table>
          <thead><tr><th>Автор</th><th>Совпадение</th><th>Почему</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }).join("") || `<div class="empty-state">нет данных</div>`;
}

function competitionLabel(saturationScore) {
  const score = saturationScore || 0;
  if (score < 34) return "низкая";
  if (score < 67) return "средняя";
  return "высокая";
}

function renderWhiteSpace() {
  const ws = state.result.white_space || {};
  const segments = (ws.segments || []).filter((s) => s.our_relevance > 0).slice(0, 9);
  const body = document.getElementById("ws-body");
  if (!segments.length) {
    body.innerHTML = `<div class="empty-state">Нет сегментов, релевантных нашему профилю в наблюдаемой выборке</div>`;
    return;
  }
  body.innerHTML = `<div class="card-grid">${segments.map((s, idx) => `
    <div class="card" style="animation-delay:${idx * 0.07}s">
      <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <h3 style="margin:0;">${s.segment.label}</h3>
        <div class="opportunity-score" style="color:${s.opportunity_score >= 60 ? 'var(--good)' : 'var(--text-secondary)'}">${s.opportunity_score}</div>
      </div>
      <p class="subtitle" style="margin:2px 0 10px;">оценка возможности / 100 · Конкуренция в наблюдаемой выборке: ${competitionLabel(s.saturation_score)} <span style="color:var(--text-muted);">(${s.saturation_score}/100)</span></p>
      ${s.insufficient_data ? `<div class="insufficient-tag">недостаточно данных: ${s.insufficient_data_reason}</div>` : ""}
      <div style="margin-top:8px;"><button class="text-btn" onclick='showEvidence(${JSON.stringify(s.evidence_ids || [])})'>Почему?</button></div>
    </div>
  `).join("")}</div>`;
}

function renderOurMove() {
  const grid = document.getElementById("om-cards");
  grid.innerHTML = (state.result.our_move?.opportunities || []).map((op, idx) => `
    <div class="card" style="animation-delay:${idx * 0.07}s">
      <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <h3 style="margin:0 0 6px;">${op.title}</h3>
        <span class="badge badge-${op.priority}">${op.priority}</span>
      </div>
      <p style="font-size:13.5px; color:var(--text-secondary);">${op.why_now}</p>
      <p style="font-size:12.5px; color:var(--text-muted);"><strong style="color:var(--text-secondary);">Что проверить:</strong> ${op.suggested_test}</p>
      <div class="confidence-bar-track" style="margin-top:10px;"><div class="confidence-bar-fill" style="width:${Math.round(op.confidence * 100)}%"></div></div>
      <p class="subtitle" style="font-size:11.5px; margin-top:4px;">уверенность ${(op.confidence * 100).toFixed(0)}%
        ${op.evidence && op.evidence.length ? `<button class="text-btn" onclick='showEvidence(${JSON.stringify(op.evidence)})'>Почему?</button>` : ""}
      </p>
    </div>
  `).join("") || `<div class="empty-state">нет гипотез в наблюдаемой выборке</div>`;
}

// ---------------------------------------------------------------------------
// Evidence modal ("Источники" / кнопки "Почему?") - GET /api/evidence/{id}
// (часть того же real-analysis API, не legacy endpoint).
// ---------------------------------------------------------------------------
async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

const EVIDENCE_SECTION_ORDER = ["fact", "computed", "visual_ai", "ai_inference"];
const EVIDENCE_TYPE_LABEL = { fact: "Факт", computed: "Расчёт", visual_ai: "Визуальный сигнал", ai_inference: "Вывод AI" };

function humanizeKey(key) {
  return String(key).replace(/^[a-z_]+:/i, "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function formatEvidenceValue(ev) {
  const v = ev.value;
  if (typeof v === "boolean") return v ? "да" : "нет";
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
  if (v && typeof v === "object") {
    const trueKeys = Object.entries(v).filter(([, val]) => val === true).map(([k]) => humanizeKey(k));
    return trueKeys.length ? trueKeys.join(", ") : "—";
  }
  return v == null ? "—" : String(v);
}

function evidenceItemHtml(ev) {
  return `
    <div class="evidence-item">
      <span class="evidence-type-tag ${ev.type}">${EVIDENCE_TYPE_LABEL[ev.type] || ev.type}</span>
      <div class="ev-text"><strong>${humanizeKey(ev.field)}:</strong> ${formatEvidenceValue(ev)}</div>
      ${ev.raw_fragment ? `<div class="ev-meta">${ev.raw_fragment}</div>` : ""}
      ${ev.source_url ? `<div class="ev-meta"><a href="${ev.source_url}" target="_blank" rel="noopener">Открыть источник →</a></div>` : ""}
    </div>
  `;
}

async function showEvidence(ids) {
  const modal = document.getElementById("evidence-modal");
  const body = document.getElementById("evidence-body");
  if (!ids || !ids.length) {
    body.innerHTML = `<div class="empty-state">Источники не привязаны к этому выводу</div>`;
  } else {
    const items = (await Promise.all(ids.map((id) => fetchJson(`/api/evidence/${id}`).catch(() => null)))).filter(Boolean);
    if (!items.length) {
      body.innerHTML = `<div class="empty-state">Источники не найдены</div>`;
    } else {
      const byType = {};
      items.forEach((ev) => { (byType[ev.type] = byType[ev.type] || []).push(ev); });
      const knownTypes = EVIDENCE_SECTION_ORDER.filter((t) => byType[t]);
      const otherTypes = Object.keys(byType).filter((t) => !EVIDENCE_SECTION_ORDER.includes(t));
      body.innerHTML = [...knownTypes, ...otherTypes].map((type) => `
        <div class="evidence-section">
          <div class="evidence-section-title">${EVIDENCE_TYPE_LABEL[type] || type}</div>
          ${byType[type].map(evidenceItemHtml).join("")}
        </div>
      `).join("");
    }
  }
  modal.classList.add("active");
}
document.getElementById("evidence-close").addEventListener("click", () => {
  document.getElementById("evidence-modal").classList.remove("active");
});

// ---------------------------------------------------------------------------
// Навигация по секциям результатов
// ---------------------------------------------------------------------------
const TITLES = {
  overview: "Обзор", "market-map": "Что нашли", "competitor-dna": "Как бренд выбирает",
  "next-move": "Кто подходит дальше", "white-space": "Где меньше конкуренции", "our-move": "Что стоит проверить",
};
const SUBTITLES = {
  overview: "Итог по тому, что агент нашёл в этой выборке.",
  "market-map": "Где и с кем бренд уже появлялся.",
  "competitor-dna": "Какие паттерны видны в его размещениях.",
  "next-move": "Авторы, похожие на наблюдаемый профиль закупки.",
  "white-space": "Сегменты с большим выбором авторов и низкой насыщенностью в нашей выборке.",
  "our-move": "Несколько действий, которые логично проверить сейчас.",
};

document.getElementById("side-nav").addEventListener("click", (e) => {
  const btn = e.target.closest(".nav-item");
  if (btn) goToSection(btn.dataset.section);
});

function goToSection(key) {
  document.querySelectorAll("#side-nav .nav-item").forEach((el) => el.classList.toggle("active", el.dataset.section === key));
  document.querySelectorAll("#view-results .section").forEach((el) => el.classList.remove("active"));
  document.getElementById(`section-${key}`).classList.add("active");
  document.getElementById("page-title").textContent = TITLES[key];
  document.getElementById("page-subtitle").textContent = SUBTITLES[key];
}

document.getElementById("page-subtitle").textContent = SUBTITLES.overview;
