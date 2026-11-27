"""Opt-in replaceable live protocol smoke above the bounded HTTP boundary."""

import asyncio
import os

import pytest

from extensions.rss.adapter import FeedParserContext, parse_feed_snapshot
from extensions.rss.http import HTTPFetchOptions, fetch_http_bytes
from extensions.rss.schema import FeedFamily


async def _fetch_and_parse(url: str, family: FeedFamily):
  response = await fetch_http_bytes(
    url,
    options=HTTPFetchOptions(
      timeout_seconds=30,
      max_response_bytes=8 * 1024 * 1024,
      user_agent="InKCre RSS live acceptance/0.1",
    ),
  )
  return await asyncio.to_thread(
    parse_feed_snapshot,
    response.body,
    FeedParserContext(
      expected_family=family,
      source_instance_id=-1,
      configured_url=url,
      effective_url=response.effective_url,
      response_headers=response.headers,
    ),
  )


@pytest.mark.integration
@pytest.mark.acceptance
@pytest.mark.parametrize(
  ("family", "environment_name"),
  (
    ("rss", "INKCRE_LIVE_RSS_URL"),
    ("atom", "INKCRE_LIVE_ATOM_URL"),
  ),
)
def test_replaceable_live_feed_is_usable(
  family: FeedFamily,
  environment_name: str,
):
  url = os.getenv(environment_name)
  if not url:
    pytest.skip(f"set {environment_name} to select a replaceable live endpoint")
  snapshot = asyncio.run(_fetch_and_parse(url, family))
  assert snapshot.feed.family == family
  assert snapshot.feed.configured_url == url
  assert snapshot.feed.title or snapshot.feed.source_native_id or snapshot.feed.home_url
