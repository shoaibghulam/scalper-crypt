"""Test isolation for state that lives outside the process.

The engine now persists its bot registry, and the default location is the real
one under the project root. Without this fixture a test that constructs an
Engine reads the file a running engine wrote, so the suite passes or fails
depending on what is open on the exchange at the time. That is not a test.
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_state_file(tmp_path, monkeypatch):
    """Point every test at a state file of its own.

    Autouse rather than opt-in: the failure mode is a test silently reading
    live trading state, which is the kind of thing that has to be off by
    default rather than remembered.
    """
    monkeypatch.setenv("SCALPER_STATE_FILE", str(tmp_path / "engine-state.json"))
    monkeypatch.setattr("scalper.exchange.market_limits.COOLDOWN_DIR", tmp_path / "cooldowns")
    # Clock offsets and rate-limit state are shared per REST origin so the
    # engine stops re-syncing on every client it builds. That is right in a
    # long-lived process and wrong across tests, where one case's synced clock
    # would silently satisfy the next case's assertion.
    monkeypatch.setattr("scalper.exchange.client.CLOCKS", {})
    monkeypatch.setattr("scalper.exchange.market_limits.LIMITS", {})
