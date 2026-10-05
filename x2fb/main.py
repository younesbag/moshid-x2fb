"""نقطة التشغيل.

  python -m x2fb check        فحص كل الاتصالات (لا ينشر شيئاً)
  python -m x2fb dry [--n 5]  معاينة تحويل أفضل منشوراتك الأخيرة في out/preview.md (لا ينشر ولا يغيّر السجل)
  python -m x2fb run          دورة واحدة
  python -m x2fb loop --sync  تشغيل مستمر (GitHub Actions): تلغرام لحظياً، X كل ساعة، السجل يُدفع عند كل تغيير
"""
import argparse
import subprocess
import sys
import time
from datetime import timedelta, timezone

from . import config, fb, gate, ledger, schedule, tg, xsrc
from .transform import revise, transform



def _item_from(t: dict) -> dict:
    return {
        "id": t["id"], "x_url": t.get("url") or f"https://x.com/{config.X_USERNAME}/status/{t['id']}",
        "created": xsrc.parse_time(t["createdAt"]).isoformat(), "text": t.get("text", ""),
        "score": xsrc.score(t), "photos": xsrc.photos(t), "video": xsrc.has_video(t),
        "quote": bool(t.get("quoted_tweet")), "article": bool(t.get("article")),
    }


def _threshold(baseline: dict) -> float:
    vals = sorted(baseline.values())
    if len(vals) < 8:
        return float("inf")  # لا حكم قبل وجود خط أساس كافٍ
    return vals[min(len(vals) - 1, int(len(vals) * (1 - config.TOP_SHARE)))]


def _percentile(baseline: dict, s: float) -> float:
    vals = list(baseline.values())
    return 100 * sum(v >= s for v in vals) / len(vals) if vals else 0


def draft(item: dict, tweet: dict) -> None:
    """تحويل + بوابة. يغيّر حالة البند فقط."""
    if item["article"]:
        ledger.set_status(item, "skipped", reason="مقال X طويل")
        return
    chain = xsrc.thread_chain(tweet)
    video = xsrc.video_url(chain)
    if xsrc.has_video(chain) and not video:
        ledger.set_status(item, "manual", reason="فيه فيديو بلا نسخة MP4 قابلة للنقل")
        return
    item["photos"] = xsrc.photos(chain)
    item["video_url"] = video
    out = transform(chain)
    if out.get("decision") != "publish":
        ledger.set_status(item, "skipped", reason=out.get("skip_reason", "قرار النموذج"))
        return
    text = out["text"].strip()
    # الفيديو يُنقل دائماً مع منشوره؛ الصور بقرار النموذج. والفيديو يغني عن الصور
    use_media = bool(out.get("use_media")) and bool(item["photos"]) and not video
    problems = gate.check(text, use_media or bool(video))
    if problems:
        ledger.set_status(item, "blocked", reason="البوابة: " + "، ".join(problems), fb_text=text)
        return
    ledger.set_status(item, "drafted", fb_text=text + "\n\n" + config.SIGNATURE,
                      use_media=use_media, notes=out.get("notes", ""),
                      thread_len=len(chain))


def _queue(items: list[dict]) -> list[dict]:
    """ترتيب النشر: الأبكر موعداً أولاً؛ «الآن» (بلا موعد) يسبق الجميع بترتيب الموافقة."""
    return sorted(items, key=lambda i: (i.get("publish_at") or "", i.get("approved_at") or i["created"]))


def _queue_text(state: dict, now) -> str:
    q = _queue([i for i in state["items"].values() if i["status"] in ("approved", "scheduled")])
    if not q:
        return "🗓 لا منشورات مجدولة الآن."
    lines = [f"🗓 المجدول للنشر ({len(q)}) — بتوقيت مكة:"]
    for it in q:
        first = it["fb_text"].strip().splitlines()[0][:55]
        kind = "🎬 " if it.get("video_url") else ("🖼 " if it.get("use_media") else "")
        at = it.get("publish_at")
        when = schedule.label(ledger.datetime.fromisoformat(at), now) if at else "الآن"
        mark = " ✓" if it["status"] == "scheduled" else " (ينتظر التسجيل)"
        lines.append(f"• {when}{mark} — {kind}{first}")
    lines.append("✓ = مسجّل عند فيسبوك وسيُنشر في دقيقته.")
    lines.append("لتغيير وقت منشور: اضغط وقتاً آخر تحت معاينته، أو رُدّ عليها بوقت. وللإلغاء «❌ إلغاء».")
    return chr(10).join(lines)


STATUS_AR = {"published": "نُشر فعلاً", "vetoed": "ملغى", "expired": "انتهت مهلته", "failed": "فشل نشره",
             "uncertain": "نتيجة نشره غير مؤكدة", "publishing": "قيد النشر", "skipped": "متخطّى", "blocked": "موقوف"}


EDIT_VERBATIM = ("نص:", "نص جديد:", "النص:")


def _edited_text(it: dict, request_text: str) -> str | None:
    """«نص: …» = استبدال حرفي بنصك؛ أي رد آخر = تعليمة يطبّقها وكيل التحويل. يعيد None عند الفشل."""
    body = it["fb_text"]
    sig = config.SIGNATURE
    if body.rstrip().endswith(sig):
        body = body.rstrip()[: -len(sig)].rstrip()
    req = request_text.strip()
    verbatim = next((req[len(p):].strip() for p in EDIT_VERBATIM if req.startswith(p)), None)
    if verbatim is not None:
        text, how = verbatim, "استبدال حرفي"
    else:
        try:
            text, how = revise(body, req).get("text", "").strip(), "تعليمة"
        except Exception as e:
            tg.notify(f"🔴 تعذّر تطبيق التعديل الآن ({str(e)[:150]}). أعد إرسال طلبك بعد قليل.")
            return None
    media = bool(it.get("video_url")) or bool(it.get("use_media"))
    problems = gate.check(text, media)
    if problems:
        tg.notify(f"⚠️ لم أطبّق التعديل ({how}) لأن النص الناتج لا يجتاز الفحص: {'، '.join(problems)}.\n"
                  "بقيت المسودة كما كانت. جرّب صياغة أخرى أو أرسل «نص: …» بالنص الكامل.")
        return None
    return text + "\n\n" + sig


def _apply_events(state: dict, events: list[dict], now) -> bool:
    """يطبّق ضغطات وردود تلغرام على السجل. يعيد True إن وجب إرسال الجدول.

    المواعيد تُحسب من لحظة اختيارك لا من لحظة الدورة (الدورات قد تتأخر ساعات):
    الرد المكتوب يحمل وقته، والضغطة تُنسب لآخر دورة سابقة. موعد فات بسبب تأخرنا يُنشر الآن لا غداً.
    """
    items = state["items"]
    parse = ledger.datetime.fromisoformat
    by_msg = {m: i for i in items.values() for m in (i.get("preview_msgs") or [])}
    press_ref = parse(state["last_poll"]) if state.get("last_poll") else now
    changed = False
    for ev in events:
        if ev["kind"] == "text":
            it = by_msg.get(ev.get("reply_to"))
            if it is None:
                changed = True  # رسالة حرة: يُرسل الجدول الحالي جواباً
                continue
            ref = ledger.datetime.fromtimestamp(ev["date"], timezone.utc) if ev.get("date") else press_ref
            text = (ev.get("text") or "").strip()
            # رد قصير فيه وقت = موعد؛ أي رد آخر على المعاينة = تعديل للمسودة
            at = schedule.parse(text, ref) if len(text) <= 25 else None
            if at is None:
                ev = {**ev, "kind": "edit"}
        else:
            it = items.get(ev["id"])
            at = None
            if ev["kind"] == "at" and len(ev.get("arg", "")) == 4 and ev["arg"].isdigit():
                at = schedule.resolve(int(ev["arg"][:2]), int(ev["arg"][2:]), press_ref)
        if not it:
            continue
        # الرسالة التي ضُغط زرها تُضم لرسائل المنشور: معاينة قديمة أُرسلت قبل تتبّع المعرّفات
        # تتحدث أزرارها لحالته الحقيقية (لا يبقى «نشر» تحت منشور نُشر)
        mid = ev.get("msg_id")
        if mid and mid not in (it.get("kb_msgs") or []):
            it["kb_msgs"] = (it.get("kb_msgs") or []) + [mid]
            it["kb_state"] = ""
        if ev["kind"] == "noop":
            continue
        first = it.get("fb_text", "").strip().splitlines()[0][:40] if it.get("fb_text") else it["x_url"]
        if it["status"] == "expired" and it.get("fb_text"):
            ledger.set_status(it, "pending", reason="أُعيد بطلبك من تلغرام")
        new_text = None
        if ev["kind"] == "edit" and it["status"] in ("pending", "drafted", "approved", "scheduled"):
            if it["status"] == "scheduled" and it.get("publish_at") and now >= parse(it["publish_at"]):
                tg.notify(f"هذا المنشور حان موعده ونُشر، فلا يمكن تعديله من هنا:\n{first}")
                continue
            # النص الجديد يُحسب ويُفحص أولاً — لا يُسحب منشور مجدول من فيسبوك ثم يفشل تعديله
            new_text = _edited_text(it, ev["text"])
            if new_text is None:
                continue
        if it["status"] == "scheduled" and it.get("publish_at") and now >= parse(it["publish_at"]):
            # فات موعده: فيسبوك نشره (أو يكاد). السحب الآن يحذف منشوراً حياً — لا نلمسه
            tg.notify(f"هذا المنشور حان موعده ونُشر، فلا يمكن تغييره من هنا:\n{first}")
            continue
        if it["status"] not in ("pending", "drafted", "approved", "scheduled"):
            tg.notify(f"لا تغيير: هذا المنشور {STATUS_AR.get(it['status'], it['status'])}.\n{first}")
            continue
        # منشور مسجّل عند فيسبوك: أي تغيير (إلغاء أو موعد جديد) يبدأ بسحبه من هناك.
        # إن فشل السحب لا نغيّر شيئاً — وإلا نُشر في موعده القديم وأنت تظنه ملغى
        if it["status"] == "scheduled":
            try:
                fb.delete(it["fb_id"])
            except Exception as e:
                tg.notify(f"🔴 تعذّر سحب المنشور المجدول من فيسبوك، فبقي على موعده: {str(e)[:200]}\n{first}")
                continue
            it["fb_id"] = ""
        if ev["kind"] == "no":
            ledger.set_status(it, "vetoed")
        elif ev["kind"] == "edit":
            # النص تغيّر: يعود للمراجعة بمعاينة جديدة ويُختار وقته من جديد — لا يُنشر نص لم تره كاملاً
            it["fb_text"] = new_text
            it["publish_at"] = ""
            it["previewed"] = None
            ledger.set_status(it, "pending", edited_at=now.isoformat())
            tg.notify(f"✏️ عُدّلت المسودة. المعاينة الجديدة تصلك الآن — اختر وقتها من أزرارها:\n{new_text.splitlines()[0][:50]}")
        else:  # now | at | text: موافقة، أو تغيير موعد منشور موافَق عليه
            ledger.set_status(it, "approved", approved_at=it.get("approved_at") or now.isoformat())
            it["publish_at"] = at.isoformat() if at else ""
            if at and at <= now:
                tg.notify(f"⏱ الموعد الذي اخترته ({schedule.label(at, now)}) فات قبل أن تصل دورتنا — يُنشر الآن:\n{first}")
        changed = True
    return changed


def cycle(wait: int = 0) -> None:
    """دورة واحدة. wait>0 = انتظار طويل لتلغرام (وضع loop): الضغطة تُطبَّق لحظة وصولها."""
    state = ledger.load()
    items = state["items"]
    state.setdefault("baseline", {})
    # لحظة بدء الاستماع تُحفظ خارج السجل المودَع (لا كوميت كل دقيقة): مرجع «لحظة الضغط»
    state["last_poll"] = ledger.runtime().get("last_poll", "")
    poll_start = ledger.now()

    # ١) تلغرام: موافقات بأوقاتها، تغيير مواعيد، إلغاءات — بترتيب وقوعها
    events, state["tg_offset"] = tg.poll(state.get("tg_offset", 0), wait)
    now = ledger.now()
    changed = _apply_events(state, events, now)
    ledger.save_runtime({"last_poll": poll_start.isoformat()})
    state.pop("last_poll", None)
    ledger.save(state)

    def age_h(it):
        return (now - ledger.datetime.fromisoformat(it["created"])).total_seconds() / 3600

    # الجلب من X والحكم والصياغة مرة في الساعة. عطله لا يوقف النشر والجدولة
    last = state.get("last_fetch")
    if not last or (now - ledger.datetime.fromisoformat(last)).total_seconds() >= 50 * 60:
        try:
            _intake(state, now, age_h)
            state["last_fetch"] = now.isoformat()
        except Exception as e:
            print(f"تعذّر الجلب/الصياغة هذه الدورة: {str(e)[:300]}")
        ledger.save(state)

    # معاينات أُرسلت قبل أزرار المواعيد تُعاد بلوحتها الجديدة (ثلاث في الدورة)
    for it in items.values():
        if it["status"] == "pending" and it.get("previewed") and "preview_msgs" not in it:
            it["previewed"] = None
    # المعاينات: تُرسل لكل منشور معلّق لم يُعاين بعد. المهلة تبدأ من لحظة المعاينة — لا يُنشر شيء لم تره
    if tg.enabled():
        for it in [i for i in items.values() if i["status"] == "pending" and not i.get("previewed")][:3]:
            try:
                # تُضاف معرّفات المعاينة الجديدة إلى القديمة: الرد على أي معاينة سابقة يبقى منسوباً لمنشورها
                sent = tg.preview(it)
                it["preview_msgs"] = (it.get("preview_msgs") or []) + sent
                it["kb_msgs"] = (it.get("kb_msgs") or []) + sent[-1:]  # الأزرار على آخر جزء من كل معاينة
                it["kb_state"] = "pending"
            except Exception as e:
                print(f"تعذّرت المعاينة: {str(e)[:200]}")
                break
            it["previewed"] = now.isoformat()
            it["deadline"] = (now + timedelta(hours=config.VETO_HOURS)).isoformat()
            ledger.save(state)

    # منشور عالق في «publishing» يعني دورة انقطعت أثناء النشر: لا يُعاد آلياً أبداً، بل يُنبَّه عنه
    for it in items.values():
        if it["status"] == "publishing" and not it.get("stuck_alerted"):
            tg.notify(f"🟠 منشور عالق أثناء النشر — تحقّق يدوياً من الصفحة:\n{it['x_url']}")
            it["stuck_alerted"] = now.isoformat()
    ledger.save(state)

    _publish_due(state, now)
    if tg.enabled():
        _sync_keyboards(state, now)
        ledger.save(state)
    # الجدول يُرسل بعد التسجيل عند فيسبوك ليحمل علامة ✓ الصحيحة
    if changed:
        tg.notify(_queue_text(state, now))


def _status_keyboard(it: dict, now):
    """الأزرار التي تعكس حالة المنشور: لا يبقى «انشر الآن» تحت منشور نُشر أو أُلغي."""
    xid, st = it["id"], it["status"]
    at = it.get("publish_at")
    when = schedule.label(ledger.datetime.fromisoformat(at), now) if at else ""
    if st == "published":
        url = f"https://facebook.com/{it['fb_id']}" if it.get("fb_id") else f"noop:{xid}"
        return [[("✅ نُشر على الصفحة" + (" — افتحه" if it.get("fb_id") else ""), url)]]
    if st == "scheduled":
        return tg.keyboard(xid, f"🗓 مجدول عند فيسبوك: {when} ✓")
    if st == "approved":
        return tg.keyboard(xid, f"⏳ موافَق — {when}" if when else "⏳ يُنشر في الدقائق القادمة")
    if st in ("publishing", "uncertain"):
        return [[("⏳ قيد النشر — تحقّق من الصفحة" if st == "publishing" else "⚠️ نتيجة النشر غير مؤكدة", f"noop:{xid}")]]
    if st == "vetoed":
        return [[("❌ أُلغي", f"noop:{xid}")]]
    if st == "failed":
        return [[("🔴 فشل النشر", f"noop:{xid}")]]
    if st == "pending":
        return tg.keyboard(xid)
    return None  # expired وغيرها: تبقى الأزرار كما هي (المنتهي يمكن إعادته بضغطة)


def _sync_keyboards(state: dict, now) -> None:
    """يحدّث أزرار كل معاينة تغيّرت حالة منشورها منذ آخر عرض. فشل التحديث لا يوقف الدورة."""
    for it in state["items"].values():
        msgs = it.get("kb_msgs") or it.get("preview_msgs") or []  # معاينات قبل kb_msgs: كلها رسالة واحدة غالباً
        if not msgs:
            continue
        key = f"{it['status']}|{it.get('publish_at', '')}|{it.get('fb_id', '')}"
        if it.get("kb_state") == key:
            continue
        kb = _status_keyboard(it, now)
        if kb is None:
            it["kb_state"] = key
            continue
        try:
            for m in msgs:
                tg.set_keyboard(m, kb)
            it["kb_state"] = key
        except Exception as e:
            print(f"تعذّر تحديث أزرار المعاينة: {str(e)[:200]}")


def _intake(state: dict, now, age_h) -> None:
    """جلب آخر المنشورات، الحكم على أدائها، وصياغة مرشح واحد."""
    items, baseline = state["items"], state["baseline"]
    # تحديث أرقام كل ما زال في نافذة الحكم أو النقل بمعرّفاته
    # (الناشر النشط تخرج منشوراته من آخر ٢٠ قبل ٢٤ ساعة — بلا هذا يتجمد رقمها مبكراً)
    tweets = {t["id"]: t for t in xsrc.latest_originals(pages=1 if baseline else 3)}
    for tid, t in tweets.items():
        if tid not in items:
            items[tid] = _item_from(t)
            ledger.set_status(items[tid], "seen")
    live = ("seen", "candidate", "pending", "approved")
    missing = [k for k, it in items.items() if it["status"] in live and k not in tweets
               and age_h(it) <= config.MAX_AGE_HOURS + 48]
    if missing:
        try:
            tweets.update(xsrc.by_ids(missing))
        except Exception as e:
            print(f"تعذّر تحديث المنشورات بالمعرّف: {str(e)[:200]}")
    for tid, t in tweets.items():
        if tid in items:
            items[tid]["score"] = xsrc.score(t)

    # الحكم على ما بلغ عمر الحكم — خط الأساس يُحدَّث أولاً ثم تُحسب العتبة مرة واحدة
    for it in items.values():
        if age_h(it) >= config.MIN_AGE_HOURS:
            baseline[it["id"]] = it["score"]
    thr = _threshold(baseline)
    for it in items.values():
        age = age_h(it)
        # ما لم توافق عليه خلال يومين بعد نافذة النقل صار قديماً. الموافَق عليه لا يسقط بالعمر
        # (حتى ٣٠ يوماً) لأنك اخترت موعده بنفسك
        if it["status"] == "candidate" and age > config.MAX_AGE_HOURS + 48:
            ledger.set_status(it, "expired", reason="تجاوز مهلة النقل")
            continue
        if it["status"] == "pending" and age > (30 * 24 if it.get("previewed") else config.MAX_AGE_HOURS + 48):
            ledger.set_status(it, "expired", reason="بقي بلا قرار أكثر من ٣٠ يوماً" if it.get("previewed") else "تجاوز مهلة النقل")
            continue
        if it["status"] == "approved" and age > 30 * 24:
            ledger.set_status(it, "expired", reason="بقي مجدولاً أكثر من ٣٠ يوماً")
            continue
        # منشورات الفيديو التي أُجّلت قبل تفعيل نقل الفيديو تعود للترشيح ما دامت في المهلة
        if it["status"] == "manual" and "غير مفعّل" in it.get("reason", "") and age <= config.MAX_AGE_HOURS + 48:
            ledger.set_status(it, "candidate", reason="")
            continue
        if it["status"] == "seen" and age >= config.MIN_AGE_HOURS:
            if age > config.MAX_AGE_HOURS:
                ledger.set_status(it, "expired")
            elif it["score"] >= thr:
                ledger.set_status(it, "candidate", percentile=_percentile(baseline, it["score"]))
            else:
                ledger.set_status(it, "low")
    # خط الأساس: آخر ٦٠ منشوراً فقط
    for old in sorted(baseline, key=lambda k: items.get(k, {}).get("created", ""))[:-60]:
        baseline.pop(old, None)
    ledger.save(state)

    # صياغة مرشح واحد في كل جلب (الأعلى أداءً)
    cands = sorted((i for i in items.values() if i["status"] == "candidate"), key=lambda i: -i["score"])
    if not cands:
        return
    it = cands[0]
    tw = tweets.get(it["id"])
    if tw is None:
        it.setdefault("errors", []).append("تعذّر جلب المنشور بمعرّفه")
        if len(it["errors"]) >= 3:
            ledger.set_status(it, "failed", reason="تعذّر جلبه ٣ مرات (ربما حُذف)")
    else:
        try:
            draft(it, tw)
        except Exception as e:  # فشل النموذج لا يوقف الدورة؛ يعاد في الجلب التالي
            it.setdefault("errors", []).append(str(e)[:300])
            if len(it["errors"]) >= 3:
                ledger.set_status(it, "failed", reason="فشل التحويل ٣ مرات")
        if it["status"] == "drafted":
            ledger.set_status(it, "approved" if config.MODE == "auto" else "pending")
    ledger.save(state)
    if it["status"] in ("blocked", "manual"):
        tg.notify(f"⚪ لم يُنقل تلقائياً ({it.get('reason')}):\n{it['x_url']}")


def _send(state: dict, it: dict, now, at=None) -> bool:
    """طلب واحد إلى فيسبوك: نشر فوري (at=None) أو تسجيل موعد. طابع الادّعاء يُحفظ قبل الطلب."""
    ledger.set_status(it, "publishing")
    if not at:  # عدّاد اليوم للنشر الفوري فقط؛ المجدول يُعدّ من حالته في السجل
        day = now.astimezone(schedule.MAKKAH).date().isoformat()
        state["published_days"][day] = state["published_days"].get(day, 0) + 1
    ledger.save(state)
    at_unix = int(at.timestamp()) if at else None
    try:
        if it.get("video_url"):
            fb_id = fb.publish_video(it["fb_text"], it["video_url"], at_unix)
        else:
            fb_id = fb.publish(it["fb_text"], it["photos"] if it.get("use_media") else None, at_unix)
    except Exception as e:
        # مهلة انتهت بعد POST ناجح ممكنة: الحالة «uncertain» لا تُعاد آلياً، وحارس الصفحة يحسمها لاحقاً
        timeout = "timed out" in str(e).lower() or isinstance(e, TimeoutError)
        ledger.set_status(it, "uncertain" if timeout else "failed", reason=str(e)[:500])
        ledger.save(state)
        tg.notify(f"🔴 {'نتيجة الطلب غير مؤكدة — تحقّق من الصفحة' if timeout else 'فشل النشر'}: "
                  f"{str(e)[:300]}\n{it['x_url']}")
        return False
    ledger.set_status(it, "scheduled" if at else "published", fb_id=fb_id)
    ledger.save(state)
    return True


def _adopt(it: dict, post: dict) -> None:
    """منشور وُجد على الصفحة ولم يسجّله سجلنا (ضاع حفظ دورة سابقة): نتبنّاه بمعرّفه ليبقى قابلاً للإلغاء."""
    ledger.set_status(it, "scheduled" if post.get("scheduled") else "published",
                      fb_id=post.get("id", ""), reason="وُجد على الصفحة مسبقاً")


def _publish_due(state: dict, now) -> None:
    """المواعيد البعيدة تُسجَّل عند فيسبوك فينشرها في دقيقتها؛ وما حان موعده يُنشر الآن (واحد في الدورة).
    كل ما يمر من الفلتر وتوافق عليه يُنشر — العدد اليومي يتبع إيقاعك على X، وMAX_PER_DAY صمام أمان فقط."""
    items = state["items"]
    parse = ledger.datetime.fromisoformat

    # قاطع: ميتا أوقفت حساب المطوّر/التطبيق ← لا طلبات إليها ٦ ساعات. الإلحاح على API محجوب
    # يُقرأ «نشاطاً غير طبيعي» إضافياً على حساب مقيّد (أُوقف مرة في 05/10)
    if state.get("fb_pause_until") and now < parse(state["fb_pause_until"]):
        return

    # ما سُجّل عند فيسبوك وفات موعده: نتحقق أنه نُشر فعلاً قبل أن نقول «نُشر»
    for it in items.values():
        if it["status"] != "scheduled" or not it.get("publish_at") or now < parse(it["publish_at"]) + timedelta(minutes=5):
            continue
        live = fb.is_live(it.get("fb_id", ""), bool(it.get("video_url"))) if it.get("fb_id") else None
        late = now >= parse(it["publish_at"]) + timedelta(hours=6)
        if live:
            ledger.set_status(it, "published")
            tg.notify(f"✅ نُشر في موعده: https://facebook.com/{it['fb_id']}")
        elif live is False or late:
            ledger.set_status(it, "uncertain", reason="فات موعده ولم يظهر منشوراً على الصفحة")
            tg.notify(f"🔴 منشور مجدول فات موعده ولم أجده منشوراً — تحقّق من الصفحة:\n{it['x_url']}")
        # live is None ولم يطل التأخر: يُعاد الفحص في الدورة التالية
    ledger.save(state)

    later, due = [], []
    for it in items.values():
        at = parse(it["publish_at"]) if it.get("publish_at") else None
        if it["status"] == "approved":
            if at and (at - now).total_seconds() >= fb.MIN_LEAD_SECONDS:
                later.append(it)
            elif not at or now >= at:
                due.append(it)
            # موعد أقرب من هامش الجدولة ولم يحن: ينتظر الدورة التي تليه
        elif (it["status"] == "pending" and config.MODE == "veto" and it.get("deadline")
              and now >= parse(it["deadline"])):
            due.append(it)
    if not later and not due:
        return
    if not config.FB_PAGE_TOKEN:
        print("FB_PAGE_TOKEN غير مضبوط — تخطّي النشر")
        return
    # الصفحة نفسها مصدر الحقيقة: لو ضاع حفظ السجل في دورة سابقة (فشل push مثلاً)
    # يبقى المنشور (أو موعده) على الصفحة شاهداً — لا نشر ولا جدولة مكررة
    try:
        posts = fb.recent_posts()
    except Exception as e:
        print(f"تعذّر قراءة الصفحة قبل النشر — لا نشر هذه الدورة: {str(e)[:200]}")
        if "Cannot call API" in str(e) or '"code":190' in str(e):
            state["fb_pause_until"] = (now + timedelta(hours=6)).isoformat()
            ledger.save(state)
            tg.notify("🔴 ميتا ترفض طلبات التطبيق الآن (غالباً «مطلوب تأكيد الحساب» في developers.facebook.com).\n"
                      "أوقفتُ كل طلب إلى فيسبوك ٦ ساعات. افتح developers.facebook.com وأكمل التأكيد إن طُلب، "
                      "ومواعيدك المختارة تبقى محفوظة.")
        return

    def cap_reached(when) -> bool:
        day = when.astimezone(schedule.MAKKAH).date().isoformat()
        live = sum(1 for p in posts if not p.get("scheduled")
                   and parse(p["created_time"].replace("+0000", "+00:00"))
                   .astimezone(schedule.MAKKAH).date().isoformat() == day)
        booked = sum(1 for i in items.values() if i["status"] == "scheduled" and i.get("publish_at")
                     and parse(i["publish_at"]).astimezone(schedule.MAKKAH).date().isoformat() == day)
        return max(live, state["published_days"].get(day, 0)) + booked >= config.MAX_PER_DAY

    def capped(it) -> None:
        if not it.get("cap_alerted"):
            tg.notify(f"🟠 بلغ ذلك اليوم سقف الأمان ({config.MAX_PER_DAY} منشورات) — هذا المنشور ينتظر، "
                      f"اختر له يوماً آخر:\n{it['fb_text'].strip().splitlines()[0][:50]}")
            it["cap_alerted"] = now.isoformat()

    for it in _queue(later):
        found = fb.find_posted(it["fb_text"], posts)
        if found:
            _adopt(it, found)
            continue
        at = parse(it["publish_at"])
        if cap_reached(at):
            capped(it)
            continue
        _send(state, it, now, at)
    ledger.save(state)

    if not due:
        return
    it = _queue(due)[0]
    found = fb.find_posted(it["fb_text"], posts)
    if found:
        _adopt(it, found)
        ledger.save(state)
        return
    if cap_reached(now):
        capped(it)
        ledger.save(state)
        return
    if _send(state, it, now):
        tg.notify(f"✅ نُشر على الصفحة: https://facebook.com/{it['fb_id']}")


def dry(n: int, min_age: float) -> None:
    tweets = xsrc.latest_originals(pages=2)
    now = ledger.now()
    old = [t for t in tweets if (now - xsrc.parse_time(t["createdAt"])).total_seconds() / 3600 >= min_age]
    old.sort(key=lambda t: -xsrc.score(t))
    config.OUT_DIR.mkdir(exist_ok=True)
    out = ["# معاينة التحويل (لم يُنشر شيء)\n"]
    for t in old[:n]:
        it = _item_from(t)
        try:
            draft(it, t)
        except Exception as e:
            ledger.set_status(it, "failed", reason=str(e)[:300])
        out += [f"\n---\n\n## {it['x_url']}\n", f"- الأداء: {it['score']} · الحالة: **{it['status']}** "
                f"{('· ' + it['reason']) if it.get('reason') else ''}", f"- ملاحظات النموذج: {it.get('notes', '')}\n",
                "### الأصل\n", "```", it["text"], "```\n", "### فيسبوك\n", it.get("fb_text", "—")]
        if it.get("use_media"):
            out += ["", *[f"![صورة]({u})" for u in it["photos"]]]
        print(f"{it['id']}: {it['status']} {it.get('reason', '')}")
    (config.OUT_DIR / "preview.md").write_text("\n".join(out), encoding="utf-8")
    print(f"→ {config.OUT_DIR / 'preview.md'}")


def check() -> int:
    ok = True
    def line(name, good, detail=""):
        nonlocal ok
        ok &= good
        print(f"{'✅' if good else '❌'} {name} {detail}")
    try:
        n = len(xsrc.latest_originals(pages=1))
        line("X (twitterapi.io)", n > 0, f"{n} منشوراً")
    except Exception as e:
        line("X (twitterapi.io)", False, str(e)[:200])
    if config.FB_PAGE_TOKEN:
        try:
            me = fb.whoami()
            line("فيسبوك", me.get("id") == config.FB_PAGE_ID, f"{me.get('name')} ({me.get('id')})")
        except Exception as e:
            line("فيسبوك", False, str(e)[:200])
    else:
        line("فيسبوك", False, "FB_PAGE_TOKEN غير مضبوط")
    if tg.enabled():
        try:
            tg.send("🔧 فحص اتصال نظام نقل X ← فيسبوك")
            line("تلغرام", True, "أُرسلت رسالة فحص")
        except Exception as e:
            line("تلغرام", False, str(e)[:200])
    else:
        line("تلغرام", config.MODE == "auto", "غير مضبوط")
    import shutil
    if config.TRANSFORMER == "claude":
        line("التحويل (claude CLI)", bool(shutil.which("claude")))
    elif config.TRANSFORMER == "anthropic":
        line("التحويل (Anthropic API)", bool(config.ANTHROPIC_API_KEY), config.ANTHROPIC_MODEL)
    else:
        line("التحويل (Gemini)", bool(config.GEMINI_API_KEY))
    print("الوضع:", config.MODE, "· المواعيد تُختار من تلغرام بتوقيت مكة · سقف الأمان اليومي:", config.MAX_PER_DAY)
    return 0 if ok else 1


def _git(*args: str) -> int:
    return subprocess.run(["git", *args], cwd=config.ROOT, capture_output=True, text=True).returncode


def _sync_ledger() -> None:
    """يودِع السجل ويدفعه إن تغيّر. فشل الدفع لا يوقف الحلقة: حارس الصفحة يمنع أي نشر مكرر."""
    rel = str(config.STATE_FILE.relative_to(config.ROOT)).replace("\\", "/")
    _git("add", rel)
    if _git("diff", "--cached", "--quiet") == 0:
        return
    _git("commit", "-q", "-m", f"state: {ledger.now().isoformat(timespec='seconds')}")
    for attempt in range(4):
        if _git("pull", "-q", "--rebase", "-X", "theirs") == 0 and _git("push", "-q") == 0:
            return
        time.sleep(5 * (attempt + 1))
    print("تعذّر دفع السجل — يُعاد في التغيير التالي")


def loop(minutes: float, sync: bool) -> None:
    """تشغيل مستمر: يستمع لتلغرام بانتظار طويل فتُطبَّق ضغطتك خلال ثوانٍ، والجلب من X مرة في الساعة."""
    end = time.time() + minutes * 60
    while time.time() < end:
        try:
            cycle(wait=min(50, max(1, int(end - time.time()))))
        except Exception as e:  # خطأ دورة واحدة لا يُسقط الحلقة
            print(f"خطأ في دورة: {str(e)[:300]}")
            time.sleep(15)
        if sync:
            _sync_ledger()


def cli() -> None:
    ap = argparse.ArgumentParser(prog="x2fb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("check")
    lp = sub.add_parser("loop")
    lp.add_argument("--minutes", type=float, default=340)
    lp.add_argument("--sync", action="store_true", help="احفظ السجل في المستودع (git) كلما تغيّر")
    d = sub.add_parser("dry")
    d.add_argument("--n", type=int, default=5)
    d.add_argument("--min-age", type=float, default=config.MIN_AGE_HOURS)
    a = ap.parse_args()
    if a.cmd == "run":
        cycle()
    elif a.cmd == "loop":
        loop(a.minutes, a.sync)
    elif a.cmd == "dry":
        dry(a.n, a.min_age)
    else:
        sys.exit(check())
