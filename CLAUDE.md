# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 저장소의 정체

이곳은 **`changja88/homebrew-dotsync` Homebrew tap** 저장소이며, 다음 두 가지를 함께 담는다.

1. **`dotsync` Python CLI** (`lib/dotsync/`, 진입점 `bin/dotsync`) — stdlib만 사용해 macOS 앱 설정(Claude Code, Ghostty, BetterTouchTool, zsh)을 사용자가 지정한 폴더와 양방향으로 sync하는 도구.
2. **Homebrew formula** (`Formula/dotsync.rb`) — `brew install changja88/dotsync/dotsync`로 위 CLI를 설치하기 위한 정의 파일.

## 아키텍처

CLI는 **단일 패키지 + plugin 스타일 앱 레지스트리** 구조를 따른다.

- `lib/dotsync/cli.py` — argparse dispatch (`init`, `config`, `from`, `to`, `status`, `apps`). `init`은 설치된 앱을 `detect_present()`로 자동 감지해 default로 제시하고, 사용자가 지정한 폴더에 `dotsync.toml`이 이미 있으면 그것을 채택한다. 종료 직전에 사용자 동의(`--yes` 또는 interactive 프롬프트)를 받아 `~/.zshrc`(또는 `~/.bash_profile`)에 `export DOTSYNC_DIR=...` 한 줄을 자동 추가한다 — 동의 없이는 절대 안 건드리고, `--no-shell-init`으로 명시적으로 끌 수 있다.
- `lib/dotsync/shellrc.py` — 셸 rc 파일 감지(`detect_rc_path` via `$SHELL`)와 멱등 insert/update/skip 로직(`update_shell_rc`). 순수 함수만 두고, 동의 처리는 cli.py가 담당. 다른 `export DOTSYNC_DIR=` 라인이 이미 있으면 그 자리에서 갱신, 동일하면 무변경, 없으면 marker 주석과 함께 EOF에 append. rc 파일이 없으면 만들지 않는다(`rc_missing` 반환).
- `lib/dotsync/config.py` — **dotsync는 사용자가 지정한 sync 폴더 외에는 어디에도 파일/디렉토리를 만들지 않는다.** 실제 config는 `<sync_folder>/dotsync.toml`에만 존재한다. sync 폴더 위치는 (1) `$DOTSYNC_DIR` 환경변수 (절대경로), (2) cwd에서 위로 거슬러 올라가며 `dotsync.toml` 검색(git 방식) 중 하나로 발견한다. 폴더가 자기 위치를 자기에게 적을 필요 없으므로 dotsync.toml에 `dir` 필드 없음. stdlib `tomllib` 사용.
- `lib/dotsync/backup.py` — `to` 직전 스냅샷을 `<sync_folder>/.backups/<YYYYMMDD_HHMMSS>/<app>/`(default)에 저장, `backup_keep` 기준으로 회전. 사용자 폴더 외부에는 절대 쓰지 않는다.
- `lib/dotsync/ui.py` — ANSI 컬러 출력 (`NO_COLOR` 환경변수 존중). 출력 톤(`▶ ↳ ✓ ⚠ ✗ ✔`)은 기존 Make 스타일과 동일.
- `lib/dotsync/apps/base.py` — `App` ABC + `AppStatus` + `FilePair` + `diff_files(pairs)` 헬퍼. App에는 (a) 선언적 `tracked_files(target_dir)`을 walk하는 default `sync_from`/`sync_to`/`status` 구현, (b) `_run_external(cmd, *, desc, fail_mode)` 외부 프로세스 헬퍼, (c) `warnings` 누적 채널이 있다. 단순 파일 sync 앱은 `tracked_files()`만 선언하면 끝.
- `lib/dotsync/apps/{claude,ghostty,bettertouchtool,zsh}.py` — 구체 앱 모듈. 각 앱은 `is_present_locally()`로 init 자동 감지에 참여하고, 자기 옵션을 읽는 `from_config(cls, cfg)`을 가질 수 있다. CLI 통합은 `extra_init_args`/`picker_annotation`/`resolve_options`/`extra_config_subcommands`/`handle_config_subcommand` 5개 hook으로 자기 모듈 안에서 선언한다 (cli.py가 BTT 같은 특정 앱을 모름).
- `lib/dotsync/apps/__init__.py` — 단일 `APP_CLASSES` 튜플이 모든 등록의 source of truth. `APP_NAMES`, `app_descriptions()`, `build_app()`, `detect_present()`가 전부 여기서 derive. 새 앱 추가는 `APP_CLASSES`에 한 줄. config 의존은 각 App의 `from_config(cls, cfg)` classmethod로 다형성 처리.

설계에 새겨진 횡단 규칙:

- **런타임은 stdlib only.** 허용: `tomllib`, `argparse`, `shutil`, `pathlib`, `subprocess`, `json`, `dataclasses`, `abc`, `hashlib`, `re`, `sqlite3`. 외부 의존성 금지 — Homebrew formula를 단순하게 유지(`depends_on "python@3.12"` 하나)하고 vendoring을 피하기 위함.
- **macOS 전용.** BTT sync는 `osascript`로 BTT 앱을 제어하고, Ghostty/zsh/Claude 경로도 macOS 관례를 가정한다. v0.1에서 Linux 분기 추가하지 말 것.
- **dotsync 자체는 네트워크 호출 없음.** Claude 앱 모듈이 `claude plugin install --scope user`을 shell-out하거나(marketplace fetch), BTT가 `osascript`을 호출하는 것이 유일한 외부 프로세스.
- **사용자 sync 폴더 외부에 dotsync가 쓰는 곳은 단 한 곳: 사용자 동의 받은 셸 rc 파일.** 그 외에는 `~/.dotsync` 같은 메타 디렉토리도 만들지 않는다. shellrc 모듈은 rc 파일이 없으면 새로 만들지도 않는다 — 멱등성과 안전성 우선.
- **`from` = local → folder, `to` = folder → local.** `to` 전에는 항상 백업. `from` 전에는 백업하지 않음 — 사용자 sync 폴더는 사용자의 git 책임.
- **App ABC는 `tracked_files(target_dir) -> list[FilePair]`를 선언적 모델로 제공.** 단일/다중 파일 sync는 base의 default `sync_from`/`sync_to`/`status`가 알아서 처리(fail-fast 누락 체크 + 백업 후 덮어쓰기 포함). 외부 프로세스/복합 동작이 필요한 앱(claude, BTT)만 sync 메서드를 override한다.
- **외부 프로세스 호출은 `self._run_external(cmd, desc=..., fail_mode="warn"|"raise")`로 통일.** `warn` 모드 실패는 `self.warnings`에 누적되어 cli summary가 surface한다. Claude의 `claude` CLI 부재 같은 부분 실패는 더이상 sync 전체를 죽이지 않는다.
- **앱별 옵션은 `cfg.app_options[<app_name>]` 딕셔너리.** 코어 Config dataclass는 앱별 키를 모른다. 각 앱이 자기 옵션 schema를 자기 `from_config`에서 책임진다. (`Config.bettertouchtool_presets` 필드는 legacy 호환을 위한 임시 bridge — TODO 마킹돼 있음.)
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

## 새 앱 추가

새 앱을 추가하는 4단계 절차는 `docs/adding-an-app.md`에 정리돼 있다. 단순 파일 기반 앱은 모듈 1개 + `apps/__init__.py`의 `APP_CLASSES`에 한 줄 추가로 끝난다. 복잡한 앱(외부 프로세스, 자체 CLI 옵션, 다중 파일)은 같은 자리에서 base의 hook을 override해서 확장한다.

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
