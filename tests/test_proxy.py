from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROXY_PATH = ROOT / "codex-app-server-proxy"
LOADER = importlib.machinery.SourceFileLoader("codex_app_server_proxy", os.fspath(PROXY_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
proxy = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(proxy)


class PathTranslationTests(unittest.TestCase):
    def test_wsl_dollar_unc(self) -> None:
        self.assertEqual(
            proxy.translate_path(
                r"\\wsl$\Ubuntu\home\alice\dev\example",
                "Ubuntu",
            ),
            "/home/alice/dev/example",
        )

    def test_extended_wsl_localhost_unc(self) -> None:
        self.assertEqual(
            proxy.translate_path(
                r"\\?\UNC\wsl.localhost\Ubuntu\home\alice\dev\example",
                "Ubuntu",
            ),
            "/home/alice/dev/example",
        )

    def test_absolute_drive_path(self) -> None:
        self.assertEqual(
            proxy.translate_path(r"D:\code\example", "Ubuntu"),
            "/mnt/d/code/example",
        )

    def test_drive_relative_path_is_not_guessed(self) -> None:
        self.assertEqual(proxy.translate_path(r"D:example", "Ubuntu"), r"D:example")

    def test_linux_path_is_unchanged(self) -> None:
        value = "/home/alice/dev/example"
        self.assertEqual(proxy.translate_path(value, "Ubuntu"), value)

    def test_other_wsl_distribution_is_unchanged(self) -> None:
        value = r"\\wsl$\Debian\home\alice\dev\example"
        self.assertEqual(proxy.translate_path(value, "Ubuntu"), value)


class WireProtocolTests(unittest.TestCase):
    def test_project_roots_are_rewritten(self) -> None:
        for method in proxy.PROJECT_METHODS:
            with self.subTest(method=method):
                request = {
                    "id": 1,
                    "method": method,
                    "params": {
                        "name": "Graph Manager",
                        "roots": [
                            {"path": r"\\wsl$\Ubuntu\home\alice\dev\example"},
                            {"path": r"C:\code\shared", "label": "shared"},
                        ],
                    },
                }
                rewritten, changes = proxy.rewrite_message(request, "Ubuntu")
                self.assertEqual(changes, 2)
                self.assertEqual(
                    [root["path"] for root in rewritten["params"]["roots"]],
                    ["/home/alice/dev/example", "/mnt/c/code/shared"],
                )
                self.assertEqual(rewritten["params"]["roots"][1]["label"], "shared")
                self.assertEqual(rewritten["id"], request["id"])

    def test_unrelated_message_keeps_identical_bytes(self) -> None:
        line = b'{ "id": 2, "method": "thread/list", "params": {} }\r\n'
        rewritten, changes = proxy.rewrite_wire_line(line, "Ubuntu")
        self.assertEqual(changes, 0)
        self.assertEqual(rewritten, line)

    def test_already_correct_project_message_keeps_identical_bytes(self) -> None:
        line = b'{ "id": 3, "method": "project/create", "params": {"roots":[{"path":"/home/me/project"}]}}\n'
        rewritten, changes = proxy.rewrite_wire_line(line, "Ubuntu")
        self.assertEqual(changes, 0)
        self.assertEqual(rewritten, line)

    def test_malformed_message_keeps_identical_bytes(self) -> None:
        line = b'{"method":"project/create",not-json}\n'
        rewritten, changes = proxy.rewrite_wire_line(line, "Ubuntu")
        self.assertEqual(changes, 0)
        self.assertEqual(rewritten, line)


class ProcessProxyTests(unittest.TestCase):
    def test_proxy_rewrites_in_flight_and_removes_selector_from_child(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temporary:
            temporary = Path(raw_temporary)
            fake_codex = temporary / "fake-codex"
            fake_codex.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "if '--version' in sys.argv:\n"
                "    print('codex-cli test')\n"
                "    raise SystemExit(0)\n"
                "for line in sys.stdin.buffer:\n"
                "    value = json.loads(line)\n"
                "    value['childSawSelector'] = 'CODEX_CLI_PATH' in os.environ\n"
                "    sys.stdout.write(json.dumps(value) + '\\n')\n"
                "    sys.stdout.flush()\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            request = {
                "id": 9,
                "method": "project/import",
                "params": {
                    "roots": [
                        {"path": r"\\wsl.localhost\Ubuntu\home\alice\dev\example"}
                    ]
                },
            }
            environment = os.environ.copy()
            environment.pop("CODEX_FIXES_PROXY_ACTIVE", None)
            environment.update(
                {
                    "CODEX_CLI_PATH": os.fspath(PROXY_PATH),
                    "CODEX_FIXES_REAL_CODEX": os.fspath(fake_codex),
                    "WSL_DISTRO_NAME": "Ubuntu",
                    "XDG_STATE_HOME": os.fspath(temporary / "state"),
                }
            )
            result = subprocess.run(
                [os.fspath(PROXY_PATH), "app-server"],
                input=(json.dumps(request) + "\n").encode("utf-8"),
                capture_output=True,
                check=False,
                env=environment,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            response = json.loads(result.stdout)
            self.assertEqual(
                response["params"]["roots"][0]["path"],
                "/home/alice/dev/example",
            )
            self.assertFalse(response["childSawSelector"])

    def test_non_app_server_command_delegates_to_real_cli(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temporary:
            fake_codex = Path(raw_temporary) / "fake-codex"
            fake_codex.write_text(
                "#!/bin/sh\nprintf 'codex-cli delegated\\n'\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            environment = os.environ.copy()
            environment["CODEX_FIXES_REAL_CODEX"] = os.fspath(fake_codex)
            result = subprocess.run(
                [os.fspath(PROXY_PATH), "--version"],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "codex-cli delegated\n")


if __name__ == "__main__":
    unittest.main()
