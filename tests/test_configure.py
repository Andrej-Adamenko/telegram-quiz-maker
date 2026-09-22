"""Secret handling and API gate tests; no network or credentials required."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "deploy" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


configure = load_script("configure", "configure.py")
setup = load_script("setup_token", "setup-token.py")
TOKEN = "123456:" + "x" * 35


class SetupTests(unittest.TestCase):
    def test_input_requires_only_token_and_positive_integer_owner(self):
        self.assertEqual(configure.validate_request(json.dumps({"token": TOKEN, "owner_user_id": 123456789}).encode()), (TOKEN, 123456789))
        for value in ({"token": TOKEN, "owner_user_id": True}, {"token": TOKEN, "owner_user_id": -1},
                      {"token": TOKEN, "owner_user_id": 5, "destination": "evil"},
                      {"token": TOKEN + "\nINJECTION=yes", "owner_user_id": 5}):
            with self.assertRaises(configure.SetupError):
                configure.validate_request(json.dumps(value).encode())

    def test_duplicate_keys_and_excess_input_rejected(self):
        for raw in (b'{"token":"one","token":"two","owner_user_id":4}', b" " * 4097):
            with self.assertRaises(configure.SetupError):
                configure.validate_request(raw)

    def test_existing_webhook_is_never_deleted(self):
        calls = []
        class API:
            def __init__(self, token):
                pass
            def call(self, method):
                calls.append(method)
                return ({"id": 123456, "is_bot": True, "username": "ExampleQuizBot"}
                        if method == "getMe" else {"url": "https://example.invalid/hook"})
        with self.assertRaises(configure.SetupError) as error:
            configure.verify_bot(TOKEN, API)
        self.assertEqual(error.exception.code, "webhook_exists")
        self.assertEqual(calls, ["getMe", "getWebhookInfo"])

    def test_api_identity_excludes_names_and_other_response_fields(self):
        class API:
            def __init__(self, token):
                pass
            def call(self, method):
                return ({"id": 123456, "is_bot": True, "username": "ExampleQuizBot", "secret": TOKEN}
                        if method == "getMe" else {"url": ""})
        self.assertEqual(configure.verify_bot(TOKEN, API), {"bot_id": 123456, "username": "ExampleQuizBot"})

    def test_status_allowlist_discards_secrets_and_unsanitized_code(self):
        self.assertEqual(setup.safe_result({"ok": False, "code": TOKEN, "token": TOKEN, "error": TOKEN}),
                         {"ok": False, "code": "setup_failed"})
        result = setup.safe_result({"ok": True, "code": "service_active", "username": "ExampleQuizBot", "token": TOKEN})
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertTrue(result["ok"])

    def test_ssh_token_is_only_in_stdin_and_host_key_is_required(self):
        response = subprocess.CompletedProcess([], 0, b'{"ok":true,"code":"service_active"}')
        with patch.object(setup.subprocess, "run", return_value=response) as run, \
                patch.object(setup.shutil, "which", return_value="ssh"):
            self.assertTrue(setup.provision(TOKEN, 123456789, server="root@server.example",
                                           identity_file=Path("private-key"),
                                           known_hosts_file=Path("pinned hosts"))["ok"])
        args, kwargs = run.call_args
        self.assertNotIn(TOKEN, " ".join(args[0]))
        self.assertIn("StrictHostKeyChecking=yes", args[0])
        self.assertIn('UserKnownHostsFile="pinned hosts"', args[0])
        self.assertIn("root@server.example", args[0])
        self.assertEqual(args[0][args[0].index("-p") + 1], "22")
        self.assertEqual(json.loads(kwargs["input"]), {"token": TOKEN, "owner_user_id": 123456789})
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("env", kwargs)

    def test_setup_requires_explicit_destination_files_and_owner(self):
        with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            setup.parse_arguments([])
        self.assertTrue(setup.parse_arguments(["--check"]).check)
        with tempfile.TemporaryDirectory() as directory:
            key, hosts = Path(directory) / "private-key", Path(directory) / "pinned hosts"
            key.write_text("test key placeholder")
            hosts.write_text("verified host key placeholder")
            arguments = ["--server", "root@server.example", "--identity-file", str(key),
                         "--known-hosts-file", str(hosts), "--owner-user-id", "123456789"]
            options = setup.parse_arguments(arguments)
            self.assertEqual(options.port, 22)
            self.assertEqual(options.known_hosts_file, hosts.resolve())
            self.assertEqual(options.owner_user_id, "123456789")
            for option, bad_value in (("--server", "-oProxyCommand=anything"),
                                      ("--port", "0"), ("--port", "65536"),
                                      ("--owner-user-id", "0"),
                                      ("--known-hosts-file", str(Path(directory) / "missing"))):
                invalid = arguments + [option + "=" + bad_value]
                with self.subTest(option=option, value=bad_value), \
                        patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                    setup.parse_arguments(invalid)

    def test_atomic_publish_refuses_overwrite_and_removes_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "token.txt"
            # Windows does not provide Unix ownership/mode calls; publication is portable.
            with patch.object(configure.os, "fchown", create=True), patch.object(configure.os, "fchmod", create=True):
                configure.write_new_file(target, b"original\n", 0o640, 0)
                with self.assertRaises(FileExistsError):
                    configure.write_new_file(target, b"replacement\n", 0o640, 0)
            self.assertEqual(target.read_bytes(), b"original\n")
            self.assertEqual(list(Path(directory).iterdir()), [target])


if __name__ == "__main__":
    unittest.main()
