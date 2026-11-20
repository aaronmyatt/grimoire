# Headless delegates: observed output (paid smoke, 2026-11-20)

Run: `agent_ask --exec-args JSON both smoke-1 DIR 300` on a 2-line brief, isolated library and tmux server. Both ended DONE, checked by `agent_done`.

| | claude 2.1.322 (`-p --output-format json`) | codex-cli 0.170.0 (`exec --json -o FILE`) |
|---|---|---|
| answer | `result` (string) | the `-o` file (= last `item.completed` → `item.text`) |
| error flag | `is_error` (false); also `subtype: "success"` | none seen |
| cost | `total_cost_usd`: **$0.0584** | not reported, so `cost_usd` is null |
| tokens | `usage.input_tokens` 2, `usage.output_tokens` 179 | `turn.completed.usage.input_tokens` 22396, `.output_tokens` 201 |
| wall | 4.1 s (`duration_ms` 2525) | 11.4 s |

- Events codex emitted: `thread.started`, `turn.started`, `item.completed`, `turn.completed`. `usage` was at the top level of `turn.completed`.
- claude's `usage.input_tokens` counts only uncached input. The other 9,995 input tokens were in `cache_read_input_tokens` (3,223) and `cache_creation_input_tokens` (6,772), which `meta.json` does not sum yet. codex `input_tokens` already includes `cached_input_tokens` (8,320).
- Spend: **$0.0584** reported, against the $0.50 cap (claude ran with `--max-budget-usd 0.25`). No parser change was needed: every field `agent_exec` reads was present.
- Still UNVERIFIED:
  - the AUTH and CONTEXT hint strings (nothing failed);
  - claude's output when `is_error` is true;
  - codex's error events;
  - the shape when a run hits `--max-budget-usd`.
