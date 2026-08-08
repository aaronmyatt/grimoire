"""`grim completion` — install shell tab-completion for the grim CLI.

Writes two completion files under ~/.grimoire/completions (bash: grim.bash,
zsh: _grim) that read script names live from the grimoire database, and
appends idempotent hook lines to ~/.bashrc / ~/.zshrc so every new shell gets
the completion automatically. `grim init` calls install() as part of setup, so
a fresh install gets completions with no extra step.

Test seam: GRIM_COMPLETIONS_DIR / GRIM_BASHRC / GRIM_ZSHRC override the target
paths so tests (and CI) never touch real dotfiles.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# NOTE: both templates must stay free of backslashes (they live inside python
# string literals) and under 100 chars per line (ruff E501 counts physical
# lines even inside strings). Script names are validated slugs, so interpolating
# $script straight into the SQL is safe.
BASH_COMPLETION = """# grim bash completion (managed by `grim completion`).
# Reads script names live from the grim sqlite database so `grim run <TAB>`
# autocompletes every non-archived script in the library.

_grim() {
    local cur prev db q_names
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    db="${GRIM_DB:-$HOME/.grimoire/grimoire.db}"
    q_names="SELECT name FROM script WHERE archived=0 ORDER BY name;"

    # top-level verb
    if (( COMP_CWORD == 1 )); then
        local verbs="run write update read list find init config doctor"
        verbs+=" near recent edit tag untag tags tagged favourite unfavourite"
        verbs+=" favourites completion"
        COMPREPLY=( $(compgen -W "$verbs" -- "$cur") )
        return 0
    fi

    case "$prev" in
        run|write|update|read)
            # these verbs take a script name from the database
            if [[ "$cur" == *@* ]]; then
                # script@version — offer versions from script_version
                local script="${cur%%@*}"
                local q="SELECT s.name||'@'||v.version FROM script s JOIN script_version v"
                q+=" ON v.script_id=s.id WHERE s.name='$script' AND s.archived=0"
                q+=" ORDER BY v.version;"
                local vers=""
                if [[ -f "$db" ]]; then
                    if command -v sqlite3 >/dev/null 2>&1; then
                        vers=$(sqlite3 -noheader "$db" "$q" 2>/dev/null || true)
                    elif command -v python3 >/dev/null 2>&1; then
                        vers=$(python3 -c '
import sqlite3, sys
db, script = sys.argv[1], sys.argv[2]
c = sqlite3.connect(db)
sep = chr(10)
rows = c.execute(
    "SELECT s.name, v.version FROM script s JOIN script_version v ON v.script_id=s.id "
    "WHERE s.name=? AND s.archived=0 ORDER BY v.version", (script,))
print(sep.join(f"{r[0]}@{r[1]}" for r in rows))
' "$db" "$script" 2>/dev/null || true)
                    fi
                fi
                COMPREPLY=( $(compgen -W "$vers" -- "$cur") )
            else
                local names=""
                if [[ -f "$db" ]]; then
                    if command -v sqlite3 >/dev/null 2>&1; then
                        names=$(sqlite3 -noheader "$db" "$q_names" 2>/dev/null || true)
                    elif command -v python3 >/dev/null 2>&1; then
                        names=$(python3 -c '
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
sep = chr(10)
print(sep.join(r[0] for r in c.execute("SELECT name FROM script WHERE archived=0 ORDER BY name")))
' "$db" 2>/dev/null || true)
                    fi
                fi
                COMPREPLY=( $(compgen -W "$names" -- "$cur") )
            fi
            return 0
            ;;
        *)
            COMPREPLY=()
            return 0
            ;;
    esac
}
complete -o bashdefault -o default -F _grim grim
"""

ZSH_COMPLETION = """#compdef grim
# grim zsh completion (managed by `grim completion`).
# Reads script names live from the grim sqlite database so `grim run <TAB>`
# autocompletes every non-archived script and `grim run name@<TAB>` offers
# that script's versions. Loaded via fpath + compinit (see the zsh hook).

_grim() {
    local db="${GRIM_DB:-$HOME/.grimoire/grimoire.db}"
    local -a candidates=()
    local out="" q_names
    q_names="SELECT name FROM script WHERE archived=0 ORDER BY name;"
    if (( CURRENT == 2 )); then
        local -a verbs=(run write update read list find init config doctor)
        verbs+=(near recent edit tag untag tags tagged favourite unfavourite)
        verbs+=(favourites completion)
        compadd -- "${verbs[@]}"
        return 0
    fi
    (( CURRENT == 3 )) || return 0
    [[ "${words[2]}" == (run|write|update|read) ]] || return 0
    [[ -f "$db" ]] || return 0
    if [[ "$PREFIX" == *@* ]]; then
        local script="${PREFIX%%@*}"
        local q="SELECT s.name||'@'||v.version FROM script s JOIN script_version v"
        q+=" ON v.script_id=s.id WHERE s.name='$script' AND s.archived=0"
        q+=" ORDER BY v.version;"
        out="$(sqlite3 -noheader "$db" "$q" 2>/dev/null)"
    else
        out="$(sqlite3 -noheader "$db" "$q_names" 2>/dev/null)"
    fi
    [[ -n "$out" ]] && candidates=("${(@f)out}")
    compadd -- "${candidates[@]}"
}
_grim "$@"
"""

_BASH_MARKER = "# grim bash completion (managed by `grim completion`)"
_BASH_HOOK = (
    '[ -f "$HOME/.grimoire/completions/grim.bash" ] '
    '&& source "$HOME/.grimoire/completions/grim.bash"'
)

_ZSH_MARKER = "# grim zsh completion (managed by `grim completion`)"
_ZSH_HOOK = """fpath=($HOME/.grimoire/completions $fpath)
if (( $+functions[compdef] )); then
  # compinit already ran (framework) — register the #compdef file directly
  autoload -Uz _grim && compdef _grim grim
else
  autoload -Uz compinit && compinit
fi"""


def completions_dir() -> Path:
    return Path(os.environ.get("GRIM_COMPLETIONS_DIR") or Path.home() / ".grimoire" / "completions")


def _bashrc() -> Path:
    return Path(os.environ.get("GRIM_BASHRC") or Path.home() / ".bashrc")


def _zshrc() -> Path:
    return Path(os.environ.get("GRIM_ZSHRC") or Path.home() / ".zshrc")


def _append_hook(rc: Path, marker: str, hook: str) -> bool:
    """Append marker+hook to rc if not already present. True if appended."""
    if rc.is_file() and marker in rc.read_text():
        return False
    rc.parent.mkdir(parents=True, exist_ok=True)
    with rc.open("a") as fh:
        fh.write(f"\n{marker}\n{hook}\n")
    return True


def _remove_hook(rc: Path, marker: str, hook: str) -> bool:
    """Remove the marker line plus the hook block that follows it."""
    if not rc.is_file():
        return False
    lines = rc.read_text().splitlines()
    marker_idx = next((i for i, ln in enumerate(lines) if ln.strip() == marker), None)
    if marker_idx is None:
        return False
    hook_lines = hook.splitlines()
    del lines[marker_idx : marker_idx + 1 + len(hook_lines)]
    rc.write_text("\n".join(lines) + ("\n" if lines else ""))
    return True


def install() -> None:
    """Write both completion files and hook lines. Safe to re-run (idempotent)."""
    d = completions_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "grim.bash").write_text(BASH_COMPLETION)
    (d / "_grim").write_text(ZSH_COMPLETION)
    for rc, marker, hook in (
        (_bashrc(), _BASH_MARKER, _BASH_HOOK),
        (_zshrc(), _ZSH_MARKER, _ZSH_HOOK),
    ):
        if _append_hook(rc, marker, hook):
            print(f"added completion hook to {rc}")
    print(f"wrote {d / 'grim.bash'} (bash)")
    print(f"wrote {d / '_grim'} (zsh)")


def uninstall() -> None:
    """Remove completion files and rc hooks (idempotent)."""
    d = completions_dir()
    for name in ("grim.bash", "_grim"):
        p = d / name
        if p.is_file():
            p.unlink()
            print(f"removed {p}")
    for rc, marker, hook in (
        (_bashrc(), _BASH_MARKER, _BASH_HOOK),
        (_zshrc(), _ZSH_MARKER, _ZSH_HOOK),
    ):
        if _remove_hook(rc, marker, hook):
            print(f"removed completion hook from {rc}")


def _status() -> list[str]:
    lines: list[str] = []
    d = completions_dir()
    for name in ("grim.bash", "_grim"):
        p = d / name
        lines.append(f"{'ok' if p.is_file() else 'missing'}: {p}")
    for rc, marker, _ in (
        (_bashrc(), _BASH_MARKER, _BASH_HOOK),
        (_zshrc(), _ZSH_MARKER, _ZSH_HOOK),
    ):
        hooked = rc.is_file() and marker in rc.read_text()
        lines.append(f"{'ok' if hooked else 'not hooked'}: {rc}")
    return lines


def _selftest() -> int:
    ok = True
    for shell, snippet in (("bash", BASH_COMPLETION), ("zsh", ZSH_COMPLETION)):
        exe = shutil.which(shell)
        if not exe:
            print(f"{shell}: interpreter not found, skipping syntax check")
            continue
        result = subprocess.run([exe, "-n"], input=snippet, text=True, capture_output=True)
        if result.returncode == 0:
            print(f"{shell} -n: ok")
        else:
            print(f"{shell} -n: FAIL\n{result.stderr}")
            ok = False
    for name, snippet in (("bash", BASH_COMPLETION), ("zsh", ZSH_COMPLETION)):
        has_query = "SELECT name FROM script" in snippet
        print(f"{name}: db script-name query present: {has_query}")
        ok = ok and has_query
    return 0 if ok else 1


def cmd_completion(args: argparse.Namespace) -> int:
    """`grim completion` — install (default) or manage shell completion."""
    if args.print_bash:
        sys.stdout.write(BASH_COMPLETION)
    elif args.print_zsh:
        sys.stdout.write(ZSH_COMPLETION)
    elif args.uninstall:
        uninstall()
    elif args.check:
        status = _status()
        for line in status:
            print(line)
        return 0 if all(not line.startswith(("missing", "not hooked")) for line in status) else 1
    elif args.selftest:
        return _selftest()
    else:
        install()
    return 0
