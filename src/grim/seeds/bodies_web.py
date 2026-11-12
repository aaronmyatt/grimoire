"""Seed bodies that drive one long-lived Chrome — the browser driver
(docs/features/06-browser.md). Data, not code: each constant is one seed's
full script text; bodies.py's SEEDS roster wires WEB_SEEDS in.

Chrome runs as tmux job "chrome" on its own profile, with DevTools listening
on localhost. web_open (python) starts it and opens tabs over DevTools' HTTP
endpoint; the other web_ seeds are Go programs that attach with chromedp,
built once against the shared module (exec/gobuild.py). State lives in
Chrome: the current tab is the page Chrome lists first.
Ref: https://chromedevtools.github.io/devtools-protocol/
"""

from __future__ import annotations

from grim.seeds.bodies_web_go import WEB_GO_SEEDS

WEB_OPEN = r'''"""web_open — open a URL in a new tab of grim's own long-lived Chrome, starting
that Chrome first if it is not running: tmux job "chrome", profile
~/.grimoire/chrome (never your daily profile), DevTools on 127.0.0.1:9222.
Tabs, cookies and logins last between scripts. The new tab becomes the
current tab, the one web_read, web_click, web_type, web_eval and web_shot act
on. First use also sets up the shared Go module those seeds are built against
(~/.grimoire/go, pinning chromedp: one download).
Usage: web_open URL   (http://, https://, file://, about: or data:)
Knobs: $GRIM_CHROME_PORT, $GRIM_CHROME_DIR, $GRIM_CHROME_BIN, and
$GRIM_CHROME_HEADLESS=1 for no window (the default where there is no display).
Stop Chrome with tmux_stop chrome. $GRIM_TMUX_SOCKET names a private tmux server.
Ref: https://chromedevtools.github.io/devtools-protocol/#endpoints"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SESSION, JOB = "grim", "chrome"
CHROMEDP = "github.com/chromedp/chromedp@v0.21.0"  # the API the Go web seeds are written for
START_WAIT_S, POLL_S = 30.0, 0.25
SCHEMES = ("http://", "https://", "file://", "about:", "data:")
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OTHER_CHROMES = ("google-chrome", "chromium", "chromium-browser", "chrome")
DAILY_PROFILES = ("Library/Application Support/Google/Chrome", ".config/google-chrome",
                  ".config/chromium")
socket = os.environ.get("GRIM_TMUX_SOCKET")
TMUX = ["tmux", "-L", socket] if socket else ["tmux"]
port = os.environ.get("GRIM_CHROME_PORT", "9222")
run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")


def tmux(*args):
    return subprocess.run([*TMUX, *args], capture_output=True, text=True, check=False)


def fail(code, message):
    print(f"web_open: {message}", file=sys.stderr)
    sys.exit(code)


def devtools(path, method="GET"):
    """One call to Chrome's DevTools HTTP endpoint; None when nothing answers."""
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=3) as reply:
            return json.loads(reply.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def chrome_job():
    """'alive', 'dead' or None: the state of tmux job "chrome", asked of tmux."""
    listed = tmux("list-windows", "-t", f"={SESSION}", "-F", "#{@grim_job}\t#{pane_dead}")
    states = dict(row.split("\t") for row in listed.stdout.splitlines() if row.count("\t") == 1)
    return {"0": "alive", "1": "dead"}.get(states.get(JOB))


def ensure_go_module():
    """Make the shared Go module pin chromedp, so the Go web seeds can build."""
    module = Path(os.environ.get("GRIM_GO_DIR") or Path.home() / ".grimoire" / "go")
    go_mod = module / "go.mod"
    if go_mod.is_file() and "chromedp/chromedp" in go_mod.read_text():
        return
    if shutil.which("go") is None:
        fail(127, "program not found on PATH: 'go' (the web seeds are built with it)")
    module.mkdir(parents=True, exist_ok=True)
    if not go_mod.is_file():
        go_mod.write_text("module grimscripts\n\ngo 1.25\n")
    got = subprocess.run(["go", "get", CHROMEDP], cwd=module, capture_output=True, text=True)
    if got.returncode != 0:
        fail(1, f"could not add chromedp to {module}: {got.stderr.strip()[-400:]}")
    print(f"set up the Go module for the web seeds: {module}")


def chrome_program():
    """The Chrome to start: $GRIM_CHROME_BIN, else the first one found."""
    found = [MAC_CHROME] if Path(MAC_CHROME).is_file() else []
    found += [path for path in map(shutil.which, OTHER_CHROMES) if path]
    program = os.environ.get("GRIM_CHROME_BIN") or (found[0] if found else None)
    if program is None or shutil.which(program) is None:
        fail(127, "no Chrome found; set GRIM_CHROME_BIN to its program")
    return program


def start_chrome(profile, program):
    """Open tmux job "chrome" (the same window recipe as tmux_start) and wait
    until its DevTools endpoint answers."""
    no_display = sys.platform != "darwin" and not os.environ.get("DISPLAY")
    headless = os.environ.get("GRIM_CHROME_HEADLESS", "1" if no_display else "0") == "1"
    argv = [program, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check",
            *(["--headless=new"] if headless else []), "about:blank"]
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / f"{JOB}.log"
    log_path.write_text("")
    exists = tmux("has-session", "-t", f"={SESSION}").returncode == 0
    create = ["new-window", "-t", f"={SESSION}:"] if exists else ["new-session", "-s", SESSION]
    made = tmux(*create, "-d", "-n", JOB, "-P", "-F", "#{window_id}", "cat", "-")
    if made.returncode != 0:
        fail(1, f"tmux could not open a window: {made.stderr.strip()}")
    window = made.stdout.strip()
    steps = [
        ["set-option", "-w", "-t", window, "remain-on-exit", "on"],
        ["set-option", "-w", "-t", window, "@grim_kind", "browser"],
        ["set-option", "-w", "-t", window, "@grim_job", JOB],
        ["set-option", "-w", "-t", window, "@grim_started", str(int(time.time()))],
        ["pipe-pane", "-t", window, f"cat >> {shlex.quote(str(log_path))}"],
        ["set-hook", "-w", "-t", window, "pane-died", f"wait-for -S grim-{JOB}"],
        ["respawn-pane", "-k", "-t", window, "--", *argv],
    ]
    for step in steps:
        done = tmux(*step)
        if done.returncode != 0:
            tmux("kill-window", "-t", window)
            fail(1, f"tmux {step[0]} failed: {done.stderr.strip()}")
    deadline = time.monotonic() + START_WAIT_S
    while devtools("/json/version") is None:
        if chrome_job() != "alive" or time.monotonic() > deadline:
            fail(1, f"Chrome did not come up on port {port}; see tmux_read {JOB}")
        time.sleep(POLL_S)
    print(f"started Chrome as job {JOB!r}: profile {profile}, port {port}")


if len(sys.argv) != 2 or not sys.argv[1].startswith(SCHEMES) or not port.isdigit():
    fail(2, "usage: web_open URL   (http://, https://, file://, about: or data:)")
url = sys.argv[1]
home_profile = Path.home() / ".grimoire" / "chrome"
profile = Path(os.environ.get("GRIM_CHROME_DIR") or home_profile).expanduser().resolve()
if any(str(profile).startswith(str(Path.home() / daily)) for daily in DAILY_PROFILES):
    fail(2, f"refusing {profile}: that is a daily Chrome profile, not grim's own")
if shutil.which("tmux") is None:
    fail(127, "program not found on PATH: 'tmux'")
job, answering = chrome_job(), devtools("/json/version") is not None
if job == "dead":
    fail(1, f"job {JOB!r} is dead; clear it with tmux_stop {JOB}, then open again")
if job is None and answering:
    fail(1, f"port {port} is held by a Chrome grim did not start; set GRIM_CHROME_PORT")
if job == "alive" and not answering:
    fail(1, f"job {JOB!r} is alive but DevTools is silent on port {port}; see tmux_read {JOB}")
# Every needed tool is checked before anything is set up or started.
program = chrome_program() if job is None else None
ensure_go_module()
if job is None:
    start_chrome(profile, program)
tab = devtools("/json/new?" + urllib.parse.quote(url, safe=":/?&=#%@+,;!$'()*~[]"), "PUT")
if not tab or "id" not in tab:
    fail(1, f"Chrome did not open a tab for {url}")
print(f"opened {url} in tab {tab['id']} (now the current tab)")
'''

# (name, language, description, body) rows: SeedSpec's own field order.
WEB_SEEDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "web_open",
        "python",
        "open a URL in a new tab of grim's own long-lived Chrome (starts it if needed; "
        "logins and tabs last between scripts): web_open URL",
        WEB_OPEN,
    ),
    *WEB_GO_SEEDS,
)
