# System driver — reading checklist

- Tick a box by changing `[ ]` to `[x]`.
- Titles are copied from `surfaces.md` and `features/*.md`. Made 2026-11-12.

## Concept — [surfaces.md](surfaces.md)

### 3.1 The surface test

- [ ] **Story:** As a maintainer, I want one test for "is this a surface?", so drivers are only
      built for things that need them.
  - [ ] A thing that passes is a surface
  - [ ] A thing that fails is a plain script

### 3.2 The surface is the registry

- [ ] **Story:** As the human, I want no list of jobs that can drift, so "what is running" is always
      true.
  - [ ] Listing asks the surface
  - [ ] A job dies outside grim
  - [ ] grim restarts

### 3.3 Attach, act, leave

- [ ] **Story:** As the agent, I want each script to be one short visit, so runs stay under the
      timeout.
  - [ ] One visit
  - [ ] State lasts between visits
  - [ ] Nothing is held open

### 3.4 One driver contract

- [ ] **Story:** As the agent, I want every surface to feel the same, so I learn one shape.
  - [ ] Six moves cover every surface
  - [ ] A wrong call fails loudly
  - [ ] Drivers are ordinary scripts

### 3.5 Wait on a signal

- [ ] **Story:** As the agent, I want to wait without sleeping, so I spend no turns on polling.
  - [ ] The signal arrives
  - [x] The signal came first
  - [ ] The signal never comes

### 3.6 Every act is on the record

- [ ] **Story:** As the human, I want to see what was done to each surface, so I can trust and
      replay it.
  - [ ] An act leaves a row
  - [ ] Replay the story
  - [ ] A secret is named, never passed

### 3.7 Surface text is data

- [ ] **Story:** As the human, I want text read from a surface to never steer the agent, so a web
      page or another agent cannot hijack it.
  - [ ] Text that gives orders

### 3.8 The human can see and step in

- [ ] **Story:** As the human, I want to watch any surface live and take over, so I am never locked
      out.
  - [ ] Every surface can be watched
  - [ ] Take over

### 3.9 Adding a surface

- [ ] **Story:** As a maintainer, I want a new surface to cost only seeds, so the core stays closed.
  - [ ] A new driver is a set of seeds
  - [ ] A recipe mixes surfaces

### 3.10 The agent writes its own seeds and drivers

- [ ] **Story:** As the agent, I want to write a seed or a whole driver myself when none fits, so no
      surface waits for a grim release.
  - [ ] The agent writes a driver itself
  - [ ] A home-grown seed is a full seed
  - [ ] A home-grown driver lasts
  - [ ] A shipped seed falls short

## Features — one file each

### 1. Jobs are tmux windows — [01-jobs.md](features/01-jobs.md)

- [ ] **Story:** As the agent, I want work that outlives one run, so servers and builds don't die at
      the timeout.
  - [ ] Start a job
  - [ ] A job name is taken
  - [ ] No shell in the command
  - [ ] List jobs with no registry file
  - [ ] Read a job's screen
  - [ ] Type into a job
  - [ ] A dead job keeps its story
  - [ ] Stop a job
  - [ ] The old background seeds retire

### 2. Wait on a signal — [02-waiting.md](features/02-waiting.md)

- [ ] **Story:** As the agent, I want to wait without sleeping, so I spend no turns on polling.
  - [ ] The signal arrives
  - [ ] The signal came first
  - [x] A job signals when it ends
  - [ ] The signal never comes
  - [ ] A wait cannot outlive the time budget

### 3. tmux is the dashboard — [03-dashboard.md](features/03-dashboard.md)

- [ ] **Story:** As the human, I want one place to watch everything grim drives, and to step in.
  - [ ] The agent starts at home
  - [ ] Already inside tmux
  - [ ] A headless run has no window
  - [ ] See it all
  - [ ] Take over a pane
  - [ ] The prompt teaches surfaces

### 4. Delegate to another agent — [04-delegates.md](features/04-delegates.md)

- [ ] **Story:** As the agent, I want to hand a hard task to another agent and get the answer back.
  - [ ] Same seeds, any agent
  - [ ] A long brief arrives whole
  - [ ] First-run dialogs are known
  - [x] Collect the answer
  - [x] The delegate forgot the contract
  - [ ] The delegate needs input
  - [ ] Follow up
  - [ ] Stop a delegate
  - [ ] A delegate's powers are stated

### 5. Go scripts build once — [05-go-build.md](features/05-go-build.md)

- [ ] **Story:** As the agent, I want Go scripts to use a third-party package and start fast, so the
      browser seeds can be written with chromedp.
  - [ ] Build against the shared module
  - [ ] Run from the cache
  - [ ] A new version rebuilds
  - [ ] The cache has a cap
  - [ ] A build error is the script's error

### 6. Drive one long-lived Chrome — [06-browser.md](features/06-browser.md)

- [ ] **Story:** As the agent, I want a real browser that keeps tabs and logins between scripts.
  - [ ] First use starts Chrome
  - [ ] Later use shares it
  - [ ] State lasts between scripts
  - [ ] Waits are built in
  - [ ] A thing that never shows
  - [ ] Reading is capped
  - [ ] Run JavaScript
  - [ ] Take a picture
  - [ ] A secret is typed by name

### 7. Recipes compound — [07-recipes.md](features/07-recipes.md)

- [ ] **Story:** As the human, I want every driven task to leave a script I can reuse.
  - [ ] Reuse before write
  - [ ] New work becomes a recipe
  - [ ] A probe stays a one-off
  - [ ] Recipes compose through grim run
  - [ ] Everything is on the record
  - [ ] The library grows

### 8. Guard rails — [08-guard-rails.md](features/08-guard-rails.md)

- [ ] **Story:** As the human, I want the driver to be safe to leave running.
  - [ ] Screen text is data
  - [ ] Only grim's windows are touched
  - [ ] A wrong call fails loudly
  - [ ] Every wait has a cap
  - [ ] A needed tool is missing
  - [ ] The doctor knows the new needs
  - [ ] The container can drive too

## Progress

```bash
# Prints ticked boxes, then open boxes. `grep -c` counts matching lines.
# Ref: https://pubs.opengroup.org/onlinepubs/9699919799/utilities/grep.html
grep -c '^ *- \[x\]' docs/system-driver-checklist.md
grep -c '^ *- \[ \]' docs/system-driver-checklist.md
```
