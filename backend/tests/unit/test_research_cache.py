from scalper.api import research_cache


def test_inventory_reports_retained_files_without_claiming_gap_free_history(tmp_path, monkeypatch):
    monkeypatch.setattr(research_cache, 'CACHE', tmp_path)
    (tmp_path / 'BTCUSDT-60000-120000.json').write_text('[]')
    (tmp_path / 'BTCUSDT-180000-240000.json').write_text('[]')
    (tmp_path / 'ETHUSDT-60000-120000.json').write_text('[]')
    (tmp_path / 'unrelated.json').write_text('ignore')
    result = research_cache.inventory()
    assert result['files'] == 3
    assert result['bytes'] == 6
    assert result['coins'][0] == {'symbol': 'BTCUSDT', 'files': 2, 'bytes': 4, 'first': 60000, 'last': 240000}
    assert 'gaps' in result['retention']
    assert len(list(tmp_path.glob('*.json'))) == 4
