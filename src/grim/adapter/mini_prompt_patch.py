"""Re-apply grim's submit-on-Enter initial-task prompt to the installed
minisweagent (self-healing; mirrors the patch_mini_enter_submit library
script, same markers, so either one is a no-op once the other has run).

Why this exists: grim-agent's first "What do you want to do?" prompt comes
from mini-swe-agent (minisweagent/run/mini.py -> _multiline_prompt), where a
plain Enter only inserts a newline and submitting needs Esc-then-Enter — a
forgotten combo leaves the session waiting, and the :name/@path helpers are
lost in that first interaction. The fix adds a submit-on-Enter _prompt_task()
and points mini's initial task at it. But minisweagent is a PyPI dependency
with no repo checkout, so a `uv tool install/upgrade grimoire` re-extracts the
pristine wheel and silently reverts the venv-only patch (the .bak-* backups
survive because uv removes only RECORD-listed files). The launcher calls
ensure_mini_task_prompt_patch() on every start so the fix survives reinstalls.

Fail-soft by design: an unrecognized minisweagent layout (future version)
prints a warning and leaves the harness alone — never raises, never bricks
grim-agent.
"""

from __future__ import annotations

import glob
import os
import shutil
import sys
import time

# ---------------------------------------------------------------------------
# Anchored replacements, byte-for-byte from patch_mini_enter_submit so the
# markers agree and either application is idempotent. (old, new, marker)
# ---------------------------------------------------------------------------

_PROMPT_USER_IMPORTS = (
    """from prompt_toolkit.formatted_text.html import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import PromptSession
""",
    """from prompt_toolkit.enums import DEFAULT_BUFFER
from prompt_toolkit.filters import has_focus
from prompt_toolkit.formatted_text.html import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import PromptSession
""",
    "from prompt_toolkit.enums import DEFAULT_BUFFER",
)

# Anchor: the end of _multiline_prompt() (its closing toolbar + parens). We
# insert the new submit-on-Enter machinery right after it.
_PROMPT_USER_ANCHOR = """            "Search history: <b fg='yellow' bg='black'>Ctrl+R</b>"
        ),
    )
"""

_PROMPT_USER_INSERT = """

# --- Submit-on-Enter prompt for the initial task (mini.py) -----------------
# Plain Enter submits the task (Slack-style) so there is no forgotten
# Esc+Enter key combo; Esc+Enter / Alt+Enter inserts a newline for multiline
# tasks. An empty buffer is ignored on Enter so a stray keystroke can't fire
# off an empty task. History navigation and Ctrl+R search still work.
_task_prompt_bindings = KeyBindings()
_task_prompt_focus = has_focus(DEFAULT_BUFFER)


@_task_prompt_bindings.add("enter", filter=_task_prompt_focus)
def _task_prompt_enter(event) -> None:
    buffer = event.current_buffer
    if buffer.text.strip():
        buffer.validate_and_handle()


@_task_prompt_bindings.add("escape", "enter", filter=_task_prompt_focus)
def _task_prompt_newline(event) -> None:
    # Esc+Enter; terminals also send Esc\\r for Alt+Enter, so both work.
    event.current_buffer.insert_text("\\n")


_task_prompt_session = PromptSession(
    history=_history, multiline=True, key_bindings=_task_prompt_bindings
)


def _prompt_task() -> str:
    \"\"\"Prompt for the initial task: plain Enter submits; Esc+Enter (or
    Alt+Enter) inserts a newline.\"\"\"
    return _task_prompt_session.prompt(
        "",
        bottom_toolbar=HTML(
            "Submit: <b fg='yellow' bg='black'>Enter</b> | "
            "New line: <b fg='yellow' bg='black'>Esc+Enter / Alt+Enter</b> | "
            "Navigate history: <b fg='yellow' bg='black'>Arrow Up/Down</b> | "
            "Search history: <b fg='yellow' bg='black'>Ctrl+R</b>"
        ),
    )
"""
_PROMPT_USER_MARKER = "_task_prompt_bindings = KeyBindings()"

_MINI_IMPORT = (
    "from minisweagent.agents.utils.prompt_user import _multiline_prompt\n",
    "from minisweagent.agents.utils.prompt_user import _prompt_task\n",
    "import _prompt_task",
)

_MINI_CALL = (
    "        run_task = _multiline_prompt()\n",
    "        run_task = _prompt_task()\n",
    "run_task = _prompt_task()",
)


class _PatchFailure(Exception):
    """The installed minisweagent no longer matches the patch anchors."""


def _package_dir() -> str | None:
    """Directory of the installed minisweagent package, or None if absent."""
    try:
        import minisweagent  # noqa: PLC0415 -- optional agent extra
    except ModuleNotFoundError:
        return None
    path = os.path.dirname(os.path.abspath(minisweagent.__file__))
    return path or None


def _patch_file(path: str, old: str, new: str, marker: str) -> bool:
    """Replace `old` with `new` in `path`, guarded by `marker` (idempotent).
    Raises _PatchFailure when the layout no longer matches exactly once."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if marker in text:
        return False  # already applied
    if text.count(old) != 1:
        raise _PatchFailure(f"expected exactly one occurrence of {old[:60]!r} in {path}")
    patched = text.replace(old, new)
    compile(patched, path, "exec")  # syntax-check before writing
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(patched)
    return True


def _insert_after(path: str, anchor: str, insertion: str, marker: str) -> bool:
    """Insert `insertion` right after the single `anchor` in `path`."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if marker in text:
        return False  # already applied
    if text.count(anchor) != 1:
        raise _PatchFailure(f"expected exactly one anchor {anchor[:60]!r} in {path}")
    patched = text.replace(anchor, anchor + insertion)
    compile(patched, path, "exec")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(patched)
    return True


def _backup_once(path: str) -> None:
    """Back up the pristine file once; a reinstall wipes the venv, but the
    .bak-* orphans survive and keep the pre-patch file around for diffing."""
    if glob.glob(f"{path}.bak-*"):
        return
    shutil.copyfile(path, f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}")


def ensure_mini_task_prompt_patch(pkg_dir: str | None = None) -> bool:
    """Apply the submit-on-Enter initial-task prompt to minisweagent.

    True if this call applied it; False when it was already applied or could
    not be (a warning is printed to stderr). Never raises.
    """
    if pkg_dir is None:
        pkg_dir = _package_dir()
    if not pkg_dir or not os.path.isdir(pkg_dir):
        print(
            "warning: minisweagent package not found; cannot ensure the "
            "submit-on-Enter task prompt",
            file=sys.stderr,
        )
        return False
    prompt_user = os.path.join(pkg_dir, "agents", "utils", "prompt_user.py")
    mini_py = os.path.join(pkg_dir, "run", "mini.py")
    missing = [p for p in (prompt_user, mini_py) if not os.path.exists(p)]
    if missing:
        print(
            f"warning: minisweagent layout changed, missing {missing}; cannot "
            "ensure the submit-on-Enter task prompt",
            file=sys.stderr,
        )
        return False
    try:
        _backup_once(prompt_user)
        _backup_once(mini_py)
        changed = 0
        changed += _patch_file(prompt_user, *_PROMPT_USER_IMPORTS)
        changed += _insert_after(
            prompt_user, _PROMPT_USER_ANCHOR, _PROMPT_USER_INSERT, _PROMPT_USER_MARKER
        )
        changed += _patch_file(mini_py, *_MINI_IMPORT)
        changed += _patch_file(mini_py, *_MINI_CALL)
        if changed:
            print(f"grim-agent: applied submit-on-Enter task prompt to {pkg_dir}", file=sys.stderr)
        return changed > 0
    except _PatchFailure as exc:
        print(f"warning: could not apply the submit-on-Enter task prompt: {exc}", file=sys.stderr)
        return False
