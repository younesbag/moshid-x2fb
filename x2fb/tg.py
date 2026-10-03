"""المراجعة والجدولة عبر تلغرام: معاينة بأزرار أوقات، وقراءة الضغطات والردود بالسحب (getUpdates)."""
import json

from . import config
from .http import request
from .schedule import SLOTS

CHUNK = 3800  # حد رسالة تلغرام ٤٠٩٦


def enabled() -> bool:
    return bool(config.TG_BOT_TOKEN and config.TG_CHAT_ID)


def _api(method: str, **data):
    return request("POST", f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/{method}", data=data, retries=1)


def send(text: str, keyboard: list[list[tuple[str, str]]] | None = None) -> list[int]:
    """يرسل النص كاملاً مقسماً على رسائل؛ لوحة الأزرار على آخر رسالة. يعيد معرّفات كل الرسائل
    (الرد على أي جزء من معاينة طويلة يجب أن يُنسب لمنشورها)."""
    if not enabled():
        return []
    chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]
    ids = []
    for n, chunk in enumerate(chunks):
        data = {"chat_id": config.TG_CHAT_ID, "text": chunk, "disable_web_page_preview": "true"}
        if keyboard and n == len(chunks) - 1:
            data["reply_markup"] = json.dumps(
                {"inline_keyboard": [[{"text": t, "callback_data": c} for t, c in row] for row in keyboard]})
        mid = (_api("sendMessage", **data).get("result") or {}).get("message_id")
        if mid:
            ids.append(mid)
    return ids


def notify(text: str) -> None:
    """إشعار لا يُسقط الدورة أبداً — فشل الإشعار لا يجوز أن يغيّر حالة منشور."""
    try:
        send(text)
    except Exception as e:
        print(f"تعذّر إشعار تلغرام: {str(e)[:200]}")


def keyboard(xid: str) -> list[list[tuple[str, str]]]:
    slots = [(s, f"at:{xid}:{s.replace(':', '')}") for s in SLOTS]
    return [[("✅ انشر الآن", f"now:{xid}")], slots[:4], slots[4:], [("❌ إلغاء", f"no:{xid}")]]


def preview(item: dict) -> list[int]:
    attach = ("🎬 فيديو المنشور الأصلي" if item.get("video_url")
              else (f"{len(item.get('photos', []))} صورة" if item.get("use_media") else "لا شيء"))
    head = (f"🟡 منشور مقترح لصفحة فيسبوك\n"
            f"من: {item['x_url']}\n"
            f"أداؤه على X: {item['score']:.0f} نقطة (ضمن الأعلى {item.get('percentile', 0):.0f}٪ من منشوراتك)\n"
            f"المرفق: {attach}\n"
            f"────────\n")
    rule = ("\n────────\nاختر وقت النشر بتوقيت مكة من الأزرار، أو رُدّ على هذه الرسالة بوقت تكتبه "
            "(مثل 19:45 أو «8:30 م» أو «غدا 08:00»). لن يُنشر بلا اختيارك، ويمكنك تغيير الوقت أو الإلغاء ما دام لم يُنشر."
            "\n✏️ للتعديل: رُدّ على هذه الرسالة بما تريد تغييره (مثل «اجعله أقصر» أو «احذف آخر فقرة»)، "
            "أو بـ«نص:» ثم النص الكامل كما تريده."
            if config.MODE == "approval"
            else f"\n────────\nسيُنشر تلقائياً بعد {config.VETO_HOURS:g} ساعات ما لم تضغط «إلغاء» أو تختر وقتاً.")
    return send(head + item["fb_text"] + rule, keyboard(item["id"]))


def poll(offset: int, wait: int = 0) -> tuple[list[dict], int]:
    """أحداث صاحب المحادثة وحده بترتيب وقوعها، والإزاحة الجديدة.

    {"kind": "now"|"at"|"no", "id": ..., "arg": "HHMM"}  ضغطة زر
    {"kind": "text", "text": ..., "reply_to": message_id|None}  رسالة مكتوبة
    """
    if not enabled():
        return [], offset
    r = request("GET", f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": wait, "allowed_updates": '["callback_query","message"]'},
                timeout=wait + 30)
    owner = str(config.TG_CHAT_ID)
    events = []
    for u in r.get("result", []):
        offset = max(offset, u["update_id"] + 1)
        cq, msg = u.get("callback_query"), u.get("message")
        if cq:
            chat = str(cq.get("message", {}).get("chat", {}).get("id"))
            # محادثة خاصة: معرّف المحادثة = معرّف المرسل. أي ضغطة من غيرك تُهمل
            if chat != owner or str(cq.get("from", {}).get("id")) != owner:
                continue
            kind, _, rest = (cq.get("data") or "").partition(":")
            xid, _, arg = rest.partition(":")
            if kind == "ok":  # أزرار المعاينات القديمة قبل الجدولة
                kind = "now"
            if kind in ("now", "at", "no") and xid:
                events.append({"kind": kind, "id": xid, "arg": arg})
                try:
                    _api("answerCallbackQuery", callback_query_id=cq["id"], text="وصل ✓ يصلك التأكيد مع الجدول في الدورة القادمة")
                except Exception:
                    pass
        elif msg:
            if str(msg.get("chat", {}).get("id")) != owner or str(msg.get("from", {}).get("id")) != owner:
                continue
            events.append({"kind": "text", "text": msg.get("text") or "", "date": msg.get("date"),
                           "reply_to": (msg.get("reply_to_message") or {}).get("message_id")})
    return events, offset
