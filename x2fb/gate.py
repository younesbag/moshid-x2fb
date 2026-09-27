"""بوابة حتمية بعد النموذج: ما يفلت من النموذج يُمسك هنا، ولا يُنشر نص يسقط فيها."""
import re

CHECKS = [
    ("رابط", re.compile(r"https?://|www\.|t\.co/", re.I)),
    ("إشارة @", re.compile(r"(?<![\w.])@\w+")),
    ("هاشتاق", re.compile(r"(?<!\w)#[\w؀-ۿ]+")),
    ("أثر X", re.compile(r"ثريد|🧵|كمل الثريد|تابعني|ريتويت|رتويت|\bRT\b|احفظ التغريدة|الصورة تستاهل تنسرق|^\s*\d+\s*/\s*\d*\s*$", re.M)),
    ("عرض/سعر مُشَيِّد", re.compile(r"اليوم الوطني|بدل\s*\d+|ريال\s*شهري|ر\.س|خصم|لمدة\s*\d+\s*(أيام|ايام|يوم)|كوبون")),
    ("سهم معلّق في النهاية", re.compile(r"👇\s*$")),
    ("تنسيق Markdown", re.compile(r"\*\*|__|```|^#{1,6}\s", re.M)),
    ("سطر معلّق بلا محتواه", re.compile(r":\s*\n\s*\n\s*\n")),
]


def check(text: str, has_media: bool = False) -> list[str]:
    problems = [name for name, rx in CHECKS if rx.search(text)]
    t = text.strip()
    # المنشور الذي قيمته في صورته يُسمح له بنص قصير
    if len(t) < (40 if has_media else 120):
        problems.append("قصير جداً")
    if len(t) > 8000:  # حد فيسبوك ٦٣ ألفاً؛ البرومبتات الطويلة قيمة لا حشو
        problems.append("طويل جداً")
    letters = [c for c in t if c.isalpha()]
    arabic = [c for c in letters if "؀" <= c <= "ۿ"]
    # البرومبتات الإنجليزية المنسوخة حرفياً ترفع نسبة اللاتيني — الحد منخفض عمداً
    if letters and len(arabic) / len(letters) < 0.25:
        problems.append("العربية أقل من ربع النص")
    return problems
