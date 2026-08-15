"""
Formatting of stored stdout/stderr into the observation a run returns.

Full output is the default; head/tail limits are opt-in (`grim run
--head/--tail`) for the occasional huge-output script. Either way the text
shown here matches the execution row — the caller stores it first (clamped
to verbs/run.py's per-stream storage budget), and `grim read --exec <id>`
pages all of it back.
"""

from __future__ import annotations


def _format_stream(name: str, text: str, head_lines: int | None, tail_lines: int | None) -> str:
    lines = text.splitlines()
    total = len(lines)
    head = head_lines or 0
    tail = tail_lines or 0
    # No limit requested (both None), or the output already fits inside the
    # requested window -> show every line under a plain "N lines" header.
    if (head_lines is None and tail_lines is None) or total <= head + tail:
        return "\n".join([f"--- {name}: {total} lines ---", *lines])
    skipped = total - head - tail
    header = f"--- {name}: first {head} + last {tail} of {total} lines ---"
    # `lines[-tail:]` with tail == 0 is `lines[0:]` (the whole list, not the
    # empty slice you'd expect), so guard the tail slice explicitly.
    tail_slice = lines[-tail:] if tail else []
    return "\n".join([header, *lines[:head], f"... ({skipped} skipped) ...", *tail_slice])


def truncate(
    stdout: str, stderr: str, head_lines: int | None = 40, tail_lines: int | None = 10
) -> str:
    """Format stdout (always) and stderr (only if non-empty) for a run's
    observation. Passing None for BOTH head_lines and tail_lines emits the
    full text unabridged; any int limit collapses the middle of a long
    stream to its first head_lines and last tail_lines. The text shown is
    never richer than what the caller already stored on the execution row.

    The 40/10 defaults are retained only for backward-compatible direct
    callers; `grim run` itself now passes None/None (full output) unless
    the human/agent opts into `--head`/`--tail`.
    """
    assert head_lines is None or head_lines >= 0, "head_lines must be non-negative"
    assert tail_lines is None or tail_lines >= 0, "tail_lines must be non-negative"
    sections = [_format_stream("stdout", stdout, head_lines, tail_lines)]
    if stderr:
        sections.append(_format_stream("stderr", stderr, head_lines, tail_lines))
    return "\n".join(sections)
