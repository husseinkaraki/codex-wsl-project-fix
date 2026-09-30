#!/usr/bin/env python3
"""Translate a WSL cwd for Windows node_repl without changing permissions.

Temporary compatibility adapter for Windows-drive and WSL working directories.
Only disabled (unrestricted) permission profiles are supported. Restricted or
unknown profiles pass through unchanged, preserving their existing validation.
All non-target JSONL messages and all runtime output pass through byte-for-byte.
"""
# codex-fixes-computer-use-path-proxy-v1

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
from urllib.parse import quote, unquote, urlsplit

META_KEY = 'codex/sandbox-state-meta'


def child_environment(environment: dict[str, str], config: dict) -> dict[str, str]:
    """Give the Windows runtime a native CLI; leave the outer WSL selector intact."""
    if not isinstance(config, dict):
        raise ValueError('Native runtime settings must be a JSON object')
    inherited = environment.get('CODEX_CLI_PATH', '')
    expected = config.get('wsl_proxy_path')
    native = config.get('native_cli_path')
    digest = config.get('native_cli_sha256')
    normalize = lambda value: value.replace('/', '\\').casefold()
    if (not isinstance(expected, str) or not isinstance(native, str)
            or not isinstance(digest, str) or not inherited
            or normalize(inherited) != normalize(expected)):
        return environment.copy()
    if not re.fullmatch(r'[A-Za-z]:\\.*\\codex\.exe', native, flags=re.IGNORECASE):
        raise ValueError('Configured native CLI must be an absolute Windows codex.exe path')
    translated = subprocess.run(['/usr/bin/wslpath', '-u', native],
        capture_output=True, text=True, timeout=5, check=True).stdout.rstrip('\r\n')
    with Path(translated).open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('Configured native CLI is not a Windows executable')
        stream.seek(0)
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != digest:
        raise ValueError('Native CLI hash differs from the installed package copy')
    result = environment.copy()
    result['CODEX_CLI_PATH'] = native
    # Desktop's shared helper retains the outer WSL launcher as its CLI.
    # Sky's built-in helper uses this runtime's verified Windows CLI instead.
    result['SKY_CUA_NATIVE_PIPE'] = '0'
    return result


def runtime_environment() -> dict[str, str]:
    settings = Path(__file__).resolve().with_name('node-repl-native-cli.json')
    environment = os.environ.copy()
    if not settings.is_file():
        return environment
    return child_environment(environment, json.loads(settings.read_text()))


def windows_file_uri(value: str) -> str:
    """Map actual, non-symlink directories to the same Windows-accessible path."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme != 'file' or parsed.netloc or parsed.query or parsed.fragment:
            return value
        path = unquote(parsed.path, errors='strict')
        if '\x00' in path or not path.startswith('/') or path.startswith('//'):
            return value
        if not Path(path).is_dir() or os.path.realpath(path) != (path.rstrip('/') or '/'):
            return value
        mapped = subprocess.run(['/usr/bin/wslpath', '-w', path],
            capture_output=True, text=True, timeout=5, check=True).stdout.rstrip('\r\n')
        drive_path = re.fullmatch(r'[a-zA-Z]:\\.*', mapped)
        wsl_path = re.fullmatch(r'\\\\(wsl\.localhost|wsl\$)\\([^\\]+)(\\.*)?',
            mapped, flags=re.IGNORECASE)
        if not drive_path and not wsl_path:
            return value
        # Confirm WSL's reverse mapping identifies the same directory.
        roundtrip = subprocess.run(['/usr/bin/wslpath', '-u', mapped],
            capture_output=True, text=True, timeout=5, check=True).stdout.rstrip('\r\n')
        if not os.path.samefile(path, roundtrip):
            return value
        if drive_path:
            return 'file:///' + quote(mapped.replace('\\', '/'), safe='/:')
        host, distro, tail = wsl_path.groups()
        uri_path = '/' + distro + (tail or '\\').replace('\\', '/')
        return 'file://' + host + quote(uri_path, safe='/')
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        return value


def rewrite_line(line: bytes, mapper=windows_file_uri) -> tuple[bytes, bool]:
    if META_KEY.encode() not in line:
        return line, False
    try:
        message = json.loads(line)
        if not isinstance(message, dict) or message.get('method') != 'tools/call':
            return line, False
        params = message.get('params')
        if not isinstance(params, dict) or params.get('name') != 'js':
            return line, False
        meta = params.get('_meta')
        if not isinstance(meta, dict):
            return line, False
        state = meta.get(META_KEY)
        if not isinstance(state, dict):
            return line, False
        # Exact shape: never silently widen or reinterpret a permission profile.
        if state.get('permissionProfile') != {'type': 'disabled'}:
            return line, False
        cwd = state.get('sandboxCwd')
        if not isinstance(cwd, str):
            return line, False
        replacement = mapper(cwd)
        if replacement == cwd:
            return line, False
        state['sandboxCwd'] = replacement
        ending = b'\r\n' if line.endswith(b'\r\n') else b'\n' if line.endswith(b'\n') else b''
        return json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode() + ending, True
    except (ValueError, UnicodeError, RecursionError):
        return line, False


def copy_stream(source, destination):
    try:
        while block := source.read(65536):
            destination.write(block)
            destination.flush()
    except (BrokenPipeError, OSError):
        pass


def run(arguments: list[str]) -> int:
    if arguments[:1] == ['--']:
        arguments = arguments[1:]
    if not arguments or Path(arguments[0]).name.lower() != 'node_repl.exe':
        print('Expected the installed Windows node_repl.exe launch command.', file=sys.stderr)
        return 2
    try:
        environment = runtime_environment()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'node-repl-path-proxy: native runtime verification failed: {error}', file=sys.stderr)
        return 2
    if environment.get('CODEX_CLI_PATH') != os.environ.get('CODEX_CLI_PATH'):
        print('node-repl-path-proxy: selected verified native Windows CLI for child runtime', file=sys.stderr, flush=True)
    process = subprocess.Popen(arguments, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, env=environment)

    def forward_input():
        try:
            for line in sys.stdin.buffer:
                rewritten, changed = rewrite_line(line)
                if changed:
                    print('node-repl-path-proxy: mapped cwd to the same Windows directory', file=sys.stderr, flush=True)
                process.stdin.write(rewritten)
                process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    output_threads = [
        threading.Thread(target=copy_stream, args=(process.stdout, sys.stdout.buffer), daemon=True),
        threading.Thread(target=copy_stream, args=(process.stderr, sys.stderr.buffer), daemon=True),
    ]
    threading.Thread(target=forward_input, daemon=True).start()
    for thread in output_threads:
        thread.start()

    def forward_signal(number, _frame):
        try:
            process.send_signal(number)
        except OSError:
            pass

    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, forward_signal)
    result = process.wait()
    for thread in output_threads:
        thread.join(timeout=2)
    return result


if __name__ == '__main__':
    raise SystemExit(run(sys.argv[1:]))
