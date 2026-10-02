"""Ensure supervisor cleanup also stops orphaned release subprocesses."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location('interruption_acceptance', Path(__file__).with_name('interrupt.py'))
interruption = importlib.util.module_from_spec(spec)
spec.loader.exec_module(interruption)


class ProcessCleanup(unittest.TestCase):
    def test_child_is_stopped_even_after_writer_parent_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'child.pid'
            source = ('import subprocess,sys;from pathlib import Path;'
                      'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
                      'Path(sys.argv[1]).write_text(str(p.pid))')
            parent = subprocess.Popen([sys.executable, '-c', source, str(marker)], start_new_session=True)
            try:
                parent.wait(timeout=5)
                child = int(marker.read_text())
                interruption.terminate_tree(parent, grace=1)
                status = Path('/proc') / str(child) / 'stat'
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if not status.exists() or status.read_text().split()[2] == 'Z':
                        break
                    time.sleep(.02)
                else:
                    self.fail('Release descendant survived parent cleanup')
            finally:
                interruption.terminate_tree(parent, grace=1)


if __name__ == '__main__':
    unittest.main()
