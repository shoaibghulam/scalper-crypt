"""Entrypoint for the web process.

Run with: python -m scalper.api
"""

from __future__ import annotations

import uvicorn

from scalper.api.app import create_app
from scalper.settings import AppSettings


def main() -> None:
    settings = AppSettings()
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
