# Notification Guard 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** launcher가 관리하는 launch마다 알림 설정 불변식 5종을 점검하고 드리프트를 자동 수리하는 가드를 구현한다.

**Architecture:** 순수 함수(텍스트 수리) + 원자적 적용 파이프라인 + best-effort 오케스트레이터의 3층. `notification_guard.py` 단일 모듈이 `_main_v2` 최상단에서 호출된다. 설계 명세: `local_dev/docs/notification-guard-spec.md` (v2, 적대 리뷰 반영).

**Tech Stack:** Python 3.12 stdlib only (`tomllib`, `json`, `re`, `pathlib`, `dataclasses`), pytest.

## Global Constraints

- **stdlib only** — 외부 의존성 추가 금지 (launcher 전체 규칙).
- TOML 파일은 **라인 보존 수리만** — 전체 재직렬화 금지. 예외: `~/.claude/settings.json`(JSON)은 `json.dump(indent=2, ensure_ascii=False)` 재직렬화 허용.
- **silent-when-clean**: 수리/경고 0건이면 출력 0줄.
- 가드의 어떤 실패도 **launch를 중단시키지 않는다** (예외는 GuardAction warn으로 강등).
- UI 출력은 기존 `ui.render_inline_row(label, value, status=...)` 사용, label은 `"notif guard"`.
- 테스트 실행: 저장소 루트에서 `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v`
- 수정 범위는 `local_dev/` 안에 닫는다. dotsync 본체(`lib/`, `tests/`)와 커밋 분리.
- 커밋은 main 직접(기존 local_dev 커밋 관례), 메시지 형식 `feat(local_dev): …`/`test(local_dev): …`/`docs(local_dev): …`, 트레일러:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MhX4kquFZp1GwMhyvsvpzy
  ```
- 테스트 픽스처의 가짜 홈 경로에는 **공백을 반드시 포함**한다 (`Application Support` 대응 검증).

---

### Task 1: 선행 검증 — `enabled = false`가 훅 실행을 실제로 억제하는지 e2e 확정

스펙의 "선행 검증 과제". **억제가 확인되지 않으면 여기서 중단하고 사용자에게 보고한다** (불변식 #3 폐기 → 스펙 회귀 필요).

**Files:**
- Create: 스크래치 전용 (scratchpad 아래, 커밋 안 함)
- Modify: `local_dev/docs/notification-guard-spec.md` (결과 추기)

**Interfaces:**
- Produces: 스펙 "선행 검증 과제" 절 하단에 결과 기록 (`검증 결과 (YYYY-MM-DD): …`). Task 4의 구현 근거.

- [ ] **Step 1: 리스너 스크립트 작성** — scratchpad에 `hook_listener.py`:

```python
import http.server
import sys

log_path = sys.argv[2]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode(errors="replace")
        with open(log_path, "a") as f:
            f.write(body + "\n---\n")
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
```

- [ ] **Step 2: scratch CODEX_HOME 구성**

```bash
S=<scratchpad>/permreq-e2e; mkdir -p "$S/home" "$S/work"
ACTIVE="$HOME/Library/Application Support/orca/codex-accounts/03b36326-81c7-46a4-bd76-ab4e3c91ab50/home"
cp "$ACTIVE/hooks.json" "$S/home/hooks.json"
cp "$ACTIVE/auth.json" "$S/home/auth.json"
```

config.toml: active 미러의 `[hooks.state."…"]` 블록들을 **키 경로만 `$S/home`으로 치환**해 복사하고(핸들러 내용이 같으면 trusted_hash 동일 — active/legacy 홈 비교로 기확인), 최소 설정을 더한다:

```bash
python3 - "$ACTIVE/config.toml" "$S/home" <<'EOF'
import re, sys, pathlib
src = pathlib.Path(sys.argv[1]).read_text()
scratch_home = sys.argv[2]
blocks = re.findall(r'\[hooks\.state\."([^"]+/hooks\.json:[^"]+)"\]\n(?:[^\[\n][^\n]*\n|\n)*', src)
out = ['approval_policy = "never"\nsandbox_mode = "read-only"\n[analytics]\nenabled = false\n']
for m in re.finditer(r'(\[hooks\.state\.")([^"]+?/home)(/hooks\.json:[^"]+"\]\n(?:trusted_hash[^\n]*\n)?)', src):
    out.append(m.group(1) + scratch_home + m.group(3))
pathlib.Path(scratch_home, "config.toml").write_text("\n".join(out))
EOF
```

- [ ] **Step 3: baseline 실행 (run A)** — 리스너 기동 후 ORCA env를 실어 codex exec:

```bash
python3 "$S/../hook_listener.py" 41799 "$S/posts_a.log" &  LISTENER=$!
cd "$S/work" && CODEX_HOME="$S/home" \
  ORCA_AGENT_HOOK_ENDPOINT="" \
  ORCA_AGENT_HOOK_PORT=41799 ORCA_AGENT_HOOK_TOKEN=test \
  ORCA_PANE_KEY=test-pane ORCA_TAB_ID=t1 ORCA_WORKTREE_ID=w1 \
  /opt/homebrew/bin/codex exec --skip-git-repo-check "reply with exactly: ok"
kill $LISTENER
grep -c "hook_event_name" "$S/posts_a.log"   # 기대: ≥1 (SessionStart/UserPromptSubmit/Stop 중)
```

**판정 분기**: posts_a.log에 POST가 0건이면 exec 모드에서 훅 자체가 안 도는 것 — **중단하고 사용자 보고** (TUI 검증 필요 여부 논의). ≥1건이면 계속.

- [ ] **Step 4: 억제 실행 (run B)** — run A에서 도달한 이벤트 하나(예: `SessionStart` → 키 `…:session_start:0:0`)의 hooks.state 블록에 `enabled = false` 한 줄을 추가하고 새 로그로 재실행:

```bash
# config.toml의 해당 블록에 enabled = false 추가 후:
python3 "$S/../hook_listener.py" 41799 "$S/posts_b.log" &  LISTENER=$!
cd "$S/work" && CODEX_HOME="$S/home" \
  ORCA_AGENT_HOOK_ENDPOINT="" \
  ORCA_AGENT_HOOK_PORT=41799 ORCA_AGENT_HOOK_TOKEN=test \
  ORCA_PANE_KEY=test-pane ORCA_TAB_ID=t1 ORCA_WORKTREE_ID=w1 \
  /opt/homebrew/bin/codex exec --skip-git-repo-check "reply with exactly: ok"
kill $LISTENER
grep -o '"hook_event_name":"[A-Za-z]*"' "$S/posts_b.log" | sort | uniq -c
```

기대: 끈 이벤트의 POST만 사라지고 다른 이벤트는 유지 → **억제 확인**.

- [ ] **Step 5: 결과를 스펙에 추기** — `notification-guard-spec.md`의 "선행 검증 과제" 절 끝에 결과(날짜, run A/B 이벤트 카운트, 판정) 3–5줄 추가.

- [ ] **Step 6: 문서 커밋**

```bash
git add local_dev/docs/notification-guard-spec.md local_dev/docs/notification-guard-plan.md
git commit -m "docs(local_dev): notification guard 설계 명세·구현 계획 추가"
```

---

### Task 2: 모듈 골격 — GuardAction·대상 발견 + 테스트 픽스처

**Files:**
- Create: `local_dev/serena_mcp_management/notification_guard.py`
- Create: `local_dev/tests/test_notification_guard.py`

**Interfaces:**
- Produces:
  - `GuardAction(kind: str, message: str, path: Path | None = None)` — frozen dataclass, kind ∈ {"repair", "warn"}
  - `CodexTarget(config: Path, hooks_json: Path | None)` — frozen dataclass; hooks_json이 None이면 불변식 #3 미적용(user config)
  - `discover_codex_targets(home: Path) -> list[CodexTarget]`
  - `discover_orca_data_files(home: Path) -> list[Path]`

- [ ] **Step 1: 실패하는 테스트 작성** — `local_dev/tests/test_notification_guard.py`:

```python
"""notification_guard 짝 테스트 — 설계 명세: local_dev/docs/notification-guard-spec.md"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_dev.serena_mcp_management.notification_guard import (
    CodexTarget,
    discover_codex_targets,
    discover_orca_data_files,
)

HOOKS_JSON = json.dumps({
    "hooks": {
        "PermissionRequest": [
            {"hooks": [{"type": "command", "command": "/bin/true", "timeout": 10}]}
        ],
        "Stop": [
            {"hooks": [{"type": "command", "command": "/bin/true", "timeout": 10}]}
        ],
    }
})

CLEAN_TUI = '[tui]\nnotifications = ["approval-requested"]\nnotification_condition = "unfocused"\n'


def clean_managed_config(home_dir: Path) -> str:
    key = f"{home_dir}/hooks.json:permission_request:0:0"
    return (
        'approvals_reviewer = "guardian_subagent"\n'
        "notify = []\n\n"
        f"{CLEAN_TUI}\n"
        f'[hooks.state."{key}"]\n'
        'trusted_hash = "sha256:e460"\n'
        "enabled = false\n"
    )


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    # 공백 포함 경로 강제: 실경로의 "Application Support" 대응을 우회로 통과 못 하게 한다
    home = tmp_path / "fake home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        'notify = []\n\n' + CLEAN_TUI
    )
    orca = home / "Library" / "Application Support" / "orca"
    for rel in ("codex-accounts/abc-123/home", "codex-runtime-home/home"):
        managed = orca / rel
        managed.mkdir(parents=True)
        (managed / "hooks.json").write_text(HOOKS_JSON)
        (managed / "config.toml").write_text(clean_managed_config(managed))
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"preferredNotifChannel": "notifications_disabled"}, indent=2)
    )
    profile = orca / "profiles" / "local-default"
    profile.mkdir(parents=True)
    (profile / "orca-data.json").write_text(json.dumps({
        "settings": {"notifications": {
            "enabled": True, "agentTaskComplete": True, "terminalBell": False,
        }}
    }))
    return home


class TestDiscovery:
    def test_finds_user_and_managed_configs(self, fake_home: Path) -> None:
        targets = discover_codex_targets(fake_home)
        configs = [t.config for t in targets]
        assert fake_home / ".codex" / "config.toml" in configs
        assert len([t for t in targets if t.hooks_json is not None]) == 2

    def test_user_config_has_no_hooks_json(self, fake_home: Path) -> None:
        user = [t for t in discover_codex_targets(fake_home)
                if t.config == fake_home / ".codex" / "config.toml"]
        assert user[0].hooks_json is None

    def test_missing_files_are_skipped(self, tmp_path: Path) -> None:
        assert discover_codex_targets(tmp_path / "empty home") == []

    def test_finds_orca_profiles(self, fake_home: Path) -> None:
        files = discover_orca_data_files(fake_home)
        assert len(files) == 1
        assert files[0].name == "orca-data.json"
```

- [ ] **Step 2: RED 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: … notification_guard`

- [ ] **Step 3: 최소 구현** — `local_dev/serena_mcp_management/notification_guard.py`:

```python
"""launch 시 알림 설정 불변식을 점검·자동 수리하는 가드.

설계 명세: local_dev/docs/notification-guard-spec.md
알림 정책("입력 필요/완료 시에만")을 되돌리는 외부 writer(ChatGPT 앱의
notify 재주입, codex 신뢰 재기록, orca 재미러링)에 맞서 관리되는 launch마다
설정을 수렴시킨다. silent-when-clean, best-effort — launch를 절대 막지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GuardAction:
    kind: str  # "repair" | "warn"
    message: str
    path: Path | None = None


@dataclass(frozen=True)
class CodexTarget:
    config: Path
    hooks_json: Path | None  # None → 불변식 #3 미적용 (user config)


def discover_codex_targets(home: Path) -> list[CodexTarget]:
    targets: list[CodexTarget] = []
    user = home / ".codex" / "config.toml"
    if user.is_file():
        targets.append(CodexTarget(config=user, hooks_json=None))
    orca = home / "Library" / "Application Support" / "orca"
    for pattern in ("codex-accounts/*/home", "codex-runtime-home/home"):
        for managed_home in sorted(orca.glob(pattern)):
            config = managed_home / "config.toml"
            if config.is_file():
                targets.append(
                    CodexTarget(config=config, hooks_json=managed_home / "hooks.json")
                )
    return targets


def discover_orca_data_files(home: Path) -> list[Path]:
    orca = home / "Library" / "Application Support" / "orca"
    return sorted(orca.glob("profiles/*/orca-data.json"))
```

- [ ] **Step 4: GREEN 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v`
Expected: 4 passed

- [ ] **Step 5: 커밋**

```bash
git add local_dev/serena_mcp_management/notification_guard.py local_dev/tests/test_notification_guard.py
git commit -m "feat(local_dev): notification guard 골격 — 대상 발견과 GuardAction"
```

---

### Task 3: 불변식 #1(notify)·#2(notification_condition) 텍스트 수리 순수 함수

**Files:**
- Modify: `local_dev/serena_mcp_management/notification_guard.py`
- Modify: `local_dev/tests/test_notification_guard.py`

**Interfaces:**
- Produces:
  - `repair_notify(text: str) -> tuple[str, str | None]` — preamble(첫 `[` 헤더 이전)의 `notify`가 `[]`가 아니면 `notify = []`로 치환. 반환 `(새 텍스트, 제거된 줄 | None)`
  - `repair_tui_condition(text: str) -> tuple[str, bool]` — `[tui]` 섹션의 `notification_condition = "always"`를 `"unfocused"`로. 반환 `(새 텍스트, 수리 여부)`

- [ ] **Step 1: 실패하는 테스트 작성** — 테스트 파일에 추가:

```python
from local_dev.serena_mcp_management.notification_guard import (
    repair_notify,
    repair_tui_condition,
)

SKY = ("/Users/x/.codex/computer-use/Codex Computer Use.app/Contents/SharedSupport/"
       "SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient")


class TestRepairNotify:
    def test_clean_config_unchanged(self) -> None:
        text = "notify = []\n\n[tools]\nview_image = true\n"
        assert repair_notify(text) == (text, None)

    def test_sky_reinjection_with_previous_notify_arg(self) -> None:
        text = (
            "# 주석은 보존된다\n"
            f'notify = ["{SKY}", "turn-ended", "--previous-notify", "[]"]\n'
            "\n[mcp_servers.computer-use]\n"
            f'command = "{SKY}"\n'
        )
        new, removed = repair_notify(text)
        assert "notify = []\n" in new
        assert removed is not None and "turn-ended" in removed
        assert "# 주석은 보존된다" in new
        # 테이블 내부의 SkyComputerUseClient 경로 줄은 무접촉
        assert f'command = "{SKY}"' in new

    def test_unknown_program_also_emptied(self) -> None:
        text = 'notify = ["/usr/bin/say", "done"]\n\n[tools]\n'
        new, removed = repair_notify(text)
        assert "notify = []\n" in new
        assert removed is not None and "/usr/bin/say" in removed

    def test_absent_notify_untouched(self) -> None:
        text = "[tools]\nview_image = true\n"
        assert repair_notify(text) == (text, None)


class TestRepairTuiCondition:
    def test_always_becomes_unfocused(self) -> None:
        text = '[tui]\nnotification_condition = "always"\ntheme = "x"\n'
        new, repaired = repair_tui_condition(text)
        assert repaired is True
        assert 'notification_condition = "unfocused"' in new
        assert 'theme = "x"' in new

    def test_unfocused_unchanged(self) -> None:
        text = '[tui]\nnotification_condition = "unfocused"\n'
        assert repair_tui_condition(text) == (text, False)

    def test_same_key_outside_tui_untouched(self) -> None:
        text = (
            '[other]\nnotification_condition = "always"\n\n'
            '[tui]\nnotification_condition = "unfocused"\n'
        )
        assert repair_tui_condition(text) == (text, False)
```

- [ ] **Step 2: RED 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v -k "RepairNotify or RepairTui"`
Expected: FAIL — `ImportError: cannot import name 'repair_notify'`

- [ ] **Step 3: 구현** — 모듈에 추가 (`import re`, `import tomllib` 상단 추가):

```python
_NOTIFY_LINE = re.compile(r"notify\s*=")
_TUI_ALWAYS_LINE = re.compile(r'notification_condition\s*=\s*"always"')


def repair_notify(text: str) -> tuple[str, str | None]:
    """preamble의 notify를 []로 수렴. (새 텍스트, 제거된 줄|None) 반환.

    파싱값 기준으로 판정하고 라인 기준으로 수리한다. 멀티라인 notify 배열은
    관측된 적 없어 한 줄 치환만 지원 — 어긋나면 임시 파일 파싱 검증이 막는다.
    """
    if tomllib.loads(text).get("notify", []) == []:
        return text, None
    out: list[str] = []
    removed: str | None = None
    in_preamble = True
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if in_preamble and stripped.startswith("["):
            in_preamble = False
        if in_preamble and removed is None and _NOTIFY_LINE.match(stripped):
            removed = stripped
            out.append("notify = []" + ("\n" if line.endswith("\n") else ""))
            continue
        out.append(line)
    return "".join(out), removed


def repair_tui_condition(text: str) -> tuple[str, bool]:
    if tomllib.loads(text).get("tui", {}).get("notification_condition") != "always":
        return text, False
    out: list[str] = []
    in_tui = False
    repaired = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("["):
            in_tui = stripped == "[tui]"
        if in_tui and not repaired and _TUI_ALWAYS_LINE.match(stripped):
            out.append(
                'notification_condition = "unfocused"'
                + ("\n" if line.endswith("\n") else "")
            )
            repaired = True
            continue
        out.append(line)
    return "".join(out), repaired
```

- [ ] **Step 4: GREEN 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v`
Expected: 전부 passed

- [ ] **Step 5: 커밋**

```bash
git add -u local_dev/
git commit -m "feat(local_dev): notification guard — notify·tui condition 수리 함수"
```

---

### Task 4: 불변식 #3 — hooks.state 키 도출과 enabled=false 수리

**Files:**
- Modify: `local_dev/serena_mcp_management/notification_guard.py`
- Modify: `local_dev/tests/test_notification_guard.py`

**Interfaces:**
- Consumes: Task 1의 e2e 확정 결과 (억제 미확인이면 이 태스크는 실행하지 않는다)
- Produces:
  - `permission_request_state_keys(hooks_json: Path) -> list[str]` — hooks.json을 파싱해 `"<hooks_json 경로>:permission_request:<g>:<h>"` 키 목록 (인덱스 하드코딩 금지)
  - `repair_hooks_state(text: str, keys: list[str]) -> tuple[str, list[str]]` — 각 키 블록에 `enabled = false` 보장. 반환 `(새 텍스트, 수리한 키들)`
  - `GUARDIAN_REVIEWER = "guardian_subagent"` (모듈 상수)

- [ ] **Step 1: 실패하는 테스트 작성**:

```python
from local_dev.serena_mcp_management.notification_guard import (
    permission_request_state_keys,
    repair_hooks_state,
)


class TestPermissionRequestKeys:
    def test_derives_index_from_hooks_json(self, tmp_path: Path) -> None:
        hooks = tmp_path / "fake home" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(json.dumps({"hooks": {"PermissionRequest": [
            {"hooks": [{"type": "command", "command": "/bin/true"},
                       {"type": "command", "command": "/bin/echo"}]},
        ]}}))
        keys = permission_request_state_keys(hooks)
        assert keys == [
            f"{hooks}:permission_request:0:0",
            f"{hooks}:permission_request:0:1",
        ]

    def test_no_permission_request_event(self, tmp_path: Path) -> None:
        hooks = tmp_path / "hooks.json"
        hooks.write_text(json.dumps({"hooks": {"Stop": []}}))
        assert permission_request_state_keys(hooks) == []


class TestRepairHooksState:
    KEY = "/fake home/hooks.json:permission_request:0:0"

    def test_enabled_false_already_present(self) -> None:
        text = f'[hooks.state."{self.KEY}"]\ntrusted_hash = "sha256:x"\nenabled = false\n'
        assert repair_hooks_state(text, [self.KEY]) == (text, [])

    def test_reinserts_removed_enabled_line(self) -> None:
        text = (
            f'[hooks.state."{self.KEY}"]\n'
            'trusted_hash = "sha256:x"\n\n'
            "[tools]\nview_image = true\n"
        )
        new, repaired = repair_hooks_state(text, [self.KEY])
        assert repaired == [self.KEY]
        cfg = __import__("tomllib").loads(new)
        assert cfg["hooks"]["state"][self.KEY]["enabled"] is False
        assert cfg["hooks"]["state"][self.KEY]["trusted_hash"] == "sha256:x"
        assert cfg["tools"]["view_image"] is True

    def test_creates_missing_block_at_eof(self) -> None:
        text = "[tools]\nview_image = true\n"
        new, repaired = repair_hooks_state(text, [self.KEY])
        assert repaired == [self.KEY]
        cfg = __import__("tomllib").loads(new)
        assert cfg["hooks"]["state"][self.KEY]["enabled"] is False
```

- [ ] **Step 2: RED 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v -k "PermissionRequestKeys or RepairHooksState"`
Expected: FAIL — ImportError

- [ ] **Step 3: 구현** (`import json` 상단 추가):

```python
GUARDIAN_REVIEWER = "guardian_subagent"
_GUARD_COMMENT = (
    "# [notification guard] guardian_subagent가 승인을 자동 처리하므로 이 훅"
    '(가짜 "Codex needs input" 알림의 원인)만 끈다.'
)


def permission_request_state_keys(hooks_json: Path) -> list[str]:
    data = json.loads(hooks_json.read_text())
    groups = data.get("hooks", {}).get("PermissionRequest") or []
    keys: list[str] = []
    for g, group in enumerate(groups):
        for h in range(len(group.get("hooks") or [])):
            keys.append(f"{hooks_json}:permission_request:{g}:{h}")
    return keys


def repair_hooks_state(text: str, keys: list[str]) -> tuple[str, list[str]]:
    state = tomllib.loads(text).get("hooks", {}).get("state", {})
    needs = [k for k in keys if (state.get(k) or {}).get("enabled") is not False]
    if not needs:
        return text, []
    lines = text.splitlines()
    for key in needs:
        header = f'[hooks.state."{key}"]'
        try:
            start = lines.index(header)
        except ValueError:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend([_GUARD_COMMENT, header, "enabled = false"])
            continue
        end = start + 1
        while end < len(lines) and not lines[end].lstrip().startswith("["):
            end += 1
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = [_GUARD_COMMENT, "enabled = false"]
    return "\n".join(lines) + "\n", needs
```

- [ ] **Step 4: GREEN 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v`
Expected: 전부 passed

- [ ] **Step 5: 커밋**

```bash
git add -u local_dev/
git commit -m "feat(local_dev): notification guard — permission_request 훅 비활성 수리"
```

---

### Task 5: 원자적 적용 파이프라인 (임시 파일 검증 → mtime 재확인 → replace)

**Files:**
- Modify: `local_dev/serena_mcp_management/notification_guard.py`
- Modify: `local_dev/tests/test_notification_guard.py`

**Interfaces:**
- Produces:
  - `RepairOutcome(status: str, meta: object = None)` — status ∈ {"unchanged", "repaired", "invalid", "conflicted"}
  - `apply_text_repair(path: Path, transform: Callable[[str], tuple[str, object]], validate: Callable[[str], object]) -> RepairOutcome`
  - 절차(스펙 순서 엄수): 읽기(mtime/size 기록) → 임시 파일 작성 → **임시 파일 파싱 검증**(실패 시 임시 삭제, 원본 무접촉, "invalid") → 원본 mtime/size 재확인(변경 시 1회 재시도, 재차 변경 "conflicted") → `os.replace`

- [ ] **Step 1: 실패하는 테스트 작성**:

```python
from local_dev.serena_mcp_management.notification_guard import (
    RepairOutcome,
    apply_text_repair,
)
import tomllib


class TestApplyTextRepair:
    def test_unchanged_when_transform_is_identity(self, tmp_path: Path) -> None:
        p = tmp_path / "a.toml"
        p.write_text("x = 1\n")
        outcome = apply_text_repair(p, lambda t: (t, None), tomllib.loads)
        assert outcome.status == "unchanged"

    def test_repaired_and_meta_passthrough(self, tmp_path: Path) -> None:
        p = tmp_path / "a.toml"
        p.write_text("x = 1\n")
        outcome = apply_text_repair(p, lambda t: ("x = 2\n", "meta!"), tomllib.loads)
        assert outcome.status == "repaired"
        assert outcome.meta == "meta!"
        assert p.read_text() == "x = 2\n"

    def test_invalid_result_leaves_original_untouched(self, tmp_path: Path) -> None:
        p = tmp_path / "a.toml"
        p.write_text("x = 1\n")
        outcome = apply_text_repair(p, lambda t: ("[broken", None), tomllib.loads)
        assert outcome.status == "invalid"
        assert p.read_text() == "x = 1\n"
        assert list(tmp_path.iterdir()) == [p]  # 임시 파일 잔류 없음

    def test_concurrent_write_detected_then_retried(self, tmp_path: Path) -> None:
        p = tmp_path / "a.toml"
        p.write_text("x = 1\n")
        calls = {"n": 0}

        def transform(text: str) -> tuple[str, None]:
            calls["n"] += 1
            if calls["n"] == 1:
                # 첫 시도 도중 다른 writer가 파일을 바꿔치기
                p.write_text("x = 99\n")
            return text.replace("x = 99", "x = 2").replace("x = 1", "x = 2"), None

        outcome = apply_text_repair(p, transform, tomllib.loads)
        assert outcome.status == "repaired"
        assert calls["n"] == 2          # 재시도 1회
        assert p.read_text() == "x = 2\n"
```

- [ ] **Step 2: RED 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v -k ApplyTextRepair`
Expected: FAIL — ImportError

- [ ] **Step 3: 구현** (`import os`, `from collections.abc import Callable` 상단 추가):

```python
@dataclass(frozen=True)
class RepairOutcome:
    status: str  # "unchanged" | "repaired" | "invalid" | "conflicted"
    meta: object = None


def apply_text_repair(
    path: Path,
    transform: Callable[[str], tuple[str, object]],
    validate: Callable[[str], object],
) -> RepairOutcome:
    for _attempt in range(2):
        original = path.read_text()
        before = path.stat()
        new_text, meta = transform(original)
        if new_text == original:
            return RepairOutcome("unchanged")
        tmp = path.with_name(f".{path.name}.notifguard.tmp")
        tmp.write_text(new_text)
        try:
            validate(tmp.read_text())
        except Exception:
            tmp.unlink(missing_ok=True)
            return RepairOutcome("invalid")
        after = path.stat()
        if (after.st_mtime_ns, after.st_size) != (before.st_mtime_ns, before.st_size):
            tmp.unlink(missing_ok=True)
            continue  # 동시 수정 감지 — 처음부터 1회 재시도
        os.replace(tmp, path)
        return RepairOutcome("repaired", meta)
    return RepairOutcome("conflicted")
```

- [ ] **Step 4: GREEN 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v`
Expected: 전부 passed

- [ ] **Step 5: 커밋**

```bash
git add -u local_dev/
git commit -m "feat(local_dev): notification guard — 원자적 수리 파이프라인"
```

---

### Task 6: 불변식 #4(claude settings)·#5(orca 토글 경고)

**Files:**
- Modify: `local_dev/serena_mcp_management/notification_guard.py`
- Modify: `local_dev/tests/test_notification_guard.py`

**Interfaces:**
- Produces:
  - `DESIRED_CLAUDE_NOTIF_CHANNEL = "notifications_disabled"` (모듈 상수)
  - `repair_claude_settings(path: Path) -> RepairOutcome` — JSON 재직렬화(`indent=2, ensure_ascii=False`) 허용
  - `check_orca_notifications(path: Path) -> list[GuardAction]` — 수리 없음, 경고만

- [ ] **Step 1: 실패하는 테스트 작성**:

```python
from local_dev.serena_mcp_management.notification_guard import (
    check_orca_notifications,
    repair_claude_settings,
)


class TestClaudeSettings:
    def test_drifted_channel_repaired_with_korean_preserved(self, tmp_path: Path) -> None:
        p = tmp_path / "settings.json"
        p.write_text(json.dumps(
            {"preferredNotifChannel": "terminal_bell", "language": "한국어"},
            indent=2, ensure_ascii=False,
        ))
        outcome = repair_claude_settings(p)
        assert outcome.status == "repaired"
        data = json.loads(p.read_text())
        assert data["preferredNotifChannel"] == "notifications_disabled"
        assert "한국어" in p.read_text()  # ensure_ascii=False 왕복

    def test_clean_settings_unchanged(self, tmp_path: Path) -> None:
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"preferredNotifChannel": "notifications_disabled"}))
        assert repair_claude_settings(p).status == "unchanged"


class TestOrcaToggles:
    def _write(self, tmp_path: Path, **notif: object) -> Path:
        p = tmp_path / "orca-data.json"
        base = {"enabled": True, "agentTaskComplete": True, "terminalBell": False}
        base.update(notif)
        p.write_text(json.dumps({"settings": {"notifications": base}}))
        return p

    def test_clean_toggles_no_actions(self, tmp_path: Path) -> None:
        assert check_orca_notifications(self._write(tmp_path)) == []

    def test_master_enabled_off_warns(self, tmp_path: Path) -> None:
        actions = check_orca_notifications(self._write(tmp_path, enabled=False))
        assert len(actions) == 1 and actions[0].kind == "warn"

    def test_bell_on_warns_without_writing(self, tmp_path: Path) -> None:
        p = self._write(tmp_path, terminalBell=True)
        before = p.read_text()
        actions = check_orca_notifications(p)
        assert actions[0].kind == "warn"
        assert p.read_text() == before  # 절대 수정하지 않는다
```

- [ ] **Step 2: RED 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v -k "ClaudeSettings or OrcaToggles"`
Expected: FAIL — ImportError

- [ ] **Step 3: 구현**:

```python
DESIRED_CLAUDE_NOTIF_CHANNEL = "notifications_disabled"


def repair_claude_settings(path: Path) -> RepairOutcome:
    def transform(text: str) -> tuple[str, object]:
        data = json.loads(text)
        if data.get("preferredNotifChannel") == DESIRED_CLAUDE_NOTIF_CHANNEL:
            return text, None
        previous = data.get("preferredNotifChannel")
        data["preferredNotifChannel"] = DESIRED_CLAUDE_NOTIF_CHANNEL
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n", previous

    return apply_text_repair(path, transform, json.loads)


def check_orca_notifications(path: Path) -> list[GuardAction]:
    notif = json.loads(path.read_text()).get("settings", {}).get("notifications", {})
    problems: list[str] = []
    if notif.get("enabled") is not True:
        problems.append("알림 비활성")
    if notif.get("agentTaskComplete") is not True:
        problems.append("Agent 작업 완료 꺼짐")
    if notif.get("terminalBell") is not False:
        problems.append("Terminal 벨 켜짐")
    if not problems:
        return []
    return [GuardAction(
        "warn",
        f"orca 알림 토글 어긋남({', '.join(problems)}) — Orca 설정 › Notifications에서 조정 필요",
        path,
    )]
```

- [ ] **Step 4: GREEN 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v`
Expected: 전부 passed

- [ ] **Step 5: 커밋**

```bash
git add -u local_dev/
git commit -m "feat(local_dev): notification guard — claude 채널 수리·orca 토글 경고"
```

---

### Task 7: 오케스트레이터 — guard_codex_target + run_notification_guard

**Files:**
- Modify: `local_dev/serena_mcp_management/notification_guard.py`
- Modify: `local_dev/tests/test_notification_guard.py`

**Interfaces:**
- Consumes: Task 2–6의 모든 공개 함수
- Produces:
  - `guard_codex_target(target: CodexTarget) -> list[GuardAction]`
  - `run_notification_guard(*, home: Path | None = None, stream: TextIO | None = None) -> list[GuardAction]` — launcher가 호출하는 유일한 진입점. 예외 절대 전파 금지.

- [ ] **Step 1: 실패하는 테스트 작성**:

```python
import io

from local_dev.serena_mcp_management.notification_guard import (
    guard_codex_target,
    run_notification_guard,
)


class TestGuardCodexTarget:
    def test_reviewer_user_skips_hooks_repair_but_warns_on_leftover(
        self, fake_home: Path
    ) -> None:
        managed = (fake_home / "Library" / "Application Support" / "orca"
                   / "codex-accounts" / "abc-123" / "home")
        config = managed / "config.toml"
        config.write_text(
            clean_managed_config(managed).replace(
                '"guardian_subagent"', '"user"'
            )
        )
        target = CodexTarget(config=config, hooks_json=managed / "hooks.json")
        actions = guard_codex_target(target)
        # enabled=false가 남아 있으므로 경고 1건, 수리 0건
        assert [a.kind for a in actions] == ["warn"]

    def test_missing_hooks_json_warns_and_skips(self, fake_home: Path) -> None:
        managed = (fake_home / "Library" / "Application Support" / "orca"
                   / "codex-runtime-home" / "home")
        (managed / "hooks.json").unlink()
        target = CodexTarget(config=managed / "config.toml",
                             hooks_json=managed / "hooks.json")
        actions = guard_codex_target(target)
        assert any(a.kind == "warn" for a in actions)


class TestRunNotificationGuard:
    def test_clean_home_silent(self, fake_home: Path) -> None:
        out = io.StringIO()
        actions = run_notification_guard(home=fake_home, stream=out)
        assert actions == []
        assert out.getvalue() == ""

    def test_drift_repaired_and_reported(self, fake_home: Path) -> None:
        user = fake_home / ".codex" / "config.toml"
        user.write_text(f'notify = ["{SKY}", "turn-ended"]\n\n' + CLEAN_TUI)
        out = io.StringIO()
        actions = run_notification_guard(home=fake_home, stream=out)
        assert any(a.kind == "repair" for a in actions)
        assert "notif guard" in out.getvalue()
        assert "notify = []" in user.read_text().splitlines()[0]

    def test_internal_error_becomes_warn_not_raise(self, fake_home: Path) -> None:
        # 파손된 TOML → 개별 대상 오류가 warn으로 강등되고 전체는 계속
        (fake_home / ".codex" / "config.toml").write_text("[broken")
        out = io.StringIO()
        actions = run_notification_guard(home=fake_home, stream=out)
        assert any(a.kind == "warn" for a in actions)
```

- [ ] **Step 2: RED 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v -k "GuardCodexTarget or RunNotificationGuard"`
Expected: FAIL — ImportError

- [ ] **Step 3: 구현** (`import sys`, `from typing import TextIO`, `from local_dev.serena_mcp_management.ui import render_inline_row` 상단 추가):

```python
def _short(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def guard_codex_target(target: CodexTarget) -> list[GuardAction]:
    actions: list[GuardAction] = []

    outcome = apply_text_repair(target.config, repair_notify, tomllib.loads)
    if outcome.status == "repaired":
        actions.append(GuardAction(
            "repair", f"codex notify 재주입 제거 ({_short(target.config)}): {outcome.meta}",
            target.config,
        ))
    elif outcome.status in {"invalid", "conflicted"}:
        actions.append(GuardAction(
            "warn", f"notify 수리 실패[{outcome.status}] ({_short(target.config)})",
            target.config,
        ))

    outcome = apply_text_repair(target.config, repair_tui_condition, tomllib.loads)
    if outcome.status == "repaired":
        actions.append(GuardAction(
            "repair", f"tui notification_condition → unfocused ({_short(target.config)})",
            target.config,
        ))
    elif outcome.status in {"invalid", "conflicted"}:
        actions.append(GuardAction(
            "warn",
            f"notification_condition 수리 실패[{outcome.status}] ({_short(target.config)})",
            target.config,
        ))

    if target.hooks_json is None:
        return actions
    if not target.hooks_json.is_file():
        actions.append(GuardAction(
            "warn", f"hooks.json 없음 — permission_request 점검 건너뜀 ({_short(target.hooks_json)})",
            target.hooks_json,
        ))
        return actions
    keys = permission_request_state_keys(target.hooks_json)
    if not keys:
        return actions
    cfg = tomllib.loads(target.config.read_text())
    if cfg.get("approvals_reviewer") == GUARDIAN_REVIEWER:
        outcome = apply_text_repair(
            target.config, lambda text: repair_hooks_state(text, keys), tomllib.loads
        )
        if outcome.status == "repaired":
            actions.append(GuardAction(
                "repair", f"permission_request 훅 비활성 복구 ({_short(target.config)})",
                target.config,
            ))
        elif outcome.status in {"invalid", "conflicted"}:
            actions.append(GuardAction(
                "warn",
                f"permission_request 수리 실패[{outcome.status}] ({_short(target.config)})",
                target.config,
            ))
    else:
        state = cfg.get("hooks", {}).get("state", {})
        if any((state.get(k) or {}).get("enabled") is False for k in keys):
            actions.append(GuardAction(
                "warn",
                "approvals_reviewer가 guardian이 아닌데 permission_request 훅이 꺼져 있음 —"
                f" 진짜 승인 알림이 오지 않습니다 ({_short(target.config)})",
                target.config,
            ))
    return actions


def run_notification_guard(
    *, home: Path | None = None, stream: TextIO | None = None
) -> list[GuardAction]:
    """모든 불변식을 점검·수리하고 GuardAction 목록을 반환한다. 예외 전파 금지."""
    out = stream if stream is not None else sys.stdout
    actions: list[GuardAction] = []
    try:
        base = home if home is not None else Path.home()
        for target in discover_codex_targets(base):
            try:
                actions.extend(guard_codex_target(target))
            except Exception as exc:
                actions.append(GuardAction(
                    "warn", f"가드 오류 ({_short(target.config)}): {exc}", target.config
                ))
        claude_settings = base / ".claude" / "settings.json"
        if claude_settings.is_file():
            try:
                outcome = repair_claude_settings(claude_settings)
                if outcome.status == "repaired":
                    actions.append(GuardAction(
                        "repair",
                        f"claude 알림 채널 → notifications_disabled (이전: {outcome.meta})",
                        claude_settings,
                    ))
                elif outcome.status in {"invalid", "conflicted"}:
                    actions.append(GuardAction(
                        "warn", f"claude settings 수리 실패[{outcome.status}]",
                        claude_settings,
                    ))
            except Exception as exc:
                actions.append(GuardAction("warn", f"claude settings 가드 오류: {exc}",
                                           claude_settings))
        for orca_data in discover_orca_data_files(base):
            try:
                actions.extend(check_orca_notifications(orca_data))
            except Exception as exc:
                actions.append(GuardAction("warn", f"orca 설정 점검 오류: {exc}", orca_data))
    except Exception as exc:  # 가드 자체가 launch를 막으면 안 된다
        actions.append(GuardAction("warn", f"notification guard 내부 오류: {exc}", None))
    try:
        for action in actions:
            out.write(render_inline_row(
                "notif guard", action.message,
                status="done" if action.kind == "repair" else "warn",
            ))
        if actions:
            out.flush()
    except Exception:
        pass
    return actions
```

- [ ] **Step 4: GREEN 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_notification_guard.py -v`
Expected: 전부 passed

- [ ] **Step 5: 커밋**

```bash
git add -u local_dev/
git commit -m "feat(local_dev): notification guard — 오케스트레이터와 UI 출력"
```

---

### Task 8: launcher 통합 + 문서 + 롤아웃

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py` (`_main_v2` 최상단, import 블록)
- Modify: `local_dev/tests/test_launcher_phases.py` (호출 확인 테스트 추가)
- Modify: `local_dev/README.md`

**Interfaces:**
- Consumes: `run_notification_guard(*, stream)` (Task 7)

- [ ] **Step 1: 실패하는 테스트 작성** — `test_launcher_phases.py`에 추가 (기존 테스트 스타일의 monkeypatch 관례를 따른다):

```python
def test_main_v2_runs_notification_guard_before_launch(monkeypatch, tmp_path):
    """가드는 interactive 여부와 무관하게 _main_v2 진입 즉시 1회 호출된다."""
    from local_dev.serena_mcp_management import serena_agent_launcher as launcher

    calls: list[bool] = []
    monkeypatch.setattr(
        launcher, "run_notification_guard",
        lambda *, stream=None: calls.append(True) or [],
    )
    # 가드 직후 단계에서 의도적으로 중단시켜 나머지 flow를 실행하지 않는다
    monkeypatch.setattr(
        launcher, "infer_client_type",
        lambda *a, **k: (_ for _ in ()).throw(SystemExit(99)),
    )
    monkeypatch.delenv("SERENA_AGENT_INTERACTIVE", raising=False)
    with pytest.raises(SystemExit):
        launcher._main_v2([])
    assert calls == [True]
```

- [ ] **Step 2: RED 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py -v -k notification_guard`
Expected: FAIL — `AttributeError: … has no attribute 'run_notification_guard'`

- [ ] **Step 3: 구현** — launcher import 블록에 추가:

```python
from local_dev.serena_mcp_management.notification_guard import run_notification_guard
```

`_main_v2` 최상단(`started_at = time.time()` 바로 다음, interactive 판정 이전)에 삽입:

```python
    # 알림 설정 불변식 가드 — 외부 writer가 되돌린 설정을 launch마다 수렴시킨다.
    # (spec: local_dev/docs/notification-guard-spec.md) 실패해도 launch는 계속.
    try:
        run_notification_guard(stream=sys.stdout)
    except Exception:
        pass
```

- [ ] **Step 4: GREEN + 전체 회귀 확인**

Run: `.venv/bin/python3 -m pytest local_dev/tests/ -v`
Expected: 신규 포함 전부 passed (기존 launcher 테스트 회귀 없음)

- [ ] **Step 5: README 갱신** — `local_dev/README.md`에 섹션 추가 (External CLI prerequisites 섹션 뒤):

```markdown
## Notification guard

launcher는 매 관리 launch 시작 시 알림 설정 불변식을 점검하고 드리프트를
자동 수리한다 (`notification_guard.py`, 설계: `docs/notification-guard-spec.md`).
대상: codex `notify = []`·`notification_condition`·permission_request 훅
비활성(guardian_subagent 구성일 때만), claude 알림 채널, orca 알림 토글(경고만).
정상이면 출력이 없고, 수리/경고 시에만 `notif guard` 행이 표시된다.
비대화식 호출(`codex exec` 등)은 shim이 launcher를 거치지 않으므로 가드
범위 밖이다.
```

- [ ] **Step 6: 커밋**

```bash
git add -u local_dev/
git commit -m "feat(local_dev): _main_v2에 notification guard 통합 + README"
```

- [ ] **Step 7: 미러 반영**

Run: `make -C local_dev install-shim`
Expected: `~/Desktop/dotsync_config/agent_launcher/` 미러 갱신 완료 메시지

- [ ] **Step 8: 스모크 검증 (실사용 확인)** — 아무 codex config에 드리프트를 주입한 뒤 **실제 Orca 패널에서** `codex`를 한 번 실행:

```bash
# 주입 (user config의 notify를 오염시킴)
python3 - <<'EOF'
import pathlib
p = pathlib.Path.home() / ".codex" / "config.toml"
p.write_text(p.read_text().replace("notify = []", 'notify = ["/usr/bin/true", "smoke-test"]', 1))
EOF
```

Orca 패널에서 `codex` 실행 → 화면에 `notif guard  codex notify 재주입 제거 …` 행이 보이고, `grep '^notify' ~/.codex/config.toml`이 `notify = []`이면 성공. 이 확인은 "orca 패널이 shim을 통과한다"는 커버리지 전제의 실증이므로 생략 금지 (사용자 확인 필요 단계).

---

## Self-Review 결과

- **Spec coverage**: 불변식 #1(T3) #2(T3) #3(T1+T4) #4(T6) #5(T6), 동적 발견(T2), 원자 절차·레이스(T5), best-effort·silent(T7), 통합 지점·커버리지 실증(T8), 선행 검증(T1), README/롤아웃(T8) — 스펙 테스트 계획 14케이스 전부 태스크에 매핑됨.
- **Placeholder**: 없음 (모든 스텝에 코드/커맨드/기대값 포함).
- **Type consistency**: `GuardAction`/`CodexTarget`/`RepairOutcome`/`apply_text_repair`/`run_notification_guard` 시그니처가 태스크 간 일치함을 확인.

---

### Task 9: 가드 가시성 — interactive 스피너 행 + 상시 결과 행 (스펙 v3 개정 반영)

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py` (`_run_notification_guard_v2` 신설, `_main_v2` 최상단 호출 교체)
- Modify: `local_dev/tests/test_launcher_phases.py`

**Interfaces:**
- Consumes: `run_notification_guard(*, home=None, stream=None) -> list[GuardAction]`, `GuardAction.kind`
- Produces: `_run_notification_guard_v2(*, stream: TextIO | None = None) -> None` — interactive면 스피너+결과 행, 비대화식이면 기존 위임. 예외 절대 전파 금지.

- [ ] **Step 1: 실패하는 테스트 작성** — `test_launcher_phases.py`에 추가 (기존 autouse `_stub_notification_guard`는 그대로 두고, 이 테스트들은 자체 monkeypatch로 덮는다):

```python
class TestNotificationGuardV2Row:
    def _actions(self, *kinds: str):
        from local_dev.serena_mcp_management.notification_guard import GuardAction
        return [GuardAction(kind, f"msg-{i}") for i, kind in enumerate(kinds)]

    def test_interactive_clean_shows_row(self, monkeypatch, capsys=None):
        import io
        monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
        monkeypatch.setattr(launcher, "run_notification_guard", lambda *, stream=None: [])
        out = io.StringIO()
        launcher._run_notification_guard_v2(stream=out)
        text = out.getvalue()
        assert "notif guard" in text
        assert "clean" in text

    def test_interactive_actions_show_summary_then_details(self, monkeypatch):
        import io

        def fake_guard(*, stream=None):
            stream.write("DETAIL-LINE\n")
            return self._actions("repair", "warn")

        monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
        monkeypatch.setattr(launcher, "run_notification_guard", fake_guard)
        out = io.StringIO()
        launcher._run_notification_guard_v2(stream=out)
        text = out.getvalue()
        assert "1 repaired" in text
        assert "1 warning" in text
        assert text.index("repaired") < text.index("DETAIL-LINE")  # 요약 → 상세 순서

    def test_interactive_guard_crash_degrades_to_warn_row(self, monkeypatch):
        import io

        def boom(*, stream=None):
            raise RuntimeError("boom")

        monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
        monkeypatch.setattr(launcher, "run_notification_guard", boom)
        out = io.StringIO()
        launcher._run_notification_guard_v2(stream=out)  # 예외가 전파되면 실패
        assert "failed" in out.getvalue()

    def test_non_interactive_delegates_silently(self, monkeypatch):
        import io
        calls = []
        monkeypatch.delenv("SERENA_AGENT_INTERACTIVE", raising=False)
        monkeypatch.setattr(
            launcher, "run_notification_guard",
            lambda *, stream=None: calls.append(stream) or [],
        )
        out = io.StringIO()
        launcher._run_notification_guard_v2(stream=out)
        assert calls == [out]          # 가드에 스트림 직접 위임
        assert out.getvalue() == ""    # 스피너/결과 행 없음 (silent-when-clean은 가드 몫)
```

- [ ] **Step 2: RED 확인** — `.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py -v -k NotificationGuardV2Row` → AttributeError 기대

- [ ] **Step 3: 구현** — launcher에 추가 (`import io`가 상단에 없으면 추가; PURPLE/PINK/style_spinner/SpinnerTicker는 기존 ui import에 이미 있음). `_start_mcp_with_spinner` 근처에:

```python
_GUARD_SPINNER_TEXT = "notif guard checking notification config"


def _run_notification_guard_v2(*, stream: TextIO | None = None) -> None:
    """알림 불변식 가드를 가시적으로 실행한다.

    interactive면 스피너 행 후 결과 행을 항상 남기고, 비대화식이면 가드에
    그대로 위임한다(silent-when-clean). 어떤 실패도 launch를 막지 않는다.
    상세 행은 스피너의 \r 갱신과 겹치지 않게 버퍼에 받아 완료 후 출력한다.
    """
    out = stream if stream is not None else sys.stdout
    if os.environ.get("SERENA_AGENT_INTERACTIVE") != "1":
        try:
            run_notification_guard(stream=out)
        except Exception:
            pass
        return
    try:
        out.write(f"  \x1b[{PURPLE}m·\x1b[0m {_GUARD_SPINNER_TEXT}")
        out.flush()

        def on_tick(frame: int) -> None:
            out.write(f"\r  {style_spinner(frame)} {_GUARD_SPINNER_TEXT}")
            out.flush()

        ticker = SpinnerTicker(on_tick=on_tick, interval=0.1)
        ticker.start()
        detail = io.StringIO()
        try:
            actions = run_notification_guard(stream=detail)
        except Exception:
            actions = None
        finally:
            ticker.stop()
        if actions is None:
            line = "\x1b[33m!\x1b[0m notif guard check failed — launch continues"
        elif not actions:
            line = f"\x1b[{PINK}m✓\x1b[0m notif guard clean"
        else:
            repaired = sum(1 for action in actions if action.kind == "repair")
            warned = len(actions) - repaired
            parts = []
            if repaired:
                parts.append(f"{repaired} repaired")
            if warned:
                parts.append(f"{warned} warning" + ("s" if warned > 1 else ""))
            line = f"\x1b[{PINK}m✓\x1b[0m notif guard " + " · ".join(parts)
        # \r 덮어쓰기: 스피너 줄보다 짧은 결과 줄이 잔상을 남기지 않게 패딩
        out.write("\r  " + line.ljust(len(_GUARD_SPINNER_TEXT) + 12) + "\n")
        if actions:
            out.write(detail.getvalue())
        out.flush()
    except Exception:
        pass
```

`_main_v2` 최상단의 기존 가드 블록(try/except로 감싼 `run_notification_guard(stream=sys.stdout)`)을 다음으로 교체:

```python
    # 알림 설정 불변식 가드 — 외부 writer가 되돌린 설정을 launch마다 수렴시킨다.
    # (spec: local_dev/docs/notification-guard-spec.md) 실패해도 launch는 계속.
    _run_notification_guard_v2()
```

- [ ] **Step 4: GREEN + 전체 회귀** — `.venv/bin/python3 -m pytest local_dev/tests/ -q` 전부 통과 (기존 `test_main_v2_runs_notification_guard_before_launch`는 비대화식 위임 경로로 그대로 통과해야 함)

- [ ] **Step 5: 커밋** — `feat(local_dev): notification guard 가시성 — interactive 스피너·상시 결과 행`

---

### Task 10: graphify 통합 프롬프트 기본값을 No로 고정

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py` (`_run_preflight_v2`)
- Modify: `local_dev/tests/test_launcher_phases.py` (동적 기본값을 단언하는 기존 테스트 갱신)
- Modify(해당 시): `local_dev/README.md`, `local_dev/docs/cli-self-install-prompt-spec.md` 등 동적 기본값을 서술한 문서

**Interfaces:**
- Consumes: `_run_preflight_v2(serena_state=...)` — 시그니처 유지 (호출부/테스트 변경 최소화)

- [ ] **Step 1: 기존 동작 파악** — `grep -n "integration_default\|serena_done\|global_done" local_dev/serena_mcp_management/serena_agent_launcher.py local_dev/tests/test_launcher_phases.py` 로 동적 기본값(`serena_done and global_done`)을 단언하는 테스트를 찾는다.

- [ ] **Step 2: RED — 테스트를 새 명세로 갱신** — 동적 기본값을 단언하던 테스트를 "항상 default No"로 바꾸고 실행해 실패 확인. 기본값 검증 테스트가 없다면 신설:

```python
def test_graphify_integration_prompt_defaults_to_no(monkeypatch):
    """통합 프롬프트는 serena/global 상태와 무관하게 기본 No — 사용자 선호."""
    # 기존 preflight 테스트들의 픽스처/monkeypatch 스타일을 따라
    # integration_status=missing + serena_state="managed" + global 설치 완료 상태를 구성하고,
    # confirm에 전달되는 default가 False임을 단언한다 (confirm을 가로채 default 기록).
```

(구체 픽스처는 파일 내 기존 `_run_preflight_v2` 테스트들의 관례를 그대로 따른다.)

- [ ] **Step 3: 구현** — `_run_preflight_v2`에서:
  - `integration_default = serena_done and global_done` → `integration_default = False` 로 교체하고 다음 주석을 남긴다: `# 사용자 선호(2026-07-23): 프로젝트에 graphify를 실수로 심지 않도록 통합 프롬프트는 항상 No 기본값.`
  - 이제 죽은 코드가 된 `serena_done = ...` 줄과 `global_done` 변수(초기화 + install 성공 시 `global_done = True`)를 제거한다. `serena_state` 파라미터는 시그니처 유지를 위해 남기되, docstring의 "dynamic default" 문단을 새 동작(항상 No)으로 갱신한다.

- [ ] **Step 4: GREEN + 전체 회귀** — `.venv/bin/python3 -m pytest local_dev/tests/ -q`

- [ ] **Step 5: 문서 갱신** — Step 1에서 찾은 동적 기본값 서술(문서·docstring)을 새 동작으로 갱신.

- [ ] **Step 6: 커밋 + 미러** — `feat(local_dev): graphify 통합 프롬프트 기본값을 No로 고정` → `make -C local_dev install-shim` → 미러 diff 무출력 확인.

### Task 11: 요구사항 확정 반영 — #2 폐기·#3 공허 충족·#5 재정의 (스펙 v5)

2026-07-24 사용자 요구사항 확정: ①입력 필요 ②메인 작업 완료 시에만, 포커스
무관 항상 알림 · 서브에이전트 완료 알림 절대 금지 · 벨 계열 설정은 사용자
관리(가드 비관여).

- Produces: 스펙 v5(요구사항 절 + 불변식 표 개정), `notification_guard.py`
  개정, 짝 테스트 개정, README 가드 절 갱신
- Consumes: Orca 알림 파이프라인 실측(발화 주체 = Orca 데몬, 메인 pane
  working→idle 전이에서만 발화 — 서브에이전트 구조적 무음 확인)

- [x] **Step 1: RED** — 새 명세 테스트로 교체: hooks.json 부재 시 무경고
  (`test_missing_hooks_json_silently_skipped`), codex `notification_condition`
  무접촉(`test_tui_notification_condition_left_untouched`), orca 토글에서
  `terminalBell` 무관여·`suppressWhenFocused=false` 요구
  (`TestOrcaToggles`), clean 픽스처에 `terminalBell=True` 포함. 6건 실패 확인.
- [x] **Step 2: GREEN** — `repair_tui_condition` 제거(불변식 #2 폐기),
  hooks.json 부재를 공허 충족으로 조용히 통과, `check_orca_notifications`를
  `enabled`/`agentTaskComplete`/`suppressWhenFocused`로 재정의(벨 제거).
  가드 37건 + 전체 1141건 통과.
- [x] **Step 3: 문서** — 스펙 v5(요구사항 절, 불변식 표, 테스트 계획 4·7·10),
  README 가드 절.
- [x] **Step 4: 커밋 + 미러** — `make -C local_dev install-shim` 후 스모크.

잔여 수동 조치(가드가 대신 못 하는 것, Orca 실행 중 파일 수정 불가):
Orca Settings › Notifications에서 `agentTaskComplete` ON,
`suppressWhenFocused` OFF. 반영 전까지 가드가 경고 행으로 안내한다.
