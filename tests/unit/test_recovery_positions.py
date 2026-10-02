"""Position-file compaction fixtures, independent of live Fluentd evidence."""
import copy
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    'recovery_positions', Path(__file__).resolve().parents[1] / 'e2e/mvp-recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class CollectorPositionRestoration(unittest.TestCase):
    def setUp(self):
        self.stable_a = ('stable-a.log', 101)
        self.stable_b = ('stable-b.log', 102)
        self.canary = ('canary.log', 103)
        self.tombstone = ('deleted-old-canary.log', 201)
        self.deleted = ('deleted-old-stable.log', 202)
        sources = {self.stable_a: 100, self.stable_b: 200, self.canary: 0}
        positions = dict(sources)
        self.arguments = {
            'saved': dict(positions),
            'restored': dict(positions),
            'sources_before': dict(sources),
            'sources_after': dict(sources),
            'boundaries': {self.stable_a: 100, self.stable_b: 180},
            'emitted': 0,
        }
        self.arguments['saved'].update({self.tombstone: 0xffffffffffffffff, self.deleted: 760})

    def evaluate(self, arguments=None):
        return recovery.collector_positions_restored(**(self.arguments if arguments is None else arguments))

    def test_compaction_may_remove_only_inactive_history(self):
        self.assertIs(self.evaluate(), True)

    def test_unchanged_inactive_history_is_also_valid(self):
        self.arguments['restored'] = dict(self.arguments['saved'])
        self.assertIs(self.evaluate(), True)

    def test_compaction_may_remove_only_some_inactive_entries(self):
        self.arguments['restored'][self.deleted] = self.arguments['saved'][self.deleted]
        self.assertIs(self.evaluate(), True)

    def test_unchanged_offset_can_cover_boundary_without_reaching_eof(self):
        self.arguments['saved'][self.stable_b] = 190
        self.arguments['restored'][self.stable_b] = 190
        self.assertIs(self.evaluate(), True)

    def test_missing_active_offset_fails_even_for_source_without_marker(self):
        for missing_from in ('saved', 'restored', 'both'):
            with self.subTest(missing_from=missing_from):
                arguments = copy.deepcopy(self.arguments)
                for field in ('saved', 'restored') if missing_from == 'both' else (missing_from,):
                    arguments[field].pop(self.canary)
                self.assertIs(self.evaluate(arguments), False)

    def test_active_offsets_must_match_exactly_including_advance(self):
        for offset in (180, 189, 191):
            with self.subTest(offset=offset):
                arguments = copy.deepcopy(self.arguments)
                # Both positions remain within size and cover the marker;
                # only exact persistence can reject this difference.
                arguments['saved'][self.stable_b] = 190
                arguments['restored'][self.stable_b] = offset
                self.assertIs(self.evaluate(arguments), False)

    def test_active_saved_offset_must_be_finite_nonnegative_and_within_source(self):
        for offset in (-1, 101, 0xffffffffffffffff, float('inf'), float('-inf'), float('nan')):
            with self.subTest(offset=offset):
                arguments = copy.deepcopy(self.arguments)
                # Equality alone must not approve a bogus persisted position.
                arguments['saved'][self.stable_a] = offset
                arguments['restored'][self.stable_a] = offset
                self.assertIs(self.evaluate(arguments), False)

    def test_offset_must_cover_every_marker_boundary(self):
        self.arguments['saved'][self.stable_b] = 179
        self.arguments['restored'][self.stable_b] = 179
        self.assertIs(self.evaluate(), False)

    def test_boundary_source_must_still_be_active(self):
        self.arguments['boundaries'][self.deleted] = 500
        self.assertIs(self.evaluate(), False)

    def test_same_path_with_different_inode_is_not_persistence(self):
        replacement = (self.stable_a[0], 999)
        self.arguments['restored'][replacement] = self.arguments['restored'].pop(self.stable_a)
        self.assertIs(self.evaluate(), False)

    def test_source_rotation_is_not_accepted_by_comparing_intersection(self):
        replacement = (self.stable_a[0], 999)
        self.arguments['sources_after'][replacement] = self.arguments['sources_after'].pop(self.stable_a)
        self.arguments['restored'][replacement] = self.arguments['restored'].pop(self.stable_a)
        self.assertIs(self.evaluate(), False)

    def test_disappearing_active_source_cannot_be_called_compaction(self):
        # Use the non-marker source: checking just marker paths would miss this.
        self.arguments['sources_after'].pop(self.canary)
        self.arguments['restored'].pop(self.canary)
        self.assertIs(self.evaluate(), False)

    def test_source_size_change_invalidates_the_quiet_window(self):
        for size in (99, 101):
            with self.subTest(size=size):
                arguments = copy.deepcopy(self.arguments)
                arguments['sources_after'][self.stable_a] = size
                self.assertIs(self.evaluate(arguments), False)

    def test_added_source_invalidates_the_quiet_window(self):
        self.arguments['sources_after'][('new.log', 301)] = 0
        self.assertIs(self.evaluate(), False)

    def test_unchanged_positions_do_not_mask_missing_active_file(self):
        self.arguments['restored'] = dict(self.arguments['saved'])
        self.arguments['sources_after'].pop(self.canary)
        self.assertIs(self.evaluate(), False)

    def test_unknown_added_position_is_rejected_even_when_inactive(self):
        self.arguments['restored'][('unexpected.log', 401)] = 0
        self.assertIs(self.evaluate(), False)

    def test_retained_inactive_position_must_not_change(self):
        self.arguments['restored'][self.deleted] = 759
        self.assertIs(self.evaluate(), False)

    def test_nonzero_input_emission_is_not_explained_by_compaction(self):
        for emitted in (-1, 1, 5, 10000):
            with self.subTest(emitted=emitted):
                arguments = copy.deepcopy(self.arguments)
                arguments['emitted'] = emitted
                self.assertIs(self.evaluate(arguments), False)

    def test_empty_active_inventory_cannot_pass_vacuously(self):
        self.arguments['sources_before'] = {}
        self.arguments['sources_after'] = {}
        self.arguments['boundaries'] = {}
        self.arguments['restored'] = {}
        self.assertIs(self.evaluate(), False)

    def test_empty_marker_boundaries_cannot_pass_vacuously(self):
        self.arguments['boundaries'] = {}
        self.assertIs(self.evaluate(), False)


if __name__ == '__main__':
    unittest.main()
