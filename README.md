# Supermarket Hunter

Family grocery price bot for Singapore. Every morning (08:00) it finds the **best value** pack of each staple the family
cooks with: price per 100g / 100ml / piece, so a 10kg bag and a 5kg bag compare fairly, with trusted brands first.
It posts the result to Telegram and shows it on a **web page on the home network**, with product photos and one tap
through to the shop's own product page.

![Today's Best Buys web page](docs/web-page.png)

## How it works

![Architecture](docs/architecture.png)

Editable source: [`docs/architecture.drawio`](docs/architecture.drawio) (open in [draw.io](https://app.diagrams.net); regenerate with
`python tools/gen_arch.py docs/architecture.drawio`).

One Docker container on a home NAS runs `python -m smh serve`, which starts four threads:

| Thread | When | Does |
|---|---|---|
| Scheduler | daily 08:00 SGT | full report: every staple, every store, plus the flyer promos → Telegram |
| Promo watcher | every 3 h | re-checks the staples and pushes promos not announced in the last 14 days |
| Telegram listener | always | `/ask`, `/report`, `/promos`, `/flyers`, `/staples` and the inline buttons |
| **Web server** | always | the family web page on port 8790 |

**The data path (steps 1–4 in the diagram)**
1. A timer or a command starts a run.
2. `smh/scrape.py` reads the shop's search page through [Jina Reader](https://r.jina.ai), which returns the page as Markdown.
   Each product is one Markdown link: name, price, old price, promo, product URL, and the photo on the line above.
   `smh/flyers.py` does the same for promotion pages; the Sheng Siong flyer is a JPG, so Claude reads the image.
3. Every row is saved to `data/prices.db` (SQLite) with its unit price and photo URL. The history is what makes
   "cheaper ▼ / dearer ▲ since last time" and "lowest in 8 weeks, stock up" possible.
4. `smh/cards.py` turns the results into Telegram HTML cards.

**The web page (steps 5–7)**
5. A browser on the home Wi-Fi opens `http://<nas-ip>:8790`. Docker maps NAS port 8790 to port 8000 in the container.
6. `smh/web.py` (Python's built-in `http.server`, no framework) reads the newest prices for each staple from
   `prices.db`, the staples and trusted brands from `config.json`, and the last flyer run from `flyers.json`, and
   draws the page **on every visit**. There is no separate "publish" step that could go stale: the page is always
   exactly as fresh as the database, and the header shows the date the prices were checked (in red if the morning
   run failed).
7. Every card is a link to the product on the shop's own website. Photos load straight from the shop's image server.

What the page shows per staple: the best-value pack as a big price tag (★ = a brand the family trusts), the best pack
at each other store, one current offer, the price change since the last check, and a 💰 stock-up flag. Large type
(Atkinson Hyperlegible, designed for low-vision readers), Chinese names for each staple, 48px+ tap targets, and it
works on a phone.

## Telegram commands (in the bot's own topic, or a private chat for allowed users)
| Command | What |
|---|---|
| `/ask eggs` (also `/askretailer`, `/price`) | 5 cheapest per unit across the stores, ★ trusted brands, 🟢 deals, store links |
| `/report` | run the report now |
| `/promos` | every current promo on the staples (new ones are pushed automatically every 3 h) |
| `/flyers` | FairPrice `/promotions`, Sheng Siong flyer (read by Claude), Giant campaigns, singpromos.com posts |
| `/staples` · `/staples add kailan` · `/staples del tomato` | the watched list |
| `/shophelp` | help |

Buttons on the last message: 🔄 Run again · 📋 Staples.

## Sources and rules
- **Cold Storage** online search (robots.txt allows it). **FairPrice** search is not scraped (robots.txt disallows
  `/search`); only its `/promotions` page is read, under a named personal-use exception, a few requests a day.
- A 403 / 429 / captcha is never retried or routed around: the site is marked blocked and skipped.
- Two seconds between searches. `STORES` in `smh/scrape.py` is the registry.

## Run
```bash
cp .env.example .env          # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID
docker compose up -d --build
# web page: http://<nas-ip>:8790   (WEB_PORT inside the container, default 8000; 0 turns it off)
docker compose exec supermarket-hunter python -m smh hello     # posts the help card: proves token + topic
docker compose exec supermarket-hunter python -m smh report    # report now
python -m pytest tests -q                                      # local checks
```
Staples and trusted brands live in `data/config.json` (created on first run from `smh/store.py` DEFAULT).
The Sheng Siong flyer needs `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`); without it that one source is skipped.
Everything else is Python stdlib, no API keys needed.
