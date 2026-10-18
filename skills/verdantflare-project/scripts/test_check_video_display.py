import copy
import unittest
from check_video_display import audit


class DisplayAuditTest(unittest.TestCase):
    def setUp(self):
        self.manifest = {'category':'video','files':[
            {'file_id':i,'content_ref':{'store_id':'s','artifact_id':i,'version_id':'v'}}
            for i in ['image','video','audio','doc']]}
        self.versions = {f['file_id']:{**f['content_ref'], 'mime':mime} for f,mime in zip(
            self.manifest['files'], ['image/jpeg','video/mp4','audio/wav','text/markdown'])}
        self.content = {'category':'video','cover_file_id':'image','video':{
            'output_file_id':'video','storyboards':[{'key':'scene','image_file_id':'image',
            'reference_file_ids':['image'],'result_file_ids':['video'],'file_ids':['audio','doc']}],
            'cast':[{'key':'actor','portrait_file_id':'image'}]}}

    def test_visible_candidate_does_not_need_selection_or_approval(self):
        snapshot=copy.deepcopy((self.manifest,self.content,self.versions))
        self.assertEqual(audit(self.manifest,self.content,self.versions,require_output=True,
            require_covers=True,expect_units=['scene'],expect_cast=['actor']),[])
        self.assertEqual(snapshot,(self.manifest,self.content,self.versions))

    def test_archive_and_names_do_not_satisfy_display(self):
        self.content['video']['output_file_id']=None
        self.content['video']['storyboards'][0]['image_file_id']=None
        self.content['video']['cast']=[]
        errors=audit(self.manifest,self.content,self.versions,require_output=True,require_covers=True,expect_cast=['actor'])
        self.assertEqual(len(errors),3)

    def test_mime_over_filename_and_cross_type_slots(self):
        b=self.content['video']['storyboards'][0]
        b['reference_file_ids']=['audio','doc']
        b['result_file_ids']=['image']
        self.assertEqual(len(audit(self.manifest,self.content,self.versions)),3)

    def test_stale_or_missing_metadata_cannot_pass(self):
        self.versions['video']['version_id']='old'
        del self.versions['image']
        self.assertGreaterEqual(len(audit(self.manifest,self.content,self.versions)),2)

    def test_valid_draft_can_be_empty(self):
        self.assertEqual(audit(self.manifest,{'category':'video','video':{}},{}),[])

    def test_no_category_migration_or_dangling_files(self):
        self.content['category']='ai_short_drama'
        self.assertTrue(audit(self.manifest,self.content,self.versions))
        self.content['category']='video'
        self.content['video']['storyboards'][0]['file_ids']=['missing']
        self.assertTrue(audit(self.manifest,self.content,self.versions))


if __name__ == '__main__':
    unittest.main()
