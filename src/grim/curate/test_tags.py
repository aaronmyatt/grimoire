"""Tests for curate/tags.py."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.curate import _shared
from grim.curate.tags import (
    add_tags,
    cmd_favourite,
    cmd_favourites,
    cmd_tag,
    cmd_tagged,
    cmd_tags,
    cmd_unfavourite,
    cmd_untag,
    list_tags,
    normalize_tag,
    remove_tags,
    scripts_for_tag,
)
from grim.verbs.write import WriteRequest, write_script


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    return conn


def _write(conn: sqlite3.Connection, name: str) -> None:
    write_script(
        conn,
        WriteRequest(
            name=name,
            language="python",
            description="d",
            body="print(1)",
            parent=None,
            scope="global",
            session_id="human-adhoc",
        ),
    )


# --- normalize_tag -----------------------------------------------------


def test_normalize_tag_lowercases_and_trims() -> None:
    assert normalize_tag("  CI  ") == "ci"


def test_normalize_tag_rejects_bad_shape() -> None:
    with pytest.raises(ValueError, match="invalid tag"):
        normalize_tag("not a tag!")


def test_normalize_tag_rejects_empty() -> None:
    with pytest.raises(ValueError, match="invalid tag"):
        normalize_tag("   ")


# --- add_tags / remove_tags ---------------------------------------------


def test_add_tags_creates_and_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet")

    normalized = add_tags(conn, "greet", ["CI", "flaky"])

    assert normalized == ["ci", "flaky"]
    names = {r["name"] for r in list_tags(conn)}
    assert {"ci", "flaky"} <= names


def test_add_tags_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet")

    add_tags(conn, "greet", ["ci"])
    add_tags(conn, "greet", ["ci"])  # must not error or double-count

    rows = scripts_for_tag(conn, "ci")
    assert [r["name"] for r in rows] == ["greet"]


def test_add_tags_unknown_script_raises_lookup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(LookupError):
        add_tags(_shared.connect(), "ghost", ["ci"])


def test_add_tags_bad_tag_raises_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet")
    with pytest.raises(ValueError, match="invalid tag"):
        add_tags(conn, "greet", ["not valid!"])


def test_remove_tags_unlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet")
    add_tags(conn, "greet", ["ci", "flaky"])

    remove_tags(conn, "greet", ["flaky"])

    rows = scripts_for_tag(conn, "ci")
    assert [r["name"] for r in rows] == ["greet"]
    # the tag row itself still exists (only the link was removed) — an
    # empty result, not LookupError, which is reserved for an unknown tag.
    assert scripts_for_tag(conn, "flaky") == []


def test_remove_tags_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet")
    remove_tags(conn, "greet", ["never-tagged"])  # must not error


# --- list_tags / scripts_for_tag -----------------------------------------


def test_list_tags_orders_by_usage_then_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "script_a")
    _write(conn, "script_b")
    scripts_tagged_popular = 2
    add_tags(conn, "script_a", ["popular"])
    add_tags(conn, "script_b", ["popular", "rare"])

    rows = list_tags(conn)

    assert [r["name"] for r in rows] == ["popular", "rare"]
    assert rows[0]["uses"] == scripts_tagged_popular


def test_scripts_for_tag_unknown_tag_raises_lookup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(LookupError):
        scripts_for_tag(conn, "ghost-tag")


def test_scripts_for_tag_excludes_archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet")
    add_tags(conn, "greet", ["ci"])
    conn.execute("UPDATE script SET archived = 1 WHERE name = 'greet'")
    conn.commit()

    assert scripts_for_tag(conn, "ci") == []


# --- cmd_* -------------------------------------------------------


def test_cmd_tag_and_cmd_tagged_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _migrated_conn(tmp_path, monkeypatch)
    _write(_shared.connect(), "greet")

    exit_code = cmd_tag(argparse.Namespace(name="greet", tags=["ci"]))
    assert exit_code == 0
    assert "tagged greet: ci" in capsys.readouterr().out

    exit_code = cmd_tagged(argparse.Namespace(tag="ci", limit=None))
    assert exit_code == 0
    assert "greet" in capsys.readouterr().out


def test_cmd_untag_removes_a_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet")
    add_tags(conn, "greet", ["ci"])

    exit_code = cmd_untag(argparse.Namespace(name="greet", tags=["ci"]))

    assert exit_code == 0
    assert "untagged greet: ci" in capsys.readouterr().out
    assert scripts_for_tag(conn, "ci") == []


def test_cmd_tags_lists_all_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet")
    add_tags(conn, "greet", ["ci"])

    exit_code = cmd_tags(argparse.Namespace(limit=None))

    assert exit_code == 0
    assert "ci\tuses=1" in capsys.readouterr().out


def test_cmd_tagged_unknown_tag_errors_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _migrated_conn(tmp_path, monkeypatch)

    exit_code = cmd_tagged(argparse.Namespace(tag="ghost", limit=None))

    assert exit_code == 1
    assert "not found" in capsys.readouterr().err


def test_cmd_tag_unknown_script_errors_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _migrated_conn(tmp_path, monkeypatch)

    exit_code = cmd_tag(argparse.Namespace(name="ghost", tags=["ci"]))

    assert exit_code == 1
    assert "not found" in capsys.readouterr().err


# --- favourite/unfavourite/favourites -------------------------------------


def test_cmd_favourite_and_favourites_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _migrated_conn(tmp_path, monkeypatch)
    _write(_shared.connect(), "greet")

    exit_code = cmd_favourite(argparse.Namespace(name="greet"))
    assert exit_code == 0
    assert "favourited greet" in capsys.readouterr().out

    exit_code = cmd_favourites(argparse.Namespace(limit=None))
    assert exit_code == 0
    assert "greet" in capsys.readouterr().out


def test_cmd_unfavourite_removes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet")
    cmd_favourite(argparse.Namespace(name="greet"))

    exit_code = cmd_unfavourite(argparse.Namespace(name="greet"))

    assert exit_code == 0
    assert "unfavourited greet" in capsys.readouterr().out
    assert cmd_favourites(argparse.Namespace(limit=None)) == 0
    assert capsys.readouterr().out == ""


def test_cmd_favourites_empty_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _migrated_conn(tmp_path, monkeypatch)  # nobody has favourited anything — no "favourite" tag row

    exit_code = cmd_favourites(argparse.Namespace(limit=None))

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_cmd_favourite_unknown_script_errors_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _migrated_conn(tmp_path, monkeypatch)

    exit_code = cmd_favourite(argparse.Namespace(name="ghost"))

    assert exit_code == 1
    assert "not found" in capsys.readouterr().err
