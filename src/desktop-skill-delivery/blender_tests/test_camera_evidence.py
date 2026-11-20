"""Synthetic measurements only; none of these fixtures are native capture evidence."""
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import zlib
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

TOOLS = Path(__file__).resolve().parents[1] / 'blender_tools'
sys.path.insert(0, str(TOOLS))
import check_camera_evidence as checker


def picture(size=checker.RESOLUTION, shift=0, shade=240):
    image = Image.new('RGB', size, (15, 15, 15))
    draw = ImageDraw.Draw(image)
    for x in range(30, size[0] - 50, 80):
        draw.rectangle((x + shift, 30, x + shift + 20, size[1] - 30), fill=(shade,) * 3)
    return image


class CameraEvidenceTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[4] / '.tmp' / 'camera-evidence-tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'synthetic-room'
        self.package.mkdir()
        self.evidence = self.root / 'evidence.json'
        self.manifest = {
            'schema_version': '1.0', 'package_id': 'synthetic-room', 'title': 'Synthetic fixture',
            'units': {'system': 'METRIC', 'scale_length': 1}, 'room_bounds': {'size_m': [4, 4, 3]},
            'navigation': {'orbit_target_blender_m': [0, 0, 1], 'walkable_boxes_blender_m': [{'min': [-2,-2,0], 'max': [2,2,3]}]},
            'render_defaults': {'resolution': list(checker.RESOLUTION), 'default_camera_id': 'C1', 'default_lighting_preset': 'neutral'},
            'lighting_presets': [{'id': 'neutral'}],
            'cameras': [{'id': 'C1', 'projection': 'PERSP', 'lens_mm': 42,
                         'fov_y_deg': checker.validator.compute_vertical_fov_deg(42),
                         'location_blender_m': [0,-5,2], 'target_blender_m': [0,0,1],
                         'reference_image': 'cameras/CAM_C1.png'}], 'files': []}
        gltf = json.dumps({'asset': {'version': '2.0'}, 'nodes': [{'name': 'SET_fixture'}]}).encode()
        gltf += b' ' * (-len(gltf) % 4)
        self.add_file('scene.glb', struct.pack('<4sIII4s', b'glTF', 2, 20+len(gltf), len(gltf), b'JSON') + gltf, 'model/gltf-binary')
        self.add_file('poster.png', self.png(picture((320,180))), 'image/png')
        reference = self.png(picture())
        self.add_file('cameras/CAM_C1.png', reference, 'image/png')
        original = self.image_ref('blender.png', reference)
        web = self.image_ref('web.png', self.png(picture(shift=2, shade=220)))
        fov = self.manifest['cameras'][0]['fov_y_deg']
        self.data = {'schema': 'vf.camera-evidence/v1', 'cameras': [{
            'camera_id': 'C1', 'frame': 1, 'resolution': list(checker.RESOLUTION), 'anchor_ids': ['corner'],
            'blender': {'capture_ref': 'synthetic-blender', 'image': original, 'fov_y_deg': fov, 'anchors': [{'id': 'corner', 'pixel': [30,30]}]},
            'web': {'capture_ref': 'synthetic-web', 'image': web, 'fov_y_deg': fov, 'anchors': [{'id': 'corner', 'pixel': [32,30]}]}}]}
        self.bind_manifest()

    @staticmethod
    def png(image):
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        return stream.getvalue()

    def add_file(self, name, raw, mime):
        path = self.package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.manifest['files'].append({'path': name, 'size': len(raw), 'sha256': checker.sha256(raw), 'mime': mime})

    def image_ref(self, name, raw):
        (self.root / name).write_bytes(raw)
        return {'path': name, 'sha256': checker.sha256(raw)}

    def bind_manifest(self):
        raw = json.dumps(self.manifest).encode()
        (self.package / 'set.json').write_bytes(raw)
        self.data.update(manifest_sha256=checker.sha256(raw), scene_sha256=checker.sha256((self.package / 'scene.glb').read_bytes()))

    def measure(self):
        self.evidence.write_text(json.dumps(self.data))
        return checker.measure(self.package, self.evidence)

    def test_complete_measurement_is_not_native_acceptance(self):
        before = {p: checker.sha256(p.read_bytes()) for p in self.root.rglob('*') if p.is_file()}
        result = self.measure()
        self.assertTrue(result['thresholds_passed'])
        self.assertFalse(result['native_capture_origin_verified'])
        self.assertFalse(result['desktop_or_project_acceptance'])
        self.assertEqual(result['cameras'][0]['max_anchor_drift_px'], 2)
        self.assertEqual(before, {p: checker.sha256(p.read_bytes()) for p in before})

    def test_symmetric_three_pixel_tolerance_and_large_shift_failure(self):
        first = picture((240,180))
        near = picture((240,180), shift=3)
        far = picture((240,180), shift=14)
        a = checker.compare_edges(first, near)
        b = checker.compare_edges(near, first)
        self.assertEqual(a['edge_iou'], b['edge_iou'])
        self.assertGreaterEqual(a['edge_iou'], .92)
        self.assertLess(checker.compare_edges(first, far)['edge_iou'], .92)

    def test_identical_pixels_and_blank_images_reject(self):
        for first, second, code in ((picture(), picture(), 'DUPLICATE_CAPTURE_PIXELS'),
                                    (Image.new('RGB', (80,80), 'white'), Image.new('RGB', (80,80), 'black'), 'INSUFFICIENT_STRUCTURE')):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code):
                checker.compare_edges(first, second)

    def test_malformed_image_shapes_reject_cleanly(self):
        baseline = copy.deepcopy(self.data)
        for side in ('blender', 'web'):
            for bad in (None, [], 3, 'text', True):
                self.data = copy.deepcopy(baseline)
                self.data['cameras'][0][side]['image'] = bad
                with self.subTest(side=side, bad=bad), self.assertRaisesRegex(ValueError, 'INVALID_IMAGE_REFERENCE'):
                    self.measure()

    def test_hash_and_package_bindings_reject(self):
        baseline = copy.deepcopy(self.data)
        for mutate, code in ((lambda d: d.update(scene_sha256='0'*64), 'PACKAGE_BINDING_MISMATCH'),
                             (lambda d: d.update(manifest_sha256='0'*64), 'PACKAGE_BINDING_MISMATCH'),
                             (lambda d: d['cameras'][0]['blender']['image'].update(sha256='0'*64), 'REFERENCE_IMAGE_NOT_FROM_PACKAGE'),
                             (lambda d: d['cameras'][0]['web']['image'].update(sha256='0'*64), 'IMAGE_HASH_MISMATCH')):
            self.data = copy.deepcopy(baseline)
            mutate(self.data)
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code):
                self.measure()

    def test_anchors_and_optics_can_fail_without_malformed_evidence(self):
        row = self.data['cameras'][0]
        row['web']['anchors'][0]['pixel'] = [39,30]
        self.assertFalse(self.measure()['thresholds_passed'])
        row['web']['anchors'][0]['pixel'] = [38,30]
        self.assertTrue(self.measure()['thresholds_passed'])
        row['web']['fov_y_deg'] += .06
        self.assertFalse(self.measure()['thresholds_passed'])

    def test_camera_coverage_and_anchor_shape_reject(self):
        baseline = copy.deepcopy(self.data)
        for mutate, code in ((lambda d: d.update(cameras=[]), 'CAMERA_COVERAGE_MISMATCH'),
                             (lambda d: d['cameras'][0].update(camera_id='N9'), 'INVALID_EVIDENCE_CAMERA'),
                             (lambda d: d['cameras'][0].update(frame=True), 'CAPTURE_FRAME_OR_RESOLUTION_MISMATCH'),
                             (lambda d: d['cameras'][0].update(anchor_ids=['missing']), 'ANCHOR_COVERAGE_MISMATCH'),
                             (lambda d: d['cameras'][0]['web']['anchors'][0].update(pixel=[True,30]), 'INVALID_ANCHOR_PIXEL'),
                             (lambda d: d['cameras'][0]['web'].update(capture_ref='synthetic-blender'), 'DISTINCT_CAPTURE_REFERENCES_REQUIRED')):
            self.data = copy.deepcopy(baseline)
            mutate(self.data)
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code):
                self.measure()

    def test_orthographic_width_is_measured(self):
        camera = self.manifest['cameras'][0]
        camera.update(projection='ORTHO', ortho_scale=7)
        del camera['fov_y_deg']
        self.bind_manifest()
        for side in ('blender', 'web'):
            self.data['cameras'][0][side]['ortho_width_m'] = 7
        self.assertTrue(self.measure()['thresholds_passed'])
        self.data['cameras'][0]['web']['ortho_width_m'] = 7.1
        result = self.measure()
        self.assertFalse(result['thresholds_passed'])
        self.assertIsNone(result['cameras'][0]['fov_error_deg'])

    def test_mask_requires_binding_and_independent_review(self):
        mask = self.image_ref('mask.png', self.png(Image.new('L', checker.RESOLUTION, 255)))
        row = self.data['cameras'][0]
        row['structure_mask'] = mask
        with self.assertRaisesRegex(ValueError, 'UNBOUND_STRUCTURE_MASK'):
            self.measure()
        mask.update(rationale='Synthetic full-frame mask', baseline_manifest_sha256=self.data['manifest_sha256'])
        self.assertTrue(self.measure()['cameras'][0]['structure_mask_requires_independent_review'])
        mask.update(self.image_ref('mask.png', self.png(Image.new('L', checker.RESOLUTION, 0))))
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_MASK_AREA'):
            self.measure()

    def test_near_duplicate_and_pinhole_mask_reject(self):
        first = picture()
        second = first.copy()
        second.putpixel((0, 0), (15,15,16))
        with self.assertRaisesRegex(ValueError, 'NEAR_DUPLICATE_CAPTURE_PIXELS'):
            checker.compare_edges(first, second)
        mask = Image.new('L', checker.RESOLUTION, 0)
        mask.putpixel((30,100), 255)
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_MASK_AREA'):
            checker.compare_edges(first, picture(shift=2, shade=220), mask)

    def test_optional_manifest_fov_uses_optics(self):
        del self.manifest['cameras'][0]['fov_y_deg']
        self.bind_manifest()
        self.assertTrue(self.measure()['thresholds_passed'])

    def test_huge_png_header_rejects_before_pillow_warning(self):
        def chunk(kind, raw):
            return struct.pack('>I', len(raw)) + kind + raw + struct.pack('>I', zlib.crc32(kind+raw))
        raw = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 100000,100000,8,2,0,0,0))
        raw += chunk(b'IDAT', zlib.compress(b'')) + chunk(b'IEND', b'')
        with self.assertRaisesRegex(ValueError, 'IMAGE_FORMAT_OR_SIZE_MISMATCH'):
            checker.read_image(self.root, self.image_ref('oversized.png',raw))

    def test_paths_links_transparency_and_dimensions_reject(self):
        ref = self.data['cameras'][0]['web']['image']
        for name in ('../web.png', '/web.png', 'a//b.png', './web.png', 'C:/web.png'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'INVALID_IMAGE_PATH'):
                checker.read_image(self.root, dict(ref, path=name))
        (self.root / 'link.png').symlink_to(self.root / 'web.png')
        with self.assertRaisesRegex(ValueError, 'INVALID_IMAGE_FILE'):
            checker.read_image(self.root, dict(ref, path='link.png'))
        for image, code in ((Image.new('RGB', (128,128)), 'IMAGE_FORMAT_OR_SIZE_MISMATCH'),
                            (Image.new('RGBA', checker.RESOLUTION, (1,2,3,0)), 'TRANSPARENT_CAPTURE')):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code):
                checker.read_image(self.root, self.image_ref('bad.png', self.png(image)))

    def test_linked_evidence_and_duplicate_json_reject(self):
        self.measure()
        linked = self.root / 'linked.json'
        linked.symlink_to(self.evidence)
        with self.assertRaisesRegex(ValueError, 'INVALID_EVIDENCE_FILE'):
            checker.measure(self.package, linked)
        self.evidence.write_text('{"schema":1,"schema":2}')
        with self.assertRaises(ValueError):
            checker.measure(self.package, self.evidence)

    def test_cli_writes_once_and_reports_invalid_input_without_traceback(self):
        self.measure()
        output = self.root / 'report.json'
        command = [sys.executable, str(TOOLS / 'check_camera_evidence.py'), '--package-dir', str(self.package),
                   '--evidence', str(self.evidence), '--output', str(output)]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        before = output.read_bytes()
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(before, output.read_bytes())
        self.assertNotIn('Traceback', second.stderr)
        self.assertNotIn(str(self.root), second.stderr)


if __name__ == '__main__':
    unittest.main()
