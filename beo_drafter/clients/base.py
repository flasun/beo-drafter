from __future__ import annotations

from typing import Any, Protocol


class ModelClient(Protocol):
    name: str

    def draft_beo(self, brief: dict[str, Any]) -> dict[str, Any]:
        """Return a BEO dict for the given brief. Must not include secrets."""
        ...
