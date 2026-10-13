"""The previous-session snippet for `grim-agent --continue` (adapter slice).

Moved verbatim out of context.py (which had grown past the 500-line file
budget); behavior is unchanged. GrimAgent.run renders the result as the
grim_previous_session template var.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grim.adapter.context import _trim_text

_TRAJ_POINTER = Path.home() / ".grimoire" / "last-trajectory"
_SNIPPET_LIMIT = 2_000  # total chars of the previous-session snippet
_SNIPPET_MESSAGES = 5  # tail messages to include
_SNIPPET_CONTENT = 240  # per-message content cap


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
