"""Go seed bodies for the browser driver (docs/features/06-browser.md): the
web_ seeds that attach to grim's long-lived Chrome with chromedp. Data, not
code. Each seed is one complete Go program; the programs share one block of
Go text (_GO_SHARED: the attach, the wait and the exit rules), joined in here
so the five can never drift apart. bodies_web.py adds these rows to WEB_SEEDS.

Written for chromedp v0.21.0 (its `remote` allocator and generic actions),
the version web_open pins in the shared Go module.
Ref: https://pkg.go.dev/github.com/chromedp/chromedp
"""

from __future__ import annotations

_GO_SHARED = r"""import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/chromedp/cdproto/target"
	"github.com/chromedp/chromedp"
	"github.com/chromedp/chromedp/remote"
)

const (
	defaultWaitS = 10.0
	maxWaitS     = 600.0
	timeoutExit  = 124 // the exit code of a wait that ran out, as for a timed-out run
)

func fail(code int, message string) {
	fmt.Fprintln(os.Stderr, seed+": "+message)
	os.Exit(code)
}

// waitFrom reads the optional SECONDS argument at index i: how long to wait
// for the page (default 10, up to 600).
func waitFrom(i int) time.Duration {
	seconds := defaultWaitS
	if len(os.Args) > i {
		parsed, err := strconv.ParseFloat(os.Args[i], 64)
		if err != nil || parsed <= 0 || parsed > maxWaitS {
			fail(2, usage)
		}
		seconds = parsed
	}
	return time.Duration(seconds * float64(time.Second))
}

// attach binds a context, with a deadline, to the current tab of grim's
// Chrome: the page Chrome lists first, which is the one opened or used most
// recently. WithDetachOnCancel leaves the tab open when this program ends, so
// the next seed finds it as it was.
func attach(wait time.Duration) context.Context {
	addr := os.Getenv("GRIM_CHROME_ADDR")
	if addr == "" {
		port := os.Getenv("GRIM_CHROME_PORT")
		if port == "" {
			port = "9222"
		}
		addr = "http://127.0.0.1:" + port
	}
	reply, err := http.Get(addr + "/json/list")
	if err != nil {
		fail(1, "no Chrome at "+addr+": start it with web_open URL")
	}
	defer reply.Body.Close()
	var tabs []struct{ ID, Type string }
	if err := json.NewDecoder(reply.Body).Decode(&tabs); err != nil {
		fail(1, "could not read Chrome's tab list: "+err.Error())
	}
	id := ""
	for _, tab := range tabs {
		if tab.Type == "page" {
			id = tab.ID
			break
		}
	}
	if id == "" {
		fail(1, "Chrome has no open tab: open one with web_open URL")
	}
	alloc, _ := remote.NewAllocator(context.Background(), addr)
	browser, _ := chromedp.NewContext(alloc)
	if _, err := chromedp.Targets(browser); err != nil {
		fail(1, "could not attach to Chrome: "+err.Error())
	}
	tab, _ := chromedp.NewContext(browser,
		chromedp.WithTargetID(target.ID(id)), chromedp.WithDetachOnCancel())
	ctx, _ := context.WithTimeout(tab, wait)
	return ctx
}

// check ends the run if an action failed: exit 124 when the wait ran out.
func check(err error, what string) {
	if errors.Is(err, context.DeadlineExceeded) {
		fail(timeoutExit, "timed out: "+what)
	}
	if err != nil {
		fail(1, what+": "+err.Error())
	}
}
"""

_WEB_READ_HEAD = r"""// web_read — print the visible text of the first element matching a CSS
// selector in the current tab of grim's Chrome (the tab web_open made last).
// It waits until the element is visible, up to SECONDS (default 10); if it
// never shows, the exit code is 124 and the message names the selector. Text
// is capped at 20000 characters: when there is more, a last line says so.
// Usage: web_read SELECTOR [SECONDS]
// Chrome's address: $GRIM_CHROME_ADDR, else http://127.0.0.1:$GRIM_CHROME_PORT (9222).
// Ref: https://pkg.go.dev/github.com/chromedp/chromedp
package main

"""

_WEB_READ_MAIN = r"""
const (
	seed     = "web_read"
	usage    = "usage: web_read SELECTOR [SECONDS]   (SECONDS over 0, up to 600)"
	maxChars = 20000
)

func main() {
	if len(os.Args) < 2 || len(os.Args) > 3 {
		fail(2, usage)
	}
	selector := os.Args[1]
	ctx := attach(waitFrom(2))
	text, err := chromedp.Run(ctx, chromedp.Text(chromedp.CSS(selector), chromedp.NodeVisible))
	check(err, fmt.Sprintf("nothing visible matched %q", selector))
	runes := []rune(text)
	if len(runes) > maxChars {
		fmt.Println(string(runes[:maxChars]))
		fmt.Printf("[web_read] cut: showed %d of %d characters\n", maxChars, len(runes))
		return
	}
	fmt.Println(text)
}
"""

WEB_READ = _WEB_READ_HEAD + _GO_SHARED + _WEB_READ_MAIN

_WEB_EVAL_HEAD = r"""// web_eval — evaluate a JavaScript expression in the current tab of grim's
// Chrome and print its value as JSON.
// Usage: web_eval EXPRESSION [SECONDS]   e.g. web_eval "document.title"
// Chrome's address: $GRIM_CHROME_ADDR, else http://127.0.0.1:$GRIM_CHROME_PORT (9222).
// Ref: https://pkg.go.dev/github.com/chromedp/chromedp
package main

"""

_WEB_EVAL_MAIN = r"""
const (
	seed  = "web_eval"
	usage = "usage: web_eval EXPRESSION [SECONDS]   (SECONDS over 0, up to 600)"
)

func main() {
	if len(os.Args) < 2 || len(os.Args) > 3 {
		fail(2, usage)
	}
	ctx := attach(waitFrom(2))
	value, err := chromedp.Run(ctx, chromedp.Evaluate[any](os.Args[1]))
	check(err, "the expression did not evaluate")
	out, err := json.Marshal(value)
	check(err, "the value is not JSON")
	fmt.Println(string(out))
}
"""

WEB_EVAL = _WEB_EVAL_HEAD + _GO_SHARED + _WEB_EVAL_MAIN

# (name, language, description, body) rows: SeedSpec's own field order.
WEB_GO_SEEDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "web_read",
        "go",
        "read the visible text of an element in the current browser tab, waiting for "
        "it to show: web_read SELECTOR [SECONDS]",
        WEB_READ,
    ),
    (
        "web_eval",
        "go",
        "run a JavaScript expression in the current browser tab and print its value "
        "as JSON: web_eval EXPRESSION",
        WEB_EVAL,
    ),
)
