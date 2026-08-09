"""Tests for adapter/mini_prompt_patch.py — the self-healing re-application
of the submit-on-Enter initial-task prompt to the installed minisweagent."""

from __future__ import annotations

from pathlib import Path

from grim.adapter import mini_prompt_patch

# The pristine minisweagent 2.4.x layout the patch anchors target (trimmed to
# the seam: prompt_user.py + mini.py).
_PRISTINE_PROMPT_USER = """from prompt_toolkit.formatted_text.html import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import PromptSession

from minisweagent import global_config_dir

_history = FileHistory(global_config_dir / "interactive_history.txt")
prompt_session = PromptSession(history=_history)
_multiline_prompt_session = PromptSession(history=_history, multiline=True)


def _multiline_prompt() -> str:
    return _multiline_prompt_session.prompt(
        "",
        bottom_toolbar=HTML(
            "Submit message: <b fg='yellow' bg='black'>Esc, then Enter</b> | "
            "Navigate history: <b fg='yellow' bg='black'>Arrow Up/Down</b> | "
            "Search history: <b fg='yellow' bg='black'>Ctrl+R</b>"
        ),
    )
"""

_PRISTINE_MINI = """from minisweagent.agents.utils.prompt_user import _multiline_prompt


def main() -> None:
    if run_task is UNSET:
        run_task = _multiline_prompt()
"""


def _fake_pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "minisweagent"
    (pkg / "agents" / "utils").mkdir(parents=True)
    (pkg / "run").mkdir()
    (pkg / "agents" / "utils" / "prompt_user.py").write_text(_PRISTINE_PROMPT_USER)
    (pkg / "run" / "mini.py").write_text(_PRISTINE_MINI)
    return pkg


def test_applies_patch_to_pristine_layout(tmp_path: Path) -> None:
    pkg = _fake_pkg(tmp_path)

    assert mini_prompt_patch.ensure_mini_task_prompt_patch(str(pkg)) is True

    prompt_user = (pkg / "agents" / "utils" / "prompt_user.py").read_text()
    mini = (pkg / "run" / "mini.py").read_text()
    assert mini_prompt_patch._PROMPT_USER_MARKER in prompt_user
    assert "_prompt_task" in prompt_user
    assert "from minisweagent.agents.utils.prompt_user import _prompt_task" in mini
    assert "        run_task = _prompt_task()" in mini
    assert "run_task = _multiline_prompt()" not in mini


def test_patch_is_idempotent(tmp_path: Path) -> None:
    pkg = _fake_pkg(tmp_path)
    assert mini_prompt_patch.ensure_mini_task_prompt_patch(str(pkg)) is True
    prompt_user_before = (pkg / "agents" / "utils" / "prompt_user.py").read_text()

    assert mini_prompt_patch.ensure_mini_task_prompt_patch(str(pkg)) is False

    assert (pkg / "agents" / "utils" / "prompt_user.py").read_text() == prompt_user_before


def test_fails_soft_on_unknown_layout(tmp_path: Path) -> None:
    pkg = _fake_pkg(tmp_path)
    (pkg / "agents" / "utils" / "prompt_user.py").write_text(
        "# a future minisweagent layout with no matching anchors\n"
    )

    assert mini_prompt_patch.ensure_mini_task_prompt_patch(str(pkg)) is False
    assert (pkg / "agents" / "utils" / "prompt_user.py").read_text().startswith("# a future")


def test_missing_package_returns_false(tmp_path: Path) -> None:
    assert mini_prompt_patch.ensure_mini_task_prompt_patch(str(tmp_path / "nope")) is False
