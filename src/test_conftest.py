"""The suite is hermetic: a live grim session's env must not change results."""

import os
import subprocess
import sys
from pathlib import Path

_AMBIENT = "GRIM_SESSION GRIM_SESSION_TASK GRIM_SESSION_MODEL GRIM_MODEL GRIM_CWD GRIM_BASE_SEEDS"


def test_fixtures_pass_with_a_live_session_env_set() -> None:
    # A child pytest, so the vars are set before the autouse fixture runs; one test per
    # leak seen (session metadata, call depth, the seed roster). Ref:
    # https://docs.pytest.org/en/stable/how-to/usage.html#calling-pytest-through-python-m-pytest
    tests = ["grim/adapter/test_environment.py", "grim/verbs/test_shared.py"]
    tests.append("grim/seeds/test_loader.py::test_load_seeds_is_idempotent")
    env = {**os.environ, **dict.fromkeys(_AMBIENT.split(), "run_cmd"), "GRIM_CALL_DEPTH": "2"}
    command = [sys.executable, "-m", "pytest", "-q", *tests]
    cwd = Path(__file__).parent
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=120)
    assert done.returncode == 0, done.stdout.decode()[-2000:]
    assert b" passed" in done.stdout and b"failed" not in done.stdout
