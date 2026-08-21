from app.analysis.models import AnalysisConfig


def test_search_level_targets():
    light = AnalysisConfig(search_level='light')
    standard = AnalysisConfig(search_level='standard')
    deep = AnalysisConfig(search_level='deep')
    assert (light.sample_target(), light.hunting_target(), light.discovery_pool_target()) == (30, 15, 60)
    assert (standard.sample_target(), standard.hunting_target(), standard.discovery_pool_target()) == (60, 25, 120)
    assert (deep.sample_target(), deep.hunting_target(), deep.discovery_pool_target()) == (100, 40, 180)


def test_frontend_does_not_hard_cap_hunting_list_at_20():
    from pathlib import Path
    source = Path('static/analyze.js').read_text(encoding='utf-8')
    assert 'candidates.slice(0, 20)' not in source
