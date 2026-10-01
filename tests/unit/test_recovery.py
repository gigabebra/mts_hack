"""The collector counter and position file are independently observed signals."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('recovery', Path(__file__).resolve().parents[1] / 'e2e/mvp-recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class CollectorObservation(unittest.TestCase):
    def test_waits_for_both_counter_and_persisted_position(self):
        before = ({('nginx.log', 'inode'): 34200}, 165)
        boundaries = {('nginx.log', 'inode'): 35170}
        # Observed live: emit_records already advanced while the earlier file
        # read still showed the old offset; the next observation saw both.
        self.assertFalse(recovery.collector_consumed(before, (before[0], 170), 5, boundaries))
        self.assertFalse(recovery.collector_consumed(before, ({('nginx.log', 'inode'): 34394}, 170), 5, boundaries))
        self.assertFalse(recovery.collector_consumed(before, ({('nginx.log', 'inode'): 35170}, 165), 5, boundaries))
        self.assertFalse(recovery.collector_consumed(before, ({}, 170), 5, boundaries))
        self.assertFalse(recovery.collector_consumed(before, ({('nginx.log', 'new-inode'): 35170}, 170), 5, boundaries))
        self.assertTrue(recovery.collector_consumed(before, ({('nginx.log', 'inode'): 35170}, 170), 5, boundaries))


if __name__ == '__main__':
    unittest.main()
