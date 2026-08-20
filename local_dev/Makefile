# local_dev: dotsync 저장소와 무관한 내부 개발용 도구.
# 여기에 추가되는 타겟은 공개 dotsync 사용자에게 노출되면 안 된다.
#
# 워크플로:
#   1) 이 디렉토리에서 launcher 코드를 수정한다.
#   2) `make install-shim` 으로 안정 위치($STABLE_DIR)에 코드를 복사한 뒤
#      ~/.zshrc 의 managed block을 안정 위치로 갱신한다.
#   ~/.zshrc 의 SERENA_AGENT_LAUNCHER 는 항상 안정 위치를 가리키므로, 이 dev 저장소를 옮기거나 지워도
#   shim 동작은 영향받지 않는다. 변경을 셸에 반영하려면 매번 install-shim 을 다시 돌려야 한다.
#
# 런타임 인터프리터(SERENA_AGENT_PYTHON)는 serena_zsh_shim.py 의 default_python_executable()
# 이 고르는 "안정적인 system-managed python" 이다 — Homebrew/python.org, 1순위는
# /opt/homebrew/bin/python3.12 (dotsync formula 의 python@3.12 의존성으로 항상 존재).
# uv 가 만든 일회성 managed python 은 나중에 GC 되며 SERENA_AGENT_PYTHON 을 dangling 으로
# 만들기 때문에(v0.1.x uv-3.13 사고) 더이상 쓰지 않는다. 그래서 별도 venv 를 만들지 않는다.
# launcher 는 stdlib only 라 어떤 3.12+ 든 된다.
#
# Override:
#   make install-shim ZSHRC=/path/to/.zshrc STABLE_DIR=/some/where PYTHON=/path/to/python3
#   # 기록될 interpreter 를 강제로 고정하려면:
#   make install-shim PYTHON_EXECUTABLE=/opt/homebrew/bin/python3.12

.PHONY: help install-shim

ZSHRC ?= $(HOME)/.zshrc
STABLE_DIR ?= $(HOME)/Desktop/dotsync_config/agent_launcher
DOTSYNC_CONFIG_DIR ?= $(abspath $(STABLE_DIR)/..)
LIVE_HOME ?= $(HOME)
# generator 를 "실행할" python (stdlib only 라 3.9+ 면 충분; 기록될 interpreter 와는 별개).
PYTHON ?= python3
# 비우면 default_python_executable() 자동 선택(권장). 채우면 그 값을 그대로 기록.
PYTHON_EXECUTABLE ?=

SRC_TREE  := serena_mcp_management
DEST_TREE := $(STABLE_DIR)/local_dev/$(SRC_TREE)

help:
	@echo "local_dev — internal-only development tooling (not part of dotsync)"
	@echo ""
	@echo "Targets:"
	@echo "  install-shim     Mirror launcher, update user guidance, and write the managed zsh block"

install-shim:
	@mkdir -p "$(DEST_TREE)"
	@rsync -a --delete --delete-excluded --exclude '__pycache__' --exclude '*.pyc' \
	    "$(SRC_TREE)/" "$(DEST_TREE)/"
	@"$(PYTHON)" "$(DEST_TREE)/user_scope_guidance.py" \
	    --config-root "$(DOTSYNC_CONFIG_DIR)" --live-home "$(LIVE_HOME)"
	@"$(PYTHON)" "$(DEST_TREE)/serena_zsh_shim.py" --install-zshrc --rc-path "$(ZSHRC)" \
	    $(if $(PYTHON_EXECUTABLE),--python-executable "$(PYTHON_EXECUTABLE)",)
