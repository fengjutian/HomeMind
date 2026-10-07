"""HomeMind-specific errors kept out of Octop's stable error catalog.

The codes below are the ones the API contract names explicitly. They
are deliberately namespaced (``HOMEMIND_``) so a client can branch on
them without colliding with Octop's catalogue, and every one has a
bilingual message so a user never sees a raw exception string.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class HomeMindErrorCode(StrEnum):
    # Family scope and lifecycle
    FAMILY_INVALID = "FAMILY_INVALID"
    FAMILY_CONFLICT = "FAMILY_CONFLICT"
    FAMILY_NOT_FOUND = "HOMEMIND_FAMILY_NOT_FOUND"
    FAMILY_ACCESS_DENIED = "HOMEMIND_FAMILY_ACCESS_DENIED"
    ACTIVE_FAMILY_REQUIRED = "HOMEMIND_ACTIVE_FAMILY_REQUIRED"
    PRIVATE_SPACE_DENIED = "HOMEMIND_PRIVATE_SPACE_DENIED"

    # Transactions and approvals
    APPROVAL_REQUIRED = "HOMEMIND_APPROVAL_REQUIRED"
    TRANSACTION_CONFLICT = "HOMEMIND_TRANSACTION_CONFLICT"

    # Devices
    DEVICE_TOKEN_INVALID = "HOMEMIND_DEVICE_TOKEN_INVALID"
    DEVICE_OFFLINE = "HOMEMIND_DEVICE_OFFLINE"

    # Background jobs
    JOB_NOT_FOUND = "HOMEMIND_JOB_NOT_FOUND"

    # Privacy and external providers
    EXTERNAL_PROCESSING_DENIED = "HOMEMIND_EXTERNAL_PROCESSING_DENIED"

    # Memory review
    MEMORY_REVIEW_REQUIRED = "HOMEMIND_MEMORY_REVIEW_REQUIRED"

    # MCP
    IDENTITY_REQUIRED = "HOMEMIND_IDENTITY_REQUIRED"


_STATUS: dict[HomeMindErrorCode, int] = {
    HomeMindErrorCode.FAMILY_INVALID: 400,
    HomeMindErrorCode.FAMILY_CONFLICT: 409,
    HomeMindErrorCode.FAMILY_NOT_FOUND: 404,
    HomeMindErrorCode.FAMILY_ACCESS_DENIED: 403,
    HomeMindErrorCode.ACTIVE_FAMILY_REQUIRED: 409,
    HomeMindErrorCode.PRIVATE_SPACE_DENIED: 403,
    HomeMindErrorCode.APPROVAL_REQUIRED: 409,
    HomeMindErrorCode.TRANSACTION_CONFLICT: 409,
    HomeMindErrorCode.DEVICE_TOKEN_INVALID: 401,
    HomeMindErrorCode.DEVICE_OFFLINE: 409,
    HomeMindErrorCode.JOB_NOT_FOUND: 404,
    HomeMindErrorCode.EXTERNAL_PROCESSING_DENIED: 403,
    HomeMindErrorCode.MEMORY_REVIEW_REQUIRED: 409,
    HomeMindErrorCode.IDENTITY_REQUIRED: 401,
}


#: Default bilingual copy for each code. Callers may still pass a more
#: specific ``message``; this is what a client renders when the
#: exception carries no message of its own.
DEFAULT_MESSAGES: dict[HomeMindErrorCode, tuple[str, str]] = {
    HomeMindErrorCode.FAMILY_INVALID: ("家庭请求无效", "invalid family request"),
    HomeMindErrorCode.FAMILY_CONFLICT: ("家庭状态冲突", "family state conflict"),
    HomeMindErrorCode.FAMILY_NOT_FOUND: ("未找到该家庭", "family not found"),
    HomeMindErrorCode.FAMILY_ACCESS_DENIED: ("无权访问该家庭", "family access denied"),
    HomeMindErrorCode.ACTIVE_FAMILY_REQUIRED: (
        "请先选择一个家庭",
        "select an active family first",
    ),
    HomeMindErrorCode.PRIVATE_SPACE_DENIED: (
        "无权访问该私密空间",
        "private space access denied",
    ),
    HomeMindErrorCode.APPROVAL_REQUIRED: ("该操作需要审批", "this action needs approval"),
    HomeMindErrorCode.TRANSACTION_CONFLICT: (
        "事务状态已变化,请刷新后重试",
        "transaction state changed; refresh and retry",
    ),
    HomeMindErrorCode.DEVICE_TOKEN_INVALID: ("设备凭证无效", "device credential invalid"),
    HomeMindErrorCode.DEVICE_OFFLINE: ("设备当前离线", "device is offline"),
    HomeMindErrorCode.JOB_NOT_FOUND: ("未找到该后台任务", "asset job not found"),
    HomeMindErrorCode.EXTERNAL_PROCESSING_DENIED: (
        "家庭隐私设置不允许发送到外部服务",
        "family privacy settings forbid external processing",
    ),
    HomeMindErrorCode.MEMORY_REVIEW_REQUIRED: (
        "该记忆需要人工确认",
        "this memory needs human review",
    ),
    HomeMindErrorCode.IDENTITY_REQUIRED: ("无法确认调用者身份", "caller identity required"),
}


def default_message(code: HomeMindErrorCode, *, locale: str = "zh") -> str:
    """Return the code's message for ``locale`` (``zh`` / ``en``)."""

    zh, en = DEFAULT_MESSAGES.get(code, (code.value, code.value))
    return zh if locale == "zh" else en


@dataclass
class HomeMindError(Exception):
    code: HomeMindErrorCode
    message: str | None = None
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.message:
            self.message = default_message(self.code)

    @property
    def status(self) -> int:
        return _STATUS[self.code]

    def to_envelope(self, *, locale: str = "zh") -> dict[str, Any]:
        message = self.message or default_message(self.code, locale=locale)
        error: dict[str, Any] = {
            "code": self.code.value,
            "message": message,
        }
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}


__all__ = [
    "DEFAULT_MESSAGES",
    "HomeMindError",
    "HomeMindErrorCode",
    "default_message",
]
