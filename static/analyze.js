// Новый dark-premium frontend поверх уже существующего real-analysis API.
//
// КРИТИЧЕСКОЕ ПРАВИЛО: единственный источник данных для этого UI - новый
// real-analysis flow:
//   POST /api/analyze            -> { analysis_id }
//   GET  /api/analysis/{id}      -> полный AnalysisResult
//   GET  /api/analysis/{id}/evidence/{evidence_id} -> evidence этого анализа
// Никакие legacy/demo endpoints (/api/overview, /api/market-map,
// /api/competitor-dna, /api/next-moves, /api/white-space, /api/our-move) НЕ
// используются здесь - они принадлежат /demo.html + app.js (отдельный явный
// demo-режим) и не должны быть fallback-источником для этого интерфейса.
// Все карточки/counts/coverage/evidence строятся только из AnalysisResult.

const state = {
  result: null,
  findingsById: new Map(),
  nextMoveCandidates: new Map(),
  whiteSpaceCells: [],
  ourMoveItems: [],
};

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function safeUrl(value) {
  if (!value) return "";
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
  } catch (_err) {
    return "";
  }
}

function formatNumber(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 }).format(Number(value));
}

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString("ru-RU");
}

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
// Клиентская оценка времени - зависит от площадок, периода и уровня поиска.
// ---------------------------------------------------------------------------
const DATE_RANGE_TIME_BASE = {
  "7d": [1, 2],
  "30d": [2, 3],
  "90d": [3, 5],
};

function computeEstimate() {
  const dateRange = document.getElementById("cfg-date-range").value || "90d";
  let [lo, hi] = DATE_RANGE_TIME_BASE[dateRange] || DATE_RANGE_TIME_BASE["90d"];
  const extra = Math.max(0, selectedPlatforms.size - 1);
  const level = document.getElementById("cfg-search-level")?.value || "light";
  const levelExtra = level === "deep" ? 2 : (level === "standard" ? 1 : 0);
  lo = Math.min(4, lo + extra + levelExtra);
  hi = Math.min(5, hi + extra + levelExtra);
  return { lo, hi };
}

function updateEstimateHint() {
  const est = computeEstimate();
  const hint = document.getElementById("estimate-hint");
  if (hint) hint.textContent = `при текущих настройках — обычно около ${est.lo}–${est.hi} мин`;
}

document.getElementById("cfg-date-range").addEventListener("change", updateEstimateHint);
document.getElementById("cfg-search-level").addEventListener("change", updateEstimateHint);

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
    search_level: document.getElementById("cfg-search-level").value,
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
  closeDetailDrawer();
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
  limSlot.innerHTML = (r.limitations || []).map((text) => `<div class="limitation-banner">${escapeHtml(text)}</div>`).join("");
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
      body: pattern ? `<strong>${escapeHtml(pattern.competitor)}:</strong> ${escapeHtml(pattern.statement)}` : "Недостаточно данных в этой выборке.",
      ev: pattern ? pattern.evidence_ids : null,
    },
    {
      kicker: "Лучший кандидат",
      body: candidate ? `<strong>${escapeHtml(candidate.candidate)}</strong> — совпадение ${candidate.similarity_score}/100 с наблюдаемым профилем закупки бренда.` : "Недостаточно данных в этой выборке.",
      ev: candidate ? candidate.evidence_ids : null,
    },
    {
      kicker: "Сильный сегмент",
      body: segment ? `<strong>${escapeHtml(segment.segment.label)}</strong> — оценка возможности ${segment.opportunity_score}/100, конкуренция в наблюдаемой выборке: ${competitionLabel(segment.saturation_score)}.` : "Недостаточно данных в этой выборке.",
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

  document.getElementById("articles-funnel").innerHTML = buildArticlesFunnel(r);
  document.getElementById("overview-highlights").innerHTML = renderHighlights(r);
}

function providerDisplayName(provider) {
  const raw = String(provider || "").trim();
  if (!raw) return "";
  if (raw.toLowerCase() === "serpapi") return "SerpAPI";
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

function buildArticlesFunnel(result) {
  const coverageRows = ((result.coverage && result.coverage.platforms) || [])
    .filter((row) => row.platform === "articles");
  if (!coverageRows.length) return "";

  const found = coverageRows.reduce((sum, row) => sum + Number(row.items_collected || 0), 0);
  const checked = coverageRows.reduce(
    (sum, row) => sum + Number(row.items_checked == null ? row.items_collected || 0 : row.items_checked), 0,
  );
  const confirmed = coverageRows.reduce((sum, row) => sum + Number(row.confirmed_integrations || 0), 0);
  const organic = coverageRows.reduce((sum, row) => sum + Number(row.organic_mentions || 0), 0);
  const potential = coverageRows.reduce((sum, row) => sum + Number(row.potential_creators || 0), 0);
  const providers = [...new Set(coverageRows.map((row) => row.search_provider).filter(Boolean))]
    .map(providerDisplayName);
  const providerLabel = providers.join(" / ");
  const throughProvider = providerLabel ? ` через ${providerLabel}` : "";
  const zeroConfirmedText = found > 0 && confirmed === 0
    ? `<p class="articles-funnel-note">Найдено ${found} материалов${throughProvider}, после проверки подтверждённых рекламных интеграций не найдено.</p>`
    : "";

  return `
    <div class="articles-funnel-card">
      <div class="articles-funnel-title">Статьи / Web</div>
      <div class="articles-funnel-metrics">
        <span>Найдено материалов: <strong>${found}</strong></span>
        <span>Реальных статей после фильтра: <strong>${checked}</strong></span>
        <span>Подтверждено интеграций: <strong>${confirmed}</strong></span>
        <span>Потенциальные / органические: <strong>${organic + potential}</strong></span>
      </div>
      ${providerLabel ? `<div class="articles-funnel-provider">Источник поиска: ${escapeHtml(providerLabel)}</div>` : ""}
      ${zeroConfirmedText}
    </div>
  `;
}

const CLASSIFICATION_LABEL = {
  confirmed: "Подтверждённая интеграция",
  confirmed_sponsored: "Подтверждённая реклама",
  affiliate: "Партнёрское издание",
  affiliate_publisher: "Партнёрское издание",
  editorial_publisher: "Издание / обзор",
  retailer: "Магазин",
  brand_owned: "Материал бренда",
  partner_content: "Издание / обзор",
  editorial_review: "Издание / обзор",
  organic_mention: "Органическое упоминание",
  potential_creator: "Потенциальный автор",
  manual_review: "Требует проверки",
  probable: "Вероятно релевантно",
};

const ENTITY_TYPE_LABEL = {
  creator: "Автор",
  publisher: "Издание / обзор",
  affiliate_publisher: "Партнёрское издание",
  editorial_publisher: "Издание / обзор",
  retailer: "Магазин",
  brand_owned: "Материал бренда",
};
function entityTypeLabel(value) { return ENTITY_TYPE_LABEL[value] || "Источник"; }
const SIZE_LABEL = { nano: "Нано", micro: "Микро", mid: "Средние", macro: "Крупные" };
const FACTOR_LABEL = {
  creator_size_match: "Размер автора",
  topic_match: "Тематика",
  platform_match: "Площадка",
  content_type_match: "Формат",
  views_profile_match: "Просмотры",
  recent_strategy_match: "Недавний паттерн",
};
const SIGNAL_LABEL = {
  paid_partnership: "Платное партнёрство",
  sponsored_label: "Маркировка рекламы",
  hashtag_ad: "#ad / #sponsored",
  promo_code: "Промокод",
  affiliate_link: "Партнёрская ссылка",
  affiliate_pattern: "Партнёрская ссылка / паттерн",
  commercial_cta: "Коммерческий призыв",
  brand_product_url: "Ссылка на бренд / продукт",
  direct_brand_link: "Прямая ссылка на бренд",
  explicit_partnership: "Явное партнёрство",
  partner_wording: "Партнёрская формулировка",
  sponsor_wording: "Спонсорская формулировка",
  ambassador: "Амбассадорство",
  review_wording: "Формат обзора",
  first_person_use: "Личное использование",
  recommendation: "Рекомендация",
  organic_affinity: "Органический интерес",
};

function classificationLabel(value) {
  return CLASSIFICATION_LABEL[value] || humanizeKey(value || "не классифицировано");
}

function classificationTone(value) {
  if (["confirmed", "confirmed_sponsored", "affiliate", "partner_content"].includes(value)) return "confirmed";
  if (["organic_mention", "editorial_review", "potential_creator"].includes(value)) return "potential";
  return "review";
}

function classificationBadge(value) {
  return `<span class="classification-pill ${classificationTone(value)}">${escapeHtml(classificationLabel(value))}</span>`;
}

function signalLabel(value) {
  return SIGNAL_LABEL[value] || humanizeKey(value || "сигнал");
}

function signalChips(values, limit = 3) {
  const items = (values || []).filter(Boolean).slice(0, limit);
  if (!items.length) return `<span class="muted-value">—</span>`;
  return `<div class="signal-stack">${items.map((value) => `<span class="signal-chip">${escapeHtml(signalLabel(value))}</span>`).join("")}</div>`;
}

function externalLink(url, label = "Открыть источник ↗", className = "source-link") {
  const href = safeUrl(url);
  if (!href) return "";
  return `<a class="${className}" href="${escapeHtml(href)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${escapeHtml(label)}</a>`;
}

function evidenceButton(ids, label = "Показать подтверждения") {
  if (!ids || !ids.length) return "";
  return `<button type="button" class="text-btn" onclick='event.stopPropagation(); showEvidence(${JSON.stringify(ids)})'>${escapeHtml(label)}</button>`;
}

function metricGrid(metrics) {
  const data = metrics || {};
  const items = [
    ["Подписчики", formatNumber(data.followers)],
    ["Медиана просмотров", formatNumber(data.median_views)],
    ["Средние просмотры", formatNumber(data.avg_views)],
    ["Вовлечённость", formatPercent(data.engagement_rate)],
  ];
  return `<div class="detail-metric-grid">${items.map(([label, value]) => `
    <div class="detail-metric"><span>${label}</span><strong>${value}</strong></div>
  `).join("")}</div>`;
}

function openDetailDrawer(kicker, title, bodyHtml) {
  document.getElementById("drawer-kicker").textContent = kicker || "";
  document.getElementById("drawer-title").textContent = title || "Подробности";
  document.getElementById("drawer-body").innerHTML = bodyHtml || "";
  document.getElementById("detail-drawer").classList.add("active");
  document.getElementById("detail-drawer-backdrop").classList.add("active");
  document.getElementById("detail-drawer").setAttribute("aria-hidden", "false");
  document.body.classList.add("drawer-open");
}

function closeDetailDrawer() {
  document.getElementById("detail-drawer").classList.remove("active");
  document.getElementById("detail-drawer-backdrop").classList.remove("active");
  document.getElementById("detail-drawer").setAttribute("aria-hidden", "true");
  document.body.classList.remove("drawer-open");
}

function findingDetailBody(finding) {
  const source = externalLink(finding.source_url, "Открыть источник ↗", "drawer-primary-link");
  const topicFormat = [finding.topic, finding.format].filter(Boolean).map(humanizeKey).join(" · ") || "—";
  return `
    <div class="drawer-status-row">
      ${classificationBadge(finding.classification)}
      <span>${escapeHtml(PLATFORM_LABEL[finding.platform] || finding.platform || "—")}</span>
      <span>${formatDate(finding.published_at)}</span>
    </div>
    ${finding.content_preview ? `<div class="drawer-lead">${escapeHtml(finding.content_preview)}</div>` : ""}
    <div class="detail-section">
      <div class="detail-section-title">Материал</div>
      <div class="detail-list-row"><span>Автор / издание</span><strong>${escapeHtml(finding.entity_name || "—")}</strong></div>
      <div class="detail-list-row"><span>Тематика / формат</span><strong>${escapeHtml(topicFormat)}</strong></div>
      <div class="detail-list-row"><span>Тип сущности</span><strong>${escapeHtml(entityTypeLabel(finding.entity_type))}</strong></div>
    </div>
    ${metricGrid(finding.metrics)}
    <div class="detail-section">
      <div class="detail-section-title">Обнаруженные сигналы</div>
      ${signalChips(finding.detected_signals, 12)}
    </div>
    <div class="drawer-actions">
      ${source}
      ${evidenceButton(finding.evidence_ids, "Почему так классифицировано?")}
    </div>
  `;
}

function openFindingDrawer(findingId) {
  const finding = state.findingsById.get(findingId);
  if (!finding) return;
  openDetailDrawer(
    finding.entity_type === "creator" ? "Автор / источник" : "Издание / источник",
    finding.content_title || finding.entity_name,
    findingDetailBody(finding),
  );
}

function barRow(label, value, max, color, ev) {
  const pct = max ? Math.round((value / max) * 100) : 0;
  const evBtn = ev && ev.length ? evidenceButton(ev, "Почему?") : "";
  return `<div class="bar-row">
    <div class="label" title="${escapeHtml(label)}">${escapeHtml(label)}</div>
    <div class="bar-track"><div class="bar-fill" style="width:${pct}%; background:${color}"></div></div>
    <div class="bar-value">${value}</div>
    ${evBtn}
  </div>`;
}

const SERIES = ["#4c8fe0", "#d97a4c", "#33ab6f", "#d9a13d", "#c77fb0", "#4caf7d", "#8a7fd9", "#dd6a63"];

function renderMarketMap() {
  const mm = state.result.market_map || {};
  const findingsEl = document.getElementById("mm-findings");
  const findings = (state.result.findings || []).filter((finding) => safeUrl(finding.source_url));
  state.findingsById = new Map(findings.map((finding) => [finding.finding_id, finding]));

  findingsEl.innerHTML = findings.length ? `
    <div class="data-table-card">
      <div class="data-table-head">
        <div>
          <div class="section-label">Наблюдаемые находки</div>
          <div class="data-table-note">Каждая строка ведёт к исходному публичному материалу.</div>
        </div>
        <div class="data-table-count">${findings.length}</div>
      </div>
      <div class="table-wrap">
        <table class="findings-table">
          <thead><tr>
            <th>Автор / издание</th><th>Площадка</th><th>Контент / источник</th>
            <th>Тематика / формат</th><th>Сигнал</th><th>Классификация</th>
          </tr></thead>
          <tbody>${findings.map((finding) => {
            const source = externalLink(finding.source_url, "Открыть ↗");
            const topicFormat = [finding.topic, finding.format].filter(Boolean).map(humanizeKey).join(" · ") || "—";
            return `<tr class="clickable-row" data-finding-id="${escapeHtml(finding.finding_id)}">
              <td><div class="entity-name">${escapeHtml(finding.entity_name || "—")}</div><div class="cell-meta">${escapeHtml(entityTypeLabel(finding.entity_type))}</div></td>
              <td>${escapeHtml(PLATFORM_LABEL[finding.platform] || finding.platform || "—")}</td>
              <td><div class="content-title">${escapeHtml(finding.content_title || "Материал")}</div>${source}</td>
              <td>${escapeHtml(topicFormat)}</td>
              <td>${signalChips(finding.detected_signals, 2)}</td>
              <td>${classificationBadge(finding.classification)}</td>
            </tr>`;
          }).join("")}</tbody>
        </table>
      </div>
    </div>
  ` : `<div class="empty-state">В текущей выборке нет находок с доступным публичным source URL.</div>`;
  findingsEl.onclick = (event) => {
    if (event.target.closest("a, button")) return;
    const row = event.target.closest("[data-finding-id]");
    if (row) openFindingDrawer(row.dataset.findingId);
  };

  const compGrid = document.getElementById("mm-competitors");
  const competitors = mm.competitors || [];
  document.getElementById("mm-summary-label").style.display = competitors.length ? "block" : "none";
  compGrid.innerHTML = competitors.map((c, idx) => `
    <div class="card" style="animation-delay:${idx * 0.07}s">
      <h3>${escapeHtml(c.name)}</h3>
      <p class="subtitle">
        ${c.total_integrations} интеграций в наблюдаемой выборке · ${c.unique_creators} уникальных авторов ·
        повтор ${((c.repeat_creator_rate || 0) * 100).toFixed(0)}%
        ${evidenceButton(c.evidence_ids || [], "Почему?")}
      </p>
      ${Object.entries(c.platform_distribution || {}).map(([key, value], i) => barRow(
        PLATFORM_LABEL[key] || key, value, Math.max(1, ...Object.values(c.platform_distribution || {})), SERIES[i % SERIES.length],
      )).join("")}
      ${Object.entries(c.topic_distribution || {}).slice(0, 6).map(([key, value]) => `<span class="chip">${escapeHtml(key)}: ${value}</span>`).join("")}
    </div>
  `).join("") || `<div class="empty-state">Нет сводных данных в наблюдаемой выборке.</div>`;
}

function renderDna() {
  const grid = document.getElementById("dna-cards");
  grid.innerHTML = (state.result.competitor_dna || []).map((d, idx) => `
    <div class="card" style="animation-delay:${idx * 0.07}s">
      <h3>${escapeHtml(d.competitor)}</h3>
      ${d.strategy_message ? `<div class="insufficient-tag">${escapeHtml(d.strategy_message)}</div>` : ""}
      ${(d.observed_patterns || []).map((p) => `
        <div class="dna-pattern">
          <div class="dna-statement">${escapeHtml(p.statement)} ${evidenceButton(p.evidence_ids || [], "Почему?")}</div>
          <div class="confidence-bar-track"><div class="confidence-bar-fill" style="width:${Math.round(p.confidence * 100)}%"></div></div>
        </div>
      `).join("") || `<div class="empty-state">Нет наблюдаемых паттернов в этой выборке.</div>`}
    </div>
  `).join("") || `<div class="empty-state">Нет данных по бренду.</div>`;
}

function candidateBadges(candidate) {
  const badges = [];
  if (candidate.has_organic_brand_affinity) {
    badges.push(["Органический интерес", "organic"]);
    badges.push(["Уже упоминает бренд", "mention"]);
  }
  if (candidate.not_used_by_brand) badges.push(["Не найден среди интеграций бренда", "unused"]);
  if (candidate.similarity_score != null && Number(candidate.similarity_score) >= 60) badges.push(["Подходит по паттерну", "match"]);
  return `<div class="rank-badges">${badges.map(([label, tone]) => `<span class="ui-badge ${tone}">${label}</span>`).join("")}</div>`;
}

function candidateReason(candidate) {
  const factors = (candidate.why || [])
    .filter((factor) => Number(factor.factor_score || 0) > 0)
    .sort((a, b) => Number(b.contribution || 0) - Number(a.contribution || 0))
    .slice(0, 3);
  if (!factors.length) return candidate.has_organic_brand_affinity
    ? "Есть наблюдаемый органический интерес к бренду."
    : "Недостаточно метрик для числового сравнения.";
  return factors.map((factor) => `${FACTOR_LABEL[factor.factor] || humanizeKey(factor.factor)} ${Math.round(factor.factor_score * 100)}%`).join(" · ");
}

function candidateDetailBody(candidate) {
  const source = externalLink(candidate.canonical_url, "Открыть профиль ↗", "drawer-primary-link");
  const factors = (candidate.why || []).slice().sort((a, b) => Number(b.contribution || 0) - Number(a.contribution || 0));
  return `
    <div class="drawer-status-row">
      <span>${escapeHtml(PLATFORM_LABEL[candidate.platform] || candidate.platform || "—")}</span>
      <span>${formatNumber(candidate.followers)} подписчиков</span>
      <span>${escapeHtml(candidate.competitor ? `Профиль: ${candidate.competitor}` : "")}</span>
    </div>
    <div class="strategy-detail">
      <div><span>Соответствие</span><strong>${candidate.similarity_score == null ? escapeHtml(candidate.match_label || "Недостаточно метрик") : `${Number(candidate.similarity_score)}/100`}</strong></div>
      ${candidate.similarity_score == null ? "" : `<div class="strategy-track"><div class="strategy-fill" style="width:${Math.max(0, Math.min(100, Number(candidate.similarity_score)))}%"></div></div>`}
    </div>
    ${candidate.note ? `<div class="drawer-callout">${escapeHtml(candidate.note)}</div>` : ""}
    ${candidateBadges(candidate)}
    ${metricGrid(candidate)}
    ${(candidate.topics || []).length ? `<div class="detail-section"><div class="detail-section-title">Тематики</div><div>${candidate.topics.map((topic) => `<span class="chip">${escapeHtml(topic)}</span>`).join("")}</div></div>` : ""}
    <div class="detail-section">
      <div class="detail-section-title">Почему совпадает</div>
      <div class="factor-list">${factors.map((factor) => `
        <div class="factor-row">
          <span>${escapeHtml(FACTOR_LABEL[factor.factor] || humanizeKey(factor.factor))}</span>
          <div class="factor-track"><div style="width:${Math.round(Number(factor.factor_score || 0) * 100)}%"></div></div>
          <strong>${Math.round(Number(factor.factor_score || 0) * 100)}%</strong>
        </div>
      `).join("")}</div>
    </div>
    <div class="drawer-actions">${source}${evidenceButton(candidate.evidence_ids, "Показать расчёт")}</div>
  `;
}

function openCandidateDrawer(candidateKey) {
  const candidate = state.nextMoveCandidates.get(candidateKey);
  if (!candidate) return;
  openDetailDrawer("Кандидат", candidate.candidate, candidateDetailBody(candidate));
}

function renderNextMove() {
  const container = document.getElementById("nm-tables");
  const candidates = [];
  (state.result.next_move || []).forEach((entry) => {
    (entry.candidates || []).forEach((candidate) => candidates.push({ ...candidate, competitor: entry.competitor }));
  });
  candidates.sort((a, b) => Number(b.similarity_score || 0) - Number(a.similarity_score || 0));
  state.nextMoveCandidates = new Map();

  if (!candidates.length) {
    container.innerHTML = `<div class="empty-state">Нет кандидатов в наблюдаемой базе авторов.</div>`;
    return;
  }

  container.innerHTML = `
    <div class="ranking-note">Соответствие показывает близость к наблюдаемому профилю выбора бренда, а не вероятность сделки.</div>
    <div class="ranked-list">${candidates.map((candidate, index) => {
      const key = `candidate_${index}`;
      state.nextMoveCandidates.set(key, candidate);
      return `<button type="button" class="rank-card" data-candidate-key="${key}">
        <div class="rank-number">${String(index + 1).padStart(2, "0")}</div>
        <div class="rank-main">
          <div class="rank-title-row">
            <div><strong>${escapeHtml(candidate.candidate)}</strong><span>${escapeHtml(PLATFORM_LABEL[candidate.platform] || candidate.platform || "—")} · ${formatNumber(candidate.followers)} подписчиков</span></div>
            <div class="rank-score">${candidate.similarity_score == null
              ? `<strong class="rank-score-label">${escapeHtml(candidate.match_label || "Недостаточно метрик")}</strong>`
              : `<strong>${Number(candidate.similarity_score)}</strong><span>/100</span>`}</div>
          </div>
          <div class="rank-reason">${escapeHtml(candidateReason(candidate))}</div>
          ${candidateBadges(candidate)}
          ${candidate.similarity_score == null ? "" : `<div class="strategy-track"><div class="strategy-fill" style="width:${Math.max(0, Math.min(100, Number(candidate.similarity_score)))}%"></div></div>`}
        </div>
      </button>`;
    }).join("")}</div>
  `;
  container.onclick = (event) => {
    const card = event.target.closest("[data-candidate-key]");
    if (card) openCandidateDrawer(card.dataset.candidateKey);
  };
}

function competitionLabel(saturationScore) {
  const score = Number(saturationScore || 0);
  if (score < 34) return "низкая";
  if (score < 67) return "средняя";
  return "высокая";
}

function whiteSpaceMatrix(segments) {
  const platforms = [...new Set(segments.map((item) => item.segment && item.segment.platform).filter(Boolean))];
  const buckets = [...new Set(segments.map((item) => item.segment && item.segment.followers_bucket).filter(Boolean))];
  const columnKey = platforms.length > 1 || buckets.length <= 1 ? "platform" : "followers_bucket";
  const rawColumns = columnKey === "platform" ? platforms : buckets;
  const columns = rawColumns.length ? rawColumns : ["all"];
  const rows = new Map();

  segments.forEach((item) => {
    const segment = item.segment || {};
    const topic = segment.topic || "Без темы";
    const platform = segment.platform || "all";
    const bucket = segment.followers_bucket || "all";
    const rowKey = columnKey === "platform" ? `${topic}|${bucket}` : `${topic}|${platform}`;
    const rowLabel = columnKey === "platform"
      ? [topic, SIZE_LABEL[bucket] || (bucket !== "all" ? humanizeKey(bucket) : null)].filter(Boolean).join(" · ")
      : [topic, PLATFORM_LABEL[platform] || (platform !== "all" ? platform : null)].filter(Boolean).join(" · ");
    const column = columnKey === "platform" ? platform : bucket;
    if (!rows.has(rowKey)) rows.set(rowKey, { label: rowLabel, cells: new Map(), maxOpportunity: 0 });
    const row = rows.get(rowKey);
    row.cells.set(column, item);
    row.maxOpportunity = Math.max(row.maxOpportunity, Number(item.opportunity_score || 0));
  });

  return {
    columnKey,
    columns,
    rows: [...rows.values()].sort((a, b) => b.maxOpportunity - a.maxOpportunity),
  };
}

function whiteSpaceCellClass(segment, strongest) {
  if (segment === strongest) return "strong";
  const saturation = Number(segment.saturation_score || 0);
  return saturation < 34 ? "low" : "neutral";
}

function segmentDetailBody(segment) {
  const creators = segment.top_creators || [];
  const sources = (segment.observed_sources || []).filter((source) => safeUrl(source.source_url));
  const saturation = Number(segment.saturation_score || 0);
  const callout = saturation < 34
    ? "Низкая конкурентная насыщенность в наблюдаемой выборке."
    : `Конкурентная насыщенность в наблюдаемой выборке: ${competitionLabel(saturation)}.`;
  return `
    <div class="drawer-callout ${saturation < 34 ? "positive" : ""}">${callout}</div>
    <div class="detail-metric-grid">
      <div class="detail-metric"><span>Доступно авторов</span><strong>${segment.available_creators || 0}</strong></div>
      <div class="detail-metric"><span>Подтверждено интеграций</span><strong>${segment.confirmed_integrations || 0}</strong></div>
      <div class="detail-metric"><span>Насыщенность</span><strong>${Number(segment.saturation_score || 0)}/100</strong></div>
      <div class="detail-metric"><span>Возможность</span><strong>${Number(segment.opportunity_score || 0)}/100</strong></div>
    </div>
    <div class="detail-section">
      <div class="detail-section-title">Сегмент</div>
      <div class="detail-list-row"><span>Площадка</span><strong>${escapeHtml(PLATFORM_LABEL[segment.segment?.platform] || segment.segment?.platform || "—")}</strong></div>
      <div class="detail-list-row"><span>Размер авторов</span><strong>${escapeHtml(SIZE_LABEL[segment.segment?.followers_bucket] || humanizeKey(segment.segment?.followers_bucket || "—"))}</strong></div>
      <div class="detail-list-row"><span>Активные бренды / конкуренты</span><strong>${escapeHtml((segment.active_competitors || []).join(", ") || "не зафиксированы в этой выборке")}</strong></div>
    </div>
    <div class="detail-section">
      <div class="detail-section-title">Кого можно схантить</div>
      ${creators.length ? `<div class="creator-detail-list">${creators.map((creator) => `
        <div class="creator-detail-item">
          <div><strong>${escapeHtml(creator.name)}</strong><span>${escapeHtml(PLATFORM_LABEL[creator.platform] || creator.platform || "—")} · ${creator.followers == null ? "метрики недоступны" : `${formatNumber(creator.followers)} подписчиков`} · ${creator.median_views == null ? "просмотры недоступны" : `медиана ${formatNumber(creator.median_views)}`}</span></div>
          <div class="creator-detail-score">${(creator.topic_tags || []).length ? `Темы: ${escapeHtml(creator.topic_tags.join(", "))}` : "Темы не определены"}</div>
          <div>
            ${creator.has_organic_brand_affinity ? `<span class="ui-badge organic">Органический интерес</span><span class="ui-badge mention">Уже упоминает бренд</span>` : ""}
            ${creator.already_used_by_competitor ? `<span class="ui-badge used">Есть наблюдаемая интеграция</span>` : `<span class="ui-badge unused">Не найден среди интеграций бренда</span>`}
          </div>
          <div class="drawer-actions">${externalLink(creator.canonical_url, "Открыть профиль ↗")}${evidenceButton(segment.evidence_ids, "Почему подходит?")}</div>
        </div>
      `).join("")}</div>` : `<div class="muted-value">Подходящие авторы не найдены в текущей выборке.</div>`}
    </div>
    ${sources.length ? `<div class="detail-section"><div class="detail-section-title">Наблюдаемые интеграции</div><div class="source-list">${sources.map((source) => `
      <div class="source-list-item"><div><strong>${escapeHtml(source.creator || "Источник")}</strong><span>${escapeHtml(PLATFORM_LABEL[source.platform] || source.platform || "—")} · ${formatDate(source.published_at)} · ${escapeHtml(classificationLabel(source.classification))}</span></div>${externalLink(source.source_url, "Открыть ↗")}</div>
    `).join("")}</div></div>` : ""}
    <div class="drawer-actions">${evidenceButton(segment.evidence_ids, "Показать расчёт сегмента")}</div>
  `;
}

function openWhiteSpaceDrawer(index) {
  const segment = state.whiteSpaceCells[Number(index)];
  if (!segment) return;
  openDetailDrawer("Сегмент", segment.segment?.label || "Сегмент", segmentDetailBody(segment));
}

function renderWhiteSpace() {
  const body = document.getElementById("ws-body");
  const segments = ((state.result.white_space && state.result.white_space.segments) || []).slice(0, 30);
  state.whiteSpaceCells = [];
  if (!segments.length) {
    const creatorCount = Number(state.result?.summary?.creator_universe_size || 0);
    const limited = ((state.result?.coverage?.platforms || []).filter((p) => p.status !== "ok").map((p) => PLATFORM_LABEL[p.platform] || p.platform).join(", ")) || "нет дополнительных данных";
    body.innerHTML = `<div class="empty-state"><strong>Пока недостаточно авторов для карты сегментов.</strong><br>Обнаружено авторов: ${creatorCount}<br>Ограничение источника: ${escapeHtml(limited)}</div>`;
    return;
  }

  const matrix = whiteSpaceMatrix(segments);
  const strongest = segments.reduce((best, item) => (
    !best || Number(item.opportunity_score || 0) > Number(best.opportunity_score || 0) ? item : best
  ), null);
  const template = `minmax(190px, 1.35fr) repeat(${matrix.columns.length}, minmax(150px, 1fr))`;
  body.innerHTML = `
    <div class="ws-matrix-shell">
      <div class="ws-matrix-head">
        <div><strong>Насыщенность и возможность</strong><span>Ячейки построены только из сегментов текущего анализа.</span></div>
        <div class="ws-legend"><span><i class="strong"></i>Сильная возможность</span><span><i class="low"></i>Низкая насыщенность</span><span><i class="neutral"></i>Наблюдаемая активность</span></div>
      </div>
      <div class="ws-matrix-scroll">
        <div class="ws-matrix-grid" style="grid-template-columns:${template}">
          <div class="ws-corner">Сегмент</div>
          ${matrix.columns.map((column) => `<div class="ws-column-head">${escapeHtml(matrix.columnKey === "platform" ? (PLATFORM_LABEL[column] || column) : (SIZE_LABEL[column] || humanizeKey(column)))}</div>`).join("")}
          ${matrix.rows.map((row) => `
            <div class="ws-row-label">${escapeHtml(row.label)}</div>
            ${matrix.columns.map((column) => {
              const segment = row.cells.get(column);
              if (!segment) return `<div class="ws-cell empty" aria-hidden="true"></div>`;
              const index = state.whiteSpaceCells.push(segment) - 1;
              const tone = whiteSpaceCellClass(segment, strongest);
              const saturation = Number(segment.saturation_score || 0);
              return `<button type="button" class="ws-cell ${tone}" data-cell-index="${index}">
                <strong>${Number(segment.opportunity_score || 0)}</strong>
                <span>возможность</span>
                <small>${saturation < 34 ? "низкая насыщенность" : `насыщенность ${saturation}/100`}</small>
              </button>`;
            }).join("")}
          `).join("")}
        </div>
      </div>
      <div class="ws-footnote">«Низкая насыщенность» означает низкую конкурентную насыщенность только в наблюдаемой выборке.</div>
    </div>
  `;
  body.onclick = (event) => {
    const cell = event.target.closest("[data-cell-index]");
    if (cell) openWhiteSpaceDrawer(cell.dataset.cellIndex);
  };
}

function relatedObjectForOpportunity(opportunity) {
  if (opportunity.related_type === "creator" && opportunity.related_id) {
    for (const [key, candidate] of state.nextMoveCandidates.entries()) {
      if (candidate.creator_id === opportunity.related_id) return { type: "creator", key, item: candidate };
    }
  }
  if (opportunity.related_type === "segment" && opportunity.related_id) {
    const index = state.whiteSpaceCells.findIndex((segment) => segment.segment?.key === opportunity.related_id);
    if (index >= 0) return { type: "segment", index, item: state.whiteSpaceCells[index] };
  }
  return null;
}

function opportunityDetailBody(opportunity) {
  const related = relatedObjectForOpportunity(opportunity);
  let relatedHtml = "";
  if (related?.type === "creator") {
    const candidate = related.item;
    relatedHtml = `<div class="detail-section"><div class="detail-section-title">Связанный автор</div>
      <div class="related-preview"><strong>${escapeHtml(candidate.candidate)}</strong><span>${escapeHtml(PLATFORM_LABEL[candidate.platform] || candidate.platform || "—")} · соответствие ${Number(candidate.similarity_score || 0)}/100</span></div>
    </div>`;
  } else if (related?.type === "segment") {
    const segment = related.item;
    relatedHtml = `<div class="detail-section"><div class="detail-section-title">Связанный сегмент</div>
      <div class="related-preview"><strong>${escapeHtml(segment.segment?.label || "Сегмент")}</strong><span>Возможность ${Number(segment.opportunity_score || 0)}/100 · насыщенность ${Number(segment.saturation_score || 0)}/100</span></div>
    </div>`;
  }
  return `
    <div class="drawer-status-row"><span>Уверенность ${Math.round(Number(opportunity.confidence || 0) * 100)}%</span><span>${opportunity.evidence?.length ? "Есть подтверждающие данные" : "Нужна дополнительная проверка"}</span></div>
    <div class="drawer-lead">${escapeHtml(opportunity.why_now || "")}</div>
    <div class="detail-section"><div class="detail-section-title">Что проверить</div><div class="detail-prose">${escapeHtml(opportunity.suggested_test || "—")}</div></div>
    ${(opportunity.creators || []).length ? `<div class="detail-section"><div class="detail-section-title">Авторы</div><div>${opportunity.creators.map((name) => `<span class="chip">${escapeHtml(name)}</span>`).join("")}</div></div>` : ""}
    ${relatedHtml}
    <div class="drawer-actions">${evidenceButton(opportunity.evidence, "Показать подтверждения")}</div>
  `;
}

function openOpportunityDrawer(index) {
  const opportunity = state.ourMoveItems[Number(index)];
  if (!opportunity) return;
  openDetailDrawer("Действие", opportunity.title, opportunityDetailBody(opportunity));
}

function renderOurMove() {
  const grid = document.getElementById("om-cards");
  state.ourMoveItems = (state.result.our_move?.opportunities || []).slice(0, 5);
  if (!state.ourMoveItems.length) {
    grid.innerHTML = `<div class="empty-state">Нет проверяемых действий в текущей выборке.</div>`;
    return;
  }
  const priorityLabel = { high: "Высокий приоритет", medium: "Средний приоритет", low: "Низкий приоритет" };
  grid.innerHTML = state.ourMoveItems.map((opportunity, index) => `
    <button type="button" class="move-card" data-opportunity-index="${index}" style="animation-delay:${index * 0.07}s">
      <div class="move-number">${String(index + 1).padStart(2, "0")}</div>
      <div class="move-content">
        <div class="move-head"><h3>${escapeHtml(opportunity.title)}</h3><span class="badge badge-${escapeHtml(opportunity.priority)}">${priorityLabel[opportunity.priority] || opportunity.priority}</span></div>
        <p>${escapeHtml(opportunity.why_now || "")}</p>
        <div class="move-related">${opportunity.related_label ? `Связано: ${escapeHtml(opportunity.related_label)}` : "Самостоятельная проверка"}</div>
        <div class="move-footer"><span>Уверенность ${Math.round(Number(opportunity.confidence || 0) * 100)}%</span><span>${opportunity.evidence?.length ? "Есть подтверждающие данные" : "Нужна проверка"}</span></div>
      </div>
    </button>
  `).join("");
  grid.onclick = (event) => {
    const card = event.target.closest("[data-opportunity-index]");
    if (card) openOpportunityDrawer(card.dataset.opportunityIndex);
  };
}

// ---------------------------------------------------------------------------
// Evidence modal ("Источники" / кнопки "Почему?") - evidence всегда
// разрешается внутри текущего analysis_id, без обращения к legacy/demo cache.
// ---------------------------------------------------------------------------
async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

const EVIDENCE_SECTION_ORDER = ["fact", "computed", "visual_ai", "ai_inference"];
const EVIDENCE_TYPE_LABEL = { fact: "Факт", computed: "Расчёт", visual_ai: "Визуальный сигнал", ai_inference: "Вывод AI" };

const EVIDENCE_FIELD_LABEL = {
  live_integration_confidence: "Уверенность классификации",
  visual_commercial_signal: "Визуальный коммерческий сигнал",
};
function humanizeKey(key) {
  const raw = String(key || "");
  if (EVIDENCE_FIELD_LABEL[raw]) return EVIDENCE_FIELD_LABEL[raw];
  const cleaned = raw.replace(/^[a-z_]+:/i, "").replace(/_/g, " ");
  return cleaned.replace(/^./, (c) => c.toUpperCase());
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
  const source = externalLink(ev.source_url, "Открыть источник →");
  return `
    <div class="evidence-item">
      <span class="evidence-type-tag ${escapeHtml(ev.type)}">${escapeHtml(EVIDENCE_TYPE_LABEL[ev.type] || ev.type)}</span>
      <div class="ev-text"><strong>${escapeHtml(humanizeKey(ev.field))}:</strong> ${escapeHtml(formatEvidenceValue(ev))}</div>
      ${ev.raw_fragment ? `<div class="ev-meta">${escapeHtml(ev.raw_fragment)}</div>` : ""}
      ${source ? `<div class="ev-meta">${source}</div>` : ""}
    </div>
  `;
}

async function showEvidence(ids) {
  const modal = document.getElementById("evidence-modal");
  const body = document.getElementById("evidence-body");
  if (!ids || !ids.length) {
    body.innerHTML = `<div class="empty-state">Источники не привязаны к этому выводу</div>`;
  } else {
    const analysisId = state.result && state.result.analysis_id;
    const items = analysisId
      ? (await Promise.all(ids.map((id) => fetchJson(
        `/api/analysis/${encodeURIComponent(analysisId)}/evidence/${encodeURIComponent(id)}`,
      ).catch(() => null)))).filter(Boolean)
      : [];
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

document.getElementById("drawer-close").addEventListener("click", closeDetailDrawer);
document.getElementById("detail-drawer-backdrop").addEventListener("click", closeDetailDrawer);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    document.getElementById("evidence-modal").classList.remove("active");
    closeDetailDrawer();
  }
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
