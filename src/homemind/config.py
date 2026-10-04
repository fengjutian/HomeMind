"""HomeMind configuration layered on the Octop runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass

from octop.config import OctopConfig


@dataclass(frozen=True)
class HomeMindConfig:
    """Product configuration without duplicating Octop's runtime settings."""

    octop: OctopConfig
    api_prefix: str = "/api/homemind/families"
