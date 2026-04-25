# dotsync

macOS 앱 설정을 **사용자가 지정한 한 폴더**에서 관리하는 양방향 sync CLI · A CLI that consolidates your macOS app configs into **one folder of your choice**, with two-way sync.

---

## 한국어

### 목적

dotsync는 macOS의 앱 설정(Claude Code, Ghostty, BetterTouchTool, zsh)을 **사용자가 지정한 단일 폴더**에 모아서 양방향으로 동기화한다. 그 폴더는 어디든 OK — 새 폴더(`~/my-configs`)일 수도 있고, 이미 git이나 iCloud Drive로 관리 중인 폴더일 수도 있다. 도구(dotsync)와 데이터(폴더)를 분리해서, 새 Mac 셋업도 폴더만 옮겨오면 끝난다.

### 설치

```bash
brew install changja88/dotsync/dotsync
```

### 사용법

#### 1. 처음 한 번 — sync 대상 폴더 정하기 (앱은 자동 감지됨)

대화형으로 폴더 경로만 입력하면, 이 머신에 설치된 앱들이 자동 감지돼 default로 제시된다.

```bash
dotsync init
# sync folder (absolute path): /Users/you/my-configs
#
# Detected on this machine:
#   ✓ claude
#   ✓ ghostty
#   ✓ bettertouchtool
#   ✓ zsh
# Track all of these? [Y/n/edit]: ⏎
# BetterTouchTool preset name [Master_bt]: ⏎
```

`Y`(또는 Enter)면 감지된 전부 추적, `n`이면 아무것도 안 추적, `edit`이면 직접 입력.

비대화형(스크립트/새 머신 셋업용):

```bash
# --apps 생략 시 자동 감지된 전체를 추적
dotsync init --dir ~/my-configs --yes

# 명시적으로 지정도 가능
dotsync init --dir ~/my-configs --apps claude,zsh --btt-preset Master_bt --yes
```

설정은 두 곳에 저장된다:
- `<sync 폴더>/dotsync.toml` — 추적 앱, 백업 정책, BTT preset 등 (폴더와 함께 백업/이동된다)
- `~/.dotsync` — sync 폴더 절대경로 한 줄짜리 pointer

#### 새 머신에서 복원할 때

폴더에 이미 `dotsync.toml`이 있으면 dotsync는 그걸 그대로 채택한다 — 한 줄이면 끝.

```bash
git clone git@github.com:you/my-configs.git ~/my-configs
dotsync init --dir ~/my-configs --yes   # 폴더 안 dotsync.toml 그대로 사용, pointer만 갱신
dotsync to --all
```

#### 2. 로컬 앱 설정 → 폴더 (스냅샷 뜨기)

```bash
dotsync from --all          # 추적 중인 모든 앱
dotsync from claude         # 한 앱만
```

이후 그 폴더를 git에 커밋하거나 iCloud로 동기화해두면 백업이 된다.

#### 3. 폴더 → 로컬 앱 (다른 머신에서 복원하기)

```bash
dotsync to --all            # 직전 로컬 상태는 자동 백업됨
```

`to` 직전 로컬 파일은 `~/.local/share/dotsync/backups/<YYYYMMDD_HHMMSS>/<app>/`에 자동 백업된다.

#### 4. 동기화 상태 확인 (파일별 sha256 비교)

```bash
dotsync status
#   claude             clean
#   ghostty            dirty — config.ghostty
#   zsh                clean
```

#### 폴더/앱 목록을 나중에 바꾸고 싶으면

```bash
dotsync config show                       # 현재 설정 보기
dotsync config dir ~/another-folder       # sync 폴더 변경
dotsync config apps claude,zsh            # 추적 앱 변경
dotsync config btt-preset MyPreset        # BTT preset 이름 변경
```

지원 앱: `claude`, `ghostty`, `bettertouchtool`, `zsh`

도움말: `dotsync --help`, `dotsync <command> --help`

---

## English

### Purpose

dotsync consolidates your macOS app configs (Claude Code, Ghostty, BetterTouchTool, zsh) into **one folder of your choice** and keeps it in two-way sync with the apps. That folder can be anywhere — a fresh directory like `~/my-configs`, or a folder you already track in git or sync via iCloud Drive. Tool (dotsync) and data (the folder) are separated, so setting up a new Mac is just a matter of bringing the folder along.

### Install

```bash
brew install changja88/dotsync/dotsync
```

### Usage

#### 1. One-time setup — pick your sync folder (apps auto-detected)

Interactive. You provide the folder path; dotsync detects which supported apps are installed on this machine and offers them as the default.

```bash
dotsync init
# sync folder (absolute path): /Users/you/my-configs
#
# Detected on this machine:
#   ✓ claude
#   ✓ ghostty
#   ✓ bettertouchtool
#   ✓ zsh
# Track all of these? [Y/n/edit]: ⏎
# BetterTouchTool preset name [Master_bt]: ⏎
```

`Y` (or Enter) tracks all detected apps, `n` tracks none, `edit` lets you type a custom list.

Non-interactive (scripts / new-machine bootstrap):

```bash
# --apps omitted → use all auto-detected apps
dotsync init --dir ~/my-configs --yes

# Or specify explicitly
dotsync init --dir ~/my-configs --apps claude,zsh --btt-preset Master_bt --yes
```

Settings live in two places:
- `<sync folder>/dotsync.toml` — tracked apps, backup policy, BTT preset (travels with the folder)
- `~/.dotsync` — single-line pointer to the sync folder's absolute path

#### Restoring on a new machine

If the folder already contains a `dotsync.toml`, `init` adopts it as-is — one line is enough.

```bash
git clone git@github.com:you/my-configs.git ~/my-configs
dotsync init --dir ~/my-configs --yes   # reuses existing dotsync.toml, just refreshes pointer
dotsync to --all
```

#### 2. Local app configs → folder (take a snapshot)

```bash
dotsync from --all          # all tracked apps
dotsync from claude         # one app
```

Then commit the folder to git or let iCloud sync it — that's your backup.

#### 3. Folder → local apps (restore on another machine)

```bash
dotsync to --all            # local state is backed up automatically before overwrite
```

Each `to` snapshots the about-to-be-overwritten local files into `~/.local/share/dotsync/backups/<YYYYMMDD_HHMMSS>/<app>/`.

#### 4. Check sync state (per-file sha256 diff)

```bash
dotsync status
#   claude             clean
#   ghostty            dirty — config.ghostty
#   zsh                clean
```

#### Change the folder or app list later

```bash
dotsync config show                       # print current config
dotsync config dir ~/another-folder       # change sync folder
dotsync config apps claude,zsh            # change tracked apps
dotsync config btt-preset MyPreset        # change BTT preset name
```

Supported apps: `claude`, `ghostty`, `bettertouchtool`, `zsh`

Help: `dotsync --help`, `dotsync <command> --help`

---

## License

MIT
