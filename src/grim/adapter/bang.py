"""`!slug` execute-and-substitute — Phase 2 of the @-command plan (see
completer.py's Phase 1: `@`/`:` completion).

When a human types `!slug` as a standalone token in any interactive prompt,
it is replaced with `grim run slug`'s captured output before the text
becomes a message. Wired in by wrapping mini's own prompt_toolkit sessions
(no fork), the same sessions `install_grim_completer` already reaches into —
that covers every place a human composes text: the initial task, human-mode
commands, interrupt comments, confirm/reject replies, and post-submit new
tasks.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from grim.adapter.environment import _invoke

# Standalone token only: `!` must be whitespace-bounded so "wow!" or
# "e.g.!foo" never match, matching the slug shape from GRIM_TOOLS
# (^[a-z][a-z0-9_]{2,63}$).
_BANG_TOKEN = re.compile(r"(?<!\S)!([a-z][a-z0-9_]{2,63})(?!\S)")

# Bounded: every loop has an explicit upper bound (root CLAUDE.md §3).
MAX_BANGS_PER_MESSAGE = 10


def expand_bangs(text: str, runner: Callable[[str], str]) -> str:
    """Replace up to MAX_BANGS_PER_MESSAGE `!slug` tokens in `text` with
    `runner(slug)`'s result; any beyond the cap are left as literal text.
    Pure over the injected runner — text with no bang tokens is returned
    unchanged (never mutated without a match)."""
    assert isinstance(text, str), "text is a string"
    assert callable(runner), "runner must be callable"
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        if count >= MAX_BANGS_PER_MESSAGE:
            return match.group(0)
        count += 1
        return runner(match.group(1))

    result = _BANG_TOKEN.sub(_replace, text)
    assert count <= MAX_BANGS_PER_MESSAGE, "expansion never exceeds its own bound"
    assert isinstance(result, str), "expansion always yields a string"
    return result


def _run_slug(slug: str, session_id: str) -> str:
    """Execute `slug` via the same in-process `cli.main` dispatch the agent's
    own `run` tool uses (no new subprocess — adapter/CLAUDE.md's hard
    invariant). An unknown slug isn't special-cased: whatever `grim run`
    prints (including its own error) is exactly what gets substituted."""
    assert isinstance(slug, str) and slug, "slug is a non-empty string"
    assert isinstance(session_id, str), "session_id is a string"
    text, _ = _invoke(["run", slug], "", session_id)
    return text


# Sessions already wrapped, tracked by identity rather than an attribute on
# the (third-party, strictly-typed) PromptSession object itself.
_WRAPPED: set[int] = set()


def install_bang_expansion(session_id_fn: Callable[[], str]) -> None:
    """Wrap `.prompt` on mini's shared prompt sessions so every human
    input is passed through expand_bangs before it becomes a message.
    `session_id_fn` is called fresh on every prompt (never captured once),
    so a session_id that changes later in the same process (GrimAgent's
    /new) is always honored. Idempotent per session object, mirroring
    install_grim_completer."""
    from minisweagent.agents.utils import prompt_user  # noqa: PLC0415 -- extra may be absent

    assert callable(session_id_fn), "session_id_fn must be callable"
    sessions = [prompt_user.prompt_session, prompt_user._multiline_prompt_session]
    task_session = getattr(prompt_user, "_task_prompt_session", None)
    if task_session is not None:
        sessions.append(task_session)
    for session in sessions:
        if id(session) in _WRAPPED:
            continue
        original_prompt = session.prompt

        def wrapped_prompt(
            *args: object, __original: Callable[..., str] = original_prompt, **kwargs: object
        ) -> str:
            return expand_bangs(
                __original(*args, **kwargs), lambda slug: _run_slug(slug, session_id_fn())
            )

        # Deliberate monkeypatch of a third-party session's bound method, the
        # same class of operation install_grim_completer does for
        # `.completer` — mypy's method-assign check doesn't distinguish a
        # legitimate instance override from an accidental one.
        session.prompt = wrapped_prompt  # type: ignore[method-assign]
        _WRAPPED.add(id(session))
    assert all(id(s) in _WRAPPED for s in sessions), "both sessions wrapped"
