import tempfile
import unittest
from unittest import mock
import test_video_client_fal
from mcp_fixture import schemas
from video_client import generate_mcp, resume_mcp, ClientError


class InterpolationClientTests(unittest.TestCase):
    def test_resume_waits_for_delivery_after_native_success(self):
        task = 'video_task_' + 'a' * 32
        states = [{'status': 'succeeded', 'delivery': {'status': s}} for s in ('running', 'succeeded')]
        with mock.patch('video_client.mcp_status', side_effect=states) as status, mock.patch('video_client.mcp_result', return_value={'delivery': {'final_artifact_id': 'final'}}) as result, mock.patch('video_client.time.sleep'):
            value = resume_mcp(None, task, 1, 10)
        self.assertEqual(status.call_count, 2)
        self.assertEqual(result.call_count, 1)
        self.assertEqual(value['delivery']['final_artifact_id'], 'final')

    def test_failed_delivery_returns_original_without_new_generation(self):
        value = {'status': 'succeeded', 'delivery': {'status': 'failed'}, 'artifact_id': 'original'}
        with mock.patch('video_client.mcp_status', return_value=value), mock.patch('video_client.mcp_result', return_value=value), mock.patch('video_client.generate_mcp') as generate:
            self.assertEqual(resume_mcp(None, 'video_task_'+'a'*32, 1, 10), value)
            generate.assert_not_called()

    def test_option_reaches_preflight_and_create_and_conflicts_with_disabled(self):
        fixture = test_video_client_fal.VideoClientFalTests()
        inventory = {'tools': ['video.preflight'], 'schemas': schemas(), 'create_tool': 'video.create', 'capabilities': {
            'routes': [{'route': 'h3-singularity', 'model': 'minimax-h3-ref2va', 'configured': True, 'duration_seconds': {'minimum': 4, 'maximum': 15}, 'aspect_ratios': ['16:9']}],
            'postprocess': {'interpolate': {'configured': True, 'readiness': 'ready'}}}}
        with tempfile.TemporaryDirectory() as root, mock.patch('video_client.check_mcp_tools', return_value=inventory), mock.patch('video_client.call_mcp_tool', return_value={'video_task_id': 'video_task_'+'a'*32}) as call:
            args = fixture.args(route='h3-singularity', ratio='16:9', interpolate=True)
            generate_mcp(fixture.config(root), args)
            self.assertEqual([c.args[1] for c in call.call_args_list], ['video.preflight', 'video.create'])
            for c in call.call_args_list:
                self.assertEqual(c.args[2]['postprocess'], {'interpolate': {'profile': 'rife_2x'}})
            args.interpolate = False
            with self.assertRaises(ClientError):
                generate_mcp(fixture.config(root), args)
