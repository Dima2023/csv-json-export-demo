"""Failure injection at actual file-operation boundaries; no disk-full needed."""
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import converter


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'source.csv'
        self.source.write_bytes(b'id,name\n001,example\n')
        self.destination = self.root / 'result.json'

    def invoke(self):
        stderr = io.StringIO()
        stdout = io.StringIO()
        with mock.patch('sys.argv', ['converter.py', 'csv-to-json', str(self.source), str(self.destination)]), contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            code = converter.main()
        self.assertEqual(self.source.read_bytes(), b'id,name\n001,example\n')
        if code:
            self.assertIn('error:', stderr.getvalue())
            self.assertNotIn('Wrote', stdout.getvalue())
        return code, stderr.getvalue()

    def assert_clean_failure(self):
        self.assertFalse(self.destination.exists())
        self.assertEqual(set(self.root.iterdir()), {self.source})

    def test_write_failure_leaves_no_partial_destination(self):
        # Works on the original direct-write code and the staged implementation.
        original_open = Path.open
        original_named = tempfile.NamedTemporaryFile
        class BrokenWriter:
            def __init__(self, stream):
                self.stream = stream
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def write(self, data):
                self.stream.write(data[:5])
                raise OSError('injected write failure after five bytes')
        def opening(path, mode='r', *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            return BrokenWriter(stream) if mode == 'xb' else stream
        def temporary(*args, **kwargs):
            return BrokenWriter(original_named(*args, **kwargs))
        with mock.patch.object(Path, 'open', opening), mock.patch.object(tempfile, 'NamedTemporaryFile', temporary):
            code, _ = self.invoke()
        self.assertEqual(code, 2)
        self.assert_clean_failure()

    def test_flush_close_short_write_failures(self):
        original = tempfile.NamedTemporaryFile
        for stage in ('flush', 'close', 'short-write'):
            with self.subTest(stage=stage):
                class BrokenStream:
                    def __init__(self, stream):
                        self.stream = stream
                    def __getattr__(self, name):
                        return getattr(self.stream, name)
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        self.stream.close()
                        if stage == 'close':
                            raise OSError('injected close failure')
                    def write(self, data):
                        if stage == 'short-write':
                            return self.stream.write(data[:5])
                        return self.stream.write(data)
                    def flush(self):
                        if stage == 'flush':
                            raise OSError('injected flush failure')
                        self.stream.flush()
                def temporary(*args, **kwargs):
                    return BrokenStream(original(*args, **kwargs))
                with mock.patch.object(tempfile, 'NamedTemporaryFile', temporary):
                    code, error = self.invoke()
                self.assertEqual(code, 2)
                self.assertIn(stage.split('-')[0], error)
                self.assert_clean_failure()

    def test_create_sync_commit_failures(self):
        for target in ('tempfile.NamedTemporaryFile', 'os.fsync', 'os.link'):
            with self.subTest(target=target):
                with mock.patch(target, side_effect=OSError('injected ' + target)):
                    code, error = self.invoke()
                self.assertEqual(code, 2)
                self.assertIn(target, error)
                self.assert_clean_failure()

    def test_no_destination_until_closed_and_synced(self):
        original_link = os.link
        original_fsync = os.fsync
        original_temp = tempfile.NamedTemporaryFile
        state = {}
        def temporary(*args, **kwargs):
            state['stream'] = original_temp(*args, **kwargs)
            return state['stream']
        def sync(fd):
            self.assertFalse(self.destination.exists())
            original_fsync(fd)
            state['synced'] = True
        def commit(source, destination):
            self.assertTrue(state['synced'])
            self.assertTrue(state['stream'].closed)
            self.assertFalse(self.destination.exists())
            self.assertEqual(json.loads(Path(source).read_text()), [{'id':'001', 'name':'example'}])
            original_link(source, destination)
        with mock.patch.object(tempfile, 'NamedTemporaryFile', temporary), mock.patch.object(os, 'fsync', sync), mock.patch.object(os, 'link', commit):
            code, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(set(self.root.iterdir()), {self.source, self.destination})

    def test_destination_created_during_commit_is_preserved(self):
        original = os.link
        def racing_link(source, destination):
            Path(destination).write_bytes(b'other writer')
            original(source, destination)
        with mock.patch.object(os, 'link', racing_link):
            code, _ = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(self.destination.read_bytes(), b'other writer')
        self.assertEqual(set(self.root.iterdir()), {self.source, self.destination})

    def test_symlinks_existing_dangling_and_racing(self):
        original = os.link
        for racing in (False, True):
            for target in (self.source, self.root / 'missing'):
                with self.subTest(racing=racing, target=target):
                    if not racing:
                        self.destination.symlink_to(target)
                    def linking(source, destination):
                        if racing:
                            Path(destination).symlink_to(target)
                        original(source, destination)
                    with mock.patch.object(os, 'link', linking):
                        code, _ = self.invoke()
                    self.assertEqual(code, 2)
                    self.assertTrue(self.destination.is_symlink())
                    self.assertEqual(self.destination.readlink(), target)
                    self.assertFalse((self.root / 'missing').exists())
                    self.assertEqual(set(self.root.iterdir()), {self.source, self.destination})
                    self.destination.unlink()

    def test_stable_parent_symlink_and_input_symlink(self):
        alias = self.root / 'parent-alias'
        alias.symlink_to(self.root, target_is_directory=True)
        source_alias = self.root / 'source-alias'
        source_alias.symlink_to(self.source)
        self.source = source_alias
        self.destination = alias / 'result.json'
        code, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.destination.read_text()), [{'id':'001','name':'example'}])
        self.assertFalse(list(self.root.glob('.csv-json-*')))

    def test_cleanup_failure_reports_publication_state(self):
        original = Path.unlink
        for committed in (False, True):
            with self.subTest(committed=committed):
                def unlink(path, *args, **kwargs):
                    if path.name.startswith('.csv-json-'):
                        raise OSError('injected cleanup failure')
                    return original(path, *args, **kwargs)
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(Path, 'unlink', unlink))
                    if not committed:
                        stack.enter_context(mock.patch.object(os, 'link', side_effect=OSError('commit failed')))
                    code, error = self.invoke()
                self.assertEqual(code, 2)
                self.assertIn('temporary cleanup failed:', error)
                self.assertIn('complete destination was published' if committed else 'destination was not published', error)
                self.assertEqual(self.destination.exists(), committed)
                leftovers = list(self.root.glob('.csv-json-*'))
                self.assertEqual(len(leftovers), 1)
                self.assertIn(str(leftovers[0]), error)
                if committed:
                    self.assertEqual(self.destination.read_bytes(), leftovers[0].read_bytes())
                    self.destination.unlink()
                leftovers[0].unlink()

    def test_two_real_processes_only_one_wins(self):
        command = [sys.executable, converter.__file__, 'csv-to-json', str(self.source), str(self.destination)]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        results = [p.communicate(timeout=10) for p in processes]
        self.assertEqual(sorted(p.returncode for p in processes), [0, 2])
        self.assertEqual(json.loads(self.destination.read_text()), [{'id':'001','name':'example'}])
        self.assertEqual(set(self.root.iterdir()), {self.source, self.destination})
        for process, (stdout, stderr) in zip(processes, results):
            if process.returncode:
                self.assertIn(b'error:', stderr)
                self.assertNotIn(b'Wrote', stdout)


if __name__ == '__main__':
    unittest.main()
