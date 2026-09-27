"""المصدر: منشورات يونس على X عبر twitterapi.io (نفس المزوّد المستعمل في x-style-lab)."""
from datetime import datetime, timezone

from . import config
from .http import request

BASE = "https://api.twitterapi.io/twitter"


def _get(path: str, params: dict) -> dict:
    return request("GET", f"{BASE}/{path}", params=params, headers={"X-API-Key": config.TWITTERAPI_IO_KEY})


def parse_time(s: str) -> datetime:
    # مثال: "Sat Sep 26 10:00:14 +0000 2026"
    return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc)


def _is_own(t: dict) -> bool:
    return (t.get("author") or {}).get("userName", "").lower() == config.X_USERNAME.lower()


def latest_originals(pages: int = 1) -> list[dict]:
    """آخر المنشورات الأصلية (بلا إعادة نشر ولا ردود). صفحة واحدة ≈ ٢٠ منشوراً."""
    out, cursor = [], ""
    for _ in range(pages):
        d = _get("user/last_tweets", {"userName": config.X_USERNAME, "includeReplies": "false", "cursor": cursor})
        data = d.get("data", d)
        tweets = data.get("tweets", []) if isinstance(data, dict) else []
        for t in tweets:
            if not _is_own(t) or t.get("retweeted_tweet") or t.get("isReply"):
                continue
            out.append(t)
        cursor = d.get("next_cursor") or ""
        if not d.get("has_next_page") or not cursor:
            break
    return out


def by_ids(ids: list[str]) -> dict[str, dict]:
    """جلب منشورات بعينها بمعرّفاتها — لتحديث أرقام ما خرج من نافذة آخر ٢٠ منشوراً."""
    out = {}
    for i in range(0, len(ids), 50):
        d = _get("tweets", {"tweet_ids": ",".join(ids[i:i + 50])})
        for t in d.get("tweets", []):
            out[t["id"]] = t
    return out


def thread_chain(tweet: dict) -> list[dict]:
    """الثريد: المنشور الأول ثم ردود يونس المتسلسلة على نفسه.

    thread_context يعيد أحياناً صفراً مع has_next_page (عيب معروف عند المزوّد)،
    فنبحث بمعرّف المحادثة وهو أثبت. ردوده على الآخرين (تبدأ بـ@) لا تدخل السلسلة.
    """
    found, cursor = [], ""
    for _ in range(3):
        # الخطأ يُرفع عمداً: رأس ثريد بلا بقيته يُنقل كأنه منشور كامل — أسوأ من إعادة المحاولة
        d = _get("tweet/advanced_search", {"query": f"conversation_id:{tweet['id']} from:{config.X_USERNAME}",
                                           "queryType": "Latest", "cursor": cursor})
        found += d.get("tweets", [])
        cursor = d.get("next_cursor") or ""
        if not d.get("has_next_page") or not cursor:
            break
    by_parent = {t.get("inReplyToId"): t for t in found
                 if _is_own(t) and not t.get("text", "").lstrip().startswith("@")}
    chain, cur = [tweet], tweet["id"]
    while cur in by_parent and len(chain) < 25:
        chain.append(by_parent[cur])
        cur = chain[-1]["id"]
    return chain


def expanded_urls(tweets: list[dict]) -> list[str]:
    urls = []
    for t in tweets:
        for u in (t.get("entities") or {}).get("urls") or []:
            if u.get("expanded_url") and u["expanded_url"] not in urls:
                urls.append(u["expanded_url"])
    return urls


def _media(tweets) -> list[dict]:
    tweets = tweets if isinstance(tweets, list) else [tweets]
    return [m for t in tweets for m in ((t.get("extendedEntities") or {}).get("media") or [])]


def photos(tweets) -> list[str]:
    return [m["media_url_https"] + "?name=large" for m in _media(tweets)
            if m.get("type") == "photo" and m.get("media_url_https")]


def has_video(tweets) -> bool:
    return any(m.get("type") in ("video", "animated_gif") for m in _media(tweets))


def score(t: dict) -> float:
    """تفاعل موزون: الحفظ وإعادة النشر أثقل من الإعجاب لأنهما يدلان على قيمة تُنقل."""
    return (t.get("likeCount", 0) + 2 * t.get("bookmarkCount", 0) + 2 * t.get("retweetCount", 0)
            + t.get("replyCount", 0) + 2 * t.get("quoteCount", 0))
