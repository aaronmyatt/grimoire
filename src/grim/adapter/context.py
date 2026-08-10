"""Context-length awareness for grim-agent (adapter slice).

Prevents long sessions from dying to "maximum context length exceeded": a
per-model token budget (litellm.get_model_info window minus an output
reserve), proactive compaction of the message list at _query time, and a
retry-once with aggressive compaction when the provider still rejects the
call. All in the adapter slice: the kernel's message store is never
mutated — only the list handed to litellm.completion is rewritten.
GRIM_COMPACT=0 restores the pre-awareness behavior byte-for-byte.

Env knobs (validated/clamped, never trusted): GRIM_COMPACT (1|0, on),
GRIM_COMPACT_AT (0.75), GRIM_COMPACT_KEEP (6), GRIM_MAX_TOOL_OUTPUT (4096),
GRIM_COMPACT_MODEL (unset = no LLM tail summary; the grim-native summary
always runs). The ladder is cheap-first: trim tool results -> drop stale
results -> replace the old span with a grim-native summary (task +
recently-used scripts + recent executions). Every step degrades, never
crashes.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import litellm

from grim import db

_COMPACT_ENV = "GRIM_COMPACT"
_COMPACT_AT_ENV = "GRIM_COMPACT_AT"
_COMPACT_KEEP_ENV = "GRIM_COMPACT_KEEP"
_MAX_TOOL_OUTPUT_ENV = "GRIM_MAX_TOOL_OUTPUT"
_COMPACT_MODEL_ENV = "GRIM_COMPACT_MODEL"

_DEFAULT_WINDOW = 128_000
_DEFAULT_OUTPUT = 8_000
_OVERHEAD = 2_000  # system prompt + tool schemas + per-message slack
_SUMMARY_LIMIT = 2_000  # chars of the grim-native summary block
_LLM_SUMMARY_TAIL = 6_000  # chars of conversation handed to GRIM_COMPACT_MODEL

_TRAJ_POINTER = Path.home() / ".grimoire" / "last-trajectory"
_SNIPPET_LIMIT = 2_000  # total chars of the previous-session snippet
_SNIPPET_MESSAGES = 5  # tail messages to include
_SNIPPET_CONTENT = 240  # per-message content cap

# Authoritative prompt-token count from the most recent response (the model's
# own count); None until the first completion reports usage.
_LAST_PROMPT_TOKENS: int | None = None
_MODEL_INFO: dict[str, dict[str, int]] = {}


def _env_flag(name: str) -> bool:
    raw = os.environ.get(name)
    return raw is None or raw.strip().lower() not in ("", "0", "off", "false", "no", "none")


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(lo, min(value, hi))


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(lo, min(value, hi))


def enabled() -> bool:
    """Master switch: GRIM_COMPACT=0 (or off/no/false) disables the whole
    budget, restoring the pre-awareness call path byte-for-byte."""
    return _env_flag(_COMPACT_ENV)


def compact_at() -> float:
    """Budget fraction that triggers proactive compaction, clamped to
    [0.5, 0.95] so the ceiling is never pushed into the output reserve."""
    return _env_float(_COMPACT_AT_ENV, 0.75, 0.5, 0.95)


def keep() -> int:
    """Verbatim turns kept at the tail, clamped to [1, 20]."""
    return _env_int(_COMPACT_KEEP_ENV, 6, 1, 20)


def max_tool_output() -> int:
    """Bytes cap for tool results kept in history, clamped to [512, 65536]."""
    return _env_int(_MAX_TOOL_OUTPUT_ENV, 4096, 512, 65536)


def compact_model() -> str | None:
    """GRIM_COMPACT_MODEL, or None when unset (LLM tail summary off)."""
    raw = os.environ.get(_COMPACT_MODEL_ENV, "").strip()
    return raw or None


def _model_info(model: str) -> dict[str, int]:
    """Per-model (window, reserve), cached; unmapped/unknown models fall back
    to conservative defaults (external input: degrade, never crash)."""
    if model not in _MODEL_INFO:
        try:
            info = litellm.get_model_info(model)
            window = int(info.get("max_input_tokens") or _DEFAULT_WINDOW)
            reserve = int(info.get("max_output_tokens") or _DEFAULT_OUTPUT)
        except Exception:
            window, reserve = _DEFAULT_WINDOW, _DEFAULT_OUTPUT
        _MODEL_INFO[model] = {"window": window, "reserve": reserve}
    return _MODEL_INFO[model]


def budget_for(model: str) -> int:
    """Tokens usable for the prompt: window minus an output reserve and
    system/tools overhead, never below half the window."""
    info = _model_info(model)
    budget = info["window"] - info["reserve"] - _OVERHEAD
    return max(budget, info["window"] // 2)


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate (4 chars/token + per-message/tool-call overhead)
    used only until the provider reports an authoritative count."""
    total = 0
    for message in messages:
        total += 4
        content = message.get("content")
        if isinstance(content, str):
            total += max(1, len(content) // 4)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    total += max(1, len(block["text"]) // 4)
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            total += 12 * len(tool_calls)
    return total


def _remember(response: Any) -> None:
    """Record the provider's authoritative prompt-token count (best-effort)."""
    global _LAST_PROMPT_TOKENS
    usage = getattr(response, "usage", None)
    value = getattr(usage, "prompt_tokens", None)
    if isinstance(value, int):
        _LAST_PROMPT_TOKENS = value


def prompt_tokens(messages: list[dict[str, Any]], model: str) -> int:
    """Tokens the current prompt would consume: the model's own last count
    when known (authoritative and ~the same size), else the estimate; the max
    of the two catches a single oversized new turn."""
    estimated = _estimate_tokens(messages)
    if _LAST_PROMPT_TOKENS is None:
        return estimated
    return max(_LAST_PROMPT_TOKENS, estimated)


def _trim_text(text: str, limit: int) -> str:
    """Keep the head and tail of a string with an explicit truncation marker
    — a bounded buffer is a first-class budget, never silently dropped. The
    result is guaranteed to fit within `limit` characters."""
    if len(text) <= limit:
        return text
    marker = "\n…[truncated {} chars]…\n".format(len(text) - limit)
    room = max(1, limit - len(marker))
    half = max(1, room // 2)
    head, tail = text[:half], text[-half:]
    return head + marker + tail


def _compact_old_span(
    out: list[dict[str, Any]],
    cut: int,
    summary_text: str | None,
    notes: list[str],
) -> list[dict[str, Any]]:
    """Replace the old span (between the first user message and `cut`) with a
    single summary message, or drop its stale tool results when no summary is
    available. System + first user task always survive."""
    first_user = next((i for i, m in enumerate(out) if m.get("role") == "user"), 0)
    span = out[first_user + 1 : cut]
    if summary_text:
        summary = {
            "role": "user",
            "content": summary_text,
            "extra": {"interrupt_type": "CompactionSummary"},
        }
        summarized = sum(1 for m in span if m.get("role") == "assistant")
        notes.append(f"summarized {summarized} earlier turns")
        return out[: first_user + 1] + [summary] + out[cut:]
    kept: list[dict[str, Any]] = []
    dropped = 0
    for i, m in enumerate(out):
        if i < first_user + 1 or i >= cut or m.get("role") != "tool":
            kept.append(m)
        else:
            dropped += 1
    if dropped:
        notes.append(f"dropped {dropped} stale tool results")
    return kept


def compact_messages(
    messages: list[dict[str, Any]],
    *,
    keep_turns: int,
    max_output: int,
    summary_text: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Apply the bounded compaction ladder to a COPY of `messages`; the input
    is never mutated. Returns (new_messages, human note). Cheap first:
    1) trim tool results to max_output; 2) drop stale tool results older than
    the last keep_turns turns; 3) replace the old span with one synthetic
    user message carrying summary_text (system + first user task always
    survive). No-op when there is nothing old enough to compact."""
    out = [dict(m) for m in messages]
    notes: list[str] = []
    trimmed = False
    for i, m in enumerate(out):
        content = m.get("content")
        if m.get("role") == "tool" and isinstance(content, str) and len(content) > max_output:
            out[i] = {**m, "content": _trim_text(content, max_output)}
            trimmed = True
    if trimmed:
        notes.append("trimmed tool results")
    assistants = [i for i, m in enumerate(out) if m.get("role") == "assistant"]
    if not assistants:
        return out, "; ".join(notes) or "no-op"
    cut = assistants[-keep_turns] if len(assistants) > keep_turns else None
    if cut is None:
        return out, "; ".join(notes) or "no-op"
    if not out[1:cut]:
        return out, "; ".join(notes) or "no-op"
    return _compact_old_span(out, cut, summary_text, notes), "; ".join(notes) or "no-op"


def _first_task(messages: list[dict[str, Any]]) -> str:
    """The first user message's text — the session task, which must survive
    compaction verbatim (the summary just echoes it)."""
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content
    return ""


def _recent_scripts(limit: int) -> list[str]:
    """Recently-used scripts (name: description) for the grim-native summary —
    a small adapter-owned read, bounded; DB hiccups degrade to []."""
    try:
        conn = db.connect()
        try:
            rows = conn.execute(
                "SELECT s.name, s.description FROM script s "
                "JOIN script_health h ON h.id = s.id "
                "WHERE s.archived = 0 AND s.seeded = 0 AND h.runs > 0 "
                "ORDER BY h.last_used DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return []
    return [f"{row[0]}: {row[1]}" for row in rows]


def _recent_executions(limit: int) -> list[str]:
    """Recent script executions ("name argv -> exit N") for the summary;
    scoped to GRIM_SESSION when the harness has one set, else global. Bounded;
    DB hiccups degrade to []."""
    session = os.environ.get("GRIM_SESSION")
    sql = (
        "SELECT s.name, e.argv, e.exit_code FROM execution e "
        "JOIN script_version sv ON sv.id = e.script_version_id "
        "JOIN script s ON s.id = sv.script_id "
    )
    params: list[object] = []
    if session:
        sql += "WHERE e.session_id = ? "
        params.append(session)
    sql += "ORDER BY e.started_at DESC, e.id DESC LIMIT ?"
    params.append(limit)
    try:
        conn = db.connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return []
    out: list[str] = []
    for row in rows:
        argv = _trim_text(str(row[1] or ""), 80).replace("\n", " ")
        out.append(f"{row[0]} {argv} -> exit {row[2]}")
    return out


def _render_tail(messages: list[dict[str, Any]], limit: int) -> str:
    """Plain-text rendering of the conversation for the optional LLM summary."""
    parts: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            parts.append(f"[{role}] {content}")
        elif isinstance(content, list):
            texts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ]
            if texts:
                parts.append(f"[{role}] {' '.join(texts)}")
    return "\n".join(parts)[:limit]


def _llm_summary(messages: list[dict[str, Any]]) -> str:
    """Best-effort LLM summary of the conversation via GRIM_COMPACT_MODEL;
    any failure degrades to '' — compaction must never crash on a summarizer
    hiccup."""
    model = compact_model()
    if model is None:
        return ""
    tail = _render_tail(messages, _LLM_SUMMARY_TAIL)
    if not tail:
        return ""
    try:
        response = litellm.completion(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize this conversation in at most 120 words. Keep concrete "
                        "facts: scripts/tools used, files touched, decisions, current "
                        "state, and next steps."
                    ),
                },
                {"role": "user", "content": tail},
            ],
            max_tokens=200,
        )
        text = response.choices[0].message.content
    except Exception:
        return ""
    if not isinstance(text, str) or not text.strip():
        return ""
    return "LLM summary: " + _trim_text(text.strip(), 800)


def build_summary_text(messages: list[dict[str, Any]]) -> str:
    """The grim-native summary block placed where the compacted turns were:
    session task + recently-used scripts + recent executions (+ optional LLM
    summary). Bounded; empty when nothing is available (the caller then falls
    back to the drop-stale-results rung of the ladder)."""
    parts: list[str] = []
    task = _first_task(messages)
    if task:
        parts.append(f"Session task: {_trim_text(task, 300)}")
    scripts = _recent_scripts(5)
    if scripts:
        parts.append("Recently used scripts: " + "; ".join(scripts))
    executions = _recent_executions(5)
    if executions:
        parts.append("Last executions: " + "; ".join(executions))
    llm = _llm_summary(messages)
    if llm:
        parts.append(llm)
    if not parts:
        return ""
    body = "\n".join(parts)[:_SUMMARY_LIMIT]
    return (
        "Context compaction: the earlier turns of this conversation were "
        f"replaced by this summary.\n{body}"
    )


def is_context_error(exc: BaseException) -> bool:
    """Whether an exception is a provider context-window rejection — the only
    error class compaction can meaningfully retry against."""
    if isinstance(exc, litellm.exceptions.ContextWindowExceededError):
        return True
    if isinstance(exc, litellm.exceptions.BadRequestError):
        message = str(exc).lower()
        return "context" in message and (
            "length" in message or "token" in message or "window" in message
        )
    return False


def completion(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    kwargs: dict[str, Any],
) -> Any:
    """litellm.completion wrapped with the context-length budget: proactively
    compacts when prompt tokens cross compact_at() of the window; on a
    context-window error compacts aggressively (keep=2) and retries once; a
    failure that survives retry propagates for the caller to surface a
    grim-branded hint. GRIM_COMPACT=0 bypasses all of it."""
    assert model and isinstance(messages, list), "completion needs a model and a message list"
    if not enabled():
        return litellm.completion(model=model, messages=messages, tools=tools, **kwargs)
    threshold = int(budget_for(model) * compact_at())
    sent = messages
    if prompt_tokens(messages, model) > threshold:
        sent, _ = compact_messages(
            messages,
            keep_turns=keep(),
            max_output=max_tool_output(),
            summary_text=build_summary_text(messages),
        )
    try:
        response = litellm.completion(model=model, messages=sent, tools=tools, **kwargs)
        _remember(response)
        return response
    except Exception as exc:
        if not is_context_error(exc):
            raise
        retried, _ = compact_messages(
            messages,
            keep_turns=min(keep(), 2),
            max_output=max_tool_output(),
            summary_text=build_summary_text(messages),
        )
        if retried == messages:
            raise  # nothing left to compact: let the caller surface the hint
        response = litellm.completion(model=model, messages=retried, tools=tools, **kwargs)
        _remember(response)
        return response


def _snippet_body(data: dict[str, Any]) -> list[str]:
    """The previous-session snippet's lines: exit status + tail messages."""
    parts: list[str] = []
    info = data.get("info")
    if isinstance(info, dict) and info.get("exit_status"):
        parts.append(f"Previous run ended: {info.get('exit_status')}")
    messages = data.get("messages")
    if isinstance(messages, list):
        for message in messages[-_SNIPPET_MESSAGES:]:
            if not isinstance(message, dict):
                continue
            text = _message_text(message)
            if text:
                parts.append(f"[{message.get('role')}] {_trim_text(text, _SNIPPET_CONTENT)}")
    return parts


def previous_session_snippet(pointer: Path | None = None) -> str:
    """Bounded tail of the previous agent run for --continue's
    grim_previous_session template var: exit status + the last few messages
    from the newest trajectory file (the adapter's own transcript channel —
    no DB access). Missing/unreadable/stale pointer -> '' (degrade, never
    crash; external input is validated, never trusted)."""
    path = pointer if pointer is not None else _TRAJ_POINTER
    try:
        raw = path.read_text().strip()
    except OSError:
        return ""
    if not raw:
        return ""
    traj = Path(raw)
    if not str(traj).endswith(".traj.json") or not traj.is_file():
        return ""
    try:
        data = json.loads(traj.read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return "\n".join(_snippet_body(data))[:_SNIPPET_LIMIT]


def _message_text(message: dict[str, Any]) -> str:
    """Plain text of a trajectory message: str content or text blocks."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return " ".join(texts)
    return ""
