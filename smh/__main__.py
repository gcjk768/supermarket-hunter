"""python -m smh serve | refresh | ask <item> | health"""
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str]) -> int:
    from . import cards, scrape, serve
    cmd = argv[0] if argv else "serve"
    if cmd == "serve":
        serve.main()
    elif cmd == "refresh":   # full refresh now (what the 08:00 job does)
        print(serve.App().refresh(full=True))
    elif cmd == "ask":   # quick look from the shell: cheapest per unit first
        for r in scrape.search(" ".join(argv[1:]))[:10]:
            print(f"{cards.money(r['price']):>8}  {cards.unit(r):>14}  {r['store']:<12} {r['name'][:60]}  {r['promo']}")
    elif cmd == "health":   # healthy = the family page answers (whether today's update ran is shown on the page itself)
        import urllib.request
        try:
            return 0 if urllib.request.urlopen(f"http://127.0.0.1:{os.environ.get('WEB_PORT', '8000')}/", timeout=15).status == 200 else 1
        except Exception:   # noqa: BLE001
            return 1
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
