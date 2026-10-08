"""HTTP surface for smart-home providers and entities (Stage 6).

Read paths are open to any family member; anything that changes the
house — adding a provider, changing an allow-list, probing — is
manager-only. There is deliberately **no** "execute a command" route:
a smart-home write happens through a Family Transaction, which is what
gives it approval, an audit row, and a verify step. A direct HTTP
endpoint would bypass all three.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.smart_home_factory import build_adapter
from homemind.infra.family.smart_home_manager import FamilySmartHomeManager
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


class ProviderCreateBody(BaseModel):
    kind: str = Field(description="HOME_ASSISTANT | MQTT")
    name: str = Field(min_length=1, max_length=120)
    base_url: str | None = Field(
        default=None, description="Home Assistant base URL. Never carries a token."
    )
    secret_ref: str | None = Field(
        default=None,
        description=(
            "Name of a credential in the secret store. The secret itself "
            "is never accepted or returned by this API."
        ),
    )
    topic_allowlist: list[str] = Field(
        default_factory=list,
        description="MQTT topics this family permits. Wildcards are refused.",
    )


class TopicAllowlistBody(BaseModel):
    topics: list[str] = Field(
        description="Replacement allow-list. ``#`` is refused; ``+`` is allowed."
    )


class ProviderResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    family_id: str
    kind: str
    name: str
    base_url: str | None
    secret_ref: str | None
    topic_allowlist: list[str]
    enabled: bool
    last_seen_at: int | None
    last_error: str | None
    created_at: int
    updated_at: int


class EntityResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    family_id: str
    provider_id: str
    external_entity_id: str
    domain: str
    name: str
    capabilities: list[str]
    state: dict[str, Any]
    last_state_at: int | None
    last_changed_at: int | None


class CommandDescription(BaseModel):
    """One entry of an entity's command catalogue."""

    name: str
    domain: str
    description: str
    risk: str
    payload_schema: dict[str, Any]


class CommandPreviewBody(BaseModel):
    command_name: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandPreviewResponse(BaseModel):
    entity_external_id: str
    command_name: str
    domain: str
    risk: str
    summary: str
    payload: dict[str, Any]


class ProbeResponse(BaseModel):
    ok: bool


def _manager(server: OctopServer) -> FamilySmartHomeManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    # Adapter factory wired to the real secret store: a provider row plus a
    # resolved credential becomes a live adapter, so sync/command stop
    # short-circuiting to `adapterUnavailable`.
    secret_store = _SecretStoreAdapter(server.services.secret_repo)
    return FamilySmartHomeManager(
        FamilyManager(services.family_repo),
        services.smart_home_repo,
        adapter_factory=lambda provider: build_adapter(provider, secret_store),
    )


class _SecretStoreAdapter:
    """Adapts octop's ``SecretRepo`` to the narrow store the factory needs."""

    __slots__ = ("_repo",)

    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def get(self, key: str) -> bytes | None:
        raw: bytes | None = self._repo.get(key)
        return raw


@router.post(
    "/{family_id}/smart-home/providers",
    response_model=ProviderResponse,
    status_code=201,
    summary="Register a Home Assistant or MQTT connection",
    description=(
        "Manager-only. The request carries a *reference* to a stored "
        "credential; the secret itself never passes through this API, "
        "so it cannot appear in a request log, an error message, or "
        "this response."
    ),
)
async def create_provider(
    family_id: str, body: ProviderCreateBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).create_provider(family_id, user, **body.model_dump())


@router.get(
    "/{family_id}/smart-home/providers",
    response_model=list[ProviderResponse],
    summary="List the family's smart-home connections",
)
async def list_providers(family_id: str, server: Server, user: CurrentUser) -> object:
    return _manager(server).list_providers(family_id, user)


@router.delete(
    "/{family_id}/smart-home/providers/{provider_id}",
    status_code=204,
    summary="Remove a connection and its mapped entities",
    description="Manager-only. The stored credential is left untouched.",
)
async def delete_provider(
    family_id: str, provider_id: str, server: Server, user: CurrentUser
) -> Response:
    _manager(server).delete_provider(family_id, provider_id, user)
    return Response(status_code=204)


@router.put(
    "/{family_id}/smart-home/providers/{provider_id}/topics",
    response_model=ProviderResponse,
    summary="Replace the MQTT topic allow-list",
    description=(
        "Manager-only. A ``#`` wildcard is refused: a wildcard publish "
        "would make the allow-list meaningless."
    ),
)
async def put_topics(
    family_id: str,
    provider_id: str,
    body: TopicAllowlistBody,
    server: Server,
    user: CurrentUser,
) -> object:
    return _manager(server).set_topic_allowlist(family_id, provider_id, user, body.topics)


@router.post(
    "/{family_id}/smart-home/providers/{provider_id}/probe",
    response_model=ProbeResponse,
    summary="Test a connection",
    description=(
        "Manager-only. The recorded failure is the exception type only; "
        "a connection error can carry a host name, which does not belong "
        "in a status field."
    ),
)
async def probe_provider(
    family_id: str, provider_id: str, server: Server, user: CurrentUser
) -> object:
    return ProbeResponse(ok=_manager(server).probe(family_id, provider_id, user))


@router.post(
    "/{family_id}/smart-home/providers/{provider_id}/sync",
    response_model=list[EntityResponse],
    summary="Refresh the entity mapping from the provider",
    description="Manager-only. An outage leaves the previous mapping intact.",
)
async def sync_entities(
    family_id: str, provider_id: str, server: Server, user: CurrentUser
) -> object:
    return _manager(server).sync_entities(family_id, provider_id, user)


@router.get(
    "/{family_id}/smart-home/entities",
    response_model=list[EntityResponse],
    summary="List the family's mapped smart entities",
)
async def list_entities(
    family_id: str,
    server: Server,
    user: CurrentUser,
    domain: Annotated[str | None, Query(description="Filter by domain, e.g. 'light'.")] = None,
) -> object:
    return _manager(server).list_entities(family_id, user, domain=domain)


@router.get(
    "/{family_id}/smart-home/entities/{entity_id}/commands",
    response_model=list[CommandDescription],
    summary="List the commands an entity accepts",
    description=(
        "The complete set of commands an agent may name. There is no "
        "free-form service call: a command outside this list cannot be "
        "expressed, which is how locks and cameras stay unreachable."
    ),
)
async def list_commands(
    family_id: str, entity_id: str, server: Server, user: CurrentUser
) -> object:
    return _manager(server).commands_for(family_id, entity_id, user)


@router.post(
    "/{family_id}/smart-home/entities/{entity_id}/preview",
    response_model=CommandPreviewResponse,
    summary="Describe a command without executing it",
    description=(
        "What an approval screen renders. Execution is deliberately not "
        "offered here: a smart-home write goes through a Family "
        "Transaction so it is approved, audited, and verified."
    ),
)
async def preview_command(
    family_id: str,
    entity_id: str,
    body: CommandPreviewBody,
    server: Server,
    user: CurrentUser,
) -> object:
    return _manager(server).preview_command(
        family_id,
        entity_id,
        user,
        command_name=body.command_name,
        payload=body.payload,
    )


__all__ = [
    "router",
    "CommandDescription",
    "CommandPreviewBody",
    "CommandPreviewResponse",
    "EntityResponse",
    "ProbeResponse",
    "ProviderCreateBody",
    "ProviderResponse",
    "TopicAllowlistBody",
]
