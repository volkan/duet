PREFIX ?= $(HOME)/.local
BIN := $(PREFIX)/bin/duet
SRC := $(abspath duet.py)

.PHONY: install uninstall help test ci unit-test smoke-test complexity reasoning-check loop-test
install:  ## symlink duet.py to $(BIN) (PREFIX=... to override)
	@mkdir -p $(dir $(BIN))
	@ln -sfn $(SRC) $(BIN)
	@echo "linked $(BIN) -> $(SRC)"
	@command -v duet >/dev/null && echo "PATH ok: $$(which duet)" || \
		echo "WARN: $(dir $(BIN)) not on PATH; add it to your shell rc"
uninstall: ## remove the symlink
	@rm -f $(BIN) && echo "removed $(BIN)"
test: unit-test smoke-test ## run unit tests then scripts/smoke.sh
ci: unit-test reasoning-check smoke-test complexity ## run every check the CI merge gate runs
unit-test: ## run pure-function unit tests (stdlib unittest, no agents)
	@python3 -m unittest discover -s tests
smoke-test: ## run scripts/smoke.sh dry-run regression checks
	@bash scripts/smoke.sh
complexity: ## fail if any function exceeds the cyclomatic-complexity/length budget
	@python3 scripts/check_complexity.py
reasoning-check: ## verify the reasoning-effort translation layer (no agents)
	@python3 scripts/check_reasoning_levels.py
loop-test: ## run real Claude/Codex end-to-end loop scenarios (slow, costs model turns)
	@python3 scripts/duet_loop_e2e.py $(LOOP_TEST_ARGS)
help: ## show targets
	@grep -E '^[a-z_-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ -/'
