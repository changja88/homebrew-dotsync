"""local_dev 테스트 공용 픽스처."""
from __future__ import annotations

import pytest

from local_dev.serena_mcp_management import serena_agent_launcher as launcher


@pytest.fixture(autouse=True)
def _stub_notification_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """notification guard가 실사용자 홈을 읽고 쓰지 않도록 launcher 경유 호출을 기본 스텁.

    launcher를 통째로 실행하는 테스트(main/_main_v2 호출)가 가드까지 실행하면
    실행 머신의 ~/.codex 등 실제 설정 파일을 수리해 버린다 (2026-07-24 실측:
    test_serena_launcher의 main([]) 경로가 실홈 config.toml을 수정).
    가드 자체 동작 테스트는 notification_guard 모듈을 직접 호출하므로 영향 없고,
    launcher 통합 테스트는 자체 monkeypatch로 이 스텁을 덮는다.
    """
    monkeypatch.setattr(
        launcher, "run_notification_guard", lambda *, stream=None: []
    )
