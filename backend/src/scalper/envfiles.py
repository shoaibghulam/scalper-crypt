"""Where to look for the .env file.

Resolved rather than assumed. The application is started from the backend
directory but the .env sits at the project root, so a bare relative filename
silently finds nothing: pydantic reports the fields as missing, which reads as
"you did not set your keys" rather than "your keys are one directory up".

Candidates are returned in increasing precedence; pydantic-settings loads all
existing files and the last value wins.
"""

from __future__ import annotations

from pathlib import Path

#: backend/src/scalper/envfiles.py -> backend/src/scalper -> src -> backend -> root
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def env_files() -> tuple[Path, ...]:
    """Candidate .env locations, nearest last.

    The working directory is tried first so a deliberate per-directory override
    wins, then the project root, which is where the file actually lives in a
    normal checkout.
    """
    # pydantic-settings loads every file, with LAST taking precedence.
    return tuple(dict.fromkeys((
        PROJECT_ROOT / ".env",
        Path.cwd().parent / ".env",
        Path.cwd() / ".env",
    )))


def found_env_file() -> Path | None:
    """The .env actually in use, for reporting to the operator."""
    return next((path for path in reversed(env_files()) if path.is_file()), None)


#: Where the engine record lives. Anchored to the project root for the same
#: reason the .env is: a path relative to the working directory means launching
#: from a different directory starts with no memory of open positions, and the
#: engine would then report its own live trades as belonging to nobody.
STATE_DIR = PROJECT_ROOT / "var"


def state_file() -> Path:
    """The engine state file, overridable for tests and for a second account."""
    import os

    override = os.environ.get("SCALPER_STATE_FILE")
    return Path(override) if override else STATE_DIR / "engine-state.json"
