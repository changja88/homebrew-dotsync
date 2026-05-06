# dotsync dev helpers
#
# Usage:
#   make help        목록 출력
#   make test        pytest 실행 (.venv/bin/python3 사용)

.PHONY: help test

PYTHON ?= .venv/bin/python3

help:
	@echo "Targets:"
	@echo "  test         Run pytest"

test:
	@$(PYTHON) -m pytest
