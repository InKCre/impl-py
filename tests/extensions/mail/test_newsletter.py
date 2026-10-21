"""Newsletter projections preserve articles, source bytes and MIME alternatives."""

import email
import email.policy

import pytest
from bs4 import BeautifulSoup

from app.business.info_base.resolver import ResolverManager
from app.schemas.info_base.block import BlockModel
from extensions.mail.adapter import IMAPAdapter, _mailing_list, _walk_body_structure
from extensions.mail.newsletter import (
  NEWSLETTER_HTML_RESOLVER,
  NEWSLETTER_TEXT_RESOLVER,
  clean_link,
  clean_newsletter_html,
  clean_newsletter_text,
)
from extensions.mail.resolver import reading_bodies
from extensions.mail.schema import ComponentRelation, IMAPParameters, SolvedBody


HTML = """
<html><head><title>Newsletter</title><style>.a { color: red; }</style></head><body>
<div style="display: none !important">Preview noise</div>
<table><tr><td><h1>Two useful articles</h1>
<p>Article one explains unsubscribe design without removing its discussion.</p>
<a href="https://example.org/one?id=a%2Fb&utm_source=mail&edition=x%2By#section">
Article one</a>
<h2>Article two</h2><p>A separate article must survive.</p>
<a href="https://example.org/two?page=2&mc_cid=track">Article two</a>
<img src="https://tracker.invalid/open" width="1" height="1">
<img src="cid:diagram" alt="Useful diagram" width="400">
<p>Public reference https://example.org/reference</p>
<pre>left | right</pre>
<p><a href="https://example.org/unsubscribe?token=secret">Unsubscribe</a></p>
</td></tr></table></body></html>
"""


def test_reading_html_preserves_both_articles_and_functional_links():
  clean = clean_newsletter_html(HTML)
  document = BeautifulSoup(clean, "html.parser")
  assert document.h1 is not None and document.h2 is not None and document.img is not None
  assert document.h1.get_text() == "Two useful articles"
  assert document.h2.get_text() == "Article two"
  assert "unsubscribe design" in document.get_text()
  assert "Preview noise" not in clean
  assert "tracker.invalid" not in clean
  assert "token=secret" not in clean
  assert document.img["src"] == "cid:diagram"
  assert [link["href"] for link in document.find_all("a")] == [
    "https://example.org/one?id=a%2Fb&edition=x%2By#section",
    "https://example.org/two?page=2",
  ]
  assert BeautifulSoup(clean_newsletter_html(clean), "html.parser").get_text(
    " ", strip=True
  ) == document.get_text(" ", strip=True)


@pytest.mark.parametrize(
  "url",
  [
    "https://example.org/a?signature=a%2Bb&empty=&id=1&id=2#part",
    "https://redirect.example/track?url=https%3A%2F%2Fexample.org%2Fa&token=x",
    "mailto:reader@example.org",
    "https://[broken/",
    "https://example.org/signed?signature=x%2By&utm_source=email",
    "https://example.org/signed?X-Amz-Signature=abc&utm_medium=email",
  ],
)
def test_unknown_or_functional_links_are_not_rewritten(url):
  assert clean_link(url) == url


def test_plain_text_keeps_editorial_discussion_of_unsubscribe():
  original = (
    "This article explains unsubscribe design.\n"
    "Read: https://example.org/story?id=7&utm_medium=email\n"
    "Unsubscribe: https://example.org/remove?token=secret\n"
    "取消订阅\n"
    "A second article."
  )
  assert clean_newsletter_text(original) == (
    "This article explains unsubscribe design.\n"
    "Read: https://example.org/story?id=7\n"
    "A second article."
  )


def test_control_only_paragraph_does_not_remove_its_articles():
  source = (
    "<table><tr><td><p>An article about email preferences.</p>"
    '<a href="https://example.org/issue">Read online</a>'
    '<p>Update your email preferences or unsubscribe <a href="https://example.org/remove">'
    "here</a></p><p>A final article.</p></td></tr></table>"
  )
  document = BeautifulSoup(clean_newsletter_html(source), "html.parser")
  assert document.get_text(" ", strip=True) == (
    "An article about email preferences. A final article."
  )
  assert not document.select("a")


@pytest.mark.parametrize(
  "header, expected",
  [
    ("List-ID: Research Weekly <Weekly.Example.ORG>\n", "weekly.example.org"),
    ("List-ID: Research Weekly <weekly. example.org>\n", "weekly.example.org"),
    ("List-ID: =?utf-8?b?56CU56m25ZGo5oql?= <weekly.example.org>\n", "weekly.example.org"),
    ("List-Unsubscribe: <https://example.org/leave>\n", None),
    ("List-ID: no brackets\n", None),
    ("List-ID: <one.example>\nList-ID: <two.example>\n", None),
    ("List-ID: <>\n", None),
  ],
)
def test_declared_list_identity_is_required(header, expected):
  message = email.message_from_string(header + "\n", policy=email.policy.default)
  result = _mailing_list(message)
  assert (result.list_id if result else None) == expected


@pytest.mark.parametrize(
  "headers, expected",
  [
    (
      "X-Beehiiv-Type: newsletter\nX-Newsletter-ID: HTTPS://Weekly.Example:443\n",
      "beehiiv:https://weekly.example/",
    ),
    (
      "X-Beehiiv-Type: newsletter\nX-Newsletter-ID: https://weekly.example/Publication\n",
      "beehiiv:https://weekly.example/Publication",
    ),
    ("X-Newsletter-ID: https://weekly.example/\n", None),
    ("X-Beehiiv-Type: newsletter\n", None),
    (
      "X-Beehiiv-Type: newsletter\nX-Newsletter-ID: https://weekly.example/?subscriber=123\n",
      None,
    ),
    (
      "X-Beehiiv-Type: newsletter\nX-Newsletter-ID: https://reader:secret@weekly.example/\n",
      None,
    ),
    (
      "X-Beehiiv-Type: newsletter\nX-Newsletter-ID: https://weekly.example/#subscriber\n",
      None,
    ),
    (
      "X-Beehiiv-Type: newsletter\nX-Newsletter-ID: https://weekly.example/\n"
      "X-Newsletter-ID: https://other.example/\n",
      None,
    ),
    (
      "X-Beehiiv-Type: newsletter\nX-Beehiiv-Type: newsletter\n"
      "X-Newsletter-ID: https://weekly.example/\n",
      None,
    ),
    (
      "List-ID: <declared.example>\nX-Beehiiv-Type: newsletter\n"
      "X-Newsletter-ID: https://weekly.example/\n",
      "declared.example",
    ),
    (
      "List-ID: broken\nX-Beehiiv-Type: newsletter\n"
      "X-Newsletter-ID: https://weekly.example/\n",
      None,
    ),
  ],
)
def test_beehiiv_requires_an_unambiguous_publication_declaration(headers, expected):
  message = email.message_from_string(headers + "\n", policy=email.policy.default)
  result = _mailing_list(message)
  assert (result.list_id if result else None) == expected


def test_imap_fetch_preserves_list_identity_original_content_and_alternatives(monkeypatch):
  plain = (b"TEXT", b"PLAIN", (b"CHARSET", b"UTF-8"), None, None, b"8BIT", 20, 1)
  html = (b"TEXT", b"HTML", (b"CHARSET", b"UTF-8"), None, None, b"8BIT", 200, 1)
  structure = ([([plain, html], b"ALTERNATIVE"), plain], b"MIXED")

  class Client:
    def capabilities(self):
      return ()

    def fetch(self, uids, items):
      if items[0] == "BODY.PEEK[HEADER]":
        return {
          1: {
            b"BODY[HEADER]": (
              b"List-ID: Weekly <weekly.example.org>\r\nSubject: Same title\r\n\r\n"
            ),
            b"BODYSTRUCTURE": structure,
          }
        }
      part = items[0].removeprefix("BODY.PEEK[").removesuffix("]")
      return {1: {f"BODY[{part}]".encode(): HTML.encode() if part == "1.2" else b"Article"}}

  adapter = IMAPAdapter(
    IMAPParameters(host="test.invalid", username="test", password="test")
  )
  monkeypatch.setattr(adapter, "_connected", lambda: Client())
  fact = adapter._fetch_messages("INBOX", 1, [1])[0]
  assert fact.mailing_list is not None
  assert fact.mailing_list.list_id == "weekly.example.org"
  assert fact.bodies[1].content == HTML
  assert [(body.part_id, body.alternative_groups) for body in fact.bodies] == [
    ("1.1", ("1",)),
    ("1.2", ("1",)),
    ("2", ()),
  ]
  bodies = tuple(
    SolvedBody(
      block=BlockModel(
        id=index + 1,
        resolver=NEWSLETTER_HTML_RESOLVER
        if body.media_type == "text/html"
        else NEWSLETTER_TEXT_RESOLVER,
        content=body.content,
      ),
      solved_content=body.content,
      relation=ComponentRelation(
        role="body",
        part_id=body.part_id,
        alternative_groups=body.alternative_groups,
      ),
    )
    for index, body in enumerate(fact.bodies)
  )
  assert [body.relation.part_id for body in reading_bodies(bodies)] == ["1.2", "2"]
  assert len(_walk_body_structure(structure)) == 3


def test_body_resolver_indexes_clean_text_and_keeps_original(async_runner):
  resolver = ResolverManager.get(
    BlockModel(id=100, resolver=NEWSLETTER_HTML_RESOLVER, content=HTML)
  )
  text = async_runner.run(resolver.get_text(context="lexical", materialize_missing=False))
  assert "Article one" in text and "Article two" in text
  assert "Preview noise" not in text and "token=secret" not in text
  assert "https://example.org/one" not in text and "cid:diagram" not in text
  assert "https://example.org/reference" in text
  assert "left | right" in text and "# Two useful" not in text
  default_text = async_runner.run(resolver.get_text(materialize_missing=False))
  assert "id=a%2Fb&edition=x%2By" in default_text
  solved = async_runner.run(resolver.get_solved_content(materialize_missing=False))
  assert "id=a%2Fb&amp;edition=x%2By" in solved
  assert async_runner.run(resolver.get_raw_content()) == HTML


def test_image_share_controls_do_not_remove_image_articles():
  html = """
  <a href="https://example.org/share"><img alt="share on facebook"></a>
  <a href="https://example.org/image"><img width="630"></a>
  <a href="https://example.org/article">Article about sharing
  <img alt="share on twitter"></a>
  <p>Update your email preferences
  or unsubscribe <a href="https://example.org/remove">here</a></p>
  """
  cleaned = clean_newsletter_html(html)
  assert 'href="https://example.org/share"' not in cleaned
  assert 'href="https://example.org/remove"' not in cleaned
  assert 'href="https://example.org/image"' in cleaned
  assert "Article about sharing" in cleaned


def test_nested_alternatives_select_each_level_without_losing_mixed_parts():
  plain = (b"TEXT", b"PLAIN", None, None, None, b"8BIT", 10, 1)
  html = (b"TEXT", b"HTML", None, None, None, b"8BIT", 10, 1)
  nested = ([plain, ([([plain, html], b"ALTERNATIVE")], b"RELATED")], b"ALTERNATIVE")
  descriptors = _walk_body_structure(([nested, plain], b"MIXED"))
  bodies = tuple(
    SolvedBody(
      block=BlockModel(
        id=index + 1,
        resolver="core.html.v1" if part.media_type == "text/html" else "core.text.v1",
        content="article",
      ),
      solved_content="article",
      relation=ComponentRelation(
        role="body", part_id=part.part_id, alternative_groups=part.alternative_groups
      ),
    )
    for index, part in enumerate(descriptors)
  )
  assert [body.relation.part_id for body in reading_bodies(bodies)] == ["1.2.1.2", "2"]
