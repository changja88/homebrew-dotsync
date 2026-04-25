# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 저장소의 정체

이곳은 **`changja88/homebrew-dotsync` Homebrew tap** 저장소이며, 다음 두 가지를 함께 담는다.

1. **`dotsync` Python CLI** (`lib/dotsync/`, 진입점 `bin/dotsync`) — stdlib만 사용해 macOS 앱 설정(Claude Code, Ghostty, BetterTouchTool, zsh)을 사용자가 지정한 폴더와 양방향으로 sync하는 도구.
2. **Homebrew formula** (`Formula/dotsync.rb`) — `brew install changja88/dotsync/dotsync`로 위 CLI를 설치하기 위한 정의 파일.

## 아키텍처

CLI는 **단일 패키지 + plugin 스타일 앱 레지스트리** 구조를 따른다.

- `lib/dotsync/cli.py` — argparse dispatch (`init`, `config`, `from`, `to`, `status`, `apps`). `init`은 설치된 앱을 `detect_present()`로 자동 감지해 default로 제시하고, 사용자가 지정한 폴더에 `dotsync.toml`이 이미 있으면 그것을 채택한다.
- `lib/dotsync/config.py` — config는 두 곳에 분산: `~/.dotsync`는 sync 폴더 절대경로를 담은 한 줄짜리 pointer, `<sync_folder>/dotsync.toml`이 실제 config(`apps`, `backup_dir`, `backup_keep`, `bettertouchtool_preset`). 폴더 자기 위치는 폴더 안에 적지 않음(`dir` 필드 없음 — pointer가 갖는다). stdlib `tomllib` 사용.
- `lib/dotsync/backup.py` — `to` 직전 스냅샷을 `~/.local/share/dotsync/backups/<YYYYMMDD_HHMMSS>/<app>/`에 저장, `backup_keep` 기준으로 회전.
- `lib/dotsync/ui.py` — ANSI 컬러 출력 (`NO_COLOR` 환경변수 존중). 출력 톤(`▶ ↳ ✓ ⚠ ✗ ✔`)은 기존 Make 스타일과 동일.
- `lib/dotsync/apps/base.py` — `App` 추상 클래스 + `AppStatus` + `diff_files(pairs)` 헬퍼 (sha256 기반 비교).
- `lib/dotsync/apps/{claude,ghostty,bettertouchtool,zsh}.py` — 구체 앱 모듈. 각 앱은 `is_present_locally()` classmethod를 제공해 init 자동 감지에 참여한다.
- `lib/dotsync/apps/__init__.py` — `APP_NAMES` + `build_app(name, cfg)` factory + `detect_present()` 헬퍼. config 의존(BTT preset)을 위해 정적 REGISTRY 대신 factory 사용.

설계에 새겨진 횡단 규칙:

- **런타임은 stdlib only.** 허용: `tomllib`, `argparse`, `shutil`, `pathlib`, `subprocess`, `json`, `dataclasses`, `abc`, `hashlib`. 외부 의존성 금지 — Homebrew formula를 단순하게 유지(`depends_on "python@3.12"` 하나)하고 vendoring을 피하기 위함.
- **macOS 전용.** BTT sync는 `osascript`로 BTT 앱을 제어하고, Ghostty/zsh/Claude 경로도 macOS 관례를 가정한다. v0.1에서 Linux 분기 추가하지 말 것.
- **dotsync 자체는 네트워크 호출 없음.** Claude 앱 모듈이 `claude plugin install --scope user`을 shell-out하거나(marketplace fetch), BTT가 `osascript`을 호출하는 것이 유일한 외부 프로세스.
- **`from` = local → folder, `to` = folder → local.** `to` 전에는 항상 백업. `from` 전에는 백업하지 않음 — 사용자 sync 폴더는 사용자의 git 책임.
- **Claude 앱 모듈의 데이터 구조.** `installed_plugins.json`은 `{"version": 2, "plugins": {"<id>@<mp>": [{"installPath": ...}]}}` (값이 list), `known_marketplaces.json`은 top-level이 marketplace 이름 dict (no wrapping), `source.source`는 `github`(→repo)/`directory`(→path)가 주. `claude` CLI 호출은 모두 `--scope user`. `settings.json`의 `enabledPlugins`에서 false인 항목은 install 후 다시 `claude plugin disable --scope user`로 비활성화한다.

## 구현 규율

이 저장소는 **TDD**로 만들어졌다. 각 모듈은 `tests/`의 짝 테스트와 1:1로 묶여 있다. 새 기능을 추가하거나 기존 동작을 바꿀 때는 다음 순서를 따를 것.

1. 실패하는 테스트를 먼저 작성한다.
2. 테스트를 실행해 RED 확인.
3. 최소 구현으로 GREEN.
4. 필요하면 리팩터.
5. 커밋.

테스트 없이 구현부터 쓰지 말 것. 기존 테스트가 동작 명세이므로, 거기서 조용히 벗어나면 그건 버그다.

**문서 동기화.** 새 기능을 추가하거나 사용자에게 보이는 동작(CLI 명령어/옵션, 출력, 지원 앱 목록, 설정 항목 등)이 바뀌면 같은 commit에서 `README.md`의 사용법 섹션을 함께 갱신한다. README는 한국어와 English 두 섹션이 동등하게 유지돼야 한다 — 한쪽만 고치고 다른 쪽을 두지 말 것.

## 명령어

```bash
# 소스에서 CLI 실행 (설치 없이)
PYTHONPATH=lib python3 -m dotsync --help
PYTHONPATH=lib python3 bin/dotsync --help

# 전체 테스트
make test
# 또는
.venv/bin/python3 -m pytest

# 단일 테스트 파일
.venv/bin/python3 -m pytest tests/test_config.py -v

# 단일 테스트
.venv/bin/python3 -m pytest tests/apps/test_claude.py::test_status_clean -v

# end-to-end 수동 테스트용 editable install
pip install -e .

# 태그 전에 로컬에서 Homebrew formula 검증
brew install --build-from-source ./Formula/dotsync.rb
brew test dotsync
```

## 릴리스 절차 (Homebrew tap)

릴리스는 `make release`로 자동화돼 있다. 인터랙티브로 major/minor/patch를 묻고, 버전 갱신 → 테스트 → commit/push → 태그 → GitHub release → tarball sha256 계산 → Formula 패치 → push까지 처리한다.

```bash
make release
```

수동으로 진행해야 할 경우:

1. `git push -u origin main`
2. `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`
3. `gh release create vX.Y.Z`
4. `curl -sL <tarball-url> | shasum -a 256`로 해시 계산
5. `Formula/dotsync.rb`의 `sha256` 갱신, commit, push
6. `brew install changja88/dotsync/dotsync && dotsync --version`로 검증

formula의 `sha256`을 **절대 추측해서 채우지 말 것** — 항상 GitHub release를 먼저 cut한 뒤 실제 tarball에서 계산할 것.

## Python 버전

런타임 타겟은 **Python 3.12+** (`pyproject.toml`의 `requires-python` 및 formula의 `python@3.12`와 일치). `Formula/dotsync.rb`의 install 블록은 `bin/dotsync`의 shebang을 `python@3.12`의 `opt_bin/python3.12`로 핀하므로, 사용자 시스템의 `python3`이 어떤 버전이든 dotsync는 항상 3.12로 실행된다.
