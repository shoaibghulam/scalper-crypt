"""Start the application.

    python -m scalper

Launches the trading engine and the dashboard as separate processes under a
common supervisor, and stops both on Ctrl+C. Either can still be run on its own
during development:

    python -m scalper.engine
    python -m scalper.api
"""

import sys

from scalper.supervisor import main

if __name__ == "__main__":
    sys.exit(main())
