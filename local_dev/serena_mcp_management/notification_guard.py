"""launch 시 알림 설정 불변식을 점검·자동 수리하는 가드.

설계 명세: local_dev/docs/notification-guard-spec.md
알림 정책(입력 필요·메인 작업 완료 시에만, 포커스 무관 — 서브에이전트 완료
알림 금지, 벨은 사용자 관리)을 되돌리는 외부 writer(ChatGPT 앱의 notify
재주입, codex 신뢰 재기록, orca 재미러링)에 맞서 관리되는 launch마다
설정을 수렴시킨다. silent-when-clean, best-effort — launch를 절대 막지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from local_dev.serena_mcp_management.ui import render_inline_row


@dataclass(frozen=True)
class GuardAction:
    kind: str  # "repair" | "warn"
    message: str
    path: Path | None = None


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
        before = path.stat()
        original = path.read_text()
        new_text, meta = transform(original)
        if new_text == original:
            return RepairOutcome("unchanged")
        tmp = path.with_name(f".{path.name}.notifguard.tmp")
        tmp.write_text(new_text)
        os.chmod(tmp, before.st_mode & 0o7777)
        try:
            validate(tmp.read_text())
        except Exception:
            tmp.unlink(missing_ok=True)
            return RepairOutcome("invalid")
        after = path.stat()
        if (after.st_mtime_ns, after.st_size) != (before.st_mtime_ns, before.st_size):
            tmp.unlink(missing_ok=True)
            continue  # 동시 수정 감지 — 처음부터 1회 재시도
        try:
            os.replace(tmp, path)
        except OSError:
            # 임시 파일 잔류 금지 — 실패해도 흔적을 남기지 않는다. 예외는 호출부(오케스트레이터)가 warn으로 강등한다.
            tmp.unlink(missing_ok=True)
            raise
        return RepairOutcome("repaired", meta)
    return RepairOutcome("conflicted")


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


_NOTIFY_LINE = re.compile(r"['\"]?notify['\"]?\s*=")


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
    """#5: 마스터·완료 알림 ON + 포커스 중 억제 OFF. terminalBell은 사용자 관리 — 불관여."""
    notif = json.loads(path.read_text()).get("settings", {}).get("notifications", {})
    problems: list[str] = []
    if notif.get("enabled") is not True:
        problems.append("알림 비활성")
    if notif.get("agentTaskComplete") is not True:
        problems.append("Agent 작업 완료 알림 꺼짐")
    if notif.get("suppressWhenFocused") is not False:
        problems.append("포커스 중 알림 억제 켜짐(suppressWhenFocused)")
    if not problems:
        return []
    return [GuardAction(
        "warn",
        f"orca 알림 토글 어긋남({', '.join(problems)}) — Orca 설정 › Notifications에서 조정 필요",
        path,
    )]


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

    if target.hooks_json is None or not target.hooks_json.is_file():
        # 훅 파일이 없으면 가짜 "needs input" 알림의 원인도 없다 — 공허 충족.
        # (로그인 잔재 홈처럼 config.toml만 있는 홈에서 경고를 반복하지 않는다.)
        return actions
    try:
        keys = permission_request_state_keys(target.hooks_json)
    except Exception as exc:
        actions.append(GuardAction(
            "warn",
            f"hooks.json 파싱 불가 — permission_request 점검 건너뜀 ({_short(target.hooks_json)}): {exc}",
            target.hooks_json,
        ))
        return actions
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
