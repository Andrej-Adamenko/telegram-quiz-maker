"""Safe Markdown -> public Telegraph pages, with durable duplicate protection.

Only createPage is used. Account provisioning and uncertain-outcome recovery are
operator actions; this module never retries a possibly successful publication.
"""
from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

MAX_CONTENT_BYTES = 64 * 1024
API_URL = "https://api.telegra.ph/createPage"
_UNCERTAIN = (
    "The Telegraph publication outcome is unknown. Automatic retries are blocked "
    "to avoid duplicates. Check the Telegraph account's pages before retrying."
)


class PageError(Exception):
    """Safe user-facing error: never includes remote responses or credentials."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _node(tag, children):
    return {"tag": tag, "children": children}


def _safe_link(url):
    if not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        return False
    if "\\" in url:
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        return (parsed.scheme.lower() in {"https", "http"} and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and parsed.port != 0)
    except ValueError:
        return False


# Raw HTML and Markdown images stay literal text. Only this small set of inline
# constructs creates elements; neither input tags nor attributes are copied.
_INLINE = re.compile(
    r"(?P<escape>\\[\\`*_\[\]()#!>+.-])"
    r"|(?P<code>`[^`\n]+`)"
    r"|(?P<image>!\[[^\]\n]*\]\((?:[^()\n]|\([^()\n]*\))*\))"
    r"|(?P<link>\[[^\]\n]+\]\((?:[^()\n]|\([^()\n]*\))*\))"
    r"|(?P<strong>\*\*(?=\S)(?:[^\n]*?\S)?\*\*|__(?=\S)(?:[^\n]*?\S)?__)"
    r"|(?P<em>\*(?=\S)[^*\n]*?\S\*|_(?=\S)[^_\n]*?\S_)"
)
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?$")
_LIST = re.compile(r"^ {0,3}([-+*]|[0-9]{1,9}[.)])[ \t]+(.*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[^`~]*$")
_RULE = re.compile(r"^ {0,3}(?:\*[ \t]*){3,}$|^ {0,3}(?:-[ \t]*){3,}$|^ {0,3}(?:_[ \t]*){3,}$")


def _inline(text, depth=0):
    if depth >= 12:
        return [text]
    output = []
    position = 0
    for match in _INLINE.finditer(text):
        if match.start() > position:
            output.append(text[position:match.start()])
        value = match.group()
        kind = match.lastgroup
        if kind == "escape":
            output.append(value[1:])
        elif kind == "code":
            output.append(_node("code", [value[1:-1]]))
        elif kind == "strong":
            output.append(_node("strong", _inline(value[2:-2], depth + 1)))
        elif kind == "em":
            output.append(_node("em", _inline(value[1:-1], depth + 1)))
        elif kind == "link":
            label, url = value[1:-1].split("](", 1)
            if _safe_link(url):
                output.append({"tag": "a", "attrs": {"href": url},
                               "children": _inline(label, depth + 1)})
            else:
                output.append(value)
        else:
            output.append(value)
        position = match.end()
    if position < len(text):
        output.append(text[position:])
    return output


def _paragraph(lines):
    children = []
    for index, line in enumerate(lines):
        children.extend(_inline(line.rstrip()))
        if index < len(lines) - 1:
            children.append({"tag": "br"} if line.endswith("  ") else " ")
    return _node("p", children)


def _blocks(lines, depth=0):
    output = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = _FENCE.match(line)
        heading = _HEADING.match(line)
        item = _LIST.match(line)
        if fence:
            marker = fence[1]
            index += 1
            code = []
            closing = re.compile(r"^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}[ \t]*$")
            while index < len(lines) and not closing.match(lines[index]):
                code.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            output.append(_node("pre", [_node("code", ["\n".join(code)])]))
        elif heading:
            output.append(_node("h3" if len(heading[1]) <= 2 else "h4", _inline(heading[2])))
            index += 1
        elif _RULE.match(line):
            output.append({"tag": "hr"})
            index += 1
        elif item:
            tag = "ol" if item[1][0].isdigit() else "ul"
            items = []
            while index < len(lines):
                item = _LIST.match(lines[index])
                if not item or ("ol" if item[1][0].isdigit() else "ul") != tag:
                    break
                items.append(_node("li", _inline(item[2])))
                index += 1
            output.append(_node(tag, items))
        elif line.lstrip().startswith(">") and depth < 8:
            quote = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quote.append(re.sub(r"^\s*> ?", "", lines[index], count=1))
                index += 1
            output.append(_node("blockquote", _blocks(quote, depth + 1)))
        else:
            paragraph = [line]
            index += 1
            while index < len(lines) and lines[index].strip():
                following = lines[index]
                if (_HEADING.match(following) or _FENCE.match(following)
                        or _LIST.match(following) or _RULE.match(following)
                        or following.lstrip().startswith(">") and depth < 8):
                    break
                paragraph.append(following)
                index += 1
            output.append(_paragraph(paragraph))
    return output


def validate_page(page):
    """Validate exactly {title, text}; return Telegraph DOM nodes, without I/O.

    Supported Markdown: paragraphs, headings, flat lists, quotes, fenced/inline
    code, emphasis, and HTTP(S) links. Other syntax is preserved as literal text.
    The 64 KiB limit applies to serialized UTF-8 content, as in Telegraph's API.
    """
    if not isinstance(page, dict) or set(page) != {"title", "text"}:
        raise PageError("telegraph must contain only title and text.")
    for field in ("title", "text"):
        value = page[field]
        if not isinstance(value, str) or not value.strip():
            raise PageError(f"telegraph.{field}: a non-empty string is required.")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise PageError(f"telegraph.{field}: invalid Unicode.") from None
        if any(ord(c) < 32 and c not in "\r\n\t" for c in value) or "\x7f" in value:
            raise PageError(f"telegraph.{field}: unsupported control characters.")
    if len(page["title"]) > 256:
        raise PageError("telegraph.title: maximum 256 characters.")
    text = page["text"].replace("\r\n", "\n").replace("\r", "\n")
    content = _blocks(text.split("\n"))
    if len(_json(content).encode("utf-8")) > MAX_CONTENT_BYTES:
        raise PageError("The formatted Telegraph article must not exceed 64 KiB in UTF-8.")
    return content


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Refused(Exception):
    pass


def _page_url(value):
    if not isinstance(value, str):
        return False
    return (value not in {"https://telegra.ph/.", "https://telegra.ph/.."}
            and bool(re.fullmatch(r"https://telegra\.ph/(?:[A-Za-z0-9._~-]|%[0-9a-fA-F]{2})+", value)))


def _rollback(db):
    try:
        db.rollback()
    except sqlite3.Error:
        pass


class TelegraphPublisher:
    def __init__(self, token_file: Path, db: sqlite3.Connection):
        self.token_file = Path(token_file)
        self.db = db
        self._opener = urllib.request.build_opener(_NoRedirect())
        try:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS telegraph_pages ("
                "content_hash TEXT PRIMARY KEY, status TEXT NOT NULL "
                "CHECK (status IN ('pending', 'published')), url TEXT, "
                "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            self.db.commit()
        except sqlite3.Error:
            _rollback(self.db)
            raise PageError("Could not open the local Telegraph publication log.") from None

    def _token(self):
        try:
            with self.token_file.open("rb") as handle:
                raw = handle.read(1025)
            token = raw.decode("utf-8").strip()
            if (not token or len(raw) > 1024
                    or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in token)):
                raise ValueError()
            return token
        except (OSError, UnicodeError, ValueError):
            raise PageError("Telegraph access is not configured. Check the token file on the server.") from None

    def _create(self, title, content, token):
        body = urllib.parse.urlencode({"access_token": token, "title": title,
                                       "content": _json(content), "return_content": "false"}).encode("ascii")
        request = urllib.request.Request(API_URL, data=body, method="POST", headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
        try:
            try:
                with self._opener.open(request, timeout=45) as response:
                    raw = response.read(128 * 1024 + 1)
            except urllib.error.HTTPError as exc:
                with exc:
                    if 300 <= exc.code < 400:
                        raise ValueError()
                    raw = exc.read(128 * 1024 + 1)
            if len(raw) > 128 * 1024:
                raise ValueError()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            if result.get("ok") is False and isinstance(result.get("error"), str):
                raise _Refused()
            if result.get("ok") is not True or not isinstance(result.get("result"), dict):
                raise ValueError()
            url = result["result"].get("url")
            if not _page_url(url):
                raise ValueError()
            return url
        except (OSError, ValueError, RecursionError, urllib.error.URLError, http.client.HTTPException):
            raise PageError(_UNCERTAIN) from None

    def publish(self, page):
        content = validate_page(page)
        digest = hashlib.sha256(_json({"title": page["title"], "content": content}).encode("utf-8")).hexdigest()
        try:
            row = self.db.execute("SELECT status, url FROM telegraph_pages WHERE content_hash = ?", (digest,)).fetchone()
            if row:
                if row[0] == "published" and _page_url(row[1]):
                    return row[1]
                raise PageError(_UNCERTAIN)
        except sqlite3.Error:
            raise PageError("Could not check the Telegraph publication log.") from None
        # Configuration failures happen before the durable claim, so fixing a
        # missing token does not require an operator to resolve a false pending.
        token = self._token()
        try:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO telegraph_pages (content_hash, status) VALUES (?, 'pending')", (digest,))
            claimed = cursor.rowcount == 1
            self.db.commit()
        except sqlite3.Error:
            _rollback(self.db)
            raise PageError("Could not record the start of the Telegraph publication; the article was not sent.") from None
        if not claimed:
            # Another publisher may have finished between our lookup and claim.
            try:
                row = self.db.execute("SELECT status, url FROM telegraph_pages WHERE content_hash = ?", (digest,)).fetchone()
                if row and row[0] == "published" and _page_url(row[1]):
                    return row[1]
            except sqlite3.Error:
                pass
            raise PageError(_UNCERTAIN)
        try:
            url = self._create(page["title"], content, token)
        except _Refused:
            try:
                self.db.execute("DELETE FROM telegraph_pages WHERE content_hash = ? AND status = 'pending'", (digest,))
                self.db.commit()
            except sqlite3.Error:
                _rollback(self.db)
                raise PageError("Telegraph rejected the article, but the log could not be updated. Check the server before retrying.") from None
            raise PageError("Telegraph rejected the publication. Check the account settings and article, then retry.") from None
        try:
            self.db.execute("UPDATE telegraph_pages SET status = 'published', url = ? WHERE content_hash = ?", (url, digest))
            self.db.commit()
        except sqlite3.Error:
            _rollback(self.db)
            raise PageError("The article was published, but its link could not be saved. Automatic retries are blocked; check the Telegraph account.") from None
        return url
