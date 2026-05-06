# dotsync dev helpers
#
# Usage:
#   make help                  목록 출력
#   make test                  pytest 실행 (.venv/bin/python3 사용)
#   make local-serena-shim     generated Serena shim을 ~/.zshrc managed block에 반영

.PHONY: help test local-serena-shim print-serena-shim

PYTHON ?= .venv/bin/python3
ZSHRC ?= $(HOME)/.zshrc

help:
	@echo "Targets:"
	@echo "  test                 Run pytest"
	@echo "  print-serena-shim    Print generated local Serena zsh shim"
	@echo "  local-serena-shim    Install generated local Serena zsh shim into ZSHRC=$(ZSHRC)"

test:
	@$(PYTHON) -m pytest

print-serena-shim:
	@$(PYTHON) local_dev/serena_mcp_management/serena_zsh_shim.py

local-serena-shim:
	@$(PYTHON) local_dev/serena_mcp_management/serena_zsh_shim.py --install-zshrc --rc-path "$(ZSHRC)"
