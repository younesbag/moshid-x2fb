"""الإعدادات: كل قيمة سرية من متغيرات البيئة، وكل قيمة سلوكية لها افتراض آمن."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """يقرأ .env محلياً فقط (في GitHub Actions تأتي القيم من Secrets)."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def env(name: str, default: str = "") -> str:
    # GitHub يمرر المتغير غير المضبوط كسلسلة فارغة — الفارغ يعني «استعمل الافتراضي»
    return os.environ.get(name, "").strip() or default


# المصدر
X_USERNAME = env("X_USERNAME", "younesbag1")
TWITTERAPI_IO_KEY = env("TWITTERAPI_IO_KEY")

# الوجهة
FB_PAGE_ID = env("FB_PAGE_ID", "61594405037641")
FB_PAGE_TOKEN = env("FB_PAGE_TOKEN")
GRAPH_VERSION = env("GRAPH_VERSION", "v26.0")

# المراجعة عبر تلغرام (بوت مخصص لهذا النظام، لا بوت younes-mind لأن له webhook)
TG_BOT_TOKEN = env("TG_BOT_TOKEN")
TG_CHAT_ID = env("TG_CHAT_ID")

# التحويل: anthropic (API، الافتراضي إن وُجد المفتاح) | claude (CLI باشتراكك) | gemini
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", "claude-opus-5-5")
TRANSFORMER = env("TRANSFORMER", "anthropic" if ANTHROPIC_API_KEY else "claude")
CLAUDE_MODEL = env("CLAUDE_MODEL", "opus")
GEMINI_API_KEY = env("GEMINI_API_KEY")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-3.1-pro-preview")

# السلوك
# approval: لا يُنشر شيء بلا ضغطة «نشر» | veto: يُنشر بعد المهلة إن لم تلغِه | auto: بلا مراجعة
MODE = env("MODE", "approval")
VETO_HOURS = float(env("VETO_HOURS", "3"))
MIN_AGE_HOURS = float(env("MIN_AGE_HOURS", "24"))   # نحكم على الأداء بعد يوم
MAX_AGE_HOURS = float(env("MAX_AGE_HOURS", "120"))  # لا ننقل ما فات عليه أكثر من ٥ أيام
TOP_SHARE = float(env("TOP_SHARE", "0.35"))         # أعلى ٣٥٪ من منشوراتك أداءً
MAX_PER_DAY = int(env("MAX_PER_DAY", "1"))
PUBLISH_HOURS = env("PUBLISH_HOURS", "17-22")      # بتوقيت الرياض
SIGNATURE = env("SIGNATURE", "— يونس")

STATE_FILE = ROOT / "state" / "ledger.json"
CONSTITUTION = ROOT / "prompts" / "constitution.md"
OUT_DIR = ROOT / "out"
