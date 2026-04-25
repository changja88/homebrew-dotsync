# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 저장소의 정체

이곳은 **`changja88/homebrew-dotsync` Homebrew tap** 저장소이며, 다음 두 가지를 함께 담는다.

1. **`dotsync` Python CLI** (`lib/dotsync/`, 진입점 `bin/dotsync`) — stdlib만 사용해 macOS 앱 설정(Claude Code, Ghostty, BetterTouchTool, zsh)을 사용자가 지정한 폴더와 양방향으로 sync하는 도구.
2. **Homebrew formula** (`Formula/dotsync.rb`) — `brew install changja88/dotsync/dotsync`로 위 CLI를 설치하기 위한 정의 파일.

저장소는 현재 **초기 스캐폴드 직전 상태**다. `pyproject.toml`이 placeholder로만 존재하고, `lib/`, `bin/`, `tests/`, `Formula/`는 아직 만들어지지 않았다. 전체 설계와 구현 단계는 다음 두 문서에 들어있다.

- **Spec**: `docs/superpowers/specs/2026-04-25-dotsync-design.md` — 설계 결정, CLI 표면, 설정 파일 형식, App 추상화.
- **Plan**: `docs/superpowers/plans/2026-04-25-dotsync-implementation.md` — TDD(Red → Green → Refactor) 단위로 task별 구현 절차. 사소하지 않은 변경 전에 반드시 먼저 읽을 것. 이 두 문서가 진실의 원천이다.

## 아키텍처 (목표 상태)

CLI는 **단일 패키지 + plugin 스타일 앱 레지스트리** 구조를 따른다.

- `lib/dotsync/cli.py` — argparse dispatch (`init`, `config`, `from`, `to`, `status`, `apps`).
- `lib/dotsync/config.py` — `~/.config/dotsync/config.toml` 읽기/쓰기 (stdlib `tomllib`).
- `lib/dotsync/backup.py` — `to` 직전 스냅샷을 `~/.local/share/dotsync/backups/<YYYYMMDD_HHMMSS>/<app>/`에 저장, `backup_keep` 기준으로 회전.
- `lib/dotsync/ui.py` — ANSI 컬러 출력 (`NO_COLOR` 환경변수 존중). 기존 Make 출력 톤(`▶ ↳ ✓ ⚠ ✗ ✔`) 유지.
- `lib/dotsync/apps/base.py` — `App` 추상 클래스. `sync_from(target_dir)`, `sync_to(target_dir, backup_dir)`, 선택적 `status(target_dir)`.
- `lib/dotsync/apps/{claude,ghostty,bettertouchtool,zsh}.py` — 구체 앱 모듈. 각 모듈은 **별도의 `dotfiles` repo의 `make/*.mk` 파일**(claude.mk, ghostty.mk, bettertouchtool.mk, zsh.mk)에서 로직을 이식한다. Claude 모듈은 `make/claude-plugins-restore.py`의 plugin 복원 로직을 자신의 `sync_to` 후처리로 흡수한다.

설계에 새겨진 횡단 규칙:

- **런타임은 stdlib only.** 허용: `tomllib`, `argparse`, `shutil`, `pathlib`, `subprocess`, `json`, `dataclasses`, `abc`. 외부 의존성 금지 — Homebrew formula를 단순하게 유지(`depends_on "python@3.12"` 하나)하고 vendoring을 피하기 위함.
- **macOS 전용.** BTT sync는 `osascript`로 BTT 앱을 제어하고, Ghostty/zsh/Claude 경로도 macOS 관례를 가정한다. v0.1에서 Linux 분기 추가하지 말 것.
- **dotsync 자체는 네트워크 호출 없음.** Claude 앱이 `claude plugin install`을 shell-out하거나(marketplace fetch), BTT가 `osascript`을 호출하는 것이 유일한 외부 프로세스.
- **`from` = local → folder, `to` = folder → local.** `to` 전에는 항상 백업. `from` 전에는 백업하지 않음 — 사용자 sync 폴더는 사용자의 git 책임.

## 구현 규율

`docs/superpowers/plans/`의 plan은 **TDD 실행**(`superpowers:test-driven-development` / `superpowers:executing-plans`)을 전제로 작성됐다. 각 task 블록은 다음 형태를 가진다.

1. 실패하는 테스트 작성
2. 테스트 실행 → FAIL 확인
3. 구현
4. 테스트 실행 → PASS 확인
5. 커밋

task를 구현하거나 수정할 때는 **이 순서를 따를 것** — 테스트 없이 구현부터 쓰지 말 것. plan에 박혀있는 테스트 스니펫이 동작 명세이므로, 거기서 조용히 벗어나면 그건 버그다.

## 명령어

> 참고: `pyproject.toml`은 plan의 Task 1에서 `[project.scripts] dotsync = "dotsync.cli:main"`과 `[tool.pytest.ini_options] pythonpath = ["lib"]`를 포함하는 형태로 다시 쓰인다. 그 이전까지는 아래 명령들이 동작하지 않는다.

```bash
# 소스에서 CLI 실행 (설치 없이)
PYTHONPATH=lib python3 -m dotsync --help

# 전체 테스트
pytest

# 단일 테스트 파일
pytest tests/test_config.py -v

# 단일 테스트
pytest tests/apps/test_claude.py::test_sync_from_copies_settings -v

# end-to-end 수동 테스트용 editable install
pip install -e .

# tag 전에 로컬에서 Homebrew formula 검증
brew install --build-from-source ./Formula/dotsync.rb
brew test dotsync
```

## 릴리스 절차 (Homebrew tap)

formula의 `url`은 GitHub release tarball을 가리키고, `sha256`로 핀된다. 릴리스 절차는 다음과 같다 (자세한 내용은 plan Task 15 참조).

1. `main` push.
2. `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`.
3. `gh release create vX.Y.Z`.
4. `curl -sL <tarball-url> | shasum -a 256`로 해시 계산.
5. `Formula/dotsync.rb`의 `sha256` 갱신, commit, push.
6. `brew install changja88/dotsync/dotsync && dotsync --version`로 검증.

formula의 `sha256`을 **절대 추측해서 채우지 말 것** — 항상 GitHub release를 먼저 cut한 뒤 실제 tarball에서 계산할 것.

## Python 버전 주의

spec/plan은 **Python 3.12+**를 타겟으로 한다 (formula의 `python@3.12`와 일치). 현재 placeholder `pyproject.toml`에는 `requires-python = ">=3.14"`로 되어있는데, 이는 빈 스캐폴드 상태이며 plan Task 1 Step 2에서 교체된다. 다시 쓸 때는 formula 런타임과 맞추기 위해 3.14가 아닌 `>=3.12`를 쓸 것.
