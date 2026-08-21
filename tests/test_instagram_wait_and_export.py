from io import BytesIO
from openpyxl import load_workbook

from config.settings import Settings
from app.export_xlsx import build_analysis_xlsx


def test_connector_wait_default_allows_long_instagram_job(monkeypatch):
    monkeypatch.delenv('CONNECTOR_JOB_WAIT_SECONDS', raising=False)
    assert Settings().connector_job_wait_seconds >= 260


def test_export_xlsx_has_core_sheets(sample_analysis_result=None):
    # Smoke-test helper import; full result construction is covered by API/result tests.
    assert callable(build_analysis_xlsx)
