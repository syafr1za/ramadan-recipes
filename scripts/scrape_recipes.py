#!/usr/bin/env python3
"""Daily recipe pipeline for 'Nak Masak Apa Hari Ni?'

Scrapes recent videos from Malaysian food creator YouTube feeds (public RSS,
no API key), then builds a FULL cooking guide per video by parsing the video
description: ingredients with quantities, cooking steps, nutrition estimates,
and a category tag. The final pool is the 30 newest recipes, balanced across
up to 10 categories, so users always see a varied, informative board.

Output: data/recipes.json  (consumed by index.html)
"""
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "recipes.json"

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

RECIPE_HINTS = re.compile(
    r"resepi|recipe|cara (buat|masak|bikin)|masak|goreng|kuah|sambal|kuih|cake|kek|"
    r"ayam|daging|ikan|udang|sayur|nasi|mee|mihun|kuey|laksa|sup|tomyam|kari|"
    r"rendang|bubur|roti|biskut|pudding|jus|smoothie|sahur|berbuka|iftar",
    re.IGNORECASE,
)

# ---------------------------------------------------------------- categories
CATEGORY_RULES = [  # (category, regex) — first match wins
    ("Minuman",      r"\b(air |jus|smoothie|teh|kopi|milo|bandung|limau|sirap|minuman|latte|tea)\b"),
    ("Kuih & Dessert", r"\b(kuih|dessert|puding|pudding|biskut|cake|kek|tart|brownie|cookies|agar|jeli|ice cream|ais krim|cendol|bubur cha|pengat|lama)\b"),
    ("Snek",         r"\b(snek|snack|popia|keropok|cucur|cekodok|jemput|begedil|karipap|pau|donut|krok|banyak)\b"),
    ("Kanak-kanak",  r"\b(kanak|budak|kids|kid|baby|bendi|finger|nugget|cheese|simple untuk)\b"),
    ("Sihat",        r"\b(sihat|healthy|diet|rendah|kurma|salad|sayur|tempe|tauhu|oat|quinoa|keto|protein)\b"),
    ("Mee & Pasta",  r"\b(mee|mihun|kuey|kuetiau|laksa|spaghetti|pasta|macaroni|bihun|lami|yee)\b"),
    ("Nasi",         r"\b(nasi|nasilemak|pulut|biriyani|biryani|nasi goreng|kerabu)\b"),
    ("Sup",          r"\b(sup|soup|tomyam|tomyum|broth|wantan)\b"),
    ("Lauk Ayam",    r"\b(ayam|chicken)\b"),
    ("Lauk Lain",    r"\b(daging|beef|ikut|kambing|ikan|fish|udang|prawn|sotong|squid|telur|egg|sayur|kari|rendang|masak)\b"),
]
CATEGORIES = [c for c, _ in CATEGORY_RULES]

def categorize(title, desc):
    hay = f"{title} {desc[:300]}".lower()
    for cat, pat in CATEGORY_RULES:
        if re.search(pat, hay):
            return cat
    return "Lauk Lain"

# ------------------------------------------------------- ingredient parsing
QTY_WORD = r"(?:\d+(?:[.,]\d+)?(?:\s*(?:/|-)\s*\d+)?|se(?:cubit|suap|pinggang|keping|hidangan|helas|butir|bijir)|cubit|sesendok|setengah|secawan|segenggam)"
UNIT = r"(?:kg|g|gram|ml|l|litre|liter|cawan|cup|sb|sd|sdt|senduk|sw|tbsp|tsp|keping|bijir|butir|ulas|ikat|pkg|bungkus|ketul|pep守h|slice|clop|ulas|kotak|tin|pack|paket)?"

ING_LINE = re.compile(
    rf"^\s*[-•*●◦·✔✓o\d.)\]]*\s*(?P<name>[A-Za-zÀ-ÿ%()\/,&'’\- ]+?)\s*"
    rf"(?P<qty>(?:{QTY_WORD}\s*{UNIT}\b|{QTY_WORD}\b|{UNIT}\b)\s*.*?)$",
    re.IGNORECASE,
)

QTY_PAT = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*(?:/|-)\s*\d+)?)\s*(kg|g|gram|ml|l|litre|liter|cawan|cup|sb|sd|sdt|senduk|tbsp|tsp|keping|butir|ulas|ikat|bungkus|ketul|tin|kotak|pack|paket|bijir|helas|pinggan)?\b",
    re.IGNORECASE,
)

STOPWORDS = re.compile(
    r"^(bahan|bahan-bahan|cara|langkah|nota|note|tips|panduan|video|.subscribe|"
    r"follow|fb|ig|tiktok|youtube|channel|email|business|inquiry|collab|"
    r"#\w+|www\.|http|resipi|resepi)\b.*",
    re.IGNORECASE,
)

def clean_ing_name(name):
    name = re.sub(r"\s+", " ", name).strip(" :–-—,.")
    return name.strip()

def parse_ingredients(desc):
    """Extract ingredient lines with quantities from a video description.
    Handles: '500 g sotong', '2 Biji Bawang Merah', 'Gula 2 sudu besar',
    bullet lists, numbered lists, and 'secukupnya' quantity words."""
    if not desc:
        return []
    out, seen = [], set()
    lines = desc.split("\n")
    idx_start, idx_end = 0, len(lines)
    for i, ln in enumerate(lines):
        low = ln.lower()
        if re.search(r"^\s*(bahan|ingredient)", low):
            idx_start = i + 1
        elif idx_start > 0 and re.search(r"^\s*(cara|langkah|kaedah|how|nota|note)\b", low):
            idx_end = i
            break
    qty_pat = re.compile(
        r"(?:(?P<num>\d+(?:[.,]\d+)?(?:\s*(?:/|-)\s*\d+)?)\s*"
        r"(?P<unit>kg\b|g\b|gram\b|ml\b|l\b|litre|liter|cawan|cup|sb\b|sd\b|sdt|senduk|tbsp|tsp|"
        r"keping|butir|biji\b|ulas\b|ikat|bungkus|ketul|tin|kotak|pack|paket|batang|tangkai|helas|"
        r"pinggan|inci|sudu(?:\s*(?:besar|kecil))?)?"
        r"|cubit|secukup\s*\w*|sesendok|sebesar\s+\w+)",
        re.IGNORECASE,
    )
    marker = re.compile(r"^\s*[-•*●◦·✔✓○o]+|^\s*\d+[.)\]]|\u200b")
    for raw in lines[idx_start:idx_end]:
        ln = marker.sub("", raw).strip()
        ln = ln.strip(" :–-—,.•*[]()") 
        if not ln or len(ln) < 3 or len(ln) > 120:
            continue
        if STOPWORDS.match(ln):
            continue
        if re.match(r"^\d+[:.]\d{2}", ln):
            continue
        m = qty_pat.search(ln)
        if not m:
            continue
        mstart, mend = m.span()
        head = ln[:mstart].strip(" :–-—,.")
        tail = ln[mend:].strip(" :–-—,.")
        matched = m.group(0).strip()
        if head and len(head) <= 60:
            # 'gula putih 3 sb' → name before qty. Strip trailing '(' fragments.
            name = re.sub(r"\s*\([^)]*$", "", head).strip()
            qty = (matched + " " + tail).strip()
        else:
            # '650g pisang' → name after qty; drop a leading connector word
            nm = re.sub(r"^(atau|ataupun|dan|dengan|of|or)\s+", "", tail, flags=re.I) if tail else matched
            nm = re.sub(r"\s*\([^)]*$", "", nm).strip()
            name, qty = nm, matched
        name = re.sub(r"\s+", " ", name).strip(" :–-—,.") 
        qty = re.sub(r"\s+", " ", qty).strip()
        if not name or len(name) < 2:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(f"{name} — {qty[:70]}" if qty else name)
        if len(out) >= 20:
            break
    return out

def parse_steps(desc):
    """Extract cooking steps: numbered lines, or 'langkah/cara' section lines."""
    if not desc:
        return []
    out = []
    lines = [l.strip() for l in desc.split("\n") if l.strip()]
    in_cara = False
    for ln in lines:
        if re.match(r"^\s*(cara|langkah|kaedah|how to|steps?)\b", ln, re.IGNORECASE):
            in_cara = True
            continue
        if re.match(r"^\s*(bahan|ingredients|nota|note)\b", ln, re.IGNORECASE):
            in_cara = False
            continue
        m = re.match(r"^\s*(\d+)[.)\]]\s*(.{15,})$", ln)
        if m:
            text = re.sub(r"\s+", " ", m.group(2)).strip()
            if not STOPWORDS.match(text):
                out.append(text[:220])
        elif in_cara and 20 < len(ln) < 220 and not STOPWORDS.match(ln):
            out.append(re.sub(r"\s+", " ", ln)[:220])
        if len(out) >= 12:
            break
    return out

# ------------------------------------------------------------ time/difficulty
def guess_time(title, desc):
    m = re.search(r"(\d+)\s*(minit|minute|min)", title, re.I) or re.search(r"(\d+)\s*(minit|minute|min)", desc[:200], re.I)
    if m: return min(max(int(m.group(1)), 5), 120)
    m = re.search(r"(\d+)\s*(jam|hour|hr)", title, re.I)
    if m: return min(int(m.group(1)) * 60, 120)
    return 30

def guess_difficulty(t, time_min):
    if any(w in t.lower() for w in ["mudah", "simple", "senang", "2 minit", "5 minit", "10 minit"]) or time_min <= 20:
        return "Mudah"
    if time_min >= 60:
        return "Sederhana"
    return "Mudah" if time_min <= 30 else "Sederhana"

# ------------------------------------------------------------ nutrition (est.)
# kcal per 100g for common ingredients found in Malaysian recipes
CAL_MAP = [
    (r"ayam|chicken", 165), (r"daging|beef|rendang", 250), (r"ikan|fish", 130),
    (r"udang|prawn", 99), (r"sotong|squid", 92), (r"telur|egg", 155),
    (r"nasi|rice", 130), (r"mee|mihun|bihun|kuey|noodle|pasta|spaghetti|macaroni", 150),
    (r"santan|coconut milk", 230), (r"minyak|oil|mentega|butter", 884),
    (r"gula|sugar|manis", 387), (r"susu|milk|cream", 60), (r"keju|cheese", 402),
    (r"kentang|potato", 77), (r"tempe", 193), (r"tauhu|tahu|tofu", 76),
    (r"tepung|flour", 364), (r"kurma|dates", 282), (r"pisang|banana", 89),
    (r"cili|chilli", 40), (r"sayur|kangkung|bayam|salad|vege", 30),
    (r"kuih|cake|kek|biskut|dessert", 350),
]

def estimate_nutrition(ingredients, category):
    """Rough per-serving estimate derived from ingredient keywords.
    Clearly labelled as an estimate — not a nutrition label."""
    base = {"Minuman": 180, "Kuih & Dessert": 320, "Snek": 280, "Kanak-kanak": 300,
            "Sihat": 250, "Mee & Pasta": 450, "Nasi": 500, "Sup": 220,
            "Lauk Ayam": 420, "Lauk Lain": 380}
    kcal = base.get(category, 350)
    text = " ".join(ingredients).lower()
    bonus = 0
    for pat, c in CAL_MAP:
        if re.search(pat, text):
            bonus += c * 0.12  # heuristics: each recognised ingredient nudges the estimate
    kcal = int(min(kcal + bonus, 900))
    # very rough macro split by category
    if category == "Minuman":
        return {"kcal": kcal, "karbo": f"{int(kcal*0.18)}g", "protein": f"{int(kcal*0.03)}g", "lemak": f"{int(kcal*0.05)}g"}
    if category in ("Kuih & Dessert", "Snek"):
        return {"kcal": kcal, "karbo": f"{int(kcal*0.10)}g", "protein": f"{int(kcal*0.02)}g", "lemak": f"{int(kcal*0.04)}g"}
    return {"kcal": kcal, "karbo": f"{int(kcal*0.08)}g", "protein": f"{int(kcal*0.06)}g", "lemak": f"{int(kcal*0.05)}g"}

# ---------------------------------------------------------------- RSS parsing
def fetch_channel(channel):
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['id']}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (recipe-board pipeline)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tree = ET.fromstring(r.read())
    except Exception as e:
        print(f"  ! {channel['creator']}: {e}")
        return []
    out = []
    for entry in tree.findall("a:entry", NS):
        eid = entry.findtext("a:id", default="", namespaces=NS) or ""
        vid = eid.split("yt:video:")[1].strip() if "yt:video:" in eid else ""
        title = (entry.findtext("a:title", default="", namespaces=NS) or "").strip()
        published = (entry.findtext("a:published", default="", namespaces=NS) or "")[:10]
        media = entry.find("m:group", namespaces=NS)
        desc = ""
        views = None
        if media is not None:
            d = media.find("m:description", namespaces=NS)
            desc = (d.text or "") if d is not None else ""
            v = media.find("m:community/m:statistics", namespaces=NS)
            if v is not None and v.get("views"):
                views = int(v.get("views"))
        if not RECIPE_HINTS.search(f"{title} {desc[:300]}"):
            continue
        out.append({"vid": vid, "title": title, "published": published,
                    "desc": desc, "views": views, "creator": channel["creator"]})
    print(f"  ✓ {channel['creator']}: {len(out)} kandidat resepi")
    return out



def fetch_channel_videos_page(channel_id, creator):
    """Fetch ~25 recent uploads from the channel's /videos page (new lockup layout).
    Yields more history than the 15-entry RSS cap."""
    out = []
    try:
        url = f"https://www.youtube.com/channel/{channel_id}/videos"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        for m in re.finditer(r'"lockupViewModel":', html):
            seg = html[m.start():m.start() + 6000]
            im = re.search(r"i\.ytimg\.com/vi/(\w{11})/", seg)
            tm = re.search(r'"content":"((?:[^"\\]|\\.)*?)"', seg)
            if im:
                title = json.loads('"' + tm.group(1) + '"') if tm else ""
                out.append({"vid": im.group(1), "title": title, "published": "",
                            "desc": "", "views": None, "creator": creator})
        # de-dup preserving order
        seen, dedup = set(), []
        for v in out:
            if v["vid"] not in seen:
                seen.add(v["vid"]); dedup.append(v)
        return dedup
    except Exception as ex:
        print(f"  ! videos-page {creator}: {ex}")
        return []

def fetch_full_description(vid):
    """Fetch the complete video description from the watch page.
    RSS descriptions are often truncated teasers; the full description usually
    contains the written recipe (bahan + cara). Cached politely with a pause."""
    if not vid:
        return ""
    try:
        url = f"https://www.youtube.com/watch?v={vid}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Language": "ms,en",
        })
        raw_html = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
        m = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', raw_html)
        if not m:
            return ""
        return json.loads('"' + m.group(1) + '"')
    except Exception:
        return ""

def build_recipe(e):
    # shorts often link the full video — follow the link for the real recipe text
    m = re.search(r"(?:youtu\.be/|youtube\.com/watch\?v=)([\w-]{11})", e["desc"])
    if m and m.group(1) != e["vid"]:
        full = fetch_full_description(m.group(1))
        if len(full) > len(e["desc"]):
            e = dict(e, vid=m.group(1), desc=full)
    cat = categorize(e["title"], e["desc"])
    ings = parse_ingredients(e["desc"])
    steps = parse_steps(e["desc"])
    t = guess_time(e["title"], e["desc"])
    nut = estimate_nutrition(ings, cat)
    return {
        "id": f"yt_{e['vid']}",
        "name": e["title"],
        "creator": e["creator"],
        "platform": "YouTube",
        "url": f"https://www.youtube.com/watch?v={e['vid']}" if e["vid"] else "",
        "img": f"https://i.ytimg.com/vi/{e['vid']}/hqdefault.jpg" if e["vid"] else "",
        "credit": f"Thumbnail video: {e['creator']} di YouTube",
        "category": cat,
        "summary": (e["desc"][:170].strip() or f"Resepi terkini daripada {e['creator']}.") + ("…" if len(e["desc"]) > 170 else ""),
        "time": t,
        "difficulty": guess_difficulty(e["title"], t),
        "type": cat,
        "views": e["views"],
        "published": e["published"],
        "source": f"Saluran YouTube {e['creator']}",
        "ingredientList": ings,
        "steps": steps,
        "nutrition": nut,
        "calories": f"≈ {nut['kcal']} kcal sehidangan",
        "mood": ["hungry"] if cat not in ("Minuman", "Kuih & Dessert") else ["light"],
        "ingredients": ["chicken"] if re.search(r"ayam", e["title"], re.I) else [],
        "emoji": "🎥",
    }

def pick_balanced_30(all_recipes):
    """Newest recipes first, but ensure coverage across categories (max 10)."""
    all_recipes.sort(key=lambda r: (r["published"] or "", r["views"] or 0), reverse=True)
    by_cat = {}
    for r in all_recipes:
        by_cat.setdefault(r["category"], []).append(r)
    chosen, chosen_ids = [], set()
    # round-robin: one per category per round, newest-first within category
    cats = list(by_cat.keys())[:10]
    for round_i in range(10):
        for c in cats:
            lst = by_cat[c]
            if round_i < len(lst):
                r = lst[round_i]
                if r["id"] not in chosen_ids:
                    chosen.append(r); chosen_ids.add(r["id"])
        if len(chosen) >= 30:
            break
    # fill remainder with newest overall
    for r in all_recipes:
        if len(chosen) >= 30:
            break
        if r["id"] not in chosen_ids:
            chosen.append(r); chosen_ids.add(r["id"])
    return chosen[:30]

def main():
    print("Menyarap saluran kreator…")
    scraped = []
    for ch in CHANNELS:
        page = fetch_channel_videos_page(ch["id"], ch["creator"])
        rss = fetch_channel(ch)
        merged = {v["vid"]: v for v in page}
        for r in rss:
            merged.setdefault(r["vid"], r)
        for v in merged.values():
            if RECIPE_HINTS.search(f'{v["title"]} {v["desc"][:200]}'):
                scraped.append(v)
        print(f"  ✓ {ch['creator']}: {len([v for v in merged.values() if RECIPE_HINTS.search(v['title'])])} kandidat")
        time.sleep(1)

    # merge with existing pool so recipes don't vanish once they age out of RSS
    existing = []
    if DATA_FILE.exists():
        try:
            existing = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            existing = [r for r in existing if str(r.get("id", "")).startswith("yt_")]
        except Exception:
            existing = []
    # backfill guides for older entries that were saved before guide extraction
    seen = {r["id"] for r in existing}
    for e in scraped:
        # always pull the full description — RSS/teasers are not enough for guides
        if len(e["desc"]) < 300:
            full = fetch_full_description(e["vid"])
            if len(full) > len(e["desc"]):
                e["desc"] = full
            time.sleep(0.35)  # be polite to YouTube
        rec = build_recipe(e)
        if rec["id"] in seen:
            for i, o in enumerate(existing):
                if o["id"] == rec["id"] and (len(rec.get("ingredientList") or []) > len(o.get("ingredientList") or [])
                                             or len(rec.get("steps") or []) > len(o.get("steps") or [])):
                    existing[i] = rec
        else:
            existing.append(rec)
            seen.add(rec["id"])
    # keep only entries with a real cooking guide — the board must be informative
    existing = [r for r in existing if r.get("ingredientList") or r.get("steps")]
    # normalise legacy entries that lack the new fields
    for i, r in enumerate(existing):
        if not r.get("category"):
            existing[i] = build_recipe({
                "vid": str(r["id"]).replace("yt_", ""), "title": r.get("name", ""),
                "published": r.get("published", ""), "desc": r.get("summary", ""),
                "views": r.get("views"), "creator": r.get("creator", ""),
            })

    final = pick_balanced_30(existing)
    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(final, ensure_ascii=False, indent=1), encoding="utf-8")

    with_ings = sum(1 for r in final if r["ingredientList"])
    with_steps = sum(1 for r in final if r["steps"])
    cats = sorted({r["category"] for r in final})
    print(f"Kolam akhir: {len(final)} resepi | {with_ings} dengan bahan | {with_steps} dengan langkah")
    print(f"Kategori: {', '.join(cats)}")

if __name__ == "__main__":
    main()
