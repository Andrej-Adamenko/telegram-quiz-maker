"""Offline contract and failure-path tests. No token or network is used."""
from __future__ import annotations

from copy import deepcopy
from email import policy
from email.parser import BytesParser
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bot


def minimal():
    return {"version": 1, "question": "What should you do?",
            "options": ["First action", "Second action"], "correct_option": 2}


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class ValidationTests(unittest.TestCase):
    def test_minimal_and_shipped_example(self):
        self.assertEqual(bot.parse_quiz(encoded(minimal())), minimal())
        example = bot.parse_quiz((ROOT / "example-quiz.json").read_bytes())
        self.assertEqual(example["correct_option"], 3)
        self.assertIn("Сфотографувати ≠ переслати", example["telegraph"]["text"])

    def test_maximum_valid_boundaries_with_utf8_document(self):
        quiz = minimal()
        quiz.update(question="ї" * 300, options=[str(i) + "ї" * 98 for i in range(12)],
                    correct_option=12, description="ї" * 1024,
                    explanation="ї" * 198 + "\n\n",
                    explanation_document={"filename": "a" * 77 + ".md",
                                          "text": "ї" * (bot.MAX_DOCUMENT_BYTES // 2)})
        self.assertEqual(len(quiz["explanation_document"]["text"].encode("utf-8")),
                         bot.MAX_DOCUMENT_BYTES)
        self.assertEqual(bot.parse_quiz(encoded(quiz)), quiz)

    def test_optional_empty_text_and_bom_are_accepted(self):
        quiz = dict(minimal(), explanation="", description="")
        self.assertEqual(bot.parse_quiz(b"\xef\xbb\xbf" + encoded(quiz)), quiz)

    def test_invalid_required_fields_and_boundaries(self):
        cases = [
            ("version", True), ("version", 1.0), ("version", 2),
            ("question", ""), ("question", " \t\n"), ("question", "ї" * 301),
            ("options", []), ("options", [str(i) for i in range(13)]),
            ("options", ["", "two"]), ("options", ["a" * 101, "two"]),
            ("options", ["same", "same"]), ("options", [" Same ", "same"]),
            ("options", ["one", None]), ("options", "one,two"),
            ("correct_option", 0), ("correct_option", 3),
            ("correct_option", True), ("correct_option", 1.0),
            ("explanation", None), ("description", []),
            ("question", "bad\x00text"), ("question", "bad\x1btext"),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=repr(value)[:80]):
                quiz = minimal()
                quiz[field] = value
                with self.assertRaises(bot.ValidationError):
                    bot.parse_quiz(encoded(quiz))
        for field in minimal():
            with self.subTest(missing=field):
                quiz = minimal()
                del quiz[field]
                with self.assertRaises(bot.ValidationError):
                    bot.parse_quiz(encoded(quiz))

    def test_unknown_fields_cannot_redirect_or_change_poll_policy(self):
        for field, value in (("chat_id", "@channel"), ("type", "regular"),
                             ("shuffle_options", True), ("unknown", None)):
            with self.subTest(field=field), self.assertRaises(bot.ValidationError):
                bot.parse_quiz(encoded(dict(minimal(), **{field: value})))

    def test_document_limits_and_filename_cannot_inject_headers_or_paths(self):
        names = ["../a.md", "a/b.md", "a\\b.md", "a..b.md", "приклад.md",
                 "a.pdf", 'a".md', "a.md\r\nX-Header: injected", "a" * 78 + ".md"]
        for name in names:
            with self.subTest(filename=name), self.assertRaises(bot.ValidationError):
                bot.parse_quiz(encoded(dict(minimal(), explanation_document={
                    "filename": name, "text": "Explanation"})))
        for document in ({"filename": "a.md", "text": ""},
                         {"filename": "a.md", "text": " "},
                         {"filename": "a.md", "text": "ї" * 65537},
                         {"filename": "a.md", "text": "x", "url": "https://example.test"},
                         {"filename": "a.md"}, "a.md"):
            with self.subTest(document=str(document)[:80]), self.assertRaises(bot.ValidationError):
                bot.parse_quiz(encoded(dict(minimal(), explanation_document=document)))

    def test_malformed_ambiguous_and_nonstandard_json_is_rejected(self):
        invalid = [b"[]", b"null", b"{", b"```json\n{}\n```", b"\xff",
                   b'{"version": 1, "version": 1}',
                   b'{"explanation_document": {"text": "a", "text": "b"}}',
                   b'{"version": NaN}', b'{"version": Infinity}',
                   encoded(minimal()).replace("What should you do?".encode("utf-8"), b"\\ud800"),
                   b" " * (bot.MAX_JSON_BYTES + 1)]
        for raw in invalid:
            with self.subTest(raw=repr(raw[:80])), self.assertRaises(bot.ValidationError):
                bot.parse_quiz(raw)



class RequestTests(unittest.TestCase):
    def test_single_option_supported_by_current_api(self):
        quiz = dict(minimal(), options=["Only option"], correct_option=1)
        payload, _ = bot.poll_request(bot.parse_quiz(encoded(quiz)), 123)
        self.assertEqual(payload["correct_option_ids"], [0])

    def test_url_substitution_and_custom_label_preserve_other_text(self):
        url = "https://telegra.ph/Example-09-22"
        quiz = dict(minimal(), question="Source {{telegraph.url}}",
                    description="💡 [My text]({{telegraph.url}}), again {{telegraph.url}}",
                    explanation="[Summary]({{telegraph.url}})",
                    telegraph={"title": "Another topic", "text": "Text"})
        payload, files = bot.poll_request(bot.parse_quiz(encoded(quiz)), 123, url)
        self.assertEqual(payload["question"], "Source " + url)
        escaped = r"https://telegra\.ph/Example\-09\-22"
        self.assertEqual(payload["description"], "💡 [My text](" + escaped + "), again " + escaped)
        self.assertEqual(payload["description_parse_mode"], "MarkdownV2")
        self.assertEqual(payload["explanation"], "[Summary](" + escaped + ")")
        self.assertNotIn("explanation_media", payload)
        self.assertEqual(files, {})

    def test_native_formatting_is_passed_without_local_markup_restrictions(self):
        quiz = dict(minimal(), description="*Arbitrary text*",
                    explanation="[Short link](https://example.test/" + "a" * 300 + ")")
        payload, _ = bot.poll_request(bot.parse_quiz(encoded(quiz)), 123)
        self.assertEqual(payload["explanation"], quiz["explanation"])
        self.assertEqual(payload["explanation_parse_mode"], "MarkdownV2")
        self.assertEqual(payload["description_parse_mode"], "MarkdownV2")
        self.assertNotIn("explanation_entities", payload)

    def test_templates_are_validated_and_visible_length_is_used(self):
        quiz = dict(minimal(), telegraph={"title": "T", "text": "Text"},
                    explanation="[" + "x" * 200 + "]({{telegraph.url}})")
        bot.parse_quiz(encoded(quiz))
        payload, _ = bot.poll_request(quiz, 123, "https://telegra.ph/a-09-22")
        self.assertTrue(payload["explanation"].startswith("[" + "x" * 200 + "]("))
        bad = [dict(minimal(), explanation="{{telegraph.url}}"),
               dict(minimal(), telegraph=quiz["telegraph"]),
               dict(quiz, explanation="{{telegraph.other}}"),
               dict(quiz, question="[A]({{telegraph.url}})"),
               dict(minimal(), explanation_parse_mode="HTML"),
               dict(minimal(), explanation="x", explanation_parse_mode="invalid")]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(bot.ValidationError):
                bot.parse_quiz(encoded(value))
        quiz["question"] = "x" * 280 + "{{telegraph.url}}"
        bot.parse_quiz(encoded(quiz))
        with self.assertRaises(bot.ValidationError):
            bot.poll_request(quiz, 123, "https://telegra.ph/a-long-article-url-09-22")

    def test_correct_answer_maps_from_one_based_to_zero_based_for_every_position(self):
        quiz = minimal()
        quiz["options"] = [f"Option {i}" for i in range(1, 13)]
        for correct in range(1, 13):
            with self.subTest(correct=correct):
                quiz["correct_option"] = correct
                payload, files = bot.poll_request(quiz, 123)
                self.assertEqual(payload["correct_option_ids"], [correct - 1])
                self.assertEqual(payload["chat_id"], 123)
                self.assertEqual(payload["type"], "quiz")
                self.assertTrue(payload["is_anonymous"])
                self.assertIs(payload["allows_revoting"], False)
                self.assertFalse(payload["allows_multiple_answers"])
                self.assertFalse(payload["shuffle_options"])
                self.assertFalse(payload["protect_content"])
                self.assertNotIn("explanation_media", payload)
                self.assertEqual(files, {})

    def test_multipart_links_document_to_explanation_and_preserves_utf8(self):
        quiz = dict(minimal(), explanation="Short rule", description="After answering: 💡",
                    explanation_document={"filename": "explanation.md",
                                          "text": '# Explanation\n\n«Taking ≠ sharing».\n"Quotation"'})
        payload, files = bot.poll_request(quiz, 123)
        body, content_type = bot.multipart(payload, files)
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body)
        self.assertTrue(message.is_multipart())
        parts = {part.get_param("name", header="content-disposition"): part
                 for part in message.iter_parts()}
        field = lambda name: parts[name].get_payload(decode=True).decode("utf-8")
        self.assertEqual(json.loads(field("explanation_media")),
                         {"type": "document", "media": "attach://explanation_file"})
        self.assertEqual(json.loads(field("correct_option_ids")), [1])
        self.assertEqual(json.loads(field("options")), [{"text": text} for text in quiz["options"]])
        self.assertEqual(json.loads(field("is_anonymous")), True)
        self.assertEqual(field("allows_revoting"), "false")
        self.assertEqual(field("question"), quiz["question"])
        self.assertEqual(field("explanation"), quiz["explanation"])
        self.assertEqual(field("description"), quiz["description"])
        self.assertEqual(parts["explanation_file"].get_filename(), "explanation.md")
        self.assertEqual(parts["explanation_file"].get_payload(decode=True),
                         quiz["explanation_document"]["text"].encode("utf-8"))

    def test_http_error_response_is_closed_and_token_redacted(self):
        token = "1:fake-token"
        response = io.BytesIO(encoded({"ok": False, "error_code": 400,
                                      "description": "Rejected " + token}))
        failure = urllib.error.HTTPError("https://api.telegram.org/unused", 400,
                                         "Bad request", {}, response)
        api = bot.Telegram(token)
        api._opener = Mock()
        api._opener.open.side_effect = failure
        with self.assertRaises(bot.TelegramError) as caught:
            api.call("sendPoll", {})
        self.assertEqual(caught.exception.code, 400)
        self.assertNotIn(token, str(caught.exception))
        self.assertTrue(response.closed)


class IdleCursorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.state = bot.State(self.tmp.name, clock=lambda: self.now)

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_offset_resets_at_idle_boundary_once_and_accepts_lower_new_ids(self):
        self.state.claim(1000)
        self.state.finish(1000)
        self.now += bot.IDLE_OFFSET_RESET_SECONDS - 1
        self.assertFalse(self.state.prepare_poll())
        self.assertEqual(self.state.offset, 1001)
        self.now += 1
        self.assertTrue(self.state.prepare_poll())
        self.assertEqual(self.state.offset, 0)
        self.assertFalse(self.state.prepare_poll())
        self.assertTrue(self.state.claim(10))
        self.assertEqual(self.state.offset, 11)

    def test_receipt_timestamp_survives_restart_and_delays_idle_reset(self):
        self.state.claim(100)
        self.state.finish(100)
        self.now += bot.IDLE_OFFSET_RESET_SECONDS - 1
        self.state.claim(101)
        self.state.finish(101)
        self.state.close()
        self.state = bot.State(self.tmp.name, clock=lambda: self.now)
        self.now += 2
        self.assertFalse(self.state.prepare_poll())
        self.assertEqual(self.state.offset, 102)
        self.now += bot.IDLE_OFFSET_RESET_SECONDS
        self.assertTrue(self.state.prepare_poll())
        self.state.close()
        self.state = bot.State(self.tmp.name, clock=lambda: self.now)
        self.assertFalse(self.state.prepare_poll())

    def test_idle_reset_allows_reused_completed_ids_but_keeps_processing_warning(self):
        self.state.claim(100)
        self.state.finish(100)
        self.state.claim(200)
        self.now += bot.IDLE_OFFSET_RESET_SECONDS
        self.assertTrue(self.state.prepare_poll())
        self.assertEqual(self.state.interrupted(), [200])
        self.assertTrue(self.state.claim(100))
        self.assertFalse(self.state.claim(200))


class FakeTelegram:
    """Records remote operations; never creates HTTP requests."""
    def __init__(self, raw=None, poll_error=None):
        self.raw = encoded(minimal()) if raw is None else raw
        self.poll_error = poll_error
        self.calls = []

    def call(self, method, payload=None, files=None):
        self.calls.append((method, deepcopy(payload), deepcopy(files)))
        if method == "sendPoll" and self.poll_error is not None:
            error, self.poll_error = self.poll_error, None
            raise error
        return {"message_id": 101}

    def download(self, file_id):
        self.call("getFile", {"file_id": file_id})
        return self.raw

    def count(self, method):
        return sum(call[0] == method for call in self.calls)


def update(update_id=100, owner=123):
    return {"update_id": update_id, "message": {
        "message_id": 10, "from": {"id": owner, "is_bot": False},
        "chat": {"id": owner, "type": "private"},
        "document": {"file_id": "sample-file", "file_name": "quiz.json", "file_size": 100}}}


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = bot.State(self.tmp.name)
        self.api = FakeTelegram()
        self.quiz_bot = bot.QuizBot(self.api, 123, self.state)

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def reopen(self):
        self.state.close()
        self.state = bot.State(self.tmp.name)
        self.quiz_bot = bot.QuizBot(self.api, 123, self.state)

    def test_only_owner_private_documents_are_downloaded(self):
        cases = []
        cases.append(update(owner=456))
        for chat_type in ("group", "supergroup", "channel"):
            item = update()
            item["message"]["chat"]["type"] = chat_type
            cases.append(item)
        item = update()
        item["message"]["from"]["is_bot"] = True
        cases.append(item)
        item = update()
        item["message"]["chat"]["id"] = 456
        cases.append(item)
        item = update()
        del item["message"]["from"]
        cases.append(item)
        for i, item in enumerate(cases):
            with self.subTest(case=i):
                item["update_id"] += i
                self.quiz_bot.handle(item)
                self.assertEqual(self.api.calls, [])
        self.quiz_bot.handle(update(200))
        self.assertEqual([call[0] for call in self.api.calls], ["getFile", "sendPoll"])

    def test_publication_requires_owner_and_valid_json_and_precedes_poll(self):
        quiz = dict(minimal(), telegraph={"title": "Test", "text": "Article"},
                    explanation="[Read]({{telegraph.url}})")
        self.api.raw = encoded(quiz)
        publisher = Mock()
        def publish(page):
            self.assertEqual(self.api.count("sendPoll"), 0)
            return "https://telegra.ph/Test-09-22"
        publisher.publish.side_effect = publish
        self.quiz_bot.publisher = publisher
        self.quiz_bot.handle(update(owner=456))
        publisher.publish.assert_not_called()
        self.quiz_bot.handle(update(101))
        publisher.publish.assert_called_once_with(quiz["telegraph"])
        self.assertEqual(self.api.count("sendPoll"), 1)
        self.assertIn(r"https://telegra\.ph/Test\-09\-22", self.api.calls[-1][1]["explanation"])
        self.assertEqual(self.api.calls[-1][1]["explanation_parse_mode"], "MarkdownV2")

    def test_failed_publication_never_sends_poll(self):
        self.api.raw = encoded(dict(minimal(), telegraph={"title": "T", "text": "Text"},
                                    explanation="{{telegraph.url}}"))
        publisher = Mock()
        publisher.publish.side_effect = bot.PageError("Publication was not confirmed")
        self.quiz_bot.publisher = publisher
        self.quiz_bot.handle(update())
        self.assertEqual(self.api.count("sendPoll"), 0)
        self.assertIn("Publication was not confirmed", self.api.calls[-1][1]["text"])

    def test_content_language_is_preserved_independently_of_telegram_locale(self):
        examples = [
            ("uk", "What should you do?", ["Ask first", "Send now"], "Ask permission", "Read more"),
            ("en", "Що робити?", ["Спочатку запитати", "Надіслати зараз"], "Запитайте дозволу", "Докладніше"),
            ("zh-hans", "¿Qué debes hacer?", ["Preguntar primero", "Enviar ahora"], "Pide permiso", "Más información"),
            ("uk", "ماذا تفعل؟", ["اسأل أولاً", "أرسل الآن"], "اطلب الإذن", "اقرأ المزيد"),
            ("ar", "应该怎么做？", ["先询问", "立即发送"], "先征得同意 💡", "阅读更多"),
        ]
        publisher = Mock()
        publisher.publish.return_value = "https://telegra.ph/example-09-22"
        self.quiz_bot.publisher = publisher
        for index, (locale, question, options, explanation, label) in enumerate(examples):
            with self.subTest(locale=locale, question=question):
                quiz = {"version": 1, "question": question, "description": question,
                        "options": options, "correct_option": 1,
                        "explanation": explanation + " [" + label + "]({{telegraph.url}})",
                        "telegraph": {"title": question, "text": explanation + " 👩🏽‍👧"}}
                self.api.raw = encoded(quiz)
                item = update(200 + index)
                item["message"]["from"]["language_code"] = locale
                self.quiz_bot.handle(item)
                publisher.publish.assert_called_with(quiz["telegraph"])
                method, payload, _ = self.api.calls[-1]
                self.assertEqual(method, "sendPoll")
                self.assertEqual(payload["question"], question)
                self.assertEqual(payload["description"], question)
                self.assertEqual(payload["options"], [{"text": option} for option in options])
                self.assertEqual(payload["explanation"], explanation + " [" + label +
                                 r"](https://telegra\.ph/example\-09\-22)")
                self.assertEqual(payload["description_parse_mode"], "MarkdownV2")

    def test_help_and_errors_remain_english_for_other_telegram_locales(self):
        for index, locale in enumerate(("uk", "ar", "zh-hans")):
            with self.subTest(locale=locale):
                item = update(300 + index * 2)
                item["message"]["from"]["language_code"] = locale
                item["message"]["text"] = "/help"
                self.quiz_bot.handle(item)
                self.assertTrue(self.api.calls[-1][1]["text"].startswith("Send a JSON file"))
                del item["message"]["text"]
                item["update_id"] += 1
                self.api.raw = b'{}'
                self.quiz_bot.handle(item)
                self.assertEqual(self.api.calls[-1][1]["text"], "Quiz not created: Set version to 1.")

    def test_public_whoami_discloses_only_callers_id_without_downloading(self):
        item = update(owner=456)
        item["message"]["text"] = "/whoami"
        self.quiz_bot.handle(item)
        self.assertEqual(self.api.count("getFile"), 0)
        self.assertEqual(self.api.calls[0][1], {"chat_id": 456, "text": "Your Telegram ID: 456"})

    def test_untrusted_whitespace_message_cannot_trigger_owner_error_notice(self):
        item = update(owner=456)
        item["message"]["text"] = " \n\t"
        self.quiz_bot.handle(item)
        self.assertEqual(self.api.calls, [])

    def test_obvious_wrong_file_is_rejected_before_getfile(self):
        for i, fields in enumerate(({"file_name": "image.png"},
                                    {"file_size": bot.MAX_JSON_BYTES + 1})):
            item = update(100 + i)
            item["message"]["document"].update(fields)
            self.quiz_bot.handle(item)
        self.assertEqual(self.api.count("getFile"), 0)
        self.assertEqual(self.api.count("sendPoll"), 0)
        self.assertEqual(self.api.count("sendMessage"), 2)

    def test_invalid_json_reports_error_without_sending_poll(self):
        self.api.raw = b'{"invalid": true}'
        self.quiz_bot.handle(update())
        self.assertEqual(self.api.count("getFile"), 1)
        self.assertEqual(self.api.count("sendPoll"), 0)
        self.assertIn("Quiz not created", self.api.calls[-1][1]["text"])

    def test_completed_update_is_not_reprocessed_after_restart(self):
        self.quiz_bot.handle(update())
        self.reopen()
        self.quiz_bot.handle(update())
        self.assertEqual(self.api.count("getFile"), 1)
        self.assertEqual(self.api.count("sendPoll"), 1)
        self.assertEqual(self.state.offset, 101)

    def test_processing_claim_survives_restart_and_prevents_duplicate(self):
        self.assertTrue(self.state.claim(100))
        self.reopen()
        self.assertEqual(self.state.interrupted(), [100])
        self.assertEqual(self.state.offset, 101)
        self.quiz_bot.handle(update())
        self.assertEqual(self.api.calls, [])
        self.state.finish(100, "interrupted")
        self.assertEqual(self.state.interrupted(), [])
        self.assertFalse(self.state.claim(100))

    def test_ambiguous_send_is_not_retried_even_after_restart(self):
        self.api.poll_error = bot.NetworkError("Connection lost after possible acceptance")
        with self.assertLogs(bot.LOG, level="WARNING"):
            self.quiz_bot.handle(update())
        self.assertEqual(self.api.count("sendPoll"), 1)
        notice = self.api.calls[-1][1]["text"]
        self.assertIn("Check whether the quiz appeared", notice)
        self.reopen()
        self.quiz_bot.handle(update())
        self.assertEqual(self.api.count("getFile"), 1)
        self.assertEqual(self.api.count("sendPoll"), 1)
        # A new user submission is explicit permission for a fresh attempt.
        self.quiz_bot.handle(update(101))
        self.assertEqual(self.api.count("sendPoll"), 2)

    def test_explicit_api_rejection_does_not_retry_mutation(self):
        self.api.poll_error = bot.TelegramError(400, "Bad request")
        with self.assertLogs(bot.LOG, level="WARNING"):
            self.quiz_bot.handle(update())
        self.quiz_bot.handle(update())
        self.assertEqual(self.api.count("sendPoll"), 1)
        self.assertIn("code 400", self.api.calls[-1][1]["text"])


if __name__ == "__main__":
    unittest.main()
