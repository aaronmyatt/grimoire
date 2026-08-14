"""Tests for seeds/bodies.py — each seed's actual runtime behavior,
dispatched exactly the way `grim run` would (exec.dispatch), not just
"does it look like valid python".
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from grim import db
from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS

_TIMEOUT_S = 10.0
# The strict-argv convention: a wrong invocation exits 2 with a usage line.
_USAGE_EXIT_CODE = 2


def _seed_body(name: str) -> str:
    return next(s.body for s in SEEDS if s.name == name)


def _run(
    name: str, argv: list[str] | None = None, stdin: str | None = None, cwd: str | None = None
) -> ExecutionResult:
    script_version = ScriptVersion(language="python", body=_seed_body(name))
    request = ExecutionRequest(argv=argv or [], stdin=stdin, cwd=cwd, timeout=_TIMEOUT_S)
    return dispatch(script_version, request)


def test_shell_runs_a_shell_command() -> None:
    result = _run("shell", argv=["echo hello"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"


def test_shell_propagates_exit_code() -> None:
    expected_exit_code = 3
    result = _run("shell", argv=[f"exit {expected_exit_code}"])
    assert result.exit_code == expected_exit_code


def test_shell_multi_element_argv_preserves_word_boundaries(tmp_path: Path) -> None:
    """Regression: the old naive " ".join dissolved a spaced path into two
    words, so this exact call used to fail with 'No such file'."""
    target = tmp_path / "has space.txt"
    target.write_text("payload")
    result = _run("shell", argv=["cat", str(target)])
    assert result.exit_code == 0
    assert result.stdout == "payload"


def test_shell_runs_stdin_body_when_no_argv() -> None:
    result = _run("shell", stdin="echo one\necho two\n")
    assert result.exit_code == 0
    assert result.stdout == "one\ntwo\n"


def test_shell_empty_invocation_exits_with_usage() -> None:
    result = _run("shell")
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "usage:" in result.stderr


@pytest.mark.parametrize(
    ("name", "argv"),
    [
        ("read_file", ["a.txt", "1", "2", "junk"]),
        ("read_file", []),
        ("write_file", ["a.txt", "junk"]),
        ("write_file", []),
        ("edit_file", ["a.txt", "junk"]),
        ("edit_file", []),
        ("apply_patch", ["junk"]),
        ("grep_tree", ["pattern", ".", "junk"]),
        ("grep_tree", []),
        ("list_dir", [".", "junk"]),
        ("stats", ["junk"]),
        ("gardener", ["junk"]),
        ("export_library", ["dir", "junk"]),
        ("list_bg", ["junk"]),
        ("stop_bg", ["job", "junk"]),
        ("stop_bg", []),
    ],
)
def test_wrong_arity_is_rejected_with_usage(name: str, argv: list[str]) -> None:
    """The strict-argv convention: every fixed-arity seed rejects missing or
    unexpected extra argv loudly (exit 2 + usage) instead of silently
    ignoring it — a wrong invocation must never look like a success."""
    result = _run(name, argv=argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "usage:" in result.stderr


def test_read_file_prints_whole_file(tmp_path: Path) -> None:
    target = tmp_path / "greeting.txt"
    target.write_text("line1\nline2\nline3\n")

    result = _run("read_file", argv=[str(target)])

    assert result.exit_code == 0
    assert result.stdout == "1\tline1\n2\tline2\n3\tline3\n"


def test_read_file_respects_line_range(tmp_path: Path) -> None:
    target = tmp_path / "greeting.txt"
    target.write_text("line1\nline2\nline3\n")

    result = _run("read_file", argv=[str(target), "2", "2"])

    assert result.stdout == "2\tline2\n"


def test_write_file_writes_stdin_to_path(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    content = "hello world"

    result = _run("write_file", argv=[str(target)], stdin=content)

    assert result.exit_code == 0
    assert target.read_text() == content
    assert f"wrote {len(content)} bytes" in result.stdout


def _edit_stdin(old: str, new: str) -> str:
    return f"{old}\n---GRIM-EDIT---\n{new}"


def test_edit_file_replaces_a_single_exact_match(tmp_path: Path) -> None:
    target = tmp_path / "code.py"
    target.write_text("def greet():\n    return 'hello'\n")

    result = _run(
        "edit_file", argv=[str(target)], stdin=_edit_stdin("return 'hello'", "return 'goodbye'")
    )

    assert result.exit_code == 0
    assert "replaced 1 occurrence" in result.stdout
    assert target.read_text() == "def greet():\n    return 'goodbye'\n"


def test_edit_file_rejects_zero_matches_with_count(tmp_path: Path) -> None:
    target = tmp_path / "code.py"
    target.write_text("def greet():\n    return 'hello'\n")

    result = _run("edit_file", argv=[str(target)], stdin=_edit_stdin("no such text", "new"))

    assert result.exit_code == 1
    assert "found 0 matches" in result.stderr
    assert target.read_text() == "def greet():\n    return 'hello'\n"  # untouched


def test_edit_file_rejects_multiple_matches_with_count(tmp_path: Path) -> None:
    target = tmp_path / "code.py"
    target.write_text("x = 1\nx = 1\n")

    result = _run("edit_file", argv=[str(target)], stdin=_edit_stdin("x = 1", "x = 2"))

    assert result.exit_code == 1
    assert "found 2 matches" in result.stderr
    assert target.read_text() == "x = 1\nx = 1\n"  # untouched


def test_edit_file_rejects_stdin_without_delimiter(tmp_path: Path) -> None:
    target = tmp_path / "code.py"
    target.write_text("x = 1\n")

    result = _run("edit_file", argv=[str(target)], stdin="just some text, no delimiter")

    assert result.exit_code == 1
    assert "---GRIM-EDIT---" in result.stderr


def test_apply_patch_applies_a_unified_diff(tmp_path: Path) -> None:
    (tmp_path / "greeting.txt").write_text("hello\n")
    patch_text = "--- a/greeting.txt\n+++ b/greeting.txt\n@@ -1 +1 @@\n-hello\n+hello world\n"

    result = _run("apply_patch", stdin=patch_text, cwd=str(tmp_path))

    assert result.exit_code == 0
    assert "applied via git apply" in result.stdout
    assert (tmp_path / "greeting.txt").read_text() == "hello world\n"


def test_apply_patch_reports_failure_on_a_bad_patch(tmp_path: Path) -> None:
    (tmp_path / "greeting.txt").write_text("hello\n")
    malformed = "not a real patch\n"

    result = _run("apply_patch", stdin=malformed, cwd=str(tmp_path))

    assert result.exit_code == 1
    assert "git apply failed" in result.stderr
    assert "patch -p1 also failed" in result.stderr


def test_grep_tree_finds_a_match_with_line_number(tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text("line1\nneedle here\nline3\n")

    result = _run("grep_tree", argv=["needle", str(tmp_path)])

    assert result.exit_code == 0
    assert "2:needle here" in result.stdout


def test_grep_tree_no_match_exits_nonzero(tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text("nothing interesting here\n")

    result = _run("grep_tree", argv=["needle", str(tmp_path)])

    assert result.exit_code != 0


def test_list_dir_lists_files_and_subdirs(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("hi")
    (tmp_path / "subdir").mkdir()

    result = _run("list_dir", argv=[str(tmp_path)])

    lines = result.stdout.splitlines()
    assert any(line.startswith("file\t") and line.endswith("\tfile.txt") for line in lines)
    assert any(line.startswith("dir\t") and line.endswith("\tsubdir") for line in lines)


def _seed_fixture_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    """1 'shell' run + 3 'greet' runs, all in session 's1'; 'greet' was
    authored in session 's0' so its runs count as reuse. Yields
    total_runs=4, shell-escape=25.00%, reuse=75.00%, active library=100%.
    """
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = db.connect(tmp_path / "grimoire.db")
    db.migrate(conn)
    conn.row_factory = sqlite3.Row

    conn.execute("INSERT INTO session (id, kind) VALUES ('s0', 'human')")
    conn.execute("INSERT INTO session (id, kind) VALUES ('s1', 'human')")

    conn.execute(
        "INSERT INTO script (name, language, description, scope, seeded) "
        "VALUES ('shell', 'python', 'd', 'global', 1)"
    )
    conn.execute(
        "INSERT INTO script (name, language, description, scope, seeded, origin_session_id) "
        "VALUES ('greet', 'python', 'd', 'global', 0, 's0')"
    )

    seq = 0
    for name, run_count in (("shell", 1), ("greet", 3)):
        script_id = conn.execute("SELECT id FROM script WHERE name = ?", (name,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO script_version (script_id, version, body, body_hash) "
            "VALUES (?, 1, 'b', ?)",
            (script_id, f"hash-{name}"),
        )
        version_id = conn.execute(
            "SELECT id FROM script_version WHERE script_id = ?", (script_id,)
        ).fetchone()["id"]
        for _ in range(run_count):
            seq += 1
            conn.execute(
                "INSERT INTO execution (script_version_id, session_id, seq, exit_code) "
                "VALUES (?, 's1', ?, 0)",
                (version_id, seq),
            )
    conn.commit()
    return conn


def test_stats_reports_shell_escape_and_reuse_rates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_fixture_db(tmp_path, monkeypatch)

    result = _run("stats")

    assert result.exit_code == 0
    assert "total runs: 4" in result.stdout
    assert "shell-escape rate: 25.00%" in result.stdout
    assert "reuse rate: 75.00%" in result.stdout
    assert "active library: 100.00%" in result.stdout


def test_gardener_reports_stale_scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _seed_fixture_db(tmp_path, monkeypatch)
    # backdate greet's only execution history so it reads as stale, then
    # add a never-run script that should also surface as stale.
    conn.execute("UPDATE execution SET started_at = datetime('now', '-60 days')")
    conn.execute(
        "INSERT INTO script (name, language, description, scope, seeded) "
        "VALUES ('unused_helper', 'python', 'd', 'global', 0)"
    )
    conn.commit()

    result = _run("gardener")

    assert result.exit_code == 0
    assert "greet" in result.stdout
    assert "unused_helper" in result.stdout
    assert "shell" not in result.stdout.split("stale")[1]  # seeded — never flagged stale


def test_gardener_reports_exact_duplicate_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _seed_fixture_db(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO script (name, language, description, scope, seeded) "
        "VALUES ('greet_copy', 'python', 'd', 'global', 0)"
    )
    copy_id = conn.execute("SELECT id FROM script WHERE name = 'greet_copy'").fetchone()["id"]
    conn.execute(
        "INSERT INTO script_version (script_id, version, body, body_hash) VALUES (?, 1, 'b', ?)",
        (copy_id, "hash-greet"),  # same hash as 'greet' — an exact duplicate
    )
    conn.commit()

    result = _run("gardener")

    dupes_section = result.stdout.split("stale")[0]
    assert "greet" in dupes_section
    assert "greet_copy" in dupes_section


def test_export_library_writes_latest_bodies_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_fixture_db(tmp_path, monkeypatch)
    out_dir = tmp_path / "export"

    result = _run("export_library", argv=[str(out_dir)])

    assert result.exit_code == 0
    assert "exported 2 scripts" in result.stdout
    assert (out_dir / "shell.py").read_text() == "b"
    assert (out_dir / "greet.py").read_text() == "b"


def test_list_bg_reports_no_jobs_when_run_dir_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIM_RUN_DIR", str(tmp_path / "run"))
    result = _run("list_bg")
    assert result.exit_code == 0
    assert "no background jobs" in result.stdout


def test_run_bg_then_list_then_stop_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    monkeypatch.setenv("GRIM_RUN_DIR", str(run_dir))

    # `sleep 30` is the managed workload (not a test-timing sleep): it stays
    # alive for the ms-scale window until stop_bg kills it, and self-destructs
    # if the test ever crashes before stopping it.
    started = _run("run_bg", argv=["job1", "sleep 30"])
    assert started.exit_code == 0
    assert "grimbg:job1" in started.stdout
    assert (run_dir / "job1.pid").is_file()

    listed = _run("list_bg")
    assert "job1" in listed.stdout
    assert "running" in listed.stdout

    stopped = _run("stop_bg", argv=["job1"])
    assert stopped.exit_code == 0
    assert "job1" in stopped.stdout
    assert not (run_dir / "job1.pid").is_file()  # pid file removed on stop


def test_run_bg_multi_element_argv_preserves_word_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: run_bg had the same naive " ".join as the shell seed, so a
    spaced path split into two words. The touch target appearing on disk
    proves the command line survived re-tokenization intact."""
    monkeypatch.setenv("GRIM_RUN_DIR", str(tmp_path / "run"))
    target = tmp_path / "has space.txt"

    started = _run("run_bg", argv=["job2", "touch", str(target)])
    assert started.exit_code == 0

    # The job is detached, so its side effect lands asynchronously; poll with
    # a hard deadline (bounded loop, not a fixed sleep) for the file to appear.
    deadline = time.monotonic() + _TIMEOUT_S
    while not target.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert target.exists(), "backgrounded touch never created its spaced-path target"


def test_stop_bg_reports_unknown_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_RUN_DIR", str(tmp_path / "run"))
    result = _run("stop_bg", argv=["nope"])
    assert result.exit_code == 1
    assert "no such job" in result.stderr
