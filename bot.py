"""Private JSON -> Telegram quiz bot. Python 3.12+, standard library only."""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid

from telegraph_pages import PageError, TelegraphPublisher, validate_page

MAX_JSON_BYTES = 256 * 1024
MAX_DOCUMENT_BYTES = 128 * 1024
PAGE_MARKER = "{{telegraph.url}}"
PAGE_LINK = re.compile(r"\[([^\[\]\n]+)\]\(\{\{telegraph\.url\}\}\)")
# Telegram may choose a random update_id after seven days without updates.
# Reset slightly early so a 25-second long poll cannot cross that boundary.
IDLE_OFFSET_RESET_SECONDS = 7 * 24 * 60 * 60 - 60
LOG = logging.getLogger("quiz_bot")
HELP = (
    "Send a JSON file containing one quiz. I will create a Telegram quiz with "
    "the correct answer and an optional explanation under the 💡 button.\n"
    "Required: version: 1, question, options, correct_option. "
    "Answer numbers start at 1.\n"
    "Optional: explanation (up to 200 visible characters), description (up to 1024), "
    "telegraph: {title, text} to publish an article, explanation_document: {filename, text} for an attachment. "
    "Place {{telegraph.url}} in any quiz text field to insert the article URL. "
    "In explanation and description, use [Your link label]({{telegraph.url}}) for a labelled link. "
    "These two fields always use Telegram MarkdownV2; no mode setting is needed. "
    "The article will be publicly accessible by URL. "
    "Quiz and article content can be in any language and is not translated. "
    "You choose what each field says. Forward the finished quiz to a channel or Saved Messages."
)


class ValidationError(ValueError):
    pass


class TelegramError(Exception):
    def __init__(self, code: int, description: str, retry_after: int = 0):
        self.code = code
        self.description = description
        self.retry_after = retry_after
        super().__init__(description)


class NetworkError(Exception):
    """Outcome of a sending request may be unknown; never retry it blindly."""


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("JSON contains duplicate keys.")
        result[key] = value
    return result


def _constant(_):
    raise ValidationError("NaN and Infinity are not allowed in JSON.")


def _text(value, name, maximum, *, empty=False):
    if not isinstance(value, str):
        raise ValidationError(f"{name}: a string is required.")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValidationError(f"{name}: invalid Unicode.") from None
    if "\x00" in value or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise ValidationError(f"{name}: unsupported control characters.")
    if not empty and not value.strip():
        raise ValidationError(f"{name}: the string must not be empty.")
    if len(value) > maximum:
        raise ValidationError(f"{name}: maximum {maximum} characters; received {len(value)}.")
    return value


def render_text(value, name, maximum, *, page_url=None, empty=False):
    """Substitute the URL in fields that Telegram defines as plain text."""
    _text(value, name, MAX_JSON_BYTES, empty=empty)
    if re.search(r"\{\{\s*telegraph", value.replace(PAGE_MARKER, "")):
        raise ValidationError(f"{name}: unknown Telegraph placeholder. Use {PAGE_MARKER}.")
    if PAGE_LINK.search(value):
        raise ValidationError(f"{name}: Telegram supports labelled links only in description and explanation.")
    return _text(value.replace(PAGE_MARKER, page_url or PAGE_MARKER), name, maximum, empty=empty)


def rendered_fields(quiz, page_url=None):
    values, entities = {}, {}
    values["question"] = render_text(quiz.get("question"), "question", 300, page_url=page_url)
    options = quiz.get("options")
    if not isinstance(options, list) or not 1 <= len(options) <= 12:
        raise ValidationError("options: provide 1 to 12 answers.")
    values["options"] = [render_text(option, f"options[{index}]", 100, page_url=page_url)
                         for index, option in enumerate(options, 1)]
    if len({o.strip().casefold() for o in values["options"]}) != len(options):
        raise ValidationError("Answer options must be distinct.")
    for key, limit in (("explanation", 200), ("description", 1024)):
        if key in quiz:
            text = _text(quiz[key], key, MAX_JSON_BYTES, empty=True)
            if re.search(r"\{\{\s*telegraph", text.replace(PAGE_MARKER, "")):
                raise ValidationError(f"{key}: unknown Telegraph placeholder.")
            # MarkdownV2 permits escaping any ASCII punctuation, including in
            # link targets. This works for both bare and labelled URL markers.
            replacement = re.sub(r"([_\*\[\]\(\)~`>#+\-=|{}.!\\])", r"\\\1", page_url) if page_url else PAGE_MARKER
            values[key] = text.replace(PAGE_MARKER, replacement)
            entities[key + "_parse_mode"] = "MarkdownV2"
            # Telegram parses the full MarkdownV2 grammar and checks the
            # resulting visible length (200/1024) and explanation line feeds.
    return values, entities


def parse_quiz(raw: bytes) -> dict:
    if len(raw) > MAX_JSON_BYTES:
        raise ValidationError("The JSON file must not exceed 256 KiB.")
    try:
        quiz = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object_pairs,
                          parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValidationError("Send valid UTF-8 JSON without ``` code fences.") from None
    if not isinstance(quiz, dict):
        raise ValidationError("The JSON root must be one object containing one quiz.")
    allowed = {"version", "question", "options", "correct_option", "explanation",
               "description", "explanation_document", "telegraph"}
    if set(quiz) - allowed:
        raise ValidationError("JSON contains unknown fields. Check the format specification.")
    if type(quiz.get("version")) is not int or quiz["version"] != 1:
        raise ValidationError("Set version to 1.")
    rendered_fields(quiz)
    options = quiz["options"]
    correct = quiz.get("correct_option")
    if type(correct) is not int or not 1 <= correct <= len(options):
        raise ValidationError("correct_option: provide an existing answer number, starting at 1.")
    texts = [quiz["question"], *options, quiz.get("description", ""), quiz.get("explanation", "")]
    has_marker = any(PAGE_MARKER in text for text in texts)
    if has_marker != ("telegraph" in quiz):
        raise ValidationError("Publishing requires telegraph: {title, text} and {{telegraph.url}} in at least one quiz text field.")
    if "telegraph" in quiz:
        try:
            validate_page(quiz["telegraph"])
        except PageError as exc:
            raise ValidationError(str(exc)) from None
    if "explanation_document" in quiz:
        document = quiz["explanation_document"]
        if not isinstance(document, dict) or set(document) != {"filename", "text"}:
            raise ValidationError("explanation_document must contain only filename and text.")
        filename = document["filename"]
        if (not isinstance(filename, str) or not 1 <= len(filename) <= 80
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(?:md|txt)", filename)
                or ".." in filename):
            raise ValidationError("filename: use a portable ASCII .md or .txt filename, without a path, up to 80 characters.")
        _text(document["text"], "explanation_document.text", MAX_DOCUMENT_BYTES)
        if len(document["text"].encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValidationError("Document text must not exceed 128 KiB in UTF-8.")
    return quiz


def poll_request(quiz: dict, chat_id: int, page_url=None):
    if "telegraph" in quiz and not page_url:
        raise ValidationError("Publish the Telegraph article first.")
    if page_url is not None and not re.fullmatch(r"https://telegra\.ph/[A-Za-z0-9_%~.-]+", page_url):
        raise ValidationError("Telegraph did not return a valid URL.")
    values, entities = rendered_fields(quiz, page_url)
    payload = {
        "chat_id": chat_id, "question": values["question"],
        "options": [{"text": option} for option in values["options"]],
        "type": "quiz", "correct_option_ids": [quiz["correct_option"] - 1],
        "is_anonymous": True, "allows_multiple_answers": False,
        "allows_revoting": True, "shuffle_options": False,
        "protect_content": False,
    }
    for key in ("explanation", "description"):
        if key in values:
            payload[key] = values[key]
    payload.update(entities)
    files = {}
    if "explanation_document" in quiz:
        document = quiz["explanation_document"]
        payload["explanation_media"] = {"type": "document", "media": "attach://explanation_file"}
        files["explanation_file"] = (document["filename"], document["text"].encode("utf-8"),
                                     "text/plain; charset=utf-8")
    return payload, files


def multipart(payload, files):
    boundary = "quiz-" + uuid.uuid4().hex
    parts = []
    for name, value in payload.items():
        encoded = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                      f'\r\n\r\n{encoded}\r\n').encode("utf-8"))
    for name, (filename, data, content_type) in files.items():
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                      f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n').encode("ascii"))
        parts.extend((data, b"\r\n"))
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class Telegram:
    def __init__(self, token: str):
        self._token = token
        # API endpoints should never redirect a token-bearing request elsewhere.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self._opener = urllib.request.build_opener(NoRedirect())

    def call(self, method, payload=None, files=None):
        payload = payload or {}
        if files:
            body, content_type = multipart(payload, files)
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}", data=body,
            headers={"Content-Type": content_type}, method="POST")
        try:
            try:
                with self._opener.open(request, timeout=45) as response:
                    raw = response.read(4 * 1024 * 1024)
            except urllib.error.HTTPError as exc:
                with exc:
                    raw = exc.read(64 * 1024)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Unexpected response")
        except (OSError, ValueError, urllib.error.URLError):
            raise NetworkError("No response from Telegram; the sending outcome may be unknown.") from None
        if not result.get("ok"):
            description = str(result.get("description", "Telegram error")).replace(self._token, "[hidden]")
            raise TelegramError(int(result.get("error_code", 0)), description[:400],
                                int(result.get("parameters", {}).get("retry_after", 0)))
        return result["result"]

    def download(self, file_id):
        info = self.call("getFile", {"file_id": file_id})
        if info.get("file_size", 0) > MAX_JSON_BYTES:
            raise ValidationError("The JSON file must not exceed 256 KiB.")
        path = info.get("file_path", "")
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or ".." in path or path.startswith("/"):
            raise NetworkError("Telegram did not return a valid file path.")
        try:
            with self._opener.open(f"https://api.telegram.org/file/bot{self._token}/{path}", timeout=30) as response:
                raw = response.read(MAX_JSON_BYTES + 1)
        except (OSError, urllib.error.URLError):
            raise NetworkError("Could not download the file from Telegram.") from None
        if len(raw) > MAX_JSON_BYTES:
            raise ValidationError("The JSON file must not exceed 256 KiB.")
        return raw


class State:
    def __init__(self, directory, *, clock=None):
        self._clock = clock if clock is not None else time.time
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(Path(directory) / "state.sqlite3"))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS cursor (singleton INTEGER PRIMARY KEY CHECK(singleton=1), next_id INTEGER NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, timestamp REAL NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('last_reset', ?)", (self._clock(),))
        self.db.commit()

    @property
    def offset(self):
        row = self.db.execute("SELECT next_id FROM cursor WHERE singleton=1").fetchone()
        return row[0] if row else 0

    def claim(self, update_id):
        with self.db:
            added = self.db.execute("INSERT OR IGNORE INTO updates VALUES (?, 'processing')", (update_id,)).rowcount
            self.db.execute("INSERT INTO cursor VALUES (1, ?) ON CONFLICT(singleton) DO UPDATE SET next_id=MAX(next_id, excluded.next_id)", (update_id + 1,))
            self.db.execute("INSERT INTO metadata VALUES ('last_received', ?) ON CONFLICT(key) DO UPDATE SET timestamp=excluded.timestamp", (self._clock(),))
        return bool(added)

    def prepare_poll(self):
        """Forget stale offsets before Telegram can start a new ID sequence.

        Telegram retains pending updates for only 24 hours. Completed records
        older than this idle interval cannot protect a legitimate redelivery,
        and retaining them could suppress a new randomly reused update ID.
        Processing records survive for the startup interruption warning.
        """
        now = self._clock()
        last_activity = self.db.execute(
            "SELECT MAX(timestamp) FROM metadata WHERE key IN ('last_received', 'last_reset')"
        ).fetchone()[0]
        if now - last_activity < IDLE_OFFSET_RESET_SECONDS:
            return False
        with self.db:
            self.db.execute("INSERT INTO cursor VALUES (1, 0) ON CONFLICT(singleton) DO UPDATE SET next_id=0")
            self.db.execute("DELETE FROM updates WHERE status != 'processing'")
            self.db.execute("INSERT INTO metadata VALUES ('last_reset', ?) ON CONFLICT(key) DO UPDATE SET timestamp=excluded.timestamp", (now,))
        return True

    def finish(self, update_id, status="done"):
        with self.db:
            self.db.execute("UPDATE updates SET status=? WHERE id=?", (status, update_id))
            self.db.execute("DELETE FROM updates WHERE status != 'processing' AND id < ?", (update_id - 10000,))

    def interrupted(self):
        return [r[0] for r in self.db.execute("SELECT id FROM updates WHERE status='processing'")]

    def close(self):
        self.db.close()


class QuizBot:
    def __init__(self, api, owner_id, state, publisher=None):
        self.api, self.owner_id, self.state = api, owner_id, state
        self.publisher = publisher

    def tell(self, chat_id, text):
        return self.api.call("sendMessage", {"chat_id": chat_id, "text": text})

    def process(self, update):
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat", {})
        sender = message.get("from", {})
        if chat.get("type") != "private" or sender.get("is_bot"):
            return
        user_id, chat_id = sender.get("id"), chat.get("id")
        if type(user_id) is not int or chat_id != user_id:
            return
        words = message.get("text", "").split(maxsplit=1)
        command = words[0].split("@")[0] if words else ""
        if command == "/whoami":
            self.tell(chat_id, f"Your Telegram ID: {user_id}")
            return
        if user_id != self.owner_id:
            return
        if command in ("/start", "/help"):
            self.tell(chat_id, HELP)
            return
        document = message.get("document")
        if not isinstance(document, dict):
            self.tell(chat_id, "Send your quiz as a .json file. Format: /help")
            return
        if not str(document.get("file_name", "")).lower().endswith(".json"):
            self.tell(chat_id, "A file with the .json extension is required.")
            return
        if document.get("file_size", 0) > MAX_JSON_BYTES:
            self.tell(chat_id, "The JSON file must not exceed 256 KiB.")
            return
        raw = self.api.download(document["file_id"])
        quiz = parse_quiz(raw)
        page_url = None
        if "telegraph" in quiz:
            if self.publisher is None:
                raise PageError("Telegraph publishing has not been configured on the server.")
            page_url = self.publisher.publish(quiz["telegraph"])
        try:
            payload, files = poll_request(quiz, chat_id, page_url)
        except ValidationError as exc:
            if page_url:
                raise ValidationError(f"{exc} The article is already published: {page_url}. Fix the quiz field and send the file again; the same article will be reused.") from None
            raise
        self.api.call("sendPoll", payload, files=files)

    def handle(self, update):
        update_id = update["update_id"]
        if not self.state.claim(update_id):
            return
        status = "done"
        try:
            self.process(update)
        except ValidationError as exc:
            status = "invalid"
            self.safe_notice(f"Quiz not created: {exc}")
        except PageError as exc:
            status = "page_error"
            self.safe_notice(f"Quiz not created: {exc}")
        except TelegramError as exc:
            status = "api_error"
            LOG.warning("Telegram rejected update %s (code %s)", update_id, exc.code)
            self.safe_notice(f"Telegram rejected the request (code {exc.code}): {exc.description}\n"
                             "Fix the issue and send the JSON again.")
        except NetworkError:
            status = "uncertain"
            LOG.warning("Network error for update %s; no automatic resend", update_id)
            self.safe_notice("The connection to Telegram was interrupted. Check whether the quiz appeared. "
                             "If it did not, send the JSON again. Automatic retries are disabled to avoid duplicates.")
        except Exception:
            status = "internal_error"
            # Never log raw requests, private quiz contents, or token-bearing URLs.
            LOG.error("Internal processing error for update %s", update_id)
            self.safe_notice("Processing could not be completed. Check whether the quiz appeared before sending the JSON again.")
        finally:
            self.state.finish(update_id, status)

    def safe_notice(self, text):
        try:
            self.tell(self.owner_id, text)
        except (TelegramError, NetworkError):
            LOG.warning("Could not deliver status notice to owner")


def configuration():
    token = os.environ.get("BOT_TOKEN", "").strip()
    token_file = os.environ.get("BOT_TOKEN_FILE", "").strip()
    if token and token_file:
        raise ValueError("Set either BOT_TOKEN or BOT_TOKEN_FILE, not both.")
    if token_file:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("Set BOT_TOKEN or BOT_TOKEN_FILE using your token from @BotFather.")
    owner = os.environ.get("OWNER_USER_ID", "")
    if not owner.isascii() or not owner.isdigit() or int(owner) <= 0:
        raise ValueError("Set OWNER_USER_ID to the owner's positive numeric Telegram ID.")
    return token, int(owner), os.environ.get("STATE_DIR", "data")


def run():
    token, owner, directory = configuration()
    api = Telegram(token)
    me = api.call("getMe")
    if api.call("getWebhookInfo").get("url"):
        raise ValueError("This bot has a webhook. Use a separate bot or explicitly remove the existing webhook first.")
    state = State(directory)
    default_page_token = (Path(os.environ["BOT_TOKEN_FILE"]).with_name("telegraph-token.txt")
                          if os.environ.get("BOT_TOKEN_FILE") else Path(directory) / "telegraph-token.txt")
    publisher = TelegraphPublisher(Path(os.environ.get("TELEGRAPH_TOKEN_FILE", str(default_page_token))), state.db)
    bot = QuizBot(api, owner, state, publisher)
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    LOG.info("Bot @%s started; private owner-only quizzes", me.get("username", "unknown"))
    try:
        interrupted = state.interrupted()
        if interrupted:
            bot.safe_notice("The bot restarted during processing. Check your latest quizzes. "
                            "Resend any JSON that did not produce a quiz; interrupted requests are not duplicated automatically.")
            for update_id in interrupted:
                state.finish(update_id, "interrupted")
        delay = 1
        while not stopped.is_set():
            try:
                state.prepare_poll()
                updates = api.call("getUpdates", {"offset": state.offset, "timeout": 25,
                                                  "limit": 20, "allowed_updates": ["message"]})
                delay = 1
                for update in updates:
                    if stopped.is_set():
                        break
                    bot.handle(update)
            except TelegramError as exc:
                if exc.code in (401, 403, 409):
                    raise ValueError(f"Polling stopped (Telegram {exc.code}). Check the token and ensure only one bot instance is running.") from None
                LOG.warning("Polling rejected (code %s); retrying", exc.code)
                stopped.wait(max(delay, min(exc.retry_after, 3600)))
                delay = min(delay * 2, 60)
            except NetworkError:
                LOG.warning("Polling connection interrupted; retrying")
                stopped.wait(delay)
                delay = min(delay * 2, 60)
    finally:
        state.close()


def main():
    parser = argparse.ArgumentParser(description="Create Telegram quizzes from JSON documents.")
    parser.add_argument("--check", metavar="FILE", help="Validate one JSON locally without a token or network")
    args = parser.parse_args()
    handlers = None
    if os.environ.get("LOG_FILE"):
        handlers = [RotatingFileHandler(os.environ["LOG_FILE"], maxBytes=2 * 1024 * 1024,
                                        backupCount=3, encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers)
    try:
        if args.check:
            with Path(args.check).open("rb") as stream:
                quiz = parse_quiz(stream.read(MAX_JSON_BYTES + 1))
            print(f"OK: {len(quiz['options'])} options; correct answer #{quiz['correct_option']}.")
        else:
            run()
        return 0
    except (ValueError, OSError, TelegramError, NetworkError) as exc:
        if isinstance(exc, TelegramError):
            LOG.error("Telegram startup error (code %s)", exc.code)
        elif isinstance(exc, OSError):
            LOG.error("Cannot read configuration or write state directory")
        else:
            LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
