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
# Override:
#   make install-shim PYTHON=/usr/bin/python3 ZSHRC=/path/to/.zshrc STABLE_DIR=/some/where

.PHONY: help install-shim

PYTHON ?= python3
ZSHRC ?= $(HOME)/.zshrc
STABLE_DIR ?= $(HOME)/Desktop/dotsync_config/agent_launcher

SRC_TREE  := serena_mcp_management
DEST_TREE := $(STABLE_DIR)/local_dev/$(SRC_TREE)

help:
	@echo "local_dev — internal-only development tooling (not part of dotsync)"
	@echo ""
	@echo "Targets:"
	@echo "  install-shim     Mirror $(SRC_TREE)/ to $(STABLE_DIR)/ and write managed block into ZSHRC=$(ZSHRC)"

install-shim:
	@mkdir -p "$(DEST_TREE)"
	@rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' \
	    "$(SRC_TREE)/" "$(DEST_TREE)/"
	@$(PYTHON) "$(DEST_TREE)/serena_zsh_shim.py" --install-zshrc --rc-path "$(ZSHRC)"
