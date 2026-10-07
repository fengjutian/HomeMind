"""Versioned, non-sensitive configuration for a persistent asset job.

A worker may run hours after the request that queued the job, and it may run
in a different process after a restart. Everything it needs to know about
*what* it is doing therefore has to live in the job row, not in the caller.

Two rules shape this module:

* **No secrets, ever.** The config carries *public* provider and model
  identifiers. An API key is read live from the Octop provider repo at
  execution time, so a leaked database dump or a stray audit log never
  contains a credential. :func:`AssetJobConfig.reject_secrets` enforces this
  rather than trusting every call site to remember.
* **One version, one meaning.** ``version`` is checked on parse so a payload
  written by a newer HomeMind fails loudly instead of being silently read
  with the wrong field meanings.
"""

from __future__ import annotations

import json
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from homemind.infra.errors import HomeMindError, HomeMindErrorCode

#: The only payload shape this build understands.
CONFIG_VERSION: Final[Literal[1]] = 1

#: Substrings that must never appear in a persisted config. A provider row
#: is referenced by numeric id and a model by its public name; anything that
#: looks like a bearer token or an ``api_key`` assignment is a bug upstream.
_SECRET_MARKERS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "password",
    "token",
    "authorization",
    "bearer",
    "access_key",
)


class AssetJobConfig(BaseModel):
    """Non-sensitive job description persisted alongside the job row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = Field(
        default=CONFIG_VERSION,
        description="Payload schema version. Unknown versions are rejected, not guessed.",
    )
    vision_provider_id: int | None = Field(
        default=None,
        description="Octop provider row id for VISION jobs.",
    )
    vision_model: str | None = Field(default=None, description="Vision model name.")
    embedding_provider_id: int | None = Field(
        default=None,
        description="Octop provider row id for EMBEDDING jobs.",
    )
    embedding_model: str | None = Field(default=None, description="Embedding model name.")
    geocoder: str | None = Field(default=None, description="Reverse-geocoding provider name.")
    thumbnail_width: int | None = Field(default=None, ge=1, le=2048)
    thumbnail_height: int | None = Field(default=None, ge=1, le=2048)
    thumbnail_format: Literal["webp", "jpeg"] | None = None

    @field_validator("vision_model", "embedding_model", "geocoder", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """Treat an empty string as "not set" rather than a real model name."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def to_json(self) -> str:
        """Serialise for the ``config_json`` column."""
        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> AssetJobConfig:
        """Parse a persisted payload, mapping every failure to HomeMind errors.

        A malformed payload is a programming or migration error, not user
        input, but it still has to surface as a stable HomeMind code rather
        than a bare ``ValueError`` from a worker thread.
        """
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError) as exc:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "asset job config is not valid JSON",
            ) from exc
        if not isinstance(data, dict):
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "asset job config must be a JSON object",
            )
        reject_secrets(data)
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                f"asset job config is invalid: {exc.error_count()} field error(s)",
            ) from exc


def reject_secrets(data: dict[str, Any]) -> None:
    """Refuse a config whose keys look like credentials.

    Deliberately a key scan rather than a value scan: a model legitimately
    named ``secret-model-v2`` should not be blocked, but a ``vision_api_key``
    field has no business in this table at all.
    """
    for key in data:
        folded = str(key).casefold()
        if any(marker in folded for marker in _SECRET_MARKERS):
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                f"asset job config must not carry credentials (field {key!r})",
            )


def validate_config_for_job_type(
    job_type: str,
    config: AssetJobConfig,
) -> None:
    """Check that ``config`` carries what ``job_type`` needs to run.

    Run at creation time so the caller gets a 4xx instead of a job that fails
    hours later in a worker thread. The worker still re-checks, because a
    provider can be deleted between queueing and execution.
    """
    if job_type == "VISION" and (config.vision_provider_id is None or config.vision_model is None):
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID,
            "VISION job requires vision_provider_id and vision_model",
        )
    if job_type == "EMBEDDING" and (
        config.embedding_provider_id is None or config.embedding_model is None
    ):
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID,
            "EMBEDDING job requires embedding_provider_id and embedding_model",
        )


__all__ = [
    "CONFIG_VERSION",
    "AssetJobConfig",
    "reject_secrets",
    "validate_config_for_job_type",
]
