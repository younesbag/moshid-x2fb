"""الجدولة: أوقات النشر يحددها المؤسس من تلغرام، وكلها بتوقيت مكة المكرمة (UTC+3)."""
import re
from datetime import datetime, timedelta, timezone

MAKKAH = timezone(timedelta(hours=3))
SLOTS = ["08:00", "10:00", "13:00", "16:00", "18:00", "20:00", "21:30", "23:00"]
DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_TIME = re.compile(r"(?<!\d)(\d{1,2})\s*[:.٫]\s*(\d{2})(?!\d)")
_TOMORROW = re.compile(r"غدا|غداً|غدًا|بكرة|بكره|باكر")


_PM = re.compile(r"(?<!\w)(م|مساء|مساءً|pm|PM)(?!\w)")
_AM = re.compile(r"(?<!\w)(ص|صباحا|صباحاً|am|AM)(?!\w)")


def resolve(hour: int, minute: int, ref: datetime, tomorrow: bool = False) -> datetime:
    """أقرب موعد لهذه الساعة بتوقيت مكة بعد اللحظة المرجعية ref (لحظة اختيارك لا لحظة دورتنا):
    اليوم إن لم يكن قد فات عند ref، وإلا غداً."""
    local = ref.astimezone(MAKKAH)
    at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if tomorrow or at <= local:
        at += timedelta(days=1)
    return at.astimezone(timezone.utc)


def parse(text: str, ref: datetime) -> datetime | None:
    """«19:45» أو «٧:٣٠ م» أو «غدا 08:00». يعيد None إن لم يكن وقتاً صالحاً."""
    t = (text or "").translate(_AR_DIGITS)
    m = _TIME.search(t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    rest = t[m.end():]
    if _PM.search(rest) and h < 12:
        h += 12
    elif _AM.search(rest) and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    return resolve(h, mi, ref, tomorrow=bool(_TOMORROW.search(t)))


def label(at: datetime, now: datetime) -> str:
    a, n = at.astimezone(MAKKAH), now.astimezone(MAKKAH)
    hm = f"{a.hour:02d}:{a.minute:02d}"
    days = (a.date() - n.date()).days
    if days == 0:
        return f"اليوم {hm}"
    if days == 1:
        return f"غداً {hm}"
    return f"{DAYS[a.weekday()]} {a.day}/{a.month} {hm}"
