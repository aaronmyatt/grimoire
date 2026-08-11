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

from grim.adapter import context, trace
from grim.adapter.tools import GRIM_TOOLS, render_command

_TOOL_NAMES = frozenset(t["function"]["name"] for t in GRIM_TOOLS)
# name -> its required-argument list, precomputed from the schemas so
# validation and the schema can never drift apart.
_REQUIRED: dict[str, list[str]] = {
    t["function"]["name"]: t["function"]["parameters"]["required"] for t in GRIM_TOOLS
}
# name -> property schemas, precomputed from GRIM_TOOLS so argument
# type-checking and the schemas handed to the model can never drift apart.
_PROPERTIES: dict[str, dict[str, Any]] = {
    t["function"]["name"]: t["function"]["parameters"]["properties"] for t in GRIM_TOOLS
}

# Cap how much of a mismatched value is echoed back to the model.
_MAX_SHOWN = 60


def _expected_type(prop: dict[str, Any]) -> str:
    """Human phrasing of a property schema's type for FormatError messages,
    e.g. 'a string' or 'an array of strings'."""
    ptype = prop.get("type", "value")
    if ptype == "array":
        items = prop.get("items", {}).get("type", "value")
        return f"an array of {items}s"
    article = "an" if ptype.startswith(("a", "e", "i", "o", "u")) else "a"
    return f"{article} {ptype}"


def _type_ok(expected: str, value: object) -> bool:
    """Whether `value` satisfies a JSON-schema `type` from our tool schemas.
    `bool` is rejected for numeric types (it subclasses int)."""
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return True  # unknown schema type: don't guess, let the verb decide


def _type_violations(tool: str, args: dict[str, Any]) -> list[str]:
    """Human-readable argument type mismatches, e.g. ``'query' must be a
    string, got list (['a', 'b'])``. Unknown keys are tolerated — the verbs
    ignore extras, so only declared properties are checked."""
    props = _PROPERTIES[tool]
    out: list[str] = []
    for key, value in args.items():
        prop = props.get(key)
        if prop is None or _type_ok(prop.get("type", ""), value):
            continue
        shown = repr(value)
        if len(shown) > _MAX_SHOWN:
            shown = shown[: _MAX_SHOWN - 3] + "..."
        out.append(f"{key!r} must be {_expected_type(prop)}, got {type(value).__name__} ({shown})")
    return out


class GrimToolcallModel(LitellmModel):
    """Same contract as LitellmModel; only the tool set and action parsing
    differ. Selected via `model.model_class` in grimoire.yaml."""

    def _query(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        # Overridden wholesale (not super()) because the parent hardcodes
        # tools=[BASH_TOOL] with no seam to swap it. Auth-error hint kept
        # for parity with the parent's UX; the context budget (adapter/
        # context.py) compacts and retries around the call, and a context
        # error that survives compaction becomes a FormatError pointing at
        # `grim-agent --continue` instead of a raw provider 400.
        try:
            with trace.span("llm.completion", model=self.config.model_name) as sp:
                response = context.completion(
                    self.config.model_name,
                    messages,
                    GRIM_TOOLS,
                    self.config.model_kwargs | kwargs,
                )
                sp.update(**_usage_fields(response))
            return response
        except litellm.exceptions.AuthenticationError as e:
            e.message += (
                " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            )
            raise
        except (
            litellm.exceptions.ContextWindowExceededError,
            litellm.exceptions.BadRequestError,
        ) as e:
            if not context.is_context_error(e):
                raise
            raise self._format_error(
                "Context budget exhausted: this conversation no longer fits the model "
                "window even after compaction. Wrap up and submit now, or start a "
                "fresh run with `grim-agent --continue` to warm-start from the "
                "last session.",
                None,
            ) from e

    def _parse_actions(self, response: Any) -> list[dict[str, Any]]:
        """Timed wrapper — llm.parse spans cover validation + lowering."""
        with trace.span("llm.parse"):
            return self._parse_actions_impl(response)

    def _parse_actions_impl(self, response: Any) -> list[dict[str, Any]]:
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
                f"Unknown tool {name!r}. Available: {sorted(_TOOL_NAMES)}. If you meant to run "
                f"a library script (not a built-in tool), call run(name={name!r}) instead.",
                finish_reason,
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

        violations = _type_violations(name, args)
        if violations:
            raise self._format_error(
                f"Tool {name!r} has argument type error(s): {'; '.join(violations)}.",
                finish_reason,
            )
        assert isinstance(tool_call.id, str), "a tool call must carry an id for result correlation"
        # `command` is a display string mini's InteractiveAgent reads
        # unconditionally; GrimEnvironment dispatches from tool/args.
        return {
            "tool": name,
            "args": args,
            "tool_call_id": tool_call.id,
            "command": render_command(name, args),
        }

    def _format_error(self, error: str, finish_reason: Any) -> FormatError:
        content = Template(self.config.format_error_template, undefined=StrictUndefined).render(
            error=error, actions=[], has_tool_calls=True, finish_reason=finish_reason
        )
        return FormatError(
            {"role": "user", "content": content, "extra": {"interrupt_type": "FormatError"}}
        )


def _usage_fields(response: Any) -> dict[str, object]:
    """Token counts for the llm.completion span — best-effort: a provider
    that omits usage yields {} (external input, degrade, never crash)."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    fields: dict[str, object] = {}
    for attr in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, attr, None)
        if isinstance(value, int):
            fields[attr] = value
    return fields
