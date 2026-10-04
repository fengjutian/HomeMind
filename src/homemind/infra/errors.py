"""HomeMind-specific errors kept out of Octop's stable error catalog."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class HomeMindErrorCode(StrEnum):
    FAMILY_INVALID = "FAMILY_INVALID"
    FAMILY_CONFLICT = "FAMILY_CONFLICT"


_STATUS = {
    HomeMindErrorCode.FAMILY_INVALID: 400,
    HomeMindErrorCode.FAMILY_CONFLICT: 409,
}


@dataclass
class HomeMindError(Exception):
    code: HomeMindErrorCode
    message: str
    details: dict[str, Any] | None = None

    @property
    def status(self) -> int:
        return _STATUS[self.code]

    def to_envelope(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code.value, "message": self.message}
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}
