"""النشر والجدولة على صفحة فيسبوك عبر Graph API الرسمي.

كل طلب نشر بلا إعادة محاولة (retries=0): إعادة POST بعد انقطاع قد تنشر المنشور مرتين.
الجدولة: المنشور يُسجَّل عند فيسبوك بوقته (scheduled_publish_time) وفيسبوك ينشره في دقيقته —
فلا تعتمد دقة الموعد على موعد دورتنا (جدول GitHub يتأخر ساعات).
"""
from . import config
from .http import HttpError, request

# فيسبوك يقبل الجدولة من ١٠ دقائق فصاعداً؛ نترك هامشاً
MIN_LEAD_SECONDS = 15 * 60


def _url(path: str) -> str:
    return f"https://graph.facebook.com/{config.GRAPH_VERSION}/{path}"


def _when(at_unix: int | None) -> dict:
    return {"published": "false", "scheduled_publish_time": str(at_unix)} if at_unix else {}


def publish(message: str, photo_urls: list[str] | None = None, at_unix: int | None = None) -> str:
    """نص، أو نص مع صور. at_unix = جدولة عند فيسبوك بدل النشر الفوري."""
    tok = config.FB_PAGE_TOKEN
    if not photo_urls:
        r = request("POST", _url(f"{config.FB_PAGE_ID}/feed"),
                    data={"message": message, "access_token": tok, **_when(at_unix)}, retries=0)
        return r["id"]
    if len(photo_urls) == 1 and not at_unix:
        r = request("POST", _url(f"{config.FB_PAGE_ID}/photos"),
                    data={"url": photo_urls[0], "message": message, "access_token": tok}, retries=0)
        return r.get("post_id") or r["id"]
    # عدة صور (أو صورة مجدولة): تُرفع غير منشورة ثم تُربط بمنشور واحد
    ids = []
    for u in photo_urls[:10]:
        data = {"url": u, "published": "false", "access_token": tok}
        if at_unix:
            data["temporary"] = "true"  # صور منشور مجدول
        ids.append(request("POST", _url(f"{config.FB_PAGE_ID}/photos"), data=data, retries=0)["id"])
    data = {"message": message, "access_token": tok, **_when(at_unix)}
    for i, pid in enumerate(ids):
        data[f"attached_media[{i}]"] = '{"media_fbid":"%s"}' % pid
    return request("POST", _url(f"{config.FB_PAGE_ID}/feed"), data=data, retries=0)["id"]


def publish_video(description: str, file_url: str, at_unix: int | None = None) -> str:
    """فيديو برابط عام: ميتا تسحب الملف بنفسها (فيسبوك يعرضه ريلز)."""
    r = request("POST", f"https://graph-video.facebook.com/{config.GRAPH_VERSION}/{config.FB_PAGE_ID}/videos",
                data={"file_url": file_url, "description": description, "access_token": config.FB_PAGE_TOKEN,
                      **_when(at_unix)},
                retries=0, timeout=300)
    return r["id"]


def delete(object_id: str) -> None:
    """سحب منشور مجدول (للإلغاء أو تغيير الموعد). «غير موجود» = مسحوب أصلاً: نجاح لا خطأ،
    وإلا علق البند حين يُعاد الحدث بعد دورة حُذف فيها المنشور ولم يُحفظ سجلها."""
    try:
        request("DELETE", _url(object_id), params={"access_token": config.FB_PAGE_TOKEN}, retries=0)
    except HttpError as e:
        if e.status in (400, 404) and "does not exist" in e.body:
            return
        raise


def is_live(object_id: str, video: bool) -> bool | None:
    """هل نُشر فعلاً؟ None = تعذّر التحقق (لا نعلن نجاحاً ولا فشلاً)."""
    try:
        r = request("GET", _url(object_id), params={"fields": "published" if video else "is_published",
                                                     "access_token": config.FB_PAGE_TOKEN})
    except HttpError as e:
        return False if e.status in (400, 404) and "does not exist" in e.body else None
    except Exception:
        return None
    return bool(r.get("published" if video else "is_published"))


def recent_posts(limit: int = 15) -> list[dict]:
    """آخر المنشور والمجدول على الصفحة — مصدر الحقيقة عن «ما نُشر أو سيُنشر» لا السجل وحده."""
    params = {"fields": "id,message,created_time", "limit": limit, "access_token": config.FB_PAGE_TOKEN}
    posts = request("GET", _url(f"{config.FB_PAGE_ID}/posts"), params=params).get("data", [])
    try:
        sched = request("GET", _url(f"{config.FB_PAGE_ID}/scheduled_posts"), params=params).get("data", [])
    except Exception:
        sched = []
    for p in sched:
        p["scheduled"] = True
    return posts + sched


def _norm(s: str) -> str:
    return " ".join((s or "").split())[:300]


def find_posted(message: str, posts: list[dict]) -> dict | None:
    key = _norm(message)
    return next((p for p in posts if _norm(p.get("message", "")) == key), None)


def already_posted(message: str, posts: list[dict]) -> bool:
    return find_posted(message, posts) is not None


def whoami() -> dict:
    """فحص التوكن: يعيد اسم الصفحة ومعرّفها إن كان التوكن توكن صفحة صالحاً."""
    return request("GET", _url("me"), params={"fields": "id,name", "access_token": config.FB_PAGE_TOKEN})
