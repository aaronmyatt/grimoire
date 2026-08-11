"""Native tool-calling surface for the grim agent — the structured-args
replacement for the text-based `parse_grim` fenced-block grammar (D6,
revised). Two exports:

- ``GRIM_TOOLS``: OpenAI-style function schemas for the six agent-facing
  verbs plus the terminal ``submit`` control, handed to the model as its
  entire tool set (``toolcall_model.py``).
- ``tool_call_to_argv``: maps one validated data-verb call to a
  ``cli.main`` argv (+ optional stdin body). Pure text/data in, data out —
  no mini-swe-agent import, mirroring ``parse.py``'s old role.

Submission is no longer a magic string scanned from output: the model
finishes by calling ``submit``, which ``environment.py`` turns into a
deterministic stop. ``submit`` is a control tool, not a library verb — the
six-verb data surface (root CLAUDE.md §2, build plan D11/D12) is unchanged.
"""

from __future__ import annotations

import os
import shlex
from typing import Any

SUBMIT_TOOL_NAME = "submit"


_BUILTIN_PAIR = frozenset({"python", "bash"})


def lang_enum() -> list[str]:
    """The `--lang` choices the model may propose: the (subsettable)
    builtins plus extended languages enabled in $GRIM_LANGUAGES.
    $GRIM_BASE_LANGUAGES mirrors exec/dispatch.py's knob — unset -> both
    builtins, '' -> none (solo-language sweep arms) — including the same
    never-empty fail-safe, so schema, prompt, and write gate always agree.
    A deliberate copy of dispatch's env parses (ledgered) — the adapter
    never imports a slice; the authoritative platform gate stays in
    write_script. Public: feeds BOTH the write/list tool schemas below and
    grimoire.yaml's prompts (GrimAgent stashes it as grim_languages)."""
    raw_extended = os.environ.get("GRIM_LANGUAGES", "")
    enabled = {tok.strip() for tok in raw_extended.split(",") if tok.strip()}
    raw_base = os.environ.get("GRIM_BASE_LANGUAGES")
    base = (
        _BUILTIN_PAIR
        if raw_base is None
        else {tok.strip() for tok in raw_base.split(",") if tok.strip()} & _BUILTIN_PAIR
    )
    assert base <= _BUILTIN_PAIR, "base only ever narrows the builtin pair"
    languages = sorted(base | enabled) or sorted(_BUILTIN_PAIR)  # dispatch's fail-safe, mirrored
    assert languages, "the enum is never empty"
    return languages


# The six data verbs `tool_call_to_argv` knows how to invoke. `submit` is
# deliberately absent — it never reaches cli.main (environment.py stops on
# it), so mapping it is a bug, asserted against below.
_DATA_VERBS = frozenset({"write", "update", "read", "list", "find", "run"})


def _str(value: object) -> str:
    """JSON numbers arrive as int/float; cli.py re-parses argv with
    type=int/float, so everything crosses the boundary as a string.

    Also the last line of defense against a model that slips a non-string
    (e.g. a list) into a string-typed field: argv must stay ``list[str]`` or
    ``render_command``'s ``shlex.join`` raises TypeError ("expected string
    object, got 'list'"). toolcall_model.py rejects those calls with a
    FormatError before dispatch; coercion here keeps the pure mapper total."""
    return str(value)


def _opt(flag: str, value: object) -> list[str]:
    """``['--flag', '<value>']`` when the optional arg is set, else ``[]``."""
    return [] if value is None else [flag, _str(value)]


def tool_call_to_argv(tool: str, args: dict[str, Any]) -> tuple[list[str], str | None]:
    """Map one validated data-verb tool call to ``(argv, stdin)`` for
    ``cli.main``. Callers validate the tool name and required args first
    (toolcall_model.py), so required keys are indexed directly and only the
    verb set is asserted here."""
    assert tool in _DATA_VERBS, f"tool_call_to_argv maps data verbs only, got {tool!r}"
    match tool:
        case "write":
            argv = [
                "write",
                "--name",
                _str(args["name"]),
                "--lang",
                _str(args["lang"]),
                "--desc",
                _str(args["desc"]),
            ]
            argv += _opt("--parent", args.get("parent")) + _opt("--scope", args.get("scope"))
            return argv, args["body"]
        case "update":
            return (
                ["update", _str(args["name"]), "--changelog", _str(args["changelog"])],
                args["body"],
            )
        case "read":
            argv = ["read", *([_str(args["name"])] if args.get("name") else [])]
            argv += _opt("--exec", args.get("exec")) + _opt("--page", args.get("page"))
            return argv, None
        case "list":
            argv = ["list", *_opt("--scope", args.get("scope")), *_opt("--lang", args.get("lang"))]
            argv += _opt("--limit", args.get("limit")) + _opt("--offset", args.get("offset"))
            return argv, None
        case "find":
            return ["find", _str(args["query"]), *_opt("--limit", args.get("limit"))], None
        case "run":
            argv = ["run", _str(args["name"]), *_opt("--timeout", args.get("timeout"))]
            argv += _opt("--head", args.get("head")) + _opt("--tail", args.get("tail"))
            script_args = args.get("args") or []
            # A bare string where the schema wants an array is a single script
            # arg, not a sequence of characters to split.
            if isinstance(script_args, str):
                script_args = [script_args]
            if script_args:
                argv += ["--", *(_str(a) for a in script_args)]
            return argv, args.get("stdin")
    raise AssertionError(f"unhandled data verb {tool!r}")  # unreachable past the guard


def render_command(tool: str, args: dict[str, Any]) -> str:
    """A one-line human-readable rendering of a tool call. Every action
    must carry a `command` string: mini's InteractiveAgent reads it
    unconditionally (for the confirm-mode prompt and whitelist matching),
    exactly as mini's own tool-calling model attaches one. Display only —
    GrimEnvironment dispatches from `tool`/`args`, not this string."""
    if tool == SUBMIT_TOOL_NAME:
        return "submit"
    argv, _ = tool_call_to_argv(tool, args)
    return "grim " + shlex.join(argv)


def _fn(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    """Assemble one OpenAI-style function-tool schema."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}

# The model's entire tool set. Descriptions carry the "how" that the
# text-based system prompt used to spell out, since the fenced-block
# mechanics are gone (D6 revised).
GRIM_TOOLS: list[dict[str, Any]] = [
    _fn(
        "write",
        "Create a new script in the library. Name it verb_noun; the description is a "
        "search-index entry for your future self's `find` queries, not documentation. "
        "python or bash, plus any languages you enabled in config (off by default). "
        "The body is the full script source.",
        {
            "name": {**_STR, "description": "slug, ^[a-z][a-z0-9_]{2,63}$"},
            "lang": {
                "type": "string",
                "enum": lang_enum(),
                "description": "REQUIRED — always set this, even when only one language is enabled",
            },
            "desc": {**_STR, "description": "search-index description"},
            "body": {**_STR, "description": "full script source"},
            "parent": {**_STR, "description": "fork lineage: parent script name[@version]"},
            "scope": {"type": "string", "enum": ["global", "repo"]},
        },
        ["name", "lang", "desc", "body"],
    ),
    _fn(
        "update",
        "Append a new version of an existing script (e.g. to fix a bug found by run). "
        "changelog is a required one-line note on why this version exists; body is the "
        "full new source.",
        {
            "name": _STR,
            "changelog": {**_STR, "description": "why this version exists"},
            "body": {**_STR, "description": "full new script source"},
        },
        ["name", "changelog", "body"],
    ),
    _fn(
        "read",
        "Show a script's source + a preview of its last 3 runs; or, with exec, page "
        "through a stored execution's full output.",
        {
            "name": {**_STR, "description": "script name[@version]"},
            "exec": {**_INT, "description": "execution id to page instead of a script"},
            "page": _INT,
        },
        [],
    ),
    _fn(
        "list",
        "Terse rows of library scripts. Prefer find when you know what you want.",
        {
            "scope": _STR,
            "lang": {"type": "string", "enum": lang_enum()},
            "limit": _INT,
            "offset": _INT,
        },
        [],
    ),
    _fn(
        "find",
        "Ranked search over the library (name > description > body). Check here first "
        "when unsure a suitable script already exists.",
        {"query": _STR, "limit": _INT},
        ["query"],
    ),
    _fn(
        "run",
        "Execute a script. args are passed to the script; stdin is fed on stdin. Output "
        "is returned in full by default — set head/tail only to cap a huge observation.",
        {
            "name": {**_STR, "description": "script name[@version]"},
            "args": {"type": "array", "items": _STR, "description": "script arguments"},
            "stdin": {**_STR, "description": "text fed to the script's stdin"},
            "timeout": {**_NUM, "description": "seconds before the run is killed"},
            "head": _INT,
            "tail": _INT,
        },
        ["name"],
    ),
    _fn(
        SUBMIT_TOOL_NAME,
        "Finish the task. Call this once, when done, with your final answer as result. "
        "This is the only way to end the task — there is no output sentinel.",
        {"result": {**_STR, "description": "the final answer / result text"}},
        ["result"],
    ),
]
