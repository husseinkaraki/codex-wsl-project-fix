#!/usr/bin/env python3
"""Preview or install a version-guarded native Chrome capture timing workaround.

The agent remains in WSL. Native Sky handles every browser action and URL check.
Default is dry run. --apply changes two JavaScript files in the selected Sky
package; --restore restores the original facade and removes this script's shim.
After the current task is idle, reset its JavaScript session before importing
Sky again. An MCP connection reload alone does not refresh imported modules.
Verify real input in each affected chat after loading the changed module.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

EXPECTED_VERSION = '0.7.5'
EXPECTED_SHA256 = '0123da875a2eef5648fac407fcfedad5147ef9a1616b623ad615f4c337aee285'
CLIENT_REL = Path('dist/project/cua/sky_js/src/sky.js')
SHIM_NAME = 'wsl_chrome_settler.js'
SHIM_SOURCE = Path(__file__).with_name('chrome-action-settler.mjs')
BACKUP_SUFFIX = '.before-wsl-chrome-settler'
KNOWN_SHIM_SHA256 = {
    # Published two-second revision. Upgrade only this exact previous shim.
    'c6aa99aa554c465a9140af84391845dac663eb0f14d3dfbdbe98ffeb8a359730',
}
ANCHOR = b',d=o,d}'
REPLACEMENT = b',d=o,"windows"===o.target&&__codexWslInstallChromeSettler(d),d}'
IMPORT = b'import{installChromeSettler as __codexWslInstallChromeSettler}from"./wsl_chrome_settler.js";'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def patched_bytes(original):
    if digest(original) != EXPECTED_SHA256 or original.count(ANCHOR) != 1:
        raise ValueError('Unsupported Sky client bytes; no files were changed')
    return IMPORT + original.replace(ANCHOR, REPLACEMENT, 1)


def inspect(package):
    package = Path(package).resolve(strict=True)
    metadata = json.loads((package / 'package.json').read_text())
    if metadata.get('name') != '@oai/sky' or metadata.get('version') != EXPECTED_VERSION:
        raise ValueError('Only the reviewed @oai/sky 0.7.5 build is supported')
    client = package / CLIENT_REL
    if not client.resolve(strict=True).is_relative_to(package):
        raise ValueError('Client resolves outside the selected Sky package')
    backup = client.with_name(client.name + BACKUP_SUFFIX)
    shim = client.with_name(SHIM_NAME)
    shim_data = SHIM_SOURCE.read_bytes()
    current = client.read_bytes()
    original = current if digest(current) == EXPECTED_SHA256 else (
        backup.read_bytes() if backup.is_file() else b''
    )
    patched = patched_bytes(original)
    if current not in (original, patched):
        raise ValueError('Client changed since the reviewed build; refusing to overwrite it')
    if backup.exists() and backup.read_bytes() != original:
        raise ValueError('Existing backup differs from the reviewed original')
    current_shim_data = shim.read_bytes() if shim.exists() else None
    if (current_shim_data is not None and current_shim_data != shim_data
            and digest(current_shim_data) not in KNOWN_SHIM_SHA256):
        raise ValueError('Existing shim differs; refusing to overwrite or remove it')
    if current == patched and not shim.is_file():
        raise ValueError('Patched client is missing its shim')
    return {
        'client': client, 'backup': backup, 'shim': shim,
        'current': current, 'original': original, 'patched': patched,
        'shim_data': shim_data, 'current_shim_data': current_shim_data,
    }


def atomic_write(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.',
                                         suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def validate_plan(plan):
    if plan['client'].read_bytes() != plan['current']:
        raise ValueError('Client changed after inspection; no patch was applied')
    if plan['current'] == plan['patched'] and (
            not plan['backup'].is_file() or not plan['shim'].is_file()):
        raise ValueError('Installed backup or shim disappeared after inspection')
    if plan['backup'].exists() and plan['backup'].read_bytes() != plan['original']:
        raise ValueError('Backup changed after inspection; refusing to overwrite it')
    current_shim_data = plan['shim'].read_bytes() if plan['shim'].exists() else None
    if current_shim_data != plan['current_shim_data']:
        raise ValueError('Shim changed after inspection; refusing to overwrite or remove it')


def apply(plan):
    validate_plan(plan)
    if plan['current'] == plan['patched'] and plan['current_shim_data'] == plan['shim_data']:
        return 'already applied'
    if not plan['backup'].exists():
        atomic_write(plan['backup'], plan['original'])
    atomic_write(plan['shim'], plan['shim_data'])
    if plan['current'] != plan['patched']:
        atomic_write(plan['client'], plan['patched'])
    return 'applied; reset the idle JavaScript session before importing Sky, then verify it'


def restore(plan):
    validate_plan(plan)
    if plan['current'] == plan['original']:
        return 'already original'
    atomic_write(plan['client'], plan['original'])
    plan['shim'].unlink()
    return 'restored; backup retained; reset the idle JavaScript session before importing Sky'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sky-package', required=True, type=Path,
                        help='Actual native runtime node_modules/@oai/sky directory')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--restore', action='store_true')
    args = parser.parse_args(argv)
    plan = inspect(args.sky_package)
    mode = 'apply' if args.apply else 'restore' if args.restore else 'dry-run'
    print(json.dumps({
        'mode': mode,
        'client': str(plan['client']),
        'current_sha256': digest(plan['current']),
        'original_sha256': digest(plan['original']),
        'patched_sha256': digest(plan['patched']),
        'already_applied': (plan['current'] == plan['patched']
                            and plan['current_shim_data'] == plan['shim_data']),
        'shim_upgrade_required': (plan['current'] == plan['patched']
                                 and plan['current_shim_data'] != plan['shim_data']),
        'settle_ms': 2000,
        'navigation_settle_ms': 5000,
        'native_policy_changed': False,
        'engine_changed': False,
        'browser_profile_changed': False,
    }, indent=2))
    if args.apply:
        print(apply(plan))
    elif args.restore:
        print(restore(plan))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError) as error:
        print(f'Chrome timing workaround refused: {error}', file=sys.stderr)
        raise SystemExit(1)
