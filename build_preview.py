#!/usr/bin/env python3
"""
Preview generator for the new Pinefinders Auction Finds design.
Reads existing data.json and emits index-v2.html (does NOT push to git).

Once approved, the build_html() function gets folded back into scrape_auction_finds.py.
"""
import json, os
from pathlib import Path
from datetime import datetime

REPO_DIR      = Path(os.path.expanduser("~/auction-finds"))
SEEN_FILE     = REPO_DIR / "seen_lots.json"
DATA_FILE     = REPO_DIR / "data.json"
POSTCODES_FILE = REPO_DIR / "house_postcodes.json"
OUTPUT_FILE   = REPO_DIR / "index-v2.html"


def load_seen():
    """Return set of lot IDs we've seen in previous runs."""
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            return set()
    return set()


_COMPANY_SUFFIXES = [
    " ltd", " limited", " llp", " plc",
    " auctioneers and valuers", " auctioneers & valuers",
    " auctioneers", " auctions", " auction",
]


def _normalize(name):
    """Lowercase + strip common company suffixes for fuzzy matching."""
    n = (name or "").strip().lower()
    # strip trailing punctuation
    n = n.rstrip(".,;:·- ")
    changed = True
    while changed:
        changed = False
        for suf in _COMPANY_SUFFIXES:
            if n.endswith(suf):
                n = n[: -len(suf)].strip()
                changed = True
    return n


def load_postcodes():
    """Return (raw_lookup, normalized_lookup). The normalized lookup maps
    a stripped-name key to the same record, used as a fallback when the
    exact house name doesn't match (e.g. 'Churchill Auctioneers' vs
    'Churchill Auctions Ltd')."""
    if not POSTCODES_FILE.exists():
        return {}, {}
    try:
        data = json.loads(POSTCODES_FILE.read_text())
    except Exception:
        return {}, {}
    raw = {k: v for k, v in data.items() if not k.startswith("_")}
    norm = {}
    for name, info in raw.items():
        key = _normalize(name)
        if key and key not in norm:
            norm[key] = info
    return raw, norm


def house_meta(house, postcodes):
    raw, norm = postcodes
    info = raw.get(house) or norm.get(_normalize(house))
    if not info:
        return {"postcode": None, "location": None, "map_url": None, "known": False}
    pc = info.get("postcode", "")
    # Build a friendly location string from address if no explicit location
    loc = info.get("location") or ""
    if not loc and info.get("address"):
        # Use the address minus the trailing postcode for the tooltip subtitle
        addr = info["address"]
        if pc and pc in addr:
            addr = addr.replace(pc, "").strip().rstrip(",")
        loc = addr
    map_url = f"https://www.google.com/maps/search/?api=1&query={pc.replace(' ', '+')}" if pc else None
    return {"postcode": pc, "location": loc, "map_url": map_url, "known": True}


def card_html(lot, is_new, postcodes):
    img_src = f"images/{lot['img_file']}" if lot.get("img_file") else ""
    img_tag = (
        f'<img src="{img_src}" alt="{lot["title"]}" loading="lazy">'
        if img_src else '<div class="no-img">No image</div>'
    )
    bid      = f'<span class="bid">Bid {lot["bid"]}</span>'           if lot.get("bid")       else ""
    estimate = f'<span class="estimate">Est {lot["estimate"]}</span>' if lot.get("estimate") else ""
    timeleft = f'<span class="timeleft">⏱ {lot["time_left"]}</span>'  if lot.get("time_left") else ""
    new_badge = '<span class="new-badge">NEW</span>' if is_new else ""

    # House name with postcode tooltip + map link
    h = house_meta(lot.get("house", ""), postcodes)
    if h["known"] and h["map_url"]:
        tooltip = f'📍 {h["postcode"]}'
        if h["location"]:
            tooltip += f' · {h["location"]}'
        tooltip += ' · click for map'
        house_html_str = (
            f'<span class="house" data-tip="{tooltip}" '
            f'onclick="event.preventDefault(); event.stopPropagation(); '
            f"window.open('{h['map_url']}','_blank'); "
            f'">{lot["house"]} <span class="pc">{h["postcode"]}</span></span>'
        )
    elif h["known"]:
        house_html_str = f'<span class="house" data-tip="📍 {h["location"] or ""}"">{lot["house"]}</span>'
    else:
        house_html_str = f'<span class="house unknown" data-tip="📍 postcode unknown">{lot["house"]} <span class="pc-unknown">?</span></span>'

    return f"""
    <a class="card" href="{lot['url']}" target="_blank" rel="noopener">
      <div class="card-img">{img_tag}{new_badge}</div>
      <div class="card-body">
        <p class="title">{lot['title']}</p>
        <p class="house-line">{house_html_str}</p>
        <div class="meta">{bid}{estimate}{timeleft}</div>
      </div>
    </a>"""


def section_html(title, lots, anchor, seen, postcodes, css_class=""):
    if not lots:
        return f'<section id="{anchor}" class="{css_class}"><h2>{title}</h2><p class="empty">No results found.</p></section>'

    cards = "\n".join(card_html(l, l["id"] not in seen, postcodes) for l in lots)
    new_count = sum(1 for l in lots if l["id"] not in seen)
    new_pill = f' <span class="new-count">{new_count} new</span>' if new_count else ""

    return f"""
    <section id="{anchor}" class="{css_class}">
      <h2>{title} <span class="count">{len(lots)} lots</span>{new_pill}</h2>
      <div class="masonry">{cards}</div>
    </section>"""


def build_html(local_lots, wide_lots, seen, search_terms, postcodes):
    now       = datetime.now().strftime("%A %d %B %Y, %H:%M")
    terms_str = ", ".join(search_terms)
    total     = len(local_lots) + len(wide_lots)
    new_total = sum(1 for l in local_lots + wide_lots if l["id"] not in seen)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pinefinders — Auction Finds</title>
  <style>
    :root {{
      --bg: #faf7f2;
      --panel: #ffffff;
      --ink: #2a241d;
      --muted: #8a7e6f;
      --accent: #a8743a;
      --accent-soft: #f4ead8;
      --local-bg: #fdf6e8;
      --local-border: #d9a85a;
      --shadow: 0 1px 3px rgba(40,30,15,0.06), 0 4px 12px rgba(40,30,15,0.05);
      --shadow-hover: 0 4px 10px rgba(40,30,15,0.10), 0 10px 28px rgba(40,30,15,0.10);
      --radius: 10px;
      --new-bg: #2c6e2c;
    }}
    @media (prefers-color-scheme: dark) {{
      :root:not([data-theme="light"]) {{
        --bg: #14110d;
        --panel: #1f1b15;
        --ink: #ede4d2;
        --muted: #8a7e6f;
        --accent: #d9a85a;
        --accent-soft: #2a2218;
        --local-bg: #2a2218;
        --local-border: #d9a85a;
        --shadow: 0 1px 3px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.3);
        --shadow-hover: 0 4px 10px rgba(0,0,0,0.5), 0 10px 28px rgba(0,0,0,0.4);
      }}
    }}
    :root[data-theme="dark"] {{
      --bg: #14110d;
      --panel: #1f1b15;
      --ink: #ede4d2;
      --muted: #8a7e6f;
      --accent: #d9a85a;
      --accent-soft: #2a2218;
      --local-bg: #2a2218;
      --local-border: #d9a85a;
      --shadow: 0 1px 3px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.3);
      --shadow-hover: 0 4px 10px rgba(0,0,0,0.5), 0 10px 28px rgba(0,0,0,0.4);
    }}

    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{ background: var(--bg); color: var(--ink); }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      padding: 0 0 80px;
      -webkit-font-smoothing: antialiased;
    }}

    header {{
      background: var(--panel);
      border-bottom: 1px solid var(--accent-soft);
      padding: 20px 24px;
      position: sticky; top: 0; z-index: 10;
      backdrop-filter: blur(8px);
      display: flex; align-items: center; gap: 20px; flex-wrap: wrap;
    }}
    .brand {{
      display: flex; align-items: baseline; gap: 12px;
    }}
    .brand h1 {{
      font-size: 1.25rem; font-weight: 800; letter-spacing: -0.01em;
    }}
    .brand .logo {{ font-size: 1.5rem; }}
    .meta {{
      font-size: 0.78rem; color: var(--muted);
      margin-left: auto;
    }}
    .meta strong {{ color: var(--ink); }}
    .theme-toggle {{
      background: var(--accent-soft); color: var(--ink);
      border: none; cursor: pointer;
      padding: 7px 12px; border-radius: 6px;
      font-size: 0.85rem; font-family: inherit;
    }}
    .theme-toggle:hover {{ background: var(--accent); color: var(--panel); }}

    nav.jump {{
      max-width: 1500px; margin: 24px auto 0; padding: 0 24px;
      display: flex; gap: 10px; flex-wrap: wrap;
    }}
    nav.jump a {{
      background: var(--panel); color: var(--ink);
      border: 1px solid var(--accent-soft);
      padding: 8px 14px; border-radius: 999px;
      text-decoration: none; font-size: 0.85rem; font-weight: 500;
      transition: all 0.15s;
    }}
    nav.jump a:hover {{ border-color: var(--accent); color: var(--accent); }}
    nav.jump a.new-pill {{ background: var(--new-bg); color: #fff; border-color: var(--new-bg); }}

    section {{
      max-width: 1500px; margin: 36px auto 0; padding: 0 24px;
    }}
    section.local-section {{
      background: var(--local-bg);
      border-left: 4px solid var(--local-border);
      padding: 28px 24px 32px;
      border-radius: var(--radius);
      max-width: calc(1500px - 0px);
      margin: 36px 24px 0;
    }}
    @media (min-width: 1548px) {{
      section.local-section {{ margin: 36px auto 0; }}
    }}
    section h2 {{
      font-size: 1.15rem; font-weight: 800;
      margin-bottom: 18px; padding-bottom: 12px;
      border-bottom: 1px solid var(--accent-soft);
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    }}
    .count {{
      font-weight: 500; font-size: 0.78rem; color: var(--muted);
      background: var(--accent-soft); padding: 3px 9px; border-radius: 999px;
    }}
    .new-count {{
      font-weight: 600; font-size: 0.72rem; color: #fff;
      background: var(--new-bg); padding: 3px 9px; border-radius: 999px;
    }}

    /* CSS columns masonry — natural aspect ratios, no JS */
    .masonry {{
      column-count: 5;
      column-gap: 18px;
    }}
    @media (max-width: 1400px) {{ .masonry {{ column-count: 4; }} }}
    @media (max-width: 1100px) {{ .masonry {{ column-count: 3; }} }}
    @media (max-width: 760px)  {{ .masonry {{ column-count: 2; }} }}
    @media (max-width: 460px)  {{ .masonry {{ column-count: 1; }} }}

    /* Local section gets bigger cards = fewer columns */
    .local-section .masonry {{ column-count: 4; }}
    @media (max-width: 1400px) {{ .local-section .masonry {{ column-count: 3; }} }}
    @media (max-width: 900px)  {{ .local-section .masonry {{ column-count: 2; }} }}
    @media (max-width: 500px)  {{ .local-section .masonry {{ column-count: 1; }} }}

    .card {{
      display: inline-block; width: 100%;
      margin: 0 0 18px;
      background: var(--panel);
      border-radius: var(--radius);
      overflow: hidden;
      text-decoration: none; color: inherit;
      box-shadow: var(--shadow);
      transition: transform 0.18s ease, box-shadow 0.18s ease;
      break-inside: avoid;
    }}
    .card:hover {{
      transform: translateY(-3px);
      box-shadow: var(--shadow-hover);
    }}
    .card-img {{
      position: relative;
      width: 100%; line-height: 0;
      background: var(--accent-soft);
    }}
    .card-img img {{
      width: 100%; height: auto; display: block;
    }}
    .no-img {{
      aspect-ratio: 4/3; display: flex; align-items: center;
      justify-content: center; font-size: 0.8rem; color: var(--muted);
    }}
    .new-badge {{
      position: absolute; top: 10px; left: 10px;
      background: var(--new-bg); color: #fff;
      font-size: 0.65rem; font-weight: 800;
      padding: 4px 8px; border-radius: 4px;
      letter-spacing: 0.06em;
      box-shadow: 0 2px 6px rgba(0,0,0,0.2);
    }}
    .card-body {{
      padding: 12px 14px 14px;
      display: flex; flex-direction: column; gap: 5px;
    }}
    .title {{
      font-size: 0.88rem; font-weight: 600; line-height: 1.35;
      color: var(--ink);
      display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .house-line {{
      font-size: 0.74rem; color: var(--muted);
      line-height: 1.3;
    }}
    .house {{
      position: relative; cursor: pointer;
      border-bottom: 1px dotted var(--muted);
    }}
    .house:hover {{ color: var(--accent); border-bottom-color: var(--accent); }}
    .house[data-tip]:hover::after {{
      content: attr(data-tip);
      position: absolute; bottom: calc(100% + 6px); left: 0;
      background: var(--ink); color: var(--panel);
      padding: 6px 10px; border-radius: 6px;
      font-size: 0.72rem; white-space: nowrap;
      box-shadow: 0 2px 8px rgba(0,0,0,0.2);
      z-index: 20; pointer-events: none;
    }}
    .house.unknown {{ opacity: 0.7; }}
    .pc {{
      display: inline-block; margin-left: 4px;
      background: var(--accent-soft); color: var(--accent);
      padding: 1px 6px; border-radius: 4px;
      font-size: 0.65rem; font-weight: 600;
    }}
    .pc-unknown {{
      display: inline-block; margin-left: 4px;
      background: var(--accent-soft); color: var(--muted);
      padding: 1px 6px; border-radius: 4px;
      font-size: 0.65rem;
    }}
    .meta {{
      margin-top: 6px;
      display: flex; flex-wrap: wrap; gap: 5px;
      font-size: 0.7rem;
    }}
    .bid {{ background: var(--new-bg); color: #fff; padding: 2px 8px; border-radius: 4px; font-weight: 600; }}
    .estimate {{ background: var(--ink); color: var(--panel); padding: 2px 8px; border-radius: 4px; font-weight: 500; }}
    .timeleft {{ color: var(--muted); padding: 2px 0; }}

    .empty {{ color: var(--muted); font-size: 0.9rem; padding: 20px 0; }}

    footer {{
      text-align: center; margin-top: 60px; padding: 0 24px;
      font-size: 0.75rem; color: var(--muted);
    }}
    footer a {{ color: var(--accent); text-decoration: none; }}
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span class="logo">🪵</span>
      <h1>Pinefinders Auction Finds</h1>
    </div>
    <span class="meta">
      <strong>{total} lots</strong> · {new_total} new since yesterday · Updated {now}
    </span>
    <button class="theme-toggle" onclick="toggleTheme()" id="themeBtn">🌙 Dark</button>
  </header>

  <nav class="jump">
    <a href="#local">📍 Local · {len(local_lots)}</a>
    <a href="#uk-wide">🇬🇧 UK-Wide · {len(wide_lots)}</a>
    {f'<a class="new-pill" href="#" onclick="filterNew(); return false;">✨ {new_total} new</a>' if new_total else ''}
  </nav>

  {section_html("📍 Local auctions", local_lots, "local", seen, postcodes, "local-section")}
  {section_html("🇬🇧 UK-Wide", wide_lots, "uk-wide", seen, postcodes, "")}

  <footer>
    Pinefinders Old Pine Furniture Warehouse · search terms: {terms_str}<br>
    <a href="https://pinefinders.github.io/auction-finds">pinefinders.github.io/auction-finds</a>
  </footer>

  <script>
    function toggleTheme() {{
      const html = document.documentElement;
      const cur = html.getAttribute('data-theme') ||
        (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      const next = cur === 'dark' ? 'light' : 'dark';
      html.setAttribute('data-theme', next);
      localStorage.setItem('pf-theme', next);
      updateThemeBtn();
    }}
    function updateThemeBtn() {{
      const btn = document.getElementById('themeBtn');
      const isDark = (document.documentElement.getAttribute('data-theme') === 'dark') ||
        (!document.documentElement.getAttribute('data-theme') &&
         window.matchMedia('(prefers-color-scheme: dark)').matches);
      btn.textContent = isDark ? '☀️ Light' : '🌙 Dark';
    }}
    const saved = localStorage.getItem('pf-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
    updateThemeBtn();

    let newOnly = false;
    function filterNew() {{
      newOnly = !newOnly;
      document.querySelectorAll('.card').forEach(c => {{
        const isNew = c.querySelector('.new-badge');
        c.style.display = (newOnly && !isNew) ? 'none' : '';
      }});
    }}
  </script>
</body>
</html>"""


def main():
    if not DATA_FILE.exists():
        print(f"ERROR: {DATA_FILE} not found. Run the scraper first.")
        return

    lots = json.loads(DATA_FILE.read_text())
    seen = load_seen()
    postcodes = load_postcodes()

    # For preview purposes: pretend half the lots are "new" if seen file is empty,
    # so Ken can see what the NEW badges look like
    if not seen and lots:
        seen = {l["id"] for i, l in enumerate(lots) if i % 2 == 0}
        print(f"  (no seen_lots.json yet — simulating {len(lots) - len(seen)} 'new' lots for preview)")

    local_lots = [l for l in lots if l.get("local")]
    wide_lots  = [l for l in lots if not l.get("local")]

    # Use search_terms from data
    search_terms = sorted(set(l.get("search_term", "") for l in lots) - {""})

    OUTPUT_FILE.write_text(build_html(local_lots, wide_lots, seen, search_terms, postcodes), encoding="utf-8")
    print(f"✓ Wrote {OUTPUT_FILE}")
    print(f"  Open: file://{OUTPUT_FILE}")
    print(f"  Local: {len(local_lots)}  ·  UK-Wide: {len(wide_lots)}")

    # Report unknown houses so Ken can fill them in (after fuzzy fallback)
    raw, norm = postcodes
    unknown = {
        l["house"] for l in lots
        if l.get("house")
        and l["house"] not in raw
        and _normalize(l["house"]) not in norm
    }
    if unknown:
        print(f"  ⚠️  {len(unknown)} house(s) missing postcodes:")
        for h in sorted(unknown):
            print(f"     - {h}")


if __name__ == "__main__":
    main()
