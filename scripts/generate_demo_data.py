"""
Генератор детерминированного demo/synthetic датасета.

Датасет специально сконструирован так, чтобы аналитический pipeline
(Market Map -> Competitor DNA -> Next Move -> White Space -> Our Move)
гарантированно находил:
  - >= 4 конкурентов
  - >= 80 креаторов
  - >= 150 интеграций
  - несколько сегментов
  - минимум один перегретый (oversaturated) сегмент
  - минимум один явный white space
  - минимум один явный recent strategic shift у конкурента
  - несколько next-move кандидатов на конкурента

Все данные помечены is_synthetic=True. Запуск:
    python scripts/generate_demo_data.py
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import DEMO_DATA_DIR  # noqa: E402

RNG = random.Random(42)
NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)  # "today" для демо, фиксировано для воспроизводимости

TOPICS = [
    "fitness", "gaming", "medical_students", "beauty",
    "personal_finance", "coding", "cooking", "travel",
]
PLATFORMS = ["youtube", "telegram", "instagram"]
CONTENT_TYPES = ["review", "integration_ad", "unboxing", "tutorial", "vlog_mention", "live_stream"]
OFFERS = ["discount_code", "free_trial", "giveaway", "bundle_offer", "referral_bonus"]
MECHANICS = ["dedicated_video", "pre_roll", "story_mention", "pinned_post", "live_mention"]
CTAS = {
    "discount_code": "use code at checkout",
    "free_trial": "sign up via link",
    "giveaway": "join giveaway",
    "bundle_offer": "link in bio",
    "referral_bonus": "invite friends via link",
}
GEOS = ["RU", "EU", "US", "LATAM"]
LANGS = {"RU": "ru", "EU": "en", "US": "en", "LATAM": "es"}

FOLLOWER_RANGES = {
    "nano": (500, 9_900),
    "micro": (10_000, 49_000),
    "mid": (50_000, 199_000),
    "macro": (200_000, 2_000_000),
}


def sample_followers(bucket: str) -> int:
    lo, hi = FOLLOWER_RANGES[bucket]
    return RNG.randint(lo, hi)


def views_for(followers: int) -> tuple[float, float, float]:
    er = round(RNG.uniform(0.015, 0.09), 4)
    avg_views = round(followers * RNG.uniform(0.08, 0.35), 1)
    median_views = round(avg_views * RNG.uniform(0.75, 0.95), 1)
    return avg_views, median_views, er


class IdGen:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"{self.prefix}_{self.n:04d}"


creator_ids = IdGen("cr")
integration_ids = IdGen("int")

creators: list[dict] = []
integrations: list[dict] = []


def make_creator(topic: str, platform: str, bucket: str, geo: str | None = None,
                  name_hint: str | None = None) -> dict:
    geo = geo or RNG.choice(GEOS)
    followers = sample_followers(bucket)
    avg_views, median_views, er = views_for(followers)
    cid = creator_ids.next()
    created_days_ago = RNG.randint(200, 900)
    last_seen_days_ago = RNG.randint(0, 20)
    name = name_hint or f"{topic.replace('_', ' ').title()} Creator {cid.split('_')[1]}"
    creator = {
        "creator_id": cid,
        "name": name,
        "canonical_url": f"https://{platform}.example/{cid}",
        "platform": platform,
        "followers": followers,
        "avg_views": avg_views,
        "median_views": median_views,
        "engagement_rate": er,
        "topic_tags": [topic],
        "audience_tags": [f"{topic}_audience"],
        "geo": geo,
        "language": LANGS.get(geo, "en"),
        "created_at": (NOW - timedelta(days=created_days_ago)).isoformat(),
        "last_seen_at": (NOW - timedelta(days=last_seen_days_ago)).isoformat(),
        "source_refs": [f"https://{platform}.example/{cid}"],
        "is_synthetic": True,
    }
    creators.append(creator)
    return creator


def make_integration(competitor_id: str, creator: dict, days_ago: int,
                      content_type: str | None = None, offer: str | None = None,
                      mechanic: str | None = None) -> dict:
    content_type = content_type or RNG.choice(CONTENT_TYPES)
    offer = offer or RNG.choice(OFFERS)
    mechanic = mechanic or RNG.choice(MECHANICS)
    published_at = NOW - timedelta(days=days_ago, hours=RNG.randint(0, 23))
    iid = integration_ids.next()
    integration = {
        "integration_id": iid,
        "competitor_id": competitor_id,
        "creator_id": creator["creator_id"],
        "platform": creator["platform"],
        "content_url": f"https://{creator['platform']}.example/{creator['creator_id']}/{iid}",
        "published_at": published_at.isoformat(),
        "content_type": content_type,
        "detected_offer": offer,
        "detected_cta": CTAS.get(offer, "link in bio"),
        "detected_mechanic": mechanic,
        "campaign_tags": [creator["topic_tags"][0], offer],
        "raw_text": (
            f"[SYNTHETIC DEMO DATA] {creator['name']} x {competitor_id}: "
            f"{content_type} c офером {offer} ({mechanic})."
        ),
        "evidence": [],
        "is_synthetic": True,
    }
    integrations.append(integration)
    return integration


# ---------------------------------------------------------------------------
# 1. Конкуренты
# ---------------------------------------------------------------------------
competitors = [
    {
        "competitor_id": "comp_novafit",
        "name": "NovaFit Media",
        "aliases": ["NovaFit", "Nova Fit Media"],
        "sources": ["https://novafit.example/press"],
    },
    {
        "competitor_id": "comp_pulsemedia",
        "name": "PulseMedia Agency",
        "aliases": ["PulseMedia"],
        "sources": ["https://pulsemedia.example"],
    },
    {
        "competitor_id": "comp_growthlabs",
        "name": "GrowthLabs Partners",
        "aliases": ["GrowthLabs"],
        "sources": ["https://growthlabs.example"],
    },
    {
        "competitor_id": "comp_peakpromo",
        "name": "PeakPromo Network",
        "aliases": ["PeakPromo"],
        "sources": ["https://peakpromo.example"],
    },
]

RECENT_DAYS = 30
HISTORICAL_DAYS = 90  # окно ПЕРЕД recent (т.е. дни 31..120)


def recent_day() -> int:
    return RNG.randint(0, RECENT_DAYS - 1)


def historical_day() -> int:
    return RNG.randint(RECENT_DAYS, RECENT_DAYS + HISTORICAL_DAYS - 1)


# ---------------------------------------------------------------------------
# 2. Базовый пул креаторов по всем topic x platform комбинациям
#    (обеспечивает разнообразие сегментов и >= 80 креаторов)
# ---------------------------------------------------------------------------
segment_pool: dict[tuple[str, str], list[dict]] = {}

for topic in TOPICS:
    for platform in PLATFORMS:
        pool: list[dict] = []
        # 3-5 креаторов на комбинацию, бакет по умолчанию - смешанный
        count = RNG.randint(3, 5)
        for _ in range(count):
            bucket = RNG.choices(
                ["nano", "micro", "mid", "macro"], weights=[0.35, 0.35, 0.22, 0.08]
            )[0]
            c = make_creator(topic, platform, bucket)
            pool.append(c)
        segment_pool[(topic, platform)] = pool


# ---------------------------------------------------------------------------
# 3. Явно перегретый сегмент: fitness / youtube / mid
#    Много креаторов + все конкуренты активно там закупаются.
# ---------------------------------------------------------------------------
oversaturated_creators = [make_creator("fitness", "youtube", "mid") for _ in range(10)]
segment_pool[("fitness", "youtube")].extend(oversaturated_creators)

for comp in competitors:
    used = RNG.sample(oversaturated_creators, k=7)
    for creator in used:
        # несколько интеграций на конкурента в этом сегменте, вкл. повторные размещения
        for _ in range(RNG.randint(1, 3)):
            make_integration(comp["competitor_id"], creator, days_ago=historical_day())


# ---------------------------------------------------------------------------
# 4. Явный White Space: medical_students / telegram / nano
#    Много доступных креаторов, но почти нет конкурентной активности,
#    и сегмент совпадает с нашим our_profile.
# ---------------------------------------------------------------------------
white_space_creators = [
    make_creator("medical_students", "telegram", "nano", geo="RU") for _ in range(28)
]
segment_pool[("medical_students", "telegram")].extend(white_space_creators)

# Только GrowthLabs слегка попробовал сегмент - 3 интеграции с 2 креаторами.
growthlabs_id = "comp_growthlabs"
touched = RNG.sample(white_space_creators, k=2)
make_integration(growthlabs_id, touched[0], days_ago=historical_day())
make_integration(growthlabs_id, touched[1], days_ago=historical_day())
make_integration(growthlabs_id, touched[0], days_ago=historical_day())


# ---------------------------------------------------------------------------
# 5. Recent strategic shift: NovaFit исторически fitness/youtube/macro,
#    но за последние 30 дней резко сместился в coding/telegram/micro.
# ---------------------------------------------------------------------------
novafit_id = "comp_novafit"

novafit_macro_fitness = [make_creator("fitness", "youtube", "macro") for _ in range(6)]
segment_pool[("fitness", "youtube")].extend(novafit_macro_fitness)
for creator in novafit_macro_fitness:
    make_integration(
        novafit_id, creator, days_ago=historical_day(),
        content_type="dedicated_video" if False else "review",
        mechanic="dedicated_video",
    )
    make_integration(novafit_id, creator, days_ago=historical_day(), mechanic="dedicated_video")

novafit_micro_coding = [make_creator("coding", "telegram", "micro") for _ in range(9)]
segment_pool[("coding", "telegram")].extend(novafit_micro_coding)
for creator in novafit_micro_coding:
    make_integration(
        novafit_id, creator, days_ago=recent_day(),
        content_type="tutorial", mechanic="pinned_post", offer="referral_bonus",
    )
    make_integration(novafit_id, creator, days_ago=recent_day(), mechanic="pinned_post")


# ---------------------------------------------------------------------------
# 6. Стабильные профили остальных конкурентов (без резкого шифта)
#    - PulseMedia: gaming + instagram, mid/macro, стабильно recent и historical
#    - PeakPromo: beauty + travel, instagram, micro/mid
# ---------------------------------------------------------------------------
pulsemedia_id = "comp_pulsemedia"
pulse_creators = [make_creator("gaming", "instagram", RNG.choice(["mid", "macro"])) for _ in range(10)]
segment_pool[("gaming", "instagram")].extend(pulse_creators)
for creator in pulse_creators:
    make_integration(pulsemedia_id, creator, days_ago=historical_day(), mechanic="story_mention")
    if RNG.random() < 0.6:
        make_integration(pulsemedia_id, creator, days_ago=recent_day(), mechanic="story_mention")

peakpromo_id = "comp_peakpromo"
peak_beauty = [make_creator("beauty", "instagram", RNG.choice(["micro", "mid"])) for _ in range(6)]
peak_travel = [make_creator("travel", "instagram", RNG.choice(["micro", "mid"])) for _ in range(5)]
segment_pool[("beauty", "instagram")].extend(peak_beauty)
segment_pool[("travel", "instagram")].extend(peak_travel)
for creator in peak_beauty + peak_travel:
    make_integration(peakpromo_id, creator, days_ago=historical_day())
    if RNG.random() < 0.5:
        make_integration(peakpromo_id, creator, days_ago=recent_day())

# GrowthLabs основной профиль: personal_finance + youtube, mid/macro
gl_finance = [make_creator("personal_finance", "youtube", RNG.choice(["mid", "macro"])) for _ in range(9)]
segment_pool[("personal_finance", "youtube")].extend(gl_finance)
for creator in gl_finance:
    make_integration(growthlabs_id, creator, days_ago=historical_day(), mechanic="dedicated_video")
    if RNG.random() < 0.7:
        make_integration(growthlabs_id, creator, days_ago=recent_day(), mechanic="dedicated_video")


# ---------------------------------------------------------------------------
# 7. Дополнительный "фоновый шум" интеграций по остальным сегментам,
#    чтобы рынок выглядел реалистично разнообразным (без доминирующей логики).
# ---------------------------------------------------------------------------
background_pairs = [
    ("cooking", "youtube"), ("cooking", "instagram"), ("travel", "youtube"),
    ("beauty", "youtube"), ("gaming", "telegram"), ("personal_finance", "telegram"),
    ("coding", "youtube"), ("fitness", "telegram"), ("fitness", "instagram"),
]
for topic, platform in background_pairs:
    pool = segment_pool.get((topic, platform), [])
    if not pool:
        continue
    n_competitors = RNG.choices([1, 2, 3], weights=[0.55, 0.3, 0.15])[0]
    chosen_competitors = RNG.sample(competitors, k=min(n_competitors, len(competitors)))
    for comp in chosen_competitors:
        sample_n = min(len(pool), RNG.randint(1, 4))
        for creator in RNG.sample(pool, k=sample_n):
            for _ in range(RNG.choices([1, 2], weights=[0.75, 0.25])[0]):
                make_integration(comp["competitor_id"], creator, days_ago=historical_day())


# ---------------------------------------------------------------------------
# 8. our_profile.json - наш профиль закупки для White Space / relevance
# ---------------------------------------------------------------------------
our_profile = {
    "preferred_topics": ["medical_students", "coding", "personal_finance"],
    "platforms": ["telegram", "youtube"],
    "creator_size": ["nano", "micro"],
    "geo": ["RU", "EU"],
    "minimum_views": 300,
    "excluded_topics": ["gaming"],
}

# ---------------------------------------------------------------------------
# Валидация обязательных свойств датасета (fail fast, если что-то не так)
# ---------------------------------------------------------------------------
assert len(competitors) >= 4, "нужно >= 4 конкурентов"
assert len(creators) >= 80, f"нужно >= 80 креаторов, получили {len(creators)}"
assert len(integrations) >= 150, f"нужно >= 150 интеграций, получили {len(integrations)}"
assert len(segment_pool) >= 5, "нужно несколько сегментов"

fitness_yt_integrations = [
    i for i in integrations
    if any(c["creator_id"] == i["creator_id"] and c["topic_tags"] == ["fitness"] for c in oversaturated_creators + novafit_macro_fitness)
]
assert len(fitness_yt_integrations) >= 20, "перегретый сегмент fitness/youtube должен иметь много интеграций"

ws_integrations = [i for i in integrations if i["creator_id"] in {c["creator_id"] for c in white_space_creators}]
assert 1 <= len({i["competitor_id"] for i in ws_integrations}) <= 2, "white space должен иметь низкую насыщенность"
assert len(white_space_creators) - len({i["creator_id"] for i in ws_integrations}) >= 20, (
    "white space должен иметь много неиспользованных креаторов для next-move"
)

print(f"OK: {len(competitors)} конкурентов, {len(creators)} креаторов, {len(integrations)} интеграций, "
      f"{len(segment_pool)} сегментов")

DEMO_DATA_DIR.mkdir(parents=True, exist_ok=True)
(DEMO_DATA_DIR / "competitors.json").write_text(json.dumps(competitors, ensure_ascii=False, indent=2), encoding="utf-8")
(DEMO_DATA_DIR / "creators.json").write_text(json.dumps(creators, ensure_ascii=False, indent=2), encoding="utf-8")
(DEMO_DATA_DIR / "integrations.json").write_text(json.dumps(integrations, ensure_ascii=False, indent=2), encoding="utf-8")
(DEMO_DATA_DIR / "our_profile.json").write_text(json.dumps(our_profile, ensure_ascii=False, indent=2), encoding="utf-8")

print("Демо-датасет сохранён в", DEMO_DATA_DIR)
