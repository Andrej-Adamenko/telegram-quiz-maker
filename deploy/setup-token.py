"""Masked local token entry; no token in local files, arguments, output, or logs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading

REMOTE_SCRIPT = "/opt/telegram-quiz-bot/current/deploy/configure.py"
ERRORS = {
    "invalid_input": "Check the token and the owner's numeric Telegram ID.",
    "telegram_rejected": "Telegram rejected the token. Check it with @BotFather.",
    "telegram_unreachable": "The server could not reach Telegram. The token was not saved.",
    "invalid_bot": "Telegram did not confirm a valid bot. The token was not saved.",
    "webhook_exists": "This bot already has a webhook. Create a separate quiz bot.",
    "already_configured": "Server settings already exist and were left unchanged. Check the server before retrying.",
    "service_not_active": "Settings were saved, but the service is not running. Check the service logs.",
    "ssh_failed": "Setup over SSH failed. Check the destination, key, and pinned host key.",
    "timeout": "The request timed out. Setup may have completed; check the server before retrying.",
    "setup_failed": "Setup did not complete. Check the server before retrying.",
}


def safe_result(value):
    """Allowlist the only fields that may reach UI or optional status files."""
    if not isinstance(value, dict) or type(value.get("ok")) is not bool:
        return {"ok": False, "code": "setup_failed"}
    code = value.get("code")
    if code not in {*ERRORS, "service_active"}:
        code = "setup_failed"
    result = {"ok": value["ok"] and code == "service_active", "code": code}
    username = value.get("username")
    if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{5,64}", username):
        result["username"] = username
    if type(value.get("bot_id")) is int and value["bot_id"] > 0:
        result["bot_id"] = value["bot_id"]
    for key in ("configured", "quiz_tested"):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    return result


def provision(token, owner, *, server, identity_file, known_hosts_file, port=22):
    ssh = shutil.which("ssh")
    if ssh is None:
        return {"ok": False, "code": "ssh_failed"}
    known_hosts_option = f'UserKnownHostsFile="{Path(known_hosts_file).as_posix()}"'
    command = [ssh, "-T", "-p", str(port), "-i", str(identity_file),
               "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", known_hosts_option,
               "-o", "ConnectTimeout=15", server, "python3", REMOTE_SCRIPT]
    try:
        completed = subprocess.run(command,
            input=json.dumps({"token": token, "owner_user_id": owner}).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=180, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if len(completed.stdout) > 16384:
            return {"ok": False, "code": "setup_failed"}
        if completed.returncode == 255:
            return {"ok": False, "code": "ssh_failed"}
        return safe_result(json.loads(completed.stdout))
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "timeout"}
    except Exception:
        return {"ok": False, "code": "setup_failed"}


def write_status(path, result):
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".quiz-status-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(safe_result(result), stream, ensure_ascii=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", help="SSH destination as user@hostname (required for setup)")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    parser.add_argument("--identity-file", type=Path, help="Existing SSH private key (required for setup)")
    parser.add_argument("--known-hosts-file", type=Path,
                        help="Existing file with a verified, pinned SSH host key (required for setup)")
    parser.add_argument("--owner-user-id", help="Positive numeric Telegram ID allowed to create quizzes")
    parser.add_argument("--status-file", type=Path, help="Optional JSON result without secrets")
    parser.add_argument("--check", action="store_true", help="Validate imports/configuration without showing a window")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.check:
        return args
    required = ("server", "identity_file", "known_hosts_file", "owner_user_id")
    missing = ["--" + field.replace("_", "-") for field in required if getattr(args, field) is None]
    if missing:
        parser.error("setup requires " + ", ".join(missing))
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*", args.server):
        parser.error("--server must be user@hostname, using a hostname or IPv4 address")
    owner = args.owner_user_id
    if (not owner.isascii() or not owner.isdigit() or len(owner) > 19
            or not 0 < int(owner) < 2**63):
        parser.error("--owner-user-id must be a positive numeric Telegram ID")
    for field in ("identity_file", "known_hosts_file"):
        path = getattr(args, field).expanduser().resolve()
        if not path.is_file() or any(character in str(path) for character in ('"', '\r', '\n')):
            parser.error("--" + field.replace("_", "-") + " must be an existing file with a valid path")
        setattr(args, field, path)
    return args


def main():
    args = parse_arguments()
    if args.check:
        if os.name == "nt":
            import tkinter  # noqa: F401 - validate the bundled Windows GUI runtime.
        assert safe_result({"ok": False, "code": "setup_failed", "token": "secret"}) == {"ok": False, "code": "setup_failed"}
        print("GUI imports and safe result validation: OK")
        return

    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("Telegram Quiz Bot Setup")
    root.geometry("600x490")
    root.minsize(580, 460)
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Set up a dedicated quiz bot", font=("Segoe UI", 15, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Create a new bot with /newbot in @BotFather.\n"
              "Paste its token here. Use a token dedicated to this bot.\n"
              f"The token is sent over SSH to {args.server}, port {args.port},\n"
              "and stored only on that server.",
              wraplength=540).pack(anchor="w", pady=(10, 16))
    ttk.Label(frame, text="New bot token").pack(anchor="w")
    token_entry = ttk.Entry(frame, show="●", font=("Segoe UI", 11))
    token_entry.pack(fill="x", pady=(4, 12))
    ttk.Label(frame, text="Owner's Telegram ID (allowed to create quizzes)").pack(anchor="w")
    owner_entry = ttk.Entry(frame)
    owner_entry.insert(0, args.owner_user_id)
    owner_entry.pack(fill="x", pady=(4, 12))
    status = tk.StringVar(value="The token is sent over SSH only and is not saved on this computer.")
    status_label = ttk.Label(frame, textvariable=status, wraplength=540)
    status_label.pack(anchor="w", pady=12)
    results = queue.Queue()
    busy = False

    def worker(token, owner):
        result = provision(token, owner, server=args.server, port=args.port,
                           identity_file=args.identity_file, known_hosts_file=args.known_hosts_file)
        try:
            write_status(args.status_file, result)
        except Exception:
            pass  # Status file is optional; never expose an exception or secret.
        results.put(result)

    def submit():
        nonlocal busy
        token = token_entry.get().strip()
        owner = owner_entry.get().strip()
        if (not re.fullmatch(r"[1-9][0-9]{4,19}:[A-Za-z0-9_-]{20,150}", token)
                or not owner.isascii() or not owner.isdigit() or len(owner) > 19
                or not 0 < int(owner) < 2**63):
            messagebox.showerror("Check your details", ERRORS["invalid_input"], parent=root)
            return
        token_entry.delete(0, "end")
        token_entry.configure(state="disabled")
        owner_entry.configure(state="disabled")
        button.configure(state="disabled")
        busy = True
        status.set("Checking the token and starting the service...\nThis usually takes a few seconds, or up to three minutes if delayed.")
        threading.Thread(target=worker, args=(token, int(owner)), daemon=True).start()

    button = ttk.Button(frame, text="Verify and start", command=submit)
    button.pack(anchor="w", pady=4)

    def poll():
        nonlocal busy
        try:
            result = results.get_nowait()
        except queue.Empty:
            root.after(200, poll)
            return
        busy = False
        if result["ok"]:
            username = result.get("username", "")
            status.set(f"Service started: @{username}\nOpen https://t.me/{username}, press Start, and send a JSON file.\n"
                       "Quiz creation still needs to be checked with the first file.")
            button.configure(text="Close", command=root.destroy, state="normal")
        else:
            status.set(ERRORS.get(result["code"], ERRORS["setup_failed"]))
            token_entry.configure(state="normal")
            owner_entry.configure(state="normal")
            button.configure(state="normal")
            root.after(200, poll)

    def close():
        if busy:
            messagebox.showinfo("Setup in progress", "Wait for the result before closing setup.", parent=root)
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(200, poll)
    token_entry.focus_set()
    root.mainloop()


if __name__ == "__main__":
    main()
