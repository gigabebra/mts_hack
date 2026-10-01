#!/usr/bin/env python3
"""Helm post-renderer: every image must be in the reviewed digest lock."""
import sys
import yaml
from stacklib import lock_document

try:
    documents = [lock_document(x) for x in yaml.safe_load_all(sys.stdin) if x]
    yaml.safe_dump_all(documents, sys.stdout, sort_keys=False)
except Exception as error:
    print(f'image-lock: {error}', file=sys.stderr)
    sys.exit(1)
