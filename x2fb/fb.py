"""النشر على صفحة فيسبوك عبر Graph API الرسمي.

كل طلب نشر بلا إعادة محاولة (retries=0): إعادة POST بعد انقطاع قد تنشر المنشور مرتين.
"""
from . import config
from .http import request


def _url(path: str) -> str:
    return f"https://graph.facebook.com/{config.GRAPH_VERSION}/{path}"


def publish(message: str, photo_urls: list[str] | None = None) -> str:
    tok = config.FB_PAGE_TOKEN
    if not photo_urls:
        r = request("POST", _url(f"{config.FB_PAGE_ID}/feed"), data={"message": message, "access_token": tok}, retries=0)
        return r["id"]
    if len(photo_urls) == 1:
        r = request("POST", _url(f"{config.FB_PAGE_ID}/photos"),
                    data={"url": photo_urls[0], "message": message, "access_token": tok}, retries=0)
        return r.get("post_id") or r["id"]
    # عدة صور: ترفع غير منشورة ثم تُربط بمنشور واحد
    ids = []
    for u in photo_urls[:10]:
        r = request("POST", _url(f"{config.FB_PAGE_ID}/photos"),
                    data={"url": u, "published": "false", "access_token": tok}, retries=0)
        ids.append(r["id"])
    data = {"message": message, "access_token": tok}
    for i, pid in enumerate(ids):
        data[f"attached_media[{i}]"] = '{"media_fbid":"%s"}' % pid
    r = request("POST", _url(f"{config.FB_PAGE_ID}/feed"), data=data, retries=0)
    return r["id"]


def recent_posts(limit: int = 15) -> list[dict]:
    """آخر منشورات الصفحة — مصدر الحقيقة عن «ما نُشر» لا السجل وحده."""
    r = request("GET", _url(f"{config.FB_PAGE_ID}/posts"),
                params={"fields": "message,created_time", "limit": limit, "access_token": config.FB_PAGE_TOKEN})
    return r.get("data", [])


def _norm(s: str) -> str:
    return " ".join((s or "").split())[:300]


def already_posted(message: str, posts: list[dict]) -> bool:
    key = _norm(message)
    return any(_norm(p.get("message", "")) == key for p in posts)


def whoami() -> dict:
    """فحص التوكن: يعيد اسم الصفحة ومعرّفها إن كان التوكن توكن صفحة صالحاً."""
    return request("GET", _url("me"), params={"fields": "id,name", "access_token": config.FB_PAGE_TOKEN})
