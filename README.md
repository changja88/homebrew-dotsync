# dotsync

macOS 앱 설정을 폴더와 양방향으로 동기화하는 CLI · A CLI that syncs macOS app configs with a folder of your choice.

---

## 한국어

### 목적

dotsync는 macOS의 앱 설정(Claude Code, Ghostty, BetterTouchTool, zsh)을 사용자가 지정한 로컬 폴더와 양방향으로 동기화한다. 도구와 데이터를 분리해서, 폴더는 git이나 iCloud 등으로 자유롭게 백업/복제할 수 있고 새 Mac 셋업도 명령어 몇 줄로 끝난다.

### 설치

```bash
brew install changja88/dotsync/dotsync
```

### 사용법

```bash
# 1. 초기 설정 (폴더 경로 + 추적할 앱 선택; 대화형)
dotsync init

# 2. 로컬 앱 설정을 폴더로 가져오기
dotsync from --all

# 3. 폴더 내용을 로컬 앱에 적용 (직전 상태 자동 백업)
dotsync to --all

# 4. 동기화 상태 확인 (파일별 sha256 비교)
dotsync status
```

지원 앱: `claude`, `ghostty`, `bettertouchtool`, `zsh`

도움말: `dotsync --help`, `dotsync <command> --help`

---

## English

### Purpose

dotsync is a CLI that syncs your macOS app configs (Claude Code, Ghostty, BetterTouchTool, zsh) bidirectionally with a folder of your choice. Tool and data are separated — track the folder in git, sync it through iCloud, or just keep it local. Setting up a new Mac becomes a few commands.

### Install

```bash
brew install changja88/dotsync/dotsync
```

### Usage

```bash
# 1. First-time setup (pick a folder + which apps to track; interactive)
dotsync init

# 2. Pull current local configs into the folder
dotsync from --all

# 3. Push folder contents back into local apps (with automatic backup)
dotsync to --all

# 4. Check sync state (per-file sha256 diff)
dotsync status
```

Supported apps: `claude`, `ghostty`, `bettertouchtool`, `zsh`

Help: `dotsync --help`, `dotsync <command> --help`

---

## License

MIT
