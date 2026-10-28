#!/usr/bin/env python3
"""Small, standard-library-only CSV / flat JSON demonstration."""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile

MAX_BYTES = 1024 * 1024
csv.field_size_limit(MAX_BYTES)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON member: ' + key)
        result[key] = value
    return result


def read_table(path, direction):
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('input exceeds 1 MiB')
    text = raw.decode('utf-8')
    if text.startswith('\ufeff'):
        raise ValueError('UTF-8 BOM is not supported')
    if direction == 'csv-to-json':
        records = list(csv.reader(io.StringIO(text, newline=''), strict=True))
        if len(records) < 2:
            raise ValueError('CSV needs a header and at least one row')
        headers, *values = records
        if any(len(row) != len(headers) for row in values):
            raise ValueError('CSV rows must match the header width; blank lines are invalid')
        if len(set(headers)) != len(headers):
            raise ValueError('duplicate CSV header')
        rows = [dict(zip(headers, row)) for row in values]
    else:
        rows = json.loads(text, object_pairs_hook=unique_object)
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            raise ValueError('JSON must be a nonempty array of objects')
        headers = list(rows[0])
    if not headers or any(not h.strip() or h != h.strip() for h in headers):
        raise ValueError('headers must be nonempty and have no surrounding whitespace')
    for row in rows:
        if set(row) != set(headers) or any(not isinstance(value, str) for value in row.values()):
            raise ValueError('rows must share identical columns and contain only string values')
    return headers, rows


def transform(headers, rows, trim, renames, key):
    mapping = {}
    for spec in renames:
        source, separator, target = spec.partition('=')
        if not separator or source not in headers or source in mapping or not target.strip() or target != target.strip():
            raise ValueError('invalid or repeated rename: ' + spec)
        mapping[source] = target
    renamed = [mapping.get(h, h) for h in headers]
    if len(set(renamed)) != len(renamed):
        raise ValueError('renaming creates duplicate columns')
    if key is not None and key not in renamed:
        raise ValueError('dedupe key must name an output column')
    output, seen = [], set()
    for row in rows:
        result = {new: row[old].strip() if trim else row[old] for old, new in zip(headers, renamed)}
        if key is not None:
            value = result[key]
            if not value.strip():
                raise ValueError('dedupe key cannot be blank')
            if value in seen:
                continue
            seen.add(value)
        output.append(result)
    return renamed, output


def publish(destination, payload):
    """Publish a closed, complete file using an atomic, no-clobber hard link.

    Requires a stable, trusted directory on a local filesystem supporting links.
    This provides visibility atomicity, not power-loss durability.
    """
    staged = tempfile.NamedTemporaryFile(mode='wb', prefix='.csv-json-',
                                         suffix='.tmp', dir=destination.parent,
                                         delete=False)
    temporary = Path(staged.name)
    published = False
    try:
        with staged as stream:
            if stream.write(payload) != len(payload):
                raise OSError('short write while staging output')
            stream.flush()
            os.fsync(stream.fileno())
        # No check-then-rename race: link fails if any destination entry exists,
        # including a dangling symlink. Never remove or replace that entry.
        os.link(temporary, destination)
        published = True
    finally:
        try:
            temporary.unlink()
        except OSError as exc:
            state = 'complete destination was published' if published else 'destination was not published'
            raise OSError(f'temporary cleanup failed: {temporary}; {state}; {exc}') from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('direction', choices=['csv-to-json', 'json-to-csv'])
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--trim', action='store_true', help='strip Unicode whitespace from values')
    parser.add_argument('--rename', action='append', default=[], metavar='OLD=NEW')
    parser.add_argument('--dedupe-key', help='output column; keep first occurrence')
    args = parser.parse_args()
    try:
        headers, rows = read_table(args.source, args.direction)
        headers, rows = transform(headers, rows, args.trim, args.rename, args.dedupe_key)
        if args.direction == 'csv-to-json':
            rendered = json.dumps(rows, ensure_ascii=False, indent=2) + '\n'
        else:
            stream = io.StringIO(newline='')
            writer = csv.DictWriter(stream, fieldnames=headers, lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
            rendered = stream.getvalue()
        payload = rendered.encode('utf-8')
        if len(payload) > MAX_BYTES:
            raise ValueError('output exceeds 1 MiB; choose a smaller input')
        publish(args.destination, payload)
    except (OSError, ValueError, csv.Error, RecursionError) as exc:
        print('error: ' + str(exc), file=sys.stderr)
        return 2
    print(f'Wrote {len(rows)} rows to {args.destination}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
