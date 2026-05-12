import pytest
import tempfile
import os

@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Redirect decision and report files to a temp dir for each test."""
    import trading_bot.webhook as wh
    monkeypatch.setattr(wh, 'DECISIONS_FILE', str(tmp_path / 'decisions.json'))
    monkeypatch.setattr(wh, 'REPORTS_FILE',   str(tmp_path / 'reports.json'))
