import sys

# مخرجات عربية سليمة في طرفية ويندوز
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from .main import cli

cli()
