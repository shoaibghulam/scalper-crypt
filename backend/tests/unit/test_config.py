"""Environment and URL routing.

The routing split is the single most dangerous piece of configuration in this
project. Binance decommissioned the unrouted market-data WebSocket paths: a
legacy /ws/ URL for a kline stream still CONNECTS and then delivers nothing,
with no error and no close frame. Testnet does not yet enforce routing, so a
bot can pass every testnet run on legacy URLs and get a dead feed the moment it
is pointed at live money.

These tests exist to make that failure impossible to ship.
"""

import pytest

from scalper.config import Environment, ExchangeConfig, StreamRoute, route_for_stream


class TestEnvironment:
    def test_three_environments_exist(self):
        assert {e.value for e in Environment} == {"testnet", "paper", "live"}

    def test_live_is_not_the_default(self):
        # Defaulting to live is how an accident becomes a loss.
        assert Environment.default() is Environment.TESTNET


class TestBaseUrls:
    def test_testnet_rest_url(self):
        cfg = ExchangeConfig(env=Environment.TESTNET, api_key="k", api_secret="s")
        assert cfg.rest == "https://testnet.binancefuture.com"

    def test_live_rest_url(self):
        cfg = ExchangeConfig(env=Environment.LIVE, api_key="k", api_secret="s")
        assert cfg.rest == "https://fapi.binance.com"

    def test_paper_uses_live_market_data(self):
        # Paper mode simulates fills against the REAL book. Testnet spreads are
        # ~265x too wide and depth ~250x too deep, which makes them useless for
        # measuring an edge.
        cfg = ExchangeConfig(env=Environment.PAPER, api_key="k", api_secret="s")
        assert cfg.rest == "https://fapi.binance.com"

    @pytest.mark.parametrize("env", list(Environment))
    def test_market_url_is_always_routed(self, env):
        cfg = ExchangeConfig(env=env, api_key="k", api_secret="s")
        assert cfg.ws_market.endswith("/market")

    @pytest.mark.parametrize("env", list(Environment))
    def test_public_url_is_always_routed(self, env):
        cfg = ExchangeConfig(env=env, api_key="k", api_secret="s")
        assert cfg.ws_public.endswith("/public")

    @pytest.mark.parametrize("env", list(Environment))
    def test_private_url_is_always_routed(self, env):
        cfg = ExchangeConfig(env=env, api_key="k", api_secret="s")
        assert cfg.ws_private.endswith("/private")

    @pytest.mark.parametrize("env", list(Environment))
    def test_no_environment_ever_yields_a_legacy_unrouted_ws_url(self, env):
        cfg = ExchangeConfig(env=env, api_key="k", api_secret="s")
        for url in (cfg.ws_market, cfg.ws_public, cfg.ws_private):
            tail = url.rsplit("/", 1)[-1]
            assert tail in {"market", "public", "private"}, f"unrouted URL: {url}"


class TestStreamRouting:
    @pytest.mark.parametrize(
        "stream",
        [
            "btcusdt@kline_1m",
            "ethusdt@kline_5m",
            "btcusdt@aggTrade",
            "btcusdt@markPrice",
            "!markPrice@arr@1s",
            "btcusdt@ticker",
            "!ticker@arr",
        ],
    )
    def test_market_category_streams(self, stream):
        assert route_for_stream(stream) is StreamRoute.MARKET

    @pytest.mark.parametrize(
        "stream",
        ["btcusdt@bookTicker", "!bookTicker", "btcusdt@depth20@100ms", "btcusdt@depth"],
    )
    def test_public_category_streams(self, stream):
        assert route_for_stream(stream) is StreamRoute.PUBLIC

    def test_unknown_stream_raises_rather_than_guessing(self):
        # Guessing a route yields a socket that opens and stays silent. Fail loudly.
        with pytest.raises(ValueError, match="unknown stream"):
            route_for_stream("btcusdt@somethingNew")

    def test_kline_and_bookticker_do_not_share_a_route(self):
        # Mixing categories on one combined connection silently drops the
        # wrong-category streams. This assertion encodes why we need >=2 sockets.
        assert route_for_stream("btcusdt@kline_1m") is not route_for_stream(
            "btcusdt@bookTicker"
        )


class TestLiveModeGuard:
    def test_live_requires_explicit_opt_in(self, monkeypatch):
        monkeypatch.delenv("TRADING_MODE", raising=False)
        cfg = ExchangeConfig(env=Environment.LIVE, api_key="k", api_secret="s")
        with pytest.raises(RuntimeError, match="TRADING_MODE"):
            cfg.assert_armed()

    def test_live_allowed_when_env_var_set(self, monkeypatch):
        monkeypatch.setenv("TRADING_MODE", "live")
        cfg = ExchangeConfig(env=Environment.LIVE, api_key="k", api_secret="s")
        cfg.assert_armed()

    @pytest.mark.parametrize("env", [Environment.TESTNET, Environment.PAPER])
    def test_non_live_needs_no_opt_in(self, env, monkeypatch):
        monkeypatch.delenv("TRADING_MODE", raising=False)
        ExchangeConfig(env=env, api_key="k", api_secret="s").assert_armed()

    def test_key_environment_mismatch_fails_closed(self):
        cfg = ExchangeConfig(env=Environment.LIVE, api_key="k", api_secret="s")
        with pytest.raises(RuntimeError, match="environment mismatch"):
            cfg.assert_key_environment(Environment.TESTNET)


class TestDatabaseSeparation:
    def test_each_environment_gets_its_own_database_file(self):
        paths = {
            ExchangeConfig(env=e, api_key="k", api_secret="s").db_filename
            for e in Environment
        }
        assert len(paths) == 3, "environments must never share a database file"

    def test_live_database_is_named_unambiguously(self):
        cfg = ExchangeConfig(env=Environment.LIVE, api_key="k", api_secret="s")
        assert "live" in cfg.db_filename
