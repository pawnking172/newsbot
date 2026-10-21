import asyncio
import datetime
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

# يخلي رسائل السجل تظهر فوراً في GitHub (بدل ما تتأخر لين يخلص التشغيل)
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

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
QUIET_MINUTES = _cfg_int("QUIET_MINUTES", 60)           # الأخبار الجانبية تنزل بس إذا القناة ساكتة كذا دقيقة
RELAXED_MAX = _cfg_int("RELAXED_MAX", 2)                # أقصى عدد أخبار جانبية لكل قناة في المرة
RELAXED_TOTAL_PER_RUN = _cfg_int("RELAXED_TOTAL_PER_RUN", 8)  # أقصى عدد "مختارات" في المرة (كل الأقسام)
RELAXED_PER_DAY = _cfg_int("RELAXED_PER_DAY", 4)        # أقصى عدد أخبار جانبية لكل قناة في اليوم
MAX_PER_CATEGORY = _cfg_int("MAX_PER_CATEGORY", 10)     # أقصى عدد أخبار أساسية لكل قناة في المرة (الباقي ينتظر)
RELAXED_TAG = "مختارات"                                 # الكلمة اللي تظهر أسفل الأخبار الشبه أساسية

BOT_VERSION = "v18-three-tiers"

# RUN_ONCE=1 يعني: يشتغل مرة وحدة ويقفل (وضع GitHub Actions)
RUN_ONCE = os.environ.get("RUN_ONCE") == "1"

# صور افتراضية اختيارية (من settings.py فقط)
DEFAULT_IMAGES = getattr(_settings, "DEFAULT_IMAGES", {}) if _settings else {}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =====================================================================
#   قواعد الأخبار — نفس القواعد لكل القنوات (أنمي، ألعاب، أفلام، مانجا)
#   1) أساسي : ينزل أول بأول ولا يفوت (إصدار جديد، DLC، تكملة، تريلر، موعد...)
#   2) جانبي : تقييمات/أفضل الأعمال المشهورة فقط. ينزل بس إذا القناة ساكتة ساعة،
#              وأقصى حد 2 في المرة.
#   3) ممنوع : أي شيء عن الممثلين والحياة الشخصية والأخبار التافهة.
# =====================================================================

# ---------- 3) ممنوع نهائياً (يُطبّق على كل القنوات، على العنوان) ----------
BLOCK_ALL_TITLE = [
    # حياة شخصية
    r"\bpregnan", r"\b(gives?|gave) birth\b", r"\bfirst (child|baby)\b", r"\bsecond (child|baby)\b",
    r"\bexpecting (a|her|his|their|first|second)\b", r"\bbaby\b",
    r"\bgets? married\b", r"\bgot married\b", r"\bmarries\b", r"\bwedding\b", r"\bengaged\b",
    r"\bengagement\b", r"\bdivorc", r"\bdating\b", r"\bbreak ?up\b", r"\bsplits? (from|with|up)\b",
    r"\b(announces?|reveals?) (her|his|their) (marriage|engagement|wedding|divorce|pregnancy|retirement)\b",
    r"\bpasses away\b", r"\bpassed away\b", r"\bhas died\b", r"\bobituar", r"\bfuneral\b", r"\bin memoriam\b",
    r"\b(actor|actress|seiyuu|idol|singer|mangaka|creator|director|composer|author|artist|designer|legend)\b"
    r".*\b(dies|dead)\b",
    r"\barrest(ed|s)?\b", r"\bscandal\b", r"\bhospitali[sz]ed\b", r"\bcancer\b", r"\brehab\b",
    r"\bfeud\b", r"\blawsuit\b", r"\bsues?\b", r"\bnet worth\b", r"\bbirthday\b",
    # ممثلين (ممنوع نهائياً)
    r"\bactors?\b", r"\bactress(es)?\b", r"\bvoice (actor|actress)\b", r"\bseiyuu\b",
    r"\bstarring\b", r"\bco-?stars?\b", r"\bstars? (in|as|of)\b",
    r"\bjoins (the )?(cast|film|movie|series|show)\b", r"\bportray", r"\b(his|her|their|the) role\b", r"\bbiopic\b",
    # مقابلات وأحاديث شخصية
    r"\binterview\b", r"\bon (playing|portraying|becoming|being|how|why)\b",
    r"\b(talks about|opens up|reflects on|responds to|admits|reacts to)\b",
    # سوشال ومواضيع جانبية
    r"\bcosplay", r"\bmeme", r"\btiktok", r"\binstagram", r"\btweet", r"\breddit\b", r"\bstreamer",
    r"\btwitch\b", r"\byoutuber", r"\bred carpet\b", r"\bfashion\b", r"\boutfit",
    # أعمال وتجارة وعروض
    r"\blayoffs?\b", r"\bstock\b", r"\bearnings\b", r"\bunion\b", r"\bmerch", r"\bfigures?\b", r"\bgoods\b",
    r"\bdeals?\b", r"\bdiscount", r"\bcoupon", r"\bcollab", r"\bcafe\b", r"\bpop-?up\b",
    # شروحات وذكريات قديمة
    r"\bguide\b", r"how to", r"\bwhere to\b", r"\bwalkthrough", r"\btips?\b", r"tier list",
    r"\bexplained\b", r"easter eggs?", r"\byears? (ago|later)\b", r"\banniversary", r"\brewatch",
    r"\bforgotten\b", r"\bthrowback", r"retrospective", r"\bpodcast", r"\bdocumentary",
]
BLOCK_ALL_TEXT = [   # في الملخص: عبارات شخصية صريحة فقط
    r"\bis pregnant\b", r"\bgave birth\b", r"\bgives birth\b", r"\bpregnancy\b",
    r"\btheir (first|second) child\b", r"\b(her|his) marriage\b", r"\bgot married\b",
    r"\bannounced (her|his|their) (marriage|engagement)\b",
]

# ---------- 4) أخبار تافهة/مش مهمة (على العنوان) ----------
# كل مجموعة تقدر تعطّلها من config.json ("noise_groups": {"rumors": false})
NOISE_GROUPS = {
    "rumors": [      # إشاعات وتكهنات
        r"\brumou?rs?\b", r"\bleak(s|ed|ing)?\b", r"\binsider(s)?\b", r"\bspeculation\b",
        r"\bwe (think|believe|heard|guess)\b", r"\bmight (be|come)\b", r"\bcould (be|come)\b",
        r"\breportedly\b", r"\bsupposedly\b", r"\ballegedly\b", r"\bif true\b",
    ],
    "opinions": [    # رأي وتحليل
        r"\bopinion\b", r"\beditorial\b", r"\bhot take\b", r"\bwe think\b", r"\bcolumn\b",
        r"\blook(s|ing)? back\b", r"\bthrowback\b", r"\bforeshadow", r"\bfan theor",
        r"\bdeep dive\b", r"\bexplained\b", r"\bbreakdown\b",
    ],
    "questions": [   # أسئلة وقوائم
        r"^(should|will|could|would|can|is|are|did|does|do|why|how|what|who|when|where)\b.*\?$",
        r"\bhow dare\b", r"\byou (need|should|must) to (play|watch|read|buy)\b", r"\bwe ranked\b",
        r"\bmy (favorites?|top \d+)\b", r"\b\d+ (things|reasons|ways)\b",
    ],
    "promos": [      # محتوى ترويجي
        r"\bgiveaway\b", r"\bgive away\b", r"\bcontest\b", r"\bsponsored\b", r"\badvertorial\b",
        r"\bfree to play\b", r"\bsteam sale\b", r"\bbundle deal\b", r"\bearly bird\b",
    ],
    "spoilers": [    # حرق
        r"\bspoiler", r"\bplot twist\b", r"\bending explained\b", r"\bfinale explained\b",
    ],
    "personal": [    # مقالات شخصية ومعلومات ما تهم
        r"^I\b", r"\bI (still )?(haven't|have not|didn't|don't|couldn't|wouldn't|can't)\b",
        r"\bI 've (been|had|played|bought|watched)\b", r"\b\d+ years? after\b",
        r"\bex-(developer|designer|producer|artist|editor)\b",
        r"\bformer (developer|designer|producer|artist)\b",
        r"\bin (depth|detail)\b", r"\bthings to know\b", r"\bthings we learned\b",
        r"\bquick facts\b", r"\bquiz\b", r"\btrivia\b",
    ],
}
NOISE_TEXT = [
    r"\bclick here\b", r"\bsubscribe\b", r"\bsign up\b", r"\bnewsletter\b",
    r"\bpatreon\b", r"\baffiliate\b", r"\bdiscount code\b",
]

# عنوان فيلم يبدأ باسم شخص ثم فعل أو 's  (مثل: Tom Cruise Confirms) = خبر ممثل = ممنوع
_NAME = r"[A-Z][A-Za-z'’\-]+(?: [A-Z]\.)? [A-Z][A-Za-z'’\-]+(?: (?:Jr|Sr|II|III|IV)\.?)?"
PERSON_FIRST = re.compile(
    r"^(" + _NAME + r")(?:['’]s\b|\s+(?i:on|says|said|reveals|talks|joins|stars|plays|responds|explains|"
    r"admits|opens|teases|confirms|reacts|addresses|shares|wants|hopes|lands|is|was|has|gets|gives|"
    r"takes|returns|will|may|might|reportedly|set)\b)"
)
STUDIO_FIRST = {
    "marvel", "disney", "pixar", "warner", "netflix", "universal", "sony", "paramount", "lionsgate",
    "a24", "hbo", "apple", "amazon", "legendary", "lucasfilm", "dc", "nintendo", "crunchyroll", "toho",
    "ghibli", "studio", "box", "hollywood", "oscars", "oscar", "cannes", "sundance", "venice", "toronto",
}

# ---------- 2) الجانبي: لازم عمل مشهور ----------
FAMOUS = [
    # ألعاب
    "zelda", "mario", "pokemon", "pokémon", "elden ring", "dark souls", "sekiro", "bloodborne", "gta",
    "grand theft auto", "red dead", "call of duty", "fortnite", "minecraft", "hollow knight", "silksong",
    "baldur's gate", "cyberpunk", "witcher", "final fantasy", "resident evil", "street fighter",
    "mortal kombat", "god of war", "the last of us", "uncharted", "assassin's creed", "battlefield",
    "apex legends", "valorant", "league of legends", "genshin", "honkai", "persona", "metal gear",
    "kingdom hearts", "dragon quest", "monster hunter", "smash bros", "animal crossing", "splatoon",
    "stardew valley", "hades", "celeste", "undertale", "among us", "roblox", "fifa", "ea sports fc",
    "nba 2k", "madden", "halo", "forza", "gran turismo", "the sims", "civilization", "starfield",
    "fallout", "skyrim", "doom", "bioshock", "destiny", "overwatch", "diablo", "world of warcraft",
    "borderlands", "death stranding", "horizon", "ratchet", "sonic", "crash bandicoot", "tekken",
    "silent hill", "palworld", "helldivers", "black myth", "clair obscur", "expedition 33", "ghost of",
    "marvel's spider-man", "the legend of zelda", "metroid", "kirby", "donkey kong", "fire emblem",
    # أنمي ومانجا
    "one piece", "naruto", "boruto", "bleach", "dragon ball", "attack on titan", "demon slayer",
    "kimetsu", "jujutsu kaisen", "my hero academia", "chainsaw man", "spy x family", "frieren",
    "solo leveling", "death note", "fullmetal alchemist", "hunter x hunter", "fairy tail", "black clover",
    "tokyo ghoul", "sword art online", "one punch man", "mob psycho", "dr. stone", "haikyuu", "blue lock",
    "kaiju no. 8", "dandadan", "oshi no ko", "vinland saga", "berserk", "vagabond", "slam dunk",
    "evangelion", "gundam", "cowboy bebop", "steins;gate", "re:zero", "konosuba", "overlord",
    "mushoku tensei", "bocchi", "edgerunners", "studio ghibli", "ghibli", "detective conan", "doraemon",
    "jojo", "tokyo revengers", "my happy marriage", "apothecary diaries", "kaiju", "gintama", "monster",
    # أفلام
    "marvel", "avengers", "spider-man", "batman", "superman", "joker", "star wars", "avatar", "dune",
    "harry potter", "lord of the rings", "the hobbit", "the matrix", "john wick", "mission: impossible",
    "mission impossible", "fast & furious", "fast and furious", "jurassic", "transformers", "toy story",
    "frozen", "pixar", "oppenheimer", "barbie", "godzilla", "king kong", "alien", "terminator", "rocky",
    "james bond", "indiana jones", "top gun", "deadpool", "x-men", "fantastic four", "thunderbolts",
    "black panther", "guardians of the galaxy", "wicked", "sinners", "weapons", "wonder woman",
    "mortal kombat", "the odyssey", "michael",
]
FAMOUS_RE = re.compile(r"(?<![\w])(" + "|".join(re.escape(f) for f in FAMOUS) + r")(?![\w])")

# علامات شهرة: أرقام كبيرة وجوائز وتقييمات
POPULARITY = [
    r"\bmillion\b", r"\bbillion\b", r"best-?sell", r"\b#1\b", r"number one",
    r"most[- ](popular|watched|played|anticipated)", r"top[- ]selling", r"award[- ]winning",
    r"game of the year", r"\bgoty\b", r"\boscars?\b", r"academy award", r"golden globe",
    r"\bcannes\b", r"metacritic", r"opencritic", r"rotten tomatoes", r"steam charts", r"\bbox office\b",
]
# عنوان الخبر الجانبي: تقييم / مديح / أفضل / مبيعات
SIDE_TITLE = [
    r"\breviews?\b", r"\bmetacritic", r"opencritic", r"rotten tomatoes", r"\bscores?\b", r"\bratings?\b",
    r"\bacclaim", r"\bpraise", r"\braves?\b", r"\bmasterpiece", r"\bhailed\b", r"highest[- ]rated",
    r"best[- ]reviewed", r"top[- ]rated", r"perfect score", r"\b10/10\b", r"overwhelmingly positive",
    r"review[- ]bomb", r"\bmixed\b", r"\bnegative\b", r"\bbest\b", r"\bgreatest\b",
    r"\bgame of the year\b", r"\bgoty\b", r"\bof the year\b", r"\branked\b", r"\bbox office\b",
    r"\bsells?\b", r"\bsales\b", r"million (copies|players|units|views)", r"record[- ]breaking",
]
BEST_OF_YEAR = [   # قوائم "أفضل ... هذي السنة" تعتبر مشهورة بحد ذاتها
    r"\bbest\b.*\b(of (the )?year|of 20\d\d|this year|so far)\b", r"\bgame of the year\b", r"\bgoty\b",
    r"\b(best|top|greatest)\b.*\b20\d\d\b",
]
# للمصادر اللي كلها مراجعات: نقبل بس المراجعات الإيجابية
PRAISE_PATTERNS = [
    r"\bacclaim", r"\bpraise", r"\braves?\b", r"\bmasterpiece", r"best[- ]reviewed", r"highest[- ]rated",
    r"top[- ]rated", r"perfect score", r"\b10/10\b", r"overwhelmingly positive", r"very positive",
    r"\bbrilliant", r"\bstunning", r"\bexcellent", r"\bfantastic", r"\bsuperb", r"\btriumph",
    r"must[- ](play|watch|read)", r"game of the year", r"\bgoty\b", r"fan[- ]favou?rite", r"\bbeloved\b",
    r"\bhailed\b", r"\bgreat\b", r"\bamazing\b", r"\bessential\b",
]

# ---------- 1) الأساسي: الأحداث اللي ما نبي تفوتنا (على العنوان) ----------
ESSENTIAL_GAMES = [
    r"release date", r"\breleases?\b", r"\blaunch(es|ed|ing)?\b", r"\bout now\b", r"\bavailable now\b",
    r"\bnow (live|available)\b", r"\bcoming to\b", r"\barrives?\b", r"\bannounc", r"\bre(veal|vealed|veals)\b",
    r"\bunveil", r"\bnew game\b", r"\bsequel\b", r"\bremake\b", r"\bremaster", r"\bdlc\b", r"\bexpansion",
    r"\bupdate\b", r"\bpatch\b", r"\bseason \d", r"\btrailer", r"\bgameplay", r"\bteaser", r"\bdemo\b",
    r"\bbeta\b", r"early access", r"\bplaytest", r"\bdirect\b", r"state of play", r"\bshowcase",
    r"\bdelay", r"\bcancel", r"\bkickstarter", r"\bsteam page\b",
]
ESSENTIAL_ANIME = [
    r"\bannounc", r"\breveal", r"\bseasons?\b", r"\bsequel", r"\bnew anime\b",
    r"\banime\b.*\b(gets|reveals|announces|premieres|debuts|returns|ends|delayed|confirmed)\b",
    r"\bgets?\b.*\b(anime|season|film|movie|ova)\b", r"\bpremiere", r"\btrailer", r"\bteaser", r"\bpv\b",
    r"\bvisual\b", r"release date", r"\bto (premiere|debut|air|launch|return|stream|release)\b", r"\bdelay",
    r"\bfilm\b", r"\bova\b", r"\bona\b", r"\breturns?\b", r"\bfinale\b", r"\bnow streaming\b",
    r"\bgreenlit\b", r"\bconfirm",
]
ESSENTIAL_MANGA = [
    r"\bannounc", r"\breveal", r"\bnew manga\b", r"\bnew series\b", r"\bsequel", r"spin-?off", r"\breturns?\b",
    r"\bresum", r"\bhiatus", r"\bfinal (chapter|volume|arc)\b", r"\bends\b", r"\bending\b",
    r"\bgets?\b.*\b(anime|adaptation|sequel|spin-?off)\b", r"\bserializ", r"\blaunch",
    r"\bnew (chapter|volume|arc)\b", r"\bvolume \d+", r"\brelease", r"\bcomplete", r"\bconclude",
    r"\bcancel", r"\bdelay",
]
ESSENTIAL_MOVIES = [
    r"\btrailer", r"\bteaser", r"release date", r"sets? release", r"\bannounc", r"\bgreenlit",
    r"\bsequel", r"\bprequel", r"\breboot", r"\bremake", r"first look", r"\bposter", r"in theaters",
    r"\bpremieres?\b", r"\bcoming to\b", r"\bdated\b", r"\breleases?\b", r"now streaming", r"\bunveil",
    r"\bnow (on|streaming|playing|available)\b", r"\bin (cinemas|theaters|theatres)\b",
    r"\bpicked? up\b", r"\bin production\b", r"\brenewed\b", r"\bfootage\b",
    r"\bpost-?credits?\b", r"\bstreaming\b", r"\bnow on\b",
    r"\bnew (film|movie|series)\b",
]

# ---------- الأقسام ----------
# spec لكل مصدر: "trusted" = المصدر كله مواضيع القسم (ما نطلب كلمات الموضوع)
#                "require_any" = شرط خاص بالمصدر
CATEGORIES = [
    {
        "name": "أخبار المانجا", "key": "manga", "channel": MANGA_CHANNEL_ID, "color": 0xE67E22,
        "feeds": [
            "https://www.animenewsnetwork.com/all/rss.xml",
            "https://otakuusamagazine.com/feed/",
            "https://animecorner.me/feed/",
        ],
        "topic": [r"\bmanga\b", r"manhwa", r"manhua", r"light novel", r"webtoon"],
        "essential": ESSENTIAL_MANGA,
        "block": [r"\blive-?action\b", r"\bbox office\b", r"\bhollywood\b", r"\bnetflix\b",
                  r"\bkickstarter", r"\bcrowdfund"],
    },
    {
        "name": "أخبار الأنمي", "key": "anime", "channel": ANIME_CHANNEL_ID, "color": 0xFF6B9D,
        "feeds": [
            "https://www.animenewsnetwork.com/all/rss.xml",
            "https://myanimelist.net/rss/news.xml",
            {"url": "https://animecorner.me/feed/", "trusted": True},
        ],
        "topic": [r"\banime\b", r"\bova\b", r"\bona\b", r"crunchyroll", r"ghibli"],
        "essential": ESSENTIAL_ANIME,
        "block": [r"\blive-?action\b", r"\bhollywood\b", r"\bnintendo\b", r"\bswitch 2?\b", r"\bplaystation\b",
                  r"\bxbox\b", r"\bsteam\b", r"\bps[45]\b", r"\bzelda\b", r"\bkickstarter", r"\bcrowdfund"],
    },
    {
        "name": "أخبار الألعاب", "key": "games", "channel": GAMES_CHANNEL_ID, "color": 0x5865F2,
        "feeds": [
            {"url": "https://www.gamespot.com/feeds/news/", "trusted": True},
            {"url": "https://www.eurogamer.net/feed/news", "trusted": True},
            # ألعاب مستقلة (Indie): لازم الخبر يذكر indie أو demo أو early access...
            {"url": "https://www.rockpapershotgun.com/feed/news", "trusted": True,
             "require_any": [r"\bindie\b", r"early access", r"\bdemo\b", r"kickstarter", r"next fest",
                             r"solo dev", r"small studio", r"\bwishlist", r"debut game", r"first game"]},
            {"url": "https://indiegamesplus.com/feed", "trusted": True},
            # مراجعات: نقبل بس اللي وصفها إيجابي
            {"url": "https://www.gamespot.com/feeds/reviews/", "trusted": True, "require_any": PRAISE_PATTERNS},
            {"url": "https://www.eurogamer.net/feed/reviews", "trusted": True, "require_any": PRAISE_PATTERNS},
            "https://www.polygon.com/rss/index.xml",
            "https://www.pcgamer.com/rss/",
        ],
        "topic": [r"\bgames?\b", r"\bgaming\b", r"playstation", r"\bps[45]\b", r"xbox", r"nintendo",
                  r"\bswitch\b", r"\bsteam\b", r"\bdlc\b", r"\bconsoles?\b"],
        "essential": ESSENTIAL_GAMES,
        "block": [r"\bfilms?\b", r"\bmovies?\b", r"\btv\b", r"\bnovels?\b", r"\bbooks?\b", r"\bnetflix\b",
                  r"\bhbo\b", r"\bdisney\+?", r"\bepisodes?\b", r"\bsitcom", r"\bcomics?\b",
                  r"\bbox office\b", r"\bhollywood\b"],
    },
    {
        "name": "أخبار الأفلام", "key": "movies", "channel": MOVIES_CHANNEL_ID, "color": 0xE74C3C,
        "feeds": [
            # مصادر الأفلام: كلها عن أفلام، فما نطلب كلمة "movie" في العنوان
            {"url": "https://www.theplaylist.net/feed/", "trusted": True},
            {"url": "https://www.slashfilm.com/feed/", "trusted": True},
            {"url": "https://deadline.com/feed/", "trusted": True},
            {"url": "https://variety.com/v/film/feed/", "trusted": True},
            {"url": "https://collider.com/feed/", "trusted": True},
        ],
        "topic": [r"\bfilms?\b", r"\bmovies?\b"],
        "essential": ESSENTIAL_MOVIES,
        "block_person_first": True,
        "block": [r"\bvideo games?\b", r"\banime\b", r"\bmanga\b", r"\bseason \d", r"\bepisodes?\b",
                  r"\bshowrunner", r"\bsitcom", r"tv series", r"tv show", r"series finale", r"\bconcert",
                  r"\bsong\b", r"\balbum", r"\bpredictions?\b", r"\bcontenders\b",
                  r"\bcast(s|ing)?\b", r"\bvoice cast\b"],
    },
    {
        "name": "أخبار الموسيقى", "key": "music", "channel": MUSIC_CHANNEL_ID, "color": 0x1ABC9C,
        "feeds": ["https://www.billboard.com/feed/", "https://pitchfork.com/feed/feed-news/rss"],
        "topic": None,
        "essential": [r"\balbum\b", r"\bnew song\b", r"\bsingle\b", r"release date", r"\bannounc"],
        "block": [],
    },
]

SEEN_FILE = "seen.json"
SCAN_ENTRIES = 30
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


# =====================================================================
#   إعدادات المستخدم (config.json) + قائمة الحظر (blocklist.json)
#   تقدر تعدّلهم من GitHub بدون ما تلمس الكود
# =====================================================================
CONFIG_FILE = "config.json"
BLOCKLIST_FILE = "blocklist.json"

DEFAULT_CONFIG = {
    "blocked_domains": [],       # نطاقات ما تنزل منها أي خبر (مثال: "example.com")
    "blocked_words": [],         # كلمات تحجب الخبر (مثال: "gacha")
    "blocked_titles": [],        # عناوين كاملة تتحجب
    "block_reactions": ["❌", "👎", "🚫"],   # أي تفاعل يعلّم الخبر كـ"غير مرغوب"
    "delete_blocked": True,      # نحذف الخبر من القناة لما يعلّمه تفاعل
    "max_total_per_run": 8,      # أقصى عدد أخبار في تشغيل واحد (كل القنوات)
    "llm_check": False,          # نستعمل Claude للحكم على الأهمية (يحتاج ANTHROPIC_API_KEY)
    "llm_model": "claude-sonnet-5",
    "noise_patterns": [],        # قواعد إضافية تكتبها بنفسك (regex)
}


def load_json_file(path, default):
    """يقرأ ملف JSON من مجلد البوت (وإن ما موجود يرجّع الافتراضي)."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("Could not read", path, "- using defaults:", e)
        return default


def save_json_file(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


CONFIG = dict(DEFAULT_CONFIG)
_cfg_file = load_json_file(CONFIG_FILE, None)
if isinstance(_cfg_file, dict):
    CONFIG.update(_cfg_file)

BLOCKLIST = load_json_file(BLOCKLIST_FILE, None) or {}
BLOCKLIST.setdefault("links", [])
BLOCKLIST.setdefault("titles", [])
BLOCKLIST.setdefault("words", [])
BLOCKLIST.setdefault("history", [])

BLOCK_TITLES = {norm_title(t) for t in BLOCKLIST["titles"] if t}
BLOCK_WORDS = [str(w).strip().lower() for w in BLOCKLIST["words"] if str(w).strip()]
BLOCK_LINKS = {normalize_url(l) for l in BLOCKLIST["links"] if l}

CFG_BLOCKED_WORDS = [str(w).strip().lower() for w in CONFIG.get("blocked_words", []) if str(w).strip()]
CFG_BLOCKED_TITLES = {norm_title(str(t)) for t in CONFIG.get("blocked_titles", []) if str(t).strip()}
CFG_BLOCKED_DOMAINS = [
    str(d).strip().lower().lstrip(".") for d in CONFIG.get("blocked_domains", []) if str(d).strip()
]
BLOCK_REACTIONS = [str(r) for r in CONFIG.get("block_reactions", []) if str(r).strip()]
DELETE_BLOCKED = bool(CONFIG.get("delete_blocked", True))
NOISE_EXTRA = [str(p) for p in CONFIG.get("noise_patterns", []) if str(p).strip()]

# مجموعات القواعد التافهة: تقدر تعطّل أي مجموعة من config.json
NOISE_ENABLED = {k: True for k in NOISE_GROUPS}
for _group, _on in (CONFIG.get("noise_groups") or {}).items():
    if _group in NOISE_ENABLED:
        NOISE_ENABLED[_group] = bool(_on)
NOISE_TITLE = [
    p for group, pats in NOISE_GROUPS.items() if NOISE_ENABLED.get(group, True) for p in pats
]
print("Noise groups:", ", ".join("%s=%s" % (k, "on" if v else "off") for k, v in NOISE_ENABLED.items()))

# قواعد إضافية لكل قسم (مثال: {"movies": ["\\bbox office\\b"]})
CATEGORY_EXTRA_ESSENTIAL = CONFIG.get("category_essential") or {}
MAX_TOTAL_PER_RUN = _cfg_int("MAX_TOTAL_PER_RUN", int(CONFIG.get("max_total_per_run", 8) or 8))
# أقصى عدد أخبار لكل قناة في التشغيل الواحد (يعني القناة ما تاكل الحصة كلها)
MAX_PER_CATEGORY_PER_RUN = _cfg_int(
    "MAX_PER_CATEGORY_PER_RUN", int(CONFIG.get("max_per_category_per_run", 2) or 2)
)

# تشغيل تجريبي: يطبع اللي كان بينزل بدون ما يرسل شي
DRY_RUN = os.environ.get("DRY_RUN") == "1" or bool(CONFIG.get("dry_run"))

# فحص الأهمية بالذكاء الاصطناعي (اختياري)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or ""
LLM_CHECK = os.environ.get("LLM_CHECK") == "1" or bool(CONFIG.get("llm_check"))
LLM_MODEL = str(os.environ.get("LLM_MODEL") or CONFIG.get("llm_model") or "claude-sonnet-5")
ANTHROPIC_API = "https://api.anthropic.com/v1/messages"


def load_seen():
    links, titles, rejected = {}, [], []
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):          # الصيغة القديمة
                raw_links, raw_titles = data, []
            else:
                raw_links = data.get("links", [])
                raw_titles = data.get("titles", [])
                raw_rejected = data.get("rejected", [])
            for l in raw_links:
                links[normalize_url(l)] = None
            titles = list(raw_titles)
            rejected = list(raw_rejected)
        except Exception as e:
            print("Could not read seen.json, starting fresh:", e)
    return links, titles, rejected


def save_seen():
    tmp = SEEN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            {"links": list(seen_links), "titles": seen_titles, "rejected": seen_rejected},
            f,
            ensure_ascii=False,
        )
    os.replace(tmp, SEEN_FILE)


seen_links, seen_titles, seen_rejected = load_seen()


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
    """هل هذا الخبر (أو خبر مشابه جداً له) انرسل أو اترفض من قبل؟"""
    if normalize_url(entry.get("link")) in seen_links:
        return True
    if normalize_url(entry.get("link")) in seen_rejected:
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


def unmark_seen(entry):
    """لو فشل الإرسال نرجّع الخبر للقائمة عشان ينزل في التشغيل الجاي."""
    for key in (entry.get("link"), entry.get("id")):
        if key:
            seen_links.pop(normalize_url(key), None)
    nt = norm_title(entry.get("title", ""))
    if nt and nt in seen_titles:
        seen_titles.remove(nt)
    save_seen()


def mark_rejected(entry):
    """خبر مرفوض (مش مهم): نسجّله عشان ما نتحقق منه مرة ثانية ولا ينزل."""
    link = normalize_url(entry.get("link"))
    if not link:
        return
    if link not in seen_rejected:
        seen_rejected.append(link)
        del seen_rejected[:-MAX_SEEN_LINKS]
        save_seen()


def clean_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


class TranslationUnavailable(Exception):
    """الترجمة معطلة الآن (قوقل حاظرنا مؤقتاً مثلاً)."""


last_post_at = {}      # آخر وقت نزل فيه خبر لكل قسم
relaxed_recent = {}    # أوقات آخر أخبار "مختارات" لكل قسم (نقرأها من القناة نفسها)
relaxed_run_total = 0  # كم "مختارات" نزلت في هذا التشغيل
history_ok = {}        # هل قدرنا نقرأ سجل القناة (عشان نعرف آخر وقت)
_translation_pause_until = 0.0   # إذا فشلت الترجمة نوقف الإرسال شوي بدل ما نرسل إنجليزي
TRANSLATION_PAUSE_SECONDS = 15 * 60

posted_total = 0        # كم خبر نزل في هذا التشغيل (كل الأقسام)
run_stats = {"posted": 0, "essential": 0, "side": 0, "rejected": 0, "duplicates": 0}


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


LLM_PROMPT = """أنت محرّر أخبار لقنوات ديسكورد عربية (أنمي، مانجا، ألعاب، أفلام).

راجع الخبر التالي، وأجب سؤالين:
1) هل هو خبر مهم يستاهل يعرض على متابعين هذه الأعمال؟ (مو تقييم، مو إعلان، مو إشاعة، مو رأي شخصي، مو خبر مكرر)
2) لخّصه بالعربي الفصحى المبسّط في 3-5 جمل، وعنوان قصير وواضح.

القسم: {category}
العنوان الأصلي: {title}

النص:
{body}

رد بـ JSON فقط (بدون أي كلام ثاني):
{{"keep": true أو false, "reason": "سبب قصير", "title_ar": "العنوان بالعربي", "body_ar": "الملخص بالعربي"}}"""


def llm_judge(title, body, category):
    """يستشير Claude: هل الخبر مهم؟ ويعيده مترجماً بالعربي.
    يرجع None إذا فشل الاتصال (فنكمل بالقواعد العادية)."""
    if not ANTHROPIC_API_KEY:
        return None
    prompt = LLM_PROMPT.format(
        category=category,
        title=title,
        body=trim_to_sentence(body, 1500) or "(ما فيه نص)",
    )
    try:
        r = requests.post(
            ANTHROPIC_API,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=90,
        )
        if r.status_code != 200:
            print("Claude check failed:", r.status_code, r.text[:200])
            return None
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []))
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            print("Claude check: could not read the JSON answer")
            return None
        out = json.loads(m.group(0))
        out["keep"] = bool(out.get("keep"))
        return out
    except Exception as e:
        print("Claude check error:", e)
        return None


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


def _why(why, msg):
    """يكتب سبب الرفض مرة واحدة (للتسجيل)."""
    if why is not None and not why:
        why.append(msg)


def blocked_by_user(entry, raw_title, text):
    """هل الخبر محظور حسب إعدادات المستخدم/قائمة الحظر أو لأنه تافه؟
    يرجع سبب الرفض أو None إذا الخبر مسموح."""
    link = normalize_url(entry.get("link"))
    if link and link in BLOCK_LINKS:
        return "محظور (قائمة الحظر)"
    host = ""
    if link and link.startswith("http"):
        host = urlsplit(link).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
    for d in CFG_BLOCKED_DOMAINS:
        if host and (host == d or host.endswith("." + d)):
            return "محظور (نطاق محجوب)"
    nt = norm_title(raw_title)
    if nt and (nt in CFG_BLOCKED_TITLES or nt in BLOCK_TITLES):
        return "محظور (عنوان محجوب)"
    low = raw_title.lower()
    for w in CFG_BLOCKED_WORDS + BLOCK_WORDS:
        if w and w in low:
            return "محظور (كلمة: %s)" % w
    for pat in NOISE_TITLE + NOISE_EXTRA:
        try:
            if re.search(pat, low):
                return "غير مهم (قاعدة تافهة)"
        except re.error:
            print("Invalid noise pattern, skipped:", pat)
    for pat in NOISE_TEXT:
        if re.search(pat, text):
            return "غير مهم (نص ترويجي)"
    return None


def classify(entry, cat, spec=None, why=None):
    """يصنّف الخبر: "essential" (أساسي) أو "side" (جانبي) أو None (ممنوع / ما يناسب القسم).
    why = قائمة نكتب فيها سبب الرفض عشان نعرف ليش ما نزل الخبر."""
    spec = spec or {}
    raw_title = entry.get("title", "").strip()
    title = raw_title.lower()
    summary = clean_html(entry.get("summary", "")).lower()
    text = title + " " + summary

    # 3) ممنوع نهائياً: ممثلين، حياة شخصية، مقابلات، أخبار تافهة
    for pat in BLOCK_ALL_TITLE + (cat.get("block") or []):
        if re.search(pat, title):
            _why(why, "ممنوع (قائمةblk)")
            return None
    for pat in BLOCK_ALL_TEXT:
        if re.search(pat, text):
            _why(why, "ممنوع (نص شخصي)")
            return None
    if cat.get("block_person_first"):
        mp = PERSON_FIRST.match(raw_title)
        if mp:
            name = mp.group(1).lower()
            if name.split()[0] not in STUDIO_FIRST and not FAMOUS_RE.search(name):
                _why(why, "ممنوع (خبر ممثل)")
                return None

    # 4) حظر المستخدم + الأخبار التافهة/غير المهمة
    reason = blocked_by_user(entry, raw_title, text)
    if reason:
        _why(why, reason)
        return None

    # لازم يكون عن موضوع القسم (إلا المصادر اللي كلها مواضيع القسم)
    topic = cat.get("topic")
    if topic and not spec.get("trusted") and not any(re.search(p, text) for p in topic):
        _why(why, "ما يناسب القسم")
        return None
    require_any = spec.get("require_any")
    if require_any and not any(re.search(p, text) for p in require_any):
        _why(why, "ما يحقق شرط المصدر")
        return None

    # 1) أساسي
    essential = list(cat.get("essential") or [])
    extra = CATEGORY_EXTRA_ESSENTIAL.get(cat.get("key"))
    if extra:
        essential += [str(p) for p in extra]
    if any(re.search(p, title) for p in essential):
        return "essential"

    # 2) جانبي: تقييم/أفضل/مبيعات، لعمل مشهور فقط
    if any(re.search(p, title) for p in SIDE_TITLE):
        famous = (
            FAMOUS_RE.search(text) is not None
            or any(re.search(p, text) for p in POPULARITY)
            or any(re.search(p, title) for p in BEST_OF_YEAR)
        )
        if famous:
            return "side"
    _why(why, "ما هو خبر أساسي")
    return None


def hours_since_last_post(cat):
    """كم ساعة مرت من آخر خبر في القسم (None إذا القناة فاضية)."""
    t = last_post_at.get(cat["key"])
    if t is None:
        return None
    return (discord.utils.utcnow() - t).total_seconds() / 3600


async def send_entry(cat, channel, entry, relaxed=False):
    """يجهّز الخبر (صورة + ترجمة) ويرسله. يرفع TranslationUnavailable إذا الترجمة معطلة."""
    link = entry.get("link")
    raw_title = (entry.get("title") or "").strip()
    summary = clean_html(entry.get("summary", ""))

    # 0) فحص الأهمية بالذكاء الاصطناعي (اختياري): يمنع الأخبار التافهة قبل أي شغل ثقيل
    verdict = None
    if LLM_CHECK and ANTHROPIC_API_KEY:
        verdict = await asyncio.to_thread(llm_judge, raw_title, summary, cat["name"])
        if verdict is None:
            print("[llm] the check failed - using the normal filters")
        elif not verdict.get("keep", True):
            print("[llm] skipped (not important):", raw_title[:90], "|", verdict.get("reason", ""))
            mark_rejected(entry)
            return False

    image = feed_image(entry)

    if image is None or len(summary) < 300:
        page_image, page_text = await asyncio.to_thread(fetch_page, link)
        image = image or page_image
        if len(summary) < 300 and page_text:
            summary = page_text

    if image is None and cat["key"] in ("anime", "manga"):
        look_in = raw_title + ". " + summary
        image = await asyncio.to_thread(find_anime_image, look_in, cat["key"])

    if image:
        print("Image found:", image)
    else:
        print("No image found for:", link)

    if verdict and verdict.get("title_ar"):
        title, body = verdict["title_ar"], verdict.get("body_ar", "")
    else:
        title, body = await asyncio.to_thread(
            translate_pair,
            raw_title,
            trim_to_sentence(summary, MAX_TEXT),
        )

    if DRY_RUN:
        print(f"[dry-run] would post to {cat['name']}: {title} | {link}")
        return True

    mark_seen(entry)

    description = f"**{title}**\n\n{body}\n\n[المصدر]({link})"
    embed = discord.Embed(description=description[:4000], color=cat["color"])
    embed.set_footer(text=cat["name"] + (f" · {RELAXED_TAG}" if relaxed else ""))

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
    except Exception:
        unmark_seen(entry)      # ما نجح الإرسال: نرجّع الخبر يtry في التشغيل الجاي
        raise

    now_utc = discord.utils.utcnow()
    last_post_at[cat["key"]] = now_utc
    if relaxed:
        relaxed_recent.setdefault(cat["key"], []).append(now_utc)
    await asyncio.sleep(3)
    return True


def _pause_translation():
    global _translation_pause_until
    _translation_pause_until = time.monotonic() + TRANSLATION_PAUSE_SECONDS
    print("Translation unavailable: NOT posting English news. Will retry in 15 minutes.")


async def post_category(cat):
    global relaxed_run_total, posted_total
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

    essential_posted = 0
    side_candidates = []

    # ---- 1) الأساسي: ينزل أول بأول (وما يتأخر عن الجانبي)
    for spec in cat["feeds"]:
        spec = spec if isinstance(spec, dict) else {"url": spec}
        url = spec["url"]
        feed = await asyncio.to_thread(feedparser.parse, url)
        if not feed.entries:
            print("No entries from this feed (maybe the link is wrong):", url)
            continue

        scanned = dup = unmatched = n_essential = n_side = 0
        samples = []
        for entry in feed.entries[:SCAN_ENTRIES]:
            scanned += 1
            link = entry.get("link")
            if not link or is_duplicate(entry):
                dup += 1
                run_stats["duplicates"] += 1
                continue
            why = []
            kind = classify(entry, cat, spec, why)
            if kind == "essential":
                if (essential_posted >= min(MAX_PER_CATEGORY, MAX_PER_CATEGORY_PER_RUN)
                        or posted_total >= MAX_TOTAL_PER_RUN):
                    continue   # الباقي ينتظر التشغيل الجاي (ما ينعلّم كمرسل، فما يفوت)
                try:
                    if await send_entry(cat, channel, entry):
                        essential_posted += 1
                        n_essential += 1
                        posted_total += 1
                        run_stats["posted"] += 1
                        run_stats["essential"] += 1
                except TranslationUnavailable:
                    _pause_translation()
                    return
            elif kind == "side":
                side_candidates.append(entry)
                n_side += 1
            else:
                unmatched += 1
                run_stats["rejected"] += 1
                if len(samples) < 3:
                    samples.append((entry.get("title", "")[:90], why[0] if why else ""))

        print(f"[{cat['key']}] {url} -> scanned {scanned}, already sent {dup}, "
              f"ESSENTIAL posted {n_essential}, side candidates {n_side}, rejected {unmatched}")
        for t, r in samples:
            print("      rejected e.g.:", t, "|", r)

    # ---- 2) الجانبي: بس إذا ما نزل أساسي، والقناة ساكتة ساعة، وأقصى حد 2
    if essential_posted or not side_candidates:
        return
    if not history_ok.get(cat["key"]):
        return   # ما نقدر نعرف آخر وقت نزل فيه خبر، فلا نغامر
    quiet = hours_since_last_post(cat)
    if quiet is not None and quiet * 60 < QUIET_MINUTES:
        return

    day_ago = discord.utils.utcnow() - datetime.timedelta(hours=24)
    done_today = len([t for t in relaxed_recent.get(cat["key"], []) if t > day_ago])
    allowed = min(RELAXED_MAX, RELAXED_PER_DAY - done_today,
                  RELAXED_TOTAL_PER_RUN - relaxed_run_total,
                  min(MAX_PER_CATEGORY, MAX_PER_CATEGORY_PER_RUN) - essential_posted)
    allowed = min(allowed, MAX_TOTAL_PER_RUN - posted_total)
    if allowed <= 0:
        print(f"[{cat['key']}] quiet, but the side-news limit is reached "
              f"(today {done_today}/{RELAXED_PER_DAY}, this run {relaxed_run_total}/{RELAXED_TOTAL_PER_RUN})")
        return

    shown = "no previous posts" if quiet is None else f"{quiet * 60:.0f} min"
    print(f"[{cat['key']}] no essential news, quiet for {shown} -> side news (up to {allowed})")
    side_posted = 0
    for entry in side_candidates:
        if side_posted >= allowed:
            break
        if is_duplicate(entry):
            continue
        try:
            if await send_entry(cat, channel, entry, relaxed=True):
                side_posted += 1
                relaxed_run_total += 1
                posted_total += 1
                run_stats["posted"] += 1
                run_stats["side"] += 1
                print(f"[{cat['key']}] side post:", entry.get("title", "")[:90])
        except TranslationUnavailable:
            _pause_translation()
            return


async def handle_blocked_message(msg, cat):
    """خبر علّمه المستخدم بتفاعل (❌): يتحجّب من جدار الحظر ونحذفه من القناة."""
    description = ""
    title = ""
    if msg.embeds:
        description = msg.embeds[0].description or ""
        title = description.split("\n")[0].replace("**", "").strip()
    links = re.findall(r"\]\((https?://[^)\s]+)\)", description)
    if not links:
        return False
    link = links[-1]

    if link not in BLOCKLIST["links"]:
        BLOCKLIST["links"].append(link)
    if title and title not in BLOCKLIST["titles"]:
        BLOCKLIST["titles"].append(title)
    BLOCKLIST["history"].append({
        "title": title,
        "link": link,
        "channel": cat["name"],
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    })
    del BLOCKLIST["history"][:-200]
    save_json_file(BLOCKLIST_FILE, BLOCKLIST)

    if title:
        BLOCK_TITLES.add(norm_title(title))
    BLOCK_LINKS.add(normalize_url(link))
    print(f"[{cat['key']}] blocked by reaction -> {title[:90]}")
    return True


async def preload_seen_from_channels():
    """يقرأ آخر رسائل البوت في القنوات ويسجّل روابط أخبارها كأنها انرسلت.
    حماية من التكرار حتى لو فشل حفظ seen.json (مهم على GitHub Actions).
    وبنفس الفرصة: أي خبر عليه تفاعل حجب من المستخدم ينحذف ويتسجل في قائمة الحظر."""
    added = 0
    to_delete = []
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
                if cat["key"] not in last_post_at:    # أول رسالة نمرّ عليها هي الأحدث
                    last_post_at[cat["key"]] = msg.created_at
                for emb in msg.embeds:
                    footer = getattr(getattr(emb, "footer", None), "text", None) or ""
                    if RELAXED_TAG in footer:
                        relaxed_recent.setdefault(cat["key"], []).append(msg.created_at)
                    for link in re.findall(r"\]\((https?://[^)\s]+)\)", emb.description or ""):
                        key = normalize_url(link)
                        if key not in seen_links:
                            seen_links[key] = None
                            added += 1
                # المستخدم علّم الخبر بتفاعل = ما يبغاه
                if BLOCK_REACTIONS:
                    marked = any(
                        str(r.emoji) in BLOCK_REACTIONS
                        for r in (getattr(msg, "reactions", None) or [])
                    )
                    if marked and await handle_blocked_message(msg, cat):
                        to_delete.append(msg)
            history_ok[cat["key"]] = True
        except discord.Forbidden:
            print("Cannot read history in", cat["name"],
                  "- give the bot 'Read Message History' for extra duplicate protection.")
        except Exception as e:
            print("Could not read history for", cat["name"], ":", e)

    if to_delete and DELETE_BLOCKED:
        for msg in to_delete:
            try:
                await msg.delete()
                print("Deleted a blocked message")
            except discord.Forbidden:
                print("Missing 'Manage Messages' permission - cannot delete blocked messages.")
            except Exception as e:
                print("Could not delete the blocked message:", e)
    print("Loaded", added, "links from recent channel messages")


async def run_all_categories():
    global relaxed_run_total, posted_total
    relaxed_run_total = 0
    posted_total = 0
    for key in run_stats:
        run_stats[key] = 0
    for cat in CATEGORIES:
        try:
            await post_category(cat)
        except Exception as e:
            print("Error in category", cat["name"], ":", e)
    summary = {
        "posted": run_stats["posted"],
        "essential": run_stats["essential"],
        "side": run_stats["side"],
        "rejected": run_stats["rejected"],
        "duplicates": run_stats["duplicates"],
        "dry_run": DRY_RUN,
        "llm_check": LLM_CHECK,
        "version": BOT_VERSION,
    }
    print("===SUMMARY===", json.dumps(summary, ensure_ascii=False))


@tasks.loop(minutes=CHECK_EVERY_MINUTES)
async def news_loop():
    await run_all_categories()


_ready_done = False


@bot.event
async def on_ready():
    global _ready_done
    print("Bot started:", bot.user, "| version:", BOT_VERSION)
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
