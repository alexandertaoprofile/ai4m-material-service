"""Directory cleanup never removes data or interferes with a running service."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('directory_cleanup', Path(__file__).resolve().parents[1] / 'scripts/finalize_directory_rename.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class DirectoryRenameTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.actual = self.root / 'material_selection'
        self.actual.mkdir()
        self.legacy = self.root / 'material_database'
        self.legacy.symlink_to('material_selection')
        self.listener = patch.object(cleanup, 'listener_present', return_value=False)
        self.processes = patch.object(cleanup, 'service_processes', return_value=[])
        self.listener.start()
        self.processes.start()
        self.addCleanup(self.listener.stop)
        self.addCleanup(self.processes.stop)

    def test_only_link_is_removed(self):
        result = cleanup.finalize(self.root)
        self.assertEqual(result['status'], 'cleaned')
        self.assertTrue(self.actual.is_dir())
        self.assertFalse(self.legacy.is_symlink())

    def test_running_listener_blocks_removal(self):
        with patch.object(cleanup, 'listener_present', return_value=True), self.assertRaises(RuntimeError):
            cleanup.finalize(self.root)
        self.assertTrue(self.legacy.is_symlink())

    def test_shutdown_process_blocks_removal(self):
        with patch.object(cleanup, 'service_processes', return_value=[123]), self.assertRaises(RuntimeError):
            cleanup.finalize(self.root)
        self.assertTrue(self.legacy.is_symlink())

    def test_wrong_target_is_preserved(self):
        self.legacy.unlink()
        self.legacy.symlink_to('some_other_directory')
        with self.assertRaises(RuntimeError):
            cleanup.finalize(self.root)
        self.assertTrue(self.legacy.is_symlink())

    def test_real_old_directory_is_preserved(self):
        self.legacy.unlink()
        self.legacy.mkdir()
        with self.assertRaises(RuntimeError):
            cleanup.finalize(self.root)
        self.assertTrue(self.legacy.is_dir())

    def test_dry_run_preserves_link(self):
        self.assertEqual(cleanup.finalize(self.root, dry_run=True)['status'], 'ready')
        self.assertTrue(self.legacy.is_symlink())

    def test_repeated_cleanup_is_safe(self):
        cleanup.finalize(self.root)
        self.assertEqual(cleanup.finalize(self.root)['status'], 'already_clean')

    def test_starting_service_during_cleanup_is_detected(self):
        with patch.object(cleanup, 'listener_present', side_effect=[False, True]), self.assertRaises(RuntimeError):
            cleanup.finalize(self.root)
        self.assertTrue(self.legacy.is_symlink())
