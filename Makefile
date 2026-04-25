# dotsync release / dev helpers
#
# Usage:
#   make help        목록 출력
#   make test        pytest 실행 (.venv/bin/python3 사용)
#   make release     인터랙티브 릴리스 (major/minor/patch 선택)

.PHONY: help test release

PYTHON ?= .venv/bin/python3

help:
	@echo "Targets:"
	@echo "  test       Run pytest"
	@echo "  release    Interactive release: bumps version, tags, pushes, patches sha256"

test:
	@$(PYTHON) -m pytest

release:
	@bash scripts/release.sh
