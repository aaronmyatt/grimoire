"""GrimToolcallModel — mini-swe-agent's LitellmModel wired to grim's tool
set instead of its single hardcoded `bash` tool (D6 revised → native
tool-calling).

Two overrides are all it takes: `_query` hands the model `GRIM_TOOLS`, and
`_parse_actions` validates each returned tool call and turns it into a
`{tool, args, tool_call_id}` action for GrimEnvironment. The inherited
`format_observation_messages` already renders results as `role: "tool"`
messages keyed by `tool_call_id`, so nothing else changes.

Deterministic stop: finishing is the `submit` tool call (handled in
environment.py), never a string scanned from output.
Ref: https://docs.litellm.ai/docs/completion/function_call
"""

from __future__ import annotations

import json
from typing import Any

import litellm
from jinja2 import StrictUndefined, Template
from minisweagent.exceptions import FormatError
from minisweagent.models.litellm_model import LitellmModel

from grim.adapter.tools import GRIM_TOOLS

_TOOL_NAMES = frozenset(t["function"]["name"] for t in GRIM_TOOLS)
# name -> its required-argument list, precomputed from the schemas so
# validation and the schema can never drift apart.
_REQUIRED: dict[str, list[str]] = {
    t["function"]["name"]: t["function"]["parameters"]["required"] for t in GRIM_TOOLS
}


class GrimToolcallModel(LitellmModel):
    """Same contract as LitellmModel; only the tool set and action parsing
    differ. Selected via `model.model_class` in grimoire.yaml."""

    def _query(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        # Overridden wholesale (not super()) because the parent hardcodes
        # tools=[BASH_TOOL] with no seam to swap it. Auth-error hint kept
        # for parity with the parent's UX.
        try:
            return litellm.completion(
                model=self.config.model_name,
                messages=messages,
                tools=GRIM_TOOLS,
                **(self.config.model_kwargs | kwargs),
            )
        except litellm.exceptions.AuthenticationError as e:
            e.message += (
                " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            )
            raise

    def _parse_actions(self, response: Any) -> list[dict[str, Any]]:
        """Validate the model's tool calls and lower them to grim actions.
        Any deviation (no call, unknown tool, bad/missing args) raises
        FormatError so the agent loop feeds a precise correction back —
        the deterministic, structured replacement for regex flakiness."""
        choice = response.choices[0]
        tool_calls = choice.message.tool_calls or []
        if not tool_calls:
            raise self._format_error(
                "No tool call in the response. Every response MUST call a grim tool.",
                choice.finish_reason,
            )
        return [self._parse_one(tc, choice.finish_reason) for tc in tool_calls]

    def _parse_one(self, tool_call: Any, finish_reason: Any) -> dict[str, Any]:
        name = tool_call.function.name
        if name not in _TOOL_NAMES:
            raise self._format_error(
                f"Unknown tool {name!r}. Available: {sorted(_TOOL_NAMES)}.", finish_reason
            )
        try:
            args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError as e:
            raise self._format_error(
                f"Tool {name!r} arguments are not valid JSON: {e}.", finish_reason
            ) from e
        if not isinstance(args, dict):
            raise self._format_error(
                f"Tool {name!r} arguments must be a JSON object.", finish_reason
            )
        missing = [key for key in _REQUIRED[name] if key not in args]
        if missing:
            raise self._format_error(
                f"Tool {name!r} is missing required argument(s): {', '.join(missing)}.",
                finish_reason,
            )
        assert isinstance(tool_call.id, str), "a tool call must carry an id for result correlation"
        return {"tool": name, "args": args, "tool_call_id": tool_call.id}

    def _format_error(self, error: str, finish_reason: Any) -> FormatError:
        content = Template(self.config.format_error_template, undefined=StrictUndefined).render(
            error=error, actions=[], has_tool_calls=True, finish_reason=finish_reason
        )
        return FormatError(
            {"role": "user", "content": content, "extra": {"interrupt_type": "FormatError"}}
        )
