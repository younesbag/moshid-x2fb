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
    def _press(self, uid, chat, sender, data):
        return {"update_id": uid, "callback_query": {"id": str(uid), "data": data,
                "from": {"id": sender}, "message": {"chat": {"id": chat}}}}

    def _poll(self, updates):
        from x2fb import config, tg
        with mock.patch.object(config, "TG_BOT_TOKEN", "t"), mock.patch.object(config, "TG_CHAT_ID", "42"), \
             mock.patch.object(tg, "request", return_value={"result": updates}), mock.patch.object(tg, "_api"):
            return tg.poll(0)

    def test_only_owner_and_order_kept(self):
        events, offset = self._poll([
            self._press(1, 42, 42, "at:7:2000"), self._press(2, 42, 99, "now:8"),
            self._press(3, 13, 13, "now:9"), self._press(4, 42, 42, "no:7"),
            {"update_id": 5, "message": {"chat": {"id": 42}, "from": {"id": 42}, "text": "21:15",
                                         "reply_to_message": {"message_id": 500}}},
            {"update_id": 6, "message": {"chat": {"id": 42}, "from": {"id": 7}, "text": "now"}}])
        self.assertEqual([(e["kind"], e.get("id"), e.get("arg")) for e in events[:2]],
                         [("at", "7", "2000"), ("no", "7", "")])
        self.assertEqual((events[2]["kind"], events[2]["text"], events[2]["reply_to"]), ("text", "21:15", 500))
        self.assertEqual(len(events), 3)
        self.assertEqual(offset, 7)

    def test_old_ok_button_means_now(self):
        events, _ = self._poll([self._press(1, 42, 42, "ok:7")])
        self.assertEqual(events[0]["kind"], "now")


class Schedule(unittest.TestCase):
    def setUp(self):
        from datetime import datetime, timezone
        self.now = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)  # 12:00 مكة

    def test_resolve_and_parse_in_makkah_time(self):
        from x2fb import schedule
        at = schedule.resolve(20, 0, self.now)
        self.assertEqual(at.isoformat(), "2026-10-03T17:00:00+00:00")          # 20:00 مكة = 17:00 UTC
        self.assertEqual(schedule.resolve(8, 0, self.now).day, 4)               # فات وقتها اليوم ← غداً
        self.assertEqual(schedule.parse("٧:٣٠", self.now).isoformat(), "2026-10-04T04:30:00+00:00")
        self.assertEqual(schedule.parse("غدا 20:00", self.now).isoformat(), "2026-10-04T17:00:00+00:00")
        self.assertIsNone(schedule.parse("انشره بكرة", self.now))
        self.assertIsNone(schedule.parse("25:00", self.now))
        self.assertEqual(schedule.label(at, self.now), "اليوم 20:00")
        self.assertEqual(schedule.label(schedule.resolve(8, 0, self.now), self.now), "غداً 08:00")

    def _state(self):
        return {"published_days": {}, "items": {
            "a": {"id": "a", "status": "pending", "created": "x", "x_url": "u", "fb_text": "أول", "preview_msgs": [600, 500]},
            "b": {"id": "b", "status": "pending", "created": "x", "x_url": "u", "fb_text": "ثانٍ", "preview_msgs": [601, 501]},
            "c": {"id": "c", "status": "pending", "created": "x", "x_url": "u", "fb_text": "ثالث", "preview_msgs": [602, 502]}}}

    def test_events_schedule_reschedule_and_cancel(self):
        from x2fb import tg
        st = self._state()
        with mock.patch.object(tg, "notify"):
            main._apply_events(st, [
                {"kind": "at", "id": "a", "arg": "2000"},                 # زر وقت
                {"kind": "text", "text": "22:45", "reply_to": 501},       # رد بوقت مكتوب
                {"kind": "now", "id": "c", "arg": ""},                    # الآن
                {"kind": "at", "id": "a", "arg": "2130"},                 # تغيير موعد منشور موافَق عليه
                {"kind": "no", "id": "c", "arg": ""},                     # إلغاء بعد الموافقة
            ], self.now)
        it = st["items"]
        self.assertEqual(it["a"]["status"], "approved")
        self.assertEqual(it["a"]["publish_at"], "2026-10-03T18:30:00+00:00")
        self.assertEqual(it["b"]["publish_at"], "2026-10-03T19:45:00+00:00")
        self.assertEqual(it["c"]["status"], "vetoed")
        order = main._queue([i for i in it.values() if i["status"] == "approved"])
        self.assertEqual([i["id"] for i in order], ["a", "b"])
        text = main._queue_text(st, self.now)
        self.assertIn("اليوم 21:30", text)
        self.assertIn("اليوم 22:45", text)

    def test_bad_time_reply_changes_nothing(self):
        from x2fb import tg
        st = self._state()
        with mock.patch.object(tg, "notify") as note:
            main._apply_events(st, [{"kind": "text", "text": "بعدين", "reply_to": 500}], self.now)
        self.assertEqual(st["items"]["a"]["status"], "pending")
        note.assert_called_once()

    def test_only_due_items_publish_and_never_two_in_a_cycle(self):
        from datetime import timedelta
        from x2fb import config, fb
        st = self._state()
        for k, mins in (("a", -5), ("b", -1), ("c", 30)):
            st["items"][k].update(status="approved", approved_at="x",
                                  publish_at=(self.now + timedelta(minutes=mins)).isoformat())
        with mock.patch.object(config, "FB_PAGE_TOKEN", "t"), mock.patch.object(fb, "recent_posts", return_value=[]), \
             mock.patch.object(fb, "publish", return_value="1_2") as pub, mock.patch.object(main.tg, "notify"), \
             mock.patch.object(main.ledger, "save"):
            main._publish_due(st, self.now)
        self.assertEqual(pub.call_count, 2)
        # a حان موعده فنُشر الآن؛ b حان أيضاً فينتظر الدورة التالية؛ c بعد ٣٠ دقيقة فسُجّل موعده عند فيسبوك
        self.assertEqual([st["items"][k]["status"] for k in "abc"], ["published", "approved", "scheduled"])
        self.assertEqual(pub.call_args_list[0].args[2], int((self.now + timedelta(minutes=30)).timestamp()))
        self.assertIsNone(pub.call_args_list[1].args[2])

    def test_change_or_cancel_withdraws_from_facebook_first(self):
        from x2fb import fb, tg
        st = self._state()
        st["items"]["a"].update(status="scheduled", fb_id="p1", approved_at="x", publish_at="2026-10-03T17:00:00+00:00")
        st["items"]["b"].update(status="scheduled", fb_id="p2", approved_at="x", publish_at="2026-10-03T18:00:00+00:00")
        st["items"]["c"].update(status="scheduled", fb_id="p3", approved_at="x", publish_at="2026-10-03T19:00:00+00:00")

        def delete(pid):
            if pid == "p3":
                raise RuntimeError("down")
        with mock.patch.object(fb, "delete", side_effect=delete) as dele, mock.patch.object(tg, "notify"):
            main._apply_events(st, [{"kind": "no", "id": "a", "arg": ""},
                                    {"kind": "at", "id": "b", "arg": "2300"},
                                    {"kind": "no", "id": "c", "arg": ""}], self.now)
        self.assertEqual(dele.call_count, 3)
        self.assertEqual(st["items"]["a"]["status"], "vetoed")
        self.assertEqual((st["items"]["b"]["status"], st["items"]["b"]["publish_at"]),
                         ("approved", "2026-10-03T20:00:00+00:00"))
        self.assertEqual(st["items"]["c"]["status"], "scheduled")  # فشل السحب: يبقى على موعده ولا يُعلَّم ملغى

    def test_late_cycle_does_not_push_time_to_tomorrow(self):
        """اخترت 12:30 مكة والدورة السابقة كانت 12:00؛ دورتنا تأخرت إلى 14:00 ← يُنشر الآن لا غداً."""
        from datetime import datetime, timezone
        from x2fb import tg
        st = self._state()
        st["last_poll"] = "2026-10-03T09:00:00+00:00"            # 12:00 مكة
        late = datetime(2026, 10, 3, 11, 0, tzinfo=timezone.utc)  # 14:00 مكة
        typed_at = int(datetime(2026, 10, 3, 9, 30, tzinfo=timezone.utc).timestamp())
        with mock.patch.object(tg, "notify") as note:
            main._apply_events(st, [
                {"kind": "at", "id": "a", "arg": "1230"},
                {"kind": "at", "id": "b", "arg": "1000"},        # 10:00 كانت فاتت فعلاً لحظة الضغط ← غداً
                {"kind": "text", "text": "13:00", "reply_to": 602, "date": typed_at},
            ], late)
        self.assertEqual(st["items"]["a"]["publish_at"], "2026-10-03T09:30:00+00:00")
        self.assertEqual(st["items"]["b"]["publish_at"], "2026-10-04T07:00:00+00:00")
        self.assertEqual(st["items"]["c"]["publish_at"], "2026-10-03T10:00:00+00:00")  # رد على الجزء الأول من المعاينة
        self.assertEqual(note.call_count, 2)  # تنبيهان بأن الموعد فات ويُنشر الآن (a وc)

    def test_no_touching_a_scheduled_post_after_its_time(self):
        from x2fb import fb, tg
        st = self._state()
        st["items"]["a"].update(status="scheduled", fb_id="p1", approved_at="x", publish_at="2026-10-03T08:00:00+00:00")
        st["items"]["b"]["status"] = "published"
        with mock.patch.object(fb, "delete") as dele, mock.patch.object(tg, "notify") as note:
            main._apply_events(st, [{"kind": "no", "id": "a", "arg": ""}, {"kind": "now", "id": "b", "arg": ""}], self.now)
        dele.assert_not_called()
        self.assertEqual(st["items"]["a"]["status"], "scheduled")
        self.assertEqual(note.call_count, 2)

    def test_scheduled_marked_published_only_when_live(self):
        from datetime import timedelta
        from x2fb import fb
        st = self._state()
        past = (self.now - timedelta(minutes=20)).isoformat()
        for k, fid in (("a", "p1"), ("b", "p2"), ("c", "p3")):
            st["items"][k].update(status="scheduled", fb_id=fid, publish_at=past)
        answers = {"p1": True, "p2": False, "p3": None}
        with mock.patch.object(fb, "is_live", side_effect=lambda i, v: answers[i]), \
             mock.patch.object(main.tg, "notify"), mock.patch.object(main.ledger, "save"):
            main._publish_due(st, self.now)
        self.assertEqual([st["items"][k]["status"] for k in "abc"], ["published", "uncertain", "scheduled"])

    def test_lost_ledger_adopts_scheduled_post_with_its_id(self):
        from datetime import timedelta
        from x2fb import config, fb
        st = self._state()
        st["items"]["a"].update(status="approved", approved_at="x", publish_at=(self.now + timedelta(hours=3)).isoformat())
        posts = [{"id": "PAGE_99", "message": "أول", "created_time": "2026-10-03T08:00:00+0000", "scheduled": True}]
        with mock.patch.object(config, "FB_PAGE_TOKEN", "t"), mock.patch.object(fb, "recent_posts", return_value=posts), \
             mock.patch.object(fb, "publish") as pub, mock.patch.object(main.tg, "notify"), mock.patch.object(main.ledger, "save"):
            main._publish_due(st, self.now)
        pub.assert_not_called()
        self.assertEqual((st["items"]["a"]["status"], st["items"]["a"]["fb_id"]), ("scheduled", "PAGE_99"))

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
