"""Provider contracts shared by isolated Claude and Codex adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal, Protocol

from dotsync.accounts import ManagedAccount, ProviderIdentity

if TYPE_CHECKING:
    from dotsync.usage.model import UsageSnapshot


@dataclass(frozen=True)
class LoginProgress:
    state: Literal[
        "starting", "waiting_for_browser", "waiting_for_user", "done"
    ]
    verification_url: str | None = None
    user_code: str | None = None


class ProviderError(RuntimeError):
    """A provider failure whose message is safe to return to a caller."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class UsageProvider(Protocol):
    def login(
        self,
        account: ManagedAccount,
        report: Callable[[LoginProgress], None],
    ) -> ProviderIdentity:
        raise NotImplementedError

    def refresh_usage(self, account: ManagedAccount) -> UsageSnapshot:
        raise NotImplementedError

    def logout(self, account: ManagedAccount) -> None:
        raise NotImplementedError
