"""HTTP surface for local photo intelligence and similarity."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.repos.photo_intelligence import PhotoIntelligenceRow
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.photo_intelligence import PhotoIntelligenceManager
from homemind.infra.family.photo_providers import (
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleVisionProvider,
    NominatimReverseGeocodingProvider,
    require_provider,
)
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()


class PhotoIntelligenceResponse(BaseModel):
    asset_id: str
    family_id: str
    description: str
    objects: list[str]
    scenes: list[str]
    faces: list[dict[str, object]]
    location_name: str | None
    perceptual_hash: str | None
    has_embedding: bool
    vision_provider: str | None
    embedding_provider: str | None
    analyzed_at: int


class SimilarPhotoResponse(BaseModel):
    asset_id: str
    hamming_distance: int


class ProviderAnalysisBody(BaseModel):
    vision_provider_id: int
    vision_model: str = Field(min_length=1, max_length=200)
    embedding_provider_id: int | None = None
    embedding_model: str | None = Field(default=None, min_length=1, max_length=200)
    reverse_geocode: bool = False
    recognize_faces: bool = False


class FaceReferenceBody(BaseModel):
    member_id: str


class SemanticSearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    embedding_provider_id: int
    embedding_model: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)


class SemanticPhotoResponse(BaseModel):
    asset_id: str
    score: float
    description: str
    location_name: str | None


Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


def _manager(server: OctopServer) -> PhotoIntelligenceManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    families = FamilyManager(services.family_repo)
    assets = FamilyAssetManager(services.family_repo, services.family_asset_repo)
    return PhotoIntelligenceManager(
        families,
        assets,
        services.family_context_repo,
        services.photo_intelligence_repo,
    )


def _response(row: PhotoIntelligenceRow) -> PhotoIntelligenceResponse:
    return PhotoIntelligenceResponse(
        asset_id=row.asset_id,
        family_id=row.family_id,
        description=row.description,
        objects=json.loads(row.objects_json),
        scenes=json.loads(row.scenes_json),
        faces=json.loads(row.faces_json),
        location_name=row.location_name,
        perceptual_hash=row.perceptual_hash,
        has_embedding=row.embedding_json is not None,
        vision_provider=row.vision_provider,
        embedding_provider=row.embedding_provider,
        analyzed_at=row.analyzed_at,
    )


@router.post(
    "/{family_id}/photos/{asset_id}/analyze-local",
    response_model=PhotoIntelligenceResponse,
    summary="Generate local photo similarity metadata",
    description="Computes a perceptual hash without sending the photo to an external provider.",
)
async def analyze_local(
    family_id: str, asset_id: str, server: Server, user: CurrentUser
) -> PhotoIntelligenceResponse:
    return _response(_manager(server).analyze(family_id, asset_id, user))


@router.post(
    "/{family_id}/photos/{asset_id}/analyze",
    response_model=PhotoIntelligenceResponse,
    summary="Analyze a photo with configured providers",
    description="Uses server-side Octop provider credentials; credentials are never returned.",
)
async def analyze_with_provider(
    family_id: str,
    asset_id: str,
    body: ProviderAnalysisBody,
    server: Server,
    user: CurrentUser,
) -> PhotoIntelligenceResponse:
    assert server.services is not None
    vision_row = require_provider(
        server.services.provider_repo.get(body.vision_provider_id)
    )
    vision = OpenAICompatibleVisionProvider(vision_row, body.vision_model)
    embedding = None
    if body.embedding_provider_id is not None:
        if body.embedding_model is None:
            raise ValueError("embedding_model is required with embedding_provider_id")
        embedding_row = require_provider(
            server.services.provider_repo.get(body.embedding_provider_id)
        )
        embedding = OpenAICompatibleEmbeddingProvider(
            embedding_row, body.embedding_model
        )
    row = await asyncio.to_thread(
        _manager(server).analyze,
        family_id,
        asset_id,
        user,
        vision=vision,
        embedding=embedding,
        geocoder=NominatimReverseGeocodingProvider()
        if body.reverse_geocode
        else None,
        face_recognition=vision if body.recognize_faces else None,
    )
    return _response(row)


@router.put(
    "/{family_id}/photos/{asset_id}/face-reference",
    status_code=204,
    summary="Register a member face reference photo",
)
async def set_face_reference(
    family_id: str,
    asset_id: str,
    body: FaceReferenceBody,
    server: Server,
    user: CurrentUser,
) -> None:
    _manager(server).set_face_reference(
        family_id, body.member_id, asset_id, user
    )


@router.delete(
    "/{family_id}/photos/{asset_id}/face-reference",
    status_code=204,
    summary="Remove a member face reference photo",
)
async def delete_face_reference(
    family_id: str, asset_id: str, server: Server, user: CurrentUser
) -> None:
    _manager(server).delete_face_reference(family_id, asset_id, user)


@router.post(
    "/{family_id}/photos/semantic-search",
    response_model=list[SemanticPhotoResponse],
    summary="Search photos by semantic similarity",
)
async def semantic_search(
    family_id: str,
    body: SemanticSearchBody,
    server: Server,
    user: CurrentUser,
) -> list[SemanticPhotoResponse]:
    assert server.services is not None
    provider = require_provider(
        server.services.provider_repo.get(body.embedding_provider_id)
    )
    embedding = OpenAICompatibleEmbeddingProvider(provider, body.embedding_model)
    rows = await asyncio.to_thread(
        _manager(server).search,
        family_id,
        user,
        query=body.query,
        embedding=embedding,
        limit=body.limit,
    )
    return [SemanticPhotoResponse.model_validate(row, from_attributes=True) for row in rows]


@router.get(
    "/{family_id}/photos/{asset_id}/similar",
    response_model=list[SimilarPhotoResponse],
    summary="Find perceptually similar indexed photos",
)
async def similar_photos(
    family_id: str,
    asset_id: str,
    server: Server,
    user: CurrentUser,
    max_distance: Annotated[int, Query(ge=0, le=64)] = 8,
) -> list[SimilarPhotoResponse]:
    return [
        SimilarPhotoResponse(asset_id=item_id, hamming_distance=distance)
        for item_id, distance in _manager(server).similar(
            family_id, asset_id, user, max_distance=max_distance
        )
    ]
