"""Load the G.R.A.S.P .env for tools that are not launched from a shell.

G.R.A.S.P's own config readers take a Mapping and default to os.environ; the
project expects you to `set -a; source .env`. These tools are invoked directly,
so they load the file themselves.

Values are unquoted. That matters: the pilot .env has ARANGO_PASSWORD wrapped in
quotes, `source` strips them, and a naive parser does not -- which presents as a
bare HTTP 401 that looks exactly like an expired credential.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

#: PlanGraph collection prefix for the block-demo skills. Deliberately NOT the
#: pilot's PROJECT_NAME: the pilot database is shared with other projects, so
#: this demo writes blocksDemo_* collections and leaves plannerTest_* alone.
DEMO_PREFIX = "blocksDemo"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
        return value[1:-1]
    return value


def load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = _unquote(value)
    return env


def live_env(repo_root: Path, prefix: str = DEMO_PREFIX,
             extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The .env, with the PlanGraph pinned to our own prefix."""
    env = load_dotenv(repo_root / ".env")
    if not env:
        raise SystemExit(f"no .env at {repo_root / '.env'} -- cannot reach Arango")
    missing = [k for k in ("ARANGO_URL", "ARANGO_DB") if not env.get(k)]
    if missing:
        raise SystemExit(f".env is missing {', '.join(missing)}")
    env["PLANGRAPH_PREFIX"] = prefix
    if extra:
        env.update(extra)
    return env


def describe(env: Mapping[str, str]) -> str:
    return (f"{env.get('ARANGO_URL')}  db={env.get('ARANGO_DB')}  "
            f"prefix={env.get('PLANGRAPH_PREFIX')}")
