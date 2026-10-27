"""The web process: read-only, stateless with respect to trading.

This app can be restarted, redeployed or run with any number of workers without
touching a live position, because it contains no trading logic at all. That
property is the whole reason the engine is a separate process: uvicorn workers
are separate OS processes that each run startup independently, so an engine
living here would be multiplied by the worker count, placing duplicate orders
that each pass their own per-process risk check.

What this process does: serve the dashboard bundle, answer snapshot requests,
fan engine state out to browsers, and record operator commands for the engine
to act on.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scalper.api import alpha_lab
from scalper.api.engine_link import EngineLink, LinkState
from scalper.api.history import history as research_history
from scalper.api.history import symbols as research_symbols
from scalper.api.quant_lab import router as quant_lab_router
from scalper.api.research import backtest as research_backtest
from scalper.api.research import catalog as research_catalog
from scalper.api.research_cache import inventory as research_cache_inventory
from scalper.api.research_jobs import cancel_job, capabilities, get_job, start_job
from scalper.api.research_store import (
    add_favorite,
    list_favorites,
    remove_favorite,
    tested_strategies,
)
from scalper.api.research_store import list_runs as research_runs
from scalper.api.research_store import load_run as research_run
from scalper.api.research_v2 import (
    cancel_v2_job,
    get_v2_job,
    start_download_job,
    start_v2_job,
    v2_inventory,
)
from scalper.api.subscriber import subscribe
from scalper.config import LIVE_REST, TESTNET_REST, Environment
from scalper.engine.commands import (
    AutoTradeRequest,
    CreateBotRequest,
    DismissBotsRequest,
    SingleCoinRequest,
)
from scalper.scanner.service import ScannerService
from scalper.settings import AppSettings
from scalper.strategies import DEFAULT_STRATEGY_ID, STRATEGIES

#: Built dashboard bundle, relative to the repository root.
DEFAULT_BUNDLE = Path(__file__).resolve().parents[3].parent / "frontend" / "dist"

API_VERSION = "0.1.0"

#: The operator may select up to 100x. The binding constraint is not this
#: number: it is whether liquidation still sits outside the stop, which is
#: checked per trade against the actual stop distance in plan validation. A
#: request that passes here can still be refused there, and that is the check
#: that matters.
MAX_LEVERAGE = 100
MAX_SYMBOLS_PER_BOT = 5
MAX_BOT_NAME_LENGTH = 64


class StrategyExperimentRequest(BaseModel):
    strategy: str | None = Field(default=None, max_length=80)
    disabled: bool | None = Field(default=None, strict=True)
    auto_disable: bool | None = Field(default=None, strict=True)


class AdoptRequest(BaseModel):
    """Claim positions the exchange holds that the engine cannot explain.

    Symbols are named explicitly rather than adopting everything: the operator
    may want to keep a hand-opened hedge out of the fleet.
    """

    symbols: list[str] = Field(min_length=1, max_length=20)
    name: str = Field(default="Adopted positions", max_length=MAX_BOT_NAME_LENGTH)
    #: Attach a stop to any named position that has none, then adopt it.
    #: Defaults to false because it places a real order, and an adopt that
    #: silently traded would be the wrong kind of surprise.
    protect: bool = False


class FavoriteRequest(BaseModel):
    """One saved coin/strategy pair.

    Declared here, not inside `create_app`: `from __future__ import
    annotations` makes every handler annotation a string, and FastAPI resolves
    those against module globals. A model nested in the factory is not found,
    so the body is reclassified as a query parameter and every write fails 422.
    """

    symbol: str = Field(max_length=25)
    strategy: str = Field(max_length=80)


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or AppSettings()
    engine = EngineLink()

    # One cached scanner per strategy. Two strategies asked at the same
    # moment produce different candidate lists from the same market data, so a
    # single shared cache would hand one strategy the other answers.
    #
    # Selection must use the same symbol universe and spread as execution.
    market_rest = LIVE_REST if settings.env.uses_live_market_data else TESTNET_REST
    scanners = {
        strategy_id: ScannerService(market_rest, strategy_id=strategy_id)
        for strategy_id in STRATEGIES.ids()
        if not STRATEGIES.get(strategy_id).testnet_only or settings.env is Environment.TESTNET
    }

    # Commands are relayed, never executed here. This process has no
    # credentials and no exchange client; it can ask the engine to act and
    # nothing more.
    commands: asyncio.Queue[str] = asyncio.Queue(maxsize=100)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Subscribe to engine state for as long as the web app is up.

        This is a read-only consumer, not the trading loop. It receives
        snapshots and updates an in-memory view, so running it under several
        uvicorn workers duplicates a subscription rather than an engine.
        """
        stop = asyncio.Event()
        task = asyncio.create_task(subscribe(settings.engine_url, engine, stop, outbound=commands))
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="The Scalper Crypt",
        version=API_VERSION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    for route in quant_lab_router.routes:
        app.add_api_route(route.path, route.endpoint, methods=list(route.methods), tags=route.tags)
    app.add_api_route("/api/alpha-lab/capabilities", alpha_lab.capabilities, methods=["GET"])
    app.add_api_route("/api/alpha-lab/jobs", alpha_lab.history, methods=["GET"])
    app.add_api_route("/api/alpha-lab/jobs", alpha_lab.start, methods=["POST"])
    app.add_api_route("/api/alpha-lab/jobs/{job_id}", alpha_lab.get, methods=["GET"])
    app.add_api_route("/api/alpha-lab/jobs/{job_id}/cancel", alpha_lab.cancel, methods=["POST"])
    app.add_api_route("/api/research/backtest", research_backtest, methods=["POST"])
    app.add_api_route("/api/research/catalog", research_catalog, methods=["GET"])
    app.add_api_route("/api/research/symbols", research_symbols, methods=["GET"])
    app.add_api_route("/api/research/history", research_history, methods=["POST"])
    app.add_api_route("/api/research/runs", research_runs, methods=["GET"])
    app.add_api_route("/api/research/tested-strategies", tested_strategies, methods=["GET"])

    @app.get("/api/research/favorites")
    def favorites() -> list[dict[str, Any]]:
        """The saved coin/strategy shortlist, shared by every browser."""
        return list_favorites()

    @app.post("/api/research/favorites")
    def save_favorite(request: FavoriteRequest) -> list[dict[str, Any]]:
        return add_favorite(request.symbol, request.strategy)

    @app.delete("/api/research/favorites")
    def drop_favorite(request: FavoriteRequest) -> list[dict[str, Any]]:
        return remove_favorite(request.symbol, request.strategy)
    app.add_api_route("/api/research/runs/{run_id}", research_run, methods=["GET"])
    app.add_api_route("/api/research/cache", research_cache_inventory, methods=["GET"])
    app.add_api_route("/api/research/capabilities", capabilities, methods=["GET"])
    app.add_api_route("/api/research/jobs", start_job, methods=["POST"])
    app.add_api_route("/api/research/jobs/{job_id}", get_job, methods=["GET"])
    app.add_api_route("/api/research/jobs/{job_id}/cancel", cancel_job, methods=["POST"])
    # v2 runs offline from the local candle store. v1 above is untouched so the
    # two can be compared on the same dates.
    app.add_api_route("/api/research/v2/inventory", v2_inventory, methods=["GET"])
    app.add_api_route("/api/research/v2/jobs", start_v2_job, methods=["POST"])
    app.add_api_route("/api/research/v2/download", start_download_job, methods=["POST"])
    app.add_api_route("/api/research/v2/jobs/{job_id}", get_v2_job, methods=["GET"])
    app.add_api_route("/api/research/v2/jobs/{job_id}/cancel", cancel_v2_job, methods=["POST"])
    app.state.engine = engine

    def queue_command(message: str) -> None:
        command = json.loads(message)
        command["issued_at"] = time.time()
        try:
            commands.put_nowait(json.dumps(command))
        except asyncio.QueueFull:
            raise HTTPException(503, "command queue is full; nothing was sent") from None

    @app.middleware("http")
    async def same_origin_commands(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Local service: block browser requests from unrelated sites. The
        # origin check also covers DELETE requests without a JSON body.
        from urllib.parse import urlsplit

        from fastapi.responses import JSONResponse

        host = request.url.hostname
        if host not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            return JSONResponse({"detail": "untrusted host"}, status_code=400)
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            parsed = urlsplit(origin)
            allowed = {
                str(request.base_url).rstrip("/"),
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            }
            if origin not in allowed or parsed.scheme not in {"http", "https"}:
                return JSONResponse({"detail": "cross-origin command refused"}, status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """Liveness plus the two independent trust signals.

        Browser-to-API and engine-to-Binance are different emergencies. The
        first means you cannot see the truth; the second means the bot is
        trading blind. Collapsing them into one dot hides the worse case.
        """
        return {
            "status": "ok",
            "version": API_VERSION,
            "mode": settings.env.value,
            "is_real_money": settings.env.is_real_money,
            # The real URL, not a label. If these ever disagree, the label lies.
            "exchange_rest_url": (
                LIVE_REST if settings.env.uses_live_market_data else TESTNET_REST
            ),
            "engine": engine.describe(now=time.monotonic()),
        }

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        """Whatever the engine last reported.

        When the link is down this still returns the last known payload rather
        than an empty one. Blanking the screen loses information the operator
        may need; the age in the health response is what tells them how much to
        trust it.
        """
        payload = engine.payload
        positions = []
        owners = {b["id"]: b.get("strategy") for b in payload.get("bots", [])}
        for position in payload.get("positions", []):
            strategy_id = owners.get(str(position.get("id", "")).split("-", 1)[0])
            timeframe = None
            if strategy_id in STRATEGIES.ids():
                strategy = STRATEGIES.get(strategy_id)
                timeframe = ("Event" if strategy.requires_order_flow else
                             f"{strategy.signal_interval_s // 60}m")
            positions.append({**position, "timeframe": timeframe})
        return {
            "seq": engine.seq,
            "mode": settings.env.value,
            "engine_state": engine.state(now=time.monotonic()).value,
            "bots": payload.get("bots", []),
            "positions": positions,
            "external_positions": payload.get("external_positions", []),
            "alerts": payload.get("alerts", []),
            # Passed through verbatim. None means no command has run yet,
            # which the dashboard renders as nothing rather than as silence
            # after a click.
            "last_action": payload.get("last_action"),
            # Defaults to False when the engine has not reported: the form must
            # not invite a trade it cannot know will be accepted.
            "halted": bool(payload.get("halted", False)),
            "max_leverage": payload.get("max_leverage", settings.max_leverage),
            "max_positions": settings.max_positions,
            "auto_trade": payload.get("auto_trade", {"enabled": False}),
            "single_coin": payload.get("single_coin", {"enabled": False}),
            "trading_enabled": (
                engine.state(now=time.monotonic()) is LinkState.LIVE
                and payload.get("trading_enabled", False)
            ),
            "strategy_monitor": payload.get("strategy_monitor"),
            "history": payload.get(
                "history",
                {
                    "trades": [],
                    "persistent": False,
                    "summary": {
                        "trades": 0,
                        "win_rate": None,
                        "profit_factor": None,
                        "expectancy": None,
                        "net": 0,
                        "fees": 0,
                        "tp_hits": 0,
                        "sl_hits": 0,
                        "wins": 0,
                        "losses": 0,
                    },
                },
            ),
            # Passed through from the engine, which is the only process
            # that holds credentials. Contains a masked preview and never
            # any key material.
            "credentials": payload.get(
                "credentials",
                {
                    "configured": False,
                    "valid": False,
                    "message": "The engine has not reported yet.",
                    "environment": "unknown",
                    "key_preview": "",
                    "balance": None,
                    "can_trade": None,
                },
            ),
            "totals": payload.get(
                "totals",
                {
                    "unrealised_pnl": "0",
                    "realised_today": "0",
                    "open_positions": 0,
                    "bots_running": 0,
                    "bots_stopped": 0,
                    "bots_error": 0,
                    "tp_hits_today": 0,
                    "sl_hits_today": 0,
                },
            ),
        }

    @app.get("/api/strategies")
    def strategies() -> dict[str, Any]:
        """Every bot type this build knows about.

        Each carries its assumptions, so an operator can see exactly where a
        strategy departs from whatever it was adapted from.
        """
        from scalper.strategies.research_registry import RESEARCH_STRATEGIES

        return {
            "default": DEFAULT_STRATEGY_ID,
            "strategies": [STRATEGIES.get(key).describe() for key in scanners]
            + [s.describe() for s in RESEARCH_STRATEGIES.values() if s.backtest_only],
        }

    @app.get("/api/scan")
    async def scan(force: bool = False, strategy: str = DEFAULT_STRATEGY_ID) -> dict[str, Any]:
        """Symbols currently worth trading, ranked.

        Read-only market analysis on public endpoints, so it works before any
        credentials are configured. It is not trading logic: nothing here can
        place an order, which is why it can live in the web process.

        Results are cached, so polling this endpoint does not multiply into
        polling Binance.
        """
        if strategy not in scanners:
            raise HTTPException(404, f"unknown strategy {strategy!r}")
        service = scanners[strategy]
        await service.get(force=force)
        return service.describe()

    @app.post("/api/control/{action}", status_code=202)
    async def control(action: str) -> dict[str, Any]:
        if action not in {"panic", "resume"}:
            raise HTTPException(404, "unknown control action")
        if engine.state(now=time.monotonic()) is not LinkState.LIVE:
            raise HTTPException(503, "engine unavailable; nothing was sent")
        queue_command(json.dumps({"type": action}))
        return {"accepted": True, "action": action}

    @app.post("/api/single-coin/start", status_code=202)
    async def start_single_coin(request: SingleCoinRequest) -> dict[str, Any]:
        if settings.env is not Environment.TESTNET:
            raise HTTPException(400, "Single Coin is testnet-only")
        if engine.state(now=time.monotonic()) is not LinkState.LIVE:
            raise HTTPException(503, "the engine is not reporting; nothing was sent")
        if request.leverage > settings.max_leverage:
            raise HTTPException(422, f"configured leverage cap is {settings.max_leverage}x")
        queue_command(json.dumps({"type": "start_single_coin", "amount": str(request.amount),
                                  "leverage": request.leverage, "timeframe": request.timeframe}))
        return {"accepted": True}

    @app.post("/api/single-coin/stop", status_code=202)
    async def stop_single_coin() -> dict[str, Any]:
        if engine.state(now=time.monotonic()) is not LinkState.LIVE:
            raise HTTPException(503, "the engine is not reporting; nothing was sent")
        queue_command(json.dumps({"type": "stop_single_coin"}))
        return {"accepted": True}

    @app.post("/api/strategy-monitor/settings", status_code=202)
    async def strategy_settings(request: StrategyExperimentRequest) -> dict[str, Any]:
        if settings.env is not Environment.TESTNET:
            raise HTTPException(400, "Strategy experiments are testnet-only")
        if (request.strategy is None) != (request.disabled is None):
            raise HTTPException(422, "strategy and disabled must be supplied together")
        if request.strategy is None and request.auto_disable is None:
            raise HTTPException(422, "no experiment setting supplied")
        if engine.state(now=time.monotonic()) is not LinkState.LIVE:
            raise HTTPException(503, "engine unavailable; nothing was sent")
        if request.strategy is not None and request.strategy not in STRATEGIES.ids():
            raise HTTPException(422, "unknown strategy")
        queue_command(json.dumps(
            {"type": "strategy_experiment", **request.model_dump(exclude_none=True)}
        ))
        return {"accepted": True}

    @app.post("/api/auto-trade/start", status_code=202)
    async def start_auto_trade(request: AutoTradeRequest) -> dict[str, Any]:
        if settings.env is not Environment.TESTNET:
            raise HTTPException(400, "Auto Trade is testnet-only")
        if engine.state(now=time.monotonic()) is not LinkState.LIVE:
            raise HTTPException(503, "the engine is not reporting; nothing was sent")
        if request.leverage > settings.max_leverage:
            raise HTTPException(422, f"configured leverage cap is {settings.max_leverage}x")
        queue_command(json.dumps({"type": "start_auto_trade", "amount": str(request.amount),
                                  "leverage": request.leverage}))
        return {"accepted": True}

    @app.post("/api/auto-trade/stop", status_code=202)
    async def stop_auto_trade() -> dict[str, Any]:
        if engine.state(now=time.monotonic()) is not LinkState.LIVE:
            raise HTTPException(503, "the engine is not reporting; nothing was sent")
        queue_command(json.dumps({"type": "stop_auto_trade"}))
        return {"accepted": True}

    @app.post("/api/bots", status_code=202)
    async def create_bot(request: CreateBotRequest) -> dict[str, Any]:
        """Ask the engine to open a bot on the selected symbols.

        Accepted rather than created: this process relays the instruction and
        the engine decides. Reporting success here would claim an order was
        placed by a process that cannot place one.
        """
        if engine.state(now=time.monotonic()) is not LinkState.LIVE:
            raise HTTPException(503, "the engine is not reporting; nothing was sent")

        if request.strategy not in STRATEGIES.ids():
            raise HTTPException(404, f"unknown strategy {request.strategy!r}")

        if (STRATEGIES.get(request.strategy).testnet_only
                and settings.env is not Environment.TESTNET):
            raise HTTPException(400, "This experimental strategy is testnet-only")

        queue_command(
            json.dumps(
                {
                    "type": "create_bot",
                    "symbols": request.symbols,
                    "amount": str(request.amount),
                    "leverage": request.leverage,
                    "strategy": request.strategy,
                    "continuous": request.continuous,
                    "name": request.name,
                    "margin_mode": request.margin_mode,
                }
            )
        )
        return {
            "accepted": True,
            "symbols": request.symbols,
            "note": "the engine will report the result in its next snapshot",
        }

    @app.post("/api/bots/adopt", status_code=202)
    async def adopt(request: AdoptRequest) -> dict[str, Any]:
        """Ask the engine to take the named orphaned positions under management.

        202 rather than 201. The engine reads the protective orders back from
        the exchange and may refuse any of them, so nothing is created here.
        """
        if engine.state(now=time.monotonic()) is not LinkState.LIVE:
            raise HTTPException(503, "the engine is not reporting; nothing was sent")
        queue_command(
            json.dumps(
                {
                    "type": "adopt_orphans",
                    "symbols": [s.upper() for s in request.symbols],
                    "name": request.name,
                    "protect": request.protect,
                }
            )
        )
        return {"accepted": True, "symbols": request.symbols}

    @app.post("/api/bots/dismiss", status_code=202)
    async def dismiss_bots(request: DismissBotsRequest) -> dict[str, Any]:
        if engine.state(now=time.monotonic()) is not LinkState.LIVE:
            raise HTTPException(503, "the engine is not reporting; nothing was sent")
        queue_command(json.dumps({"type": "dismiss_bots", "bot_ids": request.bot_ids}))
        return {"accepted": True}

    @app.delete("/api/bots/{bot_id}", status_code=202)
    async def remove_bot(bot_id: str) -> dict[str, Any]:
        """Forget a bot that will never trade again.

        A record operation, not a trading one: no orders are placed or
        cancelled. The engine refuses while the bot still holds a position,
        because deleting the record would turn a managed position into an
        orphan and disable trading on the next restart.
        """
        if engine.state(now=time.monotonic()) is not LinkState.LIVE:
            raise HTTPException(503, "the engine is not reporting; nothing was sent")
        queue_command(json.dumps({"type": "remove_bot", "bot_id": bot_id}))
        return {"accepted": True, "bot_id": bot_id}

    @app.post("/api/bots/{bot_id}/stop", status_code=202)
    async def stop_bot(bot_id: str) -> dict[str, Any]:
        if engine.state(now=time.monotonic()) is not LinkState.LIVE:
            raise HTTPException(503, "the engine is not reporting; nothing was sent")
        queue_command(json.dumps({"type": "stop_bot", "bot_id": bot_id}))
        return {"accepted": True, "bot_id": bot_id}

    _mount_dashboard(app)
    return app


def _mount_dashboard(app: FastAPI, bundle: Path | None = None) -> None:
    """Serve the built dashboard from this same process.

    One origin, one port, one process tree, and no Node runtime in production
    on a host whose entire purpose is keeping a trading engine alive. It also
    means cookies and the websocket are same-origin by construction, so there
    is no CORS configuration to get wrong.

    Mounted last, so every /api route keeps priority. Absent in development,
    where the Vite dev server proxies to this process instead.
    """
    bundle = bundle or DEFAULT_BUNDLE
    if not (bundle / "index.html").is_file():
        return

    app.mount("/assets", StaticFiles(directory=bundle / "assets"), name="dashboard-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        # Client-side routing: unknown paths return the shell rather than a
        # 404, so a deep link or a refresh lands on the right screen.
        #
        # API paths are excluded. Without this, a typo or a call to an endpoint
        # that does not exist yet returns the HTML shell with a 200, and the
        # caller sees a JSON parse error instead of a 404. That wastes a great
        # deal of time before anyone suspects routing.
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail=f"no API route /{path}")
        return FileResponse(bundle / "index.html", headers={"Cache-Control": "no-store"})
