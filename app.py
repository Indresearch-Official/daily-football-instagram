import os
import sys
import json
import re
import time
import html
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import feedparser
import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public" / "daily"
OUTPUT_DIR = ROOT / "output"
POST_FILE = OUTPUT_DIR / "post.json"
TZ = ZoneInfo("Asia/Kolkata")

FEEDS = [
    ("BBC Sport", "https://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("The Guardian", "https://www.theguardian.com/football/rss"),
    ("Sky Sports", "https://www.skysports.com/rss/11095"),
]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v26.0")


def clean_text(value):
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()


def entry_datetime(entry):
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(TZ)
            except Exception:
                pass
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).astimezone(TZ)
        except Exception:
            pass
    return None


def collect_news():
    today = datetime.now(TZ).date()
    found = []
    seen = set()

    for source_name, feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url, request_headers={"User-Agent": "DailyFootballBriefing/1.0"})
        except Exception as exc:
            print(f"Feed error ({source_name}): {exc}")
            continue

        for entry in feed.entries:
            published = entry_datetime(entry)
            if not published or published.date() != today:
                continue

            title = clean_text(entry.get("title", ""))
            link = entry.get("link", "").strip()
            summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
            if not title or not link:
                continue

            parsed = urlparse(link)
            if parsed.scheme != "https" or not parsed.netloc:
                continue

            fingerprint = hashlib.sha256((title.lower() + "|" + link.split("?")[0]).encode()).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)

            found.append({
                "source": source_name,
                "title": title[:300],
                "summary": summary[:1200],
                "url": link,
                "published_ist": published.isoformat(),
            })

    found.sort(key=lambda item: item["published_ist"], reverse=True)
    return found[:35]


def gemini_digest(items):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY secret.")

    indexed = [{"source_index": i, **item} for i, item in enumerate(items)]
    prompt = f"""
You are an accurate football news editor creating one daily Instagram carousel.
Today's date in Asia/Kolkata is {datetime.now(TZ).strftime('%d %B %Y')}.

Use ONLY the source items supplied below. Do not add facts, transfer fees, quotes,
scores, injuries, dates, or claims that are not supported by the source text.
Do not treat rumours as confirmed. Clearly label reported/rumoured claims.
Select the 2 to 6 most important DISTINCT stories from today's football news.
Prefer major competitions, confirmed transfers, club developments, international
football and major player news. Avoid trivial stories and duplicate coverage.
Write concise original summaries; do not copy source sentences.

Return ONLY valid JSON with this exact shape:
{{
  "stories": [
    {{
      "source_index": 0,
      "category": "TRANSFER / MATCH / CLUB / INTERNATIONAL",
      "headline": "Short headline, max 90 characters",
      "summary": "One or two concise factual sentences, max 220 characters",
      "context": "A short factual explanation of why this matters, max 180 characters"
    }}
  ],
  "caption": "Instagram caption, concise, with a short intro and a few relevant hashtags."
}}

Every source_index must be an integer referring to an item below. Use each source
index at most once. Return between 2 and 6 stories if the sources support that
many. If fewer than 2 genuinely distinct, sufficiently supported stories exist,
return an empty stories array and explain briefly in caption that there is not
enough verified news for a responsible digest.

SOURCE ITEMS:
{json.dumps(indexed, ensure_ascii=False)}
"""
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    response = requests.post(
        endpoint,
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "maxOutputTokens": 2200,
            },
        },
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"Could not parse Gemini JSON response: {payload}") from exc

    valid = []
    used = set()
    for story in data.get("stories", []):
        try:
            idx = int(story["source_index"])
        except Exception:
            continue
        if idx < 0 or idx >= len(items) or idx in used:
            continue
        source = items[idx]
        headline = clean_text(story.get("headline", ""))[:90]
        summary = clean_text(story.get("summary", ""))[:220]
        context = clean_text(story.get("context", ""))[:180]
        category = clean_text(story.get("category", "FOOTBALL"))[:24].upper()
        if not headline or not summary:
            continue
        valid.append({
            "headline": headline,
            "summary": summary,
            "context": context,
            "category": category,
            "source": source["source"],
            "url": source["url"],
        })
        used.add(idx)
        if len(valid) == 6:
            break

    caption = clean_text(data.get("caption", ""))[:1700]
    return {"stories": valid, "caption": caption}


def font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def wrap_text(draw, text, fnt, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = word if not current else current + " " + word
        if draw.textbbox((0, 0), test, font=fnt)[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_wrapped(draw, text, xy, fnt, fill, max_width, line_gap=15, max_lines=None):
    x, y = xy
    lines = wrap_text(draw, text, fnt, max_width)
    if max_lines:
        lines = lines[:max_lines]
    for line in lines:
        draw.text((x, y), line, font=fnt, fill=fill)
        y += fnt.size + line_gap
    return y


def make_slide(stories, slide_no, total, date_label, is_context=False):
    W, H = 1080, 1350
    im = Image.new("RGB", (W, H), (10, 13, 19))
    d = ImageDraw.Draw(im)

    # Subtle editorial background accents.
    for y in range(H):
        shade = int(10 + (y / H) * 7)
        d.line((0, y, W, y), fill=(shade, shade + 3, shade + 8))
    d.ellipse((770, -230, 1300, 300), fill=(34, 23, 18))
    d.rectangle((0, 0, 18, H), fill=(245, 105, 35))

    d.text((72, 58), "THE FOOTBALL BRIEF", font=font(34, True), fill=(245, 245, 245))
    d.text((72, 108), date_label.upper(), font=font(22, True), fill=(180, 185, 194))
    d.rounded_rectangle((72, 164, 360, 218), radius=18, fill=(238, 95, 31))
    d.text((94, 174), "DAILY NEWS", font=font(22, True), fill=(255, 255, 255))
    d.text((890, 178), f"{slide_no:02d} / {total:02d}", font=font(23, True), fill=(205, 210, 220))

    y = 280
    if is_context:
        story = stories[0]
        d.text((72, y), "THE BIG PICTURE", font=font(27, True), fill=(245, 115, 55))
        y += 72
        y = draw_wrapped(d, story["headline"], (72, y), font(48, True), (250, 250, 250), 920, 14, 3)
        y += 35
        y = draw_wrapped(d, story["context"] or story["summary"], (72, y), font(32), (205, 211, 220), 900, 17, 6)
        y += 40
        d.rounded_rectangle((72, y, 1008, y + 3), radius=2, fill=(238, 95, 31))
        y += 35
        d.text((72, y), "SOURCE", font=font(20, True), fill=(245, 115, 55))
        draw_wrapped(d, story["source"], (72, y + 42), font(28, True), (245, 245, 245), 900, 10, 2)
    else:
        for idx, story in enumerate(stories):
            d.text((72, y), story["category"], font=font(21, True), fill=(245, 115, 55))
            y += 42
            y = draw_wrapped(d, story["headline"], (72, y), font(39, True), (250, 250, 250), 930, 12, 3)
            y += 18
            y = draw_wrapped(d, story["summary"], (72, y), font(27), (195, 202, 213), 920, 13, 4)
            y += 25
            d.text((72, y), f"SOURCE: {story['source'].upper()}", font=font(17, True), fill=(145, 153, 165))
            y += 55
            if idx < len(stories) - 1:
                d.line((72, y, 1008, y), fill=(65, 70, 80), width=2)
                y += 45

    d.line((72, 1240, 1008, 1240), fill=(75, 80, 90), width=2)
    d.text((72, 1270), "FOOTBALL, EVERY DAY.", font=font(21, True), fill=(235, 235, 235))
    d.text((790, 1270), "@YOURPAGE", font=font(20, True), fill=(245, 115, 55))
    return im


def generate():
    OUTPUT_DIR.mkdir(exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    items = collect_news()
    print(f"Collected {len(items)} dated stories from feeds.")
    if len(items) < 2:
        print("Not enough same-day stories. Skipping publication.")
        if POST_FILE.exists():
            POST_FILE.unlink()
        return False

    digest = gemini_digest(items)
    stories = digest["stories"]
    if len(stories) < 2:
        print("AI found fewer than two sufficiently supported stories. Skipping publication.")
        if POST_FILE.exists():
            POST_FILE.unlink()
        return False

    # Instagram carousel posts need at least two media items. With exactly
    # two stories, put one distinct story on each slide; otherwise group up to
    # two stories per slide. This produces 2 or 3 slides without duplication.
    if len(stories) == 2:
        groups = [[stories[0]], [stories[1]]]
    else:
        groups = [stories[i:i + 2] for i in range(0, len(stories), 2)]
    groups = groups[:3]

    # If only two stories, create slide two with the second story; if three/four,
    # group naturally; if five/six, create three slides.
    date_label = datetime.now(TZ).strftime("%d %B %Y")
    for old in PUBLIC_DIR.glob("slide-*.jpg"):
        old.unlink()

    for i, group in enumerate(groups):
        image = make_slide(group, i + 1, len(groups), date_label)
        image.save(PUBLIC_DIR / f"slide-{i + 1}.jpg", "JPEG", quality=92, optimize=True)

    caption = digest["caption"].strip()
    links = []
    for story in stories:
        line = f"• {story['headline']} — {story['source']}: {story['url']}"
        if line not in links:
            links.append(line)
    if not caption:
        caption = f"Today's football briefing — {date_label}."
    caption = caption[:1550] + "\n\nSources:\n" + "\n".join(links[:6])
    caption += "\n\n#FootballNews #Football #Soccer"
    caption = caption[:2190]

    POST_FILE.parent.mkdir(exist_ok=True)
    POST_FILE.write_text(json.dumps({
        "date": date_label,
        "caption": caption,
        "slides": len(groups),
        "story_count": len(stories),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {len(groups)} carousel slides from {len(stories)} stories.")
    return True


def meta_request(method, path, data=None, params=None):
    token = os.getenv("IG_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Missing IG_ACCESS_TOKEN repository secret.")
    base = f"https://graph.instagram.com/{GRAPH_VERSION}"
    url = f"{base}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}"}
    if method == "POST":
        response = requests.post(url, headers=headers, data=data or {}, timeout=60)
    else:
        response = requests.get(url, headers=headers, params=params or {}, timeout=60)
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text}
    if not response.ok:
        raise RuntimeError(f"Meta API error {response.status_code}: {payload}")
    return payload


def publish():
    if not POST_FILE.exists():
        print("No generated post file; nothing to publish.")
        return

    ig_id = os.getenv("IG_USER_ID")
    repo = os.getenv("GITHUB_REPOSITORY")
    branch = os.getenv("GITHUB_REF_NAME", "main")
    cache_buster = os.getenv("IMAGE_CACHE_BUSTER", str(int(time.time())))
    if not ig_id or not repo:
        raise RuntimeError("Missing IG_USER_ID or GITHUB_REPOSITORY.")

    post = json.loads(POST_FILE.read_text(encoding="utf-8"))
    caption = post["caption"]
    slide_count = int(post["slides"])
    if slide_count < 2 or slide_count > 3:
        raise RuntimeError("Carousel must contain 2 or 3 slides.")

    image_urls = [
        f"https://raw.githubusercontent.com/{repo}/{branch}/public/daily/slide-{i}.jpg?v={cache_buster}"
        for i in range(1, slide_count + 1)
    ]

    child_ids = []
    for image_url in image_urls:
        result = meta_request("POST", f"{ig_id}/media", {
            "image_url": image_url,
            "is_carousel_item": "true",
        })
        child_ids.append(result["id"])

    parent = meta_request("POST", f"{ig_id}/media", {
        "media_type": "CAROUSEL",
        "children": ",".join(child_ids),
        "caption": caption,
    })
    creation_id = parent["id"]

    # Wait for Meta to finish processing the carousel container.
    status = None
    for attempt in range(10):
        state = meta_request("GET", creation_id, params={"fields": "status_code"})
        status = state.get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError(f"Instagram container processing failed: {state}")
        time.sleep(15)
    if status != "FINISHED":
        raise RuntimeError(f"Carousel was not ready to publish. Final status: {status}")

    published = meta_request("POST", f"{ig_id}/media_publish", {
        "creation_id": creation_id,
    })
    print("Instagram carousel published. Media ID:", published.get("id"))


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"generate", "publish"}:
        print("Usage: python app.py generate|publish")
        sys.exit(2)
    if sys.argv[1] == "generate":
        generate()
    else:
        publish()
