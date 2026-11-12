"""Runtime tests for the browser seeds in seeds/bodies_web.py (feature 6,
docs/features/06-browser.md), dispatched exactly as `grim run` would. They
drive one real headless Chrome on a throwaway profile and port — tmux job
"chrome" on a private tmux server — against pages served by a local test
server. Nothing is fetched from the network: chromedp comes from the local
Go module cache, and the built Go binaries persist in pytest's cache dir."""

from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import socket as socketlib
import subprocess
import threading
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest

from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS

_MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_HAS_CHROME = Path(_MAC_CHROME).is_file() or any(
    shutil.which(name) for name in ("google-chrome", "chromium", "chromium-browser")
)
pytestmark = pytest.mark.skipif(
    not (_HAS_CHROME and shutil.which("tmux") and shutil.which("go")),
    reason="these seeds drive a real Chrome through tmux and Go",
)

_TIMEOUT_S = 120.0
_USAGE_EXIT_CODE = 2
_BIG_TEXT_CHARS = 2_000_000
_READ_CAP_CHARS = 20_000
_WAIT_TIMEOUT_EXIT_CODE = 124
_PAGES = {
    "index.html": "<title>home</title><h1 id=greeting>hello from the test site</h1>",
    # .results is added to the page half a second after it loads.
    "slow.html": "<title>slow</title><script>setTimeout(() => { document.body.innerHTML = "
    "'<p class=results>three results</p>' }, 500)</script>",
    # A stand-in login: "go" stores the user (and the password's length) in
    # localStorage, and any later load of the page shows who is signed in.
    "login.html": "<title>login</title><input id=user><input id=password type=password>"
    "<button id=go>go</button><p class=account-name></p><script>"
    "const show = () => { const user = localStorage.getItem('user'); if (user) "
    "document.querySelector('.account-name').textContent = "
    "'signed in as ' + user + ' with a ' + localStorage.getItem('n') + '-char password' };"
    "document.getElementById('go').onclick = () => { "
    "localStorage.setItem('user', document.getElementById('user').value); "
    "localStorage.setItem('n', document.getElementById('password').value.length); show() };"
    "show()</script>",
    "big.html": "<title>big</title><p id=big></p><script>"
    f"document.getElementById('big').textContent = 'x'.repeat({_BIG_TEXT_CHARS})</script>",
}


class Site(NamedTuple):
    url: str  # the test server's base URL
    socket: str  # the private tmux server
    port: str  # Chrome's DevTools port
    profile: Path


def _free_port() -> str:
    with socketlib.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return str(probe.getsockname()[1])


def _run(name: str, argv: list[str]) -> ExecutionResult:
    seed = next(s for s in SEEDS if s.name == name)
    request = ExecutionRequest(argv=argv, stdin=None, cwd=None, timeout=_TIMEOUT_S)
    return dispatch(ScriptVersion(language=seed.language, body=seed.body), request)


def _tmux(socket: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["tmux", "-L", socket, *args]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=_TIMEOUT_S)


def _tabs(site: Site) -> list[dict[str, str]]:
    with urllib.request.urlopen(f"http://127.0.0.1:{site.port}/json/list", timeout=5) as reply:
        tabs: list[dict[str, str]] = json.loads(reply.read())
    return [tab for tab in tabs if tab["type"] == "page"]


@pytest.fixture(scope="module")
def site(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> Iterator[Site]:
    root = tmp_path_factory.mktemp("web")
    pages = root / "site"
    pages.mkdir()
    for name, html in _PAGES.items():
        (pages / name).write_text(html)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(pages))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    made = Site(
        url=f"http://127.0.0.1:{server.server_address[1]}",
        socket=f"grimtest-{root.parent.name}-{root.name}",
        port=_free_port(),
        profile=root / "profile",
    )
    env = {
        "GRIM_TMUX_SOCKET": made.socket,
        "GRIM_RUN_DIR": str(root / "run"),
        "GRIM_CHROME_PORT": made.port,
        "GRIM_CHROME_DIR": str(made.profile),
        "GRIM_CHROME_HEADLESS": "1",
        # Built binaries and the chromedp pin persist between test runs.
        "GRIM_GO_DIR": str(request.config.cache.mkdir("grim-go")),
        "GOFLAGS": "-mod=mod",
        "GOPROXY": "off",  # prove nothing is downloaded
        "GOSUMDB": "off",
    }
    before = {key: os.environ.get(key) for key in env}
    os.environ.update(env)  # module-scoped, so set by hand (monkeypatch is per test)
    try:
        yield made
    finally:
        _tmux(made.socket, "kill-server")
        socket_dir = Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"
        (socket_dir / made.socket).unlink(missing_ok=True)
        server.shutdown()
        for key, value in before.items():
            os.environ.pop(key, None) if value is None else os.environ.__setitem__(key, value)


def test_web_open_starts_chrome_as_a_job_on_its_own_profile_and_opens_the_tab(site: Site) -> None:
    result = _run("web_open", [f"{site.url}/index.html"])
    if "could not add chromedp" in result.stderr:
        pytest.skip("chromedp v0.21.0 is not in the local Go module cache")
    assert result.exit_code == 0, result.stderr
    assert f"started Chrome as job 'chrome': profile {site.profile.resolve()}" in result.stdout
    tags = "#{window_name} #{@grim_kind} #{@grim_job} #{?pane_dead,dead,alive}"
    assert _tmux(site.socket, "list-windows", "-t", "=grim", "-F", tags).stdout == (
        "chrome browser chrome alive\n"
    )
    started = _tmux(site.socket, "list-windows", "-t", "=grim", "-F", "#{pane_start_command}")
    assert f"--user-data-dir={site.profile.resolve()}" in started.stdout
    assert "Application Support/Google/Chrome" not in started.stdout  # never the daily profile
    assert [tab["url"] for tab in _tabs(site)][0] == f"{site.url}/index.html"


def test_web_open_again_attaches_to_the_same_chrome(site: Site) -> None:
    assert _run("web_open", [f"{site.url}/index.html"]).exit_code == 0
    again = _run("web_open", [f"{site.url}/index.html?second"])
    assert again.exit_code == 0, again.stderr
    assert "started Chrome" not in again.stdout  # no second Chrome
    assert _tmux(site.socket, "list-windows", "-t", "=grim", "-F", "#W").stdout == "chrome\n"
    assert _tabs(site)[0]["url"] == f"{site.url}/index.html?second"  # newest tab is current


@pytest.mark.parametrize("argv", [[], ["ftp://example.com"], ["example.com"], ["a", "b"]])
def test_web_open_wrong_calls_exit_2(site: Site, argv: list[str]) -> None:
    result = _run("web_open", argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "usage: web_open URL" in result.stderr


def test_web_open_refuses_a_daily_chrome_profile(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    monkeypatch.setenv("GRIM_CHROME_DIR", str(daily))
    result = _run("web_open", [f"{site.url}/index.html"])
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "that is a daily Chrome profile, not grim's own" in result.stderr


def _open(site: Site, page: str) -> None:
    opened = _run("web_open", [f"{site.url}/{page}"])
    assert opened.exit_code == 0, opened.stderr


def test_web_read_waits_for_an_element_then_returns_its_text(site: Site) -> None:
    _open(site, "slow.html")
    result = _run("web_read", [".results"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout == "three results\n"


def test_web_read_of_a_thing_that_never_shows_exits_124_naming_the_selector(site: Site) -> None:
    _open(site, "index.html")
    result = _run("web_read", ["#missing", "0.5"])
    assert result.exit_code == _WAIT_TIMEOUT_EXIT_CODE
    assert result.stderr == 'web_read: timed out: nothing visible matched "#missing"\n'


def test_web_read_caps_a_huge_text_and_says_it_was_cut(site: Site) -> None:
    _open(site, "big.html")
    result = _run("web_read", ["#big"])
    assert result.exit_code == 0, result.stderr
    text, notice = result.stdout.rstrip("\n").split("\n")
    assert text == "x" * _READ_CAP_CHARS
    assert notice == f"[web_read] cut: showed {_READ_CAP_CHARS} of {_BIG_TEXT_CHARS} characters"


@pytest.mark.parametrize(
    ("expression", "printed"),
    [("document.title", '"home"'), ("1 + 1", "2"), ("({a: [1, 'two']})", '{"a":[1,"two"]}')],
)
def test_web_eval_prints_the_value_as_json(site: Site, expression: str, printed: str) -> None:
    _open(site, "index.html")
    result = _run("web_eval", [expression])
    assert result.exit_code == 0, result.stderr
    assert result.stdout == printed + "\n"


@pytest.mark.parametrize(
    ("name", "argv"),
    [
        ("web_read", []),
        ("web_read", ["#x", "soon"]),
        ("web_read", ["#x", "1", "2"]),
        ("web_eval", []),
        ("web_eval", ["1", "0"]),
    ],
)
def test_web_read_and_eval_wrong_calls_exit_2(site: Site, name: str, argv: list[str]) -> None:
    result = _run(name, argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert f"usage: {name} " in result.stderr


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"  # Ref: https://www.w3.org/TR/png/#5PNG-file-signature
_SECRET = "hunter2-not-a-real-password"


def test_a_login_lasts_between_scripts_and_the_secret_is_typed_by_name(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SITE_PASSWORD", _SECRET)
    _open(site, "login.html")
    assert _run("web_type", ["#user", "alice"]).stdout == "typed 5 characters into #user\n"
    typed = _run("web_type", ["#password", "--env", "SITE_PASSWORD"])
    assert typed.exit_code == 0, typed.stderr
    assert typed.stdout == f"typed {len(_SECRET)} characters into #password\n"
    assert _SECRET not in typed.stdout + typed.stderr  # named, never echoed
    assert _run("web_click", ["#go"]).stdout == "clicked #go\n"
    signed_in = f"signed in as alice with a {len(_SECRET)}-char password\n"
    assert _run("web_read", [".account-name"]).stdout == signed_in

    _open(site, "login.html")  # a later script, a fresh tab: no login this time
    assert _run("web_read", [".account-name"]).stdout == signed_in


def test_web_click_on_a_thing_that_never_shows_exits_124_and_clicks_nothing(site: Site) -> None:
    _open(site, "index.html")
    result = _run("web_click", ["#missing", "0.5"])
    assert result.exit_code == _WAIT_TIMEOUT_EXIT_CODE
    assert result.stderr == (
        'web_click: timed out: nothing visible matched "#missing", so nothing was clicked\n'
    )


def test_web_type_refuses_an_unset_variable_name(site: Site) -> None:
    result = _run("web_type", ["#user", "--env", "GRIM_NO_SUCH_VARIABLE"])
    assert result.exit_code == _USAGE_EXIT_CODE
    assert result.stderr == "web_type: environment variable GRIM_NO_SUCH_VARIABLE is not set\n"


def test_web_shot_writes_a_png_and_prints_its_path(site: Site, tmp_path: Path) -> None:
    _open(site, "index.html")
    target = tmp_path / "page.png"
    result = _run("web_shot", [str(target)])
    assert result.exit_code == 0, result.stderr
    assert result.stdout == f"{target}\n"
    assert target.read_bytes().startswith(_PNG_MAGIC)


@pytest.mark.parametrize(
    ("name", "argv"),
    [
        ("web_click", []),
        ("web_click", ["#x", "never"]),
        ("web_type", ["#x"]),
        ("web_type", ["#x", "--env", "A", "B"]),
        ("web_shot", []),
        ("web_shot", ["a.png", "0"]),
    ],
)
def test_web_click_type_and_shot_wrong_calls_exit_2(site: Site, name: str, argv: list[str]) -> None:
    result = _run(name, argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert f"usage: {name} " in result.stderr
