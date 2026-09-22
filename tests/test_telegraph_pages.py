"""Offline rendering, publication idempotency and secret-redaction tests."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import telegraph_pages as pages


def page(text="Full **explanation**.", title="Answer explanation"):
    return {"title": title, "text": text}


def nodes(content):
    for item in content:
        if isinstance(item, dict):
            yield item
            yield from nodes(item.get("children", []))


def plain(content):
    return "".join(item if isinstance(item, str) else plain(item.get("children", [])) for item in content)


def response(payload):
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


class RenderingTests(unittest.TestCase):
    def test_full_explanation_markdown_semantics(self):
        content = pages.validate_page(page(
            "# Topic\n\n## Explanation\n\n**Taking ≠ sharing.** A simple *rule*.\n\n"
            "1. First item\n2. Second item\n\n- One\n- Two\n\n"
            "### Sources\n\n[Source](https://example.org/path?q=1&x=2)"
        ))
        self.assertEqual([n["tag"] for n in content], ["h3", "h3", "p", "ol", "ul", "h4", "p"])
        self.assertEqual(content[2]["children"][0], {"tag": "strong", "children": ["Taking ≠ sharing."]})
        self.assertEqual([plain(n["children"]) for n in content[3]["children"]], ["First item", "Second item"])
        links = [n for n in nodes(content) if n["tag"] == "a"]
        self.assertEqual(links, [{"tag": "a", "attrs": {"href": "https://example.org/path?q=1&x=2"}, "children": ["Source"]}])
        self.assertIn("em", [n["tag"] for n in nodes(content)])

    def test_shipped_explanation_preserves_meaning_and_sources(self):
        quiz = json.loads((ROOT / "example-quiz.json").read_text(encoding="utf-8"))
        source = quiz.get("telegraph") or quiz.get("explanation_page")
        if source is None:
            source = page(quiz["explanation_document"]["text"])
        content = pages.validate_page(source)
        self.assertIn("Сфотографувати ≠ переслати", plain(content))
        self.assertIn("Ситуація, варіанти та репліки", plain(content))
        self.assertGreaterEqual(sum(n["tag"] == "strong" for n in nodes(content)), 7)
        self.assertTrue(any(n["tag"] == "ol" for n in nodes(content)))
        links = [n["attrs"]["href"] for n in nodes(content) if n["tag"] == "a"]
        self.assertIn("https://www.esafety.gov.au/parents/issues-and-advice/privacy-child", links)
        self.assertIn("https://www.esafety.gov.au/young-people/consent-sharing-photos-videos", links)

    def test_raw_html_and_images_are_literal_text(self):
        source = '<script>alert(1)</script> <a href="https://evil.test">raw</a>\n\n![alt](https://evil.test/pic.png)'
        content = pages.validate_page(page(source))
        self.assertEqual([n["tag"] for n in nodes(content)], ["p", "p"])
        self.assertIn('<script>alert(1)</script>', plain(content))
        self.assertIn('![alt](https://evil.test/pic.png)', plain(content))

    def test_unsafe_links_remain_readable_literal_text(self):
        targets = ["javascript:alert(1)", "data:text/html;base64,AAAA", "file:///etc/passwd",
                   "tg://resolve?domain=example", "//evil.test", "https://user:pass@evil.test/x",
                   "https://evil.test:99999/x", "https://", "https://bad\\host/x"]
        for target in targets:
            with self.subTest(target=target):
                source = f"[link]({target})"
                content = pages.validate_page(page(source))
                self.assertFalse(any(n["tag"] == "a" for n in nodes(content)))
                self.assertEqual(plain(content), source)

    def test_http_links_and_balanced_parentheses(self):
        content = pages.validate_page(page("[Site](http://example.org/a_(b))"))
        self.assertEqual(content[0]["children"][0]["attrs"]["href"], "http://example.org/a_(b)")

    def test_code_quotes_escapes_hardbreaks_and_horizontal_rule(self):
        content = pages.validate_page(page("\\*literal\\*  \nline `**code**`\n\n"
            "> Quotation **important**\n\n---\n\n```python\n<script>\nx = 1\n```"))
        self.assertEqual([n["tag"] for n in content], ["p", "blockquote", "hr", "pre"])
        self.assertIn({"tag": "br"}, content[0]["children"])
        self.assertEqual(content[0]["children"][0], "*")
        self.assertEqual(content[-1]["children"][0]["children"], ["<script>\nx = 1"])
        self.assertIn("**code**", plain(content))

    def test_keys_types_unicode_and_controls(self):
        invalid = [None, [], {}, {"title": "a"}, {"title": "a", "text": "x", "url": "https://evil.test"},
                   page(""), page(" \t\n"), page(title=""), page(title="a" * 257),
                   page(None), page(title=1), page("bad\ud800"), page(title="bad\udfff"),
                   page("bad\x00"), page("bad\x1b"), page("bad\x7f")]
        for value in invalid:
            with self.subTest(value=repr(value)[:60]), self.assertRaises(pages.PageError):
                pages.validate_page(value)
        self.assertTrue(pages.validate_page(page(title="ї" * 256)))

    def test_content_limit_is_utf8_serialized_bytes_including_markup(self):
        overhead = len(pages._json(pages.validate_page(page("x"))).encode("utf-8")) - 1
        count = (pages.MAX_CONTENT_BYTES - overhead) // 2
        self.assertTrue(pages.validate_page(page("ї" * count)))
        with self.assertRaises(pages.PageError):
            pages.validate_page(page("ї" * (count + 1)))
        with self.assertRaises(pages.PageError):
            pages.validate_page(page("**ї**\n\n" * 4000))

    def test_line_endings_canonicalize_and_input_is_unmodified(self):
        value = page("# Title\r\n\r\nText\rmore")
        original = value.copy()
        self.assertEqual(pages.validate_page(value), pages.validate_page(page("# Title\n\nText\nmore")))
        self.assertEqual(value, original)


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.token_file = self.directory / "telegraph-token"
        self.token = "SECRET-TELEGRAPH-TOKEN-12345"
        self.token_file.write_text(self.token + "\n", encoding="utf-8")
        self.database_file = self.directory / "state.sqlite3"
        self.db = sqlite3.connect(self.database_file)
        self.publisher = pages.TelegraphPublisher(self.token_file, self.db)
        self.opener = Mock()
        self.publisher._opener = self.opener
        self.url = "https://telegra.ph/Explanation-09-22"
        self.opener.open.return_value = response({"ok": True, "result": {"url": self.url}})

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_fixed_https_post_form_and_pending_committed_before_request(self):
        def request(req, timeout):
            observer = sqlite3.connect(self.database_file)
            try:
                self.assertEqual(observer.execute("SELECT status FROM telegraph_pages").fetchone(), ("pending",))
            finally:
                observer.close()
            self.assertEqual(req.full_url, pages.API_URL)
            self.assertEqual(req.get_method(), "POST")
            self.assertEqual(timeout, 45)
            self.assertIn("application/x-www-form-urlencoded", req.get_header("Content-type"))
            form = urllib.parse.parse_qs(req.data.decode("ascii"), strict_parsing=True)
            self.assertEqual(form["access_token"], [self.token])
            self.assertEqual(form["title"], ["Answer explanation"])
            self.assertEqual(json.loads(form["content"][0]), pages.validate_page(page()))
            self.assertNotIn(self.token, req.full_url)
            return response({"ok": True, "result": {"url": self.url}})
        self.opener.open.side_effect = request
        self.assertEqual(self.publisher.publish(page()), self.url)
        self.assertEqual(self.db.execute("SELECT status, url FROM telegraph_pages").fetchone(), ("published", self.url))

    def test_cache_persists_across_connections_without_token_or_network(self):
        self.assertEqual(self.publisher.publish(page()), self.url)
        self.token_file.unlink()
        self.db.close()
        self.db = sqlite3.connect(self.database_file)
        other = pages.TelegraphPublisher(self.token_file, self.db)
        other._opener = Mock()
        self.assertEqual(other.publish(page()), self.url)
        other._opener.open.assert_not_called()
        self.assertEqual(self.opener.open.call_count, 1)

    def test_canonical_rendered_content_reuses_publication(self):
        self.publisher.publish(page("# Title\r\n\r\nSame"))
        self.assertEqual(self.publisher.publish(page("# Title\n\nSame\n")), self.url)
        self.assertEqual(self.opener.open.call_count, 1)

    def test_different_title_or_content_gets_distinct_cache_key(self):
        self.opener.open.side_effect = lambda *_args, **_kwargs: response({"ok": True, "result": {"url": self.url}})
        self.publisher.publish(page())
        self.publisher.publish(page(title="Another title"))
        self.publisher.publish(page("Another text"))
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM telegraph_pages").fetchone(), (3,))

    def test_multilingual_content_survives_post_and_has_distinct_cache_entries(self):
        title = "العربية · 中文 · 日本語 · 한국어 👩🏽‍💻"
        texts = ["الموافقة لا تعني المشاركة 👩🏽‍💻", "同意拍照不等于同意分享 👩🏽‍💻"]
        sent = []
        def request(req, timeout):
            form = urllib.parse.parse_qs(req.data.decode("ascii"), strict_parsing=True)
            self.assertEqual(form["title"], [title])
            content = json.loads(form["content"][0])
            self.assertEqual(content, [{"tag": "p", "children": [
                {"tag": "strong", "children": [texts[len(sent)]]}]}])
            sent.append(plain(content))
            return response({"ok": True, "result": {"url": f"https://telegra.ph/Language-{len(sent)}-09-22"}})
        self.opener.open.side_effect = request
        values = [page(f"**{text}**", title=title) for text in texts]
        urls = [self.publisher.publish(value) for value in values]
        self.assertEqual(sent, texts)
        self.assertNotEqual(urls[0], urls[1])
        self.assertEqual(self.db.execute("SELECT count(DISTINCT content_hash) FROM telegraph_pages").fetchone(), (2,))
        self.assertEqual([self.publisher.publish(value) for value in values], urls)
        self.assertEqual(self.opener.open.call_count, 2)

    def test_missing_token_is_lazy_and_creates_no_pending(self):
        self.token_file.unlink()
        other = pages.TelegraphPublisher(self.token_file, self.db)
        with self.assertRaisesRegex(pages.PageError, "token file"):
            other.publish(page())
        self.assertEqual(self.db.execute("SELECT count(*) FROM telegraph_pages").fetchone(), (0,))
        self.token_file.write_text(self.token, encoding="utf-8")
        self.assertEqual(self.publisher.publish(page()), self.url)

    def test_invalid_page_never_reads_token_or_calls_network(self):
        self.publisher._token = Mock(side_effect=AssertionError("must not read"))
        with self.assertRaises(pages.PageError):
            self.publisher.publish(page(""))
        self.publisher._token.assert_not_called()
        self.opener.open.assert_not_called()

    def test_unknown_network_outcome_is_not_retried_after_restart(self):
        self.opener.open.side_effect = urllib.error.URLError("remote leaked " + self.token)
        with self.assertRaisesRegex(pages.PageError, "outcome is unknown") as caught:
            self.publisher.publish(page())
        self.assertNotIn(self.token, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.db.close()
        self.db = sqlite3.connect(self.database_file)
        other = pages.TelegraphPublisher(self.token_file, self.db)
        other._opener = Mock()
        with self.assertRaisesRegex(pages.PageError, "retries are blocked"):
            other.publish(page())
        other._opener.open.assert_not_called()
        self.assertEqual(self.opener.open.call_count, 1)

    def test_explicit_api_refusal_clears_claim_but_redacts_reason(self):
        self.opener.open.return_value = response({"ok": False, "error": "ACCESS_TOKEN_INVALID " + self.token})
        with self.assertRaisesRegex(pages.PageError, "rejected") as caught:
            self.publisher.publish(page())
        self.assertNotIn(self.token, str(caught.exception))
        self.assertEqual(self.db.execute("SELECT count(*) FROM telegraph_pages").fetchone(), (0,))
        self.opener.open.return_value = response({"ok": True, "result": {"url": self.url}})
        self.assertEqual(self.publisher.publish(page()), self.url)
        self.assertEqual(self.opener.open.call_count, 2)

    def test_http_api_refusal_is_handled_without_secret_error(self):
        self.opener.open.side_effect = urllib.error.HTTPError(pages.API_URL, 400, self.token, {},
            response({"ok": False, "error": self.token}))
        with self.assertRaises(pages.PageError) as caught:
            self.publisher.publish(page())
        self.assertNotIn(self.token, str(caught.exception))
        self.assertEqual(self.db.execute("SELECT count(*) FROM telegraph_pages").fetchone(), (0,))

    def test_malformed_success_and_untrusted_url_keep_pending(self):
        invalid = [{"ok": True, "result": {}}, {"ok": True, "result": {"url": "http://telegra.ph/Article"}},
                   {"ok": True, "result": {"url": "https://evil.test/Article"}},
                   {"ok": True, "result": {"url": "https://telegra.ph@evil.test/Article"}},
                   {"ok": True, "result": {"url": "https://telegra.ph/Article?token=" + self.token}},
                   {"ok": True, "result": {"url": "https://telegra.ph/Article\n"}},
                   {"ok": False}, {"ok": "true", "result": {"url": self.url}}, []]
        for index, payload in enumerate(invalid):
            with self.subTest(payload=payload):
                value = page(title=f"Article {index}")
                self.opener.open.return_value = response(payload)
                with self.assertRaises(pages.PageError):
                    self.publisher.publish(value)
                calls = self.opener.open.call_count
                with self.assertRaises(pages.PageError):
                    self.publisher.publish(value)
                self.assertEqual(self.opener.open.call_count, calls)

    def test_non_json_and_oversized_response_keep_pending(self):
        for index, raw in enumerate((b"broken " + self.token.encode(), b"x" * (128 * 1024 + 1),
                                     b"[" * 2000 + b"0" + b"]" * 2000)):
            self.opener.open.return_value = io.BytesIO(raw)
            with self.assertRaises(pages.PageError) as caught:
                self.publisher.publish(page(title=str(index)))
            self.assertNotIn(self.token, str(caught.exception))
        self.assertEqual(self.db.execute("SELECT count(*) FROM telegraph_pages WHERE status='pending'").fetchone(), (3,))

    def test_redirects_are_disabled_and_do_not_clear_claim(self):
        real_opener = pages.TelegraphPublisher(self.token_file, self.db)._opener
        redirect = next(h for h in real_opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
        self.assertIsInstance(redirect, pages._NoRedirect)
        self.assertIsNone(redirect.redirect_request(None, None, 302, "", {}, "https://evil.test"))
        self.opener.open.side_effect = urllib.error.HTTPError(pages.API_URL, 302, "redirect", {},
            response({"ok": False, "error": "Do not trust a redirect body"}))
        with self.assertRaises(pages.PageError):
            self.publisher.publish(page())
        self.assertEqual(self.db.execute("SELECT status FROM telegraph_pages").fetchone(), ("pending",))

    def test_existing_pending_never_reads_token_or_posts(self):
        digest = pages.hashlib.sha256(pages._json({"title": page()["title"], "content": pages.validate_page(page())}).encode("utf-8")).hexdigest()
        self.db.execute("INSERT INTO telegraph_pages(content_hash,status) VALUES (?, 'pending')", (digest,))
        self.db.commit()
        self.publisher._token = Mock(side_effect=AssertionError("must not read"))
        with self.assertRaises(pages.PageError):
            self.publisher.publish(page())
        self.publisher._token.assert_not_called()
        self.opener.open.assert_not_called()

    def test_cache_contains_no_token_or_article_text(self):
        self.publisher.publish(page())
        rows = self.db.execute("SELECT * FROM telegraph_pages").fetchall()
        self.assertNotIn(self.token, repr(rows))
        self.assertNotIn(page()["text"], repr(rows))

    def test_claim_commit_failure_never_posts(self):
        wrapper = Mock(wraps=self.db)
        wrapper.commit.side_effect = sqlite3.OperationalError(self.token)
        self.publisher.db = wrapper
        with self.assertRaisesRegex(pages.PageError, "article was not sent") as caught:
            self.publisher.publish(page())
        self.assertNotIn(self.token, str(caught.exception))
        self.opener.open.assert_not_called()
        self.assertEqual(self.db.execute("SELECT count(*) FROM telegraph_pages").fetchone(), (0,))

    def test_success_but_cache_commit_failure_keeps_pending(self):
        wrapper = Mock(wraps=self.db)
        count = 0
        def commit():
            nonlocal count
            count += 1
            if count == 2:
                raise sqlite3.OperationalError(self.token)
            self.db.commit()
        wrapper.commit.side_effect = commit
        self.publisher.db = wrapper
        with self.assertRaisesRegex(pages.PageError, "article was published") as caught:
            self.publisher.publish(page())
        self.assertNotIn(self.token, str(caught.exception))
        self.assertEqual(self.db.execute("SELECT status, url FROM telegraph_pages").fetchone(), ("pending", None))
        with self.assertRaises(pages.PageError):
            self.publisher.publish(page())
        self.assertEqual(self.opener.open.call_count, 1)

    def test_racing_publisher_uses_committed_winner_without_second_request(self):
        other_db = sqlite3.connect(self.database_file)
        try:
            other = pages.TelegraphPublisher(self.token_file, other_db)
            other._opener = Mock()
            other._opener.open.return_value = response({"ok": True, "result": {"url": self.url}})
            def token_after_other_wins():
                self.assertEqual(other.publish(page()), self.url)
                return self.token
            self.publisher._token = token_after_other_wins
            self.assertEqual(self.publisher.publish(page()), self.url)
            self.opener.open.assert_not_called()
            self.assertEqual(other._opener.open.call_count, 1)
        finally:
            other_db.close()


if __name__ == "__main__":
    unittest.main()
