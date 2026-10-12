import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import codex_switch as switch
from codex_bridge import read, write


class DeferredCodexTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        write(self.root/'설정.json',{'유튜브_선택_person':'japanese','AI':'deepseek-web'})
        self.queue={'status':'running','items':[{'id':'episode','status':'working'}]}
        self.job={'status':'running','kind':'queue_pipeline','started':'now'}
        self.posts=[]
    def call(self,port,path,body=None):
        if path=='/api/queue':return self.queue
        if path=='/api/job':return self.job
        self.posts.append(body);return {'ok':True}
    def test_waits_for_finished_episode_and_queue_then_switches_once(self):
        with patch.object(switch,'call',side_effect=self.call):
            value=switch.reserve(self.root,launch=False)
            self.assertFalse(switch.tick(self.root,value['token']))
            self.assertEqual(self.posts,[])
            self.job['status']='done';self.queue['items'][0]['status']='done'
            self.assertFalse(switch.tick(self.root,value['token']))
            self.queue['status']='done'
            self.assertTrue(switch.tick(self.root,value['token']))
            self.assertTrue(switch.tick(self.root,value['token']))
        self.assertEqual(self.posts,[{}])
        self.assertEqual(read(switch.state_file(self.root))['status'],'done')
    def test_error_does_not_switch_and_cancel_releases_reservation(self):
        with patch.object(switch,'call',side_effect=self.call):
            value=switch.reserve(self.root,launch=False)
            self.job['status']='error';self.queue['items'][0]['status']='error';self.queue['status']='paused'
            self.assertFalse(switch.tick(self.root,value['token']))
            self.queue['items'][0]['status']='cancelled'
            self.assertTrue(switch.tick(self.root,value['token']))
        self.assertEqual(self.posts,[])
    def test_changed_channel_never_receives_old_transition(self):
        with patch.object(switch,'call',side_effect=self.call):
            value=switch.reserve(self.root,launch=False)
            write(self.root/'설정.json',{'유튜브_선택_person':'korean'})
            self.assertTrue(switch.tick(self.root,value['token']))
        self.assertEqual(self.posts,[])


if __name__=='__main__':unittest.main()
