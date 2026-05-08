"""Model pinning — production runs MUST declare which model produced an extractor.

Borrowed conceptually from Zyte API's model pinning. Without a pin, the hybrid
extractor still works in dev mode but emits warnings; with a pin it refuses to
fall back to a non-pinned model so behavior cannot silently drift.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class PinnedModel:
    provider: str
    model_id: str
    prompt_template_hash: str

    @property
    def identifier(self) -> str:
        return f"{self.provider}:{self.model_id}@{self.prompt_template_hash[:8]}"

    @staticmethod
    def make(provider: str, model_id: str, prompt_template: str) -> PinnedModel:
        h = hashlib.sha256(prompt_template.encode("utf-8")).hexdigest()
        return PinnedModel(provider=provider, model_id=model_id, prompt_template_hash=h)


class PinViolation(RuntimeError):
    """Raised when extraction would use a model that doesn't match the pin."""


def require_pin(pin: PinnedModel | None, *, strict: bool) -> None:
    if pin is None and strict:
        raise PinViolation(
            "strict pinning enabled but no pin provided — "
            "build a PinnedModel.make(provider, model_id, prompt_template) "
            "and pass pin= to the extractor."
        )


def matches(pin: PinnedModel | None, provider: str, model_id: str, prompt_hash: str) -> bool:
    if pin is None:
        return True
    return (
        pin.provider == provider
        and pin.model_id == model_id
        and pin.prompt_template_hash == prompt_hash
    )
