import os, re, json, time, hashlib, logging, requests, subprocess
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin

SEARCH_TERMS = ["pine"]

LOCAL_HOUSES = [
    "churchill", "overture", "amersham",
    "bourne end", "jones & jacob", "jones and jacob", "tring market",
]

EASYLIVE_BASE = "https://www.easyliveauction.com"
SEARCH_URL    = f"{EASYLIVE_BASE}/catalogue/"
REPO_DIR      = Path(os.path.expanduser("~/auction-finds"))
IMAGES_DIR    = REPO_DIR / "images"

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


def is_local(house_name):
    name = house_name.lower()
    return any(local in name for local in LOCAL_HOUSES)


def image_filename(url):
    ext = url.split("?")[0].rsplit(".", 1)[-1]
    ext = ext if ext in ("jpg", "jpeg", "png", "webp", "gif") else "jpg"
    return hashlib.md5(url.encode()).hexdigest()[:12] + "." + ext


def download_image(url, dest):
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


def parse_card(card):
    # Image
    img_el  = card.select_one("img.lot-image")
    img_url = img_el.get("src", "") if img_el else ""
    if img_url.startswith("//"):
        img_url = "https:" + img_url
    elif img_url.startswith("/"):
        img_url = EASYLIVE_BASE + img_url

    # Link + lot ID
    link_el = card.select_one("div.grid-catalogue-thumb-container a[href]")
    href    = link_el["href"] if link_el else ""
    url     = urljoin(EASYLIVE_BASE, href) if href else ""
    lot_id  = hashlib.md5(url.encode()).hexdigest()[:12] if url else hashlib.md5(img_url.encode()).hexdigest()[:12]

    # Title — the <p> inside a.no-hover
    title_el = card.select_one("a.no-hover p")
    title    = title_el.get_text(strip=True) if title_el else ""
    if not title:
        return None

    # Estimate — find <p> containing "Estimate"
    estimate = ""
    for p in card.select("a.no-hover p"):
        txt = p.get_text(" ", strip=True)
        if "Estimate" in txt:
            estimate = txt.replace("Estimate", "").strip()
            break

    # Current bid
    bid = ""
    for p in card.select("a.no-hover p"):
        txt = p.get_text(" ", strip=True)
        if "Current Bid" in txt:
            bid = txt.replace("Current Bid:", "").strip()
            break

    # Auction house — a.blue-text inside small
    house_el = card.select_one("small a.blue-text")
    house    = house_el.get_text(strip=True).replace("by ", "") if house_el else "Unknown"

    # Time left
    time_left = ""
    small = card.select_one("small")
    if small:
        for p in small.select("p"):
            txt = p.get_text(" ", strip=True)
            if "Time Left" in txt:
                time_left = txt.replace("Time Left:", "").strip()
                break

    return {
        "id":        lot_id,
        "title":     title,
        "house":     house,
        "estimate":  estimate,
        "bid":       bid,
        "time_left": time_left,
        "url":       url,
        "img_url":   img_url,
        "img_file":  image_filename(img_url) if img_url else "",
        "local":     is_local(house),
    }


def scrape_term(session, term):
    lots, seen_ids = [], set()
    for page in range(1, MAX_PAGES + 1):
        params = {"searchTerm": term, "searchOption": 3, "page": page}
        try:
            r = session.get(SEARCH_URL, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
        except Exception as e:
            log.warning(f"Request failed for '{term}' page {page}: {e}")
            break

        soup  = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("div.grid-lot")

        if not cards:
            log.info(f"  No cards on '{term}' page {page} — stopping")
            break

        new = 0
        for card in cards:
            try:
                lot = parse_card(card)
            except Exception as e:
                log.debug(f"Parse error: {e}")
                continue
            if not lot or lot["id"] in seen_ids:
                continue
            seen_ids.add(lot["id"])
            lot["search_term"] = term
            lots.append(lot)
            new += 1

        log.info(f"  '{term}' page {page}: {len(cards)} cards, {new} new, {len(lots)} total")
        time.sleep(REQUEST_DELAY)
        if len(cards) < 10:
            break

    return lots


def build_html(local_lots, wide_lots):
    now       = datetime.now().strftime("%A %d %B %Y, %H:%M")
    terms_str = ", ".join(SEARCH_TERMS)

    def card_html(lot):
        img_src = f"images/{lot['img_file']}" if lot["img_file"] else ""
        img_tag = (
            f'<img src="{img_src}" alt="{lot["title"]}" loading="lazy">'
            if img_src else '<div class="no-img">No image</div>'
        )
        bid      = f'<span class="bid">Bid: {lot["bid"]}</span>'         if lot["bid"]       else ""
        estimate = f'<span class="estimate">Est: {lot["estimate"]}</span>' if lot["estimate"] else ""
        timeleft = f'<span class="timeleft">{lot["time_left"]}</span>'   if lot["time_left"] else ""
        return f"""
        <a class="card" href="{lot['url']}" target="_blank" rel="noopener">
          <div class="card-img">{img_tag}</div>
          <div class="card-body">
            <p class="title">{lot['title']}</p>
            <p class="house">{lot['house']}</p>
            <div class="meta">{bid}{estimate}{timeleft}</div>
          </div>
        </a>"""

    def section_html(title, lots, anchor):
        if not lots:
            return f'<section id="{anchor}"><h2>{title}</h2><p class="empty">No results found.</p></section>'
        cards = "\n".join(card_html(l) for l in lots)
        return f"""
      <section id="{anchor}">
        <h2>{title} <span class="count">({len(lots)} lots)</span></h2>
        <div class="grid">{cards}</div>
      </section>"""

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
    header h1 {{ font-size: 1.4rem; font-weight: 700; }}
    header .meta {{ font-size: 0.8rem; opacity: 0.6; }}
    nav {{ background: #3a3a3a; padding: 10px 24px; display: flex; gap: 20px; }}
    nav a {{ color: #c8b89a; text-decoration: none; font-size: 0.9rem; font-weight: 500; }}
    nav a:hover {{ color: #fff; }}
    section {{ max-width: 1400px; margin: 32px auto 0; padding: 0 20px; }}
    h2 {{ font-size: 1.2rem; font-weight: 700; margin-bottom: 16px; padding-bottom: 10px; border-bottom: 2px solid #c8b89a; }}
    .count {{ font-weight: 400; font-size: 0.9rem; color: #888; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; }}
    .card {{ background: #fff; border-radius: 8px; overflow: hidden; text-decoration: none; color: inherit; box-shadow: 0 1px 4px rgba(0,0,0,0.08); transition: transform 0.15s, box-shadow 0.15s; display: flex; flex-direction: column; }}
    .card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.14); }}
    .card-img {{ width: 100%; aspect-ratio: 4/3; overflow: hidden; background: #e8e0d4; }}
    .card-img img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
    .no-img {{ width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; color: #aaa; }}
    .card-body {{ padding: 10px 12px 12px; flex: 1; display: flex; flex-direction: column; gap: 4px; }}
    .title {{ font-size: 0.82rem; font-weight: 600; line-height: 1.35; }}
    .house {{ font-size: 0.75rem; color: #888; }}
    .meta {{ margin-top: auto; padding-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; font-size: 0.72rem; }}
    .bid {{ background: #2c6e2c; color: #fff; padding: 2px 7px; border-radius: 3px; font-weight: 600; }}
    .estimate {{ background: #2c2c2c; color: #fff; padding: 2px 7px; border-radius: 3px; }}
    .timeleft {{ color: #888; padding: 2px 0; }}
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
  {section_html("📍 Local", local_lots, "local")}
  {section_html("🇬🇧 UK-Wide", wide_lots, "uk-wide")}
  <footer>Pinefinders Old Pine Furniture Warehouse &nbsp;·&nbsp; pinefinders.github.io/auction-finds</footer>
</body>
</html>"""


def git_push(repo_dir):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    for cmd in [
        ["git", "-C", str(repo_dir), "add", "-A"],
        ["git", "-C", str(repo_dir), "commit", "-m", f"Auto update: {now_str}"],
        ["git", "-C", str(repo_dir), "push"],
    ]:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            if "nothing to commit" in result.stdout + result.stderr:
                log.info("Git: nothing to commit")
                return
            log.warning(f"Git failed: {' '.join(cmd)}\n{result.stderr}")
            return
    log.info("Git: pushed successfully")


def main():
    log.info("=== Pinefinders Auction Finds — starting ===")
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    session  = requests.Session()
    all_lots = {}

    for term in SEARCH_TERMS:
        log.info(f"Searching: '{term}'")
        for lot in scrape_term(session, term):
            if lot["id"] not in all_lots:
                all_lots[lot["id"]] = lot
        if len(all_lots) >= MAX_LOTS:
            log.info(f"Cap reached ({MAX_LOTS}) — stopping")
            break

    log.info(f"Total unique lots: {len(all_lots)}")

    log.info("Downloading images…")
    for lot in all_lots.values():
        if lot["img_url"] and lot["img_file"]:
            download_image(lot["img_url"], IMAGES_DIR / lot["img_file"])
            time.sleep(0.3)

    local_lots = [l for l in all_lots.values() if l["local"]]
    wide_lots  = [l for l in all_lots.values() if not l["local"]]
    log.info(f"Local: {len(local_lots)}  UK-wide: {len(wide_lots)}")

    (REPO_DIR / "index.html").write_text(build_html(local_lots, wide_lots), encoding="utf-8")
    (REPO_DIR / "data.json").write_text(json.dumps(list(all_lots.values()), indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("HTML written")

    log.info("Pushing to GitHub…")
    git_push(REPO_DIR)
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
