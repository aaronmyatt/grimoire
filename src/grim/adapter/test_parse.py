"""Tests for adapter/parse.py's grim-command parsing."""

from __future__ import annotations

from grim.adapter.parse import parse_grim


def test_parses_verb_with_heredoc_body() -> None:
    text = (
        "grim write --name greet --lang python --desc \"prints a greeting\" <<'EOF'\n"
        "print('hi')\n"
        "EOF"
    )
    result = parse_grim(text)
    assert result is not None
    assert result.verb == "write"
    assert result.argv == [
        "write",
        "--name",
        "greet",
        "--lang",
        "python",
        "--desc",
        "prints a greeting",
    ]
    assert result.stdin == "print('hi')"


def test_parses_verb_without_stdin() -> None:
    result = parse_grim('grim find "extract failing tests"')
    assert result is not None
    assert result.verb == "find"
    assert result.argv == ["find", "extract failing tests"]
    assert result.stdin == ""


def test_unquoted_heredoc_delimiter_is_supported() -> None:
    text = "grim update greet --changelog fix <<EOF\nprint('v2')\nEOF"
    result = parse_grim(text)
    assert result is not None
    assert result.stdin == "print('v2')"


def test_non_grim_command_returns_none() -> None:
    assert parse_grim("ls -la") is None


def test_grim_with_unknown_verb_returns_none() -> None:
    assert parse_grim("grim doctor") is None
    assert parse_grim("grim init") is None


def test_grim_alone_returns_none() -> None:
    assert parse_grim("grim") is None


def test_unterminated_heredoc_returns_none() -> None:
    text = "grim write --name greet --lang python --desc d <<'EOF'\nprint('hi')"
    assert parse_grim(text) is None


def test_unbalanced_quotes_returns_none() -> None:
    assert parse_grim('grim write --name greet --desc "unterminated') is None


def test_blank_text_returns_none() -> None:
    assert parse_grim("   \n\n  ") is None


def test_leading_blank_lines_are_skipped() -> None:
    result = parse_grim('\n\ngrim find "x"')
    assert result is not None
    assert result.verb == "find"


def test_multiline_body_preserves_internal_lines() -> None:
    text = "grim write --name greet --lang python --desc d <<'EOF'\nline1\nline2\nEOF"
    result = parse_grim(text)
    assert result is not None
    assert result.stdin == "line1\nline2"


def test_bare_verb_without_grim_prefix_is_accepted() -> None:
    # Regression: models routinely treat the ```grim fence tag as already
    # saying "grim" (the same way ```python never repeats "python" inside
    # the block) and drop the literal word from the content.
    result = parse_grim('find "extract failing tests"')
    assert result is not None
    assert result.verb == "find"
    assert result.argv == ["find", "extract failing tests"]


def test_bare_verb_with_heredoc_matches_the_reported_failure() -> None:
    text = "update greet --lang bash --desc \"prints a greeting\" <<'EOF'\necho hi\nEOF"
    result = parse_grim(text)
    assert result is not None
    assert result.verb == "update"
    assert result.argv == ["update", "greet", "--lang", "bash", "--desc", "prints a greeting"]
    assert result.stdin == "echo hi"


def test_bare_unknown_verb_still_returns_none() -> None:
    assert parse_grim("doctor") is None
    assert parse_grim("init") is None
    assert parse_grim("ls -la") is None
