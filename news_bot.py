import asyncio
import difflib
import io
import json
import os
import re
import socket
import sys
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import discord
import feedparser
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator, MyMemoryTranslator
from discord.ext import commands, tasks

try:
    import settings as _settings
except ImportError:      # على GitHub ما فيه settings.py، الإعدادات تجي من Secrets
    _settings = None


def _cfg(name, default=None):
    """يقرأ الإعداد من متغيرات البيئة (GitHub Secrets) ثم من settings.py."""
    value = os.environ.get(name)
    if value not in (None, ""):
        return value
    if _settings is not None:
        return getattr(_settings, name, default)
    return default


def _cfg_int(name, default=0):
    try:
        return int(_cfg(name, default))
    except (TypeError, ValueError):
        return default


TOKEN = _cfg("DISCORD_TOKEN") or _cfg("TOKEN")
GAMES_CHANNEL_ID = _cfg_int("GAMES_CHANNEL_ID")
ANIME_CHANNEL_ID = _cfg_int("ANIME_CHANNEL_ID")
MANGA_CHANNEL_ID = _cfg_int("MANGA_CHANNEL_ID")
MOVIES_CHANNEL_ID = _cfg_int("MOVIES_CHANNEL_ID")
MUSIC_CHANNEL_ID = _cfg_int("MUSIC_CHANNEL_ID")
CHECK_EVERY_MINUTES = _cfg_int("CHECK_EVERY_MINUTES", 30)
MAX_PER_FEED = _cfg_int("MAX_PER_FEED", 2)
MAX_TEXT = _cfg_int("MAX_TEXT", 1200)

# RUN_ONCE=1 يعني: يشتغل مرة وحدة ويقفل (وضع GitHub Actions)
RUN_ONCE = os.environ.get("RUN_ONCE") == "1"

# صور افتراضية اختيارية (من settings.py فقط)
DEFAULT_IMAGES = getattr(_settings, "DEFAULT_IMAGES", {}) if _settings else {}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# كل قسم: اسمه، القناة، لون الشريط، المصادر، وكلمات اختيارية للتصفية
CATEGORIES = [
    {
        "name": "أخبار المانجا",
        "key": "manga",
        "channel": MANGA_CHANNEL_ID,
        "color": 0xE67E22,
        "feeds": [
            "https://www.animenewsnetwork.com/all/rss.xml",
            "https://otakuusamagazine.com/feed/",
        ],
        "keywords": ["manga", "manhwa", "manhua", "light novel", "webtoon"],
    },
    {
        "name": "أخبار الأنمي",
        "key": "anime",
        "channel": ANIME_CHANNEL_ID,
        "color": 0xFF6B9D,
        "feeds": [
            "https://www.animenewsnetwork.com/all/rss.xml",
            "https://myanimelist.net/rss/news.xml",
        ],
        "keywords": None,
    },
    {
        "name": "أخبار الألعاب",
        "key": "games",
        "channel": GAMES_CHANNEL_ID,
        "color": 0x5865F2,
        "feeds": [
            "https://www.gamespot.com/feeds/news/",
            "https://www.polygon.com/rss/index.xml",
        ],
        "keywords": None,
        # لازم الخبر يتكلم عن ألعاب
        "require_groups": [
            [
                r"\bgames?\b", r"\bgaming\b", r"playstation", r"\bps[45]\b", r"xbox",
                r"nintendo", r"\bswitch\b", r"\bsteam\b", r"\bdlc\b", r"\bconsoles?\b",
            ],
        ],
        # ولازم العنوان يدل على حدث مهم: إصدار/إعلان/تريلر/تحديث كبير/مراجعة...
        "require_title_groups": [
            [
                r"release date", r"\blaunch", r"\bannounc", r"\breveal", r"\btrailer",
                r"\bgameplay", r"\bdelay", r"\breview", r"\bupdate", r"\bpatch",
                r"\bdlc\b", r"expansion", r"\bremake", r"\bremaster", r"\bsequel",
                r"\bbeta\b", r"early access", r"game awards", r"\bgoty\b",
                r"game of the year", r"coming (to|in|out)", r"\bout now", r"available now",
                r"\bpreview", r"\bdemo\b", r"shadow ?drop", r"nintendo direct",
                r"state of play", r"showcase", r"\bfree\b", r"\bbest\b.*\bgames?\b",
                r"\bgreatest\b.*\bgames?\b",
            ],
        ],
        # يرفض العنوان إذا فيه: أفلام/مسلسلات/كتب، أو قوائم وشروحات ومواضيع جانبية
        "block_title": [
            r"^\d+\s", r"\bfilms?\b", r"\bmovies?\b", r"\btv\b", r"\bnovels?\b", r"\bbooks?\b",
            r"\bnetflix\b", r"\bhbo\b", r"\bdisney\+?", r"\bepisodes?\b", r"\bsitcom",
            r"\bpodcast", r"\bdocumentary", r"\bcomics?\b",
            r"\bguide\b", r"how to", r"\bwhere to\b", r"\bwalkthrough", r"\btips?\b",
            r"tier list", r"\bexplained\b", r"easter eggs?", r"\bdeals?\b", r"\bdiscount",
            r"\bcoupon", r"\bcosplay", r"\bmeme", r"\bfans? (react|react|are)\b", r"\btweet",
            r"\breddit\b", r"\bstreamer", r"\btwitch\b", r"\byoutuber",
            r"\blawsuit", r"\bsues?\b", r"\blayoffs?\b", r"\bunion\b", r"\bstock\b",
            r"\bearnings\b", r"\bdies\b", r"\bobituar",
        ],
    },
    {
        "name": "أخبار الأفلام",
        "key": "movies",
        "channel": MOVIES_CHANNEL_ID,
        "color": 0xE74C3C,
        "feeds": [
            "https://www.slashfilm.com/feed/",
            "https://deadline.com/feed/",
        ],
        "keywords": None,
        # لازم الخبر يتكلم عن فيلم
        "require_groups": [
            [r"\bfilm", r"\bmovie"],
        ],
        # ولازم العنوان يدل على: إصدار/تريلر/إيرادات/مراجعة/مهرجان/أوسكار...
        "require_title_groups": [
            [
                r"\btrailer", r"\bteaser", r"release date", r"sets? release", r"box office",
                r"opening weekend", r"opens? (to|with|in)", r"\bpremiere", r"\bsequel",
                r"\bprequel", r"\breboot", r"\bremake", r"first look", r"\bposter",
                r"\breview", r"\bfestival", r"\boscars?\b", r"\bgreenlit", r"coming (to|in)",
                r"in theaters", r"\bdebuts?",
            ],
        ],
        # يرفض العنوان إذا فيه: أخبار ممثلين شخصية، مسلسلات، قوائم، ذكريات قديمة، أعمال
        "block_title": [
            r"^\d+\s", r"years? (ago|later)", r"\banniversary", r"\brewatch", r"\bunderrated",
            r"\bforgotten", r"\bclassic\b", r"\bdecades?\b", r"\bflop\b", r"retrospective",
            r"throwback", r"\bexplained\b", r"\bending\b", r"of all time", r"\branked\b",
            r"\bdating\b", r"\bdivorc", r"\bmarri", r"\bwedding", r"\bengag", r"\bpregnan",
            r"\bbaby\b", r"\barrest", r"\blawsuit", r"\bsues?\b", r"\bfeud", r"net worth",
            r"\bdies\b", r"\bdeath\b", r"\bdead\b", r"obituar", r"funeral", r"\brehab",
            r"scandal", r"break ?up", r"\bsplit\b", r"instagram", r"tiktok", r"red carpet",
            r"\boutfit", r"\bfashion",
            r"season \d", r"\bepisode", r"showrunner", r"\bsitcom", r"tv series", r"tv show",
            r"series finale", r"\blayoffs?\b", r"\bstock\b", r"\bearnings\b",
        ],
    },
    {
        "name": "أخبار الموسيقى",
        "key": "music",
        "channel": MUSIC_CHANNEL_ID,
        "color": 0x1ABC9C,
        "feeds": [
            "https://www.billboard.com/feed/",
            "https://pitchfork.com/feed/feed-news/rss",
        ],
        "keywords": None,
    },
]

SEEN_FILE = "seen.json"
SCAN_ENTRIES = 20
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


MAX_SEEN_LINKS = 3000      # كم رابط يتذكر
MAX_SEEN_TITLES = 600      # كم عنوان يتذكر
TITLE_SIMILARITY = 0.82    # كل ما قل الرقم صار أشد في منع الأخبار المتشابهة

TRACKING_PARAMS = {
    "ref", "source", "src", "fbclid", "gclid", "cmpid", "cid", "mc_cid", "mc_eid",
    "feature", "rss", "output", "amp", "campaign",
}
GENERIC_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "new", "gets", "get", "got",
    "anime", "manga", "film", "movie", "season", "episode", "episodes", "reveals",
    "announces", "announced", "release", "date", "trailer", "video", "game", "games",
}


def normalize_url(u):
    """يوحّد شكل الرابط (بدون www ولا رموز تتبع ولا / في الآخر)."""
    u = (u or "").strip()
    if not u.lower().startswith("http"):
        return u.lower()
    try:
        p = urlsplit(u)
        host = p.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        query = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS)
        ]
        return urlunsplit(("https", host, p.path.rstrip("/"), urlencode(query), ""))
    except Exception:
        return u


def norm_title(t):
    t = re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def title_tokens(nt):
    return {w for w in nt.split() if len(w) >= 3 and w not in GENERIC_WORDS}


def load_seen():
    links, titles = {}, []
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):          # الصيغة القديمة
                raw_links, raw_titles = data, []
            else:
                raw_links = data.get("links", [])
                raw_titles = data.get("titles", [])
            for l in raw_links:
                links[normalize_url(l)] = None
            titles = list(raw_titles)
        except Exception as e:
            print("Could not read seen.json, starting fresh:", e)
    return links, titles


def save_seen():
    tmp = SEEN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"links": list(seen_links), "titles": seen_titles}, f)
    os.replace(tmp, SEEN_FILE)


seen_links, seen_titles = load_seen()


def similar_title(nt):
    toks = title_tokens(nt)
    for old in seen_titles:
        sm = difflib.SequenceMatcher(None, nt, old)
        if (
            sm.real_quick_ratio() >= TITLE_SIMILARITY
            and sm.quick_ratio() >= TITLE_SIMILARITY
            and sm.ratio() >= TITLE_SIMILARITY
        ):
            return True
        old_toks = title_tokens(old)
        shared = toks & old_toks
        if len(shared) >= 3 and len(shared) / len(toks | old_toks) >= 0.7:
            return True
    return False


def is_duplicate(entry):
    """هل هذا الخبر (أو خبر مشابه جداً له) انرسل من قبل؟"""
    if normalize_url(entry.get("link")) in seen_links:
        return True
    eid = entry.get("id")
    if eid and normalize_url(eid) in seen_links:
        return True
    nt = norm_title(entry.get("title", ""))
    return bool(nt) and similar_title(nt)


def mark_seen(entry):
    """يسجّل الخبر فوراً ويحفظه في الملف."""
    seen_links[normalize_url(entry.get("link"))] = None
    eid = entry.get("id")
    if eid:
        seen_links[normalize_url(eid)] = None
    while len(seen_links) > MAX_SEEN_LINKS:
        seen_links.pop(next(iter(seen_links)))
    nt = norm_title(entry.get("title", ""))
    if nt:
        seen_titles.append(nt)
        del seen_titles[:-MAX_SEEN_TITLES]
    save_seen()


def clean_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


class TranslationUnavailable(Exception):
    """الترجمة معطلة الآن (قوقل حاظرنا مؤقتاً مثلاً)."""


_translation_pause_until = 0.0   # إذا فشلت الترجمة نوقف الإرسال شوي بدل ما نرسل إنجليزي
TRANSLATION_PAUSE_SECONDS = 15 * 60


def trim_to_sentence(text, limit):
    """يقص النص عند نهاية جملة بدل ما يقطعه في النص."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    pos = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind(".\n"))
    if pos >= limit * 0.5:
        return cut[: pos + 1]
    return cut.rstrip() + "…"


def translate_pair(title, body):
    """يترجم العنوان والنص بطلب واحد. إذا فشل كل شيء يرفع TranslationUnavailable
    (ما نرسل خبر بالإنجليزي أبداً)."""
    title = (title or "").strip()
    body = (body or "").strip()
    combined = f"{title}\n\n{body}" if body else title

    # 1) قوقل (طلب واحد للعنوان والنص)
    for attempt in range(3):
        try:
            google = GoogleTranslator(source="auto", target="ar")
            out = (google.translate(combined[:4500]) or "").strip()
            if out:
                parts = out.split("\n", 1)
                t_ar = parts[0].strip()
                b_ar = parts[1].strip() if len(parts) > 1 else ""
                if body and not b_ar:   # لو ضاع الفاصل نترجم العنوان والنص كل واحد لحاله
                    t_ar = (google.translate(title[:500]) or "").strip()
                    b_ar = (google.translate(body[:4500]) or "").strip()
                if t_ar and (b_ar or not body):
                    time.sleep(1.5)     # تأخير بسيط عشان ما يحظرنا قوقل
                    return t_ar, b_ar
        except Exception as e:
            print("Google translate failed, attempt", attempt + 1, ":", e)
            time.sleep(10 * (attempt + 1))

    # 2) مترجم احتياطي (يترجم نص أقصر)
    try:
        mm = MyMemoryTranslator(source="en-US", target="ar-SA")
        t_ar = (mm.translate(title[:450]) or "").strip()
        b_ar = (mm.translate(trim_to_sentence(body, 450)) or "").strip() if body else ""
        if "MYMEMORY WARNING" in (t_ar + b_ar).upper():
            raise RuntimeError("MyMemory daily quota reached")
        if t_ar and (b_ar or not body):
            return t_ar, b_ar
    except Exception as e:
        print("Backup translator failed:", e)

    raise TranslationUnavailable()


def feed_image(entry):
    """صورة الخبر إذا كانت موجودة داخل الـ RSS نفسه."""
    for key in ("media_content", "media_thumbnail"):
        items = entry.get(key)
        if items and items[0].get("url"):
            return items[0]["url"]
    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("image") and enc.get("href"):
            return enc["href"]
    html = entry.get("summary", "")
    for c in entry.get("content", []):
        html += c.get("value", "")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', html)
    if m and m.group(1).startswith("http"):
        return m.group(1)
    return None


BAD_IMAGE_WORDS = ("logo", "icon", "avatar", "sprite", "pixel", "ads", "banner", "blank", "spacer")


def find_page_image(soup, base_url):
    # 1) الوسوم الرسمية للصورة الرئيسية
    for attrs in (
        {"property": "og:image"},
        {"property": "og:image:url"},
        {"name": "twitter:image"},
        {"name": "twitter:image:src"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            url = urljoin(base_url, tag["content"].strip())
            if url.startswith("http"):
                return url
    link = soup.find("link", rel="image_src")
    if link and link.get("href"):
        url = urljoin(base_url, link["href"].strip())
        if url.startswith("http"):
            return url

    # 2) أول صورة معقولة داخل الصفحة
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if not src:
            continue
        low = src.lower()
        if any(w in low for w in BAD_IMAGE_WORDS) or low.endswith((".svg", ".gif")):
            continue
        width = str(img.get("width", ""))
        if width.isdigit() and int(width) < 200:
            continue
        url = urljoin(base_url, src.strip())
        if url.startswith("http"):
            return url
    return None


def fetch_page(url):
    """يرجع (الصورة الرئيسية، أول فقرات) من صفحة الخبر."""
    try:
        r = requests.get(url, timeout=15, headers=HEADERS)
        if r.status_code != 200:
            print(f"Article page returned status {r.status_code}: {url}")
            return None, ""
        soup = BeautifulSoup(r.text, "html.parser")
        image = find_page_image(soup, url)
        paras, total = [], 0
        for p in soup.find_all("p"):
            t = p.get_text(" ", strip=True)
            if len(t) < 80:
                continue
            paras.append(t)
            total += len(t)
            if total > MAX_TEXT:
                break
        return image, "\n\n".join(paras)
    except Exception as e:
        print("Could not open article page:", e)
        return None, ""


def download_image(url, referer):
    """ينزّل الصورة بنفسه عشان نرفعها مع الرسالة (يتجاوز منع بعض المواقع)."""
    try:
        h = dict(HEADERS)
        h["Referer"] = referer
        h["Accept"] = "image/avif,image/webp,image/*,*/*;q=0.8"
        r = requests.get(url, timeout=15, headers=h)
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if r.status_code != 200 or not ctype.startswith("image/"):
            print(f"Image download failed (status {r.status_code}, type {ctype}):", url)
            return None
        if len(r.content) > 8 * 1024 * 1024:
            print("Image too large, skipping upload:", url)
            return None
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(ctype)
        if not ext:
            return None
        return r.content, ext
    except Exception as e:
        print("Image download error:", e)
        return None


def default_image_file(cat):
    """يرجع الصورة الافتراضية للقسم (من ملف بجانب البوت) أو None."""
    name = DEFAULT_IMAGES.get(cat.get("key"))
    if not name:
        return None
    path = name if os.path.isabs(name) else os.path.join(BASE_DIR, name)
    if not os.path.exists(path):
        print("Default image file not found:", path)
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        print("Default image must be png/jpg/webp/gif:", path)
        return None
    return discord.File(path, filename="default" + ext)


ANILIST_URL = "https://graphql.anilist.co"
ANILIST_QUERY = """
query ($search: String, $type: MediaType) {
  Page(perPage: 5) {
    media(search: $search, type: $type, sort: SEARCH_MATCH) {
      title { romaji english }
      bannerImage
      coverImage { extraLarge large }
    }
  }
}
"""

NAME_STOPWORDS = {
    "japan", "japanese", "crunchyroll", "netflix", "anime", "manga", "nhk", "tokyo",
    "funimation", "amazon", "disney", "studio", "season", "episode", "episodes",
    "the", "this", "that", "with", "from", "film", "movie", "series", "volume",
    "twitter", "youtube", "sunday", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
}

_TOK = r"(?:(?:Dr|Mr|Mrs|Ms|St)\.|[A-Z0-9][\w'’\-]*)"
_LINK = r"(?:of|the|no|wa|to|a|in|on|and|ni|de|ga|wo|na|e)"
NAME_PATTERN = re.compile(rf"{_TOK}(?:(?::)?\s+(?:{_LINK}\s+)*{_TOK})*[!?]?")
SPLIT_WORDS = (" on ", " in ", " at ", " for ", " by ", " and ", " with ")


def candidate_names(text):
    """يستخرج أسماء محتملة لأنمي/مانجا من نص الخبر الإنجليزي."""
    found = []
    for m in re.finditer(r'["“]([^"”]{3,80})["”]', text):
        found.append(m.group(1).strip())
    for m in NAME_PATTERN.finditer(text):
        full = m.group(0).strip(" .,:;'’-")
        found.append(full)
        # إذا الاسم ملتصق بكلمات مثل "on TV Tokyo" نجرب الجزء الأول لحاله
        for w in SPLIT_WORDS:
            if w in full:
                found.append(full.split(w)[0].strip(" .,:;'’-"))
                break
    result, seen_names = [], set()
    for c in found:
        low = c.lower()
        if len(c) < 4 or low in NAME_STOPWORDS or low in seen_names:
            continue
        seen_names.add(low)
        result.append(c)
        if len(result) >= 10:
            break
    return result


def anilist_lookup(name, kind):
    try:
        r = requests.post(
            ANILIST_URL,
            json={
                "query": ANILIST_QUERY,
                "variables": {"search": name, "type": "ANIME" if kind == "anime" else "MANGA"},
            },
            timeout=15,
        )
        if r.status_code != 200:
            print("AniList returned status", r.status_code)
            return None
        media = r.json()["data"]["Page"]["media"]
    except Exception as e:
        print("AniList lookup error:", e)
        return None

    variants = {name.lower()}
    for prefix in ("the ", "a "):
        if name.lower().startswith(prefix):
            variants.add(name.lower()[len(prefix):])

    best, best_score = None, 0.0
    for m in media:
        for t in (m["title"].get("romaji"), m["title"].get("english")):
            if not t:
                continue
            for v in variants:
                score = difflib.SequenceMatcher(None, v, t.lower()).ratio()
                if score > best_score:
                    best, best_score = m, score
    if best and best_score >= 0.85:
        cover = best.get("coverImage") or {}
        return best.get("bannerImage") or cover.get("extraLarge") or cover.get("large")
    return None


def find_anime_image(text, kind):
    """يدور على صورة الأنمي/المانجا المذكورة في الخبر (من AniList)."""
    import time

    for name in candidate_names(text[:1500]):
        url = anilist_lookup(name, kind)
        time.sleep(0.8)
        if url:
            print(f"Matched '{name}' on AniList")
            return url
    return None


def passes_filter(entry, cat):
    """هل الخبر يناسب القسم؟ (كلمات مطلوبة + مجموعات إلزامية + كلمات ممنوعة في العنوان)"""
    title = entry.get("title", "").lower()
    text = title + " " + clean_html(entry.get("summary", "")).lower()

    keywords = cat.get("keywords")
    if keywords and not any(k in text for k in keywords):
        return False

    for group in cat.get("require_groups") or []:
        if not any(re.search(pat, text) for pat in group):
            return False

    # مجموعات لازم تظهر في العنوان نفسه (أدق من البحث في كل النص)
    for group in cat.get("require_title_groups") or []:
        if not any(re.search(pat, title) for pat in group):
            return False

    for pat in cat.get("block_title") or []:
        if re.search(pat, title):
            return False
    return True


async def post_category(cat):
    global _translation_pause_until
    channel_id = cat["channel"]
    if not channel_id:
        return
    if time.monotonic() < _translation_pause_until:
        print("Translation is paused (Google limit). Skipping:", cat["name"])
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        print(f"Channel not found: {channel_id} ({cat['name']}). Check the ID and bot permissions.")
        return

    for url in cat["feeds"]:
        feed = await asyncio.to_thread(feedparser.parse, url)
        if not feed.entries:
            print("No entries from this feed (maybe the link is wrong):", url)
            continue

        posted = 0
        for entry in feed.entries[:SCAN_ENTRIES]:
            if posted >= MAX_PER_FEED:
                break
            link = entry.get("link")
            if not link or is_duplicate(entry):
                continue
            if not passes_filter(entry, cat):
                continue

            summary = clean_html(entry.get("summary", ""))
            image = feed_image(entry)

            if image is None or len(summary) < 300:
                page_image, page_text = await asyncio.to_thread(fetch_page, link)
                image = image or page_image
                if len(summary) < 300 and page_text:
                    summary = page_text

            if image is None and cat["key"] in ("anime", "manga"):
                look_in = entry.get("title", "") + ". " + summary
                image = await asyncio.to_thread(find_anime_image, look_in, cat["key"])

            if image:
                print("Image found:", image)
            else:
                print("No image found for:", link)

            try:
                title, body = await asyncio.to_thread(
                    translate_pair,
                    entry.get("title", ""),
                    trim_to_sentence(summary, MAX_TEXT),
                )
            except TranslationUnavailable:
                _translation_pause_until = time.monotonic() + TRANSLATION_PAUSE_SECONDS
                print("Translation unavailable: NOT posting English news. "
                      "Will retry in 15 minutes.")
                return
            mark_seen(entry)

            description = f"**{title}**\n\n{body}\n\n[المصدر]({link})"
            embed = discord.Embed(description=description[:4000], color=cat["color"])
            embed.set_footer(text=cat["name"])

            file = None
            if image:
                data = await asyncio.to_thread(download_image, image, link)
                if data:
                    filename = f"news.{data[1]}"
                    file = discord.File(io.BytesIO(data[0]), filename=filename)
                    embed.set_image(url=f"attachment://{filename}")

            if file is None:
                file = default_image_file(cat)
                if file:
                    embed.set_image(url=f"attachment://{file.filename}")
                elif image:
                    embed.set_image(url=image)

            try:
                if file:
                    await channel.send(embed=embed, file=file)
                else:
                    await channel.send(embed=embed)
            except discord.Forbidden:
                print("Missing permission! Give the bot 'Attach Files' in this channel/role.")
                if image:
                    embed.set_image(url=image)
                else:
                    embed.remove_image()
                await channel.send(embed=embed)
            posted += 1
            await asyncio.sleep(3)


async def preload_seen_from_channels():
    """يقرأ آخر رسائل البوت في القنوات ويسجّل روابط أخبارها كأنها انرسلت.
    حماية من التكرار حتى لو فشل حفظ seen.json (مهم على GitHub Actions)."""
    added = 0
    for cat in CATEGORIES:
        cid = cat["channel"]
        if not cid:
            continue
        channel = bot.get_channel(cid)
        if channel is None:
            continue
        try:
            async for msg in channel.history(limit=100):
                if msg.author.id != bot.user.id:
                    continue
                for emb in msg.embeds:
                    for link in re.findall(r"\]\((https?://[^)\s]+)\)", emb.description or ""):
                        key = normalize_url(link)
                        if key not in seen_links:
                            seen_links[key] = None
                            added += 1
        except discord.Forbidden:
            print("Cannot read history in", cat["name"],
                  "- give the bot 'Read Message History' for extra duplicate protection.")
        except Exception as e:
            print("Could not read history for", cat["name"], ":", e)
    print("Loaded", added, "links from recent channel messages")


async def run_all_categories():
    for cat in CATEGORIES:
        try:
            await post_category(cat)
        except Exception as e:
            print("Error in category", cat["name"], ":", e)


@tasks.loop(minutes=CHECK_EVERY_MINUTES)
async def news_loop():
    await run_all_categories()


_ready_done = False


@bot.event
async def on_ready():
    global _ready_done
    print("Bot started:", bot.user)
    if RUN_ONCE:
        if _ready_done:
            return
        _ready_done = True
        await preload_seen_from_channels()
        await run_all_categories()
        print("Run finished, closing.")
        await bot.close()
        return
    if not news_loop.is_running():
        await preload_seen_from_channels()
        news_loop.start()


if not TOKEN:
    print("ERROR: no bot token found. Set DISCORD_TOKEN (GitHub Secret) or TOKEN in settings.py.")
    sys.exit(1)

if not RUN_ONCE:
    # يمنع تشغيل نسختين من البوت بنفس الجهاز (وإلا ترسل الأخبار مرتين)
    _lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _lock.bind(("127.0.0.1", 48621))
    except OSError as e:
        if e.errno in (98, 10048, 48):
            print("Another copy of this bot is already running here. Close it first.")
            sys.exit(1)

bot.run(TOKEN)
