import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from PIL import Image

SPEC = importlib.util.spec_from_file_location('comparison',
    Path(__file__).parents[1] / 'scripts/build_identity_comparison.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        source = self.base / 'portrait.png'
        Image.new('RGB', (20, 80), 'red').save(source)
        self.spec = {'path': 'portrait.png', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}

    def test_portrait_is_not_stretched_to_square_and_source_is_unchanged(self):
        roles = ['original_identity', 'approved_look', 'candidate']
        report = {'actors': {'actor': {r: self.spec for r in roles[:2]}},
                  'candidate': self.spec,
                  'comparison_regions': {'actor': {r: [0, 0, 1, 1] for r in roles}}}
        output = MODULE.build(report, self.base)
        tile = output.crop((0, 30, 320, 400))
        mask = tile.point(lambda v: v)
        positions = [(x, y) for y in range(mask.height) for x in range(mask.width)
                     if mask.getpixel((x, y)) == (255, 0, 0)]
        width = max(x for x, y in positions) - min(x for x, y in positions) + 1
        height = max(y for x, y in positions) - min(y for x, y in positions) + 1
        self.assertLess(abs(width / height - .25), .005)
        self.assertEqual(hashlib.sha256((self.base / 'portrait.png').read_bytes()).hexdigest(),
                         self.spec['sha256'])

    def test_out_of_bounds_region_rejected(self):
        with self.assertRaises(ValueError):
            MODULE.crop_source(self.base, self.spec, [-.1, 0, 1, 1])

    def test_changed_source_cannot_reuse_stale_region_record(self):
        Image.new('RGB', (20, 80), 'blue').save(self.base / 'portrait.png')
        with self.assertRaises(ValueError):
            MODULE.crop_source(self.base, self.spec, [0, 0, 1, 1])


if __name__ == '__main__':
    unittest.main()
