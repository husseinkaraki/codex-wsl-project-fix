#!/usr/bin/env python3
"""Reload Desktop MCP connections after a chat's permissions change.

This uses the bundled WSL app-server's supported management request. It does
not change permissions, restart the agent, or operate the Computer Use helper.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import uuid


@dataclass(frozen=True)
class Server:
    pid: int
    executable: str
    start_ticks: str
    stdin_pipe: str


def command(path: Path) -> list[str]:
    return [value.decode() for value in (path / 'cmdline').read_bytes().split(b'\0') if value]


def process_identity(path: Path) -> tuple[int, str]:
    # The comm field can contain spaces or parentheses.
    fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
    return int(fields[1]), fields[19]


def server_identity(pid: int, proc_root: Path = Path('/proc')) -> Server:
    path = proc_root / str(pid)
    argv = command(path)
    executable = os.readlink(path / 'exe')
    if ('app-server' not in argv or not argv or argv[0] != executable
            or not executable.endswith('/codex') or '/bin/wsl/' not in executable):
        raise ValueError('The adapter parent is not the bundled WSL app-server')
    with (path / 'exe').open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            raise ValueError('The app-server is not a Linux executable')
    pipe = os.readlink(path / 'fd/0')
    if not pipe.startswith('pipe:[') or not pipe.endswith(']'):
        raise ValueError('The app-server does not have the expected stdio control pipe')
    return Server(pid, executable, process_identity(path)[1], pipe)


def find_server(adapter: Path, pid: int | None = None,
                proc_root: Path = Path('/proc')) -> Server:
    adapter = adapter.resolve(strict=True)
    if not adapter.is_file() or adapter.name != 'node-repl-path-proxy.py':
        raise ValueError('--adapter must identify the installed node-repl-path-proxy.py')
    parents = set()
    for path in proc_root.iterdir():
        if not path.name.isdigit():
            continue
        try:
            argv = command(path)
            if (not argv or not Path(argv[0]).name.startswith('python3')
                    or '--' not in argv):
                continue
            separator = argv.index('--')
            script = [value for value in argv[1:separator] if not value.startswith('-')]
            if (len(script) != 1 or not os.path.samefile(script[0], adapter)
                    or len(argv) <= separator + 1
                    or Path(argv[separator + 1]).name != 'node_repl.exe'):
                continue
            parent = process_identity(path)[0]
            if pid is None or parent == pid:
                parents.add(parent)
        except (OSError, ValueError, IndexError, UnicodeError):
            continue
    if len(parents) != 1:
        raise ValueError(f'Expected one running WSL app-server for this adapter; found {sorted(parents)}. '
                         'If several match, select one with --server-pid.')
    return server_identity(parents.pop(), proc_root)


def request_reload(server: Server, proc_root: Path = Path('/proc')) -> str:
    if server_identity(server.pid, proc_root) != server:
        raise ValueError('The app-server identity changed; no request was sent')
    request_id = 'computer-use-permission-refresh-' + uuid.uuid4().hex
    packet = (json.dumps({'id': request_id, 'method': 'config/mcpServer/reload', 'params': None},
                         separators=(',', ':')) + '\n').encode()
    descriptor = os.open(proc_root / str(server.pid) / 'fd/0', os.O_WRONLY | os.O_NONBLOCK)
    try:
        # Recheck after opening, including the process start time and pipe identity.
        if server_identity(server.pid, proc_root) != server:
            raise ValueError('The app-server identity changed; no request was sent')
        if len(packet) > os.fpathconf(descriptor, 'PC_PIPE_BUF'):
            raise ValueError('The reload request exceeds the atomic pipe write limit')
        if os.write(descriptor, packet) != len(packet):
            raise RuntimeError('The reload request was not written completely')
    finally:
        os.close(descriptor)
    return request_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', type=Path, required=True,
                        help='WSL path to the installed node-repl-path-proxy.py')
    parser.add_argument('--server-pid', type=int, help='Select a specific matched WSL app-server')
    parser.add_argument('--apply', action='store_true', help='Send the reload request; otherwise preview only')
    args = parser.parse_args(argv)
    try:
        server = find_server(args.adapter, args.server_pid)
        print(f'WSL app-server: {server.pid}')
        print('Engine, chat permissions, app approvals, and browser policy: unchanged')
        if not args.apply:
            print('DRY RUN: no reload requested. Add --apply to refresh MCP connections.')
            return 0
        request_id = request_reload(server)
        print(f'MCP connection reload requested: {request_id}')
        print('Retry in the chat where you selected Full Access. Browser input still needs live verification.')
        return 0
    except (OSError, ValueError, RuntimeError, IndexError, UnicodeError) as error:
        print(f'Cannot refresh Computer Use: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
