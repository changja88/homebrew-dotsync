"""local_dev 테스트 공용 픽스처."""
from __future__ import annotations

import pytest

from local_dev.serena_mcp_management import serena_agent_launcher as launcher
from local_dev.serena_mcp_management.codex_reset import CodexSessionCatalog


@pytest.fixture(autouse=True)
def _stub_real_user_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """launcher 테스트가 실사용자 Codex 상태를 읽지 않도록 스텁한다."""
    monkeypatch.setattr(
        launcher,
        "scan_codex_session_catalog",
        lambda **kwargs: CodexSessionCatalog(homes=(), sessions=()),
    )
