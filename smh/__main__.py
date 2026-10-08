"""python -m smh serve | report | ask <item> | hello | health"""
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str]) -> int:
    from . import cards, serve
    cmd = argv[0] if argv else "serve"
    if cmd == "serve":
        serve.main()
    elif cmd == "report":
        serve.App().send_weekly()
    elif cmd == "promos":
        serve.App().send_promos()
    elif cmd == "flyers":
        app = serve.App()
        texts = app.flyer_texts()
        app.tg.post(texts) if app.tg and app.chat_id else print("\n\n".join(texts))
    elif cmd == "ask":
        app = serve.App()
        print("\n\n".join(app.cmd_ask(" ".join(argv[1:]))))
    elif cmd == "hello":
        app = serve.App()
        app.tg.post([cards.HELP], serve.BUTTONS)
        print("sent")
    elif cmd == "health":
        hb = Path(os.environ.get("DATA_DIR", "data")) / "heartbeat"
        return 0 if hb.exists() and time.time() - float(hb.read_text()) < 180 else 1
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
