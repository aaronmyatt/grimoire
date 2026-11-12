# Feature 5 — Go scripts build once

Status: **built 2026-11-12** (`src/grim/exec/gobuild.py`, tests in `test_gobuild.py`).
Concept: [surfaces.md](../surfaces.md). Slice: `exec`. Build order: 5 of 8.

- As the agent, I want Go scripts to use a third-party package and start fast, so the
  browser seeds can be written with chromedp.
- Today `go` dispatches as `go run FILE`. That cannot see a third-party package.

```gherkin
# Ref: https://pkg.go.dev/cmd/go#hdr-Compile_packages_and_dependencies
@exec
Feature: Go scripts build once

  Scenario: Build against the shared module
    Given ~/.grimoire/go/go.mod pins chromedp
    When a Go script runs for the first time
    Then it is built with `go build -C ~/.grimoire/go`
    And the binary runs in the script's own working directory

  Scenario: Run from the cache
    Given the script ran before at this version
    When it runs again
    Then the cached binary runs with no compile     # keyed by body hash

  Scenario: A new version rebuilds
    Given the script was updated
    When it runs
    Then a new binary is built for the new body hash
    And the old binary is not used

  Scenario: The cache has a cap
    Given the cache holds its maximum number of binaries
    When one more is built
    Then the least recently used binary is removed

  Scenario: A build error is the script's error
    Given a Go script that does not compile
    When it runs
    Then the run exits nonzero with the compiler's message
    And it is recorded as a failed execution like any other
```

## Checked so far

- go 1.27, offline, with a stand-in package, 2026-11-12:
  - `go run FILE` outside a module fails on a third-party import.
  - `go build -C <module> -o <bin> FILE` works, and the binary keeps its own working
    directory.
  - A cached rebuild took 70 ms.
- Not checked: chromedp itself. It needs a download.
