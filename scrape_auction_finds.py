"""
scrape_auction_finds.py
-----------------------
Scrapes EasyLive for pine/chair/bedside lots.
Splits results into Local and UK-Wide sections.
Generates index.html and pushes to GitHub Pages.

Cabbage runs this at ~5am daily.
"""

import os
import re
import json
import time
import shutil
import hashlib
import logging
import requests
import subprocess
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote_plus

# ── Configuration ─────────────────────────────────────────────────────────────

SEARCH_TERMS = ["pine", "chair", "bedside"]

LOCAL_HOUSES = [
    "churchill",
    "overture",
    "amersham",
    "bourne end",
    "jones & jacob",
    "jones and jacob",
    "tring market",
]

EASYLIVE_BASE = "https://www.easyliveauction.com"
SEARCH_URL    = f"{EASYLIVE_BASE}/catalogue/"

# Local repo path on Mac Mini — update if different
REPO_DIR = Path(os.path.expanduser("~/auction-finds"))
IMAGES_DIR = REPO_DIR / "images"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

REQUEST_DELAY = 1.5
MAX_PAGES     = 5
MAX_LOTS      = 200

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_local(house_name: str) -> bool:
    name = house_name.lower()
    return any(local in name for local in LOCAL_HOUSES)


def image_filename(url: str) -> str:
    ext = url.split("?")[0].rsplit(".", 1)[-1]
    ext = ext if ext in ("jpg", "jpeg", "png", "webp", "gif") else "jpg"
    return hashlib.md5(url.encode()).hexdigest()[:12] + "." + ext


def download_image(url: str, dest: Path) -> bool:
    if dest.exists():
        return True
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return True
    except Exception as e:
        log.warning(f"Image download failed: {url}  ({e})")
        return False


# ── Scraper ───────────────────────────────────────────────────────────────────

def scrape_term(session: requests.Session, term: str) -> list[dict]:
    lots = []
    seen_ids = set()

    for page in range(1, MAX_PAGES + 1):
        params = {"searchTerm": term, "searchOption": 3, "page": page}
        try:
            r = session.get(SEARCH_URL, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
        except Exception as e:
            log.warning(f"Request failed for '{term}' page {page}: {e}")
            break

        soup = BeautifulSoup(r.text, "html.parser")

        cards = (
            soup.select("div.lot-card")
            or soup.select("div.catalogue-item")
            or soup.select("article.lot")
            or soup.select("[class*='lot-item']")
            or soup.select("[class*='catalogue']")
        )

        if not cards:
            log.info(f"  No cards found on '{term}' page {page} — stopping")
            break

        for card in cards:
            try:
                lot = parse_card(card)
            except Exception as e:
                log.debug(f"Card parse error: {e}")
                continue

            if not lot or lot["id"] in seen_ids:
                continue
            seen_ids.add(lot["id"])
            lot["search_term"] = term
            lots.append(lot)

        log.info(f"  '{term}' page {page}: {len(cards)} cards, {len(lots)} total")
        time.sleep(REQUEST_DELAY)

        if len(cards) < 10:
            break

    return lots


def parse_card(card) -> dict | None:
    title_el = (
        card.select_one("h2")
        or card.select_one("h3")
        or card.select_one(".lot-title")
        or card.select_one("[class*='title']")
    )
    title = title_el.get_text(strip=True) if title_el else ""
    if not title:
        return None

    link_el = card.select_one("a[href]")
    href = link_el["href"] if link_el else ""
    url = urljoin(EASYLIVE_BASE, href) if href else ""

    lot_id = card.get("data-lot-id") or card.get("data-id") or ""
    if not lot_id and url:
        m = re.search(r"/(\d+)/?$", url)
        lot_id = m.group(1) if m else url[-20:]
    if not lot_id:
        lot_id = hashlib.md5(title.encode()).hexdigest()[:8]

    house_el = (
        card.select_one(".auctioneer-name")
        or card.select_one("[class*='auctioneer']")
        or card.select_one("[class*='house']")
        or card.select_one(".auction-name")
    )
    house = house_el.get_text(strip=True) if house_el else "Unknown"

    estimate_el = (
        card.select_one(".estimate")
        or card.select_one("[class*='estimate']")
        or card.select_one("[class*='price']")
        or card.select_one("[class*='bid']")
    )
    estimate = estimate_el.get_text(strip=True) if estimate_el else ""

    date_el = (
        card.select_one(".sale-date")
        or card.select_one("[class*='date']")
        or card.select_one("time")
    )
    sale_date = date_el.get_text(strip=True) if date_el else ""

    img_el = card.select_one("img")
    img_url = ""
    if img_el:
        img_url = (
            img_el.get("data-src")
            or img_el.get("data-lazy")
            or img_el.get("src")
            or ""
        )
        if img_url and img_url.startswith("//"):
            img_url = "https:" + img_url
        elif img_url and img_url.startswith("/"):
            img_url = EASYLIVE_BASE + img_url

    return {
        "id":        lot_id,
        "title":     title,
        "house":     house,
        "estimate":  estimate,
        "sale_date": sale_date,
        "url":       url,
        "img_url":   img_url,
        "img_file":  image_filename(img_url) if img_url else "",
        "local":     is_local(house),
    }


# ── HTML Generator ────────────────────────────────────────────────────────────

def build_html(local_lots: list[dict], wide_lots: list[dict]) -> str:
    now = datetime.now().strftime("%A %d %B %Y, %H:%M")

    def card_html(lot: dict) -> str:
        img_src = f"images/{lot['img_file']}" if lot["img_file"] else ""
        img_tag = (
            f'<img src="{img_src}" alt="{lot["title"]}" loading="lazy">'
            if img_src
            else '<div class="no-img">No image</div>'
        )
        estimate = f'<span class="estimate">{lot["estimate"]}</span>' if lot["estimate"] else ""
        date     = f'<span class="date">{lot["sale_date"]}</span>'    if lot["sale_date"] else ""

        return f"""
        <a class="card" href="{lot['url']}" target="_blank" rel="noopener">
          <div class="card-img">{img_tag}</div>
          <div class="card-body">
            <p class="title">{lot['title']}</p>
            <p class="house">{lot['house']}</p>
            <div class="meta">{estimate}{date}</div>
          </div>
        </a>"""

    def section_html(title: str, lots: list[dict], anchor: str) -> str:
        if not lots:
            return f'<section id="{anchor}"><h2>{title}</h2><p class="empty">No results found.</p></section>'
        cards = "\n".join(card_html(l) for l in lots)
        return f"""
      <section id="{anchor}">
        <h2>{title} <span class="count">({len(lots)} lots)</span></h2>
        <div class="grid">{cards}</div>
      </section>"""

    local_section = section_html("📍 Local", local_lots, "local")
    wide_section  = section_html("🇬🇧 UK-Wide", wide_lots, "uk-wide")
    terms_str = ", ".join(SEARCH_TERMS)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pinefinders — Auction Finds</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f1eb; color: #2c2c2c; padding: 0 0 60px; }}
    header {{ background: #2c2c2c; color: #f5f1eb; padding: 18px 24px; display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }}
    header h1 {{ font-size: 1.4rem; font-weight: 700; letter-spacing: -0.3px; }}
    header .meta {{ font-size: 0.8rem; opacity: 0.6; }}
    nav {{ background: #3a3a3a; padding: 10px 24px; display: flex; gap: 20px; }}
    nav a {{ color: #c8b89a; text-decoration: none; font-size: 0.9rem; font-weight: 500; }}
    nav a:hover {{ color: #fff; }}
    section {{ max-width: 1400px; margin: 32px auto 0; padding: 0 20px; }}
    h2 {{ font-size: 1.2rem; font-weight: 700; margin-bottom: 16px; padding-bottom: 10px; border-bottom: 2px solid #c8b89a; color: #2c2c2c; }}
    .count {{ font-weight: 400; font-size: 0.9rem; color: #888; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; }}
    .card {{ background: #fff; border-radius: 8px; overflow: hidden; text-decoration: none; color: inherit; box-shadow: 0 1px 4px rgba(0,0,0,0.08); transition: transform 0.15s, box-shadow 0.15s; display: flex; flex-direction: column; }}
    .card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.14); }}
    .card-img {{ width: 100%; aspect-ratio: 4/3; overflow: hidden; background: #e8e0d4; }}
    .card-img img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
    .no-img {{ width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; color: #aaa; }}
    .card-body {{ padding: 10px 12px 12px; flex: 1; display: flex; flex-direction: column; gap: 4px; }}
    .title {{ font-size: 0.82rem; font-weight: 600; line-height: 1.35; color: #1a1a1a; }}
    .house {{ font-size: 0.75rem; color: #888; }}
    .meta {{ margin-top: auto; padding-top: 6px; display: flex; flex-wrap: wrap; gap: 6px; font-size: 0.72rem; }}
    .estimate {{ background: #2c2c2c; color: #fff; padding: 2px 7px; border-radius: 3px; font-weight: 600; }}
    .date {{ color: #666; padding: 2px 0; }}
    .empty {{ color: #888; font-size: 0.9rem; padding: 20px 0; }}
    footer {{ text-align: center; margin-top: 48px; font-size: 0.75rem; color: #aaa; }}
  </style>
</head>
<body>
  <header>
    <h1>Pinefinders — Auction Finds</h1>
    <span class="meta">Updated: {now} &nbsp;·&nbsp; Terms: {terms_str}</span>
  </header>
  <nav>
    <a href="#local">📍 Local ({len(local_lots)})</a>
    <a href="#uk-wide">🇬🇧 UK-Wide ({len(wide_lots)})</a>
  </nav>
  {local_section}
  {wide_section}
  <footer>Pinefinders Old Pine Furniture Warehouse &nbsp;·&nbsp; pinefinders.github.io/auction-finds</footer>
</body>
</html>"""


# ── Git Push ──────────────────────────────────────────────────────────────────

def git_push(repo_dir: Path):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    cmds = [
        ["git", "-C", str(repo_dir), "add", "-A"],
        ["git", "-C", str(repo_dir), "commit", "-m", f"Auto update: {now_str}"],
        ["git", "-C", str(repo_dir), "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            if "nothing to commit" in result.stdout + result.stderr:
                log.info("Git: nothing new to commit")
                return
            log.warning(f"Git command failed: {' '.join(cmd)}\n{result.stderr}")
            return
    log.info("Git: pushed successfully")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=== Pinefinders Auction Finds — starting ===")
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    all_lots: dict[str, dict] = {}

    for term in SEARCH_TERMS:
        log.info(f"Searching: '{term}'")
        lots = scrape_term(session, term)
        for lot in lots:
            if lot["id"] not in all_lots:
                all_lots[lot["id"]] = lot
        if len(all_lots) >= MAX_LOTS:
            log.info(f"Reached cap of {MAX_LOTS} lots — stopping early")
            break

    log.info(f"Total unique lots: {len(all_lots)}")

    log.info("Downloading images…")
    for lot in all_lots.values():
        if lot["img_url"] and lot["img_file"]:
            dest = IMAGES_DIR / lot["img_file"]
            download_image(lot["img_url"], dest)
            time.sleep(0.3)

    local_lots = [l for l in all_lots.values() if l["local"]]
    wide_lots  = [l for l in all_lots.values() if not l["local"]]

    log.info(f"Local: {len(local_lots)}  UK-wide: {len(wide_lots)}")

    html = build_html(local_lots, wide_lots)
    index_path = REPO_DIR / "index.html"
    index_path.write_text(html, encoding="utf-8")
    log.info(f"Written: {index_path}")

    data_path = REPO_DIR / "data.json"
    data_path.write_text(
        json.dumps(list(all_lots.values()), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    log.info("Pushing to GitHub…")
    git_push(REPO_DIR)
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
