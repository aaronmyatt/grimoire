"""Tests for adapter/streaming_model.py's GrimStreamingTextbasedModel.

No network: litellm.completion is monkeypatched to return chunks built
from litellm's own real types (ModelResponseStream/StreamingChoices/
Delta), then the REAL litellm.stream_chunk_builder reconstructs them —
only the network call itself is faked, so the reconstruction/cost-calc
pipeline is exercised for real, per Phase 2b's plan.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("minisweagent")  # adapter/ needs the optional `adapter` extra

import litellm  # noqa: E402
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices  # noqa: E402
from minisweagent.agents import interactive  # noqa: E402

from grim.adapter import streaming_model  # noqa: E402
from grim.adapter.streaming_model import GrimStreamingTextbasedModel  # noqa: E402

_GRIM_BLOCK = '```grim\ngrim find "x"\n```'


def _chunk(
    *, content: str | None = None, reasoning: str | None = None, finish_reason: str | None = None
) -> ModelResponseStream:
    delta_kwargs: dict[str, Any] = {}
    if content is not None:
        delta_kwargs["content"] = content
    if reasoning is not None:
        delta_kwargs["reasoning_content"] = reasoning
    return ModelResponseStream(
        id="fake-1",
        model="deepseek/deepseek-v4-flash",
        choices=[
            StreamingChoices(index=0, delta=Delta(**delta_kwargs), finish_reason=finish_reason)
        ],
    )


_FAKE_CHUNKS = [
    _chunk(reasoning="thinking..."),
    _chunk(content=_GRIM_BLOCK, finish_reason="stop"),
]


def _fake_completion(**kwargs: Any) -> Any:
    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}
    return iter(_FAKE_CHUNKS)


def _model() -> GrimStreamingTextbasedModel:
    # action_regex matches grimoire.yaml's real config (a ```grim``` fence,
    # not mini's default ```mswea_bash_command``` one).
    return GrimStreamingTextbasedModel(  # type: ignore[no-untyped-call]
        model_name="deepseek/deepseek-v4-flash",
        cost_tracking="ignore_errors",
        action_regex=r"```grim\s*\n(.*?)\n```",
    )


def test_query_reconstructs_the_streamed_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(litellm, "completion", _fake_completion)

    message = _model().query([{"role": "user", "content": "find x"}])

    assert message["content"] == _GRIM_BLOCK


def test_query_prints_reasoning_and_content_deltas_live(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(litellm, "completion", _fake_completion)

    _model().query([{"role": "user", "content": "find x"}])

    out = capsys.readouterr().out
    assert "thinking..." in out
    assert 'grim find "x"' in out


def test_query_parses_the_grim_action_from_reconstructed_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(litellm, "completion", _fake_completion)

    message = _model().query([{"role": "user", "content": "find x"}])

    assert message["extra"]["actions"] == [{"command": 'grim find "x"'}]


def test_uses_interactive_agents_shared_console() -> None:
    # Regression: a second, independent Console() instance corrupts
    # InteractiveAgent's spinner (see streaming_model.py's docstring) —
    # this must always be the exact same object, not just the same class.
    assert streaming_model.console is interactive.console


def test_query_stops_the_active_status_spinner(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: printing onto the same line a transient Live spinner
    # owns gets wiped every refresh and erased entirely on exit (see
    # streaming_model.py's docstring — confirmed via a real pty capture).
    # The spinner must be stopped before we print our first token.
    monkeypatch.setattr(litellm, "completion", _fake_completion)

    with interactive.console.status("Waiting for the LM to respond..."):
        assert interactive.console._live_stack, "sanity: a Live must be active here"
        active_live = interactive.console._live_stack[-1]
        _model().query([{"role": "user", "content": "find x"}])
        assert not active_live.is_started
        assert not interactive.console._live_stack, "stop() must pop itself off the live stack"


def test_reasoning_and_content_are_separated_by_a_newline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(litellm, "completion", _fake_completion)

    _model().query([{"role": "user", "content": "find x"}])

    out = capsys.readouterr().out
    assert "thinking...\n```grim" in out, f"reasoning ran into content with no break: {out!r}"


def test_authentication_error_gets_the_helpful_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**kwargs: Any) -> Any:
        raise litellm.exceptions.AuthenticationError(
            message="bad key", llm_provider="deepseek", model="deepseek/deepseek-v4-flash"
        )

    monkeypatch.setattr(litellm, "completion", _raise)

    with pytest.raises(litellm.exceptions.AuthenticationError, match="mini-extra config set"):
        _model()._query([{"role": "user", "content": "hi"}])
