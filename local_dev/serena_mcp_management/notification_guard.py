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
