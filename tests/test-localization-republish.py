"""Only the known deleted localization build gets the explicit pointer exception."""
import base64,importlib.util,json,unittest
from pathlib import Path
from unittest.mock import Mock,patch
R=Path(__file__).resolve().parents[1];spec=importlib.util.spec_from_file_location('_rebuild_checks',R/'scripts/republish-localization.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class RepublishTests(unittest.TestCase):
 def setUp(self):
  self.notes=(R/'RELEASE-NOTES.txt').read_text(encoding='utf-8');self.old=dict(version=m.TARGET,sha256=m.PREVIOUS_SHA256,notes=self.notes);self.new=dict(version=m.TARGET,sha256='a'*64,notes=self.notes)
  self.api=Mock(return_value=dict(content=base64.b64encode(b'old').decode(),sha='pointer-before'))
 def run_pointer(self,old=None,new=None,asset=b'new'):
  def verify(body):return (self.old if old is None else old) if body==b'old' else (self.new if new is None else new)
  with patch.object(m.pub,'verify_envelope',side_effect=verify),patch.object(m.pub,'released_assets',return_value=asset),patch.object(m.pub,'json_document',side_effect=lambda b:b),patch.object(m,'normal_advance',return_value=False) as normal:
   result=m.advance_rebuilt_pointer(b'new',self.api)
   return result,normal.call_count
 def test_exact_old_digest_and_version_use_compare_and_swap(self):
  result,count=self.run_pointer();self.assertTrue(result);self.assertEqual(count,0)
  self.assertEqual(self.api.call_args.kwargs['data']['sha'],'pointer-before');self.assertEqual(self.api.call_args.kwargs['method'],'PUT')
 def test_unknown_digest_retains_normal_immutable_version_policy(self):
  result,count=self.run_pointer(old=dict(self.old,sha256='b'*64));self.assertFalse(result);self.assertEqual(count,1)
 def test_other_version_retains_normal_publisher_policy(self):
  result,count=self.run_pointer(new=dict(self.new,version='1.8.0-r1'));self.assertFalse(result);self.assertEqual(count,1)
 def test_unchanged_digest_retains_normal_idempotent_policy(self):
  result,count=self.run_pointer(new=dict(self.new,sha256=m.PREVIOUS_SHA256));self.assertFalse(result);self.assertEqual(count,1)
 def test_changed_release_notes_are_rejected(self):
  with self.assertRaises(m.pub.PublishError):self.run_pointer(new=dict(self.new,notes='changed notes'))
 def test_mismatched_released_assets_are_rejected(self):
  with self.assertRaises(m.pub.PublishError):self.run_pointer(asset=b'other')
 def test_missing_compare_and_swap_revision_is_rejected(self):
  self.api.return_value=dict(content=base64.b64encode(b'old').decode())
  with self.assertRaises(m.pub.PublishError):self.run_pointer()
if __name__=='__main__':unittest.main(verbosity=2)
