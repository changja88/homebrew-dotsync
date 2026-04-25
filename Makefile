# dotsync release / dev helpers
#
# Usage:
#   make help        목록 출력
#   make test        pytest 실행 (.venv/bin/python3 사용)
#   make demo        Walk through the full first-time user journey
#                    (brew install → welcome → init → from → status)
#   make release     인터랙티브 릴리스 (major/minor/patch 선택)

.PHONY: help test demo release

PYTHON ?= .venv/bin/python3

help:
	@echo "Targets:"
	@echo "  test       Run pytest"
	@echo "  demo       Step-by-step walkthrough of the first-time install + use"
	@echo "  release    Interactive release: bumps version, tags, pushes, patches sha256"

test:
	@$(PYTHON) -m pytest

demo:
	@bash scripts/demo.sh

release:
	@bash scripts/release.sh
