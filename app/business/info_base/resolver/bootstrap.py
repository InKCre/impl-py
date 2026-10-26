"""Explicit registration of resolver decoders owned by impl-py."""

from .main import ResolverManager


def register_core_resolvers() -> None:
  """Register built-in decoders independently of extension loading."""
  from .audio import AudioResolver
  from .epub import EPUBResolver
  from .file import FileResolver
  from .html import HTMLResolver
  from .image import ImageResolver
  from .pdf import PDFResolver
  from .text import TextResolver
  from .video import VideoResolver
  from .zip import ZIPResolver

  for resolver_class in (
    TextResolver,
    HTMLResolver,
    ImageResolver,
    AudioResolver,
    VideoResolver,
    PDFResolver,
    EPUBResolver,
    ZIPResolver,
    FileResolver,
  ):
    ResolverManager.register_resolver(resolver_class)
