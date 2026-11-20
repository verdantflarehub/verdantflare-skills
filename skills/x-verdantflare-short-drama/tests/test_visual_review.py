import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('visual_review',
    Path(__file__).parents[1] / 'scripts/validate_visual_review.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        def put(name, content):
            (self.base / name).write_text(content)
            return {'path': name, 'sha256': hashlib.sha256(content.encode()).hexdigest()}
        self.put = put
        candidate = put('candidate', 'fixture media, not a real visual approval')
        original = put('original', 'original identity fixture')
        look = put('look', 'approved look fixture')
        evidence = put('comparison', 'synthetic comparison evidence')
        request = put('request', '{}')
        review = put('review.json', json.dumps({'status': 'pending_user_review',
                     'candidate_sha256': candidate['sha256'], 'request_sha256': request['sha256']}))
        self.report = {'candidate': candidate, 'request': request,
            'current_review': review, 'media_kind': 'image', 'contact_shot': True,
            'join_required': False,
            'actors': {'actor': {'original_identity': original, 'approved_look': look}},
            'checks': {}}
        for name in ['space', 'props', 'actor/identity', 'actor/wardrobe',
                     'actor/left_arm', 'actor/right_arm']:
            self.report['checks'][name] = {'outcome': 'pass',
                'observation': 'Synthetic test observation only.', 'evidence': [evidence]}
        self.report['checks']['actor/identity']['comparison'] = {
            'original_identity': original, 'approved_look': look, 'candidate': candidate}
        for side in ['left', 'right']:
            self.report['checks']['actor/' + side + '_arm']['chain'] = {
                k: 'synthetic observed location' for k in ['shoulder', 'elbow', 'wrist', 'hand']}

    def run_report(self, report=None):
        return MODULE.validate(report or self.report, self.base)

    def test_complete_record_never_grants_visual_or_user_approval(self):
        r = self.run_report()
        self.assertTrue(r['evidence_complete'], r['errors'])
        self.assertFalse(r['visual_quality_verified'])
        self.assertFalse(r['user_approved'])

    def test_user_rejection_overrides_positive_checks_and_old_approval_flag(self):
        self.report['current_review'] = self.put('review.json', json.dumps({
            'status': 'rejected_by_user', 'user_approved': True,
            'candidate_sha256': self.report['candidate']['sha256']}))
        self.assertFalse(self.run_report()['evidence_complete'])

    def test_unknown_identity_blocks_other_good_dimensions(self):
        self.report['checks']['actor/identity']['outcome'] = 'unknown'
        self.assertFalse(self.run_report()['evidence_complete'])

    def test_palm_only_check_does_not_cover_arm(self):
        self.report['checks']['actor/right_arm']['chain'] = {'hand': 'correct palm position'}
        self.assertFalse(self.run_report()['evidence_complete'])

    def test_changed_candidate_invalidates_review_even_if_candidate_spec_updated(self):
        self.report['candidate'] = self.put('candidate', 'new media bytes')
        self.assertFalse(self.run_report()['evidence_complete'])

    def test_missing_or_changed_evidence_blocks(self):
        (self.base / 'comparison').unlink()
        self.assertFalse(self.run_report()['evidence_complete'])

    def test_changed_request_invalidates_unchanged_candidate_review(self):
        self.report['request'] = self.put('request', '{"prompt":"changed"}')
        self.assertFalse(self.run_report()['evidence_complete'])

    def test_generated_face_crop_cannot_replace_original_comparison(self):
        self.report['checks']['actor/identity']['comparison']['original_identity'] = self.report['candidate']
        self.assertFalse(self.run_report()['evidence_complete'])

    def test_video_samples_do_not_replace_full_speed_review(self):
        self.report['media_kind'] = 'video'
        self.report['checks']['action'] = copy.deepcopy(self.report['checks']['space'])
        for check in self.report['checks'].values():
            check['source_frames'] = [0, 24, 48]
        self.assertFalse(self.run_report()['evidence_complete'])
        self.report['playback_reviewed'] = True
        self.assertTrue(self.run_report()['evidence_complete'])

    def test_missing_or_string_scope_cannot_skip_checks(self):
        for flag in ('contact_shot', 'join_required'):
            for value in (None, 'false', 0):
                report = copy.deepcopy(self.report)
                report[flag] = value
                self.assertFalse(self.run_report(report)['evidence_complete'])

    def test_join_required_blocks_missing_join_evidence(self):
        self.report['join_required'] = True
        self.assertFalse(self.run_report()['evidence_complete'])

    def test_malformed_review_status_fails_without_crashing(self):
        self.report['current_review'] = self.put('review.json', json.dumps({'status': []}))
        self.assertFalse(self.run_report()['evidence_complete'])

    def test_invalid_file_path_type_fails_without_crashing(self):
        self.report['candidate']['path'] = 17
        self.assertFalse(self.run_report()['evidence_complete'])

    def test_old_boolean_self_review_is_not_evidence(self):
        self.assertFalse(MODULE.validate({'status': 'pass', 'user_approved': True,
            'checks': ['identity preserved', 'hands correct']}, self.base)['evidence_complete'])


if __name__ == '__main__':
    unittest.main()
