"""Process-level settings exposed to authenticated clients."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server, require_permission
from octop.config import OctopConfig
from octop.infra.agents.security import tool_execution_may_pause
from octop.infra.auth.captcha import current_env, load_view, save_settings
from octop.infra.users.identity import User
from octop.infra.utils.lan_address import detect_lan_ipv4

router = APIRouter()


class TimezoneSettingsResponse(BaseModel):
    timezone: str = Field(description="IANA timezone from config ``default_timezone``.")


class UploadSettingsResponse(BaseModel):
    max_upload_mb: int = Field(description="Max upload size in MiB from config ``max_upload_mb``.")
    max_upload_bytes: int = Field(description="Max upload size in bytes.")


class HitlSettingsResponse(BaseModel):
    enabled: bool = Field(description="Whether global tool human-approval (HITL) is enabled.")
    tool_guard_require_approval: bool = Field(
        description="Whether command guard pauses risky shell calls for approval."
    )
    show_approval_ui: bool = Field(
        description="Whether composer, slash help, and CLI should show tool-approval surfaces."
    )


class ServerAddressResponse(BaseModel):
    """LAN-reachable base URLs of this HomeMind server.

    Unauthenticated by design: the login page renders the QR code before any
    session exists, so a phone on the same LAN can scan it and sign in.
    """

    url: str = Field(
        description="Preferred LAN URL, e.g. ``http://192.168.1.23:8088``.",
    )
    port: int = Field(description="Port the server is listening on.")
    host: str = Field(description="Detected LAN IPv4 address of this host.")
    is_lan: bool = Field(
        description="False when detection fell back to loopback (no routable IPv4).",
    )


@router.get(
    "/settings/server-address",
    summary="LAN address for login QR code",
    response_model=ServerAddressResponse,
)
async def get_server_address(server: Any = Depends(get_server)) -> ServerAddressResponse:
    """Return this host's LAN address so the login page can render a scannable QR code.

    No authentication required — the login screen itself is unauthenticated.
    Address detection performs no network I/O and blocks for microseconds, but
    it still runs in an executor to keep the event loop free per the project's
    "no blocking I/O in async functions" rule.
    """
    cfg: OctopConfig = server.services.config
    loop = asyncio.get_running_loop()
    host = await loop.run_in_executor(None, detect_lan_ipv4)
    return ServerAddressResponse(
        url=f"http://{host}:{cfg.port}",
        port=cfg.port,
        host=host,
        is_lan=host not in ("127.0.0.1", "::1"),
    )


@router.get(
    "/settings/timezone",
    summary="Server default timezone",
    response_model=TimezoneSettingsResponse,
)
async def get_timezone_settings(
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> TimezoneSettingsResponse:
    """Return the process default timezone used for display and scheduling."""
    return TimezoneSettingsResponse(timezone=server.services.config.default_timezone)


@router.get(
    "/settings/upload",
    summary="Server upload size limit",
    response_model=UploadSettingsResponse,
)
async def get_upload_settings(
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> UploadSettingsResponse:
    """Return the process upload size limit used for chat attachments and knowledge documents."""
    _ = user
    cfg: OctopConfig = server.services.config
    return UploadSettingsResponse(
        max_upload_mb=cfg.max_upload_mb,
        max_upload_bytes=cfg.max_upload_bytes,
    )


@router.get(
    "/settings/hitl",
    summary="Tool human-approval switch",
    response_model=HitlSettingsResponse,
)
async def get_hitl_settings(
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> HitlSettingsResponse:
    """Return HITL and command-guard flags that drive approval UI."""
    _ = user
    assert server.app_runtime is not None
    policy = server.app_runtime.agent_registry.security.load()
    guard_require = bool(policy.tool_guard.enabled and policy.tool_guard.mode == "require_approval")
    return HitlSettingsResponse(
        enabled=bool(policy.hitl.enabled),
        tool_guard_require_approval=guard_require,
        show_approval_ui=tool_execution_may_pause(policy),
    )


class MobileCapabilitiesResponse(BaseModel):
    enabled: bool = Field(description="Whether Remote Android is enabled on this host.")
    backend: str = Field(description="Host backend: physical, redroid, emulator, or none.")


class CapabilitiesResponse(BaseModel):
    mobile: MobileCapabilitiesResponse


@router.get(
    "/settings/capabilities",
    summary="Host feature capabilities",
    response_model=CapabilitiesResponse,
)
async def get_capabilities(
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> CapabilitiesResponse:
    """Return install-time host capabilities (always available when authenticated)."""
    _ = user
    cfg: OctopConfig = server.services.config
    cap = cfg.capabilities.mobile
    return CapabilitiesResponse(
        mobile=MobileCapabilitiesResponse(enabled=cap.enabled, backend=cap.backend)
    )


class CaptchaPairView(BaseModel):
    site_key: str
    has_secret: bool
    cam_secret_id: str = ""
    has_cam_secret: bool = False


class CaptchaSettingsResponse(BaseModel):
    active: str
    available: list[str]
    providers: dict[str, CaptchaPairView]
    source: Literal["settings", "env"]
    v3_min_score: float


class CaptchaPairBody(BaseModel):
    site_key: str | None = None
    secret: str | None = None
    cam_secret_id: str | None = None
    cam_secret: str | None = None


class CaptchaSettingsPut(BaseModel):
    active: str | None = None
    providers: dict[str, CaptchaPairBody | None] | None = None


@router.get(
    "/settings/captcha",
    summary="Login captcha settings",
    response_model=CaptchaSettingsResponse,
)
async def get_captcha_settings(
    _admin: User = Depends(require_permission("captcha")),
    server: Any = Depends(get_server),
) -> CaptchaSettingsResponse:
    """Return the stored captcha catalog. Secrets are never included."""
    view = load_view(
        server.services.settings_repo,
        server.services.secret_repo,
        current_env(),
    )
    return CaptchaSettingsResponse.model_validate(view)


@router.put(
    "/settings/captcha",
    summary="Update login captcha settings",
    response_model=CaptchaSettingsResponse,
)
async def put_captcha_settings(
    body: CaptchaSettingsPut,
    admin: User = Depends(require_permission("captcha")),
    server: Any = Depends(get_server),
) -> CaptchaSettingsResponse:
    """Merge captcha settings. Empty secret keeps the stored ciphertext."""
    view = save_settings(
        server.services.settings_repo,
        server.services.secret_repo,
        current_env(),
        body.model_dump(exclude_unset=True),
    )
    server.services.audit_repo.write(
        actor=admin.username,
        action="captcha.settings.update",
        target=str(view["active"]),
        payload=f"source={view['source']}",
    )
    return CaptchaSettingsResponse.model_validate(view)
