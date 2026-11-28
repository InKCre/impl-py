"""Generate the repository-owned semantic-content acceptance assets."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import wave
import zipfile

import av
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


ASSET_DIRECTORY = Path(__file__).parent


def _write_image() -> None:
  image = Image.new("RGB", (3, 2), color=(32, 96, 160))
  image.save(ASSET_DIRECTORY / "image.png", format="PNG")


def _write_audio() -> None:
  with wave.open(str(ASSET_DIRECTORY / "audio.wav"), "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(8_000)
    audio.writeframes(b"\x00\x00" * 800)


def _write_video() -> None:
  with av.open(str(ASSET_DIRECTORY / "video.mp4"), mode="w") as container:
    stream = container.add_stream("mpeg4", rate=1)
    stream.width = 16
    stream.height = 16
    stream.pix_fmt = "yuv420p"

    frame = av.VideoFrame(width=16, height=16, format="yuv420p")
    for plane in frame.planes:
      plane.update(bytes(plane.buffer_size))
    for packet in stream.encode(frame):
      container.mux(packet)
    for packet in stream.encode():
      container.mux(packet)


def _write_subtitled_video() -> None:
  subtitle_source = BytesIO(
    b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nFlight software integration rehearsal\n"
  )
  with (
    av.open(subtitle_source, mode="r", format="webvtt") as subtitle_container,
    av.open(str(ASSET_DIRECTORY / "video-subtitled.mkv"), mode="w") as container,
  ):
    video = container.add_stream("mpeg4", rate=1)
    video.width = 16
    video.height = 16
    video.pix_fmt = "yuv420p"
    subtitle_input = subtitle_container.streams.subtitles[0]
    subtitle_output = container.add_stream_from_template(subtitle_input)

    frame = av.VideoFrame(width=16, height=16, format="yuv420p")
    for plane in frame.planes:
      plane.update(bytes(plane.buffer_size))
    for packet in video.encode(frame):
      container.mux(packet)
    for packet in video.encode():
      container.mux(packet)
    for packet in subtitle_container.demux(subtitle_input):
      if packet.dts is None:
        continue
      packet.stream = subtitle_output
      container.mux(packet)


def _write_pdf() -> None:
  writer = PdfWriter()
  page = writer.add_blank_page(width=420, height=240)
  font = DictionaryObject(
    {
      NameObject("/Type"): NameObject("/Font"),
      NameObject("/Subtype"): NameObject("/Type1"),
      NameObject("/BaseFont"): NameObject("/Helvetica"),
    }
  )
  page[NameObject("/Resources")] = DictionaryObject(
    {
      NameObject("/Font"): DictionaryObject(
        {NameObject("/F1"): writer._add_object(font)}  # pyrefly: ignore[missing-attribute]
      )
    }
  )
  content = DecodedStreamObject()
  content.set_data(
    b"BT /F1 12 Tf 24 120 Td "
    b"(Deterministic recovery requires an authoritative write-ahead log.) Tj ET"
  )
  page[NameObject("/Contents")] = writer._add_object(content)  # pyrefly: ignore[missing-attribute]
  writer.add_metadata({"/Title": "InKCre semantic content"})
  with (ASSET_DIRECTORY / "document.pdf").open("wb") as output:
    writer.write(output)


def _write_epub() -> None:
  with zipfile.ZipFile(ASSET_DIRECTORY / "book.epub", "w") as epub:
    epub.writestr(
      "mimetype",
      "application/epub+zip",
      compress_type=zipfile.ZIP_STORED,
    )
    epub.writestr(
      "META-INF/container.xml",
      """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
    )
    epub.writestr(
      "OEBPS/package.opf",
      """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" unique-identifier="book-id" xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">
      urn:uuid:00000000-0000-0000-0000-000000000001
    </dc:identifier>
    <dc:title>InKCre semantic content</dc:title>
    <dc:creator>InKCre</dc:creator>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine/>
</package>
""",
    )
    epub.writestr(
      "OEBPS/nav.xhtml",
      """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Navigation</title></head><body/></html>
""",
    )


def _write_zip() -> None:
  with zipfile.ZipFile(ASSET_DIRECTORY / "archive.zip", "w") as archive:
    archive.writestr("hello.txt", "InKCre semantic content\n")


def main() -> None:
  _write_image()
  _write_audio()
  _write_video()
  _write_subtitled_video()
  _write_pdf()
  _write_epub()
  _write_zip()
  (ASSET_DIRECTORY / "unknown.bin").write_bytes(b"InKCre\x00semantic-content\n")


if __name__ == "__main__":
  main()
