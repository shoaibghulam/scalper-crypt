"""The trust layer between the API and the engine.

This is the state machine behind the dot in the status strip, and its job is to
never overstate what it knows. Three failure modes it must distinguish:

The engine has not been reached yet, which is not the same as the engine being
down. A socket that is open but silent, which looks identical to a healthy feed
in a quiet market unless age is tracked. And a clean disconnect.

Reporting healthy because nothing has explicitly failed is the specific bug
this replaces: the dashboard previously showed the engine as not connected
while it was running perfectly, because nothing ever told it otherwise.
"""

import pytest

from scalper.api.engine_link import STALE_AFTER_S, EngineLink, LinkState


class TestInitialState:
    def test_starts_unknown_not_down(self):
        # Before the first attempt we genuinely do not know.
        assert EngineLink().state(now=0.0) is LinkState.UNKNOWN

    def test_reports_not_connected_initially(self):
        assert EngineLink().connected is False

    def test_has_no_sequence_yet(self):
        assert EngineLink().seq == 0


class TestConnected:
    def test_a_snapshot_marks_it_live(self):
        link = EngineLink()
        link.on_snapshot({"seq": 1}, now=100.0)
        assert link.state(now=100.0) is LinkState.LIVE
        assert link.connected is True

    def test_the_sequence_is_tracked(self):
        link = EngineLink()
        link.on_snapshot({"seq": 7}, now=100.0)
        assert link.seq == 7

    def test_payload_is_retained_for_the_dashboard(self):
        link = EngineLink()
        link.on_snapshot({"seq": 1, "positions": [{"id": "a"}]}, now=100.0)
        assert link.payload["positions"] == [{"id": "a"}]


class TestStaleness:
    def test_a_silent_socket_goes_stale(self):
        # The dangerous case: connected, no error, and no data. Without an age
        # check this is indistinguishable from a quiet market.
        link = EngineLink()
        link.on_snapshot({"seq": 1}, now=100.0)
        assert link.state(now=100.0 + STALE_AFTER_S + 1) is LinkState.STALE

    def test_it_recovers_when_data_resumes(self):
        link = EngineLink()
        link.on_snapshot({"seq": 1}, now=100.0)
        assert link.state(now=200.0) is LinkState.STALE
        link.on_snapshot({"seq": 2}, now=200.0)
        assert link.state(now=200.0) is LinkState.LIVE

    def test_stale_still_counts_as_connected_but_untrusted(self):
        link = EngineLink()
        link.on_snapshot({"seq": 1}, now=100.0)
        assert link.connected is True
        assert link.is_stale(now=100.0 + STALE_AFTER_S + 1) is True

    def test_age_is_reported_so_the_ui_can_show_it(self):
        link = EngineLink()
        link.on_snapshot({"seq": 1}, now=100.0)
        assert link.age_seconds(now=105.0) == pytest.approx(5.0)


class TestDisconnect:
    def test_an_explicit_disconnect_is_down(self):
        link = EngineLink()
        link.on_snapshot({"seq": 1}, now=100.0)
        link.on_disconnect(now=101.0)
        assert link.state(now=101.0) is LinkState.DOWN
        assert link.connected is False

    def test_the_last_known_payload_survives_a_disconnect(self):
        # Showing the last value with its age beats blanking the screen, as
        # long as the interface is honest that it is old.
        link = EngineLink()
        link.on_snapshot({"seq": 1, "positions": [{"id": "a"}]}, now=100.0)
        link.on_disconnect(now=101.0)
        assert link.payload["positions"] == [{"id": "a"}]

    def test_reconnecting_clears_the_down_state(self):
        link = EngineLink()
        link.on_disconnect(now=100.0)
        link.on_snapshot({"seq": 1}, now=101.0)
        assert link.state(now=101.0) is LinkState.LIVE


class TestSerialisation:
    def test_snapshot_carries_what_the_strip_needs(self):
        link = EngineLink()
        link.on_snapshot({"seq": 3}, now=100.0)
        view = link.describe(now=102.0)
        assert view["connected"] is True
        assert view["state"] == "live"
        assert view["seq"] == 3
        assert view["age_seconds"] == pytest.approx(2.0)

    def test_snapshot_of_an_unreached_engine_says_unknown(self):
        view = EngineLink().describe(now=5.0)
        assert view["state"] == "unknown"
        assert view["age_seconds"] is None
