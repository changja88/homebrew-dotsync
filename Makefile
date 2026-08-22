# dotsync dev helpers
#
# Usage:
#   make help                  목록 출력
#   make test                  pytest 실행 (.venv/bin/python3 사용)
#   make test-ui               웹 UI 테스트 실행
#   make test-native           Swift 네이티브 테스트 실행
#   make build-app             unsigned 로컬 DotSync.app 빌드
#   make release               인터랙티브 릴리스 (patch/minor/major 선택)

.PHONY: help test test-ui test-native build-app release

PYTHON ?= .venv/bin/python3

help:
	@echo "Targets:"
	@echo "  test                 Run pytest"
	@echo "  test-ui              Run browser UI tests"
	@echo "  test-native          Run native Swift tests"
	@echo "  build-app            Build unsigned local DotSync.app"
	@echo "  release              Interactive release: bumps version, tags, pushes, patches sha256"

test:
	@$(PYTHON) -m pytest

test-ui:
	@$(PYTHON) -m pytest tests/web tests/test_cli_ui.py tests/test_macos_actions.py
	@node --test tests/web/js/state.test.mjs tests/web/js/api-client.test.mjs

test-native:
	@swift test --package-path macos/DotSyncApp

build-app:
	@bash scripts/build_macos_app.sh

release:
	@bash scripts/release.sh
