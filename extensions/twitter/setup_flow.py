"""Whole-Extension setup workflow owned by the Twitter Extension."""

from __future__ import annotations

import base64
import datetime
import hashlib
import html
import secrets
import typing
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from authlib.integrations.base_client.errors import OAuthError  # pyrefly: ignore[untyped-import]
from authlib.integrations.httpx_client import (  # pyrefly: ignore[untyped-import]
  AsyncOAuth2Client,
  OAuth2Client,
)
import fastapi
from fastapi.responses import HTMLResponse
import httpx
import pydantic
from app.business.peer import PeerHTTPInbound, PeerManager
from app.middleware import require_peer_jwt
from app.schemas.peer import (
  CorePeerConfig,
  PeerCapabilityAdvertisement,
  PeerInboundInterface,
)


if typing.TYPE_CHECKING:
  from . import TwitterExtensionConfig


TWITTER_SETUP_STATUS_CAPABILITY = "inkcre.twitter.setup.status.v1"
TWITTER_X_MONKEY_SESSION_CAPABILITY = "inkcre.twitter.x-monkey.session.v1"
TWITTER_OAUTH_APP_CONFIGURE_CAPABILITY = "inkcre.twitter.oauth-app.configure.v1"
TWITTER_OAUTH_BEGIN_CAPABILITY = "inkcre.twitter.oauth.begin.v1"
TWITTER_OAUTH_TRANSACTION_READ_CAPABILITY = "inkcre.twitter.oauth.transaction.read.v1"
TWITTER_OAUTH_DISCONNECT_CAPABILITY = "inkcre.twitter.oauth.disconnect.v1"
PEER_BROWSER_FORM_PROTOCOL = "core.peer.protocol.browser-form.v1"


@dataclass(frozen=True)
class TwitterSessionBrowserFormInbound:
  """Advertise the extension-owned session receiver to a browser Peer Host."""

  capability: str = TWITTER_X_MONKEY_SESSION_CAPABILITY
  path: str = "/twitter/x-monkey/session"
  credential_field: str = "token"

  def advertise(self, config: CorePeerConfig) -> PeerCapabilityAdvertisement | None:
    if (
      not config.http_public_base_url
      or urlsplit(config.http_public_base_url).scheme != "https"
    ):
      return None
    return PeerCapabilityAdvertisement(
      id=self.capability,
      inbound=PeerInboundInterface(
        protocol=PEER_BROWSER_FORM_PROTOCOL,
        parameters={
          "method": "POST",
          "path": self.path,
          "credential_field": self.credential_field,
        },
      ),
    )


TWITTER_SETUP_INBOUNDS = (
  PeerHTTPInbound(TWITTER_SETUP_STATUS_CAPABILITY, "GET", "/twitter/setup"),
  TwitterSessionBrowserFormInbound(),
  PeerHTTPInbound(
    TWITTER_OAUTH_APP_CONFIGURE_CAPABILITY, "PUT", "/twitter/setup/oauth-app"
  ),
  PeerHTTPInbound(
    TWITTER_OAUTH_BEGIN_CAPABILITY, "POST", "/twitter/setup/oauth-transactions"
  ),
  PeerHTTPInbound(
    TWITTER_OAUTH_TRANSACTION_READ_CAPABILITY,
    "POST",
    "/twitter/setup/oauth-transaction",
  ),
  PeerHTTPInbound(TWITTER_OAUTH_DISCONNECT_CAPABILITY, "DELETE", "/twitter/setup/account"),
)
AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"  # noqa: S105
CURRENT_USER_URL = "https://api.x.com/2/users/me"
SCOPES = ("tweet.read", "users.read", "bookmark.read", "offline.access")
TRANSACTION_LIFETIME = datetime.timedelta(minutes=10)
TERMINAL_RETENTION = datetime.timedelta(minutes=10)
MAX_TRANSACTIONS = 8


def _now() -> datetime.datetime:
  return datetime.datetime.now(datetime.UTC)


class TwitterAccount(pydantic.BaseModel):
  token: dict[str, typing.Any]
  user_id: str
  handle: str
  scopes: tuple[str, ...] = ()
  app_fingerprint: str
  authorization_id: str
  connected_at: datetime.datetime
  reconnect_required: bool = False


class OAuthTransaction(pydantic.BaseModel):
  status: typing.Literal["pending", "exchanging", "succeeded", "failed", "expired"]
  provider_state: str | None = None
  pkce_verifier: str | None = None
  app_fingerprint: str
  redirect_uri: str
  created_at: datetime.datetime
  expires_at: datetime.datetime
  closed_at: datetime.datetime | None = None
  error: str | None = None


class TwitterExtensionState(pydantic.BaseModel):
  account: TwitterAccount | None = None
  oauth_transactions: dict[str, OAuthTransaction] = pydantic.Field(default_factory=dict)


class OAuthTransactionView(pydantic.BaseModel):
  id: str
  status: str
  authorize_url: str | None = None
  expires_at: datetime.datetime
  error: str | None = None


class TwitterSetupStatus(pydantic.BaseModel):
  callback_url: str
  x_monkey_base_url: str | None = None
  connected: bool
  user_id: str | None = None
  handle: str | None = None
  scopes: tuple[str, ...] = ()
  reconnect_required: bool = False


class SaveOAuthAppRequest(pydantic.BaseModel):
  client_id: str = pydantic.Field(min_length=1, max_length=256)
  client_secret: str = pydantic.Field(min_length=1, max_length=1024)
  confirm_account_reset: bool = False


class OAuthTransactionRequest(pydantic.BaseModel):
  transaction_id: str


class TwitterSetupError(RuntimeError): ...


class TwitterSetupConflict(TwitterSetupError): ...


class TwitterProviderError(TwitterSetupError): ...


def _extension():
  from . import Extension

  return Extension


async def _state() -> TwitterExtensionState:
  return typing.cast(TwitterExtensionState, await _extension().get_state_async())


def _config() -> TwitterExtensionConfig:
  return _extension().get_config()


def _fingerprint(config: TwitterExtensionConfig) -> str:
  material = (
    f"inkcre-twitter-oauth-app\0{config.client_id}\0{config.client_secret}".encode()
  )
  return hashlib.sha256(material).hexdigest()


async def _redirect_uri() -> str:
  try:
    base = (await PeerManager.get_current_config_async()).http_public_base_url
  except (RuntimeError, ValueError) as error:
    raise TwitterSetupError("Core Peer public HTTP URL is unavailable") from error
  if base is None:
    raise TwitterSetupError("Core Peer public HTTP URL is not configured")
  return f"{base.rstrip('/')}/twitter/auth/callback"


def _pkce_pair() -> tuple[str, str]:
  verifier = secrets.token_urlsafe(48)
  digest = hashlib.sha256(verifier.encode()).digest()
  challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
  return verifier, challenge


def _bounded_provider_error(error: BaseException | str) -> str:
  value = str(error).replace("\r", " ").replace("\n", " ").strip()
  return (value or "Twitter authorization failed")[:240]


def _transaction_view(
  transaction_id: str,
  transaction: OAuthTransaction,
  *,
  authorize_url: str | None = None,
) -> OAuthTransactionView:
  return OAuthTransactionView(
    id=transaction_id,
    status=transaction.status,
    authorize_url=authorize_url,
    expires_at=transaction.expires_at,
    error=transaction.error,
  )


def _terminal(
  transaction: OAuthTransaction,
  status: typing.Literal["succeeded", "failed", "expired"],
  *,
  error: str | None = None,
) -> OAuthTransaction:
  return transaction.model_copy(
    update={
      "status": status,
      "provider_state": None,
      "pkce_verifier": None,
      "closed_at": _now(),
      "error": error,
    }
  )


def _invalidate_mismatched_oauth_state(
  config: TwitterExtensionConfig,
  state: TwitterExtensionState,
  *,
  reason: str,
) -> tuple[TwitterExtensionState, bool]:
  """Invalidate authorization material that belongs to another OAuth App."""
  fingerprint = _fingerprint(config)
  changed = state.account is not None and state.account.app_fingerprint != fingerprint
  if changed:
    state.account = None
  transactions = {
    key: _terminal(value, "expired", error=reason)
    if value.status in {"pending", "exchanging"} and value.app_fingerprint != fingerprint
    else value
    for key, value in state.oauth_transactions.items()
  }
  if transactions != state.oauth_transactions:
    state.oauth_transactions = transactions
    changed = True
  return state, changed


async def _reconcile_oauth_state() -> TwitterExtensionState:
  """Reconcile direct deployment config writes before setup becomes reachable."""
  config = _config()
  state = await _state()
  _, changed = _invalidate_mismatched_oauth_state(
    config,
    state.model_copy(deep=True),
    reason="OAuth App changed",
  )
  if not changed:
    return state

  def reconcile(config_model, state_model):
    from . import TwitterExtensionConfig

    current_config = typing.cast(TwitterExtensionConfig, config_model)
    current_state = typing.cast(TwitterExtensionState, state_model)
    reconciled, _ = _invalidate_mismatched_oauth_state(
      current_config,
      current_state,
      reason="OAuth App changed",
    )
    return current_config, reconciled

  _, reconciled = await _extension().mutate_config_and_state_async(reconcile)
  return typing.cast(TwitterExtensionState, reconciled)


async def get_setup_status() -> TwitterSetupStatus:
  from .archive_session import x_monkey_base_url

  config = _config()
  state = await _reconcile_oauth_state()
  configured = bool(config.client_id and config.client_secret)
  account = state.account
  connected = (
    account is not None
    and configured
    and not account.reconnect_required
    and account.app_fingerprint == _fingerprint(config)
  )
  return TwitterSetupStatus(
    callback_url=await _redirect_uri(),
    x_monkey_base_url=await x_monkey_base_url(),
    connected=connected,
    user_id=account.user_id if connected and account is not None else None,
    handle=account.handle if connected and account is not None else None,
    scopes=account.scopes if connected and account is not None else (),
    reconnect_required=bool(account and account.reconnect_required),
  )


async def save_oauth_app(body: SaveOAuthAppRequest) -> TwitterSetupStatus:
  current_config = _config()
  current_state = await _state()
  next_config = current_config.model_copy(
    update={
      "backend": "official",
      "client_id": body.client_id.strip(),
      "client_secret": body.client_secret,
    }
  )
  fingerprint_changed = _fingerprint(next_config) != _fingerprint(current_config)
  has_live_setup = current_state.account is not None or any(
    value.status in {"pending", "exchanging"}
    for value in current_state.oauth_transactions.values()
  )
  if fingerprint_changed and has_live_setup and not body.confirm_account_reset:
    raise TwitterSetupConflict(
      "Replacing the OAuth App requires confirmation because it disconnects the account"
    )

  def update(config_model, state_model):
    from . import TwitterExtensionConfig

    config = typing.cast(TwitterExtensionConfig, config_model)
    state = typing.cast(TwitterExtensionState, state_model)
    next_config = config.model_copy(
      update={
        "backend": "official",
        "client_id": body.client_id.strip(),
        "client_secret": body.client_secret,
      }
    )
    changed = _fingerprint(next_config) != _fingerprint(config)
    live_setup = state.account is not None or any(
      value.status in {"pending", "exchanging"}
      for value in state.oauth_transactions.values()
    )
    if changed and live_setup and not body.confirm_account_reset:
      raise TwitterSetupConflict(
        "Replacing the OAuth App requires confirmation because it disconnects the account"
      )
    if changed:
      state, _ = _invalidate_mismatched_oauth_state(
        next_config,
        state,
        reason="OAuth App changed",
      )
    return next_config, state

  await _extension().mutate_config_and_state_async(update)
  return await get_setup_status()


async def begin_oauth() -> OAuthTransactionView:
  config = _config()
  if config.backend != "official" or not config.client_id or not config.client_secret:
    raise TwitterSetupConflict("Configure the Twitter OAuth App first")
  transaction_id = str(uuid.uuid4())
  provider_state = secrets.token_urlsafe(32)
  verifier, challenge = _pkce_pair()
  redirect_uri = await _redirect_uri()
  now = _now()
  transaction = OAuthTransaction(
    status="pending",
    provider_state=provider_state,
    pkce_verifier=verifier,
    app_fingerprint=_fingerprint(config),
    redirect_uri=redirect_uri,
    created_at=now,
    expires_at=now + TRANSACTION_LIFETIME,
  )

  def update(model: pydantic.BaseModel) -> pydantic.BaseModel:
    state = typing.cast(TwitterExtensionState, model)
    current_time = _now()
    transactions = {
      key: _terminal(value, "expired", error="Superseded by a newer setup")
      if value.status in {"pending", "exchanging"}
      else value
      for key, value in state.oauth_transactions.items()
      if value.closed_at is None or value.closed_at + TERMINAL_RETENTION > current_time
    }
    transactions[transaction_id] = transaction
    ordered = sorted(
      transactions.items(), key=lambda item: item[1].created_at, reverse=True
    )[:MAX_TRANSACTIONS]
    state.oauth_transactions = dict(ordered)
    return state

  await _extension().mutate_state_async(update)
  client = OAuth2Client(
    client_id=config.client_id,
    client_secret=config.client_secret,
    scope=" ".join(SCOPES),
    redirect_uri=redirect_uri,
    token_endpoint_auth_method="client_secret_basic",
    timeout=10,
  )
  try:
    authorize_url, _ = client.create_authorization_url(
      AUTHORIZE_URL,
      state=provider_state,
      code_verifier=verifier,
      code_challenge=challenge,
      code_challenge_method="S256",
    )
  finally:
    typing.cast(httpx.Client, client).close()
  return _transaction_view(transaction_id, transaction, authorize_url=authorize_url)


async def get_oauth_transaction(transaction_id: str) -> OAuthTransactionView:
  transaction = (await _state()).oauth_transactions.get(transaction_id)
  if transaction is None:
    raise TwitterSetupError("OAuth transaction not found")
  if transaction.status in {"pending", "exchanging"} and transaction.expires_at <= _now():

    def expire(model: pydantic.BaseModel) -> pydantic.BaseModel:
      state = typing.cast(TwitterExtensionState, model)
      current = state.oauth_transactions.get(transaction_id)
      if current is not None and current.status in {"pending", "exchanging"}:
        state.oauth_transactions[transaction_id] = _terminal(
          current, "expired", error="OAuth transaction expired"
        )
      return state

    state = typing.cast(
      TwitterExtensionState, await _extension().mutate_state_async(expire)
    )
    transaction = state.oauth_transactions[transaction_id]
  return _transaction_view(transaction_id, transaction)


async def _exchange_code(
  config: TwitterExtensionConfig,
  transaction: OAuthTransaction,
  code: str,
) -> tuple[dict[str, typing.Any], str, str]:
  client = AsyncOAuth2Client(
    client_id=config.client_id,
    client_secret=config.client_secret,
    redirect_uri=transaction.redirect_uri,
    token_endpoint_auth_method="client_secret_basic",
    timeout=10,
  )
  try:
    try:
      token = await client.fetch_token(
        TOKEN_URL,
        code=code,
        code_verifier=transaction.pkce_verifier,
      )
    except OAuthError:
      raise TwitterProviderError("Twitter rejected the token exchange") from None
    except httpx.TimeoutException:
      raise TwitterProviderError("Twitter token exchange timed out") from None
    except httpx.HTTPStatusError as failure:
      raise TwitterProviderError(
        f"Twitter token exchange failed (HTTP {failure.response.status_code})"
      ) from None
    except httpx.HTTPError:
      raise TwitterProviderError("Twitter token exchange request failed") from None

    try:
      response = await typing.cast(httpx.AsyncClient, client).get(CURRENT_USER_URL)
      response.raise_for_status()
    except httpx.TimeoutException:
      raise TwitterProviderError("Twitter current-user lookup timed out") from None
    except httpx.HTTPStatusError as failure:
      status_code = failure.response.status_code
      if status_code == 402:
        message = (
          "Twitter current-user lookup requires X API credits or project access (HTTP 402)"
        )
      else:
        message = f"Twitter current-user lookup failed (HTTP {status_code})"
      raise TwitterProviderError(message) from None
    except httpx.HTTPError:
      raise TwitterProviderError("Twitter current-user lookup request failed") from None

    try:
      payload = response.json()
      if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise TwitterProviderError(
          "Twitter current-user lookup returned an invalid response"
        )
      user = payload["data"]
      user_id = user.get("id")
      handle = user.get("username")
      if not isinstance(user_id, str) or not isinstance(handle, str):
        raise TwitterProviderError(
          "Twitter current-user lookup returned an incomplete response"
        )
      return dict(token), user_id, handle
    except (TypeError, ValueError):
      raise TwitterProviderError(
        "Twitter current-user lookup returned an invalid response"
      ) from None
  finally:
    await typing.cast(httpx.AsyncClient, client).aclose()


async def _claim_callback(provider_state: str) -> tuple[str, OAuthTransaction]:
  box: dict[str, typing.Any] = {}

  def claim(model: pydantic.BaseModel) -> pydantic.BaseModel:
    state = typing.cast(TwitterExtensionState, model)
    matched = next(
      (
        (key, value)
        for key, value in state.oauth_transactions.items()
        if value.provider_state == provider_state
      ),
      None,
    )
    if matched is None:
      raise TwitterSetupConflict("OAuth transaction is unknown")
    transaction_id, transaction = matched
    if transaction.status != "pending" or transaction.expires_at <= _now():
      raise TwitterSetupConflict("OAuth transaction is no longer active")
    box["id"] = transaction_id
    box["transaction"] = transaction
    state.oauth_transactions[transaction_id] = transaction.model_copy(
      update={"status": "exchanging"}
    )
    return state

  await _extension().mutate_state_async(claim)
  return typing.cast(str, box["id"]), typing.cast(OAuthTransaction, box["transaction"])


async def _finish_callback(
  transaction_id: str,
  transaction: OAuthTransaction,
  *,
  account: TwitterAccount | None = None,
  error: str | None = None,
) -> None:
  def finish(model: pydantic.BaseModel) -> pydantic.BaseModel:
    state = typing.cast(TwitterExtensionState, model)
    current = state.oauth_transactions.get(transaction_id)
    if (
      current is None
      or current.status != "exchanging"
      or current.provider_state != transaction.provider_state
      or current.app_fingerprint != transaction.app_fingerprint
    ):
      raise TwitterSetupConflict("OAuth transaction was superseded")
    if account is None:
      state.oauth_transactions[transaction_id] = _terminal(
        current, "failed", error=error or "Twitter authorization failed"
      )
    else:
      state.account = account
      state.oauth_transactions[transaction_id] = _terminal(current, "succeeded")
    return state

  await _extension().mutate_state_async(finish)


def _callback_html(status_code: int, title: str, message: str) -> HTMLResponse:
  safe_title = html.escape(title)
  safe_message = html.escape(message)
  return HTMLResponse(
    "<!doctype html><html><head><meta charset='utf-8'>"
    f"<title>{safe_title}</title></head><body><main><h1>{safe_title}</h1>"
    f"<p>{safe_message}</p><p>You can close this window.</p></main></body></html>",
    status_code=status_code,
  )


async def oauth_callback(
  code: str | None = None,
  state: str | None = None,
  error: str | None = None,
  error_description: str | None = None,
) -> HTMLResponse:
  del error_description
  transaction_id: str | None = None
  transaction: OAuthTransaction | None = None
  if not state:
    return _callback_html(400, "Twitter setup failed", "Missing OAuth state.")
  try:
    transaction_id, transaction = await _claim_callback(state)
    if error:
      message = (
        "Twitter authorization was declined"
        if error == "access_denied"
        else "Twitter returned an authorization error"
      )
      await _finish_callback(transaction_id, transaction, error=message)
      return _callback_html(400, "Twitter setup declined", message)
    if not code:
      raise TwitterSetupConflict("Missing authorization code")
    config = _config()
    if _fingerprint(config) != transaction.app_fingerprint:
      raise TwitterSetupConflict("OAuth App changed during authorization")
    token, user_id, handle = await _exchange_code(config, transaction, code)
    raw_scope = token.get("scope", "")
    scopes = tuple(raw_scope.split()) if isinstance(raw_scope, str) else SCOPES
    account = TwitterAccount(
      token=token,
      user_id=user_id,
      handle=handle,
      scopes=scopes,
      app_fingerprint=transaction.app_fingerprint,
      authorization_id=str(uuid.uuid4()),
      connected_at=_now(),
    )
    await _finish_callback(transaction_id, transaction, account=account)
  except TwitterSetupConflict as failure:
    message = _bounded_provider_error(failure)
    try:
      if transaction_id is not None and transaction is not None:
        await _finish_callback(transaction_id, transaction, error=message)
    except TwitterSetupError:
      pass
    return _callback_html(400, "Twitter setup failed", message)
  except (httpx.HTTPError, TwitterSetupError) as failure:
    message = _bounded_provider_error(failure)
    try:
      if transaction_id is not None and transaction is not None:
        await _finish_callback(transaction_id, transaction, error=message)
    except TwitterSetupError:
      pass
    return _callback_html(502, "Twitter is unavailable", message)
  return _callback_html(200, "Twitter connected", "Authorization completed successfully.")


async def disconnect_account() -> TwitterSetupStatus:
  def disconnect(model: pydantic.BaseModel) -> pydantic.BaseModel:
    state = typing.cast(TwitterExtensionState, model)
    state.account = None
    state.oauth_transactions = {
      key: _terminal(value, "expired", error="Account disconnected")
      if value.status in {"pending", "exchanging"}
      else value
      for key, value in state.oauth_transactions.items()
    }
    return state

  await _extension().mutate_state_async(disconnect)
  return await get_setup_status()


def _http_error(error: TwitterSetupError) -> typing.NoReturn:
  if str(error) == "OAuth transaction not found":
    status = fastapi.status.HTTP_404_NOT_FOUND
  elif isinstance(error, TwitterProviderError):
    status = fastapi.status.HTTP_502_BAD_GATEWAY
  else:
    status = fastapi.status.HTTP_409_CONFLICT
  raise fastapi.HTTPException(status_code=status, detail=str(error)) from error


def register_setup_routes(router: fastapi.APIRouter) -> None:
  protected = fastapi.APIRouter(dependencies=[fastapi.Depends(require_peer_jwt)])

  @protected.get("/setup", response_model=TwitterSetupStatus)
  async def setup_status():
    try:
      return await get_setup_status()
    except TwitterSetupError as failure:
      _http_error(failure)

  @protected.put("/setup/oauth-app", response_model=TwitterSetupStatus)
  async def configure_oauth_app(body: SaveOAuthAppRequest):
    try:
      return await save_oauth_app(body)
    except TwitterSetupError as failure:
      _http_error(failure)

  @protected.post("/setup/oauth-transactions", response_model=OAuthTransactionView)
  async def create_oauth_transaction():
    try:
      return await begin_oauth()
    except TwitterSetupError as failure:
      _http_error(failure)

  @protected.post("/setup/oauth-transaction", response_model=OAuthTransactionView)
  async def read_oauth_transaction(body: OAuthTransactionRequest):
    try:
      return await get_oauth_transaction(body.transaction_id)
    except TwitterSetupError as failure:
      _http_error(failure)

  @protected.delete("/setup/account", response_model=TwitterSetupStatus)
  async def delete_account():
    try:
      return await disconnect_account()
    except TwitterSetupError as failure:
      _http_error(failure)

  router.include_router(protected)
  router.add_api_route(
    "/auth/callback",
    oauth_callback,
    methods=["GET"],
    response_class=HTMLResponse,
  )
  from .archive_session import router as archive_session_router

  router.include_router(archive_session_router, prefix="/x-monkey")
