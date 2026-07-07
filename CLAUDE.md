# CLAUDE.md

Claude Code control file. **The tool-neutral conventions (Project & Architecture, Verbs, Coding Rules,
Tests & Verification, Commit Rules, Prohibitions, Issues & Plan, Working Method) live in
[AGENTS.md](AGENTS.md) — single source of truth.** This file only adds Claude-Code-specific mechanics and
does not duplicate the neutral rules.

> **Order:** read [AGENTS.md](AGENTS.md) first (applies to any agent), then this file for the
> Claude-Code-specific mechanics.

---

## Before Every Commit

Run `make verify-fast` (or `make verify-full` for dependency changes) for real, **read the full log**,
never pipe it through `tail`/a pipe — a pipe's exit code is the status of its last stage, not of
`make verify-fast`, and it can mask a real failure. Check the exit code separately, don't guess from a
truncated log.

## Run Guards For Real

Guard scripts (e.g. the AGENTS/CLAUDE drift guard invoked by `make verify-fast`) are **executed**, not just
read — their exit code is the finding, not their docstring.

---

## Skills / Hooks (if used)

- No project-specific Claude Code skills/hooks are configured for rtk_client at this time. General-purpose
  harness skills (TDD, systematic debugging, etc.) apply as usual and concretize the neutral rules in
  AGENTS.md — they do not replace them.

---

## Issue & Plan Workflow (Claude-specific)

- No auto-hook creates a planning issue on exiting plan mode in this repo — create the planning issue
  manually (fulfills the "always a planning issue" rule in AGENTS.md).
- **Before finalizing a plan**, check whether an issue on the topic already exists — reference it instead
  of duplicating.

---

## Completion Summary After Finishing a Plan

After every completed plan, emit the structured summary (what-was-done table, issue link) — **in the
chat** and, if the tracker supports comments, **as a comment on the plan issue**.

---

## Parallel Agents & Worktrees

Not applicable — rtk_client has no established worktree/parallel-agent convention of its own at this time;
default to a single active agent per branch until one is defined.
