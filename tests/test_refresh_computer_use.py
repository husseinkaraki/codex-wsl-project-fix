from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('refresh_computer_use', ROOT / 'refresh-computer-use.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.adapter = self.root / 'node-repl-path-proxy.py'
        self.adapter.write_text('installed adapter')
        self.executable = self.root / '.codex/bin/wsl/version/codex'
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b'\x7fELFbundled-app-server')
        self.make_server(42)
        self.make_process(43, 42, ['/usr/bin/python3', '-B', str(self.adapter), '--', '/mnt/c/runtime/node_repl.exe'])

    def make_process(self, pid, parent, argv, start='100'):
        path = self.proc / str(pid)
        path.mkdir(exist_ok=True)
        (path / 'cmdline').write_bytes(b'\0'.join(arg.encode() for arg in argv) + b'\0')
        fields = ['S', str(parent)] + ['0'] * 17 + [start]
        (path / 'stat').write_text(f'{pid} (process name) ' + ' '.join(fields))
        return path

    def make_server(self, pid):
        path = self.make_process(pid, 1, [str(self.executable), 'app-server'])
        (path / 'exe').symlink_to(self.executable)
        (path / 'fd').mkdir()
        (path / 'fd/0').symlink_to('pipe:[123]')

    def test_selects_verified_parent_and_accepts_python_flags(self):
        server = module.find_server(self.adapter, proc_root=self.proc)
        self.assertEqual(server.pid, 42)
        self.assertEqual(server.start_ticks, '100')

    def test_unrelated_adapter_process_is_ignored(self):
        self.make_process(44, 999, ['/usr/bin/python3', '/tmp/other.py', '--', '/runtime/node_repl.exe'])
        self.assertEqual(module.find_server(self.adapter, proc_root=self.proc).pid, 42)

    def test_multiple_servers_require_explicit_selection(self):
        self.make_server(45)
        self.make_process(46, 45, ['/usr/bin/python3', str(self.adapter), '--', '/runtime/node_repl.exe'])
        with self.assertRaisesRegex(ValueError, 'Expected one'):
            module.find_server(self.adapter, proc_root=self.proc)
        self.assertEqual(module.find_server(self.adapter, 45, self.proc).pid, 45)

    def test_windows_binary_or_unexpected_stdin_is_rejected(self):
        self.executable.write_bytes(b'MZnative-windows-cli')
        with self.assertRaisesRegex(ValueError, 'Linux executable'):
            module.find_server(self.adapter, proc_root=self.proc)
        self.executable.write_bytes(b'\x7fELFbundled-app-server')
        stdin = self.proc / '42/fd/0'
        stdin.unlink()
        stdin.symlink_to('/dev/pts/1')
        with self.assertRaisesRegex(ValueError, 'stdio control pipe'):
            module.find_server(self.adapter, proc_root=self.proc)

    def test_pid_reuse_prevents_any_write(self):
        server = module.find_server(self.adapter, proc_root=self.proc)
        self.make_process(42, 1, [str(self.executable), 'app-server'], start='200')
        with patch.object(module.os, 'open') as opened:
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                module.request_reload(server, self.proc)
            opened.assert_not_called()

    def test_atomic_request_changes_no_settings(self):
        server = module.find_server(self.adapter, proc_root=self.proc)
        captured = []
        with patch.object(module.os, 'open', return_value=7), \
                patch.object(module.os, 'fpathconf', return_value=4096), \
                patch.object(module.os, 'write', side_effect=lambda fd, value: captured.append(value) or len(value)), \
                patch.object(module.os, 'close') as closed:
            request_id = module.request_reload(server, self.proc)
        self.assertEqual(json.loads(captured[0]), {
            'id': request_id, 'method': 'config/mcpServer/reload', 'params': None,
        })
        self.assertTrue(captured[0].endswith(b'\n'))
        closed.assert_called_once_with(7)

    def test_dry_run_never_sends_request(self):
        server = module.find_server(self.adapter, proc_root=self.proc)
        with patch.object(module, 'find_server', return_value=server), \
                patch.object(module, 'request_reload') as requested:
            self.assertEqual(module.main(['--adapter', str(self.adapter)]), 0)
        requested.assert_not_called()


if __name__ == '__main__':
    unittest.main()
