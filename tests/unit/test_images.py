"""A known digest must not authorize another repository or unreviewed tag."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from stacklib import IMAGES, locked_image


class ImageLock(unittest.TestCase):
    def test_each_reviewed_reference_and_its_pinned_forms(self):
        for tagged, digest in IMAGES.items():
            pinned = tagged.rsplit(':', 1)[0] + '@' + digest
            with self.subTest(reference=tagged):
                self.assertEqual(locked_image(tagged), pinned)
                self.assertEqual(locked_image(pinned), pinned)
                self.assertEqual(locked_image(tagged + '@' + digest), tagged + '@' + digest)

    def test_rejects_unknown_repository_tag_or_mismatched_digest(self):
        tagged, digest = next(iter(IMAGES.items()))
        repository = tagged.rsplit(':', 1)[0]
        other_digest = next(value for value in IMAGES.values() if value != digest)
        for reference in (
            'unreviewed.invalid/arbitrary/repository@' + digest,
            repository + ':unreviewed@' + digest,
            repository + '@' + other_digest,
            repository + '@sha256:' + '0' * 64,
            repository + ':unreviewed',
        ):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                locked_image(reference)

    def test_registry_port_is_part_of_repository_identity(self):
        digest = 'sha256:' + '1' * 64
        with patch('stacklib.IMAGES', {'registry.example:5000/app:v1': digest}):
            pinned = 'registry.example:5000/app@' + digest
            self.assertEqual(locked_image('registry.example:5000/app:v1'), pinned)
            self.assertEqual(locked_image(pinned), pinned)
            with self.assertRaises(ValueError):
                locked_image('registry.example/app@' + digest)


if __name__ == '__main__':
    unittest.main()
