"""GrimAgent — subclasses mini-swe-agent's InteractiveAgent to prepopulate
the system prompt with strong FTS matches for the raw task text, run once
before the agent takes its first turn.

Rationale: the post-Phase-3 protocol made `grim find` conditional, not
mandatory (build plan §6, §8 Risks: "Optional find misses/duplicates
existing scripts"). This closes part of that gap mechanically — a
high-confidence match is surfaced up front, the same way the seed list
already is, instead of depending on the agent choosing to search.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any

from minisweagent.agents.interactive import InteractiveAgent, console
from minisweagent.exceptions import UserInterruption

from grim import db
from grim.adapter import context, trace
from grim.adapter.bang import install_bang_expansion
from grim.adapter.completer import install_grim_completer
from grim.adapter.display import action_renderables, grim_actions, reasoning_text
from grim.adapter.environment import GrimEnvironment
from grim.adapter.slash import GRIM_CLI_VERBS, run_slash_command
from grim.adapter.tools import lang_enum

# Operator-authored system-prompt extension, the agent-harness analogue of a
# global ~/.claude or ~/.pi instruction file. Lives under grim's home dir (the
# same ~/.grimoire that holds the DB and config.toml). Read fresh each run so
# edits take effect without a reinstall.
SYSTEM_PROMPT_PATH = Path.home() / ".grimoire" / "system.md"

# bm25() in FTS5 is more-negative-is-better (https://sqlite.org/fts5.html
# #the_bm25_function); sign-flipped here so higher = closer, matching
# verbs/_shared.py's similar_scripts convention. This threshold is
# deliberately strict — the whole point is a short, high-confidence list,
# not a lower-recall echo of `grim find`. Tune alongside real usage data.
STRONG_MATCH_THRESHOLD = 6.0
STRONG_MATCH_LIMIT = 3

_SCOPE_HEX_CHARS = 12  # truncation length of the root-commit hash in scope
_SCOPE_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_ROOT_HASH_RE = re.compile(r"^[0-9a-f]{40}$")


def _current_scope() -> str:
    """The enclosing repo's scope id — its oldest root-commit hash truncated
    to 12 hex (worktree/clone-stable) — or 'global' outside a git repo. An
    adapter-owned duplicate of verbs/_shared.py's default_scope, like
    _match_query below (slices don't share internals, root CLAUDE.md §2;
    flagged in ABSTRACTIONS.md). The read-only pre-run git queries are this
    slice's sole sanctioned subprocess use (see the amended D7 invariant in
    adapter/CLAUDE.md); any git hiccup degrades to 'global', never crashes.
    `--max-parents=0` lists root commits, newest first, so the last line is
    the oldest root even after unrelated-history merges.
    Ref: https://git-scm.com/docs/git-rev-list#Documentation/git-rev-list.txt---max-parentsltnumbergt
    """
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
        )
        roots = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "global"
    toplevel = top.stdout.strip()
    if top.returncode != 0 or not toplevel:
        return "global"
    tokens = roots.stdout.split() if roots.returncode == 0 else []
    oldest = tokens[-1] if tokens else ""
    if not _ROOT_HASH_RE.match(oldest):
        # unborn HEAD (fresh `git init`): the path hash, matching verbs' shape
        oldest = hashlib.sha256(toplevel.encode()).hexdigest()
    scope = oldest[:_SCOPE_HEX_CHARS]
    assert _SCOPE_ID_RE.match(scope), "a detected repo yields a 12-hex scope id"
    assert scope != "global", "a detected repo never maps to 'global'"
    return scope


def _match_query(text: str) -> str:
    """Tokenize into an FTS5 MATCH expression (OR'd, quoted tokens) — a
    small, adapter-owned duplicate of verbs/_shared.py's fts_match_query.
    Slices don't import each other's internals (root CLAUDE.md §2); this
    is a few lines, not worth a shared-kernel promotion."""
    tokens = re.findall(r"[A-Za-z0-9_]+", text)
    return " OR ".join(f'"{t}"' for t in tokens)


def strong_matches(task: str) -> list[dict[str, str]]:
    """Strict FTS5 hits for `task` against the script library — empty if
    nothing clears STRONG_MATCH_THRESHOLD, never partial/best-effort
    matches. Uses find.py's column weighting (name > description > body)
    since this is effectively a pre-run, high-confidence `grim find`.
    Restricted to the working repo's scope plus 'global' — a hard filter,
    not find's soft tiering, because this list is injected into the system
    prompt unprompted: a cross-repo "strong match" there is exactly the
    distraction it would otherwise mechanize."""
    match_query = _match_query(task)
    if not match_query:
        return []
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT s.name, s.language, s.description, "
            "-bm25(script_fts, 10.0, 5.0, 1.0) AS score "
            "FROM script_fts JOIN script s ON s.id = script_fts.rowid "
            "WHERE script_fts MATCH ? AND s.archived = 0 "
            "AND s.scope IN (?, 'global') "
            "AND -bm25(script_fts, 10.0, 5.0, 1.0) >= ? "
            "ORDER BY score DESC LIMIT ?",
            (match_query, _current_scope(), STRONG_MATCH_THRESHOLD, STRONG_MATCH_LIMIT),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) <= STRONG_MATCH_LIMIT, "strong_matches must never exceed its own limit"
    return [
        {"name": row["name"], "language": row["language"], "description": row["description"]}
        for row in rows
    ]


# Bounded collection (§1): far above the bundled roster's size, so hitting it
# means the seeded flag is being set outside `grim init` — worth failing loud.
SEEDED_ROSTER_LIMIT = 100


def seeded_roster() -> list[dict[str, str]]:
    """The library's live seed roster (name + description), for the system
    prompt: rows still seeded=1 and unarchived, in seeding order. Read from
    the DB — not from the seeds slice's bundled list (slices don't import
    each other, root CLAUDE.md §2) — so the prompt reflects what this
    library actually holds: a GRIM_BASE_SEEDS subset (an eval control arm),
    a human takeover (seeded=0), or an archived seed all drop out here,
    never advertising a script that run() would reject."""
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT name, description FROM script "
            "WHERE seeded = 1 AND archived = 0 ORDER BY id LIMIT ?",
            (SEEDED_ROSTER_LIMIT,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) < SEEDED_ROSTER_LIMIT, "seed roster at its cap — seeded flag leaking?"
    result = [{"name": row["name"], "description": row["description"]} for row in rows]
    assert all(r["name"] for r in result), "every seeded row carries a name"
    return result


def user_prompt_extension(path: Path | None = None) -> str:
    """Operator instructions appended to the system prompt, read from
    ~/.grimoire/system.md. Absent or unreadable -> "" (external input:
    degrade, never crash). Stripped, so an empty/whitespace-only file renders
    nothing under system_template's truthiness guard."""
    prompt_path = path if path is not None else SYSTEM_PROMPT_PATH
    assert prompt_path is not None, "system-prompt path resolves to a value"
    if not prompt_path.is_file():
        return ""
    try:
        text = prompt_path.read_text()
    except OSError:
        return ""
    result = text.strip()
    assert isinstance(result, str), "extension is always a string"
    return result


# --- library recall (--continue) --------------------------------------------
# On --continue the agent is warm-started with its own recently-valuable
# scripts (name + description, never bodies), so it reuses accumulated tools
# instead of rewriting them. Sibling of strong_matches(): a direct, terse
# library read stashed into extra_template_vars for instance_template to render
# — placed by the task (not the system prompt) so recency bias weighs it.

RECALL_LIMIT_DEFAULT = 10  # scripts to recall; GRIM_RECALL_LIMIT overrides
RECALL_LIMIT_MAX = 50  # hard ceiling — a bounded collection, never unbounded (§1)
RECALL_MIN_SUCCESS = 0.5  # skip mostly-failing scripts; a recalled tool should work
RECALL_POOL_FACTOR = 3  # recency-bounded candidate pool = limit * this, then value-ranked


def recall_enabled() -> bool:
    """True when --continue set GRIM_RECALL (or it was exported directly). Off
    by default, so a normal run's prompt is byte-for-byte unchanged."""
    return bool(os.environ.get("GRIM_RECALL"))


def recall_limit() -> int:
    """How many scripts to recall: GRIM_RECALL_LIMIT clamped to [1, MAX], else
    the default. External input -> validate, never trust (constitution §3)."""
    raw = os.environ.get("GRIM_RECALL_LIMIT")
    if raw is None:
        return RECALL_LIMIT_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return RECALL_LIMIT_DEFAULT
    return max(1, min(value, RECALL_LIMIT_MAX))


def rank_recall(candidates: list[dict[str, Any]], k: int) -> list[dict[str, str]]:
    """Pure: pick the k most-valuable scripts (most runs, then most iterated),
    then order them by last_used ASCENDING so the most-recently-used lands LAST
    — nearest the task, where recency bias weighs heaviest. Terse (name +
    description only); no DB, no clock, deterministic for a given input."""
    assert k >= 1, "recall limit must be positive"
    by_value = sorted(candidates, key=lambda c: (c["runs"], c["iterations"]), reverse=True)
    chosen = by_value[:k]
    chosen.sort(key=lambda c: c["last_used"])  # ISO-ish text sorts chronologically
    result = [{"name": str(c["name"]), "description": str(c["description"])} for c in chosen]
    assert len(result) <= k, "recall never exceeds its limit"
    return result


# Ranked so the SQL LIMIT bounds a recency pool; rank_recall re-ranks by value.
_RECALL_SQL = (
    "SELECT s.name, s.description, h.runs, "
    "(SELECT MAX(version) FROM script_version WHERE script_id = s.id) AS iterations, "
    "h.last_used "
    "FROM script s JOIN script_health h ON h.id = s.id "
    "WHERE s.archived = 0 AND s.seeded = 0 AND h.runs > 0 AND h.success_rate >= ? "
    "ORDER BY h.last_used DESC LIMIT ?"
)


def recent_library(limit: int) -> list[dict[str, str]]:
    """The agent's own recently-valuable scripts, ranked for recall: a
    recency-bounded pool from the library (non-seeded, non-archived, run at
    least once, not mostly-failing), value-ranked via rank_recall."""
    assert limit >= 1, "recall limit must be positive"
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    try:
        pool = conn.execute(
            _RECALL_SQL, (RECALL_MIN_SUCCESS, limit * RECALL_POOL_FACTOR)
        ).fetchall()
    finally:
        conn.close()
    return rank_recall([dict(row) for row in pool], limit)


# /new's exit_status sentinel: distinguishes "the human wants a fresh
# session" from a real Submitted/LimitsExceeded/TimeExceeded exit, so
# GrimAgent.run()'s wrapper loop knows to restart super().run() with a new
# task instead of returning to its own caller.
_NEW_SESSION_EXIT_STATUS = "GrimNewSession"
_NEW_SESSION_COMMAND = "/new"

_SLASH_HINT = (
    "[dim]grim verbs available as /commands: "
    + " ".join(f"/{verb}" for verb in GRIM_CLI_VERBS)
    + f" · {_NEW_SESSION_COMMAND} <task> starts a fresh session[/dim]"
)


class GrimAgent(InteractiveAgent):
    """Same contract as InteractiveAgent; run() loops over InteractiveAgent's
    own run() (rather than looping inside it) so /new can break all the way
    out to a fresh top-level call — system_template can reference
    {{ grim_strong_matches }} / the operator's {{ grim_user_prompt }}, and
    (on --continue) instance_template can render {{ grim_recent_library }},
    exactly as on a normal first run."""

    def run(self, task: str = "", **kwargs: object) -> dict[str, object]:
        assert isinstance(task, str), "task is a string"
        env = self._grim_env()
        model = str(getattr(getattr(self.model, "config", None), "model_name", "") or "")
        trace.session_open(env.session_id, task=task[:500], model=model)
        try:
            return self._run_loop(task, env, model, **kwargs)
        finally:
            trace.session_close()

    def _run_loop(
        self, task: str, env: GrimEnvironment, model: str, **kwargs: object
    ) -> dict[str, object]:
        # Enable @/: completion and !slug execute-and-substitute on mini's
        # prompt sessions (no-op without a TTY); installed once, but
        # session_id is re-read live so /new's fresh session is honored.
        install_grim_completer()
        install_bang_expansion(lambda: env.session_id)
        console.print(_SLASH_HINT)
        while True:
            turn = trace.turn_begin()
            with trace.span("agent.turn", turn=turn):
                self.extra_template_vars["grim_strong_matches"] = strong_matches(task)
                self.extra_template_vars["grim_user_prompt"] = user_prompt_extension()
                # Declared and enforced from the same value: env.cwd is the
                # pin every action executes from (environment.py exports it
                # as $GRIM_CWD), so the rendered {{ grim_cwd }} can never
                # disagree with where scripts actually run.
                self.extra_template_vars["grim_cwd"] = env.cwd
                # The enabled language set, from the SAME function that builds
                # the write/list schema enums — prompt and schema cannot drift.
                # Without this the prose undersells granted languages (the
                # language-sweep confound this line exists to remove).
                self.extra_template_vars["grim_languages"] = lang_enum()
                # The live seed roster, not static prose: an eval arm seeded
                # without `shell` (GRIM_BASE_SEEDS) must not have the prompt
                # advertising it — that would contaminate the control.
                self.extra_template_vars["grim_seeds"] = seeded_roster()
                if recall_enabled():
                    self.extra_template_vars["grim_recent_library"] = recent_library(recall_limit())
                    self.extra_template_vars["grim_previous_session"] = (
                        context.previous_session_snippet()
                    )
                result = super().run(task, **kwargs)
            assert isinstance(result, dict), "InteractiveAgent.run always returns a dict"
            if result.get("exit_status") != _NEW_SESSION_EXIT_STATUS:
                return result
            env.session_id = str(uuid.uuid4())
            task = str(result.get("submission", ""))
            trace.session_open(env.session_id, task=task[:500], model=model)

    def _grim_env(self) -> GrimEnvironment:
        env = self.env
        assert isinstance(env, GrimEnvironment), "GrimAgent always runs with GrimEnvironment"
        return env

    def add_messages(self, *messages: dict[str, Any]) -> list[dict[str, Any]]:
        """Extend InteractiveAgent.add_messages: assistant turns carrying grim
        actions render via display.py (submit -> Markdown, verbs -> their
        render_command line) instead of mini's raw-JSON tool-call fence.
        History is untouched: grim-rendered messages are appended verbatim via
        DefaultAgent.add_messages, skipping ONLY InteractiveAgent's printing —
        its add_messages does nothing but print, then delegate (interactive.py)."""
        added: list[dict[str, Any]] = []
        for message in messages:
            actions = grim_actions(message)
            if actions is None:
                added += super().add_messages(message)
            else:
                self._print_grim_turn(message, actions)
                added += super(InteractiveAgent, self).add_messages(message)
        assert len(added) == len(messages), "every message is recorded exactly once"
        assert self.messages[len(self.messages) - len(messages) :] == list(messages), (
            "history holds the verbatim messages"
        )
        return added

    def _print_grim_turn(self, message: dict[str, Any], actions: list[dict[str, Any]]) -> None:
        """Header + reasoning exactly as InteractiveAgent prints them
        (interactive.py's add_messages), then each action's rich rendering."""
        assert message.get("role") == "assistant", "only assistant turns render here"
        assert actions, "a grim turn carries at least one action"
        console.print(
            f"\n[red][bold]mini-swe-agent[/bold] (step [bold]{self.n_calls}[/bold], "
            f"[bold]${self.cost:.2f}[/bold]):[/red]\n",
            end="",
            highlight=False,
        )
        if text := reasoning_text(message):
            console.print(text, highlight=False, markup=False)
        for action in actions:
            for renderable in action_renderables(action):
                console.print(renderable)

    def _prompt_and_handle_slash_commands(self, prompt: str, *, _multiline: bool = False) -> str:
        """Extend InteractiveAgent's own /h /m /y /c /u handling with two
        grim-only commands: /new <task> (below) and grim's CLI verbs as
        /verb ... (slash.py) — both dispatched here, never sent to the
        model."""
        with trace.span("agent.human_wait"):
            user_input = super()._prompt_and_handle_slash_commands(prompt, _multiline=_multiline)
        assert isinstance(user_input, str), "prompt input is always a string"
        if user_input == _NEW_SESSION_COMMAND or user_input.startswith(_NEW_SESSION_COMMAND + " "):
            new_task = user_input[len(_NEW_SESSION_COMMAND) :].strip()
            if not new_task:
                console.print(f"[yellow]usage: {_NEW_SESSION_COMMAND} <task>[/yellow]")
                return self._prompt_and_handle_slash_commands(prompt, _multiline=_multiline)
            raise UserInterruption(
                {
                    "role": "exit",
                    "content": "",
                    "extra": {"exit_status": _NEW_SESSION_EXIT_STATUS, "submission": new_task},
                }
            )
        output = run_slash_command(user_input, self._grim_env().session_id)
        if output is not None:
            console.print(output)
            return self._prompt_and_handle_slash_commands(prompt, _multiline=_multiline)
        return user_input
