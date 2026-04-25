# dotsync

macOS 앱 설정을 **사용자가 지정한 한 폴더**에서 관리하는 양방향 sync CLI · A CLI that consolidates your macOS app configs into **one folder of your choice**, with two-way sync.

---

## 한국어

### 목적

dotsync는 macOS의 앱 설정(Claude Code, Ghostty, BetterTouchTool, zsh)을 **사용자가 지정한 단일 폴더**에 모아서 양방향으로 동기화한다. 그 폴더는 어디든 OK — 새 폴더(`~/my-configs`)일 수도 있고, 이미 git이나 iCloud Drive로 관리 중인 폴더일 수도 있다. 도구(dotsync)와 데이터(폴더)를 분리해서, 새 Mac 셋업도 폴더만 옮겨오면 끝난다.

### 설치

```bash
brew install changja88/dotsync/dotsync
dotsync welcome   # ASCII 환영 배너와 첫 시작 안내를 출력
```

Python 3.12+ 가 이미 시스템에 있으면 (Homebrew 의 `python@3.12` 또는 python.org
공식 설치판) 그것을 그대로 재사용한다 — 중복 설치하지 않는다. 없을 때만 brew 가
`python@3.12` 를 함께 설치한다.

> 참고: pyenv / uv 처럼 비-canonical 경로에 깔린 Python 은 자동 감지되지 않아
> brew 가 자기 `python@3.12` 를 설치한다. 동작은 정상이지만 중복 설치는 발생.

> 첫 단계는 항상 **`dotsync init`** — sync 폴더를 정한다. 그 다음 `from` / `to`가 동작한다.

### 사용법

#### 1. 처음 한 번 — sync 대상 폴더 정하기 (앱은 자동 감지됨)

대화형으로 폴더 경로만 입력하면, 이 머신에 설치된 앱들이 자동 감지돼 default로 제시된다. 폴더 경로 prompt에서 그냥 Enter만 치면 default `~/Desktop/dotsync_config`를 사용한다.

```bash
dotsync init
# sync folder (absolute path) [/Users/you/Desktop/dotsync_config]: ⏎
#
# Detected on this machine:
#   ✓ claude
#   ✓ ghostty
#   ✓ bettertouchtool
#   ✓ zsh
# Track all of these? [Y/n/edit]: ⏎
# BetterTouchTool preset name [Master_bt]: ⏎
```

`Y`(또는 Enter)면 감지된 전부 추적, `n`이면 아무것도 안 추적, `edit`이면 화살표 키 picker 로 직접 고른다 (키 안내는 아래 `apps edit` 설명 참고).

`dotsync init` 은 BTT 의 내부 SQLite store 를 읽어 현재 등록된 preset 이름을
자동 감지한다.

- preset 1개만 있으면 자동으로 채택 (질문 없음)
- 여러 개면 목록을 보여주고 첫 번째를 default 로 1개를 고르게 한다
- 감지 실패 시 (BTT 미설치, 스키마 변경 등) 기존처럼 `Master_bt` 를 default 로 묻는다

`--btt-preset <name>` 또는 `--yes` 가 주어지면 자동 감지를 건너뛴다.

비대화형(스크립트/새 머신 셋업용):

```bash
# --dir 생략 시 default ~/Desktop/dotsync_config 사용
# --apps 생략 시 자동 감지된 전체를 추적
dotsync init --yes

# 명시적으로 지정도 가능
dotsync init --dir ~/my-configs --apps claude,zsh --btt-preset Master_bt --yes
```

**dotsync는 사용자가 지정한 sync 폴더 외에는 컴퓨터 어디에도 파일/디렉토리를 만들지 않는다.** 모든 설정은 `<sync 폴더>/dotsync.toml`에만 저장되고, 백업도 `<sync 폴더>/.backups/`에 쌓인다.

#### dotsync는 sync 폴더를 어떻게 찾나?

두 가지 중 하나면 된다.

1. 환경변수 `DOTSYNC_DIR`이 절대경로로 설정돼 있으면 그것을 사용한다. `init` 종료 시 안내 메시지가 셸 rc에 추가할 한 줄을 알려준다:
   ```bash
   export DOTSYNC_DIR="/Users/you/my-configs"
   ```
2. 또는 sync 폴더 안(또는 그 하위 어디)에서 dotsync 명령을 실행하면 자동으로 `dotsync.toml`을 위로 거슬러 올라가며 찾는다 (git 방식).

#### 새 머신에서 복원할 때

폴더에 이미 `dotsync.toml`이 있으면 dotsync는 그걸 그대로 채택한다.

```bash
git clone git@github.com:you/my-configs.git ~/my-configs
export DOTSYNC_DIR="$HOME/my-configs"   # 한 번만 (.zshrc 등에 추가)
dotsync init --dir ~/my-configs --yes   # 폴더 안 dotsync.toml 그대로 사용
dotsync to --all
```

#### 2. 로컬 앱 설정 → 폴더 (스냅샷 뜨기)

```bash
dotsync from --all          # 추적 중인 모든 앱
dotsync from claude         # 한 앱만
```

이후 그 폴더를 git에 커밋하거나 iCloud로 동기화해두면 백업이 된다.

#### 3. 폴더 → 로컬 앱 (다른 머신에서 복원하기)

`dotsync to`는 로컬을 덮어쓰기 전에 **확인 프롬프트**를 띄웁니다. 변경사항을 미리 확인만 하고 싶다면 `--dry-run`, 자동화에서 프롬프트를 건너뛰려면 `--yes`를 사용하세요. 백업 세션 경로는 실행 중에 출력됩니다.

```bash
dotsync to --all --dry-run     # preview only
dotsync to --all                # interactive (asks Y/n)
dotsync to --all --yes          # automation (no prompt)
```

`to` 직전 로컬 파일은 `<sync 폴더>/.backups/<YYYYMMDD_HHMMSS>/<app>/`에 자동 백업된다 (사용자 폴더 안에만 쌓이므로 git에 올리고 싶지 않으면 `.gitignore`에 `.backups/` 추가).

#### 4. 동기화 상태 확인 (파일별 sha256 비교)

상태 라인은 색·심볼로 한눈에 구분되며, 'dirty'면 어느 쪽이 더 최신인지(direction)도 함께 표시됩니다.

```bash
$ dotsync status
▸ status                              ~/dotsync_config

  ✓ zsh              clean
  ⚠ ghostty          dirty   local-newer  — config
  ✗ claude           missing
  · bettertouchtool  unknown — BTT not running
```

#### 폴더/앱 목록을 나중에 바꾸고 싶으면

추적 앱은 두 가지 방법으로 바꿀 수 있습니다 — 인터랙티브 메뉴 또는 일괄 교체.

```bash
dotsync apps                              # 현재 추적/설치 상태 한눈에 보기
dotsync apps edit                         # 인터랙티브로 추적 앱 변경 (Enter = 그대로)
dotsync config show                       # 현재 설정 보기
dotsync config dir ~/another-folder       # sync 폴더 변경
dotsync config apps claude,zsh            # 추적 앱 일괄 교체 (자동화용)
dotsync config btt-preset MyPreset        # BTT preset 이름 변경
```

`dotsync apps edit` 또는 `dotsync init` 의 edit 분기를 고르면, 화살표 키로
체크박스를 토글하는 picker 가 뜬다.

- `↑` / `↓` — 항목 이동
- `space` — 현재 항목 체크 토글
- `enter` — 확정
- `q` / `esc` / `ctrl+c` — 취소 (설정 변경 없음)

CI나 파이프 같은 비-TTY 환경에서는 자동으로 앱별 y/n 프롬프트로 fallback 한다.

지원 앱: `claude`, `ghostty`, `bettertouchtool`, `zsh`

도움말: `dotsync --help`, `dotsync <command> --help`

---

## English

### Purpose

dotsync consolidates your macOS app configs (Claude Code, Ghostty, BetterTouchTool, zsh) into **one folder of your choice** and keeps it in two-way sync with the apps. That folder can be anywhere — a fresh directory like `~/my-configs`, or a folder you already track in git or sync via iCloud Drive. Tool (dotsync) and data (the folder) are separated, so setting up a new Mac is just a matter of bringing the folder along.

### Install

```bash
brew install changja88/dotsync/dotsync
dotsync welcome   # prints the ASCII welcome banner with quickstart hints
```

If Python 3.12+ is already installed at a canonical path (Homebrew's
`python@3.12` or a python.org official installer), dotsync reuses it —
no duplicate install. Otherwise Homebrew pulls in `python@3.12` as a
dependency.

> Note: Pythons installed via pyenv / uv are at non-canonical paths and
> won't be auto-detected — Homebrew will still install its own
> `python@3.12`. Functionality is unaffected, but the duplicate install
> is not avoided.

> Always start with **`dotsync init`** — it picks the sync folder. After that, `from` / `to` work from anywhere.

### Usage

#### 1. One-time setup — pick your sync folder (apps auto-detected)

Interactive. You provide the folder path (or just hit Enter for the default `~/Desktop/dotsync_config`); dotsync detects which supported apps are installed on this machine and offers them as the default.

```bash
dotsync init
# sync folder (absolute path) [/Users/you/Desktop/dotsync_config]: ⏎
#
# Detected on this machine:
#   ✓ claude
#   ✓ ghostty
#   ✓ bettertouchtool
#   ✓ zsh
# Track all of these? [Y/n/edit]: ⏎
# BetterTouchTool preset name [Master_bt]: ⏎
```

`Y` (or Enter) tracks all detected apps, `n` tracks none, `edit` opens an arrow-key picker so you can pick a custom set (key bindings are described under `apps edit` below).

`dotsync init` reads BetterTouchTool's internal SQLite store to auto-detect
the registered preset name(s).

- Exactly one preset → adopted automatically (no prompt)
- Multiple → shown as a list; pick one (first is the default)
- Discovery fails (BTT not installed, schema drift, …) → legacy prompt with
  `Master_bt` as the default

`--btt-preset <name>` or `--yes` skips discovery.

Non-interactive (scripts / new-machine bootstrap):

```bash
# Both --dir and --apps can be omitted:
#   --dir defaults to ~/Desktop/dotsync_config
#   --apps defaults to all auto-detected apps
dotsync init --yes

# Or specify explicitly
dotsync init --dir ~/my-configs --apps claude,zsh --btt-preset Master_bt --yes
```

**dotsync creates no files or directories anywhere on your machine outside the sync folder you chose.** All settings live in `<sync folder>/dotsync.toml`, and backups accumulate in `<sync folder>/.backups/`.

#### How does dotsync find the sync folder?

Either of these works:

1. The `DOTSYNC_DIR` environment variable holds an absolute path. `init` prints the line to add to your shell rc:
   ```bash
   export DOTSYNC_DIR="/Users/you/my-configs"
   ```
2. Or, run `dotsync` from inside the sync folder (or any subdirectory) — it walks upward looking for `dotsync.toml` (git-style).

#### Restoring on a new machine

If the folder already contains a `dotsync.toml`, `init` adopts it as-is.

```bash
git clone git@github.com:you/my-configs.git ~/my-configs
export DOTSYNC_DIR="$HOME/my-configs"   # once (add to your shell rc)
dotsync init --dir ~/my-configs --yes   # reuses existing dotsync.toml as-is
dotsync to --all
```

#### 2. Local app configs → folder (take a snapshot)

```bash
dotsync from --all          # all tracked apps
dotsync from claude         # one app
```

Then commit the folder to git or let iCloud sync it — that's your backup.

#### 3. Folder → local apps (restore on another machine)

`dotsync to` prompts for confirmation before overwriting your local configs. Use `--dry-run` to preview, `--yes` to skip the prompt in automation. The backup session path is printed at run time.

```bash
dotsync to --all --dry-run     # preview only
dotsync to --all                # interactive (asks Y/n)
dotsync to --all --yes          # automation (no prompt)
```

Each `to` snapshots the about-to-be-overwritten local files into `<sync folder>/.backups/<YYYYMMDD_HHMMSS>/<app>/` (lives inside your sync folder; add `.backups/` to `.gitignore` if you don't want it tracked).

#### 4. Check sync state (per-file sha256 diff)

Status lines use color + glyph; for 'dirty' rows, the direction hint shows which side is newer.

```bash
$ dotsync status
▸ status                              ~/dotsync_config

  ✓ zsh              clean
  ⚠ ghostty          dirty   local-newer  — config
  ✗ claude           missing
  · bettertouchtool  unknown — BTT not running
```

#### Change the folder or app list later

Two ways to change the tracked-apps list — an interactive menu or a one-shot replacement.

```bash
dotsync apps                              # see current tracked/installed state
dotsync apps edit                         # interactively edit tracked apps (Enter = keep current)
dotsync config show                       # print current config
dotsync config dir ~/another-folder       # change sync folder
dotsync config apps claude,zsh            # replace the tracked-apps list (for automation)
dotsync config btt-preset MyPreset        # change BTT preset name
```

`dotsync apps edit` (or the `edit` choice in `dotsync init`) opens an
interactive checkbox picker.

- `↑` / `↓` — move
- `space` — toggle the current row
- `enter` — confirm
- `q` / `esc` / `ctrl+c` — cancel (no config change)

In non-TTY environments (CI, piped stdin) it automatically falls back to
sequential per-app y/n prompts.

Supported apps: `claude`, `ghostty`, `bettertouchtool`, `zsh`

Help: `dotsync --help`, `dotsync <command> --help`

---

## License

MIT
