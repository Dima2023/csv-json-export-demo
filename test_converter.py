import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('converter.py')
SAMPLE = Path(__file__).with_name('examples') / 'input.csv'

class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_cli(self, content, direction='csv-to-json', options=(), suffix=None):
        source = self.root / ('input' + (suffix or ('.csv' if direction == 'csv-to-json' else '.json')))
        source.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
        before = source.read_bytes()
        output = self.root / 'output'
        result = subprocess.run([sys.executable, str(SCRIPT), direction, str(source), str(output), *options], capture_output=True, text=True)
        self.assertNotIn("can't open file", result.stderr)
        if result.returncode:
            self.assertIn('error:', result.stderr)
        self.assertEqual(source.read_bytes(), before)
        return result, output

    def test_sample_unicode_strings_trim_rename_first_wins(self):
        result, output = self.run_cli(SAMPLE.read_bytes(), options=['--trim', '--rename', 'product=name', '--dedupe-key', 'id'])
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(output.read_text())
        self.assertEqual(rows, [
            {'id':'id001','name':'Болт','quantity':'007'},
            {'id':'0002','name':'Écrou','quantity':'02'},
            {'id':'0003','name':'','quantity':'0'},
            {'id':'0004','name':'Nut, steel','quantity':'12'},
            {'id':'0005','name':'Шайба','quantity':'003'}])
        self.assertIn('Болт', output.read_text())

    def test_default_preserves_whitespace_and_duplicates(self):
        r, out = self.run_cli(SAMPLE.read_bytes())
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(out.read_text())
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]['id'], ' id001 ')

    def test_roundtrip_and_repeatability(self):
        r, out = self.run_cli('id,name\n001,"Écrou, болт"\n002,"line1\nline2"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        data = out.read_bytes()
        out.unlink()
        r, out = self.run_cli(data, 'json-to-csv')
        self.assertEqual(r.returncode, 0, r.stderr)
        csv_data = out.read_bytes()
        out.unlink()
        r, out = self.run_cli(csv_data)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.read_bytes(), data)
        out.unlink()
        r, out = self.run_cli(csv_data)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.read_bytes(), data)

    def test_bad_csv(self):
        for content in ['', 'id,id\n1,2\n', 'id,\n1,2\n', 'id,name\n1\n', 'id\n1,2\n', 'id\n"unterminated', 'id\n\n', b'id\n\xff']:
            with self.subTest(content=content):
                r, out = self.run_cli(content)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(out.exists())

    def test_bad_json(self):
        for content in ['{', '{}', '[]', '[1]', '[{"id":1}]', '[{"id":null}]', '[{"id":{}}]', '[{"id":"1","id":"2"}]', '[{"id":"1"},{"other":"2"}]', '[{"":"x"}]', '[{}]', '[{"id":"\\ud800"}]']:
            with self.subTest(content=content):
                r, out = self.run_cli(content, 'json-to-csv')
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(out.exists())

    def test_invalid_options(self):
        for options in [['--rename','id=name'], ['--rename','absent=x'], ['--rename','id='], ['--rename','bad'], ['--rename','id=a','--rename','id=b'], ['--dedupe-key','absent']]:
            with self.subTest(options=options):
                r, out = self.run_cli('id,name\n1,a\n', options=options)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(out.exists())

    def test_dedupe_key_is_after_rename_and_trim(self):
        r, out = self.run_cli('id,name\n 01 ,first\n01,second\n', options=['--trim','--rename','id=key','--dedupe-key','key'])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(out.read_text()), [{'key':'01','name':'first'}])

    def test_blank_dedupe_key_rejected(self):
        r, out = self.run_cli('id,name\n ,a\n', options=['--trim','--dedupe-key','id'])
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(out.exists())

    def test_existing_output_and_source_alias_rejected(self):
        source = self.root / 'in.csv'
        source.write_text('id\n001\n')
        targets = [source, self.root / 'existing', self.root / 'symlink', self.root / 'hardlink']
        targets[1].write_text('keep')
        targets[2].symlink_to(source)
        import os
        os.link(source, targets[3])
        for target in targets:
            before = target.read_bytes()
            r = subprocess.run([sys.executable, str(SCRIPT), 'csv-to-json', str(source), str(target)], capture_output=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(target.read_bytes(), before)

    def test_long_field_roundtrip(self):
        value = 'x' * 150000
        r, out = self.run_cli(json.dumps([{'id': '001', 'name': value}]), 'json-to-csv')
        self.assertEqual(r.returncode, 0, r.stderr)
        data = out.read_bytes()
        out.unlink()
        r, out = self.run_cli(data)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(out.read_text())[0]['name'], value)

    def test_size_limits_and_bom(self):
        for content, direction in [('id\n' + 'x' * (1024 * 1024), 'csv-to-json'),
                                   ('id\n' + 'x' * (1024 * 1024 - 4), 'csv-to-json'),
                                   ('\ufeffid\n001\n', 'csv-to-json'),
                                   ('\ufeff[{"id":"001"}]', 'json-to-csv')]:
            with self.subTest(direction=direction, size=len(content)):
                r, out = self.run_cli(content, direction)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(out.exists())

    def test_json_applies_same_rules(self):
        r, out = self.run_cli('[{"id":" 001 ","name":" Болт "},{"id":"001","name":"drop"}]',
                              'json-to-csv', ['--trim', '--rename', 'name=product', '--dedupe-key', 'id'])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.read_text(), 'id,product\n001,Болт\n')

    def test_header_only_rejected(self):
        r, out = self.run_cli('id,name\n')
        self.assertEqual(r.returncode, 2)
        self.assertFalse(out.exists())

    def test_json_different_key_order(self):
        r, out = self.run_cli('[{"id":"001","name":"a"},{"name":"b","id":"002"}]', 'json-to-csv')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.read_text(), 'id,name\n001,a\n002,b\n')

if __name__ == '__main__':
    unittest.main()
