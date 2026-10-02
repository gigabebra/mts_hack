#!/usr/bin/env python3
"""Package generated JSON verification reports only; never scrape credentials."""
import argparse
import datetime as dt
import json
import tarfile
from stacklib import ROOT, command, write_json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--env', default='ubuntu')
args = parser.parse_args()
stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
destination = ROOT / 'artifacts/evidence' / (stamp + '.tar.gz')
destination.parent.mkdir(parents=True, exist_ok=True)
reports = [path for group in ('verify', 'idempotency', 'recovery', 'release', 'canary', 'canary-acceptance', 'demo')
           for path in sorted((ROOT / 'artifacts' / group).rglob('report.json'))]
# This checker uses an explicit filename beside the aggregate release report.
# Include its allowlisted TLS/route/distribution observations as well.
reports += sorted((ROOT / 'artifacts/release').glob('*/gateway.json'))
if not reports:
    parser.error('No reports exist. Run make verify first, including failed runs.')
index = {'generated_at': stamp, 'environment': args.env,
         'revision': command(['git', 'rev-parse', 'HEAD'], capture=True).stdout.strip(),
         'reports': [str(path.relative_to(ROOT)) for path in reports]}
index_path = destination.parent / (stamp + '-index.json')
write_json(index_path, index)
with tarfile.open(destination, 'w:gz') as archive:
    archive.add(index_path, arcname='index.json')
    for report in reports:
        # Reports contain allowlisted check output. Raw logs/inventory are not included.
        json.loads(report.read_text())
        archive.add(report, arcname=str(report.relative_to(ROOT)))
print(destination)
