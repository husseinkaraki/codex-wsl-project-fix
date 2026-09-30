from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proxy = load("node_repl_path_proxy", "node-repl-path-proxy.py")
installer = load("configure_computer_use_fix", "configure-computer-use-fix.py")


def request(cwd="file:///home/alice/project", profile=None):
    return {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {
            "name": "js", "arguments": {"code": "nodeRepl.write('ok')"},
            "_meta": {
                "other": "preserve",
                proxy.META_KEY: {
                    "sandboxCwd": cwd,
                    "permissionProfile": {"type": "disabled"} if profile is None else profile,
                    "anotherField": [1, 2],
                },
            },
        },
    }


class UriTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def map_path(self, directory: Path, windows: str) -> str:
        def run(args, **kwargs):
            value = windows if args[1] == "-w" else os.fspath(directory)
            return subprocess.CompletedProcess(args, 0, value + "\n", "")
        with patch.object(proxy.subprocess, "run", side_effect=run):
            return proxy.windows_file_uri(directory.as_uri())

    def test_real_directory_maps_to_wsl_localhost_uri(self):
        self.assertEqual(
            self.map_path(self.directory, r"\\wsl.localhost\Ubuntu\home\alice\project"),
            "file://wsl.localhost/Ubuntu/home/alice/project",
        )

    def test_wsl_dollar_and_special_characters_are_encoded(self):
        directory = self.directory / "a # % 雪"
        directory.mkdir()
        self.assertEqual(
            self.map_path(directory, "\\\\wsl$\\Ubuntu\\home\\alice\\a # % 雪"),
            "file://wsl$/Ubuntu/home/alice/" + quote("a # % 雪", safe="/"),
        )

    def test_windows_drive_mapping(self):
        self.assertEqual(self.map_path(self.directory, r"C:\Users\alice\a b"), "file:///C:/Users/alice/a%20b")

    def test_different_directory_roundtrip_is_rejected(self):
        other = self.directory / "different"
        other.mkdir()
        with patch.object(proxy.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, "C:\\wrong\n", ""),
            subprocess.CompletedProcess([], 0, os.fspath(other) + "\n", ""),
        ]):
            self.assertEqual(proxy.windows_file_uri(self.directory.as_uri()), self.directory.as_uri())

    def test_symlink_is_left_unchanged(self):
        link = self.directory / "link"
        link.symlink_to(self.directory, target_is_directory=True)
        with patch.object(proxy.subprocess, "run") as run:
            self.assertEqual(proxy.windows_file_uri(link.as_uri()), link.as_uri())
            run.assert_not_called()

    def test_unsafe_or_nonlocal_uris_never_invoke_wslpath(self):
        values = [
            "https://example.com/", "file://other-host/share", "file://localhost/tmp",
            "file:///tmp#fragment", "file:///tmp?query", "file:///tmp/%00",
            "file:relative", "file:////server/share", "file:///missing-codex-fixes-test",
        ]
        with patch.object(proxy.subprocess, "run") as run:
            for value in values:
                with self.subTest(value=value):
                    self.assertEqual(proxy.windows_file_uri(value), value)
            run.assert_not_called()

    def test_mapping_failure_keeps_original(self):
        with patch.object(proxy.subprocess, "run", side_effect=subprocess.TimeoutExpired("wslpath", 5)):
            self.assertEqual(proxy.windows_file_uri(self.directory.as_uri()), self.directory.as_uri())


class MetadataTests(unittest.TestCase):
    def test_only_cwd_changes_and_line_ending_is_preserved(self):
        before = request()
        line = json.dumps(before).encode() + b"\r\n"
        expected = deepcopy(before)
        expected["params"]["_meta"][proxy.META_KEY]["sandboxCwd"] = "file://wsl.localhost/Ubuntu/home/alice/project"
        output, changed = proxy.rewrite_line(line, lambda value: expected["params"]["_meta"][proxy.META_KEY]["sandboxCwd"])
        self.assertTrue(changed)
        self.assertEqual(json.loads(output), expected)
        self.assertTrue(output.endswith(b"\r\n"))

    def test_restricted_unknown_and_extended_profiles_keep_exact_bytes(self):
        for profile in [
            {"type": "restricted"}, {"type": "workspace-write"}, {}, "disabled",
            {"type": "disabled", "extra": True},
        ]:
            with self.subTest(profile=profile):
                line = json.dumps(request(profile=profile)).encode() + b"\n"
                with patch.object(proxy, "windows_file_uri", side_effect=AssertionError("must not map")):
                    output, changed = proxy.rewrite_line(line, lambda value: self.fail("must not map"))
                self.assertFalse(changed)
                self.assertEqual(output, line)

    def test_unrelated_tool_response_malformed_and_unchanged_bytes(self):
        unrelated = request()
        unrelated["params"]["name"] = "reset"
        response = {"jsonrpc": "2.0", "id": 4, "result": {"metadata": proxy.META_KEY}}
        for line in [b"not-json\r\n", json.dumps(unrelated).encode(), json.dumps(response).encode()]:
            self.assertEqual(proxy.rewrite_line(line, lambda value: self.fail("must not map")), (line, False))
        line = json.dumps(request()).encode() + b"\n"
        self.assertEqual(proxy.rewrite_line(line, lambda value: value), (line, False))


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.native = Path(self.temporary.name) / "codex.exe"
        self.native.write_bytes(b"MZtest-installed-native-cli")
        self.settings = {
            "wsl_proxy_path": r"C:\Users\alice\CodexFixes\codex-app-server-proxy",
            "native_cli_path": r"C:\Users\alice\CodexFixes\native-runtime\codex.exe",
            "native_cli_sha256": hashlib.sha256(self.native.read_bytes()).hexdigest(),
        }

    def test_verified_cli_is_scoped_to_child_with_builtin_transport(self):
        environment = {
            "CODEX_CLI_PATH": self.settings["wsl_proxy_path"],
            "SKY_CUA_NATIVE_PIPE": "1", "CODEX_HOME": r"C:\Users\alice\.codex",
        }
        original = environment.copy()
        with patch.object(proxy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, os.fspath(self.native), "")):
            result = proxy.child_environment(environment, self.settings)
        self.assertEqual(environment, original)
        self.assertEqual(result["CODEX_CLI_PATH"], self.settings["native_cli_path"])
        self.assertEqual(result["SKY_CUA_NATIVE_PIPE"], "0")
        self.assertEqual(result["CODEX_HOME"], original["CODEX_HOME"])

    def test_unrecognized_or_absent_selector_is_not_overridden(self):
        for environment in [{}, {"CODEX_CLI_PATH": r"C:\other\codex.exe", "SKY_CUA_NATIVE_PIPE": "1"}]:
            with self.subTest(environment=environment), patch.object(proxy.subprocess, "run") as run:
                self.assertEqual(proxy.child_environment(environment, self.settings), environment)
                run.assert_not_called()

    def test_hash_and_executable_header_mismatch_are_rejected(self):
        environment = {"CODEX_CLI_PATH": self.settings["wsl_proxy_path"]}
        with patch.object(proxy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, os.fspath(self.native), "")):
            for content in [b"MZchanged", b"#!/bin/sh"]:
                self.native.write_bytes(content)
                with self.subTest(content=content), self.assertRaises(ValueError):
                    proxy.child_environment(environment, self.settings)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.server = {
            "command": "/mnt/c/runtime/bin/node_repl.exe", "args": ["--keep-arg"],
            "startup_timeout_sec": 120,
            "env": {"NODE_REPL_TRUSTED_SERVICES": '{"sky":"@oai/sky/service"}', "EXISTING": "preserve"},
        }
        self.text = (
            '# existing comment\nsandbox_mode = "workspace-write"\n'
            '[computer_use.windows]\nalways_allowed_app_ids = ["example.exe"]\n'
            '[mcp_servers.node_repl]\ncommand = "/mnt/c/runtime/bin/node_repl.exe"\n'
            'args = [\n "--keep-arg",\n]\nstartup_timeout_sec = 120\n'
            '[mcp_servers.node_repl.env]\nNODE_REPL_TRUSTED_SERVICES = \'{"sky":"@oai/sky/service"}\'\nEXISTING = "preserve"\n'
            '[mcp_servers.other]\ncommand = "other"\n# keep this too\n'
        )

    def test_replacement_preserves_other_servers_permissions_and_approvals(self):
        replacement = deepcopy(self.server)
        replacement.update(command="/usr/bin/python3", args=["/mnt/c/adapter.py", "--", self.server["command"], "--keep-arg"])
        result = installer.replace_server(self.text, replacement)
        parsed = installer.tomllib.loads(result)
        self.assertEqual(parsed["sandbox_mode"], "workspace-write")
        self.assertEqual(parsed["computer_use"]["windows"]["always_allowed_app_ids"], ["example.exe"])
        self.assertEqual(parsed["mcp_servers"]["other"], {"command": "other"})
        self.assertEqual(parsed["mcp_servers"]["node_repl"], replacement)
        self.assertIn("# existing comment", result)
        self.assertIn("# keep this too", result)

    def test_quoted_table_name_is_supported(self):
        text = self.text.replace("mcp_servers.node_repl", 'mcp_servers."node_repl"')
        self.assertEqual(installer.tomllib.loads(installer.replace_server(text, self.server))["mcp_servers"]["node_repl"], self.server)

    def test_inline_server_declaration_is_refused(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            installer.replace_server('[mcp_servers]\nnode_repl = {command="runtime"}\n', self.server)

    def test_windows_and_existing_adapter_commands_are_unwrapped(self):
        with patch.object(installer, "linux_path", side_effect=lambda value: Path("/mnt/c/runtime/node_repl.exe")):
            self.assertEqual(installer.runtime_command({"command": r"C:\runtime\node_repl.exe", "args": ["one"]}), ["/mnt/c/runtime/node_repl.exe", "one"])
            self.assertEqual(installer.runtime_command({"command": "/usr/bin/python3", "args": ["/mnt/c/fix/node-repl-path-proxy.py", "--", "/mnt/c/runtime/node_repl.exe", "one"]}), ["/mnt/c/runtime/node_repl.exe", "one"])

    def test_custom_launch_and_missing_environment_are_not_guessed(self):
        with self.assertRaises(ValueError):
            installer.runtime_command({"command": "arbitrary-launcher", "args": []})
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_text('sandbox_mode="workspace-write"\n')
            with self.assertRaisesRegex(ValueError, "No existing"):
                installer.install(config, Path(temporary) / "fix", None, False)
            self.assertEqual(config.read_text(), 'sandbox_mode="workspace-write"\n')


class InstallerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "config.toml"
        self.runtime = self.root / "node_repl.exe"
        self.runtime.write_bytes(b"MZtest-runtime")
        self.directory = self.root / "fix"
        self.original = 'sandbox_mode = "workspace-write"\n[mcp_servers.node_repl]\ncommand = ' + json.dumps(os.fspath(self.runtime)) + '\nargs = []\nenv = { EXISTING = "keep" }\n'
        self.config.write_text(self.original)

    def test_dry_run_writes_nothing(self):
        with patch.dict(os.environ, {}, clear=True):
            installer.install(self.config, self.directory, None, False)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse(self.directory.exists())

    def test_running_desktop_blocks_before_any_mutation(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(installer, "powershell", return_value="running") as ps:
            with self.assertRaisesRegex(ValueError, "Fully quit"):
                installer.install(self.config, self.directory, None, True)
            self.assertIn("Codex,ChatGPT", ps.call_args.args[0])
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse(self.directory.exists())

    def test_apply_reapply_and_remove_preserve_original_and_later_unrelated_edits(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(installer, "require_desktop_closed"):
            installer.install(self.config, self.directory, None, True)
            manifest = json.loads((self.directory / installer.MANIFEST).read_text())
            self.assertEqual((Path(manifest["backup"]) / "config.toml").read_text(), self.original)
            installer.install(self.config, self.directory, None, True)
            self.config.write_text(self.config.read_text() + '\n[unrelated]\nvalue = "added later"\n')
            installer.remove(self.config, self.directory, True)
        parsed = installer.tomllib.loads(self.config.read_text())
        self.assertEqual(parsed["mcp_servers"]["node_repl"]["command"], os.fspath(self.runtime))
        self.assertEqual(parsed["mcp_servers"]["node_repl"]["env"], {"EXISTING": "keep"})
        self.assertEqual(parsed["sandbox_mode"], "workspace-write")
        self.assertEqual(parsed["unrelated"]["value"], "added later")
        self.assertFalse((self.directory / installer.MANIFEST).exists())

    def test_remove_refuses_to_overwrite_later_node_repl_edits(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(installer, "require_desktop_closed"):
            installer.install(self.config, self.directory, None, True)
            self.config.write_text(self.config.read_text().replace('"EXISTING" = "keep"', '"EXISTING" = "changed"'))
            with self.assertRaisesRegex(ValueError, "changed after"):
                installer.remove(self.config, self.directory, True)

    def test_failed_config_write_leaves_original_config_and_no_install_manifest(self):
        original_write = installer.atomic_write
        def write(path, content, mode=0o600):
            if path == self.config:
                raise PermissionError("test config write failure")
            original_write(path, content, mode)
        with patch.dict(os.environ, {}, clear=True), patch.object(installer, "require_desktop_closed"), patch.object(installer, "atomic_write", side_effect=write):
            with self.assertRaises(PermissionError):
                installer.install(self.config, self.directory, None, True)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse((self.directory / installer.MANIFEST).exists())

    def test_native_bridge_is_copied_hashed_and_environment_is_preserved(self):
        project_proxy = self.root / "codex-app-server-proxy"
        project_proxy.write_bytes(installer.PROJECT_MARKER)
        native = self.root / "codex.exe"
        native.write_bytes(b"MZtest-installed-cli")
        server = installer.tomllib.loads(self.original)["mcp_servers"]["node_repl"]
        server["env"]["CODEX_CLI_PATH"] = r"C:\fix\codex-app-server-proxy"
        self.config.write_text(installer.replace_server(self.original, server))
        def mapped(value, flag):
            if flag == "-u":
                return os.fspath(project_proxy)
            return "C:\\fix\\native-runtime\\codex.exe"
        with patch.dict(os.environ, {}, clear=True), patch.object(installer, "require_desktop_closed"), patch.object(installer, "wslpath", side_effect=mapped):
            installer.install(self.config, self.directory, native, True)
        settings = json.loads((self.directory / "node-repl-native-cli.json").read_text())
        digest = hashlib.sha256(native.read_bytes()).hexdigest()
        self.assertEqual(settings["native_cli_sha256"], digest)
        self.assertEqual((self.directory / "native-runtime" / digest / "codex.exe").read_bytes(), native.read_bytes())
        self.assertEqual(installer.tomllib.loads(self.config.read_text())["mcp_servers"]["node_repl"]["env"], server["env"])

    def test_explicit_project_proxy_selector_is_scoped_to_node_repl(self):
        project_proxy = self.root / "codex-app-server-proxy"
        project_proxy.write_bytes(installer.PROJECT_MARKER)
        native = self.root / "codex.exe"
        native.write_bytes(b"MZtest-installed-cli")
        def mapped(value, flag):
            if flag == "-u":
                return os.fspath(project_proxy)
            if value == os.fspath(project_proxy):
                return r"C:\fix\codex-app-server-proxy"
            return r"C:\fix\native-runtime\codex.exe"
        with patch.dict(os.environ, {}, clear=True), patch.object(installer, "require_desktop_closed"), patch.object(installer, "wslpath", side_effect=mapped):
            installer.install(self.config, self.directory, native, True, project_proxy)
            self.assertNotIn("CODEX_CLI_PATH", os.environ)
        env = installer.tomllib.loads(self.config.read_text())["mcp_servers"]["node_repl"]["env"]
        self.assertEqual(env, {"EXISTING": "keep", "CODEX_CLI_PATH": r"C:\fix\codex-app-server-proxy"})

    def test_explicit_project_proxy_does_not_replace_an_unknown_selector(self):
        with patch.dict(os.environ, {"CODEX_CLI_PATH": r"C:\custom\codex.exe"}, clear=True), patch.object(installer, "wslpath", return_value=r"C:\fix\codex-app-server-proxy"):
            with self.assertRaisesRegex(ValueError, "somewhere else"):
                installer.install(self.config, self.directory, None, False, self.root / "codex-app-server-proxy")
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse(self.directory.exists())


class ProcessTests(unittest.TestCase):
    def test_stdio_unchanged_and_child_exit_status_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "node_repl.exe"
            runtime.write_text(
                '#!/usr/bin/env python3\nimport sys\n'
                'for line in sys.stdin.buffer:\n sys.stdout.buffer.write(line); sys.stdout.buffer.flush()\n'
                'sys.stderr.write("runtime diagnostic\\n")\nraise SystemExit(7)\n'
            )
            runtime.chmod(0o755)
            payload = b'{ "id": 1, "method": "initialize" }\r\n'
            result = subprocess.run([sys.executable, "-B", os.fspath(ROOT / "node-repl-path-proxy.py"), "--", os.fspath(runtime)],
                                    input=payload, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout, payload)
            self.assertEqual(result.stderr, b"runtime diagnostic\n")


if __name__ == "__main__":
    unittest.main()
