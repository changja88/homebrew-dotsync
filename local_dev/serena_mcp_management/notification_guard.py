"""launch 시 알림 설정 불변식을 점검·자동 수리하는 가드.

설계 명세: local_dev/docs/notification-guard-spec.md
알림 정책("입력 필요/완료 시에만")을 되돌리는 외부 writer(ChatGPT 앱의
notify 재주입, codex 신뢰 재기록, orca 재미러링)에 맞서 관리되는 launch마다
설정을 수렴시킨다. silent-when-clean, best-effort — launch를 절대 막지 않는다.
"""
from __future__ import annotations

import json
import re
import tomllib
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


_NOTIFY_LINE = re.compile(r'"?notify"?\s*=')
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
        replaced = False
        for i in range(start + 1, end):
            if re.match(r'"?enabled"?\s*=', lines[i].strip()):
                lines[i] = "enabled = false"
                replaced = True
                break
        if not replaced:
            lines[end:end] = [_GUARD_COMMENT, "enabled = false"]
    return "\n".join(lines) + "\n", needs
