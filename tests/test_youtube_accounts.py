import unittest
from youtube_accounts import public_accounts, save_account, select_account, switch_profile, delete_account

class AccountsTest(unittest.TestCase):
    def test_reference_and_model_are_kept_per_account(self):
        cfg={"내_채널":"https://www.youtube.com/@ko", "유튜브_계정":[
            {"id":"ko", "name":"Korean", "url":"https://www.youtube.com/@ko", "language":"ko"},
            {"id":"jp", "name":"Japan", "url":"https://www.youtube.com/@jp", "language":"ja"}]}
        korean={"이름":"Korean", "언어":"ko", "마스코트":{"이미지":"ko.png", "생성_모델":"model-ko"}}
        jp=switch_profile(cfg,"jp","person",korean)
        self.assertEqual(jp["언어"],"ja")
        jp["마스코트"]["이미지"]="jp.png"
        jp["마스코트"]["생성_모델"]="model-jp"
        ko=switch_profile(cfg,"ko","person",jp)
        self.assertEqual(ko["마스코트"],korean["마스코트"])
        restored=switch_profile(cfg,"jp","person",ko)
        self.assertEqual(restored["마스코트"]["이미지"],"jp.png")
        self.assertEqual(restored["마스코트"]["생성_모델"],"model-jp")
        self.assertNotIn("profile",public_accounts(cfg)[0])

    def test_delete_account_preserves_other_profiles(self):
        cfg={"유튜브_계정":[{"id":"a","name":"A","url":"https://www.youtube.com/@a"},
             {"id":"b","name":"B","url":"https://www.youtube.com/@b"}], "유튜브_선택_mindam":"b"}
        delete_account(cfg,"b")
        self.assertEqual([a["id"] for a in cfg["유튜브_계정"]],["a"])
        self.assertNotIn("유튜브_선택_mindam",cfg)
    def test_migrate_and_select_without_exposing_key(self):
        cfg={"내_채널":"https://www.youtube.com/@old", "유튜브_API_키":"secret-old"}
        public=public_accounts(cfg)
        self.assertNotIn("secret-old", str(public))
        save_account(cfg,{"name":"Japan", "url":"https://www.youtube.com/@japan", "api_key":"secret-new"})
        self.assertEqual(len(cfg["유튜브_계정"]),2)
        select_account(cfg,cfg["유튜브_계정"][1]["id"],"person")
        self.assertEqual(cfg["유튜브_API_키"],"secret-new")
        self.assertEqual(cfg["내_채널"],"https://www.youtube.com/@japan")
        self.assertNotIn("secret-new",str(public_accounts(cfg)))
    def test_duplicate_updates_and_bad_input(self):
        cfg={}
        body={"name":"A","url":"https://www.youtube.com/@a","api_key":"secret"}
        save_account(cfg,body); save_account(cfg,{**body,"name":"B"})
        self.assertEqual(len(cfg["유튜브_계정"]),1)
        for url in ["http://www.youtube.com/@a","https://evil.test/@a","https://www.youtube.com/watch?v=abc"]:
            with self.assertRaises(ValueError): save_account(cfg,{**body,"url":url})
        with self.assertRaises(ValueError): select_account(cfg,"bad","person")
        with self.assertRaises(ValueError): select_account(cfg,cfg["유튜브_계정"][0]["id"],"bad")
