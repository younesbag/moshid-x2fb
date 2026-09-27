"""يطبع معرّف المحادثة بعد أن ترسل للبوت الجديد أي رسالة. يقرأ TG_BOT_TOKEN من .env أو البيئة."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from x2fb import config  # noqa: E402
from x2fb.http import request  # noqa: E402

if not config.TG_BOT_TOKEN:
    sys.exit("ضع TG_BOT_TOKEN في .env أولاً")
r = request("GET", f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/getUpdates")
chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "") for u in r.get("result", []) if "message" in u}
if not chats:
    sys.exit("لا رسائل بعد — أرسل للبوت أي رسالة ثم أعد التشغيل")
for cid, name in chats.items():
    print(f"TG_CHAT_ID={cid}   ({name})")
