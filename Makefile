PREFIX ?= $(HOME)/.local
BIN := $(PREFIX)/bin/duet
SRC := $(abspath duet.py)

.PHONY: install uninstall help test ci unit-test smoke-test complexity reasoning-check distribution-check package-check plugin-check loop-test build
install:  ## symlink duet.py to $(BIN) (PREFIX=... to override)
	@mkdir -p $(dir $(BIN))
	@ln -sfn $(SRC) $(BIN)
	@echo "linked $(BIN) -> $(SRC)"
	@command -v duet >/dev/null && echo "PATH ok: $$(which duet)" || \
		echo "WARN: $(dir $(BIN)) not on PATH; add it to your shell rc"
uninstall: ## remove the symlink
	@rm -f $(BIN) && echo "removed $(BIN)"
test: unit-test smoke-test ## run unit tests then scripts/smoke.sh
ci: unit-test reasoning-check smoke-test complexity distribution-check ## run the fast local merge gate
unit-test: ## run pure-function unit tests (stdlib unittest, no agents)
	@python3 -m unittest discover -s tests
smoke-test: ## run scripts/smoke.sh dry-run regression checks
	@bash scripts/smoke.sh
complexity: ## fail if any function exceeds the cyclomatic-complexity/length budget
	@python3 scripts/check_complexity.py
reasoning-check: ## verify the reasoning-effort translation layer (no agents)
	@python3 scripts/check_reasoning_levels.py
distribution-check: ## validate pyproject/plugin manifests and source metadata
	@python3 scripts/check_distribution_metadata.py
package-check: ## build artifacts and validate wheel/sdist metadata
	@if command -v uv >/dev/null 2>&1; then uv build; else python3 -m build; fi
	@python3 scripts/check_distribution_metadata.py --artifacts dist
	@python3 scripts/check_installed_wheel.py dist
plugin-check: ## validate the Claude Code plugin (root marketplace + narrowed plugin root)
	@claude plugin validate .
	@claude plugin validate plugins/duet-claude
loop-test: ## run real end-to-end loop scenarios (default Claude/Codex; retarget any backend via LOOP_TEST_ARGS="--lead-backend … --partner-backend …"; slow, costs model turns)
	@python3 scripts/duet_loop_e2e.py $(LOOP_TEST_ARGS)
build: ## build sdist+wheel into dist/ (needs current python3 to import build: python3 -m pip install build)
	@python3 -m build
help: ## show targets
	@grep -E '^[a-z_-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ -/'
