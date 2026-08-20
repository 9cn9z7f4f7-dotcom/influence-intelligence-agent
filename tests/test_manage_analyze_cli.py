from __future__ import annotations

from click.testing import CliRunner

from manage import cli


def test_analyze_cli_runs_end_to_end_without_crashing(tmp_path, monkeypatch):
    import app.analysis.store as store_module
    monkeypatch.setattr(store_module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(store_module, "ANALYSIS_OUTPUT_DIR", tmp_path / "analyses")

    runner = CliRunner()
    result = runner.invoke(cli, ["analyze", "--brand", "Автор24", "--platform", "youtube"])

    assert result.exit_code == 0, result.output
    assert "ANALYZE DONE" in result.output
    assert "analysis_id" in result.output
    assert "integrations_found" in result.output


def test_analyze_cli_supports_multiple_platforms_and_confirmed_only(monkeypatch, tmp_path):
    import app.analysis.store as store_module
    monkeypatch.setattr(store_module, "ANALYSIS_OUTPUT_DIR", tmp_path / "analyses")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "analyze", "--brand", "Автор24", "--platform", "youtube", "--platform", "instagram",
        "--confirmed-only", "--date-range", "30d",
    ])
    assert result.exit_code == 0, result.output
    assert "platform youtube" in result.output
    assert "platform instagram" in result.output
