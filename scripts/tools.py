#!/usr/bin/env python3
"""Download SHA256-verified local Helm and kubectl; no root required."""
import tarfile
from stacklib import ROOT, VERSIONS, download

directory = ROOT / '.cache/tools'
directory.mkdir(parents=True, exist_ok=True)
archive = download(VERSIONS['downloads']['helm'], 'helm.tar.gz')
with tarfile.open(archive) as content:
    (directory / 'helm').write_bytes(content.extractfile('linux-amd64/helm').read())
(directory / 'kubectl').write_bytes(download(VERSIONS['downloads']['kubectl'], 'kubectl').read_bytes())
for name in ('helm', 'kubectl'):
    (directory / name).chmod(0o755)
print('Helm and kubectl installed in .cache/tools')
