# dotsync — Homebrew 배포 가능한 dotfiles 싱크 CLI 설계

- 작성일: 2026-04-25
- 신규 저장소: `changja88/homebrew-dotsync` (public, 미생성)
- 관련 저장소: `changja88/dotfiles` (현재, private — 사용자 데이터 저장소로 역할 재정의)

## 배경

현재 `dotfiles` 저장소는 Makefile 기반 sync 로직(`make/claude.mk`, `ghostty.mk`, `bettertouchtool.mk`, `zsh.mk`)과 사용자 개인 설정 데이터(`settings/`)가 한 repo에 섞여 있다. 사용하려면 repo를 로컬에 clone하고 해당 디렉토리에서 `make` 명령어를 실행해야 한다.

이 sync 로직을 일반 도구화하여 Homebrew로 배포하면, 다음이 가능해진다.

- 누구나 `brew install`로 도구 설치
- 자신의 로컬 폴더 어디든 sync 대상으로 지정
- 도구 코드와 개인 데이터 분리

## 목적

`dotsync`라는 CLI 도구를 만들어 Homebrew tap으로 배포한다. 도구는 사용자가 지정한 폴더와 로컬 앱 설정 간 양방향 sync를 수행한다.

## 범위 내 (v0.1)

1. `dotsync` Python CLI 패키지 구현
2. 4개 앱 sync 모듈 이식: claude, ghostty, bettertouchtool, zsh
3. 설정 파일 관리 (`~/.config/dotsync/config.toml`)
4. 백업 시스템 (`~/.local/share/dotsync/backups/<timestamp>/`)
5. Homebrew Formula 작성
6. `changja88/homebrew-dotsync` 신규 public repo 생성 + 초기 release (v0.1.0)
7. README + 사용법 문서

## 범위 밖

- 현재 `dotfiles` repo의 Makefile 제거 (별도 마이그레이션 작업, 도구 안정화 후)
- git 통합 (자동 commit/push 등) — 사용자가 직접 관리
- macOS 외 플랫폼 지원 (Ghostty/BTT가 macOS 전용)
- 설정 파일 마이그레이션 도구 (필요시 v0.2+)
- homebrew-core 본진 등록

## 설계

### 도구 정체성

| 항목 | 값 |
|---|---|
| 이름 | `dotsync` |
| 언어 | Python 3.12+ (stdlib only — `tomllib`, `argparse`, `shutil`, `json`, `subprocess`) |
| 의존성 | 없음 (Python 표준 라이브러리만 사용) |
| 플랫폼 | macOS (Apple Silicon + Intel) |
| 배포 | Homebrew tap (`changja88/homebrew-dotsync`) |
| 설치 | `brew install changja88/dotsync/dotsync` |

### 저장소 구조

```
changja88/homebrew-dotsync/
├── bin/
│   └── dotsync                    # entry point (#!/usr/bin/env python3)
├── lib/
│   └── dotsync/
│       ├── __init__.py
│       ├── __main__.py            # python -m dotsync 지원
│       ├── cli.py                 # argparse + dispatch
│       ├── config.py              # ~/.config/dotsync/config.toml 읽기/쓰기
│       ├── backup.py              # ~/.local/share/dotsync/backups/<ts>/
│       ├── ui.py                  # 컬러 출력 (ANSI), 기존 Make 출력 톤 유지
│       └── apps/
│           ├── __init__.py
│           ├── base.py            # App 추상 클래스
│           ├── claude.py
│           ├── ghostty.py
│           ├── bettertouchtool.py
│           └── zsh.py
├── tests/
│   ├── test_config.py
│   ├── test_backup.py
│   └── apps/
│       ├── test_claude.py
│       ├── test_ghostty.py
│       ├── test_bettertouchtool.py
│       └── test_zsh.py
├── Formula/
│   └── dotsync.rb                 # Homebrew formula
├── pyproject.toml
├── README.md
├── LICENSE                         # MIT
└── .gitignore
```

### CLI 인터페이스

```bash
# 초기화
dotsync init                       # 대화형: dir 경로 + 추적할 앱 선택

# 설정 조작
dotsync config dir <path>          # sync 대상 폴더 지정 (없으면 생성)
dotsync config apps <a,b,c>        # 추적할 앱 목록 (e.g. claude,ghostty,zsh)
dotsync config show                # 현재 설정 출력

# Sync 실행
dotsync from <app>                 # 로컬 → 폴더 (한 앱)
dotsync to <app>                   # 폴더 → 로컬 (한 앱)
dotsync from --all                 # 등록된 모든 앱
dotsync to --all                   # 등록된 모든 앱

# 정보
dotsync status                     # 앱별 sync 상태 (sha256 비교: clean/dirty/missing/unknown)
dotsync apps                       # 지원 앱 목록 + 설명
dotsync --version
dotsync --help
```

### 설정 파일 형식

`~/.config/dotsync/config.toml`:

```toml
# dotsync가 sync할 대상 폴더 (절대 경로)
dir = "/Users/foo/my-configs"

# 추적할 앱 (지원 앱 중 사용자가 선택한 부분집합)
apps = ["claude", "ghostty", "zsh"]

[options]
# 백업 저장 위치 (기본값)
backup_dir = "~/.local/share/dotsync/backups"

# 백업 보관 개수 (오래된 백업 자동 삭제, 0이면 무제한)
backup_keep = 10

# BetterTouchTool preset 이름 (사용자별로 다름; 기본값 Master_bt)
bettertouchtool_preset = "Master_bt"
```

설정 파일이 없으면 `dotsync init`로 안내한다. 모든 sync 명령어는 설정 파일 존재를 전제로 동작한다.

### 폴더 구조 (사용자 sync 대상)

`config.toml`의 `dir`이 가리키는 폴더의 내부 구조:

```
<user-dir>/
├── claude/
│   ├── settings.json
│   ├── mcp-servers.json
│   └── plugins/
│       ├── installed_plugins.json
│       ├── known_marketplaces.json
│       └── <plugin-name>/config.json
├── ghostty/
│   └── config.ghostty
├── bettertouchtool/
│   └── presets/<preset>.bttpreset
└── zsh/
    └── .zshrc
```

이 구조는 현재 `dotfiles/settings/` 구조와 동일. 마이그레이션 시 그대로 사용 가능.

### 백업 정책

- 모든 `to` 작업 직전에 로컬 현재 상태를 백업
- 위치: `~/.local/share/dotsync/backups/<YYYYMMDD_HHMMSS>/<app>/`
- `from` 작업 직전에는 `<user-dir>/<app>/` 자체는 git이든 사용자 책임이라 백업 안 함 (사용자가 직접 commit/push)
- `backup_keep` 설정으로 오래된 백업 자동 삭제

### App 추상화

```python
# lib/dotsync/apps/base.py
class App(ABC):
    name: str                       # "claude"
    description: str                # "Claude Code (settings + plugins + MCP)"

    @abstractmethod
    def sync_from(self, target_dir: Path) -> None:
        """로컬 → target_dir/<self.name>/"""

    @abstractmethod
    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        """target_dir/<self.name>/ → 로컬, 사전 백업"""

    def status(self, target_dir: Path) -> AppStatus:
        """변경 유무 검사 (sha256 기반 diff_files helper, 기본 'unknown')"""
```

각 앱 모듈은 `App`을 상속하여 자신의 sync 로직을 캡슐화. 기존 `make/*.mk`의 cp/json 조작/osascript 호출 로직을 그대로 Python으로 이식. 구체 앱은 base의 `diff_files((local, stored), ...)` 헬퍼로 status를 구현(BTT는 export 비용 때문에 unknown 유지).

App 인스턴스 생성은 `dotsync.apps.build_app(name, cfg)` factory를 통한다 — BTT처럼 config 의존(`cfg.bettertouchtool_preset`)이 필요한 앱이 있기 때문이다.

#### 특이 케이스: Claude

`make/claude-plugins-restore.py`의 plugin 복원 로직을 `apps/claude.py`의 `sync_to` 후처리로 흡수한다. `sync_to` 마지막 단계에서 `installed_plugins.json` + `known_marketplaces.json`을 읽고 누락된 marketplace/plugin을 자동 복원한다.

- 모든 `claude` CLI 호출은 `--scope user` 사용 (기존 Make 동작과 일치).
- marketplace `source.source` 종류: `github`(→repo), `directory`(→path) 우선 처리. `git`/`local`은 fallback.
- plugin install은 `installed_plugins.json[plugin_id]`의 `installPath` 디렉토리가 하나도 존재하지 않을 때만 호출 (cache hit 시 skip, idempotent).
- 후처리 마지막에 `settings.json["enabledPlugins"]`에서 false인 항목들을 `claude plugin disable --scope user` 호출 (`claude plugin install`이 enabled를 true로 덮어쓰므로 다시 disable).

데이터 형식 (실제 `~/.claude` 검증):

```jsonc
// installed_plugins.json
{ "version": 2,
  "plugins": { "<plugin>@<marketplace>": [ {"installPath": "...", ...} ] } }

// known_marketplaces.json — top-level이 marketplace 이름 dict (no wrapping)
{ "<marketplace_name>": { "source": {"source": "github", "repo": "owner/repo"}, ... } }
```

#### 특이 케이스: BetterTouchTool

osascript을 호출하여 `.bttpreset` export/import. preset 이름은 사용자별로 다르므로 config의 `bettertouchtool_preset` 값을 사용한다 (기본값 `Master_bt`). `dotsync init` 시 BTT를 선택하면 preset 이름을 입력받고, 비대화형 모드에서는 `--btt-preset` 플래그로 지정한다. status는 export 비용 때문에 항상 `unknown`을 반환한다.

### Homebrew Formula

```ruby
# Formula/dotsync.rb
class Dotsync < Formula
  desc "Sync app configs with a local folder"
  homepage "https://github.com/changja88/homebrew-dotsync"
  url "https://github.com/changja88/homebrew-dotsync/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "<v0.1.0 release tarball sha256>"
  license "MIT"
  depends_on "python@3.12"

  def install
    libexec.install "lib/dotsync"
    bin.install "bin/dotsync"
    bin.env_script_all_files(libexec/"bin", PYTHONPATH: libexec)
  end

  test do
    system "#{bin}/dotsync", "--version"
  end
end
```

### 출력 톤

기존 Makefile의 ANSI 컬러 출력 스타일 유지:

```
▶ 동기화: 로컬 → 폴더 [Claude]
  ↳ 소스: /Users/foo/.claude
  ✓ settings.json
  ✓ plugins/installed_plugins.json
  ...
✔ 완료. Claude Code를 재시작하세요.
```

### 에러 처리

- 설정 파일 없음 → "dotsync init를 먼저 실행하세요" 메시지
- `dir` 경로 없음 → 자동 생성 (사용자 첫 실행 친화적)
- 로컬 앱 설정 파일 없음 → 해당 앱만 skip + 경고
- 백업 디스크 공간 부족 → 명확한 에러
- BTT 미실행 시 osascript 실패 → 사용자에게 BTT 실행 안내

## 단계별 작업

1. `changja88/homebrew-dotsync` GitHub repo 신규 생성 (public)
2. Python 패키지 스캐폴드 (`pyproject.toml`, `bin/dotsync`, `lib/dotsync/`)
3. `config.py` + `backup.py` + `ui.py` 구현 + 테스트
4. `apps/base.py` + 4개 앱 모듈 구현 + 테스트 (기존 Make 로직 이식)
5. `cli.py` argparse 및 명령어 dispatch
6. README 작성 (설치, 사용법, 마이그레이션 가이드)
7. v0.1.0 git tag + GitHub release
8. `Formula/dotsync.rb` 작성 (release tarball sha256 채움)
9. main 브랜치 push, 사용자 설치 검증
10. 기존 `dotfiles` repo의 Makefile 정리는 별도 작업 (v0.1.0 안정화 후)

## 검증

- 새 머신에서 `brew install changja88/dotsync/dotsync` → `dotsync init` → `dotsync to --all`로 모든 앱 복원되는지 end-to-end 테스트
- 기존 `dotfiles/settings/` 폴더를 그대로 가리켜도 동작하는지 호환성 확인
- 단위 테스트: 각 앱 모듈의 sync_from/sync_to 로직, config 파싱, 백업 회전

## 보안 / 프라이버시

- 도구는 어떤 데이터도 외부로 전송하지 않음 (네트워크 호출 없음, 단 Claude plugin 복원 시 `claude plugin install` 명령어가 내부적으로 marketplace fetch)
- 사용자 sync 폴더 내용은 사용자 책임 (개인 정보 포함 가능 — README에 git push 시 주의 안내)
- Formula는 GitHub release tarball만 fetch, sha256 검증

## 향후 (v0.2+)

- macOS 외 플랫폼 (Linux용 zsh/ghostty)
- diff 모드 (`dotsync diff <app>` — sync 전 변경 미리보기)
- 추가 앱 (iTerm2, Karabiner, VSCode 등)
- 설치 후 첫 실행 시 기존 dotfiles repo 자동 마이그레이션 도우미
