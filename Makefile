# dotsync dev helpers
#
# Usage:
#   make help                  목록 출력
#   make test                  pytest 실행 (.venv/bin/python3 사용)
#   make release               인터랙티브 릴리스 (patch/minor/major 선택)

.PHONY: help test release app

PYTHON ?= .venv/bin/python3

help:
	@echo "Targets:"
	@echo "  test                 Run pytest"
	@echo "  release              Interactive release: bumps version, tags, pushes, patches sha256"
	@echo "  app                  Build, sign and zip dotsync.app into dist/ (VERSION=x.y.z to override)"

test:
	@$(PYTHON) -m pytest

release:
	@bash scripts/release.sh

app:
	@bash scripts/build-app.sh $(or $(VERSION),$(shell grep -E '^version = ' pyproject.toml | cut -d'"' -f2))
