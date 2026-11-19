#!/usr/bin/env python3
"""Scrape recent videos from Malaysian food creator YouTube feeds (public RSS)
and merge them into data/recipes.json for the Ramadan Recipe Suggestion Board.

No API keys needed: uses YouTube's public RSS feed per channel
(https://www.youtube.com/feeds/videos.xml?channel_id=UC...) which returns
the ~15 most recent uploads.
"""
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_FILE = DATA_DIR / "recipes.json"

CHANNELS = [
    {"id": "UCDBiRJtU16xo86mgs1E8sUw", "creator": "Che Nom"},
    {"id": "UCdXB_anUqmG7cI2bVVOOWXw", "creator": "Che Sayang Kitchen"},
    {"id": "UCVW67nfON9IIsThefwTEOMg", "creator": "Sheila Rusly"},
    {"id": "UCpE_2Q0lCHe8w7eoAaSGMwg", "creator": "Khairul Aming"},
    {"id": "UC6nFzbNaFljMMUHjc1RWm5Q", "creator": "Ummi Atiyah Atif's Kitchen"},
    {"id": "UCR2yY55MP53bJQ5SmfZd-hg", "creator": "YatieKitchen"},
    {"id": "UCFFXPHAcSjIdyMT71ArXfsA", "creator": "Resepi Chef Izz"},
    {"id": "UCNN5m4-AkGx7OnusnLAUonw", "creator": "Attien Home Cooking"},
]

NS = {"a": "http://www.w3.org/2005/Atom", "m": "http://search.yahoo.com/mrss/"}

# keywords that make a video plausibly a recipe (drop vlogs, reviews, etc.)
RECIPE_HINTS = re.compile(
    r"resepi|recipe|cara (buat|masak|bikin)|masak|goreng|kuah|sambal|kuih|cake|"
    r"ayam|daging|ikan|udang|sayur|nasi|mee|mihun|kuey|laksa|sup|tomyam|kari|"
    r"rendang|bubur|roti|kek|biskut|pudding|air |jus|smoothie|sahur|berbuka|iftar|ramadan|ramadhan",
    re.IGNORECASE,
)

# crude time guess from title (e.g. "15 Minit", "1 Jam"); default otherwise
TIME_PATTERNS = [
    (re.compile(r"(\d+)\s*(minit|minute|min)", re.I), lambda m: int(m.group(1))),
    (re.compile(r"(\d+)\s*(jam|hour|hr)", re.I), lambda m: int(m.group(1)) * 60),
    (re.compile(r"(\d+)\s*(saat|detik|second|sec)", re.I), lambda m: max(1, int(m.group(1)) // 60)),
]

def guess_time(title):
    for pat, fn in TIME_PATTERNS:
        m = pat.search(title)
        if m:
            return min(max(fn(m), 5), 120)
    return 30

def guess_difficulty(title):
    t = title.lower()
    if any(w in t for w in ["2 minit", "5 minit", "10 minit", "mudah", "simple", "senang"]):
        return "Easy"
    if any(w in t for w in ["1 jam", "60 minit", "kenduri", "special"]):
        return "Medium"
    return "Easy" if guess_time(title) <= 30 else "Medium"

def guess_meal(title):
    t = title.lower()
    if any(w in t for w in ["sahur", "imsak", "subuh"]):
        return "Sahur"
    if any(w in t for w in ["berbuka", "buka puasa", "iftar", "moreh"]):
        return "Buka puasa"
    return "Buka puasa"

def parse_entry(entry, creator):
    vid = ""
    eid = entry.findtext("a:id", default="", namespaces=NS) or ""
    if "yt:video:" in eid:
        vid = eid.split("yt:video:")[1].strip()
    title = (entry.findtext("a:title", default="", namespaces=NS) or "").strip()
    published = entry.findtext("a:published", default="", namespaces=NS) or ""
    media = entry.find("m:group", namespaces=NS)
    desc = ""
    views = None
    if media is not None:
        d = media.find("m:description", namespaces=NS)
        desc = (d.text or "") if d is not None else ""
        v = media.find("m:community/m:statistics", namespaces=NS)
        if v is not None and v.get("views"):
            views = int(v.get("views"))
    thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else ""

    # combine title + description for keyword matching
    hay = f"{title} {desc[:400]}"
    if not RECIPE_HINTS.search(hay):
        return None

    return {
        "id": f"yt_{vid}" if vid else None,
        "name": title,
        "creator": creator,
        "platform": "YouTube",
        "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
        "img": thumb,
        "credit": f"Video thumbnail: {creator} on YouTube",
        "summary": (desc[:160].strip() or f"Latest recipe video from {creator}.") + ("…" if len(desc) > 160 else ""),
        "time": guess_time(title),
        "difficulty": guess_difficulty(title),
        "type": guess_meal(title),
        "views": views,
        "published": published[:10],
        "source": f"{creator}'s YouTube channel",
        "ingredientList": [],   # simplified lists are not available via RSS; app falls back
        "steps": [],
        "calories": None,
        "mood": ["hungry"] if guess_meal(title) == "Buka puasa" else ["tired"],
        "ingredients": [],
    }

def fetch_channel(channel):
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['id']}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (recipe-board scraper)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tree = ET.fromstring(r.read())
    except Exception as e:
        print(f"  ! {channel['creator']}: {e}")
        return []
    out = []
    for entry in tree.findall("a:entry", NS):
        rec = parse_entry(entry, channel["creator"])
        if rec and rec["id"]:
            out.append(rec)
    print(f"  ✓ {channel['creator']}: {len(out)} recipe-like videos")
    return out

def main():
    print("Scraping creator feeds…")
    scraped = []
    for ch in CHANNELS:
        scraped.extend(fetch_channel(ch))
        time.sleep(1)  # be polite

    if not scraped:
        print("No new data fetched; leaving recipes.json unchanged.")
        return

    # merge with existing curated recipes (curated first, then fresh ones)
    existing = []
    if DATA_FILE.exists():
        existing = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    curated = [r for r in existing if not str(r.get("id", "")).startswith("yt_")]

    # merge: keep everything we already have, add genuinely new entries
    seen = {r["id"] for r in existing}
    fresh = [r for r in scraped if r["id"] not in seen]
    # union of existing + fresh, newest scraped first, capped at 80
    merged = existing + fresh
    scraped_all = [r for r in merged if str(r.get("id", "")).startswith("yt_")]
    scraped_all.sort(key=lambda r: r.get("published") or "", reverse=True)
    scraped_all = scraped_all[:80]
    merged = [r for r in merged if not str(r.get("id", "")).startswith("yt_")] + scraped_all
    DATA_DIR.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {len(merged)} recipes ({len(curated)} curated + {len(fresh)} new) → {DATA_FILE}")

if __name__ == "__main__":
    main()
