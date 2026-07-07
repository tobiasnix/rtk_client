# Makefile — rtk_client adoption of the keel-python stack pack.
#
# This project consumes keel-python (see KEEL_PYTHON_DIR below) via `include`
# rather than copy-pasting recipes: setup/verify-fast/verify-full/build come
# from make/python.mk, this file only supplies the project-specific
# parameters/overrides and the mandatory `run` override (the pack ships `run`
# as a deliberate override point, see python.mk).
#
# Pack location: adjust KEEL_PYTHON_DIR (env var or here) if keel-python is
# vendored at a different path than the local dev checkout default below.
KEEL_PYTHON_DIR ?= /work/keel-python
include $(KEEL_PYTHON_DIR)/make/python.mk

# Location of the keel core repo (contract/, guards/) — only needed here for
# the tool-neutral AGENTS.md/CLAUDE.md drift guard, which is core-owned (not
# part of the keel-python pack, since it applies to every stack). Adjust if
# the core checkout lives elsewhere.
KEEL_CORE_DIR ?= /work/keelyard-keel

# ── Project-specific overrides (after include, per pack convention) ──────

# rtk_client is a flat-layout project (no src/ dir): all modules live at the
# repo root, tests/ holds the test suite. Both defaults (".") already match
# this layout, restated here for clarity/documentation rather than as a
# functional change.
LINT_PATHS      := .
TYPECHECK_PATHS := .

# `run`: the pack requires this as a projectspecific override point.
# rtk_client's real entry point (rtk_client.py) is a curses full-screen TUI
# that either talks to serial hardware or, with --demo, replays canned NMEA
# data — either way it is an interactive, blocking process that needs a real
# tty. That is a legitimate "Dev-Modus starten" for a human at a terminal,
# but not something a generic `make run` should launch unattended (it would
# hang indefinitely in any non-interactive context, e.g. this very proof
# run). Honest compromise: `make run` invokes the real, unmodified entry
# point with a safe default argument (--help, non-blocking, exit 0) so the
# target is real and verifiable; a developer who wants the actual
# interactive demo overrides the argument explicitly:
#   make run RUN_ARGS=--demo
RUN_ARGS ?= --help

run:
	$(VENV_PY) rtk_client.py $(RUN_ARGS)

# deps-lock/deps-check: no override needed. rtk_client hand-maintains
# requirements.txt/requirements-dev.txt directly (loose >= pins, no
# pip-tools .in source, no hashes) — there is no requirements.in in this
# repo. The pack's deps-lock/deps-check targets already degrade gracefully
# (skip with exit 0) when REQ_IN is absent (see python.mk), so verify-full
# stays green without inventing a lock chain this project never asked for.

# ── AGENTS.md/CLAUDE.md drift guard (core-owned, not part of keel-python) ─
# Cheap, no network/browser — wired into verify-fast as an extra
# prerequisite (see make/python.mk's own verify-fast recipe; this rule adds
# a prerequisite without overriding that recipe, so both run).
.PHONY: agent-docs-sync-check
agent-docs-sync-check:
	$(VENV_PY) $(KEEL_CORE_DIR)/guards/verify_agent_docs_sync.py --config .keel/agent_docs_sync.toml

verify-fast: agent-docs-sync-check
