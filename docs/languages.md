# Extended language support

Languages considered as extensions to the grim CLI and agent harness:
janet, racket, hy, nim, ruby, bun (node), php, go, perl, jq, sql, awk,
osascript, lua/luajit, fennel, zig, duckdb + prql, typst, bc/dc.

All of them are **off by default**. `grim write --lang` accepts only
`python` and `bash` until you opt in, and the agent's tool schema only
proposes languages you've enabled.

## Enabling a language

Set it in `~/.grimoire/config.toml` (global) or `./.grimoire/config.toml`
(repo-scoped; repo wins). Two equivalent forms — a table of booleans, or a
list:

```toml
[languages]
ruby = true
jq = true
osascript = true   # macOS only — ignored elsewhere
```

```toml
languages = ["ruby", "jq"]
```

A language named in config becomes available to `grim write --lang`, the
agent's `write`/`list` tools, and `grim doctor` (which reports whether its
interpreter is installed). Removing it from config (or setting it `false`)
reverts to off-by-default — scripts already written in that language keep
running, since the toggle gates *writing* new scripts, not executing the
library.

Precedence matches every other config key: shell env > repo config > global
config > off. The env var behind it is `GRIM_LANGUAGES` (comma-joined); a
repo `[languages]` table fully determines it when present, so an empty
`languages = []` at repo level explicitly disables everything even if a
global config enables some.

## Subsetting the builtins (experiments)

`GRIM_BASE_LANGUAGES` (env-only, no config surface) subsets which BUILTIN
languages `grim write` accepts: unset keeps both python and bash (the
default everywhere); a set value names the subset to keep; `""` keeps
none — the solo-language knob the eval sweeps use (`evals/sweep-langs`).
It only ever narrows the builtin pair — extended languages still arrive
via `GRIM_LANGUAGES` — and if the two knobs together would leave nothing
writable, the write gate falls back to python+bash rather than bricking
the agent. Like the extended toggle, it gates *writing* only: scripts
already in the library (including the python seeds) always run.

## Platform-specific languages

A language whose interpreter is OS-specific carries a platform gate and is
silently skipped on other operating systems:

| language     | platform |
|---|---|
| osascript    | macOS (`darwin`) |

`grim doctor` shows why a configured language is unavailable here (wrong
platform, unknown name, missing interpreter). `grim write` rejects a
platform-mismatched language with the reason.

## Runner table

Each language dispatches through `exec/dispatch.py`'s runner catalog:

| language   | command |
|---|---|
| janet      | `janet FILE` |
| racket     | `racket FILE` |
| hy         | `hy FILE` |
| nim        | `nim r FILE` |
| ruby       | `ruby FILE` |
| bun        | `bun FILE` (JS/TS) |
| php        | `php FILE` |
| go         | `go run FILE` |
| perl       | `perl FILE` |
| jq         | `jq -f FILE` (input on stdin) |
| sql        | `sqlite3 :memory: ".read FILE"` |
| awk        | `awk -f FILE` (input on stdin) |
| osascript  | `osascript FILE` (macOS) |
| lua        | `lua FILE` |
| luajit     | `luajit FILE` |
| fennel     | `fennel FILE` |
| zig        | `zig run FILE` |
| duckdb     | `duckdb :memory: ".read FILE"` |
| prql       | `prqlc compile FILE \| duckdb :memory:` |
| typst      | `typst compile FILE` |
| bc         | `bc FILE` |
| dc         | `dc FILE` |

Script args passed to `grim run NAME -- arg...` are appended after the
runner's command, like the builtin languages.

## Linting

`grim write`/`grim edit` syntax-lint where a cheap, offline checker exists:
ruby (`ruby -c`), php (`php -l`), perl (`perl -c`), go (`gofmt -e`).
Languages without one, or whose checker binary is missing, pass silently —
lint is best-effort and never blocks a write; `grim doctor` reports missing
interpreters.
