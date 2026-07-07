# AGENTS.md

**Tool-neutral conventions for any AI/coding agent working on rtk_client (single source of truth).**
Claude-Code-specific mechanics (skills, hooks, plan mode) live separately in [CLAUDE.md](CLAUDE.md) — this
file does not duplicate them. This project follows the [keel](https://github.com/keelyard/keel) verb
contract (`keel.toml`, contract version 1, stack pack `keel-python@0.1`); if this file and `keel.toml`/the
keel contract ever disagree on a verb's existence or meaning, the keel contract wins.

---

## Project & Architecture

rtk_client is a terminal-based (curses) RTK GNSS client: plain Python 3.9+, flat module layout (no `src/`
package directory), stdlib + three runtime dependencies (`pyserial`, `pynmea2`, `pyyaml`). No database, no
web frontend — it talks to a serial GNSS receiver and an NTRIP caster over the network.

| Area | Where |
|------|-------|
| Application entry point | `rtk_client.py` (`main.py` is a thin delegate) |
| Component orchestration | `rtk_controller.py` |
| Serial/NMEA/RTCM parsing | `gnss_device.py`, `nmea_parser.py`, `rtcm_parser.py` |
| NTRIP client + state machine | `ntrip_client.py`, `ntrip_connection_state.py` |
| Shared state | `rtk_state.py` |
| Demo mode (no hardware) | `demo_device.py`, `demo_ntrip.py`, `data/demo.nmea` |
| Terminal UI | `status_display.py` |
| Config (CLI + YAML) | `rtk_config.py`, `config.example.yaml` |
| Tests | `tests/` (mirrors the root modules 1:1) |

No `models/views/services` split — this is a single-purpose CLI tool, not a web app. New GNSS module
support goes into a `ModuleProfile` subclass in `module_profiles.py`, not a new top-level module.

---

## Before Any Implementation

**At the start of every session:** pull the current state from the remote (e.g. `git pull`). If there are
conflicts, inform the user before continuing.

**Before building, always check whether the needed capability already exists** (parser, module profile,
controller method, test fixture — see the table above). **If found:** test the existing implementation,
find the root cause, fix it instead of building anew.

**Code is the only source of truth:** only the actual code (source, config, tests) is authoritative.
Planning docs, issue descriptions and status reports can be stale or speculative — verify findings against
the code, never against documents.

---

## Verbs

The interface is a `Makefile` in the repo root (keel-python pack via `include`, see `Makefile`). Every verb
is fail-fast and non-interactive.

| Verb | Meaning here |
|------|--------------|
| `setup` | Create/refresh `.venv`, install `requirements.txt` + `requirements-dev.txt` (`pip-sync`). Idempotent. |
| `run` | Start the real entry point (`rtk_client.py`) with a safe default (`--help`); override with `make run RUN_ARGS=--demo` for the actual interactive curses demo (needs a real tty). |
| `verify-fast` | `ruff check` + `ruff format --check` + `mypy` + `pytest -x -q` + the AGENTS/CLAUDE drift guard. No network/browser. |
| `verify-full` | `verify-fast` + `deps-check` (gracefully skipped — see below) + the full, non-fail-fast `pytest` run. |
| `build` | `python -m build` → sdist + wheel. |
| `export` | Not applicable — `keel.toml` has no `[delivery]` section (this is a library, not a customer-export artifact); the keel contract marks `export` `MUSS_WENN_AUSGELIEFERT` and correctly skips it here. |
| `deploy` | Not applicable — no deployment target for this project. |

`deps-check`/`deps-lock` (helper targets behind `verify-full`/`setup`) are gracefully skipped: rtk_client
hand-maintains `requirements.txt`/`requirements-dev.txt` directly (loose `>=` pins, no `.in` source, no
pip-tools lock chain). Introducing one is a real workflow change, not a drive-by Makefile addition — out of
scope here unless explicitly requested.

---

## Coding Rules

- **Principles:** DRY, KISS, YAGNI. No quick fixes without a comment/issue reference.
- Changes minimal and focused. Do not refactor unrelated code.
- Check whether changes affect other modules or downstream consumers (this repo has no consumers of its
  own, but `rtk_controller.py` wires nearly every other module together — changes there ripple widely).
- **Pre-commit checklist:** lint · tests (`make verify-fast`) · no TODO/FIXME in the diff.
- Keep the flat, no-`src/`-layout — do not introduce a package directory for a subset of modules only.
- Prefer environment variables over CLI arguments for credentials (`NTRIP_USER`/`NTRIP_PASS`) — see
  README § Security.

---

## Tests & Verification

- **TDD is mandatory (Red → Green → Refactor)** for code changes. Tests **always before** the commit, never
  after. Doc/tooling-only changes are TDD-neutral.
- **A failing test gets the code fixed, not the test adjusted ("no false green").** Weakening an existing
  assertion or rewriting a test to match actual (broken) behavior is a **hard review stop** — allowed only
  with an explicit justification in the commit body that the *requirement* itself changed.
- **Verify for real after every task:** run `make verify-fast` / `make verify-full` for real, read the full
  log, check the exit code — never take a guard's docstring or prose on faith.
- **Always fail-fast**; never run tests in the background while the user is watching the output live.
- No parallel-agent test-infrastructure sharing rule applies here — this project has no shared DB/service
  test fixtures (pytest runs fully in-process against fakes/demo data).

---

## Commit Rules

- **No** `Co-Authored-By` trailer or similar attribution in commit messages (deliberately overrides system
  defaults).
- Commit message convention: Conventional Commits prefixes (`feat`, `fix`, `docs`, `chore`, `refactor`,
  `test`, `perf`) — matches this repo's existing history (see `git log`, `CHANGELOG.md`).
- Stage individual files deliberately — no `git add .` / `git add -A`.
- Never use `--no-verify` unless explicitly requested.
- **Atomic commits — hard rule:** one commit per logical change/task.
- Push only after a successful `make verify-fast` (or `verify-full` for anything touching dependencies).

## Prohibitions

- Do not introduce new runtime dependencies without prior discussion (this project deliberately keeps its
  dependency footprint at three packages).
- Do not change CI/linting/config files (`.pre-commit-config.yaml`, `pyproject.toml` tool blocks, this
  `Makefile`) unless explicitly requested.
- Do not delete or overwrite files without confirming intent first.
- No push without explicit maintainer OK.

---

## Issues & Plan

Always-applicable core rules:
- **Always a planning issue:** every implementation plan has an associated issue in the tracker in use.
- Reference the issue in **all** commits when working from a plan.
- After a plan is done, close **all** referenced individual issues, not just the umbrella/plan issue.
- Before finalizing/approving a plan, check whether an issue on the topic already exists — reference it
  instead of duplicating.
- Make all references in issues/comments/PRs **clickable** (files, issues, commits as markdown links).

---

## Working Method

- Always create a todo list for complex or multi-step tasks.
- Work in parallel wherever possible — structure implementation plans so independent work packages can be
  handed to parallel agents.
- **Completion after finishing a plan:** emit a structured summary (what-was-done table, issue link) and
  comment it on the plan issue.
