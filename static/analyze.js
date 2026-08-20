// Dark-premium frontend for the real-analysis flow only:
//   POST /api/analyze
//   GET  /api/analysis/{analysis_id}
//   GET  /api/analysis/{analysis_id}/evidence/{evidence_id}
// Legacy/demo endpoints remain isolated in /demo.html + app.js.

"use strict";

const state = {
  result: null,
  findings: new Map(),
  candidates: new Map(),
  segments: new Map(),
  actions: [],
};

const PLATFORM_LABEL = {
  youtube: "YouTube",
  instagram: "Instagram",
  tiktok: "TikTok",
  articles: "Статьи / Web",
};
const CLOUD_PLATFORMS = new Set(["youtube", "articles"]);
const FOLLOWER_BUCKET_LABEL = {
  nano: "Нано",
  micro: "Микро",
  mid: "Средние",
  macro: "Крупные",
  unknown: "Размер не указан",
};
const CLASSIFICATION_LABEL = {
  confirmed: "Подтверждённая интеграция",
  confirmed_sponsored: "Спонсорский материал",
  affiliate: "Партнёрская ссылка",
  partner_content: "Партнёрский материал",
  editorial_review: "Редакционный обзор",
  organic_mention: "Органическое упоминание",
  potential_creator: "Потенциальный автор",
  manual_review: "Требует проверки",
  rejected: "Отклонено",
};
const SIGNAL_LABEL = {
  paid_partnership_label: "Paid partnership",
  collaboration_label: "Коллаборация",
  brand_in_title: "Бренд в заголовке",
  brand_in_description: "Бренд в описании",
  brand_mention: "Упоминание бренда",
  promo_code: "Промокод",
  brand_url: "Ссылка на бренд",
  affiliate_link: "Партнёрская ссылка",
  cta_phrase: "Коммерческий CTA",
  sponsor_wording: "Спонсорская формулировка",
  repeated_mention: "Повторное упоминание",
  dedicated_video: "Отдельный материал",
  mention: "Упоминание",
  discount_code: "Промокод",
  review_wording: "Обзор",
  affiliate_pattern: "Affiliate pattern",
  search_provider_evidence: "Данные поиска",
  organic_brand_affinity: "Органический интерес",
  repeated_brand_mention: "Повторное упоминание",
  first_person_use: "Личное использование",
  recommendation: "Рекомендация",
  brand_affinity: "Органический интерес",
};
const FACTOR_LABEL = {
  creator_size_match: "размер автора",
  topic_match: "тематика",
  platform_match: "площадка",
  content_type_match: "формат",
  views_profile_match: "профиль просмотров",
  recent_strategy_match: "недавний паттерн",
};
const EVIDENCE_SECTION_ORDER = ["fact", "computed", "visual_ai", "ai_inference"];
const EVIDENCE_TYPE_LABEL = {
  fact: "Факт",
  computed: "Расчёт",
  visual_ai: "Визуальный сигнал",
  ai_inference: "Вывод AI",
};

const compactNumberFormatter = new Intl.NumberFormat("ru-RU", {
  notation: "compact",
  maximumFractionDigits: 1,
});
const integerFormatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function safeExternalUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(String(value));
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.href;
  } catch (_err) {
    return null;
  }
}

function sourceLink(url, label = "Открыть источник ↗", className = "primary-link") {
  const safe = safeExternalUrl(url);
  if (!safe) return "";
  return `<a class="${className}" href="${escapeHtml(safe)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
}

function domainFromUrl(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch (_err) {
    return "";
  }
}

function formatNumber(value, compact = true) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  if (compact && Math.abs(number) >= 1000) return compactNumberFormatter.format(number);
  return integerFormatter.format(number);
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  const pct = Math.abs(number) <= 1 ? number * 100 : number;
  return `${pct.toFixed(pct < 10 ? 1 : 0)}%`;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("ru-RU", { day: "2-digit", month: "short", year: "numeric" });
}

function humanizeKey(key) {
  const normalized = String(key || "").replace(/^[a-z_]+:/i, "").replace(/^hard:/i, "");
  if (SIGNAL_LABEL[normalized]) return SIGNAL_LABEL[normalized];
  return normalized.replace(/_/g, " ").replace(/^./, (char) => char.toUpperCase());
}

function classificationLabel(value) {
  return CLASSIFICATION_LABEL[value] || humanizeKey(value || "не классифицировано");
}

function classificationClass(group) {
  if (group === "confirmed") return "confirmed";
  if (group === "potential_creator") return "potential";
  if (group === "organic_mention") return "organic";
  return "";
}

function encodedEvidence(ids) {
  return encodeURIComponent(JSON.stringify(Array.isArray(ids) ? ids : []));
}

function evidenceButton(ids, label = "Почему?") {
  if (!Array.isArray(ids) || !ids.length) return "";
  return `<button type="button" class="text-btn" data-evidence-ids="${encodedEvidence(ids)}">${escapeHtml(label)}</button>`;
}

function signalChips(signals, limit = 3) {
  const unique = Array.from(new Set((signals || []).filter(Boolean)));
  if (!unique.length) return `<span class="entity-meta">сигнал не указан</span>`;
  const visible = unique.slice(0, limit);
  const extra = unique.length - visible.length;
  return `<div class="signal-list">${visible.map((item) => `<span class="signal-chip">${escapeHtml(humanizeKey(item))}</span>`).join("")}${extra > 0 ? `<span class="signal-chip">+${extra}</span>` : ""}</div>`;
}

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
// Landing controls
// ---------------------------------------------------------------------------
let selectedPlatforms = new Set(["youtube"]);

function renderPlatformChips() {
  document.querySelectorAll("#platform-chips .chip-toggle").forEach((chip) => {
    chip.classList.toggle("active", selectedPlatforms.has(chip.dataset.platform));
  });
}

document.getElementById("platform-chips").addEventListener("click", (event) => {
  const chip = event.target.closest(".chip-toggle");
  if (!chip) return;
  const key = chip.dataset.platform;
  if (selectedPlatforms.has(key)) {
    if (selectedPlatforms.size > 1) selectedPlatforms.delete(key);
  } else {
    selectedPlatforms.add(key);
  }
  renderPlatformChips();
  updateEstimateHint();
});

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

document.getElementById("advanced-toggle").addEventListener("click", () => {
  const panel = document.getElementById("advanced-panel");
  const toggle = document.getElementById("advanced-toggle");
  panel.classList.toggle("open");
  toggle.classList.toggle("open");
  toggle.setAttribute("aria-expanded", panel.classList.contains("open") ? "true" : "false");
});

function splitCsv(value) {
  return (value || "").split(",").map((item) => item.trim()).filter(Boolean);
}

function collectConfig() {
  const sizes = Array.from(document.querySelectorAll(".cfg-size:checked")).map((element) => element.value);
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

renderPlatformChips();
updateEstimateHint();

// ---------------------------------------------------------------------------
// Progress states
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
    const element = document.getElementById("hero-dots");
    if (element) element.textContent = ".".repeat(dots + 1);
  }, 480);

  let phaseIndex = 0;
  const setPhase = () => {
    statusLine.style.opacity = 0;
    setTimeout(() => {
      statusLine.textContent = STATUS_PHASES[phaseIndex % STATUS_PHASES.length];
      statusLine.style.opacity = 1;
      phaseIndex += 1;
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

function pluralRu(number, forms) {
  const mod100 = Math.abs(number) % 100;
  const mod10 = mod100 % 10;
  if (mod100 > 10 && mod100 < 20) return forms[2];
  if (mod10 > 1 && mod10 < 5) return forms[1];
  if (mod10 === 1) return forms[0];
  return forms[2];
}

function interestingSegmentsCount(result) {
  const segments = (result.white_space && result.white_space.segments) || [];
  return segments.filter((segment) => !segment.insufficient_data && Number(segment.opportunity_score || 0) >= 50).length;
}

function buildFinishedSummary(result) {
  const integrations = Number(result.summary.integrations_found || 0);
  const creators = Number(result.summary.creators_used || 0);
  const segments = interestingSegmentsCount(result);
  return `Нашёл ${integrations} ${pluralRu(integrations, ["интеграцию", "интеграции", "интеграций"])}, ${creators} ${pluralRu(creators, ["автора", "авторов", "авторов"])} и ${segments} ${pluralRu(segments, ["интересный сегмент", "интересных сегмента", "интересных сегментов"])}.`;
}

function showFinishedHero(result) {
  document.getElementById("hero-headline").textContent = "Готово!";
  document.getElementById("hero-sub").textContent = buildFinishedSummary(result);
}

function resetHero() {
  document.getElementById("hero-headline").textContent = "Привет, что для тебя найти?";
  document.getElementById("hero-sub").textContent = "";
  document.getElementById("hero-search-block").style.display = "block";
}

async function fetchJson(url, options = undefined) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `${url} -> ${response.status}`);
  }
  return response.json();
}

document.getElementById("analyze-btn").addEventListener("click", async () => {
  const brand = document.getElementById("brand-input").value.trim();
  const errorElement = document.getElementById("landing-error");
  const analyzeButton = document.getElementById("analyze-btn");
  errorElement.textContent = "";

  if (!brand) {
    errorElement.textContent = "Введите название бренда или ссылку на его аккаунт.";
    return;
  }
  if (!selectedPlatforms.size) {
    errorElement.textContent = "Выберите хотя бы одну площадку в настройках поиска.";
    return;
  }

  analyzeButton.disabled = true;
  const payload = { brand, platforms: Array.from(selectedPlatforms), settings: collectConfig() };
  startProgressAnimation();

  try {
    const { analysis_id: analysisId } = await fetchJson("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.result = await fetchJson(`/api/analysis/${encodeURIComponent(analysisId)}`);
    stopProgressAnimation();
    showFinishedHero(state.result);
    setTimeout(() => {
      renderResults();
      document.getElementById("view-hero").style.display = "none";
      document.getElementById("view-results").style.display = "flex";
    }, 1100);
  } catch (error) {
    stopProgressAnimation();
    resetHero();
    errorElement.textContent = `Не удалось выполнить анализ: ${error.message}`;
  } finally {
    analyzeButton.disabled = false;
  }
});

document.getElementById("new-analysis-btn").addEventListener("click", () => {
  closeDrawer();
  document.getElementById("view-results").style.display = "none";
  document.getElementById("view-hero").style.display = "flex";
  resetHero();
  document.getElementById("landing-error").textContent = "";
});

// ---------------------------------------------------------------------------
// Result indexes and shared helpers
// ---------------------------------------------------------------------------
function segmentKey(segment) {
  const data = segment.segment || segment;
  return [data.topic || "-", data.platform || "-", data.followers_bucket || "-"].join("|");
}

function candidateKey(competitorId, candidate) {
  return `${competitorId || "brand"}::${candidate.creator_id || candidate.candidate}`;
}

function flattenCandidates() {
  const bestByCreator = new Map();
  (state.result.next_move || []).forEach((entry) => {
    (entry.candidates || []).forEach((candidate) => {
      const identity = candidate.creator_id || `${candidate.platform}:${candidate.candidate}`;
      const enriched = {
        ...candidate,
        competitor: entry.competitor,
        competitor_id: entry.competitor_id,
      };
      enriched._key = candidateKey(entry.competitor_id, enriched);
      const current = bestByCreator.get(identity);
      if (!current || Number(enriched.similarity_score || 0) > Number(current.similarity_score || 0)) {
        bestByCreator.set(identity, enriched);
      }
    });
  });
  return Array.from(bestByCreator.values()).sort((a, b) => Number(b.similarity_score || 0) - Number(a.similarity_score || 0));
}

function prepareResultIndexes() {
  state.findings = new Map((state.result.findings || []).map((finding) => [finding.finding_id, finding]));
  state.candidates = new Map();
  flattenCandidates().forEach((candidate) => state.candidates.set(candidate._key, candidate));
  state.segments = new Map();
  (((state.result.white_space || {}).segments) || []).forEach((segment) => state.segments.set(segmentKey(segment), segment));
  state.actions = ((state.result.our_move || {}).opportunities) || [];
}

function aggregateCoverage() {
  const byPlatform = new Map();
  const statusRank = {
    ok: 0,
    degraded: 1,
    manual_intervention_required: 2,
    connector_offline: 3,
    unavailable: 4,
  };

  ((state.result.coverage || {}).platforms || []).forEach((coverage) => {
    const current = byPlatform.get(coverage.platform) || {
      platform: coverage.platform,
      status: coverage.status,
      source_mode: coverage.source_mode,
      reason: coverage.reason,
      items_collected: 0,
      items_checked: 0,
      confirmed_integrations: 0,
      organic_mentions: 0,
      potential_creators: 0,
      manual_review_items: 0,
      searchProviders: new Set(),
    };
    if ((statusRank[coverage.status] ?? 9) > (statusRank[current.status] ?? 9)) current.status = coverage.status;
    if (coverage.source_mode === "live") current.source_mode = "live";
    current.reason = current.reason || coverage.reason;
    current.items_collected += Number(coverage.items_collected || 0);
    current.items_checked += Number(coverage.items_checked ?? coverage.items_collected ?? 0);
    current.confirmed_integrations += Number(coverage.confirmed_integrations || 0);
    current.organic_mentions += Number(coverage.organic_mentions || 0);
    current.potential_creators += Number(coverage.potential_creators || 0);
    current.manual_review_items += Number(coverage.manual_review_items || 0);
    if (coverage.search_provider) current.searchProviders.add(coverage.search_provider);
    byPlatform.set(coverage.platform, current);
  });
  return Array.from(byPlatform.values());
}

function topDnaPattern() {
  let best = null;
  (state.result.competitor_dna || []).forEach((entry) => {
    (entry.observed_patterns || []).forEach((pattern) => {
      if (!best || Number(pattern.confidence || 0) > Number(best.confidence || 0)) {
        best = { ...pattern, competitor: entry.competitor };
      }
    });
  });
  return best;
}

function topWhiteSpaceSegment() {
  let best = null;
  (((state.result.white_space || {}).segments) || []).forEach((segment) => {
    if (segment.insufficient_data) return;
    if (!best || Number(segment.opportunity_score || 0) > Number(best.opportunity_score || 0)) best = segment;
  });
  return best;
}

function lowerFirst(value) {
  return value ? value.charAt(0).toLowerCase() + value.slice(1) : value;
}

function competitionLabel(scoreValue) {
  const score = Number(scoreValue || 0);
  if (score < 34) return "низкая";
  if (score < 67) return "средняя";
  return "высокая";
}

function renderResults() {
  prepareResultIndexes();
  renderTopbar();
  renderOverview();
  renderMarketMap();
  renderDna();
  renderNextMove();
  renderWhiteSpace();
  renderOurMove();
  goToSection("overview");
}

function renderTopbar() {
  const result = state.result;
  document.getElementById("results-brand-name").textContent = result.brand.canonical_name;
  document.getElementById("sources-compact").innerHTML = aggregateCoverage().map((coverage) => {
    const display = sourceDisplay(coverage);
    return `<div class="source-line"><span class="dot ${display.dot}"></span><span class="name">${escapeHtml(PLATFORM_LABEL[coverage.platform] || coverage.platform)}</span><span>${escapeHtml(display.label)}</span></div>`;
  }).join("");
  document.getElementById("limitations-slot").innerHTML = (result.limitations || [])
    .map((text) => `<div class="limitation-banner">${escapeHtml(text)}</div>`)
    .join("");
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------
function buildLead() {
  const pattern = topDnaPattern();
  if (pattern) {
    return `В наблюдаемой выборке у ${pattern.competitor} чаще всего виден такой паттерн: ${lowerFirst(pattern.statement)}`;
  }
  return "Пока недостаточно данных, чтобы выделить устойчивый паттерн.";
}

function renderHighlights() {
  const pattern = topDnaPattern();
  const candidate = flattenCandidates()[0] || null;
  const segment = topWhiteSpaceSegment();
  const cards = [
    {
      kicker: "Главный паттерн",
      body: pattern ? `<strong>${escapeHtml(pattern.competitor)}:</strong> ${escapeHtml(pattern.statement)}` : "Недостаточно данных в этой выборке.",
      evidence: pattern ? pattern.evidence_ids : [],
    },
    {
      kicker: "Лучший кандидат",
      body: candidate ? `<strong>${escapeHtml(candidate.candidate)}</strong> — совпадение ${formatNumber(candidate.similarity_score, false)}/100 с наблюдаемым профилем.` : "Недостаточно данных в этой выборке.",
      evidence: candidate ? candidate.evidence_ids : [],
      candidateKey: candidate ? candidate._key : null,
    },
    {
      kicker: "Сильный сегмент",
      body: segment ? `<strong>${escapeHtml(segment.segment.label)}</strong> — оценка возможности ${formatNumber(segment.opportunity_score, false)}/100, насыщенность: ${competitionLabel(segment.saturation_score)}.` : "Недостаточно данных в этой выборке.",
      evidence: segment ? segment.evidence_ids : [],
      segmentKey: segment ? segmentKey(segment) : null,
    },
  ];

  return cards.map((card, index) => `
    <div class="highlight-card" style="animation-delay:${index * 0.08}s">
      <div class="hc-kicker">${escapeHtml(card.kicker)}</div>
      <div class="hc-text">${card.body}</div>
      <div class="drawer-actions" style="margin-top:12px;">
        ${card.candidateKey ? `<button type="button" class="text-btn" data-open-candidate="${escapeHtml(card.candidateKey)}">Подробнее</button>` : ""}
        ${card.segmentKey ? `<button type="button" class="text-btn" data-open-segment="${escapeHtml(card.segmentKey)}">Подробнее</button>` : ""}
        ${evidenceButton(card.evidence)}
      </div>
    </div>
  `).join("");
}

function renderOverview() {
  const summary = state.result.summary || {};
  document.getElementById("overview-lead").textContent = buildLead();
  const tiles = [
    [summary.confirmed_integrations ?? summary.integrations_found ?? 0, "подтверждённых интеграций"],
    [summary.potential_creators_count ?? 0, "авторов с органическим интересом"],
    [summary.creators_used ?? 0, "авторов и изданий учтено"],
    [summary.creator_universe_size ?? 0, "авторов в базе для сравнения"],
  ];
  document.getElementById("overview-stats").innerHTML = tiles.map(([value, label], index) => `
    <div class="stat-tile" style="animation-delay:${index * 0.06}s"><div class="value">${formatNumber(value, false)}</div><div class="label">${escapeHtml(label)}</div></div>
  `).join("");
  document.getElementById("overview-highlights").innerHTML = renderHighlights();
}

// ---------------------------------------------------------------------------
// Что нашли: Articles funnel + findings table + compact aggregates
// ---------------------------------------------------------------------------
function providerDisplay(provider) {
  const value = String(provider || "").trim();
  const labels = {
    serpapi: "SerpAPI",
    tavily: "Tavily",
  };
  return labels[value.toLowerCase()] || (value ? value.charAt(0).toUpperCase() + value.slice(1) : "");
}

function renderArticlesFunnel() {
  const slot = document.getElementById("articles-funnel");
  const coverage = aggregateCoverage().find((item) => item.platform === "articles");
  if (!coverage) {
    slot.innerHTML = "";
    return;
  }

  const candidates = coverage.items_collected;
  const checked = coverage.items_checked;
  const confirmed = coverage.confirmed_integrations;
  const potential = coverage.potential_creators;
  const organic = coverage.organic_mentions;
  const providers = Array.from(coverage.searchProviders).map(providerDisplay).filter(Boolean);
  const providerText = providers.join(" / ");

  let message = "";
  if (candidates > 0 && confirmed === 0) {
    message = providerText
      ? `Найдено ${candidates} материалов через ${providerText}, но подтверждённых рекламных интеграций в выбранной выборке не найдено.`
      : `Найдено ${candidates} материалов, но подтверждённых рекламных интеграций в выбранной выборке не найдено.`;
  } else if (candidates > 0) {
    message = `Поиск вернул ${candidates} материалов; классификатор подтвердил ${confirmed} рекламных интеграций.`;
  } else {
    message = coverage.reason || "Материалы в выбранной выборке не найдены.";
  }

  const stats = [
    [candidates, "Найдено кандидатов"],
    [checked, "Проверено"],
    [confirmed, "Подтверждено интеграций"],
  ];
  if (potential > 0) stats.push([potential, "Потенциальные авторы"]);
  if (organic > 0) stats.push([organic, "Органические упоминания"]);

  slot.innerHTML = `
    <div class="funnel-card" id="articles-funnel-card">
      <div class="funnel-head">
        <div><h3>Статьи / Web</h3><div class="section-caption">Воронка discovery → классификация</div></div>
        ${providerText ? `<div class="funnel-provider">Источник поиска: ${escapeHtml(providerText)}</div>` : ""}
      </div>
      <div class="funnel-grid">${stats.map(([value, label]) => `<div class="funnel-stat"><span class="value">${formatNumber(value, false)}</span><span class="label">${escapeHtml(label)}</span></div>`).join("")}</div>
      <p class="funnel-message">${escapeHtml(message)}</p>
    </div>
  `;
}

function renderFindingsTable() {
  const findings = Array.from(state.findings.values());
  document.getElementById("findings-count").textContent = `${findings.length} ${pluralRu(findings.length, ["строка", "строки", "строк"])}`;
  const slot = document.getElementById("findings-table");
  if (!findings.length) {
    slot.innerHTML = `<div class="table-card"><div class="empty-state">Классифицированных находок в текущей выборке нет. Статус discovery по источникам показан выше.</div></div>`;
    return;
  }

  const rows = findings.map((finding) => {
    const sourceUrl = safeExternalUrl(finding.source_url);
    const contentTitle = finding.content_title || domainFromUrl(finding.source_url) || "Исходный материал";
    const topics = (finding.topic_tags || []).slice(0, 2).map(humanizeKey).join(", ");
    const format = finding.content_type ? humanizeKey(finding.content_type) : "—";
    const metrics = [];
    if (finding.followers !== null && finding.followers !== undefined) metrics.push(`${formatNumber(finding.followers)} подписчиков`);
    if (finding.median_views !== null && finding.median_views !== undefined) metrics.push(`${formatNumber(finding.median_views)} median views`);
    return `
      <tr class="finding-row" data-finding-id="${escapeHtml(finding.finding_id)}" tabindex="0" role="button">
        <td>
          <div class="entity-name">${escapeHtml(finding.entity_name)}</div>
          <div class="entity-meta">${escapeHtml(metrics.join(" · ") || formatDate(finding.published_at))}</div>
        </td>
        <td><span class="platform-pill">${escapeHtml(PLATFORM_LABEL[finding.platform] || finding.platform)}</span></td>
        <td>
          <div class="source-title">${sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(contentTitle)}</a>` : escapeHtml(contentTitle)}</div>
          <div class="source-domain">${escapeHtml(domainFromUrl(finding.source_url))}</div>
        </td>
        <td><div>${escapeHtml(topics || "Тема не определена")}</div><div class="entity-meta">${escapeHtml(format)}</div></td>
        <td>${signalChips(finding.detected_signals)}</td>
        <td><span class="classification-pill ${classificationClass(finding.classification_group)}">${escapeHtml(classificationLabel(finding.classification))}</span></td>
        <td><button type="button" class="text-btn" data-open-finding="${escapeHtml(finding.finding_id)}">Подробнее</button></td>
      </tr>
    `;
  }).join("");

  slot.innerHTML = `
    <div class="table-card"><div class="table-scroll"><table class="findings-table">
      <thead><tr><th>Автор / издание</th><th>Площадка</th><th>Контент / источник</th><th>Тема / формат</th><th>Сигнал</th><th>Классификация</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div></div>
  `;
}

function barRow(label, value, maxValue) {
  const pct = maxValue ? Math.round((Number(value) / maxValue) * 100) : 0;
  return `<div class="bar-row"><div class="label" title="${escapeHtml(label)}">${escapeHtml(label)}</div><div class="bar-track"><div class="bar-fill" style="width:${Math.max(0, Math.min(100, pct))}%"></div></div><div class="bar-value">${formatNumber(value, false)}</div></div>`;
}

function renderMarketSummary() {
  const marketMap = state.result.market_map || {};
  const competitors = marketMap.competitors || [];
  document.getElementById("mm-competitors").innerHTML = competitors.map((competitor, index) => {
    const platformValues = Object.values(competitor.platform_distribution || {});
    const platformMax = Math.max(1, ...platformValues);
    return `
      <div class="card" style="animation-delay:${index * 0.07}s">
        <h3>${escapeHtml(competitor.name)}</h3>
        <p class="subtitle">${formatNumber(competitor.total_integrations, false)} интеграций · ${formatNumber(competitor.unique_creators, false)} авторов/изданий · повтор ${formatPercent(competitor.repeat_creator_rate)} ${evidenceButton(competitor.evidence_ids)}</p>
        ${Object.entries(competitor.platform_distribution || {}).map(([platform, value]) => barRow(PLATFORM_LABEL[platform] || platform, value, platformMax)).join("")}
        ${Object.entries(competitor.topic_distribution || {}).slice(0, 6).map(([topic, value]) => `<span class="chip">${escapeHtml(humanizeKey(topic))}: ${formatNumber(value, false)}</span>`).join("")}
      </div>
    `;
  }).join("") || `<div class="empty-state">Сводных данных по подтверждённым находкам пока нет.</div>`;
}

function renderMarketMap() {
  renderArticlesFunnel();
  renderFindingsTable();
  renderMarketSummary();
}

// ---------------------------------------------------------------------------
// Как бренд выбирает
// ---------------------------------------------------------------------------
function renderDna() {
  const entries = state.result.competitor_dna || [];
  document.getElementById("dna-cards").innerHTML = entries.map((entry, index) => `
    <div class="card" style="animation-delay:${index * 0.07}s">
      <h3>${escapeHtml(entry.competitor)}</h3>
      ${(entry.insufficient_data || []).length ? `<div class="insufficient-tag">недостаточно данных: ${escapeHtml(entry.insufficient_data.join(", "))}</div>` : ""}
      ${(entry.observed_patterns || []).map((pattern) => `
        <div style="margin-bottom:14px;">
          <div style="font-size:13.5px; color:var(--text-secondary); line-height:1.55;">${escapeHtml(pattern.statement)}</div>
          <div class="confidence-bar-track"><div class="confidence-bar-fill" style="width:${Math.round(Number(pattern.confidence || 0) * 100)}%"></div></div>
          <div style="margin-top:7px;">${evidenceButton(pattern.evidence_ids)}</div>
        </div>
      `).join("") || `<div class="empty-state">Наблюдаемые паттерны в этой выборке не выделены.</div>`}
    </div>
  `).join("") || `<div class="empty-state">Данных для анализа паттернов пока нет.</div>`;
}

// ---------------------------------------------------------------------------
// Кто подходит дальше: ranked list
// ---------------------------------------------------------------------------
function candidatePositiveReasons(candidate, limit = 2) {
  return (candidate.why || [])
    .filter((factor) => Number(factor.factor_score || 0) > 0)
    .sort((a, b) => Number(b.contribution || 0) - Number(a.contribution || 0))
    .slice(0, limit)
    .map((factor) => FACTOR_LABEL[factor.factor] || humanizeKey(factor.factor));
}

function candidateBadges(candidate) {
  const badges = [];
  if (candidate.has_organic_brand_affinity) {
    badges.push(["Органический интерес", "accent"]);
    badges.push(["Уже упоминает бренд", "accent"]);
  }
  badges.push(["Не использовался брендом", ""]);
  if (Number(candidate.similarity_score || 0) >= 60) badges.push(["Подходит по паттерну", "accent"]);
  return badges;
}

function renderNextMove() {
  const candidates = flattenCandidates().slice(0, 20);
  const slot = document.getElementById("nm-ranking");
  if (!candidates.length) {
    slot.innerHTML = `<div class="card empty-state">Кандидатов в наблюдаемой базе авторов пока нет.</div>`;
    return;
  }

  slot.innerHTML = `
    <p class="matrix-note">Strategy Match показывает совпадение с наблюдаемым профилем бренда, а не вероятность будущей закупки.</p>
    <div class="ranking-list">${candidates.map((candidate, index) => {
      const reasons = candidatePositiveReasons(candidate);
      const badges = candidateBadges(candidate);
      return `
        <div class="candidate-row" data-open-candidate="${escapeHtml(candidate._key)}" tabindex="0" role="button">
          <div class="rank-number">${String(index + 1).padStart(2, "0")}</div>
          <div>
            <div class="candidate-name">${escapeHtml(candidate.candidate)}</div>
            <div class="candidate-meta">${escapeHtml(PLATFORM_LABEL[candidate.platform] || candidate.platform)} · ${formatNumber(candidate.followers)} подписчиков · ${formatNumber(candidate.median_views)} median views</div>
            <div class="candidate-badges">${badges.map(([text, accent]) => `<span class="soft-badge ${accent}">${escapeHtml(text)}</span>`).join("")}</div>
          </div>
          <div class="candidate-reason">${escapeHtml(reasons.length ? `Совпадает по факторам: ${reasons.join(", ")}.` : "Совпадение рассчитано по доступным факторам профиля.")}</div>
          <div class="candidate-match">
            <div class="match-label"><span>Strategy Match</span><strong>${formatNumber(candidate.similarity_score, false)}</strong></div>
            <div class="strategy-track"><div class="strategy-fill" style="width:${Math.max(0, Math.min(100, Number(candidate.similarity_score || 0)))}%"></div></div>
          </div>
        </div>
      `;
    }).join("")}</div>
  `;
}

// ---------------------------------------------------------------------------
// Где меньше конкуренции: dynamic matrix
// ---------------------------------------------------------------------------
function orderedFollowerBuckets(values) {
  const order = ["nano", "micro", "mid", "macro", "unknown"];
  return Array.from(new Set(values)).sort((a, b) => {
    const ai = order.indexOf(a);
    const bi = order.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });
}

function buildMatrixModel(segments) {
  const followerValues = orderedFollowerBuckets(segments.map((segment) => segment.segment.followers_bucket || "unknown"));
  const platformValues = Array.from(new Set(segments.map((segment) => segment.segment.platform || "unknown")));
  const useFollowerColumns = followerValues.length > 1 || platformValues.length <= 1;
  const columns = useFollowerColumns ? followerValues : platformValues;
  const rows = new Map();

  segments.forEach((segment) => {
    const topic = segment.segment.topic ? humanizeKey(segment.segment.topic) : "Тема не определена";
    const platform = PLATFORM_LABEL[segment.segment.platform] || segment.segment.platform || "Площадка не указана";
    const bucket = segment.segment.followers_bucket || "unknown";
    const rowLabel = useFollowerColumns
      ? `${topic} · ${platform}`
      : `${topic} · ${FOLLOWER_BUCKET_LABEL[bucket] || humanizeKey(bucket)}`;
    const columnKey = useFollowerColumns ? bucket : (segment.segment.platform || "unknown");
    if (!rows.has(rowLabel)) rows.set(rowLabel, new Map());
    const existing = rows.get(rowLabel).get(columnKey);
    if (!existing || Number(segment.opportunity_score || 0) > Number(existing.opportunity_score || 0)) {
      rows.get(rowLabel).set(columnKey, segment);
    }
  });

  return {
    columns,
    rows,
    columnLabel: (column) => useFollowerColumns
      ? (FOLLOWER_BUCKET_LABEL[column] || humanizeKey(column))
      : (PLATFORM_LABEL[column] || humanizeKey(column)),
    axisLabel: useFollowerColumns ? "Сегмент / размер автора" : "Сегмент / площадка",
  };
}

function renderWhiteSpace() {
  const segments = (((state.result.white_space || {}).segments) || []);
  const slot = document.getElementById("ws-body");
  if (!segments.length) {
    slot.innerHTML = `<div class="card empty-state">Сегменты в наблюдаемой выборке не сформированы.</div>`;
    return;
  }

  const matrix = buildMatrixModel(segments);
  const strongest = segments
    .filter((segment) => !segment.insufficient_data)
    .sort((a, b) => Number(b.opportunity_score || 0) - Number(a.opportunity_score || 0))[0] || null;
  const templateColumns = `minmax(210px, 1.15fr) repeat(${matrix.columns.length}, minmax(132px, 1fr))`;
  const cells = [];
  cells.push(`<div class="matrix-corner">${escapeHtml(matrix.axisLabel)}</div>`);
  matrix.columns.forEach((column) => cells.push(`<div class="matrix-col-head">${escapeHtml(matrix.columnLabel(column))}</div>`));

  matrix.rows.forEach((rowCells, rowLabel) => {
    cells.push(`<div class="matrix-row-head">${escapeHtml(rowLabel)}</div>`);
    matrix.columns.forEach((column) => {
      const segment = rowCells.get(column);
      if (!segment) {
        cells.push(`<div class="ws-cell empty" aria-hidden="true"></div>`);
        return;
      }
      const key = segmentKey(segment);
      const classes = ["ws-cell"];
      if (strongest && key === segmentKey(strongest)) classes.push("strongest");
      else if (Number(segment.saturation_score || 0) < 34) classes.push("low-saturation");
      const note = segment.insufficient_data ? " · малая выборка" : "";
      cells.push(`
        <button type="button" class="${classes.join(" ")}" data-open-segment="${escapeHtml(key)}">
          <span class="ws-score">${formatNumber(segment.opportunity_score, false)}</span>
          <span class="ws-meta">${formatNumber(segment.available_creators, false)} авторов · насыщенность ${formatNumber(segment.saturation_score, false)}/100${escapeHtml(note)}</span>
        </button>
      `);
    });
  });

  slot.innerHTML = `
    <p class="matrix-note">Матрица показывает относительную возможность и низкую конкурентную насыщенность в наблюдаемой выборке — не утверждение о том, что сегмент свободен.</p>
    <div class="matrix-wrap"><div class="matrix-grid" style="grid-template-columns:${templateColumns};">${cells.join("")}</div></div>
    <div class="matrix-legend">
      <span class="legend-item"><span class="legend-swatch"></span>Наблюдаемая активность</span>
      <span class="legend-item"><span class="legend-swatch low"></span>Низкая насыщенность</span>
      <span class="legend-item"><span class="legend-swatch strong"></span>Сильная возможность</span>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Что стоит проверить: numbered actions
// ---------------------------------------------------------------------------
function displayActionTitle(title) {
  return String(title || "Действие")
    .replace(/^White Space:\s*/i, "Проверить сегмент: ")
    .replace(/^Next Move:\s*/i, "Проверить автора: ");
}

function priorityLabel(priority) {
  return { high: "высокий приоритет", medium: "средний приоритет", low: "низкий приоритет" }[priority] || priority || "приоритет не указан";
}

function renderOurMove() {
  const actions = state.actions.slice(0, 5);
  const slot = document.getElementById("om-cards");
  if (!actions.length) {
    slot.innerHTML = `<div class="empty-state">Конкретные действия в этой выборке не сформированы.</div>`;
    return;
  }
  slot.className = "action-grid";
  slot.innerHTML = actions.map((action, index) => `
    <div class="action-card" data-open-action="${index}" tabindex="0" role="button">
      <div class="action-index">${String(index + 1).padStart(2, "0")}</div>
      <h3>${escapeHtml(displayActionTitle(action.title))}</h3>
      <p>${escapeHtml(action.why_now)}</p>
      <p><strong style="color:var(--text-primary);">Что проверить:</strong> ${escapeHtml(action.suggested_test)}</p>
      <div class="confidence-bar-track"><div class="confidence-bar-fill" style="width:${Math.round(Number(action.confidence || 0) * 100)}%"></div></div>
      <div class="action-footer"><span class="soft-badge">${escapeHtml(priorityLabel(action.priority))}</span><span class="evidence-status">${(action.evidence || []).length ? "есть evidence chain" : "без привязанных источников"}</span></div>
    </div>
  `).join("");
}

// ---------------------------------------------------------------------------
// Drawer content
// ---------------------------------------------------------------------------
function openDrawer({ kicker = "Подробнее", title = "Подробнее", subtitle = "", html = "" }) {
  const overlay = document.getElementById("detail-drawer");
  document.getElementById("drawer-kicker").textContent = kicker;
  document.getElementById("drawer-title").textContent = title;
  document.getElementById("drawer-subtitle").textContent = subtitle;
  document.getElementById("drawer-body").innerHTML = html;
  overlay.classList.add("active");
  overlay.setAttribute("aria-hidden", "false");
  document.body.classList.add("drawer-open");
  document.getElementById("drawer-close").focus({ preventScroll: true });
}

function closeDrawer() {
  const overlay = document.getElementById("detail-drawer");
  if (!overlay) return;
  overlay.classList.remove("active");
  overlay.setAttribute("aria-hidden", "true");
  document.body.classList.remove("drawer-open");
}

function detailMetric(label, value) {
  return `<div class="detail-metric"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(value)}</div></div>`;
}

function detailSection(title, content) {
  if (!content) return "";
  return `<section class="detail-section"><div class="detail-section-title">${escapeHtml(title)}</div>${content}</section>`;
}

function showFindingDrawer(findingId) {
  const finding = state.findings.get(findingId);
  if (!finding) return;
  const metrics = [
    detailMetric("Классификация", classificationLabel(finding.classification)),
    detailMetric("Площадка", PLATFORM_LABEL[finding.platform] || finding.platform),
    detailMetric("Дата", formatDate(finding.published_at)),
    detailMetric("Уверенность", finding.confidence === null || finding.confidence === undefined ? "—" : formatPercent(finding.confidence)),
    detailMetric("Подписчики", formatNumber(finding.followers)),
    detailMetric("Median views", formatNumber(finding.median_views)),
    detailMetric("Средние просмотры", formatNumber(finding.avg_views)),
    detailMetric("Вовлечённость", formatPercent(finding.engagement_rate)),
  ].join("");
  const topicContent = (finding.topic_tags || []).length
    ? `<div class="candidate-badges">${finding.topic_tags.map((topic) => `<span class="soft-badge">${escapeHtml(humanizeKey(topic))}</span>`).join("")}</div>`
    : `<div class="detail-copy">Тема не определена.</div>`;
  const actions = [
    sourceLink(finding.source_url),
    finding.canonical_url && finding.canonical_url !== finding.source_url ? sourceLink(finding.canonical_url, "Открыть профиль ↗", "secondary-btn") : "",
    evidenceButton(finding.evidence_ids, "Показать evidence"),
  ].filter(Boolean).join("");

  openDrawer({
    kicker: classificationLabel(finding.classification),
    title: finding.entity_name,
    subtitle: `${PLATFORM_LABEL[finding.platform] || finding.platform} · ${finding.content_title || domainFromUrl(finding.source_url)}`,
    html: [
      detailSection("Материал", `<div class="detail-copy">${escapeHtml(finding.content_title || "Исходный материал")}</div>`),
      detailSection("Метрики и статус", `<div class="detail-metrics">${metrics}</div>`),
      detailSection("Тема и формат", `${topicContent}<div class="entity-meta" style="margin-top:10px;">Формат: ${escapeHtml(humanizeKey(finding.content_type || "не указан"))}</div>`),
      detailSection("Обнаруженные сигналы", signalChips(finding.detected_signals, 20)),
      detailSection("Действия", `<div class="drawer-actions">${actions}</div>`),
    ].join(""),
  });
}

function showCandidateDrawer(candidateKeyValue) {
  const candidate = state.candidates.get(candidateKeyValue);
  if (!candidate) return;
  const reasons = (candidate.why || [])
    .sort((a, b) => Number(b.contribution || 0) - Number(a.contribution || 0))
    .map((factor) => `
      <div class="detail-list-item"><strong>${escapeHtml(FACTOR_LABEL[factor.factor] || humanizeKey(factor.factor))}</strong><div class="meta">совпадение ${formatPercent(factor.factor_score)} · вклад ${formatNumber(factor.contribution, false)} п.</div></div>
    `).join("");
  const badges = candidateBadges(candidate).map(([text, accent]) => `<span class="soft-badge ${accent}">${escapeHtml(text)}</span>`).join("");
  const metrics = [
    detailMetric("Strategy Match", `${formatNumber(candidate.similarity_score, false)}/100`),
    detailMetric("Площадка", PLATFORM_LABEL[candidate.platform] || candidate.platform),
    detailMetric("Подписчики", formatNumber(candidate.followers)),
    detailMetric("Median views", formatNumber(candidate.median_views)),
    detailMetric("Средние просмотры", formatNumber(candidate.avg_views)),
    detailMetric("Вовлечённость", formatPercent(candidate.engagement_rate)),
  ].join("");
  const actions = [
    sourceLink(candidate.canonical_url, "Открыть профиль ↗"),
    evidenceButton(candidate.evidence_ids, "Показать evidence"),
  ].filter(Boolean).join("");

  openDrawer({
    kicker: "Кандидат",
    title: candidate.candidate,
    subtitle: `Совпадение с наблюдаемым профилем ${candidate.competitor || state.result.brand.canonical_name}`,
    html: [
      detailSection("Статус", `<div class="candidate-badges">${badges}</div>${candidate.note ? `<div class="detail-copy" style="margin-top:12px;">${escapeHtml(candidate.note)}</div>` : ""}`),
      detailSection("Метрики", `<div class="detail-metrics">${metrics}</div>`),
      detailSection("Почему совпадает", `<div class="detail-list">${reasons || `<div class="detail-copy">Факторы совпадения не раскрыты.</div>`}</div>`),
      detailSection("Темы", `<div class="candidate-badges">${(candidate.topics || [candidate.topic]).filter(Boolean).map((topic) => `<span class="soft-badge">${escapeHtml(humanizeKey(topic))}</span>`).join("") || `<span class="entity-meta">не указаны</span>`}</div>`),
      detailSection("Действия", `<div class="drawer-actions">${actions}</div>`),
    ].join(""),
  });
}

function showSegmentDrawer(key) {
  const segment = state.segments.get(key);
  if (!segment) return;
  const metrics = [
    detailMetric("Авторов в сегменте", formatNumber(segment.available_creators, false)),
    detailMetric("Подтверждённых интеграций", formatNumber(segment.competitor_integrations, false)),
    detailMetric("Активных брендов", formatNumber(segment.unique_competitors, false)),
    detailMetric("Насыщенность", `${formatNumber(segment.saturation_score, false)}/100`),
    detailMetric("Возможность", `${formatNumber(segment.opportunity_score, false)}/100`),
    detailMetric("Релевантность", `${formatNumber(segment.our_relevance, false)}/100`),
  ].join("");
  const creators = (segment.top_creators || []).map((creator) => {
    const name = safeExternalUrl(creator.canonical_url)
      ? `<a href="${escapeHtml(safeExternalUrl(creator.canonical_url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(creator.name)}</a>`
      : escapeHtml(creator.name);
    return `<div class="detail-list-item"><strong>${name}</strong><div class="meta">${escapeHtml(PLATFORM_LABEL[creator.platform] || creator.platform || segment.segment.platform)} · ${formatNumber(creator.followers)} подписчиков · ${formatNumber(creator.median_views)} median views · совпадение с сегментом ${formatNumber(creator.segment_match ?? 100, false)}/100${creator.already_used_by_competitor ? " · уже использовался" : ""}</div></div>`;
  }).join("");
  const integrations = (segment.observed_integrations || []).map((integration) => {
    const link = sourceLink(integration.source_url, `${integration.creator || "Материал"} ↗`, "secondary-btn");
    return `<div class="detail-list-item"><strong>${escapeHtml(integration.competitor || "Бренд")}</strong><div class="meta">${escapeHtml(PLATFORM_LABEL[integration.platform] || integration.platform)} · ${formatDate(integration.published_at)} · ${escapeHtml(classificationLabel(integration.classification))}</div><div class="drawer-actions" style="margin-top:9px;">${link}${evidenceButton(integration.evidence_ids, "Evidence")}</div></div>`;
  }).join("");
  const activeBrands = (segment.active_competitors || []).length
    ? segment.active_competitors.map((name) => `<span class="soft-badge">${escapeHtml(name)}</span>`).join("")
    : `<span class="entity-meta">Названия не доступны; наблюдаемое количество: ${formatNumber(segment.unique_competitors, false)}</span>`;

  openDrawer({
    kicker: "Сегмент",
    title: segment.segment.label,
    subtitle: "Низкая конкурентная насыщенность оценивается только внутри наблюдаемой выборки.",
    html: [
      detailSection("Показатели", `<div class="detail-metrics">${metrics}</div>${segment.insufficient_data ? `<div class="insufficient-tag" style="margin-top:12px;">${escapeHtml(segment.insufficient_data_reason || "малая выборка")}</div>` : ""}`),
      detailSection("Активные бренды", `<div class="candidate-badges">${activeBrands}</div>`),
      detailSection("Лучшие авторы в сегменте", `<div class="detail-list">${creators || `<div class="detail-copy">Авторы не доступны.</div>`}</div>`),
      detailSection("Наблюдаемые интеграции", `<div class="detail-list">${integrations || `<div class="detail-copy">Подтверждённых интеграций с URL в этой ячейке нет.</div>`}</div>`),
      detailSection("Evidence", `<div class="drawer-actions">${evidenceButton(segment.evidence_ids, "Показать расчёт")}</div>`),
    ].join(""),
  });
}

function relatedCandidateForAction(action) {
  const names = new Set(action.creators || []);
  return Array.from(state.candidates.values()).find((candidate) => names.has(candidate.candidate)) || null;
}

function relatedSegmentForAction(action) {
  const title = String(action.title || "");
  return Array.from(state.segments.entries()).find(([, segment]) => title.includes(segment.segment.label)) || null;
}

function showActionDrawer(index) {
  const action = state.actions[index];
  if (!action) return;
  const candidate = relatedCandidateForAction(action);
  const segmentEntry = relatedSegmentForAction(action);
  const related = [];
  if (candidate) related.push(`<button type="button" class="secondary-btn" data-open-candidate="${escapeHtml(candidate._key)}">Открыть автора</button>`);
  if (segmentEntry) related.push(`<button type="button" class="secondary-btn" data-open-segment="${escapeHtml(segmentEntry[0])}">Открыть сегмент</button>`);
  if ((action.evidence || []).length) related.push(evidenceButton(action.evidence, "Показать evidence"));

  openDrawer({
    kicker: `Действие ${String(index + 1).padStart(2, "0")}`,
    title: displayActionTitle(action.title),
    subtitle: `${priorityLabel(action.priority)} · уверенность ${formatPercent(action.confidence)}`,
    html: [
      detailSection("Почему сейчас", `<div class="detail-copy">${escapeHtml(action.why_now)}</div>`),
      detailSection("Что проверить", `<div class="detail-copy">${escapeHtml(action.suggested_test)}</div>`),
      (action.creators || []).length ? detailSection("Связанные авторы", `<div class="candidate-badges">${action.creators.map((name) => `<span class="soft-badge">${escapeHtml(name)}</span>`).join("")}</div>`) : "",
      detailSection("Связанные детали", `<div class="drawer-actions">${related.join("") || `<span class="entity-meta">Связанный объект не определён.</span>`}</div>`),
    ].join(""),
  });
}

// ---------------------------------------------------------------------------
// Analysis-scoped evidence
// ---------------------------------------------------------------------------
function formatEvidenceValue(evidence) {
  const value = evidence.value;
  if (typeof value === "boolean") return value ? "да" : "нет";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (value && typeof value === "object") {
    return Object.entries(value).map(([key, item]) => `${humanizeKey(key)}: ${typeof item === "object" ? JSON.stringify(item) : item}`).join("; ");
  }
  return value === null || value === undefined ? "—" : String(value);
}

function evidenceItemHtml(evidence) {
  return `
    <div class="evidence-item">
      <span class="evidence-type-tag ${escapeHtml(evidence.type)}">${escapeHtml(EVIDENCE_TYPE_LABEL[evidence.type] || evidence.type)}</span>
      <div class="ev-text"><strong>${escapeHtml(humanizeKey(evidence.field))}:</strong> ${escapeHtml(formatEvidenceValue(evidence))}</div>
      ${evidence.raw_fragment ? `<div class="ev-meta">${escapeHtml(evidence.raw_fragment)}</div>` : ""}
      ${evidence.observed_at ? `<div class="ev-meta">Наблюдалось: ${escapeHtml(formatDate(evidence.observed_at))}</div>` : ""}
      ${evidence.source_url ? `<div class="ev-meta">${sourceLink(evidence.source_url, "Открыть источник ↗", "text-btn")}</div>` : ""}
    </div>
  `;
}

async function showEvidence(ids) {
  const cleanIds = Array.from(new Set((ids || []).filter(Boolean)));
  if (!cleanIds.length) {
    openDrawer({ kicker: "Evidence", title: "Источники", subtitle: "", html: `<div class="empty-state">Источники не привязаны к этому выводу.</div>` });
    return;
  }
  const analysisId = state.result && state.result.analysis_id;
  if (!analysisId) {
    openDrawer({ kicker: "Evidence", title: "Источники", subtitle: "", html: `<div class="empty-state">analysis_id отсутствует.</div>` });
    return;
  }

  openDrawer({
    kicker: "Evidence",
    title: "Источники и расчёты",
    subtitle: `${cleanIds.length} ${pluralRu(cleanIds.length, ["элемент", "элемента", "элементов"])}`,
    html: `<div class="loading-state"><span class="loading-spinner"></span>Загружаю evidence chain…</div>`,
  });

  const items = (await Promise.all(cleanIds.map((evidenceId) => {
    const url = `/api/analysis/${encodeURIComponent(analysisId)}/evidence/${encodeURIComponent(evidenceId)}`;
    return fetchJson(url).catch(() => null);
  }))).filter(Boolean);

  const body = document.getElementById("drawer-body");
  if (!items.length) {
    body.innerHTML = `<div class="empty-state">Evidence не найдено внутри этого анализа.</div>`;
    return;
  }
  const byType = {};
  items.forEach((evidence) => {
    if (!byType[evidence.type]) byType[evidence.type] = [];
    byType[evidence.type].push(evidence);
  });
  const known = EVIDENCE_SECTION_ORDER.filter((type) => byType[type]);
  const other = Object.keys(byType).filter((type) => !EVIDENCE_SECTION_ORDER.includes(type));
  body.innerHTML = [...known, ...other].map((type) => `
    <div class="evidence-section"><div class="evidence-section-title">${escapeHtml(EVIDENCE_TYPE_LABEL[type] || type)}</div>${byType[type].map(evidenceItemHtml).join("")}</div>
  `).join("");
}

// ---------------------------------------------------------------------------
// Delegated interactions
// ---------------------------------------------------------------------------
document.addEventListener("click", (event) => {
  const evidenceTarget = event.target.closest("[data-evidence-ids]");
  if (evidenceTarget) {
    event.preventDefault();
    event.stopPropagation();
    try {
      showEvidence(JSON.parse(decodeURIComponent(evidenceTarget.dataset.evidenceIds)));
    } catch (_err) {
      showEvidence([]);
    }
    return;
  }

  const findingTarget = event.target.closest("[data-open-finding]");
  if (findingTarget) {
    if (event.target.closest("a")) return;
    event.preventDefault();
    showFindingDrawer(findingTarget.dataset.openFinding);
    return;
  }

  const findingRow = event.target.closest(".finding-row[data-finding-id]");
  if (findingRow && !event.target.closest("a, button")) {
    showFindingDrawer(findingRow.dataset.findingId);
    return;
  }

  const candidateTarget = event.target.closest("[data-open-candidate]");
  if (candidateTarget) {
    event.preventDefault();
    showCandidateDrawer(candidateTarget.dataset.openCandidate);
    return;
  }

  const segmentTarget = event.target.closest("[data-open-segment]");
  if (segmentTarget) {
    event.preventDefault();
    showSegmentDrawer(segmentTarget.dataset.openSegment);
    return;
  }

  const actionTarget = event.target.closest("[data-open-action]");
  if (actionTarget) {
    event.preventDefault();
    showActionDrawer(Number(actionTarget.dataset.openAction));
    return;
  }

  if (event.target.closest("#drawer-close")) {
    closeDrawer();
    return;
  }

  const overlay = document.getElementById("detail-drawer");
  if (event.target === overlay) closeDrawer();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
  if ((event.key === "Enter" || event.key === " ") && event.target.matches("[role='button']:not(button)")) {
    event.preventDefault();
    event.target.click();
  }
});

// ---------------------------------------------------------------------------
// Result navigation
// ---------------------------------------------------------------------------
const TITLES = {
  overview: "Обзор",
  "market-map": "Что нашли",
  "competitor-dna": "Как бренд выбирает",
  "next-move": "Кто подходит дальше",
  "white-space": "Где меньше конкуренции",
  "our-move": "Что стоит проверить",
};
const SUBTITLES = {
  overview: "Итог по наблюдаемой выборке.",
  "market-map": "Публичные материалы, сигналы и классификация с переходом к источнику.",
  "competitor-dna": "Паттерны, которые видны в наблюдаемых размещениях.",
  "next-move": "Авторы, совпадающие с наблюдаемым профилем бренда.",
  "white-space": "Сегменты с большим выбором авторов и низкой насыщенностью в наблюдаемой выборке.",
  "our-move": "Несколько конкретных действий для проверки.",
};

document.getElementById("side-nav").addEventListener("click", (event) => {
  const button = event.target.closest(".nav-item");
  if (button) goToSection(button.dataset.section);
});

function goToSection(key) {
  document.querySelectorAll("#side-nav .nav-item").forEach((element) => element.classList.toggle("active", element.dataset.section === key));
  document.querySelectorAll("#view-results .section").forEach((element) => element.classList.remove("active"));
  const section = document.getElementById(`section-${key}`);
  if (section) section.classList.add("active");
  document.getElementById("page-title").textContent = TITLES[key] || "Обзор";
  document.getElementById("page-subtitle").textContent = SUBTITLES[key] || "";
}

document.getElementById("page-subtitle").textContent = SUBTITLES.overview;
