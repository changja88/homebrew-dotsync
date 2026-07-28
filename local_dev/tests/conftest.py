"""local_dev 테스트 공용 픽스처."""
from __future__ import annotations

import pytest

from local_dev.serena_mcp_management import serena_agent_launcher as launcher
from local_dev.serena_mcp_management.codex_reset import CodexSessionCatalog


@pytest.fixture(autouse=True)
def _stub_real_user_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """launcher 테스트가 실사용자 Codex 상태를 읽거나 쓰지 않도록 스텁한다.

    launcher를 통째로 실행하는 테스트(main/_main_v2 호출)가 가드까지 실행하면
    실행 머신의 ~/.codex 등 실제 설정 파일을 수리해 버린다 (2026-07-24 실측:
    test_serena_launcher의 main([]) 경로가 실홈 config.toml을 수정).
    가드 자체 동작 테스트는 notification_guard 모듈을 직접 호출하므로 영향 없고,
    launcher 통합 테스트는 자체 monkeypatch로 이 스텁을 덮는다. 세션 카탈로그도
    기본은 빈 스냅샷으로 두고, 파일 시스템 동작 테스트만 tmp_path 스캐너를 쓴다.
    """
    monkeypatch.setattr(
        launcher, "run_notification_guard", lambda *, stream=None: []
    )
    monkeypatch.setattr(
        launcher,
        "scan_codex_session_catalog",
        lambda **kwargs: CodexSessionCatalog(homes=(), sessions=()),
    )
