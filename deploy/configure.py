"""One-time server setup. Secret input comes exclusively from SSH standard input."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import NetworkError, Telegram, TelegramError  # noqa: E402

CONFIG_DIR = Path("/etc/telegram-quiz-bot")
SERVICE = "telegram-quiz-bot.service"
MAX_INPUT = 4096


class SetupError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SetupError("invalid_input")
        result[key] = value
    return result


def validate_request(raw):
    if len(raw) > MAX_INPUT:
        raise SetupError("invalid_input")
    try:
        value = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise SetupError("invalid_input") from None
    if not isinstance(value, dict) or set(value) != {"token", "owner_user_id"}:
        raise SetupError("invalid_input")
    token, owner = value["token"], value["owner_user_id"]
    if (not isinstance(token, str)
            or not re.fullmatch(r"[1-9][0-9]{4,19}:[A-Za-z0-9_-]{20,150}", token)
            or type(owner) is not int or not 0 < owner < 2**63):
        raise SetupError("invalid_input")
    return token, owner


def verify_bot(token, api_factory=Telegram):
    api = api_factory(token)
    try:
        me = api.call("getMe")
        webhook = api.call("getWebhookInfo")
    except TelegramError:
        raise SetupError("telegram_rejected") from None
    except NetworkError:
        raise SetupError("telegram_unreachable") from None
    if (not isinstance(me, dict) or me.get("is_bot") is not True
            or type(me.get("id")) is not int or me["id"] <= 0
            or me["id"] != int(token.split(":", 1)[0])
            or not isinstance(me.get("username"), str)
            or not re.fullmatch(r"[A-Za-z0-9_]{5,64}", me["username"])):
        raise SetupError("invalid_bot")
    if not isinstance(webhook, dict) or not isinstance(webhook.get("url"), str):
        raise SetupError("invalid_bot")
    if webhook["url"]:
        raise SetupError("webhook_exists")
    return {"bot_id": me["id"], "username": me["username"]}


def write_new_file(path, data, mode, group_id):
    """Publish a complete root-owned file atomically, refusing existing paths."""
    descriptor, temporary = tempfile.mkstemp(prefix=".setup-", dir=path.parent)
    try:
        os.fchown(descriptor, 0, group_id)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Unlike replace(), this never overwrites.
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.unlink(temporary)


def configure(token, owner):
    import fcntl
    import grp

    if os.geteuid() != 0:
        raise SetupError("root_required")
    group_id = grp.getgrnam("quizbot").gr_gid
    CONFIG_DIR.mkdir(mode=0o750, exist_ok=True)
    info = CONFIG_DIR.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise SetupError("unsafe_config_directory")
    os.chown(CONFIG_DIR, 0, group_id)
    os.chmod(CONFIG_DIR, 0o750)
    lock_fd = os.open(CONFIG_DIR / ".setup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        token_path = CONFIG_DIR / "token.txt"
        environment_path = CONFIG_DIR / "bot.env"
        if token_path.exists() or token_path.is_symlink() or environment_path.exists() or environment_path.is_symlink():
            raise SetupError("already_configured")
        identity = verify_bot(token)
        environment = (f"BOT_TOKEN_FILE={token_path}\nOWNER_USER_ID={owner}\n"
                       "STATE_DIR=/var/lib/telegram-quiz-bot\n")
        write_new_file(token_path, (token + "\n").encode("ascii"), 0o640, group_id)
        try:
            write_new_file(environment_path, environment.encode("ascii"), 0o600, 0)
        except Exception:
            token_path.unlink()
            raise
        directory_fd = os.open(CONFIG_DIR, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        started = subprocess.run(["systemctl", "enable", "--now", SERVICE],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=45, check=False)
        if started.returncode:
            return {"ok": False, "code": "service_not_active", "configured": True, **identity}
        # Confirm that the unit survives initial startup; do not claim a quiz was tested.
        for _ in range(5):
            time.sleep(1)
            active = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=10, check=False)
            if active.returncode:
                return {"ok": False, "code": "service_not_active", "configured": True, **identity}
        return {"ok": True, "code": "service_active", "configured": True,
                "quiz_tested": False, **identity}


def main():
    os.umask(0o077)
    try:
        token, owner = validate_request(sys.stdin.buffer.read(MAX_INPUT + 1))
        result = configure(token, owner)
    except SetupError as error:
        result = {"ok": False, "code": error.code}
    except Exception:
        # Never serialize exception details: third-party errors can include tokens/URLs.
        result = {"ok": False, "code": "setup_failed"}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
