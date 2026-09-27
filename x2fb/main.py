"""نقطة التشغيل.

  python -m x2fb check        فحص كل الاتصالات (لا ينشر شيئاً)
  python -m x2fb dry [--n 5]  معاينة تحويل أفضل منشوراتك الأخيرة في out/preview.md (لا ينشر ولا يغيّر السجل)
  python -m x2fb run          دورة كاملة (تُشغَّل كل ساعة من GitHub Actions)
"""
import argparse
import sys
from datetime import timedelta, timezone

from . import config, fb, gate, ledger, tg, xsrc
from .transform import transform

RIYADH = timezone(timedelta(hours=3))


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
    if xsrc.has_video(chain):
        ledger.set_status(item, "manual", reason="فيه فيديو — النقل الآلي للفيديو غير مفعّل")
        return
    item["photos"] = xsrc.photos(chain)
    out = transform(chain)
    if out.get("decision") != "publish":
        ledger.set_status(item, "skipped", reason=out.get("skip_reason", "قرار النموذج"))
        return
    text = out["text"].strip()
    use_media = bool(out.get("use_media")) and bool(item["photos"])
    problems = gate.check(text, use_media)
    if problems:
        ledger.set_status(item, "blocked", reason="البوابة: " + "، ".join(problems), fb_text=text)
        return
    ledger.set_status(item, "drafted", fb_text=text + "\n\n" + config.SIGNATURE,
                      use_media=use_media, notes=out.get("notes", ""),
                      thread_len=len(chain))


def _in_window(dt) -> bool:
    a, b = (int(x) for x in config.PUBLISH_HOURS.split("-"))
    return a <= dt.astimezone(RIYADH).hour < b


def cycle() -> None:
    state = ledger.load()
    items = state["items"]
    baseline = state.setdefault("baseline", {})
    now = ledger.now()

    # ١) قرارات تلغرام
    decisions, state["tg_offset"] = tg.poll(state.get("tg_offset", 0))
    for kind, xid in decisions:
        it = items.get(xid)
        if it and it["status"] in ("pending", "drafted"):
            ledger.set_status(it, "approved" if kind == "ok" else "vetoed")
    ledger.save(state)

    # ٢) جلب آخر المنشورات وتحديث أرقامها (أول تشغيل: صفحتان لبناء خط الأساس)
    tweets = {t["id"]: t for t in xsrc.latest_originals(pages=1 if baseline else 3)}
    for tid, t in tweets.items():
        fresh = _item_from(t)
        if tid in items:
            items[tid]["score"] = fresh["score"]
        else:
            items[tid] = fresh
            ledger.set_status(items[tid], "seen")

    # ٣) الحكم على ما بلغ عمر الحكم — خط الأساس يُحدَّث أولاً ثم تُحسب العتبة مرة واحدة
    def age_h(it):
        return (now - ledger.datetime.fromisoformat(it["created"])).total_seconds() / 3600
    for it in items.values():
        if age_h(it) >= config.MIN_AGE_HOURS:
            baseline[it["id"]] = it["score"]
    thr = _threshold(baseline)
    for it in items.values():
        age = age_h(it)
        # ما لم يُنشر خلال يومين بعد نافذة النقل صار قديماً — لا يُنشر متأخراً
        if it["status"] in ("candidate", "pending", "approved") and age > config.MAX_AGE_HOURS + 48:
            ledger.set_status(it, "expired", reason="تجاوز مهلة النقل")
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

    # ٤) صياغة مرشح واحد في كل دورة (الأعلى أداءً)
    cands = sorted((i for i in items.values() if i["status"] == "candidate"), key=lambda i: -i["score"])
    if cands:
        it = cands[0]
        tw = tweets.get(it["id"])
        if tw is None:
            ledger.set_status(it, "expired", reason="خرج من نافذة الجلب")
        else:
            try:
                draft(it, tw)
            except Exception as e:  # فشل النموذج لا يوقف الدورة؛ يعاد في الدورة التالية
                it.setdefault("errors", []).append(str(e)[:300])
                if len(it["errors"]) >= 3:
                    ledger.set_status(it, "failed", reason="فشل التحويل ٣ مرات")
            if it["status"] == "drafted":
                if config.MODE == "auto":
                    ledger.set_status(it, "approved")
                else:
                    ledger.set_status(it, "pending")
            elif it["status"] in ("blocked", "manual") and tg.enabled():
                tg.send(f"⚪ لم يُنقل تلقائياً ({it.get('reason')}):\n{it['x_url']}")
        ledger.save(state)

    # المعاينات: تُرسل لكل منشور معلّق لم يُعاين بعد (ولو صيغ قبل ربط تلغرام).
    # المهلة تبدأ من لحظة المعاينة لا من لحظة الصياغة — لا يُنشر شيء لم تره.
    if tg.enabled():
        for it in [i for i in items.values() if i["status"] == "pending" and not i.get("previewed")][:3]:
            tg.preview(it)
            it["previewed"] = now.isoformat()
            it["deadline"] = (now + timedelta(hours=config.VETO_HOURS)).isoformat()
        ledger.save(state)

    # ٥) النشر: منشور واحد يومياً داخل نافذة النشر
    day = now.astimezone(RIYADH).date().isoformat()
    if state["published_days"].get(day, 0) >= config.MAX_PER_DAY or not _in_window(now):
        return
    ready = []
    for it in items.values():
        if it["status"] == "approved":
            ready.append(it)
        elif (it["status"] == "pending" and config.MODE == "veto" and it.get("deadline")
              and now >= ledger.datetime.fromisoformat(it["deadline"])):
            ready.append(it)
    if not ready:
        return
    it = max(ready, key=lambda i: i["score"])
    if not config.FB_PAGE_TOKEN:
        print("FB_PAGE_TOKEN غير مضبوط — تخطّي النشر")
        return
    ledger.set_status(it, "publishing")
    ledger.save(state)  # الادّعاء محفوظ قبل الطلب
    try:
        fb_id = fb.publish(it["fb_text"], it["photos"] if it.get("use_media") else None)
        ledger.set_status(it, "published", fb_id=fb_id)
        state["published_days"][day] = state["published_days"].get(day, 0) + 1
        tg.send(f"✅ نُشر على الصفحة: https://facebook.com/{fb_id}")
    except Exception as e:
        ledger.set_status(it, "failed", reason=str(e)[:500])
        tg.send(f"🔴 فشل النشر: {str(e)[:300]}\n{it['x_url']}")
    ledger.save(state)


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
    print("الوضع:", config.MODE, "· نافذة النشر:", config.PUBLISH_HOURS, "بتوقيت الرياض")
    return 0 if ok else 1


def cli() -> None:
    ap = argparse.ArgumentParser(prog="x2fb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("check")
    d = sub.add_parser("dry")
    d.add_argument("--n", type=int, default=5)
    d.add_argument("--min-age", type=float, default=config.MIN_AGE_HOURS)
    a = ap.parse_args()
    if a.cmd == "run":
        cycle()
    elif a.cmd == "dry":
        dry(a.n, a.min_age)
    else:
        sys.exit(check())
