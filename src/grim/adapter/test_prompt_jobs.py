"""Goldens for the system prompt's long-lived-work guidance: what it tells the
agent about jobs that outlive a run, and about waiting. Pinned so a change to
that guidance shows up as a diff here (root CLAUDE.md §4;
docs/features/03-dashboard.md, "The prompt teaches surfaces").
Whitespace is normalised: the goldens pin the words, not the line breaks."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")  # ships with the optional `adapter` extra

GRIMOIRE_YAML = Path(__file__).with_name("grimoire.yaml")

_LONG_LIVED_WORK = (
    "Long-lived work goes in a tmux job, never a blocking run: a normal grim run is time-"
    "capped and will kill a server, watcher, or other never-exiting process at the "
    'timeout. Start those with run(name="tmux_start"): the job is a window in tmux '
    'session "grim" and its output is logged to a file. Then run(name="tmux_list") shows '
    'your jobs, run(name="tmux_read") reads one, run(name="tmux_send") types into it, and'
    ' run(name="tmux_stop") ends it. A one-shot task that simply takes a while is fine as'
    " a normal run."
)
_NEVER_WAIT = (
    "Never wait in the foreground: no sleep-then-check, and no search that walks / or ~ "
    "(it hits the run timeout and returns nothing). When something takes minutes (a "
    "deploy, a crawl, a build), start it with tmux_start and block on its end with "
    'run(name="tmux_wait", args=["grim-JOB", "SECONDS"]), which returns the moment the '
    "job ends. If it will outlast your time, submit with how to check on it (tmux_list, "
    "tmux_read). If observations show a time budget, finish inside it."
)
_STATIC_ROSTER_JOBS = (
    "- run tmux_start … start a long-lived job (server, watcher, build, REPL) as a tmux "
    "window - run tmux_list list the jobs started by tmux_start: alive or dead with exit "
    "code, age - run tmux_read … read a job's output: its last lines, or its log and exit"
    " code once dead - run tmux_send … type one line into a live job and press Enter (a "
    "prompt, a REPL, a TUI) - run tmux_stop … stop a job started by tmux_start and free "
    "its name - run tmux_wait … block until a job ends (signal grim-JOB) instead of "
    "polling"
)


def _template() -> str:
    """The system template with every run of whitespace folded to one space."""
    # Ref: https://pyyaml.org/wiki/PyYAMLDocumentation (safe_load)
    template = yaml.safe_load(GRIMOIRE_YAML.read_text())["agent"]["system_template"]
    assert isinstance(template, str), "system_template is a text block"
    assert template.strip(), "system_template is never empty"
    return " ".join(template.split())


def test_prompt_says_where_long_lived_work_goes() -> None:
    assert _LONG_LIVED_WORK in _template()


def test_prompt_says_how_to_wait() -> None:
    assert _NEVER_WAIT in _template()


def test_static_roster_lists_the_job_seeds() -> None:
    assert _STATIC_ROSTER_JOBS in _template()


def test_prompt_no_longer_names_the_retired_background_seeds() -> None:
    template = _template()
    for retired in ("run_bg", "list_bg", "stop_bg"):
        assert retired not in template, f"{retired} is retired; the prompt must not teach it"
    assert "tmux_wait" in template and "no sleep-then-check" in template
