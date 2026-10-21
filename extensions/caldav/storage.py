"""Radicale storage plugin backed exclusively by an InKCre graph transaction."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from email.utils import format_datetime
import threading
from typing import overload
from uuid import uuid4

import anyio
from radicale import item as dav_item
from radicale import storage

from .graph import DAVGraph, dav_transaction
from .codec import normalize
from .schema import (
  CALENDAR,
  CONTACT,
  Collection,
  CollectionContent,
  Resource,
  ResourceContent,
)

move_etag: ContextVar[str | None] = ContextVar("caldav_move_etag", default=None)


class DAVPreconditionError(RuntimeError):
  pass


class DAVCollection(storage.BaseCollection):
  def __init__(self, graph: DAVGraph, value: Collection):
    self.graph = graph
    self.value = value

  @property
  def path(self):
    return self.value.content.path

  @property
  def last_modified(self):
    return format_datetime(self.value.modified.astimezone(UTC), usegmt=True)

  @overload
  def get_meta(self, key: None = None) -> dict[str, str]: ...

  @overload
  def get_meta(self, key: str) -> str | None: ...

  def get_meta(self, key: str | None = None) -> dict[str, str] | str | None:
    props = self.value.content.props
    return dict(props) if key is None else props.get(key)

  def set_meta(self, props):
    self.value.content.props = dict(props)
    self.value.modified = datetime.now(UTC)

  def _item(self, value: Resource):
    # shortcut: root time is approximate; add dependency clocks for date-only clients.
    return dav_item.Item(
      collection=self,
      href=value.content.href,
      text=value.content.text,
      last_modified=format_datetime(value.modified.astimezone(UTC), usegmt=True),
    )

  def get_multi(self, hrefs):
    for href in dict.fromkeys(hrefs):
      value = self.value.items.get(href)
      yield href, self._item(value) if value else None

  def get_all(self):
    for href in sorted(self.value.items):
      yield self._item(self.value.items[href])

  def upload(self, href, item):
    item.prepare()
    old = self.value.items.get(href)
    self.value.items[href] = Resource(
      ResourceContent(href=href, text=normalize(item.serialize())),
      CONTACT if self.tag == "VADDRESSBOOK" else CALENDAR,
      block_id=old.block_id if old else None,
    )
    self.value.modified = datetime.now(UTC)
    return self._item(self.value.items[href]), self._item(old) if old else None

  def delete(self, href=None):
    if href is not None:
      if href not in self.value.items:
        raise storage.ComponentNotFoundError(href)
      del self.value.items[href]
      self.value.modified = datetime.now(UTC)
      return
    for path in tuple(self.graph.collections):
      if path == self.path or path.startswith(self.path + "/"):
        del self.graph.collections[path]

  def sync(self, old_token=""):
    token = "https://inkcre.dev/caldav/sync/" + self.etag.strip('"')
    if old_token and old_token != token:
      # shortcut: full scan on change; add delta history if collection size warrants it.
      raise ValueError("DAV sync token expired")
    return token, () if old_token else tuple(sorted(self.value.items))


class Storage(storage.BaseStorage):
  _is_collision_free = True
  _supports_unicode = True

  def __init__(self, configuration):
    super().__init__(configuration)
    self._local = threading.local()

  @property
  def graph(self) -> DAVGraph:
    return self._local.graph

  @contextmanager
  def acquire_lock(self, mode, user="", *args, **kwargs):
    if mode not in ("r", "w"):
      raise ValueError("Invalid DAV storage lock mode")
    transaction = dav_transaction(mode)
    self._local.graph = anyio.from_thread.run(transaction.__aenter__)
    try:
      if kwargs.get("request") == "MOVE":
        current = next(iter(self.discover(kwargs["path"])), None)
        expected = move_etag.get()
        if current is not None and expected not in ("*", current.etag):
          raise DAVPreconditionError("MOVE requires the current ETag")
      yield
    except BaseException as error:
      anyio.from_thread.run(transaction.__aexit__, type(error), error, error.__traceback__)
      raise
    else:
      anyio.from_thread.run(transaction.__aexit__, None, None, None)
    finally:
      del self._local.graph

  def discover(self, path, depth="0", child_context_manager=None, user_groups=None):
    path = path.strip("/")
    value = self.graph.collections.get(path)
    if not path:
      value = Collection(CollectionContent(path="", props={}))
    if value is None:
      parent, _, href = path.rpartition("/")
      collection = self.graph.collections.get(parent)
      if collection and href in collection.items:
        yield DAVCollection(self.graph, collection)._item(collection.items[href])
      return
    result = DAVCollection(self.graph, value)
    yield result
    if depth == "0":
      return
    yield from result.get_all()
    for name, collection in sorted(self.graph.collections.items()):
      if name.rpartition("/")[0] == path and name != path:
        yield DAVCollection(self.graph, collection)

  def create_collection(self, href, items=None, props=None):
    path = href.strip("/")
    previous = self.graph.collections.get(path)
    if previous is not None and items is None and props is None:
      return DAVCollection(self.graph, previous), {}, []
    if previous is not None:
      raise storage.ComponentExistsError(path)
    value = Collection(
      CollectionContent(path=path, props=dict(props or {})),
      block_id=previous.block_id if previous else None,
    )
    collection = DAVCollection(self.graph, value)
    for item in items or ():
      suffix = ".vcf" if collection.tag == "VADDRESSBOOK" else ".ics"
      collection.upload(uuid4().hex + suffix, item)
    replaced = (
      {
        item.href: item
        for item in DAVCollection(self.graph, previous).get_all()
        if item.href
      }
      if previous
      else {}
    )
    self.graph.collections[path] = value
    return collection, replaced, list(value.items)

  def move(self, item, to_collection, to_href):
    source = self.graph.collections[item.collection.path]
    target = self.graph.collections[to_collection.path]
    resource = source.items.pop(item.href)
    resource.content.href = to_href
    resource.modified = datetime.now(UTC)
    target.items[to_href] = resource
    source.modified = target.modified = resource.modified

  def verify(self):
    with self.acquire_lock("r"):
      for collection in self.graph.collections.values():
        for item in DAVCollection(self.graph, collection).get_all():
          item.prepare()
    return True
