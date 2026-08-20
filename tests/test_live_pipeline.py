from __future__ import annotations

from app.models import SourceMode

CSV_CONTENT = """competitor,creator,platform,content_url,published_at,followers,views,topic,raw_text,offer,cta,mechanic
Автор24,Студент Blog,telegram,https://t.me/studentblog/123,2026-07-01,15000,3000,medical_students,Пост про Автор24,discount_code,ссылка в описании,pinned_post
Автор24,Med Vlogger,youtube,https://youtube.com/watch?v=abc,2026-07-15,80000,15000,medical_students,Видео про Автор24,free_trial,подпишись,dedicated_video
Rival Co,Другой Креатор,telegram,https://t.me/other/1,2026-06-01,9000,2000,coding,Пост про Rival Co,giveaway,see link,mention
"""


def test_imported_data_flows_through_full_analytical_pipeline(tmp_path, monkeypatch):
    import app.live_pipeline as live_pipeline_module

    live_db = tmp_path / "live_test.sqlite3"
    monkeypatch.setattr(live_pipeline_module, "LIVE_STATE_DB_PATH", live_db)

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(CSV_CONTENT, encoding="utf-8")

    import_report = live_pipeline_module.import_integrations_file(str(csv_path))
    assert import_report.rows_imported == 3

    result = live_pipeline_module.run_live_analytics(persist=False)

    # Пайплайн не падает и проходит все 5 слоёв на live/imported данных.
    assert result["overview"]["is_synthetic_data"] is False
    assert "imported" in result["overview"]["source_modes_present"]
    assert result["overview"]["integrations_analyzed"] == 3
    assert result["overview"]["competitors_analyzed"] == 2

    assert result["market_map"]["competitors"]
    assert isinstance(result["competitor_dna"], list) and result["competitor_dna"]
    assert isinstance(result["next_moves"], list)
    assert "segments" in result["white_space"]
    assert "opportunities" in result["our_move"]


def test_live_storage_is_isolated_from_demo_storage(tmp_path, monkeypatch):
    """source_mode-разделение: live/imported данные никогда не попадают в demo pipeline и наоборот."""
    import app.live_pipeline as live_pipeline_module

    live_db = tmp_path / "live_test2.sqlite3"
    monkeypatch.setattr(live_pipeline_module, "LIVE_STATE_DB_PATH", live_db)

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(CSV_CONTENT, encoding="utf-8")
    live_pipeline_module.import_integrations_file(str(csv_path))

    live_storage = live_pipeline_module.get_live_storage()
    for creator in live_storage.list_creators():
        assert creator.source_mode in (SourceMode.LIVE, SourceMode.IMPORTED)

    # Demo pipeline использует отдельную БД (output/state.sqlite3) - live/imported
    # объекты физически не могут туда попасть иначе как через явный import.
    from app.pipeline import run_pipeline
    demo_result = run_pipeline(mode="demo", persist=False)
    assert demo_result["overview"]["is_synthetic_data"] is True
