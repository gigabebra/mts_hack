"""Reports and release checks must also work from an scp copy without Git."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import stacklib


class CodeIdentity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = Path(self.temp.name) / 'source'
        self.repository.mkdir()
        location = patch.object(stacklib, '__file__', str(self.repository / 'scripts/stacklib.py'))
        location.start()
        self.addCleanup(location.stop)

    def assert_unknown(self, identity):
        self.assertEqual(identity, {'revision': None, 'working_tree_dirty': None})

    def test_source_copy_does_not_inherit_parent_repository_metadata(self):
        (Path(self.temp.name) / '.git').mkdir()
        with patch.object(stacklib.subprocess, 'run') as run:
            self.assert_unknown(stacklib.code_identity())
        run.assert_not_called()

    def test_checkout_and_worktree_report_git_metadata_from_code_directory(self):
        for marker_kind in ('directory', 'file'):
            marker = self.repository / '.git'
            marker.mkdir() if marker_kind == 'directory' else marker.write_text('gitdir: fixture')
            for status in ('', ' M scripts/verify.py\n'):
                with self.subTest(marker=marker_kind, status=status), \
                     patch.object(stacklib, 'ROOT', Path(self.temp.name) / 'artifacts'), \
                     patch.object(stacklib.subprocess, 'run', side_effect=[
                         SimpleNamespace(stdout='a' * 40 + '\n'), SimpleNamespace(stdout=status)]) as run:
                    self.assertEqual(stacklib.code_identity(),
                                     {'revision': 'a' * 40, 'working_tree_dirty': bool(status)})
                    for call in run.call_args_list:
                        self.assertEqual(call.kwargs['cwd'], self.repository)
            marker.rmdir() if marker_kind == 'directory' else marker.unlink()

    def test_missing_broken_or_timed_out_git_does_not_block_reports(self):
        (self.repository / '.git').mkdir()
        for error in (FileNotFoundError('git'), subprocess.CalledProcessError(128, ['git']),
                      subprocess.TimeoutExpired(['git'], 5)):
            with self.subTest(error=type(error).__name__), \
                 patch.object(stacklib.subprocess, 'run', side_effect=error):
                self.assert_unknown(stacklib.code_identity())

    def test_invalid_revision_is_not_recorded(self):
        (self.repository / '.git').mkdir()
        with patch.object(stacklib.subprocess, 'run', return_value=SimpleNamespace(stdout='not-a-commit')):
            self.assert_unknown(stacklib.code_identity())


class SourceCopyReports(unittest.TestCase):
    def test_commands_preserve_failure_reports_and_package_them_without_git(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            shutil.copytree(ROOT / 'scripts', source / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(ROOT / 'tests/e2e', source / 'tests/e2e', ignore=shutil.ignore_patterns('__pycache__'))
            (source / 'config').mkdir()
            for name in ('versions.yaml', 'images.lock.yaml'):
                shutil.copy2(ROOT / 'config' / name, source / 'config' / name)
            # Absolute Python remains usable; no external program, including Git, is available.
            env = dict(os.environ, PATH=str(source / 'empty-path'))
            self.assertIsNone(shutil.which('git', path=env['PATH']))
            for script, group in [('scripts/verify.py', 'verify'), ('scripts/verify-release.py', 'release'),
                                  ('scripts/verify-idempotency.py', 'idempotency'),
                                  ('tests/e2e/release-demo.py', 'demo')]:
                with self.subTest(script=script):
                    result = subprocess.run([sys.executable, script, '--env', 'fixture-missing'],
                                            cwd=source, env=env, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    reports = list((source / 'artifacts' / group).glob('*/report.json'))
                    self.assertEqual(len(reports), 1, result.stdout + result.stderr)
                    report = json.loads(reports[0].read_text())
                    self.assertEqual(report['status'], 'fail')
                    self.assertIsNone(report['revision'])
                    self.assertIsNone(report['working_tree_dirty'])
                    self.assertIn('fixture-missing.local.yaml', json.dumps(report['checks']))
            result = subprocess.run([sys.executable, 'scripts/evidence.py', '--env', 'fixture-missing'],
                                    cwd=source, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with tarfile.open(result.stdout.strip()) as archive:
                index = json.load(archive.extractfile('index.json'))
                self.assertIsNone(index['revision'])
                self.assertIsNone(index['working_tree_dirty'])
                self.assertEqual(len(index['reports']), 4)
                self.assertTrue(all(path in archive.getnames() for path in index['reports']))


if __name__ == '__main__':
    unittest.main()
