"""YouTube Shorts publishing-package generator (titles, description, tags, thumbnail brief, CTR strategy)."""

from shorts_pipeline.publisher.fallback import build_fallback_package
from shorts_pipeline.publisher.formatter import render_package_text
from shorts_pipeline.publisher.router import PublisherClient, build_publisher_client
from shorts_pipeline.publisher.schema import (
    CTRStrategy,
    PublishingPackage,
    ThumbnailBrief,
    TitleVariants,
)

__all__ = [
    "PublisherClient",
    "PublishingPackage",
    "ThumbnailBrief",
    "TitleVariants",
    "CTRStrategy",
    "build_publisher_client",
    "build_fallback_package",
    "render_package_text",
]
