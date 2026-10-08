import unittest
from youtube_accounts import public_accounts, save_account, select_account

class AccountsTest(unittest.TestCase):
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
