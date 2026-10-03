"""سجل الحالة: ملف JSON واحد يُحفظ في المستودع بعد كل دورة.

دورة حياة كل منشور:
seen → low (دون العتبة) | candidate → drafted → pending → approved | vetoed
       → publishing → published | failed          (أو skipped/blocked/manual في أي مرحلة)
«publishing» طابع ادّعاء يُكتب ويُحفظ قبل طلب النشر: إن انقطعت الدورة بعده لا يُعاد النشر آلياً.
"""
import json
from datetime import datetime, timezone

from . import config


def now() -> datetime:
    return datetime.now(timezone.utc)


def load() -> dict:
    if config.STATE_FILE.exists():
        return json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    return {"items": {}, "tg_offset": 0, "published_days": {}}


def save(state: dict) -> None:
    config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(config.STATE_FILE)


RUNTIME_FILE = config.STATE_FILE.with_name("runtime.json")  # غير مودَع: قيم تتغير كل دقيقة


def runtime() -> dict:
    try:
        return json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_runtime(data: dict) -> None:
    RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_FILE.write_text(json.dumps(data), encoding="utf-8")


def set_status(item: dict, status: str, **extra) -> None:
    item["status"] = status
    item.setdefault("history", []).append([now().isoformat(timespec="seconds"), status])
    item.update(extra)
