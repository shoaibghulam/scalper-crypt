"""The API surface.

Two properties matter more than any endpoint behaviour. The API process holds
no trading logic, so restarting it can never disturb a position. And it never
returns a secret, to anyone, under any circumstance.
"""

from fastapi.testclient import TestClient

from scalper.api.app import create_app
from scalper.config import Environment
from scalper.settings import AppSettings


def client(env: Environment = Environment.TESTNET) -> TestClient:
    return TestClient(create_app(AppSettings(BINANCE_ENV=env)))


class TestHealth:
    def test_health_responds(self):
        response = client().get("/api/health")
        assert response.status_code == 200

    def test_health_reports_the_environment(self):
        body = client().get("/api/health").json()
        assert body["mode"] == "testnet"

    def test_health_reports_engine_connectivity_separately(self):
        # Browser-to-API health and engine-to-Binance health are different
        # emergencies and must never be collapsed into one indicator.
        body = client().get("/api/health").json()
        assert "engine" in body
        assert "connected" in body["engine"]

    def test_engine_is_reported_down_when_absent(self):
        body = client().get("/api/health").json()
        assert body["engine"]["connected"] is False

    def test_health_names_the_actual_exchange_url(self):
        # Ground truth for the operator, not a label we set by hand.
        body = client().get("/api/health").json()
        assert "testnet" in body["exchange_rest_url"]

    def test_live_mode_reports_the_live_url(self):
        body = client(Environment.LIVE).get("/api/health").json()
        assert body["exchange_rest_url"] == "https://fapi.binance.com"
        assert body["mode"] == "live"


class TestStateSnapshot:
    def test_state_responds(self):
        assert client().get("/api/state").status_code == 200

    def test_state_carries_a_sequence_number(self):
        # The websocket streams deltas from this point; a gap forces a resync.
        body = client().get("/api/state").json()
        assert "seq" in body

    def test_state_has_the_collections_the_dashboard_needs(self):
        body = client().get("/api/state").json()
        for key in ("bots", "positions", "alerts", "totals"):
            assert key in body

    def test_state_starts_empty(self):
        body = client().get("/api/state").json()
        assert body["positions"] == []
        assert body["bots"] == []


class TestNoSecretsEverLeave:
    def test_no_response_contains_credential_material(self):
        forbidden = ("api_secret", "apiSecret", "secret", "private_key")
        for path in ("/api/health", "/api/state"):
            text = client().get(path).text.lower()
            for token in forbidden:
                assert token.lower() not in text, f"{path} leaked {token}"

    def test_api_process_does_not_load_exchange_credentials(self):
        # Structural guarantee: the web app is constructed from AppSettings,
        # which has no field capable of holding a key.
        assert not any(
            "secret" in name or "api_key" in name
            for name in AppSettings.model_fields
        )


class TestNoTradingLogic:
    def test_api_exposes_no_order_placement_route(self):
        app = create_app(AppSettings())
        paths = {route.path for route in app.routes}
        assert not any("order" in path for path in paths)


class TestApiRoutingIsNotSwallowedBySpa:
    """A missing API route must 404, not return the dashboard shell.

    The SPA catch-all exists so a deep link or a refresh lands on the right
    screen. Left unqualified it also swallows every unknown /api path, which
    returns HTML with a 200 and makes a simple routing typo look like a JSON
    parsing bug in the client.
    """

    def test_unknown_api_route_returns_404(self):
        assert client().get("/api/does-not-exist").status_code == 404

    def test_known_api_route_still_works(self):
        assert client().get("/api/health").status_code == 200


class TestFavorites:
    """The saved coin/strategy shortlist.

    These routes take a JSON body. A model declared inside the app factory
    cannot be resolved from module globals under postponed annotations, so
    FastAPI silently reclassifies the body as a query parameter and every save
    fails with 422 - which no frontend test catches, because those stub fetch.
    """

    def test_saving_a_favorite_accepts_a_json_body(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scalper.api.research_store.RUNS_PATH", tmp_path / "research.sqlite")
        response = client().post(
            "/api/research/favorites",
            json={"symbol": "BTCUSDT", "strategy": "ctk-1"},
        )
        assert response.status_code == 200, response.text
        assert [(row["symbol"], row["strategy"]) for row in response.json()] == [
            ("BTCUSDT", "ctk-1")
        ]

    def test_a_saved_favorite_comes_back_on_the_next_read(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scalper.api.research_store.RUNS_PATH", tmp_path / "research.sqlite")
        api = client()
        api.post("/api/research/favorites", json={"symbol": "ETHUSDT", "strategy": "ctk-5"})
        body = api.get("/api/research/favorites").json()
        assert ("ETHUSDT", "ctk-5") in [(row["symbol"], row["strategy"]) for row in body]

    def test_removing_a_favorite_accepts_a_json_body(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scalper.api.research_store.RUNS_PATH", tmp_path / "research.sqlite")
        api = client()
        api.post("/api/research/favorites", json={"symbol": "BTCUSDT", "strategy": "ctk-1"})
        response = api.request(
            "DELETE",
            "/api/research/favorites",
            json={"symbol": "BTCUSDT", "strategy": "ctk-1"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == []

    def test_an_invalid_symbol_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scalper.api.research_store.RUNS_PATH", tmp_path / "research.sqlite")
        response = client().post(
            "/api/research/favorites",
            json={"symbol": "not a symbol", "strategy": "ctk-1"},
        )
        assert response.status_code == 422


class TestStrategyTimeframe:
    """A strategy's candle interval belongs in its description.

    The trading UI has to say whether a bot runs on 1m or 5m candles before an
    operator commits money to it; without this the timeframe was only
    reachable through the research catalog, which the trading tabs do not use.
    """

    def test_every_strategy_declares_its_timeframe(self):
        body = client().get("/api/strategies").json()
        assert body["strategies"], "no strategies to check"
        for strategy in body["strategies"]:
            assert "timeframe" in strategy, strategy["id"]

    def test_the_timeframe_is_a_readable_interval(self):
        body = client().get("/api/strategies").json()
        found = {s["timeframe"] for s in body["strategies"]}
        # Most read "1m"/"5m"; a few declare their own richer label, such as
        # "5m (1h context)". Both are for a human, so the rule is that each is
        # a non-empty string that starts with the number of minutes.
        for label in found:
            assert isinstance(label, str) and label[:1].isdigit(), label
        assert {"1m", "5m"} <= found, found

    def test_a_declared_timeframe_wins_over_the_derived_one(self):
        # A strategy that spells out its own label keeps it: the derived value
        # would flatten "5m (1h context)" to "5m" and lose the context bar.
        body = client().get("/api/strategies").json()
        labels = {s["timeframe"] for s in body["strategies"]}
        assert any(" " in label for label in labels), labels
