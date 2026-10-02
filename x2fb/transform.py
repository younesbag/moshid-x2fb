"""وكيل التحويل: يعيد صياغة منشور X لفيسبوك وفق الدستور ويعيد JSON."""
import json
import re
import shutil
import subprocess
import tempfile

from . import config


def build_input(chain: list[dict]) -> str:
    from .xsrc import expanded_urls, _media
    head = chain[0]
    n = len(chain)
    lines = ["المنشور الأصلي على X" + (f" (ثريد من {n} أجزاء)" if n > 1 else "") + ":", ""]
    for i, t in enumerate(chain, 1):
        if n > 1:
            lines.append(f"[الجزء {i}]")
        lines += [t.get("text", ""), ""]
    urls = expanded_urls(chain)
    if urls:
        lines.append("(الروابط المختصرة t.co في النص تشير إلى: " + " · ".join(urls) + ")")
    media = _media(chain)
    if media:
        kinds = ", ".join(sorted({m.get("type", "?") for m in media}))
        lines.append(f"(مرفقات: {len(media)} — {kinds}. لا ترى محتواها.)")
        if any(m.get("type") in ("video", "animated_gif") for m in media):
            lines.append("(الفيديو المرفق سيُنشر مع منشور فيسبوك نفسه — الإشارة إليه في النص تبقى صحيحة.)")
    q = head.get("quoted_tweet")
    if isinstance(q, dict) and q.get("text"):
        who = (q.get("author") or {}).get("userName", "?")
        lines += ["", f"<<سياق فقط — لا يُنقل منه أي سطر: منشور مقتبس لـ@{who}>>", q["text"], "<<نهاية السياق>>"]
    if head.get("article"):
        lines.append("(هذا مقال X طويل)")
    return "\n".join(lines)


def _extract_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"لا JSON في رد النموذج: {raw[:300]}")
    return json.loads(m.group(0))


def _claude(system: str, user: str) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI غير مثبت")
    with tempfile.TemporaryDirectory() as tmp:  # مجلد فارغ: لا CLAUDE.md مشروع يتسرب للسياق
        sp = f"{tmp}/system.md"
        with open(sp, "w", encoding="utf-8") as f:
            f.write(system)
        cmd = [exe, "-p", "--model", config.CLAUDE_MODEL, "--system-prompt-file", sp,
               "--output-format", "json", "--no-session-persistence", "--disallowedTools", "*"]
        r = subprocess.run(cmd, input=user, capture_output=True, text=True, encoding="utf-8",
                           cwd=tmp, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"claude فشل: {r.stderr[:500] or r.stdout[:500]}")
    env = json.loads(r.stdout)
    if env.get("is_error"):
        raise RuntimeError(f"claude أعاد خطأ: {env.get('result', '')[:500]}")
    return env.get("result", "")


def _gemini(system: str, user: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read().decode())
    return d["candidates"][0]["content"]["parts"][0]["text"]


def _anthropic(system: str, user: str) -> str:
    body = {"model": config.ANTHROPIC_MODEL, "max_tokens": 4000, "system": system,
            "messages": [{"role": "user", "content": user}]}
    import urllib.request
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "x-api-key": config.ANTHROPIC_API_KEY,
                                          "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read().decode())
    return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")


def transform(chain: list[dict]) -> dict:
    system = config.CONSTITUTION.read_text(encoding="utf-8")
    user = build_input(chain)
    provider = {"gemini": _gemini, "anthropic": _anthropic}.get(config.TRANSFORMER, _claude)
    raw = provider(system, user)
    out = _extract_json(raw)
    out.setdefault("decision", "skip")
    out.setdefault("text", "")
    out.setdefault("use_media", False)
    return out
