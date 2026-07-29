"""GrimStreamingTextbasedModel — cosmetic-only streaming variant of
LitellmTextbasedModel (build plan Phase 2b). Prints reasoning/content
deltas live via rich as they arrive, then reconstructs a normal
ModelResponse with litellm.stream_chunk_builder so cost calculation and
action parsing (inherited, unchanged) work exactly as before — the
turn-based interaction loop is untouched, this only removes the blank
pause while a turn renders.

Known wart, not solved here: InteractiveAgent.add_messages() prints the
complete assistant message again once query() returns, so the streamed
text renders once live and once more as a block. Fixing that means
touching InteractiveAgent's print behavior — out of scope for a
Model-layer-only change.

Deliberately imports InteractiveAgent's `console` instead of creating a
new one: Rich's Live/Status display (the "Waiting for the LM to
respond..." spinner, refreshed 12.5x/sec) only coordinates cursor
movement correctly with prints going through the *same* Console object.
A second, independent Console instance writing to the same terminal
corrupts the spinner's redraw math — confirmed live: output rendered as
rapid, overlapping, in-place garbage instead of scrolling text. This is
a real (if unusual) coupling to the agent layer, forced by Rich's
design, not a layering mistake.
"""

from __future__ import annotations

from typing import Any

import litellm
from minisweagent.agents.interactive import console as console  # re-exported for tests
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel


class GrimStreamingTextbasedModel(LitellmTextbasedModel):
    def _query(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        try:
            stream = litellm.completion(
                model=self.config.model_name,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                **(self.config.model_kwargs | kwargs),
            )
            chunks = list(_print_and_collect(stream))
        except litellm.exceptions.AuthenticationError as e:
            e.message += (
                " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            )
            raise e
        response = litellm.stream_chunk_builder(chunks, messages=messages)
        assert response is not None, (
            "stream_chunk_builder returned None for a non-empty chunk stream"
        )
        assert chunks, "a completed stream must have yielded at least one chunk"
        return response


def _print_and_collect(stream: Any) -> Any:
    reasoning_open = False
    for chunk in stream:
        reasoning_open = _print_delta(chunk, reasoning_open=reasoning_open)
        yield chunk
    console.print()  # newline once the turn finishes


def _print_delta(chunk: Any, *, reasoning_open: bool) -> bool:
    """Prints this chunk's delta text, returning whether a reasoning
    block is still open (so the next content delta knows to break onto
    its own line first, instead of running "...done thinking```grim")."""
    delta = chunk.choices[0].delta if chunk.choices else None
    if delta is None:
        return reasoning_open
    reasoning = getattr(delta, "reasoning_content", None)
    content = getattr(delta, "content", None)
    if reasoning:
        console.print(reasoning, end="", highlight=False, markup=False)
        return True
    if content:
        if reasoning_open:
            console.print()  # separate reasoning from the final answer
        console.print(content, end="", highlight=False, markup=False)
    return False
