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


@pytest.fixture(autouse=True)
def _stub_graphify_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """launcher 테스트는 graphify 상태를 env로 주입한다.

    실제 probe는 파일시스템과 git을, setup guard는 launcher runtime root를
    읽으므로 launcher 수준 seam을 no-op으로 고정한다. probe/guard 모듈 자체는
    건드리지 않아 그 단위 테스트는 실제 구현을 본다. 가드/재검증 전용 테스트는
    필요한 seam을 실제 구현으로 되돌린다.
    """
    monkeypatch.setattr(
        launcher, "_populate_graphify_preflight_environ", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        launcher,
        "_graphify_component_state",
        lambda component, root, client: ("installed", "fp"),
    )
    monkeypatch.setattr(
        launcher, "_graphify_setup_suppressed", lambda component, root, client: False
    )
