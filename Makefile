PREFIX ?= $(HOME)/.local
BIN := $(PREFIX)/bin/duet
SRC := $(abspath duet.py)

.PHONY: install uninstall help test unit-test smoke-test loop-test
install:  ## symlink duet.py to $(BIN) (PREFIX=... to override)
	@mkdir -p $(dir $(BIN))
	@ln -sfn $(SRC) $(BIN)
	@echo "linked $(BIN) -> $(SRC)"
	@command -v duet >/dev/null && echo "PATH ok: $$(which duet)" || \
		echo "WARN: $(dir $(BIN)) not on PATH; add it to your shell rc"
uninstall: ## remove the symlink
	@rm -f $(BIN) && echo "removed $(BIN)"
test: unit-test smoke-test ## run unit tests then scripts/smoke.sh
unit-test: ## run pure-function unit tests (stdlib unittest, no agents)
	@python3 -m unittest discover -s tests
smoke-test: ## run scripts/smoke.sh dry-run regression checks
	@bash scripts/smoke.sh
loop-test: ## run real Claude/Codex end-to-end loop scenarios (slow, costs model turns)
	@python3 scripts/duet_loop_e2e.py $(LOOP_TEST_ARGS)
help: ## show targets
	@grep -E '^[a-z_-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ -/'
