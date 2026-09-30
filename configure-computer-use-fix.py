#!/usr/bin/env python3
"""Install or remove the Computer Use adapter from an existing WSL MCP config.

Dry run by default. Requires Python 3.11+ and the installed Computer Use runtime.
Does not install plugins, create app approvals, or change the Codex agent engine.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime, timezone


MARKER = "codex-fixes-computer-use-path-proxy-v1"
PROJECT_MARKER = b"codex-fixes-project-path-proxy-v1"
ROOT = Path(__file__).resolve().parent
MANIFEST = "computer-use-install.json"
HEADER = re.compile(r"^\s*\[([^\[\]\n]+)\]\s*(?:#.*)?$")
ANY_HEADER = re.compile(r"^\s*\[\[?[^\n]+\]\]?\s*(?:#.*)?$")


def wslpath(value: str, flag: str) -> str:
    return subprocess.run(
        ["/usr/bin/wslpath", flag, value], capture_output=True, text=True,
        check=True, timeout=5,
    ).stdout.rstrip("\r\n")


def linux_path(value: str) -> Path:
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\"):
        return Path(wslpath(value, "-u"))
    return Path(value).expanduser().absolute()


def header_path(line: str) -> list[str] | None:
    match = HEADER.fullmatch(line.rstrip("\r\n"))
    if not match:
        return None
    try:
        value = tomllib.loads("[" + match.group(1) + "]")
        result = []
        while isinstance(value, dict) and len(value) == 1:
            name, value = next(iter(value.items()))
            result.append(name)
        return result
    except tomllib.TOMLDecodeError:
        return None


def toml_value(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(map(toml_value, value)) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{toml_value(key)} = {toml_value(item)}"
            for key, item in value.items()
        ) + " }"
    raise ValueError("Unsupported value in the node_repl configuration")


def replace_server(text: str, server: dict | None) -> str:
    """Replace only explicit node_repl tables; verify every other setting."""
    before = tomllib.loads(text)
    lines = text.splitlines(keepends=True)
    output = []
    skipping = False
    found = False
    for line in lines:
        if ANY_HEADER.fullmatch(line.rstrip("\r\n")):
            path = header_path(line)
            skipping = bool(path and path[:2] == ["mcp_servers", "node_repl"])
            found |= skipping
        if not skipping:
            output.append(line)
    if before.get("mcp_servers", {}).get("node_repl") is not None and not found:
        raise ValueError("Use explicit [mcp_servers.node_repl] tables; inline declarations are not supported")
    result = "".join(output)
    if server is not None:
        result = result.rstrip("\r\n") + "\n\n[mcp_servers.node_repl]\n"
        result += "".join(f"{toml_value(key)} = {toml_value(value)}\n" for key, value in server.items())
    after = tomllib.loads(result)
    expected = deepcopy(before)
    if server is None:
        expected.get("mcp_servers", {}).pop("node_repl", None)
        if expected.get("mcp_servers") == {}:
            expected.pop("mcp_servers")
        if after.get("mcp_servers") == {}:
            after.pop("mcp_servers")
    else:
        expected.setdefault("mcp_servers", {})["node_repl"] = server
    if after != expected:
        raise ValueError("Refusing a config edit that would change unrelated settings")
    return result


def runtime_command(server: dict) -> list[str]:
    command = server.get("command")
    arguments = server.get("args", [])
    if not isinstance(command, str) or not isinstance(arguments, list) or not all(isinstance(a, str) for a in arguments):
        raise ValueError("node_repl must have a command and a string args array")
    if Path(command.replace("\\", "/")).name.lower() == "node_repl.exe":
        return [os.fspath(linux_path(command)), *arguments]
    if (arguments and Path(arguments[0]).name == "node-repl-path-proxy.py"
            and arguments[1:2] == ["--"] and len(arguments) >= 3
            and Path(arguments[2].replace("\\", "/")).name.lower() == "node_repl.exe"):
        return [os.fspath(linux_path(arguments[2])), *arguments[3:]]
    raise ValueError("The existing node_repl command is not a recognized Windows runtime launch")


def powershell(code: str) -> str:
    executable = Path(shutil.which("powershell.exe") or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    result = subprocess.run(
        [os.fspath(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", code],
        capture_output=True, text=True, check=True, timeout=20,
    )
    return result.stdout.strip().lstrip("\ufeff")


def installed_native_cli() -> Path:
    raw = powershell(
        "$ErrorActionPreference='Stop'; $p=@(Get-AppxPackage -Name OpenAI.Codex); "
        "if($p.Count -ne 1){throw 'Expected one installed OpenAI.Codex package'}; "
        "Join-Path $p[0].InstallLocation 'app\\resources\\codex.exe' | ConvertTo-Json -Compress"
    )
    return linux_path(json.loads(raw))


def executable_digest(path: Path) -> str:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError(f"Expected a Windows executable: {path}")
        stream.seek(0)
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require_desktop_closed() -> None:
    # The OpenAI.Codex package can use either desktop executable name.
    if powershell("if(Get-Process -Name Codex,ChatGPT -ErrorAction SilentlyContinue){'running'}") == "running":
        raise ValueError("Fully quit Codex, including the tray process, then run --apply from a separate WSL terminal")


def atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def backup(config: Path, directory: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = directory / "computer-use-backups" / stamp
    target.mkdir(parents=True, mode=0o700)
    atomic_write(target / "config.toml", config.read_bytes())
    return target


def install(config: Path, directory: Path, native_cli: Path | None, apply: bool,
            wsl_proxy: Path | None = None) -> None:
    text = config.read_text(encoding="utf-8")
    parsed = tomllib.loads(text)
    server = parsed.get("mcp_servers", {}).get("node_repl")
    if not isinstance(server, dict):
        raise ValueError("No existing [mcp_servers.node_repl] launch configuration; this installer preserves the runtime environment and will not invent one")
    command = runtime_command(server)
    if not Path(command[0]).is_file():
        raise ValueError("The configured Windows node_repl.exe does not exist; refresh the installed plugin runtime first")
    source = ROOT / "node-repl-path-proxy.py"
    adapter = directory / source.name
    if source.resolve() == adapter.resolve():
        raise ValueError("Choose a permanent install directory outside the repository")
    if MARKER not in source.read_text(encoding="utf-8"):
        raise ValueError("The source adapter is missing its ownership marker")
    if adapter.exists() and MARKER not in adapter.read_text(encoding="utf-8"):
        raise ValueError("The install directory contains an unowned adapter; choose a different --install-dir")
    updated = deepcopy(server)
    updated["command"] = "/usr/bin/python3"
    updated["args"] = [os.fspath(adapter), "--", *command]
    env = updated.get("env", {})
    if not isinstance(env, dict):
        raise ValueError("The node_repl env setting must be a table")
    selector = env.get("CODEX_CLI_PATH", os.environ.get("CODEX_CLI_PATH", ""))
    if not isinstance(selector, str):
        raise ValueError("The runtime CODEX_CLI_PATH must be a string")
    if wsl_proxy is not None:
        explicit = wslpath(os.fspath(wsl_proxy), "-w")
        if selector and selector.replace("/", "\\").casefold() != explicit.replace("/", "\\").casefold():
            raise ValueError("The existing runtime CODEX_CLI_PATH points somewhere else; it was not overridden")
        selector = explicit
        updated.setdefault("env", {})["CODEX_CLI_PATH"] = selector
    settings = None
    native_source = None
    native_destination = None
    if selector:
        selector_path = linux_path(selector)
        if selector_path.name == "codex-app-server-proxy":
            if PROJECT_MARKER not in selector_path.read_bytes()[:4096]:
                raise ValueError("CODEX_CLI_PATH does not identify this repository's project-path fix")
            native_source = native_cli or installed_native_cli()
            digest = executable_digest(native_source)
            native_destination = directory / "native-runtime" / digest / "codex.exe"
            windows_cli = wslpath(os.fspath(native_destination), "-w")
            if not re.fullmatch(r"[A-Za-z]:\\.*\\codex\.exe", windows_cli, flags=re.I):
                raise ValueError("Use a Windows-mounted --install-dir for the native bridge CLI")
            settings = {
                "wsl_proxy_path": selector,
                "native_cli_path": windows_cli,
                "native_cli_sha256": digest,
            }
    rewritten = replace_server(text, updated)
    print("Config:", config)
    print("Adapter:", adapter)
    print("Windows node_repl:", command[0])
    print("Child bridge:", native_source if settings else "existing runtime environment (cwd repair only)")
    print("Codex agent: unchanged; app approvals and browser policy: unchanged")
    if not apply:
        print("DRY RUN: no files changed. Add --apply after fully quitting Codex.")
        return
    require_desktop_closed()
    manifest_path = directory / MANIFEST
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if previous and (previous.get("marker") != MARKER or previous.get("config") != os.fspath(config)):
        raise ValueError("The install manifest belongs to a different configuration")
    if previous and server != previous.get("installed_server"):
        raise ValueError("node_repl was changed since installation; remove or reconcile that override first")
    saved = backup(config, directory)
    print("Backup:", saved)
    for name in (source.name, "node-repl-native-cli.json", MANIFEST):
        existing = directory / name
        if existing.is_file():
            atomic_write(saved / name, existing.read_bytes())
    atomic_write(adapter, source.read_bytes(), 0o755)
    if settings:
        native_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(native_source, native_destination)
        if executable_digest(native_destination) != settings["native_cli_sha256"]:
            raise ValueError("Native bridge copy failed its SHA-256 check; the config was not changed")
        atomic_write(directory / "node-repl-native-cli.json", (json.dumps(settings, indent=2) + "\n").encode())
    elif (directory / "node-repl-native-cli.json").exists():
        raise ValueError("Unexpected native bridge settings in the install directory; config was not changed")
    record = {
        "marker": MARKER, "config": os.fspath(config), "backup": os.fspath(saved),
        "original_server": previous["original_server"] if previous else server,
        "installed_server": updated,
    }
    if config.read_text(encoding="utf-8") != text:
        raise ValueError("The config changed during installation; the override was not applied")
    try:
        atomic_write(manifest_path, (json.dumps(record, indent=2) + "\n").encode())
        atomic_write(config, rewritten.encode(), config.stat().st_mode & 0o777)
    except OSError:
        prior_manifest = saved / MANIFEST
        if prior_manifest.is_file():
            atomic_write(manifest_path, prior_manifest.read_bytes())
        else:
            manifest_path.unlink(missing_ok=True)
        raise
    print("Applied. Backup:", saved)
    print("Reopen Codex with its WSL agent. Re-run this installer after runtime/package updates.")


def remove(config: Path, directory: Path, apply: bool) -> None:
    record = json.loads((directory / MANIFEST).read_text())
    if record.get("marker") != MARKER or record.get("config") != os.fspath(config):
        raise ValueError("The install manifest does not belong to this configuration")
    text = config.read_text(encoding="utf-8")
    current = tomllib.loads(text).get("mcp_servers", {}).get("node_repl")
    if current != record.get("installed_server"):
        raise ValueError("node_repl was changed after installation; refusing to overwrite it")
    rewritten = replace_server(text, record["original_server"])
    print("Restore the original node_repl launch in:", config)
    print("Keep the separate WSL project-path fix and unrelated configuration.")
    if not apply:
        print("DRY RUN: no files changed. Add --apply after fully quitting Codex.")
        return
    require_desktop_closed()
    saved = backup(config, directory)
    if config.read_text(encoding="utf-8") != text:
        raise ValueError("The config changed during removal; the original launch was not restored")
    atomic_write(config, rewritten.encode(), config.stat().st_mode & 0o777)
    atomic_write(saved / MANIFEST, (directory / MANIFEST).read_bytes())
    (directory / MANIFEST).unlink()
    # Keep inactive adapter/native files for recovery; no process will launch them.
    print("Removed the config override. Backup:", saved)
    print("Reopen Codex. Inactive adapter files remain in", directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "remove"))
    parser.add_argument("--config", type=linux_path,
                        default=os.fspath(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"))
    parser.add_argument("--install-dir", type=linux_path, required=True,
                        help="Permanent Windows-mounted directory, preferably separate from the project-path fix")
    parser.add_argument("--native-cli", type=linux_path,
                        help="Installed Windows codex.exe; otherwise discovered from Get-AppxPackage when needed")
    parser.add_argument("--wsl-proxy", type=linux_path,
                        help="Installed project-path proxy; scopes a matching selector to the node_repl child when it is not already present")
    parser.add_argument("--apply", action="store_true", help="Write after fully quitting Codex; default is a dry run")
    args = parser.parse_args(argv)
    try:
        if not os.environ.get("WSL_DISTRO_NAME"):
            raise ValueError("Run this configuration script inside the WSL distribution used by Codex")
        if args.action == "install":
            install(args.config.absolute(), args.install_dir.absolute(), args.native_cli, args.apply, args.wsl_proxy)
        else:
            remove(args.config.absolute(), args.install_dir.absolute(), args.apply)
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"Computer Use fix: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
