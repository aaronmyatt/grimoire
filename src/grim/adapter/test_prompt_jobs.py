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
    "Long-lived work goes in the background, never a blocking run: a normal grim run is "
    "time-capped and will kill a server, watcher, or other never-exiting process at the "
    'timeout. Start those with run(name="run_bg") (its output is logged to a file), then '
    'run(name="list_bg") to check on them and run(name="stop_bg") to end them. A one-shot'
    " task that simply takes a while is fine as a normal run."
)
_NEVER_WAIT = (
    "Never wait in the foreground: no sleep-then-check over ~30s, and no search that "
    "walks / or ~ (it hits the run timeout and returns nothing). When something takes "
    "minutes (a deploy, a crawl, a build), start it with run_bg and submit with how to "
    "check on it. If observations show a time budget, finish inside it."
)
_STATIC_ROSTER_JOBS = (
    "- run run_bg … start a program as a detached background job (for long-lived "
    "processes) - run list_bg list background jobs started by run_bg with pid and running"
    " state - run stop_bg … stop a background job started by run_bg by name"
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
