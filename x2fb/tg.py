"""المراجعة عبر تلغرام: معاينة بزرّين، وقراءة الضغطات بالسحب (getUpdates) — لا خادم ولا webhook."""
import json

from . import config
from .http import request

CHUNK = 3800  # حد رسالة تلغرام ٤٠٩٦


def enabled() -> bool:
    return bool(config.TG_BOT_TOKEN and config.TG_CHAT_ID)


def _api(method: str, **data):
    return request("POST", f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/{method}", data=data, retries=1)


def send(text: str, buttons: list[tuple[str, str]] | None = None) -> None:
    """يرسل النص كاملاً مقسماً على رسائل؛ الأزرار على آخر رسالة فقط."""
    if not enabled():
        return
    chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]
    for n, chunk in enumerate(chunks):
        data = {"chat_id": config.TG_CHAT_ID, "text": chunk, "disable_web_page_preview": "true"}
        if buttons and n == len(chunks) - 1:
            data["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": t, "callback_data": c} for t, c in buttons]]})
        _api("sendMessage", **data)


def notify(text: str) -> None:
    """إشعار لا يُسقط الدورة أبداً — فشل الإشعار لا يجوز أن يغيّر حالة منشور."""
    try:
        send(text)
    except Exception as e:
        print(f"تعذّر إشعار تلغرام: {str(e)[:200]}")


def preview(item: dict) -> None:
    head = (f"🟡 منشور مقترح لصفحة فيسبوك\n"
            f"من: {item['x_url']}\n"
            f"أداؤه على X: {item['score']:.0f} نقطة (ضمن الأعلى {item.get('percentile', 0):.0f}٪ من منشوراتك)\n"
            f"الصور: {len(item.get('photos', [])) if item.get('use_media') else 0}\n"
            f"────────\n")
    rule = ("\n────────\nلن يُنشر إلا بضغط «نشر». ويمكنك الإلغاء حتى بعد الموافقة ما دام لم يُنشر." if config.MODE == "approval"
            else f"\n────────\nسيُنشر تلقائياً بعد {config.VETO_HOURS:g} ساعات في نافذة النشر ما لم تضغط «إلغاء».")
    send(head + item["fb_text"] + rule, [("✅ نشر", f"ok:{item['id']}"), ("❌ إلغاء", f"no:{item['id']}")])


def poll(offset: int) -> tuple[list[tuple[str, str]], int]:
    """يعيد [(قرار، معرّف)] بترتيب الضغط، من صاحب المحادثة وحده، والإزاحة الجديدة."""
    if not enabled():
        return [], offset
    r = request("GET", f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 0, "allowed_updates": '["callback_query"]'})
    decisions = []
    for u in r.get("result", []):
        offset = max(offset, u["update_id"] + 1)
        cq = u.get("callback_query")
        if not cq:
            continue
        chat = str(cq.get("message", {}).get("chat", {}).get("id"))
        sender = str(cq.get("from", {}).get("id"))
        # محادثة خاصة: معرّف المحادثة = معرّف المرسل. أي ضغطة من غيرك تُهمل
        if chat != str(config.TG_CHAT_ID) or sender != str(config.TG_CHAT_ID):
            continue
        kind, _, xid = (cq.get("data") or "").partition(":")
        if kind in ("ok", "no") and xid:
            decisions.append((kind, xid))
            try:
                _api("answerCallbackQuery", callback_query_id=cq["id"],
                     text="سُجّل: نشر" if kind == "ok" else "سُجّل: إلغاء")
            except Exception:
                pass
    return decisions, offset
