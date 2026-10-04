"""Adapters from Octop providers to HomeMind photo intelligence protocols."""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from homemind.infra.family.photo_intelligence import (
    DetectedFace,
    FaceReference,
    VisionResult,
)
from octop.infra.agents.providers.opencode_session import ensure_opencode_session_header
from octop.infra.agents.providers.probe import provider_headers
from octop.infra.db.repos.providers import ProviderRow


class OpenAICompatibleVisionProvider:
    def __init__(
        self,
        provider: ProviderRow,
        model: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.name = f"{provider.name}/{model}"
        self._model = model
        self._base, self._headers = _connection(provider)
        self._client = client

    def analyze(self, image_path: Path) -> VisionResult:
        image_url = _data_url(image_path)
        payload = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Describe this family photo. Return JSON only with keys "
                                "description, objects, scenes, faces. objects and scenes are "
                                "string arrays. faces is an array of {confidence, member_id, "
                                "label}; use null member_id unless identity is known."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
        }
        data = self._post("chat/completions", payload)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("vision provider response has no choices")
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or "") for item in content if isinstance(item, dict)
            )
        parsed = _json_object(str(content or ""))
        return VisionResult(
            description=str(parsed.get("description") or ""),
            objects=_strings(parsed.get("objects")),
            scenes=_strings(parsed.get("scenes")),
            faces=[
                DetectedFace(
                    confidence=float(item.get("confidence") or 0),
                    member_id=str(item["member_id"]) if item.get("member_id") else None,
                    label=str(item["label"]) if item.get("label") else None,
                )
                for item in parsed.get("faces", [])
                if isinstance(item, dict)
            ],
        )

    def recognize(
        self, image_path: Path, references: list[FaceReference]
    ) -> list[DetectedFace]:
        if not references:
            return []
        if len(references) > 20:
            raise ValueError("face recognition supports at most 20 reference photos")
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    "The first image is the target. Remaining images are labeled family "
                    "member references. Return JSON only as {\"faces\": [{\"confidence\": "
                    "0.0, \"member_id\": null, \"label\": null}]}. Use only an exact "
                    "provided member_id when identity is visually supported."
                ),
            },
            {"type": "image_url", "image_url": {"url": _data_url(image_path)}},
        ]
        for reference in references:
            content.extend(
                [
                    {"type": "text", "text": f"reference member_id={reference.member_id}"},
                    {
                        "type": "image_url",
                        "image_url": {"url": _data_url(reference.image_path)},
                    },
                ]
            )
        data = self._post(
            "chat/completions",
            {
                "model": self._model,
                "temperature": 0,
                "messages": [{"role": "user", "content": content}],
            },
        )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("face recognition response has no choices")
        parsed = _json_object(str(choices[0].get("message", {}).get("content") or ""))
        return [
            DetectedFace(
                confidence=float(item.get("confidence") or 0),
                member_id=str(item["member_id"]) if item.get("member_id") else None,
                label=str(item["label"]) if item.get("label") else None,
            )
            for item in parsed.get("faces", [])
            if isinstance(item, dict)
        ]

    def _post(self, endpoint: str, payload: dict[str, object]) -> dict[str, Any]:
        if self._client is not None:
            response = self._client.post(
                f"{self._base}/{endpoint}", headers=self._headers, json=payload
            )
        else:
            with httpx.Client(timeout=90.0) as client:
                response = client.post(
                    f"{self._base}/{endpoint}", headers=self._headers, json=payload
                )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("provider response is not a JSON object")
        return data


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        provider: ProviderRow,
        model: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.name = f"{provider.name}/{model}"
        self._model = model
        self._base, self._headers = _connection(provider)
        self._client = client

    def embed_image(self, image_path: Path) -> Sequence[float]:
        return self._embed(_data_url(image_path))

    def embed_text(self, text: str) -> Sequence[float]:
        return self._embed(text)

    def _embed(self, value: str) -> list[float]:
        payload = {"model": self._model, "input": value}
        if self._client is not None:
            response = self._client.post(
                f"{self._base}/embeddings", headers=self._headers, json=payload
            )
        else:
            with httpx.Client(timeout=90.0) as client:
                response = client.post(
                    f"{self._base}/embeddings", headers=self._headers, json=payload
                )
        response.raise_for_status()
        data = response.json().get("data")
        if not isinstance(data, list) or not data:
            raise RuntimeError("embedding provider response has no data")
        vector = data[0].get("embedding")
        if not isinstance(vector, list) or not vector:
            raise RuntimeError("embedding provider response has no vector")
        return [float(item) for item in vector]


class NominatimReverseGeocodingProvider:
    name = "openstreetmap-nominatim"

    def __init__(
        self, *, language: str = "zh-CN", client: httpx.Client | None = None
    ) -> None:
        self._language = language
        self._client = client

    def reverse(self, latitude: float, longitude: float) -> str | None:
        params = {
            "format": "jsonv2",
            "lat": str(latitude),
            "lon": str(longitude),
            "accept-language": self._language,
            "zoom": "16",
        }
        headers = {"User-Agent": "HomeMind/0.1 reverse-geocoder"}
        if self._client is not None:
            response = self._client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params=params,
                headers=headers,
            )
        else:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    "https://nominatim.openstreetmap.org/reverse",
                    params=params,
                    headers=headers,
                )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("reverse geocoder response is not a JSON object")
        display_name = data.get("display_name")
        return str(display_name) if display_name else None


def require_provider(provider: ProviderRow | None) -> ProviderRow:
    if (
        provider is None
        or not provider.enabled
        or not provider.base_url
        or not provider.api_key
    ):
        raise ValueError("photo intelligence provider is not ready")
    return provider


def _connection(provider: ProviderRow) -> tuple[str, dict[str, str]]:
    provider = require_provider(provider)
    headers = provider_headers(provider)
    headers["Authorization"] = f"Bearer {provider.api_key}"
    headers = ensure_opencode_session_header(
        provider.name, headers, base_url=provider.base_url
    )
    return str(provider.base_url).rstrip("/"), headers


def _data_url(path: Path) -> str:
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("photo exceeds provider upload limit")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```")
        text = text.removesuffix("```").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("vision provider returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("vision provider returned a non-object JSON value")
    return value


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []
