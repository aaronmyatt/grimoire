"""scratch_* one-off scripts never surface in the agent's unprompted lists
(strong matches, --continue recall). Raw SQL against the kernel schema, like
test_agent.py; kept apart because that file is over the length budget."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import NamedTuple

import pytest

pytest.importorskip("minisweagent")  # adapter/ needs the optional `agent` extra

from grim import db  # noqa: E402
from grim.adapter import agent  # noqa: E402
from grim.adapter.agent import RECALL_LIMIT_DEFAULT, recent_library, strong_matches  # noqa: E402

_WHEN = "2026-08-01 09:00:00"


def _conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    monkeypatch.chdir(tmp_path)  # outside any repo: scope resolves to 'global'
    conn = db.connect()
    db.migrate(conn)
    return conn


def _script(conn: sqlite3.Connection, name: str, description: str) -> int:
    script_id = conn.execute(
        "INSERT INTO script (name, language, description) VALUES (?, 'python', ?)",
        (name, description),
    ).lastrowid
    version_id = conn.execute(
        "INSERT INTO script_version (script_id, version, body, body_hash) "
        "VALUES (?, 1, 'print(1)', 'h')",
        (script_id,),
    ).lastrowid
    conn.commit()
    assert version_id is not None
    return version_id


def _ran(conn: sqlite3.Connection, version_id: int, seq: int) -> None:
    conn.execute("INSERT OR IGNORE INTO session (id, kind) VALUES ('s1', 'agent')")
    conn.execute(
        "INSERT INTO execution (script_version_id, session_id, seq, exit_code, started_at) "
        "VALUES (?, 's1', ?, 0, ?)",
        (version_id, seq, _WHEN),
    )
    conn.commit()


class _Case(NamedTuple):
    prefix: str
    glob: str  # what _SCRATCH_GLOB is patched to for this case
    expected_hits: int


@pytest.mark.parametrize(
    "case",
    [
        _Case("", agent._SCRATCH_GLOB, 1),
        _Case("scratch_", "never_matches*", 1),
        _Case("scratch_", agent._SCRATCH_GLOB, 0),
    ],
    ids=["control", "filter-disabled", "scratch"],
)
def test_strong_matches_skip_scratch_scripts(
    case: _Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same corpus every way (distractors keep bm25's IDF meaningful). The
    # filter-disabled case proves the scratch script clears the score
    # threshold on its own, so only the name filter drops it.
    monkeypatch.setattr(agent, "_SCRATCH_GLOB", case.glob)
    conn = _conn(tmp_path, monkeypatch)
    for name in ("fetch_dad_joke", "list_github_repos", "gardener_sweep"):
        _script(conn, name, f"{name} unrelated distractor")
    _script(
        conn, f"{case.prefix}extract_failing_tests", "extracts failing pytest tests from a CI log"
    )
    hits = strong_matches("extract failing pytest tests from ci logs")
    assert len(hits) == case.expected_hits


def test_recent_library_skips_scratch_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _conn(tmp_path, monkeypatch)
    _ran(conn, _script(conn, "scratch_probe_ports", "probe ports"), seq=1)
    _ran(conn, _script(conn, "list_open_ports", "list open ports"), seq=2)
    assert [m["name"] for m in recent_library(RECALL_LIMIT_DEFAULT)] == ["list_open_ports"]
