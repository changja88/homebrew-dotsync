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

Python 3.12 또는 3.13 이 canonical 경로 (`/opt/homebrew/bin/python3.{12,13}`,
`/usr/local/bin/python3.{12,13}`, `/Library/Frameworks/Python.framework/Versions/3.{12,13}/...`)
에 이미 있으면 그것을 그대로 재사용한다 — 중복 설치하지 않는다. 없을 때만 brew 가
`python@3.12` 를 함께 설치한다.

> 참고: pyenv / uv 처럼 비-canonical 경로에 깔린 Python 은 자동 감지되지 않아
> brew 가 자기 `python@3.12` 를 설치한다. 동작은 정상이지만 중복 설치는 발생.

> 첫 단계는 항상 **`dotsync init`** — sync 폴더를 정한다. 그 다음 `from` / `to`가 동작한다.

### 사용법

#### 1. 처음 한 번 — sync 폴더 정하고 추적할 앱 고르기

`dotsync init` 은 두 단계 wizard 다.

**Step 1 — Sync folder.** 폴더 경로를 입력한다. 그냥 Enter면 default `~/Desktop/dotsync_config` 를 사용한다.

**Step 2 — Pick apps to track.** 화살표 키 picker 가 곧바로 뜬다. 이 머신에 설치된 앱들은 미리 체크돼 있고(`installed` 표시), 미설치 앱은 비어 있다(`not installed`). 키로 토글한 뒤 Enter 로 확정.

```bash
dotsync init
# ▶ Step 1 — Sync folder
# ? sync folder (absolute path) [/Users/you/Desktop/dotsync_config] › ⏎
# ✔ folder ready → /Users/you/Desktop/dotsync_config
#
# ▶ Step 2 — Pick apps to track
#   Pick apps to track   ↑/↓ move · space toggle · enter submit
#
#   ▸ [x] claude              installed
#     [x] ghostty             installed
#     [x] bettertouchtool     installed · 2 presets
#     [x] zsh                 installed
#
# ✔ tracked: claude · ghostty · bettertouchtool · zsh
# ✔ BetterTouchTool presets = Master_bt, Mini_bt   (auto-detected)
# ✔ config saved → /Users/you/Desktop/dotsync_config/dotsync.toml
```

picker 의 색상은 행 상태를 한눈에 보여준다.

- `[x] + installed` → 정상 (기본 색)
- `[x] + not installed` → 빨강 dim ("정리 후보")
- `[ ] + installed` → 노랑 dim ("추가 후보")
- `[ ] + not installed` → 그냥 dim

**BetterTouchTool 의 preset 은 자동으로 모두 추적된다.** dotsync 가 BTT 내부 SQLite 를 읽어 등록된 preset 이름을 모두 가져와 `bettertouchtool_presets` 에 넣는다 (예: `["Master_bt", "Mini_bt"]`). 사용자가 따로 고를 일은 없다. `--yes` 모드는 deterministic 한 결정을 위해 자동 감지를 건너뛰고 default(`["Master_bt"]`)를 쓴다.

비대화형(스크립트/새 머신 셋업용):

```bash
# --dir 생략 시 default ~/Desktop/dotsync_config 사용
# --apps 생략 시 자동 감지된 전체를 추적
dotsync init --yes

# 명시적으로 지정도 가능 (BTT presets 는 콤마 구분)
dotsync init --dir ~/my-configs --apps claude,zsh --btt-presets Master_bt,Mini_bt --yes
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

banner 의 `→` 화살표는 sync 방향을, summary box 는 어떤 앱이 실제로 동기화됐는지를 한 줄로 보여준다.

```
╭──────────────────────────────────────────────────────────╮
│ dotsync from                                             │
│ 4 apps  →  /Users/you/Desktop/dotsync_config             │
╰──────────────────────────────────────────────────────────╯
... (per-app sections) ...
╭──────────────────────────────────────────────────────────╮
│ ✓ synced     claude · ghostty · bettertouchtool · zsh    │
│ 4 ok  ·  0 warn  ·  0 error  ·  2.3s                     │
╰──────────────────────────────────────────────────────────╯
```

이후 그 폴더를 git에 커밋하거나 iCloud로 동기화해두면 백업이 된다.

#### 3. 폴더 → 로컬 앱 (다른 머신에서 복원하기)

`dotsync to`는 로컬을 덮어쓰기 전에 **노란색(warn) 확인 프롬프트**를 띄운다 — 일반 prompt 와 색을 구분해서 destructive 액션임을 강조한다. 변경사항을 미리 확인만 하고 싶다면 `--dry-run`, 자동화에서 프롬프트를 건너뛰려면 `--yes`를 사용한다. 백업 세션 경로는 실행 중에 출력된다.

```bash
dotsync to --all --dry-run     # preview only
dotsync to --all                # interactive (asks Y/n, yellow accent)
dotsync to --all --yes          # automation (no prompt)
```

`to` 의 summary box 는 실제로 변경된 앱(`✓ applied`)과 이미 같은 상태였던 앱(`· unchanged`)을 분리해서 보여준다.

```
╭──────────────────────────────────────────────────────────╮
│ ✓ applied     ghostty · bettertouchtool                  │
│ · unchanged   claude · zsh                               │
│ 4 ok  ·  0 warn  ·  0 error  ·  3.1s                     │
╰──────────────────────────────────────────────────────────╯
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

`dotsync apps` 가 init Step 2 와 똑같은 picker 를 띄운다. BTT 를 새로 토글하면 등록된 preset 들이 자동 재검색돼 config 에 반영된다.

```bash
dotsync apps                              # picker 로 추적 앱 변경 (Enter = 그대로)
dotsync config show                       # 현재 설정 보기
dotsync config dir ~/another-folder       # sync 폴더 변경
dotsync config apps claude,zsh            # 추적 앱 일괄 교체 (자동화용)
dotsync config btt-presets MyPreset,Other # BTT preset 목록 일괄 교체 (콤마 구분)
```

> 새로 저장되는 `dotsync.toml`은 BTT 옵션을 `[options.bettertouchtool]` 서브 테이블로 적는다 (기존 `bettertouchtool_presets = [...]` 형식도 호환을 위해 계속 읽힌다).

picker 키 안내:

- `↑` / `↓` — 항목 이동
- `space` — 현재 항목 체크 토글
- `enter` — 확정
- `q` / `esc` / `ctrl+c` — 취소 (설정 변경 없음)

CI나 파이프 같은 비-TTY 환경에서는 자동으로 앱별 y/n 프롬프트로 fallback 한다.

지원 앱: `claude`, `ghostty`, `bettertouchtool`, `zsh`

### 새 앱 추가

`docs/adding-an-app.md` 참고. 단순 파일 기반 앱은 모듈 1개 + `apps/__init__.py`에 한 줄로 끝난다. 복잡한 앱(외부 프로세스, 자체 CLI 옵션)은 base의 hook을 override해서 같은 자리에서 확장한다.

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

If Python 3.12 or 3.13 already exists at a canonical path
(`/opt/homebrew/bin/python3.{12,13}`, `/usr/local/bin/python3.{12,13}`,
or `/Library/Frameworks/Python.framework/Versions/3.{12,13}/...`),
dotsync reuses it — no duplicate install. Otherwise Homebrew pulls in
`python@3.12` as a dependency.

> Note: Pythons installed via pyenv / uv are at non-canonical paths and
> won't be auto-detected — Homebrew will still install its own
> `python@3.12`. Functionality is unaffected, but the duplicate install
> is not avoided.

> Always start with **`dotsync init`** — it picks the sync folder. After that, `from` / `to` work from anywhere.

### Usage

#### 1. One-time setup — pick a sync folder and the apps to track

`dotsync init` is a two-step wizard.

**Step 1 — Sync folder.** Type the folder path. Bare Enter accepts the default `~/Desktop/dotsync_config`.

**Step 2 — Pick apps to track.** An arrow-key picker opens immediately. Apps installed on this machine come pre-checked (`installed` hint); the rest start unchecked (`not installed`). Toggle with the keys, Enter to confirm.

```bash
dotsync init
# ▶ Step 1 — Sync folder
# ? sync folder (absolute path) [/Users/you/Desktop/dotsync_config] › ⏎
# ✔ folder ready → /Users/you/Desktop/dotsync_config
#
# ▶ Step 2 — Pick apps to track
#   Pick apps to track   ↑/↓ move · space toggle · enter submit
#
#   ▸ [x] claude              installed
#     [x] ghostty             installed
#     [x] bettertouchtool     installed · 2 presets
#     [x] zsh                 installed
#
# ✔ tracked: claude · ghostty · bettertouchtool · zsh
# ✔ BetterTouchTool presets = Master_bt, Mini_bt   (auto-detected)
# ✔ config saved → /Users/you/Desktop/dotsync_config/dotsync.toml
```

Row colors flag misconfigured states at a glance:

- `[x] + installed` → default (healthy)
- `[x] + not installed` → red dim ("cleanup candidate")
- `[ ] + installed` → yellow dim ("add candidate")
- `[ ] + not installed` → dim

**BetterTouchTool presets are auto-discovered and all tracked.** dotsync reads BTT's internal SQLite store and writes every registered preset name into `bettertouchtool_presets` (e.g. `["Master_bt", "Mini_bt"]`). No per-preset prompt. `--yes` mode skips discovery in favor of a deterministic default (`["Master_bt"]`) — useful for CI.

Non-interactive (scripts / new-machine bootstrap):

```bash
# Both --dir and --apps can be omitted:
#   --dir defaults to ~/Desktop/dotsync_config
#   --apps defaults to all auto-detected apps
dotsync init --yes

# Or specify explicitly (BTT presets are comma-separated)
dotsync init --dir ~/my-configs --apps claude,zsh --btt-presets Master_bt,Mini_bt --yes
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

The banner's `→` arrow shows direction; the summary box names every app that actually synced.

```
╭──────────────────────────────────────────────────────────╮
│ dotsync from                                             │
│ 4 apps  →  /Users/you/Desktop/dotsync_config             │
╰──────────────────────────────────────────────────────────╯
... (per-app sections) ...
╭──────────────────────────────────────────────────────────╮
│ ✓ synced     claude · ghostty · bettertouchtool · zsh    │
│ 4 ok  ·  0 warn  ·  0 error  ·  2.3s                     │
╰──────────────────────────────────────────────────────────╯
```

Then commit the folder to git or let iCloud sync it — that's your backup.

#### 3. Folder → local apps (restore on another machine)

`dotsync to` prompts for confirmation before overwriting your local configs — the prompt uses a **yellow (warn) accent** so it can't be mistaken for a routine question. Use `--dry-run` to preview, `--yes` to skip the prompt in automation. The backup session path is printed at run time.

```bash
dotsync to --all --dry-run     # preview only
dotsync to --all                # interactive (asks Y/n, yellow accent)
dotsync to --all --yes          # automation (no prompt)
```

The summary box separates apps that actually changed (`✓ applied`) from apps that were already in sync (`· unchanged`).

```
╭──────────────────────────────────────────────────────────╮
│ ✓ applied     ghostty · bettertouchtool                  │
│ · unchanged   claude · zsh                               │
│ 4 ok  ·  0 warn  ·  0 error  ·  3.1s                     │
╰──────────────────────────────────────────────────────────╯
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

`dotsync apps` opens the same picker as init's Step 2. Toggling BTT on re-runs preset discovery and writes the result back to config — no separate command needed.

```bash
dotsync apps                              # picker to change the tracked apps (Enter = keep current)
dotsync config show                       # print current config
dotsync config dir ~/another-folder       # change sync folder
dotsync config apps claude,zsh            # replace the tracked-apps list (for automation)
dotsync config btt-presets MyPreset,Other # replace BTT preset list (comma-separated)
```

> Newly-saved `dotsync.toml` files write BTT options under an `[options.bettertouchtool]` sub-table (the legacy `bettertouchtool_presets = [...]` form is still read for backward compatibility).

Picker keys:

- `↑` / `↓` — move
- `space` — toggle the current row
- `enter` — confirm
- `q` / `esc` / `ctrl+c` — cancel (no config change)

In non-TTY environments (CI, piped stdin) it automatically falls back to
sequential per-app y/n prompts.

Supported apps: `claude`, `ghostty`, `bettertouchtool`, `zsh`

### Adding a new app

See `docs/adding-an-app.md`. A simple file-based app is one module + one line in `apps/__init__.py`'s `APP_CLASSES`. Complex apps (external processes, custom CLI options) extend in place by overriding `App` hooks.

Help: `dotsync --help`, `dotsync <command> --help`

---

## License

MIT
