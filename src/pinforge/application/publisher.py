from __future__ import annotations

from typing import Protocol

from pinforge.domain.models import PinDraft, PublishResult


class Publisher(Protocol):
    def publish(self, draft: PinDraft) -> PublishResult: ...
