"""Durable RSS 2.0 source type wrapper."""

from app.business.source import SourceBase

from .schema import FeedCollectJobConfig, FeedSourceConfig
from .source import FeedSourceMixin


RssSourceConfig = FeedSourceConfig


class Source(
  FeedSourceMixin,
  SourceBase[FeedSourceConfig],
  config_cls=FeedSourceConfig,
  collect_config_cls=FeedCollectJobConfig,
):
  """Collect one configured RSS 2.0 feed through the shared feed service."""

  source_type = "extensions.rss.rss.Source"

  expected_family = "rss"


__all__ = ["RssSourceConfig", "Source"]
