from __future__ import annotations

import abc
import asyncio
import datetime
from email.utils import parsedate_to_datetime
import html
from pathlib import Path
import re
import typing
from authlib.integrations.base_client.errors import OAuthError  # pyrefly: ignore[untyped-import]
from authlib.integrations.httpx_client import AsyncOAuth2Client  # pyrefly: ignore[untyped-import]
import httpx
import pydantic
import sqlmodel
import twikit
import twikit.media
from typing import Optional as Opt
from utils.datetime_ import get_timestamp
from .model import TwitterBatch
from . import Extension


class TwitterAPIResult(sqlmodel.SQLModel):
  next_page: Opt[str] = None
  previous_page: Opt[str] = None
  batch: TwitterBatch = pydantic.Field(default_factory=TwitterBatch)
  primary_post_ids: tuple[str, ...] = ()


def _instant(value: str | datetime.datetime) -> str:
  if isinstance(value, str):
    try:
      value = datetime.datetime.fromisoformat(value)
    except ValueError:
      value = parsedate_to_datetime(value)
  if value.tzinfo is None:
    raise ValueError("Twitter provider returned a timestamp without a timezone")
  return (
    value.astimezone(datetime.timezone.utc)
    .isoformat(timespec="seconds")
    .replace("+00:00", "Z")
  )


def _observed_at() -> str:
  return (
    datetime.datetime.now(datetime.timezone.utc)
    .isoformat(timespec="milliseconds")
    .replace("+00:00", "Z")
  )


def _media_id(media: dict) -> str | None:
  if media.get("id"):
    return str(media["id"])
  match = re.fullmatch(r"(?:3|7|13)_([1-9][0-9]*)", media.get("media_key", ""))
  return match[1] if match else None


def _provider_counts(raw: dict, names: dict[str, str], observed_at: str) -> dict:
  values = {
    name: raw[source] for source, name in names.items() if raw.get(source) is not None
  }
  return (
    {"counts": values, "countsObservedAt": {name: observed_at for name in values}}
    if values
    else {}
  )


def _official_archive(
  raw_tweets: list[dict], includes: dict, observed_at: str
) -> TwitterBatch:
  media = []
  media_ids = {}
  for raw in includes.get("media", ()):
    media_id = _media_id(raw)
    if media_id is None:
      continue
    media_ids[raw["media_key"]] = media_id
    kind = {"photo": "photo", "video": "video", "animated_gif": "gif"}.get(raw["type"])
    url = raw.get("url") if kind == "photo" else raw.get("preview_image_url")
    if kind is None or not url:
      continue
    item = {"id": media_id, "type": kind, "url": url}
    item.update(
      {
        target: raw[source]
        for source, target in {
          "width": "width",
          "height": "height",
          "alt_text": "alt",
        }.items()
        if raw.get(source) is not None
      }
    )
    if raw.get("variants"):
      item["video"] = {
        "variants": [
          {
            "url": variant["url"],
            "type": variant["content_type"],
            **({"bitrate": variant["bit_rate"]} if "bit_rate" in variant else {}),
          }
          for variant in raw["variants"]
        ]
      }
      if raw.get("duration_ms") is not None:
        item["video"]["durationMs"] = raw["duration_ms"]
    media.append(item)
  users = []
  for raw in includes.get("users", ()):
    user = {"id": raw["id"], "handle": raw["username"], "name": raw["name"]}
    user.update(
      {
        target: raw[source]
        for source, target in {
          "profile_image_url": "avatarUrl",
          "location": "location",
          "url": "website",
          "protected": "protected",
        }.items()
        if raw.get(source) is not None
      }
    )
    if "description" in raw:
      user["bio"] = {"text": html.unescape(raw["description"])}
    if "created_at" in raw:
      user["createdAt"] = _instant(raw["created_at"])
    user.update(
      _provider_counts(
        raw.get("public_metrics", {}),
        {
          "followers_count": "followers",
          "following_count": "following",
          "tweet_count": "posts",
        },
        observed_at,
      )
    )
    users.append(user)
  posts = []
  for raw in [*raw_tweets, *includes.get("tweets", ())]:
    post = {
      "id": raw["id"],
      "authorId": raw["author_id"],
      "createdAt": _instant(raw["created_at"]),
    }
    references = {
      reference["type"]: reference["id"] for reference in raw.get("referenced_tweets", ())
    }
    if "retweeted" in references:
      post["repostOf"] = references["retweeted"]
    else:
      post["text"] = {
        "text": html.unescape(raw.get("note_tweet", {}).get("text", raw["text"]))
      }
      if "replied_to" in references:
        post["replyTo"] = {"postId": references["replied_to"]}
      if "quoted" in references:
        post["quote"] = {"postId": references["quoted"]}
      if raw.get("conversation_id") and raw["conversation_id"] != raw["id"]:
        post["conversationId"] = raw["conversation_id"]
      post.update(
        {
          target: raw[source]
          for source, target in {"lang": "lang", "possibly_sensitive": "sensitive"}.items()
          if raw.get(source) is not None
        }
      )
      attached = [
        media_ids[key]
        for key in raw.get("attachments", {}).get("media_keys", ())
        if key in media_ids
      ]
      if attached:
        post["mediaIds"] = attached
      post.update(
        _provider_counts(
          raw.get("public_metrics", {}),
          {
            "reply_count": "replies",
            "retweet_count": "reposts",
            "quote_count": "quotes",
            "like_count": "likes",
            "bookmark_count": "bookmarks",
            "impression_count": "views",
          },
          observed_at,
        )
      )
    posts.append(post)
  return TwitterBatch.model_validate({"users": users, "posts": posts, "media": media})


def _twikit_archive(
  tweets: typing.Iterable[twikit.Tweet], observed_at: str
) -> TwitterBatch:
  posts: dict[str, dict] = {}
  users: dict[str, dict] = {}
  media: dict[str, dict] = {}

  def collect(tweet: twikit.Tweet) -> None:
    if tweet.id in posts:
      return
    user = tweet.user
    post: dict[str, typing.Any] = {
      "id": tweet.id,
      "authorId": user.id,
      "createdAt": _instant(tweet.created_at_datetime),
    }
    posts[tweet.id] = post
    users[user.id] = {"id": user.id, "handle": user.screen_name, "name": user.name}
    if tweet.retweeted_tweet is not None:
      post["repostOf"] = tweet.retweeted_tweet.id
      collect(tweet.retweeted_tweet)
      return
    post["text"] = {"text": html.unescape(tweet.full_text)}
    if tweet.in_reply_to:
      post["replyTo"] = {"postId": tweet.in_reply_to}
    conversation_id = tweet._legacy.get("conversation_id_str")
    if conversation_id and conversation_id != tweet.id:
      post["conversationId"] = conversation_id
    if tweet.quote is not None:
      post["quote"] = {"postId": tweet.quote.id}
      collect(tweet.quote)
    if tweet.lang:
      post["lang"] = tweet.lang
    post.update(
      _provider_counts(
        tweet._legacy,
        {
          "reply_count": "replies",
          "retweet_count": "reposts",
          "quote_count": "quotes",
          "favorite_count": "likes",
          "bookmark_count": "bookmarks",
        },
        observed_at,
      )
    )
    attached = []
    for item in tweet.media:
      kind = {"photo": "photo", "video": "video", "animated_gif": "gif"}.get(item.type)
      if kind is None or not item.id or not item.media_url:
        continue
      value: dict[str, typing.Any] = {"id": item.id, "type": kind, "url": item.media_url}
      original = item._data.get("original_info") or {}
      value.update(
        {
          name: original[name]
          for name in ("width", "height")
          if original.get(name) is not None
        }
      )
      if item._data.get("ext_alt_text") is not None:
        value["alt"] = item._data["ext_alt_text"]
      info = item._data.get("video_info") or {}
      if info.get("variants"):
        value["video"] = {
          "variants": [
            {
              "url": variant["url"],
              "type": variant["content_type"],
              **({"bitrate": variant["bitrate"]} if "bitrate" in variant else {}),
            }
            for variant in info["variants"]
          ]
        }
        if info.get("duration_millis") is not None:
          value["video"]["durationMs"] = info["duration_millis"]
      media[item.id] = value
      attached.append(item.id)
    if attached:
      post["mediaIds"] = attached

  for tweet in tweets:
    collect(tweet)
  return TwitterBatch.model_validate(
    {
      "users": list(users.values()),
      "posts": list(posts.values()),
      "media": list(media.values()),
    }
  )


class TwitterAPI(abc.ABC):
  """Twitter API client.

  Should be singleton.
  """

  SINGLETON: Opt["TwitterAPI"] = None

  @classmethod
  async def close_singleton(cls) -> None:
    """Close and forget the singleton so a later enable gets a fresh client."""
    singleton = cls.SINGLETON
    if singleton is None:
      return
    await singleton.close()
    cls.SINGLETON = None

  @classmethod
  async def new(
    cls,
    *,
    expected_authorization_id: str | None = None,
  ) -> "TwitterAPI":
    """Create an instance of the Twitter API client.

    Use `config.backend` to determine which backend to use.
    """
    config = Extension.get_config()
    backend_type = config.backend
    if backend_type == "official":
      return await OfficialAPI.from_extension(
        expected_authorization_id=expected_authorization_id
      )
    if cls.SINGLETON is not None:
      return cls.SINGLETON
    if backend_type == "twikit":
      cls.SINGLETON = TwikitAPI(
        email=config.email,
        username=config.username,
        password=config.password,
        totp_secret=config.totp_secret,
        language=config.api_language,
        proxy=config.proxy,
      )
      return cls.SINGLETON
    raise ValueError(f"Unknown backend type: {backend_type}")

  async def close(self): ...

  @property
  @abc.abstractmethod
  def user_handle(self) -> str: ...

  @property
  @abc.abstractmethod
  def user_id(self) -> str: ...

  @abc.abstractmethod
  async def get_bookmarks(
    self, max_results: int = 20, page: Opt[str] = None
  ) -> TwitterAPIResult: ...

  @abc.abstractmethod
  async def get_tweets(
    self, query: str, max_results: int = 20, page: Opt[str] = None
  ) -> TwitterAPIResult: ...

  @abc.abstractmethod
  async def get_replies(
    self, *conversation_ids: str, from_: Opt[str] = None, max_results: int = 20
  ) -> TwitterAPIResult: ...


class OfficialAPI(TwitterAPI):
  """Official Twitter API client."""

  request_records: dict[str, tuple[int, datetime.datetime]] = {}
  """How many requests made to each endpoint for last 15 mins.
    """
  rate_limit_reset: dict[str, int] = {}
  """When the rate limit for each endpoint will reset.
    """

  def __init__(  # noqa: PLR0913
    self,
    client_id: str,
    client_secret: str,
    *,
    token: dict[str, typing.Any],
    user_id: str,
    user_handle: str,
    authorization_id: str,
  ):
    self.__client_id = client_id
    self.__client_secret = client_secret
    self.__token = token
    self.__user_id = user_id
    self.__user_handle = user_handle
    self.__authorization_id = authorization_id

  @classmethod
  async def from_extension(
    cls,
    *,
    expected_authorization_id: str | None = None,
  ) -> OfficialAPI:
    from .setup_flow import TwitterExtensionState, TwitterSetupConflict, _fingerprint

    config = Extension.get_config()
    state = typing.cast(TwitterExtensionState, await Extension.get_state_async())
    account = state.account
    if (
      account is None
      or account.reconnect_required
      or account.app_fingerprint != _fingerprint(config)
    ):
      raise TwitterSetupConflict("Twitter account is not connected")
    if (
      expected_authorization_id is not None
      and account.authorization_id != expected_authorization_id
    ):
      raise TwitterSetupConflict("Twitter authorization changed before collection")
    return cls(
      config.client_id,
      config.client_secret,
      token=dict(account.token),
      user_id=account.user_id,
      user_handle=account.handle,
      authorization_id=account.authorization_id,
    )

  @property
  def user_handle(self) -> str:
    if self.__user_handle is None:
      raise ValueError("User handle is not set. Please authorize first.")
    return self.__user_handle

  @property
  def user_id(self) -> str:
    if self.__user_id is None:
      raise ValueError("User ID is not set. Please authorize first.")
    return self.__user_id

  async def _request(  # noqa: PLR0913
    self,
    method: str,
    endpoint: str,
    path_params: Opt[dict] = None,
    query: Opt[dict] = None,
    body: Opt[dict] = None,
    retried: int = 0,
  ) -> dict:
    """Make a request to the Twitter API.

    :param method: HTTP method (GET, POST, etc.)
    :param endpoint: API endpoint (e.g., "/users/me")
        Must start with a slash ("/").
        Use "{variable}" to mark path parameters.
    :param path_params: List of path parameters to format into the endpoint
    :param query: Query parameters as a dictionary
    :param body: Request body as a dictionary (for POST/PUT requests)

    - Auto authorization header
    - Auto refresh access token
    - Rate limit
      - Failed requests also count
    - TODO Monthly limit
    - Error handling
    - Resopnse body parsing
    """
    from .setup_flow import TwitterExtensionState, TwitterSetupConflict, _fingerprint

    latest = typing.cast(TwitterExtensionState, await Extension.get_state_async())
    latest_config = Extension.get_config()
    if (
      latest.account is None
      or latest.account.reconnect_required
      or latest.account.authorization_id != self.__authorization_id
      or latest.account.app_fingerprint != _fingerprint(latest_config)
    ):
      raise TwitterSetupConflict("Twitter authorization changed before provider access")
    self.__token = dict(latest.account.token)
    request_token = dict(self.__token)
    endpoint_with_params = endpoint.format(**path_params) if path_params else endpoint

    rate_limit_reset_at = self.rate_limit_reset.get(endpoint)
    if rate_limit_reset_at is not None:
      await asyncio.sleep(max(0, (rate_limit_reset_at - get_timestamp()) + 5))
      self.rate_limit_reset.pop(endpoint, None)
      self.request_records.pop(endpoint, None)

    # request_record = cls.request_records.get(endpoint)
    # if request_record:
    #     last_request_count, last_15m_start_at = request_record
    #     if last_15m_start_at + datetime.timedelta(minutes=15) < datetime.datetime.now():
    #         del cls.request_records[endpoint]
    #     else:
    #         limit = Extension.config.api_rate_limit", {}).get(endpoint, 1)
    #         if last_request_count >= limit:
    #             # wait until the rate limit resets
    #             await asyncio.sleep((
    #                 last_15m_start_at + datetime.timedelta(minutes=15)
    #                 - datetime.datetime.now()
    #             ).total_seconds() + 5) # add a buffer of 5 seconds
    #             # reset
    #             cls.request_records[endpoint] = (0, datetime.datetime.now())
    # else:
    #     last_request_count, last_15m_start_at = 0, datetime.datetime.now()

    async def update_token(token: dict[str, typing.Any], **_: typing.Any) -> None:
      from .setup_flow import TwitterExtensionState, TwitterSetupConflict

      def update(model: pydantic.BaseModel) -> pydantic.BaseModel:
        state = typing.cast(TwitterExtensionState, model)
        if (
          state.account is None or state.account.authorization_id != self.__authorization_id
        ):
          raise TwitterSetupConflict("Twitter authorization changed during refresh")
        if dict(state.account.token) != request_token:
          raise TwitterSetupConflict("Twitter token changed during refresh")
        state.account.token = dict(token)
        return state

      await Extension.mutate_state_async(update)
      self.__token = dict(token)

    async def require_reconnect() -> None:
      from .setup_flow import TwitterExtensionState

      def update(model: pydantic.BaseModel) -> pydantic.BaseModel:
        state = typing.cast(TwitterExtensionState, model)
        if (
          state.account is not None
          and state.account.authorization_id == self.__authorization_id
        ):
          state.account.reconnect_required = True
        return state

      await Extension.mutate_state_async(update)

    client = AsyncOAuth2Client(
      client_id=self.__client_id,
      client_secret=self.__client_secret,
      token=self.__token,
      token_endpoint="https://api.x.com/2/oauth2/token",
      token_endpoint_auth_method="client_secret_basic",
      update_token=update_token,
      timeout=10,
    )
    try:
      response = await client.request(
        method,
        f"https://api.x.com/2{endpoint_with_params}",
        params=query,
        json=body,
      )
      if response.status_code == 429:
        x_rate_limit_reset = response.headers.get("x-rate-limit-reset")
        if x_rate_limit_reset:
          self.rate_limit_reset[endpoint] = int(x_rate_limit_reset)

          # # Rate limit exceeded but not expected, set request count to max
          # # and request again when rate limit reset
          # cls.request_records[endpoint] = (
          #     Extension.config.api_rate_limit", {}).get(endpoint, 1),
          #     datetime.datetime.now()
          # )

        if retried < 3:
          return await self._request(
            method, endpoint, path_params, query, body, retried + 1
          )
        raise RuntimeError("Twitter API rate limit exceeded after retries")
      response.raise_for_status()
      payload = response.json()
      if not isinstance(payload, dict):
        raise RuntimeError("Twitter API returned an invalid response")
      return payload
    except OAuthError:
      await require_reconnect()
      raise RuntimeError("Twitter authorization requires reconnection") from None
    except httpx.HTTPStatusError as error:
      if error.response.status_code == 401:
        await require_reconnect()
        raise RuntimeError("Twitter authorization requires reconnection") from None
      raise RuntimeError("Twitter API request failed") from error
    except httpx.HTTPError as error:
      raise RuntimeError("Twitter API request failed") from error
    finally:
      await typing.cast(httpx.AsyncClient, client).aclose()

  async def close(self) -> None:
    """Official clients are operation-scoped and hold no open transport."""

  async def get_user(self) -> tuple[str, str]:
    """Get the user info the token represents and store to state.

    :returns: (user ID, user handle)
    """
    user_info = await self._request("GET", "/users/me")
    user_id = user_info.get("data", {}).get("id")
    if not user_id:
      raise ValueError("Failed to get user ID from Twitter API.")
    user_handle = user_info.get("data", {}).get("username")
    if not user_handle:
      raise ValueError("Failed to get user handle from Twitter API.")

    # Extension.state["user_id"] = user_id
    # Extension.state["user_handle"] = user_handle
    self.__user_id = user_id
    self.__user_handle = user_handle
    return user_id, user_handle

  def _resolve_tweets(
    self, raw_tweets: list[dict], includes: dict[str, list[dict]]
  ) -> TwitterBatch:
    return _official_archive(raw_tweets, includes, _observed_at())

  async def get_bookmarks(
    self, max_results: int = 20, page: str | None = None
  ) -> TwitterAPIResult:
    """Get user bookmarks.

    Rate limit:
    - Free: 1 req per 15 minutes
    - Basic: 5 req per 15 minutes
    - Pro: 50 req per 15 minutes
    """
    bookmarks_query = {
      "max_results": max_results,
      "tweet.fields": (
        "author_id,created_at,attachments,entities,lang,conversation_id,"
        "referenced_tweets,public_metrics,possibly_sensitive,note_tweet"
      ),
      "media.fields": (
        "alt_text,media_key,url,type,preview_image_url,width,height,duration_ms,variants"
      ),
      "user.fields": (
        "id,name,username,description,profile_image_url,url,"
        "protected,location,public_metrics,created_at"
      ),
      "expansions": (
        "author_id,attachments.media_keys,referenced_tweets.id,"
        "referenced_tweets.id.author_id"
      ),
    }
    if page:
      bookmarks_query["pagination_token"] = page
    res = await self._request(
      "GET",
      "/users/{id}/bookmarks",
      path_params={"id": self.__user_id},
      query=bookmarks_query,
    )
    batch = self._resolve_tweets(res.get("data", []), res.get("includes", {}))
    return TwitterAPIResult(
      next_page=res.get("meta", {}).get("next_token"),
      previous_page=res.get("meta", {}).get("previous_token"),
      batch=batch,
      primary_post_ids=tuple(post["id"] for post in res.get("data", ())),
    )

  async def get_tweets(
    self, query: str, max_results: int = 20, page: str | None = None
  ) -> TwitterAPIResult:
    res = await self._request(
      "GET",
      "/tweets/search/recent",
      query={
        "query": query,
        "max_results": max_results,
        "tweet.fields": (
          "author_id,created_at,attachments,entities,lang,conversation_id,"
          "referenced_tweets,public_metrics,possibly_sensitive,note_tweet"
        ),
        "media.fields": (
          "alt_text,media_key,url,type,preview_image_url,width,height,duration_ms,variants"
        ),
        "user.fields": (
          "id,name,username,description,profile_image_url,url,"
          "protected,location,public_metrics,created_at"
        ),
        "expansions": (
          "author_id,attachments.media_keys,referenced_tweets.id,"
          "referenced_tweets.id.author_id"
        ),
      },
    )
    batch = self._resolve_tweets(res.get("data", []), res.get("includes", {}))
    return TwitterAPIResult(
      next_page=res.get("meta", {}).get("next_token"),
      previous_page=res.get("meta", {}).get("previous_token"),
      batch=batch,
      primary_post_ids=tuple(post["id"] for post in res.get("data", ())),
    )

  async def get_replies(
    self, *conversation_ids: str, from_: str | None = None, max_results: int = 20
  ) -> TwitterAPIResult:
    # TODO add query lenth limit auto adapt
    conversations = " OR ".join(
      f"conversation_id:{conversation_id}" for conversation_id in conversation_ids
    )
    res = await self.get_tweets(
      query=f"from:{from_} ({conversations})",
      max_results=max_results,
    )
    return res


class TwikitAPI(TwitterAPI):
  """Twikit API client."""

  DATA_DIRECTORY = Path("data/extensions/twitter")
  COOKIES_FILE = DATA_DIRECTORY / "twikit_cookies.json"

  @classmethod
  def _prepare_data_directory(cls) -> None:
    cls.DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)

  def __init__(  # noqa: PLR0913
    self,
    email: str,
    username: str,
    password: str,
    totp_secret: Opt[str] = None,
    language: Opt[str] = None,
    proxy: Opt[str] = None,
  ):
    """
    :param language: The language code to use in API requests.
        Keep the same with your daily use of Twitter.
    """
    if not language:
      language = "en-US"
    self._client = twikit.Client(language=language, proxy=proxy)
    self._email = email
    self._username = username
    self._password = password
    self._totp_secret = totp_secret

  async def close(self):
    self._prepare_data_directory()
    self._client.save_cookies(str(self.COOKIES_FILE))

  @property
  def user_handle(self) -> str:
    return self._username

  @property
  def user_id(self) -> str:
    if not self._client._user_id:
      raise ValueError("User ID is not set. Please login first.")
    return self._client._user_id

  async def _login(self):
    self._prepare_data_directory()
    await self._client.login(
      auth_info_1=self._email,
      auth_info_2=self._username,
      password=self._password,
      totp_secret=self._totp_secret,
      cookies_file=str(self.COOKIES_FILE),
    )

  @staticmethod
  def _resolve_tweets(result: typing.Iterable[twikit.Tweet]) -> TwitterBatch:
    return _twikit_archive(result, _observed_at())

  async def get_bookmarks(
    self, max_results: int = 20, page: str | None = None
  ) -> TwitterAPIResult:
    if not self._client._user_id:
      await self._login()
    res = await self._client.get_bookmarks(count=max_results, cursor=page)
    return TwitterAPIResult(
      next_page=res.next_cursor,
      previous_page=res.previous_cursor,
      batch=self._resolve_tweets(res),
      primary_post_ids=tuple(tweet.id for tweet in res),
    )

  async def get_tweets(
    self, query: str, max_results: int = 20, page: str | None = None, tried: int = 0
  ) -> TwitterAPIResult:
    if not self._client._user_id:
      await self._login()
    try:
      res = await self._client.search_tweet(
        query=query, product="Latest", count=max_results, cursor=page
      )
    except twikit.errors.NotFound:
      if tried < 3:
        await asyncio.sleep(3)
        return await self.get_tweets(query, max_results, page, tried + 1)
      else:
        return TwitterAPIResult()
    else:
      return TwitterAPIResult(
        next_page=res.next_cursor,
        previous_page=res.previous_cursor,
        batch=self._resolve_tweets(res),
        primary_post_ids=tuple(tweet.id for tweet in res),
      )

  async def _get_a_reply_of(
    self, from_: str, replies: twikit.utils.Result[twikit.Tweet]
  ) -> twikit.Tweet | None:
    if len(replies) == 0:
      return None
    for reply in replies:
      if reply.user.screen_name == from_:
        return reply
    else:
      await asyncio.sleep(5)  # avoid rate limit
      replies = await replies.next()
      return await self._get_a_reply_of(from_, replies)

  async def get_replies(
    self, *conversation_ids: str, from_: str | None = None, max_results: int = 20
  ) -> TwitterAPIResult:
    res_tweets: list[twikit.Tweet] = []
    for cid in conversation_ids:
      await asyncio.sleep(3)  # avoid rate limit
      try:
        tweet = await self._client.get_tweet_by_id(cid)
      except twikit.errors.TweetNotAvailable:
        # TODO log warning
        continue
      else:
        replies = tweet.replies
        if not replies:
          continue
        else:
          if from_:
            the_reply = await self._get_a_reply_of(from_, replies)
            if the_reply:
              res_tweets.append(the_reply)
          else:
            res_tweets.extend(replies)

    return TwitterAPIResult(
      batch=self._resolve_tweets(res_tweets),
      primary_post_ids=tuple(tweet.id for tweet in res_tweets),
    )
