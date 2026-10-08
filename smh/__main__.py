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
    elif cmd == "health":   # unhealthy = a real problem; NAS Doctor watches container health and alerts the owner
        problem = serve.health_problem(serve.Path(os.environ.get("DATA_DIR", "data")), int(os.environ.get("WEB_PORT", "8000")))
        if problem:
            print(problem)   # shows in `docker inspect` health log, which NAS Doctor reads as the reason
            return 1
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
