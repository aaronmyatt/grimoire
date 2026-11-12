# Feature 6 — Drive one long-lived Chrome

Status: **built 2026-11-12.** `web_open` is python; `web_read`, `web_eval`, `web_click`,
`web_type` and `web_shot` are Go, written for chromedp v0.21.0. Tests drive a real
headless Chrome (`src/grim/seeds/test_bodies_web.py`).
Concept: [surfaces.md](../surfaces.md). Slice: `seeds`. Build order: 6 of 8.
Needs: [01-jobs.md](01-jobs.md) and [05-go-build.md](05-go-build.md).

- As the agent, I want a real browser that keeps tabs and logins between scripts.
- The seeds are Go with chromedp. The agent composes them from python.
- CDP is the Chrome DevTools Protocol, the wire chromedp speaks.

```gherkin
# Ref: https://pkg.go.dev/github.com/chromedp/chromedp#NewRemoteAllocator
# Ref: https://chromedevtools.github.io/devtools-protocol/
@seeds
Feature: Drive one long-lived Chrome

  Scenario: First use starts Chrome
    Given no job "chrome" is live
    When the agent runs web_open "http://localhost:8000"
    Then Chrome runs as tmux job "chrome" with profile ~/.grimoire/chrome
    And never with my daily profile
    And the page is open in a tab

  Scenario: Later use shares it
    Given job "chrome" is live
    When any web_ seed runs
    Then it attaches to that Chrome
    And no second Chrome starts

  Scenario: State lasts between scripts
    Given one script logged in with web_type and web_click
    When a later script runs web_read ".account-name"
    Then it reads the name without logging in again

  Scenario: Waits are built in
    Given the page shows ".results" after 2 seconds
    When the agent runs web_read ".results"
    Then it waits, then returns the text

  Scenario: A thing that never shows
    When the agent runs web_click "#missing"
    Then it exits 124 naming the selector
    And nothing was clicked

  Scenario: Reading is capped
    Given a page with 2 MB of text
    When the agent runs web_read "body"
    Then it gets text up to a stated cap
    And it is told the text was cut

  Scenario: Run JavaScript
    When the agent runs web_eval "document.title"
    Then it prints the result as JSON

  Scenario: Take a picture
    When the agent runs web_shot "/tmp/page.png"
    Then a PNG is written there
    And the path is printed

  Scenario: A secret is typed by name
    Given a password lives in env var "SITE_PASSWORD"
    When the agent runs web_type "#password" with --env SITE_PASSWORD
    Then the field is filled
    And the value is in no argv and no execution row
```

## Checked so far

- Nothing in this feature is checked yet.
- From the library: 5+ one-off headless-Chrome scripts exist, each with its own waits.
