# dotsync dev helpers
#
# Usage:
#   make help                  목록 출력
#   make test                  pytest 실행 (.venv/bin/python3 사용)
#   make build-app             unsigned 로컬 DotSync.app 빌드
#   make release               인터랙티브 릴리스 (patch/minor/major 선택)

.PHONY: help test build-app release

PYTHON ?= .venv/bin/python3

help:
	@echo "Targets:"
	@echo "  test                 Run pytest"
	@echo "  build-app            Build unsigned local DotSync.app"
	@echo "  release              Interactive release: bumps version, tags, pushes, patches sha256"

test:
	@$(PYTHON) -m pytest

build-app:
	@bash scripts/build_macos_app.sh

release:
	@bash scripts/release.sh
