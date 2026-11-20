"""Offline contract fixtures for the exact bundled VoiceModel schema."""
import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry


class VoiceModelSchemaTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'public-docs/music-snapshots/voice-model.schema.json'
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        def no_remote(uri):
            raise AssertionError('external schema access forbidden')
        self.validator = Draft202012Validator(schema, registry=Registry(retrieve=no_remote))
        self.fixture = {
            'schema_version': 1, 'kind': 'voice-model',
            'native_model': {'service_id': 'music', 'model_id': 'synthetic-fixture'},
            'files': [{'path': 'models/fixture.pth', 'purpose': 'weights', 'required': True}],
            'compatibility': {'capability_id': 'voice.convert', 'model_format': 'synthetic-format'},
            'validation': {'result': 'partial', 'capabilities': ['voice.convert'],
                           'evidence_paths': ['review/fixture.json']},
        }

    def test_local_refs_validate_minimal_contract_without_network(self):
        self.validator.validate(self.fixture)

    def test_missing_weights_and_extra_fields_rejected(self):
        for invalid in ({}, dict(self.fixture, secret='not-allowed'),
                        dict(self.fixture, files=[{'path': 'note.md', 'purpose': 'documentation', 'required': True}])):
            self.assertFalse(self.validator.is_valid(invalid))

    def test_path_escape_rejected_in_model_and_evidence(self):
        for path in ('../model.pth', '/model.pth', 'models//x', 'C:/x', 'models\\x', '.vf/private', 'models/x.'):
            for section in ('files', 'validation'):
                candidate = copy.deepcopy(self.fixture)
                if section == 'files':
                    candidate['files'][0]['path'] = path
                else:
                    candidate['validation']['evidence_paths'] = [path]
                with self.subTest(path=path, section=section):
                    self.assertFalse(self.validator.is_valid(candidate))


if __name__ == '__main__':
    unittest.main()
