"""اختبارات الأجزاء الحتمية: البوابة والعتبة وسلسلة الثريد. تشغيل: python -m unittest -v"""
import unittest
from unittest import mock

from x2fb import gate, main, xsrc

CLEAN = "هذا منشور نظيف بصوت يونس، فيه فكرة عملية واضحة وخطوة تطبقها اليوم في شغلك. " * 3


class Gate(unittest.TestCase):
    def test_clean_passes(self):
        self.assertEqual(gate.check(CLEAN), [])

    def test_catches_x_leftovers(self):
        for bad in ["https://t.co/x", "@someone", "#هاشتاق", "كمل الثريد", "بدل 199", "اليوم الوطني", "**عريض**"]:
            self.assertTrue(gate.check(CLEAN + " " + bad), bad)

    def test_dangling_arrow(self):
        self.assertIn("سهم معلّق في النهاية", gate.check(CLEAN + " 👇"))

    def test_short_allowed_with_media(self):
        short = "هذي عشر قواعد في الصورة تخلي الوكيل أذكى."
        self.assertIn("قصير جداً", gate.check(short))
        self.assertNotIn("قصير جداً", gate.check(short, has_media=True))

    def test_paths_with_asterisk_ok(self):
        self.assertEqual(gate.check(CLEAN + "\n.claude/skills/*/SKILL.md"), [])


class Threshold(unittest.TestCase):
    def test_needs_baseline(self):
        self.assertEqual(main._threshold({str(i): i for i in range(5)}), float("inf"))

    def test_top_share(self):
        base = {str(i): i for i in range(1, 21)}  # 1..20
        thr = main._threshold(base)
        self.assertEqual(sum(v >= thr for v in base.values()), 7)  # أعلى ٣٥٪ تقريباً


class Thread(unittest.TestCase):
    def test_chain_excludes_replies_to_others(self):
        head = {"id": "1", "text": "رأس", "author": {"userName": "younesbag1"}}
        found = [
            {"id": "2", "inReplyToId": "1", "text": "الجزء ٢", "author": {"userName": "younesbag1"}},
            {"id": "3", "inReplyToId": "2", "text": "الجزء ٣", "author": {"userName": "younesbag1"}},
            {"id": "9", "inReplyToId": "8", "text": "@someone رد على غيري", "author": {"userName": "younesbag1"}},
            {"id": "4", "inReplyToId": "1", "text": "@other رد", "author": {"userName": "younesbag1"}},
        ]
        with mock.patch.object(xsrc, "_get", return_value={"tweets": found, "has_next_page": False}):
            chain = xsrc.thread_chain(head)
        self.assertEqual([t["id"] for t in chain], ["1", "2", "3"])


class PageGuard(unittest.TestCase):
    def test_already_posted_ignores_whitespace(self):
        from x2fb import fb
        posts = [{"message": "سطر أول\n\nسطر   ثانٍ\n\n— يونس"}]
        self.assertTrue(fb.already_posted("سطر أول\nسطر ثانٍ — يونس", posts))
        self.assertFalse(fb.already_posted("نص آخر تماماً", posts))


class Telegram(unittest.TestCase):
    def _update(self, uid, chat, sender, data):
        return {"update_id": uid, "callback_query": {"id": str(uid), "data": data,
                "from": {"id": sender}, "message": {"chat": {"id": chat}}}}

    def test_only_owner_and_order_kept(self):
        from x2fb import config, tg
        res = {"result": [self._update(1, 42, 42, "ok:7"), self._update(2, 42, 99, "ok:8"),
                          self._update(3, 13, 13, "ok:9"), self._update(4, 42, 42, "no:7")]}
        with mock.patch.object(config, "TG_BOT_TOKEN", "t"), mock.patch.object(config, "TG_CHAT_ID", "42"), \
             mock.patch.object(tg, "request", return_value=res), mock.patch.object(tg, "_api"):
            decisions, offset = tg.poll(0)
        self.assertEqual(decisions, [("ok", "7"), ("no", "7")])
        self.assertEqual(offset, 5)


if __name__ == "__main__":
    unittest.main()
