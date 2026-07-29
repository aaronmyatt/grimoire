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
"""

from __future__ import annotations

from typing import Any

import litellm
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
from rich.console import Console

console = Console(highlight=False)


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
    for chunk in stream:
        _print_delta(chunk)
        yield chunk
    console.print()  # newline once the turn finishes


def _print_delta(chunk: Any) -> None:
    delta = chunk.choices[0].delta if chunk.choices else None
    if delta is None:
        return
    text = getattr(delta, "reasoning_content", None) or getattr(delta, "content", None)
    if text:
        console.print(text, end="", highlight=False, markup=False)
