import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('chrome_timing_patch', ROOT / 'fix-computer-use-chrome-timing.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ChromeTimingPatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.package = Path(self.temporary.name) / 'sky'
        self.package.mkdir()
        (self.package / 'package.json').write_text(json.dumps({'name': '@oai/sky', 'version': '0.7.5'}))
        self.client = self.package / module.CLIENT_REL
        self.client.parent.mkdir(parents=True)
        self.original = b'fixture:' + module.ANCHOR + b':end'
        self.client.write_bytes(self.original)
        self.hash_patch = patch.object(module, 'EXPECTED_SHA256', hashlib.sha256(self.original).hexdigest())
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)

    def test_dry_run_writes_nothing(self):
        before = {p.relative_to(self.package): p.read_bytes() for p in self.package.rglob('*') if p.is_file()}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(['--sky-package', str(self.package)]), 0)
        after = {p.relative_to(self.package): p.read_bytes() for p in self.package.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_apply_idempotence_and_exact_restore(self):
        plan = module.inspect(self.package)
        module.apply(plan)
        self.assertEqual(plan['backup'].read_bytes(), self.original)
        self.assertEqual(self.client.read_bytes(), module.patched_bytes(self.original))
        self.assertEqual(plan['shim'].read_bytes(), module.SHIM_SOURCE.read_bytes())
        self.assertEqual(module.apply(module.inspect(self.package)), 'already applied')
        module.restore(module.inspect(self.package))
        self.assertEqual(self.client.read_bytes(), self.original)
        self.assertFalse(plan['shim'].exists())
        self.assertEqual(plan['backup'].read_bytes(), self.original)

    def test_unknown_build_and_sdk_version_are_rejected_before_writes(self):
        self.client.write_bytes(b'unknown updated vendor bytes')
        with self.assertRaises(ValueError):
            module.inspect(self.package)
        self.assertFalse(self.client.with_name(module.SHIM_NAME).exists())
        self.client.write_bytes(self.original)
        (self.package / 'package.json').write_text(json.dumps({'name': '@oai/sky', 'version': '0.7.6'}))
        with self.assertRaises(ValueError):
            module.inspect(self.package)

    def test_client_changed_after_inspection_is_preserved(self):
        plan = module.inspect(self.package)
        self.client.write_bytes(b'updated after inspection')
        with self.assertRaises(ValueError):
            module.apply(plan)
        self.assertEqual(self.client.read_bytes(), b'updated after inspection')
        self.assertFalse(plan['shim'].exists())
        self.assertFalse(plan['backup'].exists())

    def test_unrelated_shim_and_backup_are_never_overwritten(self):
        plan = module.inspect(self.package)
        plan['shim'].write_bytes(b'unrelated shim')
        with self.assertRaises(ValueError):
            module.inspect(self.package)
        plan['shim'].unlink()
        plan['backup'].write_bytes(b'unrelated backup')
        with self.assertRaises(ValueError):
            module.inspect(self.package)

    def test_restore_does_not_overwrite_a_later_vendor_update(self):
        module.apply(module.inspect(self.package))
        self.client.write_bytes(b'new vendor release')
        with self.assertRaises(ValueError):
            module.inspect(self.package)
        self.assertEqual(self.client.read_bytes(), b'new vendor release')

    def test_shim_created_after_inspection_is_preserved(self):
        plan = module.inspect(self.package)
        plan['shim'].write_bytes(b'created by another process')
        with self.assertRaises(ValueError):
            module.apply(plan)
        self.assertEqual(plan['shim'].read_bytes(), b'created by another process')
        self.assertEqual(self.client.read_bytes(), self.original)
        self.assertFalse(plan['backup'].exists())

    def test_restore_preserves_a_shim_modified_after_inspection(self):
        module.apply(module.inspect(self.package))
        plan = module.inspect(self.package)
        plan['shim'].write_bytes(b'later user modification')
        with self.assertRaises(ValueError):
            module.restore(plan)
        self.assertEqual(self.client.read_bytes(), plan['patched'])
        self.assertEqual(plan['shim'].read_bytes(), b'later user modification')

    def test_missing_installed_files_are_not_reported_as_success(self):
        module.apply(module.inspect(self.package))
        for field in ('shim', 'backup'):
            for operation in (module.apply, module.restore):
                with self.subTest(field=field, operation=operation.__name__):
                    plan = module.inspect(self.package)
                    original_file = plan[field].read_bytes()
                    plan[field].unlink()
                    with self.assertRaises(ValueError):
                        operation(plan)
                    self.assertEqual(self.client.read_bytes(), plan['patched'])
                    plan[field].write_bytes(original_file)

    def test_known_previous_shim_can_upgrade_without_changing_original_backup(self):
        module.apply(module.inspect(self.package))
        shim = self.client.with_name(module.SHIM_NAME)
        prior = b'reviewed prior shim fixture'
        shim.write_bytes(prior)
        with patch.object(module, 'KNOWN_SHIM_SHA256', {hashlib.sha256(prior).hexdigest()}):
            plan = module.inspect(self.package)
            self.assertNotEqual(plan['current_shim_data'], plan['shim_data'])
            module.apply(plan)
        self.assertEqual(shim.read_bytes(), module.SHIM_SOURCE.read_bytes())
        self.assertEqual(plan['backup'].read_bytes(), self.original)
        self.assertEqual(self.client.read_bytes(), plan['patched'])
        module.restore(module.inspect(self.package))
        self.assertEqual(self.client.read_bytes(), self.original)

    def test_known_previous_shim_modified_after_inspection_is_not_overwritten(self):
        module.apply(module.inspect(self.package))
        shim = self.client.with_name(module.SHIM_NAME)
        prior = b'reviewed prior shim fixture'
        shim.write_bytes(prior)
        with patch.object(module, 'KNOWN_SHIM_SHA256', {hashlib.sha256(prior).hexdigest()}):
            plan = module.inspect(self.package)
            shim.write_bytes(b'modified after inspection')
            with self.assertRaises(ValueError):
                module.apply(plan)
        self.assertEqual(shim.read_bytes(), b'modified after inspection')


if __name__ == '__main__':
    unittest.main()
