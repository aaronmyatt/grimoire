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
  - [x] One visit
        `seeds/test_bodies_tmux.py::test_tmux_start_opens_a_tagged_window_and_returns_at_once`
  - [x] State lasts between visits
        `seeds/test_bodies_web.py::test_a_login_lasts_between_scripts_and_the_secret_is_typed_by_name`
  - [ ] Nothing is held open

### 3.4 One driver contract

- [ ] **Story:** As the agent, I want every surface to feel the same, so I learn one shape.
  - [ ] Six moves cover every surface
  - [ ] A wrong call fails loudly
  - [ ] Drivers are ordinary scripts

### 3.5 Wait on a signal

- [ ] **Story:** As the agent, I want to wait without sleeping, so I spend no turns on polling.
  - [x] The signal arrives
  - [x] The signal came first
  - [ ] The signal never comes

### 3.6 Every act is on the record

- [ ] **Story:** As the human, I want to see what was done to each surface, so I can trust and
      replay it.
  - [x] An act leaves a row
        `test_recipes_e2e.py::test_a_recipe_drives_tmux_through_grim_run_and_every_call_is_on_the_record`
  - [ ] Replay the story
  - [x] A secret is named, never passed
        `seeds/test_bodies_web.py::test_a_login_lasts_between_scripts_and_the_secret_is_typed_by_name`

### 3.7 Surface text is data

- [ ] **Story:** As the human, I want text read from a surface to never steer the agent, so a web
      page or another agent cannot hijack it.
  - [ ] Text that gives orders

### 3.8 The human can see and step in

- [ ] **Story:** As the human, I want to watch any surface live and take over, so I am never locked
      out.
  - [ ] Every surface can be watched
  - [x] Take over
        `seeds/test_bodies_agent.py::test_a_human_can_answer_in_the_pane_and_the_agent_can_follow_up`

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
  - [x] Start a job
        `seeds/test_bodies_tmux.py::test_tmux_start_opens_a_tagged_window_and_returns_at_once`
  - [x] A job name is taken
  - [x] No shell in the command
  - [ ] List jobs with no registry file
  - [x] Read a job's screen
  - [x] Type into a job
        `seeds/test_bodies_tmux.py::test_tmux_send_types_text_as_is_then_presses_enter`
  - [x] A dead job keeps its story
  - [x] Stop a job
        `seeds/test_bodies_tmux.py::test_tmux_stop_closes_the_window_and_tmux_list_forgets_the_job`
  - [x] The old background seeds retire
        `seeds/test_loader.py::test_load_seeds_retires_the_background_trio_and_deletes_nothing`
        `seeds/test_loader.py::test_load_seeds_leaves_a_taken_over_retired_name_alone`

### 2. Wait on a signal — [02-waiting.md](features/02-waiting.md)

- [ ] **Story:** As the agent, I want to wait without sleeping, so I spend no turns on polling.
  - [ ] The signal arrives
  - [x] The signal came first
        `seeds/test_bodies_tmux.py::test_tmux_wait_returns_at_once_for_a_signal_sent_before_it`
  - [x] A job signals when it ends
  - [x] The signal never comes
  - [ ] A wait cannot outlive the time budget

### 3. tmux is the dashboard — [03-dashboard.md](features/03-dashboard.md)

- [ ] **Story:** As the human, I want one place to watch everything grim drives, and to step in.
  - [x] The agent starts at home
        `adapter/test_home.py::test_home_command_runs_the_agent_in_window_agent_with_the_callers_env`
        `adapter/test_home.py::test_go_home_execs_the_home_command_when_attended`
  - [x] Already inside tmux
        `adapter/test_home.py::test_stays_here` (the TMUX case)
        `seeds/test_bodies_tmux.py::test_tmux_start_opens_a_tagged_window_and_returns_at_once`
  - [x] A headless run has no window
        `adapter/test_home.py::test_go_home_returns_when_headless`
  - [ ] See it all
  - [x] Take over a pane
        `seeds/test_bodies_agent.py::test_a_human_can_answer_in_the_pane_and_the_agent_can_follow_up`
  - [x] The prompt teaches surfaces

### 4. Delegate to another agent — [04-delegates.md](features/04-delegates.md)

- [x] **Story:** As the agent, I want to hand a hard task to another agent and get the answer back.
      Every scenario below is ticked.
  - [x] Same seeds, any agent
        `seeds/test_bodies_agent.py::test_agent_start_runs_any_agent_and_pastes_the_brief_as_one_message`
  - [x] A long brief arrives whole
  - [x] First-run dialogs are known
        `seeds/test_bodies_agent.py::test_agent_start_answers_the_trust_dialog_before_it_sends_the_brief`
  - [x] Collect the answer
  - [x] The delegate forgot the contract
  - [x] The delegate needs input
        `seeds/test_bodies_agent.py::test_agent_read_names_the_state_and_shows_the_screen_when_there_is_no_result` (the ask case)
  - [x] Follow up
        `seeds/test_bodies_agent.py::test_a_human_can_answer_in_the_pane_and_the_agent_can_follow_up`
  - [x] Stop a delegate
        `seeds/test_bodies_agent.py::test_a_delegate_is_listed_and_stopped_like_any_job_and_keeps_its_result`
  - [x] A delegate's powers are stated
  - [x] A dialog eats the brief
        `seeds/test_bodies_agent.py::test_agent_start_exits_1_when_a_dialog_eats_the_enter_and_never_says_started`
  - [x] Ask two delegates headless
        `seeds/test_bodies_headless.py::test_agent_ask_both_opens_two_agent_windows_and_both_see_the_same_brief`
        `adapter/test_prompt_jobs.py::test_replay_brief_ask_wait_compare_as_the_prompt_teaches`

### 5. Go scripts build once — [05-go-build.md](features/05-go-build.md)

- [ ] **Story:** As the agent, I want Go scripts to use a third-party package and start fast, so the
      browser seeds can be written with chromedp.
  - [x] Build against the shared module
        `exec/test_gobuild.py::test_first_run_builds_against_the_shared_module_and_runs_in_the_scripts_cwd`
  - [x] Run from the cache
        `exec/test_gobuild.py::test_second_run_uses_the_cached_binary_with_no_compile`
  - [x] A new version rebuilds
        `exec/test_gobuild.py::test_a_new_version_builds_a_new_binary_and_leaves_the_old_one_unused`
  - [x] The cache has a cap
        `exec/test_gobuild.py::test_the_cache_removes_the_least_recently_used_binary_at_its_cap`
  - [ ] A build error is the script's error

### 6. Drive one long-lived Chrome — [06-browser.md](features/06-browser.md)

- [x] **Story:** As the agent, I want a real browser that keeps tabs and logins between scripts.
      Every scenario below is ticked.
  - [x] First use starts Chrome
        `seeds/test_bodies_web.py::test_web_open_starts_chrome_as_a_job_on_its_own_profile_and_opens_the_tab`
        `seeds/test_bodies_web.py::test_web_open_refuses_a_daily_chrome_profile`
  - [x] Later use shares it
        `seeds/test_bodies_web.py::test_web_open_again_attaches_to_the_same_chrome`
  - [x] State lasts between scripts
        `seeds/test_bodies_web.py::test_a_login_lasts_between_scripts_and_the_secret_is_typed_by_name`
  - [x] Waits are built in
        `seeds/test_bodies_web.py::test_web_read_waits_for_an_element_then_returns_its_text`
  - [x] A thing that never shows
        `seeds/test_bodies_web.py::test_web_click_on_a_thing_that_never_shows_exits_124_and_clicks_nothing`
  - [x] Reading is capped
        `seeds/test_bodies_web.py::test_web_read_caps_a_huge_text_and_says_it_was_cut`
  - [x] Run JavaScript
        `seeds/test_bodies_web.py::test_web_eval_prints_the_value_as_json`
  - [x] Take a picture
        `seeds/test_bodies_web.py::test_web_shot_writes_a_png_and_prints_its_path`
  - [x] A secret is typed by name
        `seeds/test_bodies_web.py::test_a_login_lasts_between_scripts_and_the_secret_is_typed_by_name`

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
  - [x] A needed tool is missing
        `seeds/test_bodies_tmux.py::test_tmux_start_without_tmux_exits_127_naming_tmux`
        `seeds/test_bodies_web.py::test_web_open_without_go_exits_127_naming_go_and_sets_nothing_up`
        `seeds/test_bodies_web.py::test_web_open_without_chrome_exits_127_and_starts_nothing`
        `seeds/test_bodies_agent.py::test_agent_start_without_the_agent_program_exits_127_naming_it`
  - [ ] The doctor knows the new needs
  - [ ] The container can drive too

## Progress

```bash
# Prints ticked boxes, then open boxes. `grep -c` counts matching lines.
# Ref: https://pubs.opengroup.org/onlinepubs/9699919799/utilities/grep.html
grep -c '^ *- \[x\]' docs/system-driver-checklist.md
grep -c '^ *- \[ \]' docs/system-driver-checklist.md
```
