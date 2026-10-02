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


class Schedule(unittest.TestCase):
    def test_one_per_day_in_approval_order(self):
        from datetime import datetime, timezone
        from x2fb import config
        now = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)  # 12:00 الرياض، قبل النافذة
        state = {"published_days": {}, "items": {
            "a": {"id": "a", "status": "approved", "approved_at": "2026-10-02T08:00:00+00:00", "created": "x", "fb_text": "أول"},
            "b": {"id": "b", "status": "approved", "approved_at": "2026-10-02T07:00:00+00:00", "created": "x", "fb_text": "ثانٍ"},
            "c": {"id": "c", "status": "pending", "created": "x", "fb_text": "معلّق"}}}
        with mock.patch.object(config, "MAX_PER_DAY", 1), mock.patch.object(config, "PUBLISH_HOURS", "17-22"):
            q = main._queue([i for i in state["items"].values() if i["status"] == "approved"])
            self.assertEqual([i["id"] for i in q], ["b", "a"])
            days = main._slots(state, now, 3)
            self.assertEqual([d.day for d in days], [2, 3, 4])
            state["published_days"]["2026-10-02"] = 1  # نُشر اليوم
            self.assertEqual([d.day for d in main._slots(state, now, 2)], [3, 4])
            late = datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)  # 23:00 الرياض، بعد النافذة
            self.assertEqual(main._slots({"published_days": {}}, late, 1)[0].day, 3)
            self.assertIn("ثانٍ", main._queue_text(state, now).splitlines()[1])


class Video(unittest.TestCase):
    def test_picks_highest_bitrate_mp4(self):
        t = {"extendedEntities": {"media": [{"type": "video", "video_info": {"variants": [
            {"content_type": "application/x-mpegURL", "url": "m3u8"},
            {"content_type": "video/mp4", "bitrate": 832000, "url": "low"},
            {"content_type": "video/mp4", "bitrate": 10368000, "url": "high"}]}}]}}
        self.assertEqual(xsrc.video_url(t), "high")
        self.assertEqual(xsrc.video_url({"extendedEntities": {"media": [{"type": "photo"}]}}), "")


if __name__ == "__main__":
    unittest.main()
