"""المراجعة عبر تلغرام: معاينة بزرّين، وقراءة الضغطات بالسحب (getUpdates) — لا خادم ولا webhook."""
import json

from . import config
from .http import request


def enabled() -> bool:
    return bool(config.TG_BOT_TOKEN and config.TG_CHAT_ID)


def _api(method: str, **data):
    return request("POST", f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/{method}", data=data)


def send(text: str, buttons: list[tuple[str, str]] | None = None) -> None:
    if not enabled():
        return
    data = {"chat_id": config.TG_CHAT_ID, "text": text[:4000], "disable_web_page_preview": "true"}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": t, "callback_data": c} for t, c in buttons]]})
    _api("sendMessage", **data)


def preview(item: dict) -> None:
    head = (f"🟡 منشور مقترح لصفحة فيسبوك\n"
            f"من: {item['x_url']}\n"
            f"أداؤه على X: {item['score']:.0f} نقطة (الأعلى {item.get('percentile', 0):.0f}٪ من منشوراتك)\n"
            f"الصور: {len(item.get('photos', [])) if item.get('use_media') else 0}\n"
            f"────────\n")
    rule = ("\n────────\nلن يُنشر إلا بضغط «نشر»." if config.MODE == "approval"
            else f"\n────────\nسيُنشر تلقائياً بعد {config.VETO_HOURS:g} ساعات في نافذة النشر ما لم تضغط «إلغاء».")
    send(head + item["fb_text"] + rule, [("✅ نشر", f"ok:{item['id']}"), ("❌ إلغاء", f"no:{item['id']}")])


def poll(offset: int) -> tuple[list[tuple[str, str]], int]:
    """يعيد [(قرار، معرّف)] من ضغطات صاحب المحادثة فقط، والإزاحة الجديدة."""
    if not enabled():
        return [], offset
    r = request("GET", f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 0, "allowed_updates": '["callback_query"]'})
    decisions = []
    for u in r.get("result", []):
        offset = max(offset, u["update_id"] + 1)
        cq = u.get("callback_query")
        if not cq or str(cq.get("message", {}).get("chat", {}).get("id")) != str(config.TG_CHAT_ID):
            continue  # أي ضغطة من خارج محادثتك تُهمل
        kind, _, xid = (cq.get("data") or "").partition(":")
        if kind in ("ok", "no") and xid:
            decisions.append((kind, xid))
            try:
                _api("answerCallbackQuery", callback_query_id=cq["id"],
                     text="سُجّل: نشر" if kind == "ok" else "سُجّل: إلغاء")
            except Exception:
                pass
    return decisions, offset
