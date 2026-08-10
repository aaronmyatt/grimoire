"""Span-based timing instrumentation for grim-agent sessions (adapter slice).

Answers "where do sessions spend their time" — LLM API calls, tool
executions, bootstrap, human wait, harness boot — without touching the
frozen kernel: no schema change, no DB writes, no lock contention with the
library's own SQLite. Each event is one JSON object per line, appended to
GRIM_INSTRUMENT_FILE (default ~/.grimoire/instrumentation.jsonl).

Rules (adapter/CLAUDE.md): stdlib only; degrade-don't-crash (a broken HOME
or unwritable file must never break a session); opt out with
GRIM_INSTRUMENT=0; auto-disabled under pytest (unless GRIM_INSTRUMENT=1).

Event kinds:
  span    {"kind":"span","phase":"llm.completion","session":...,"turn":...,
           "ts":...,"ts_start":...,"duration_ms":..., ...extra fields}
  session {"kind":"session","phase":"open"|"close","session":...,
           "wall_ms":..., "totals":{phase:{"count":n,"ms":m},...}, ...}
  boot    {"kind":"boot","phase":"agent.boot","ts":...,"duration_ms":...}

Aggregation lives in the `grim run instrument_report` script; this module
only emits.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterator

_INSTRUMENT_ENV = "GRIM_INSTRUMENT"
_FILE_ENV = "GRIM_INSTRUMENT_FILE"
try:
    _DEFAULT_FILE = Path.home() / ".grimoire" / "instrumentation.jsonl"
except (RuntimeError, OSError):  # HOME unset/odd: fall back, never crash
    _DEFAULT_FILE = Path("/tmp/grimoire-instrumentation.jsonl")

_lock = threading.Lock()
_handle: Any = None
_disabled = False
_current_session: str | None = None
_current_turn = 0
_totals: dict[str, dict[str, dict[str, float]]] = {}
_session_started: dict[str, float] = {}
_since_flush = 0
_FLUSH_EVERY = 32


def _enabled() -> bool:
    """On unless GRIM_INSTRUMENT is 0/off/empty; auto-off under pytest so the
    adapter suite stays silent (an explicit GRIM_INSTRUMENT=1 wins)."""
    raw = os.environ.get(_INSTRUMENT_ENV)
    if raw is not None and raw.strip() in ("", "0", "off", "false", "no", "none"):
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get(_INSTRUMENT_ENV) != "1":
        return False
    return True


def _ensure_enabled() -> bool:
    """First call caches the env verdict so a mid-session env flip can't
    half-enable; a False verdict also closes any open handle."""
    global _disabled
    if _disabled:
        return False
    if not _enabled():
        _disabled = True
        return False
    return True


def _open_handle() -> None:
    global _handle, _disabled
    if _disabled or _handle is not None:
        return
    path = Path(os.environ.get(_FILE_ENV, "") or _DEFAULT_FILE).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _handle = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered append
    except OSError:
        _disabled = True  # degrade: instrumenting must never break a session
        _handle = None


def _emit(record: dict[str, Any]) -> None:
    global _since_flush, _disabled
    if _disabled:
        return
    with _lock:
        if _handle is None:
            _open_handle()
        if _handle is None:
            return
        try:
            _handle.write(json.dumps(record, default=str) + "\n")
            _since_flush += 1
            if _since_flush >= _FLUSH_EVERY:
                _handle.flush()
                _since_flush = 0
        except OSError:
            _disabled = True


def _flush() -> None:
    with _lock:
        if _handle is not None:
            try:
                _handle.flush()
            except OSError:
                pass


atexit.register(_flush)


def _accumulate(session: str | None, phase: str, duration_ms: float) -> None:
    if session is None:
        return
    with _lock:
        bucket = _totals.setdefault(session, {}).setdefault(phase, {"count": 0.0, "ms": 0.0})
        bucket["count"] += 1.0
        bucket["ms"] += duration_ms


class _Span:
    """One timed region, created by span(). end() records it exactly once."""

    __slots__ = ("phase", "session", "turn", "_ts_start", "_started", "_fields", "_closed")

    def __init__(self, phase: str, session: str | None, turn: int, **fields: object) -> None:
        self.phase = phase
        self.session = session
        self.turn = turn
        self._ts_start = time.time()
        self._started = time.perf_counter()
        self._fields = dict(fields)
        self._closed = False

    def update(self, **fields: object) -> None:
        """Attach extra fields before the span closes (e.g. token counts read
        off the LLM response)."""
        self._fields.update(fields)

    def end(self, **fields: object) -> None:
        if self._closed:
            return
        self._closed = True
        self.update(**fields)
        duration_ms = (time.perf_counter() - self._started) * 1000.0
        record: dict[str, object] = {
            "kind": "span",
            "phase": self.phase,
            "ts": int(time.time() * 1000),
            "ts_start": int(self._ts_start * 1000),
            "duration_ms": round(duration_ms, 3),
        }
        if self.session is not None:
            record["session"] = self.session
        if self.turn:
            record["turn"] = self.turn
        record.update(self._fields)
        _accumulate(self.session, self.phase, duration_ms)
        _emit(record)


@contextlib.contextmanager
def span(phase: str, **fields: object) -> Iterator[_Span]:
    """Time a region; records on exit (an error field is added if it raised).
    Nested spans are fine — the report treats leaf phases as attributable and
    computes turn overhead as the remainder. A caller-supplied session/turn
    field overrides the module's current values (agent.turn passes turn)."""
    explicit_session = fields.pop("session", None)
    explicit_turn = fields.pop("turn", None)
    if not _ensure_enabled():
        turn = int(explicit_turn or 0) if explicit_turn is not None else 0
        sp = _Span(phase, None, turn, **fields)
        sp._closed = True  # inert: update/end are no-ops when disabled
        yield sp
        return
    with _lock:
        session = _current_session if explicit_session is None else str(explicit_session)
        turn = _current_turn if explicit_turn is None else int(explicit_turn or 0)
    sp = _Span(phase, session, turn, **fields)
    try:
        yield sp
    except BaseException as exc:
        sp.end(error=type(exc).__name__)
        raise
    else:
        sp.end()


def session_open(session_id: str, **fields: object) -> None:
    """Begin an agent session: stamps spans created from here on with
    session_id and starts the wall-clock + per-phase totals for the close
    record. Auto-closes a previously open session (e.g. /new's fresh id)."""
    global _current_session, _current_turn
    if not _ensure_enabled():
        return
    with _lock:
        previous = _current_session
        _current_session = session_id
        _current_turn = 0
        _session_started[session_id] = time.time()
        _totals.setdefault(session_id, {})
        closed = (
            _close_snapshot_locked(previous)
            if (previous is not None and previous != session_id)
            else None
        )
    if closed is not None:
        _emit(closed)
    _emit(
        {
            "kind": "session",
            "phase": "open",
            "session": session_id,
            "ts": int(time.time() * 1000),
            **fields,
        }
    )


def session_close(**fields: object) -> None:
    """End the current agent session, emitting its close record with wall
    time and per-phase totals (count + ms)."""
    global _current_session, _current_turn
    if not _ensure_enabled():
        return
    with _lock:
        session_id = _current_session
        _current_session = None
        _current_turn = 0
        if session_id is None:
            return
        record = _close_snapshot_locked(session_id)
    record.update(fields)
    _emit(record)


def _close_snapshot_locked(session_id: str) -> dict[str, object]:
    """Build (without emitting) a close record for session_id and drop its
    state — callers hold _lock. The wall clock starts at session_open."""
    started = _session_started.pop(session_id, time.time())
    wall_ms = (time.time() - started) * 1000.0
    totals = _totals.pop(session_id, {})
    return {
        "kind": "session",
        "phase": "close",
        "session": session_id,
        "ts": int(time.time() * 1000),
        "wall_ms": round(wall_ms, 3),
        "totals": totals,
    }


def turn_begin() -> int:
    """Advance the per-session turn counter (GrimAgent.run's loop) and return
    the new number; spans created inside the turn carry it."""
    global _current_turn
    if not _ensure_enabled():
        return 0
    with _lock:
        _current_turn += 1
        return _current_turn
