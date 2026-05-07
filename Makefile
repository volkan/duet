PREFIX ?= $(HOME)/.local
BIN := $(PREFIX)/bin/duet
SRC := $(abspath duet.py)

.PHONY: install uninstall help test
install:  ## symlink duet.py to $(BIN) (PREFIX=... to override)
	@mkdir -p $(dir $(BIN))
	@ln -sfn $(SRC) $(BIN)
	@echo "linked $(BIN) -> $(SRC)"
	@command -v duet >/dev/null && echo "PATH ok: $$(which duet)" || \
		echo "WARN: $(dir $(BIN)) not on PATH; add it to your shell rc"
uninstall: ## remove the symlink
	@rm -f $(BIN) && echo "removed $(BIN)"
test: ## run scripts/smoke.sh
	@bash scripts/smoke.sh
help: ## show targets
	@grep -E '^[a-z_-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ -/'
