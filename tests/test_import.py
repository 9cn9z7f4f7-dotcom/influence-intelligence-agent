from __future__ import annotations

from app.ingestion.import_adapter import import_integrations
from app.models import SourceMode


CSV_CONTENT = """competitor,creator,platform,content_url,published_at,followers,views,topic,raw_text,offer,cta,mechanic
Автор24,Студент Blog,telegram,https://t.me/studentblog/123,2026-07-01,15000,3000,medical_students,Пост про Автор24 с промокодом,discount_code,ссылка в описании,pinned_post
Автор24,Студент Blog,telegram,https://t.me/studentblog/123,2026-07-01,15000,3000,medical_students,Пост про Автор24 с промокодом,discount_code,ссылка в описании,pinned_post
BadRow,,telegram,,,,,,,,,
"""


def test_csv_import_creates_normalized_objects(tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(CSV_CONTENT, encoding="utf-8")

    report = import_integrations(csv_path)

    assert report.rows_total == 3
    assert report.rows_imported == 2
    assert report.rows_failed == 1
    assert "отсутствуют обязательные колонки" in report.errors[0]

    assert len(report.competitors) == 1
    assert report.competitors[0].name == "Автор24"
    assert report.competitors[0].source_mode == SourceMode.IMPORTED

    assert len(report.creators) == 1
    creator = report.creators[0]
    assert creator.name == "Студент Blog"
    assert creator.platform == "telegram"
    assert creator.followers == 15000
    assert creator.avg_views == 3000.0
    assert creator.source_mode == SourceMode.IMPORTED

    assert len(report.integrations) == 2
    integration = report.integrations[0]
    assert integration.detected_offer == "discount_code"
    assert integration.source_mode == SourceMode.IMPORTED
    assert integration.confidence == 1.0
    assert integration.evidence


def test_csv_import_dedup_key_stable_across_duplicate_rows(tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(CSV_CONTENT, encoding="utf-8")
    report = import_integrations(csv_path)
    ids = {i.integration_id for i in report.integrations}
    assert len(ids) == 1  # обе валидные строки - дубликаты одной интеграции


def test_missing_file_does_not_crash():
    report = import_integrations("/tmp/this_file_does_not_exist_xyz.csv")
    assert report.rows_total == 0
    assert report.errors


def test_json_import_supported(tmp_path):
    import json

    json_path = tmp_path / "sample.json"
    json_path.write_text(json.dumps([
        {"competitor": "Comp X", "creator": "Insta Creator", "platform": "instagram",
         "content_url": "https://instagram.com/p/abc", "followers": "5000", "views": "1200",
         "topic": "beauty", "raw_text": "reklama post", "offer": "giveaway"},
    ], ensure_ascii=False), encoding="utf-8")

    report = import_integrations(json_path)
    assert report.rows_imported == 1
    assert report.creators[0].platform == "instagram"
