"""Provider-neutral photo understanding, embeddings, similarity, and event linking."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from PIL import Image

from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.db.repos.photo_intelligence import (
    PhotoIntelligenceRepo,
    PhotoIntelligenceRow,
)
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import FamilyManager
from octop.infra.users.identity import User


@dataclass(frozen=True)
class DetectedFace:
    confidence: float
    member_id: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class VisionResult:
    description: str
    objects: list[str]
    scenes: list[str]
    faces: list[DetectedFace]


@dataclass(frozen=True)
class PhotoSearchResult:
    asset_id: str
    score: float
    description: str
    location_name: str | None


@dataclass(frozen=True)
class FaceReference:
    member_id: str
    image_path: Path


class VisionProvider(Protocol):
    name: str

    def analyze(self, image_path: Path) -> VisionResult: ...


class EmbeddingProvider(Protocol):
    name: str

    def embed_image(self, image_path: Path) -> Sequence[float]: ...

    def embed_text(self, text: str) -> Sequence[float]: ...


class ReverseGeocodingProvider(Protocol):
    name: str

    def reverse(self, latitude: float, longitude: float) -> str | None: ...


class FaceRecognitionProvider(Protocol):
    name: str

    def recognize(
        self, image_path: Path, references: list[FaceReference]
    ) -> list[DetectedFace]: ...


class PhotoReranker(Protocol):
    def rerank(
        self, query: str, results: list[PhotoSearchResult]
    ) -> list[PhotoSearchResult]: ...


class PhotoIntelligenceManager:
    def __init__(
        self,
        family: FamilyManager,
        assets: FamilyAssetManager,
        context: FamilyContextRepo,
        repo: PhotoIntelligenceRepo,
    ) -> None:
        self.family = family
        self.assets = assets
        self.context = context
        self.repo = repo

    def analyze(
        self,
        family_id: str,
        asset_id: str,
        user: User,
        *,
        vision: VisionProvider | None = None,
        embedding: EmbeddingProvider | None = None,
        geocoder: ReverseGeocodingProvider | None = None,
        face_recognition: FaceRecognitionProvider | None = None,
    ) -> PhotoIntelligenceRow:
        asset = self.assets.get(family_id, asset_id, user)
        if asset.asset_type != "PHOTO":
            raise ValueError("photo intelligence requires a photo asset")
        path = self._local_path(asset.uri)
        vision_result = vision.analyze(path) if vision else VisionResult("", [], [], [])
        if face_recognition is not None:
            self.family.require_manager(family_id, user)
            recognized = face_recognition.recognize(
                path, self._face_reference_paths(family_id, user)
            )
            vision_result = VisionResult(
                vision_result.description,
                vision_result.objects,
                vision_result.scenes,
                recognized,
            )
        self._validate_faces(family_id, vision_result.faces)
        metadata = self.assets.photo_metadata(family_id, asset_id, user)
        location_name = None
        if (
            geocoder is not None
            and metadata is not None
            and metadata.latitude is not None
            and metadata.longitude is not None
        ):
            location_name = geocoder.reverse(metadata.latitude, metadata.longitude)
        vector = [float(value) for value in embedding.embed_image(path)] if embedding else None
        if vector == []:
            raise ValueError("photo embedding cannot be empty")
        row = self.repo.upsert(
            asset_id=asset_id,
            family_id=family_id,
            description=vision_result.description.strip(),
            objects_json=json.dumps(vision_result.objects, ensure_ascii=False),
            scenes_json=json.dumps(vision_result.scenes, ensure_ascii=False),
            faces_json=json.dumps(
                [face.__dict__ for face in vision_result.faces], ensure_ascii=False
            ),
            location_name=location_name,
            perceptual_hash=self._difference_hash(path),
            embedding_json=json.dumps(vector) if vector is not None else None,
            vision_provider=vision.name if vision else None,
            embedding_provider=embedding.name if embedding else None,
        )
        self._link_matching_events(family_id, asset_id, asset.captured_at, location_name)
        return row

    def set_face_reference(
        self,
        family_id: str,
        member_id: str,
        asset_id: str,
        user: User,
    ) -> None:
        self.family.require_manager(family_id, user)
        member = self.family.repo.get_member(member_id)
        if member is None or member.family_id != family_id:
            raise ValueError("family member not found")
        asset = self.assets.get(family_id, asset_id, user)
        if asset.asset_type != "PHOTO":
            raise ValueError("face reference must be a photo")
        self.repo.set_face_reference(
            asset_id=asset_id,
            member_id=member_id,
            family_id=family_id,
            created_by=user.id,
        )

    def delete_face_reference(
        self, family_id: str, asset_id: str, user: User
    ) -> bool:
        self.family.require_manager(family_id, user)
        asset = self.assets.get(family_id, asset_id, user)
        return self.repo.delete_face_reference(asset.id)

    def search(
        self,
        family_id: str,
        user: User,
        *,
        query: str,
        embedding: EmbeddingProvider,
        reranker: PhotoReranker | None = None,
        limit: int = 20,
    ) -> list[PhotoSearchResult]:
        self.family.require_access(family_id, user)
        query_vector = [float(value) for value in embedding.embed_text(query)]
        if not query_vector:
            raise ValueError("query embedding cannot be empty")
        visible_ids = {
            asset.id
            for asset in self.assets.search(
                family_id, user, asset_type="PHOTO", limit=500
            )
        }
        results: list[PhotoSearchResult] = []
        for row in self.repo.list(family_id):
            if row.embedding_json is None or row.asset_id not in visible_ids:
                continue
            vector = [float(value) for value in json.loads(row.embedding_json)]
            if len(vector) != len(query_vector):
                continue
            results.append(
                PhotoSearchResult(
                    row.asset_id,
                    self._cosine(query_vector, vector),
                    row.description,
                    row.location_name,
                )
            )
        results.sort(key=lambda item: (-item.score, item.asset_id))
        selected = results[: max(1, min(limit, 100))]
        return reranker.rerank(query, selected) if reranker else selected

    def similar(
        self,
        family_id: str,
        asset_id: str,
        user: User,
        *,
        max_distance: int = 8,
    ) -> list[tuple[str, int]]:
        self.assets.get(family_id, asset_id, user)
        target = self.repo.get(asset_id)
        if target is None or target.perceptual_hash is None:
            return []
        visible_ids = {
            asset.id
            for asset in self.assets.search(
                family_id, user, asset_type="PHOTO", limit=500
            )
        }
        matches: list[tuple[str, int]] = []
        for row in self.repo.list(family_id):
            if (
                row.asset_id == asset_id
                or row.perceptual_hash is None
                or row.asset_id not in visible_ids
            ):
                continue
            distance = (int(target.perceptual_hash, 16) ^ int(row.perceptual_hash, 16)).bit_count()
            if distance <= max_distance:
                matches.append((row.asset_id, distance))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _link_matching_events(
        self,
        family_id: str,
        asset_id: str,
        captured_at: int | None,
        location_name: str | None,
    ) -> None:
        if captured_at is None:
            return
        for event in self.context.list_events(
            family_id, start_at=captured_at, end_at=captured_at
        ):
            if event.location and location_name and (
                event.location.casefold() not in location_name.casefold()
                and location_name.casefold() not in event.location.casefold()
            ):
                continue
            self.context.link_event_asset(event.id, asset_id)

    def _validate_faces(self, family_id: str, faces: list[DetectedFace]) -> None:
        for face in faces:
            if face.member_id is None:
                continue
            member = self.family.repo.get_member(face.member_id)
            if member is None or member.family_id != family_id:
                raise ValueError("vision provider returned an invalid family member")

    def _face_reference_paths(
        self, family_id: str, user: User
    ) -> list[FaceReference]:
        references: list[FaceReference] = []
        for row in self.repo.list_face_references(family_id):
            asset = self.assets.get(family_id, row.asset_id, user)
            references.append(FaceReference(row.member_id, self._local_path(asset.uri)))
        return references

    @staticmethod
    def _local_path(uri: str) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ValueError("photo intelligence currently requires a local file asset")
        path = Path(url2pathname(unquote(parsed.path))).resolve(strict=True)
        if path.is_symlink() or not path.is_file():
            raise ValueError("photo asset path is invalid")
        return path

    @staticmethod
    def _difference_hash(path: Path) -> str:
        with Image.open(path) as image:
            pixels = cast(
                list[int],
                list(image.convert("L").resize((9, 8)).get_flattened_data()),
            )
        bits = 0
        for row in range(8):
            offset = row * 9
            for column in range(8):
                bits = (bits << 1) | int(
                    pixels[offset + column] > pixels[offset + column + 1]
                )
        return f"{bits:016x}"

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (
            left_norm * right_norm
        )
