"""
Truncation of stored stdout/stderr into the observation the agent sees.
"""

from __future__ import annotations


def _format_stream(name: str, text: str, head_lines: int, tail_lines: int) -> str:
    lines = text.splitlines()
    total = len(lines)
    if total <= head_lines + tail_lines:
        header = f"--- {name}: {total} lines ---"
        body = lines
    else:
        skipped = total - head_lines - tail_lines
        header = f"--- {name}: first {head_lines} + last {tail_lines} of {total} lines ---"
        body = [*lines[:head_lines], f"... ({skipped} skipped) ...", *lines[-tail_lines:]]
    return "\n".join([header, *body])


def truncate(stdout: str, stderr: str, head_lines: int = 40, tail_lines: int = 10) -> str:
    """Format stdout (always) and stderr (only if non-empty) for the
    observation returned to the agent. Full text is never lost — the
    caller stores it verbatim on the execution row before calling this.
    """
    assert head_lines > 0, "head_lines must be positive"
    assert tail_lines > 0, "tail_lines must be positive"
    sections = [_format_stream("stdout", stdout, head_lines, tail_lines)]
    if stderr:
        sections.append(_format_stream("stderr", stderr, head_lines, tail_lines))
    return "\n".join(sections)
