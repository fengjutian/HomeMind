from __future__ import annotations

import json
from pathlib import Path

import httpx

from homemind.infra.family.photo_providers import (
    NominatimReverseGeocodingProvider,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleVisionProvider,
)
from octop.infra.db.repos.providers import ProviderRow


def _provider() -> ProviderRow:
    return ProviderRow(
        id=1,
        name="test-provider",
        kind="openai",
        base_url="https://provider.example/v1",
        api_key="secret",
        extra_json=json.dumps({"headers": {"X-Test": "yes"}}),
        models_json=None,
        note=None,
        enabled=1,
        created_at=1,
        updated_at=1,
    )


def test_openai_compatible_vision_adapter(tmp_path: Path) -> None:
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"image")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer secret"
        assert request.headers["X-Test"] == "yes"
        body = json.loads(request.content)
        image_url = body["messages"][0]["content"][1]["image_url"]["url"]
        assert image_url.startswith("data:image/jpeg;base64,")
        content = json.dumps(
            {
                "description": "京都樱花",
                "objects": ["樱花"],
                "scenes": ["户外"],
                "faces": [{"confidence": 0.9, "member_id": None, "label": "person"}],
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = OpenAICompatibleVisionProvider(
            _provider(), "vision-model", client=client
        ).analyze(image)

    assert result.description == "京都樱花"
    assert result.objects == ["樱花"]
    assert result.faces[0].confidence == 0.9


def test_openai_compatible_embedding_adapter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        assert json.loads(request.content)["input"] == "京都"
        return httpx.Response(200, json={"data": [{"embedding": [0.5, 0.25]}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        vector = OpenAICompatibleEmbeddingProvider(
            _provider(), "embedding-model", client=client
        ).embed_text("京都")

    assert vector == [0.5, 0.25]


def test_nominatim_reverse_geocoder() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "nominatim.openstreetmap.org"
        assert request.url.params["lat"] == "35.0"
        assert request.headers["User-Agent"].startswith("HomeMind/")
        return httpx.Response(200, json={"display_name": "京都市，日本"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        location = NominatimReverseGeocodingProvider(client=client).reverse(35.0, 135.0)

    assert location == "京都市，日本"
