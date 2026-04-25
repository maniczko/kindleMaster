"""
Kindle-oriented EPUB semantic cleanup.

Runs as a post-processing pass over generated EPUB files:
- joins PDF-broken paragraphs,
- removes running headers / page junk,
- promotes simple heading paragraphs,
- converts image wrappers to semantic figure/figcaption,
- adds direct exercise <-> solution navigation,
- rewrites Kindle-friendly CSS,
- regenerates nav.xhtml and toc.ncx,
- normalizes OPF metadata for the final EPUB package.
"""

from __future__ import annotations

import html
import io
import re
import tempfile
import unicodedata
import uuid
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag
from lxml import etree

try:
    from ftfy import fix_text as _ftfy_fix_text
except Exception:  # pragma: no cover - optional dependency
    _ftfy_fix_text = None

try:
    from wordfreq import zipf_frequency as _zipf_frequency
except Exception:  # pragma: no cover - optional dependency
    _zipf_frequency = None

try:
    from PIL import Image as _PILImage
except Exception:  # pragma: no cover - optional dependency
    _PILImage = None


XHTML_NS = "http://www.w3.org/1999/xhtml"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
NS = {
    "opf": OPF_NS,
    "dc": DC_NS,
    "ncx": NCX_NS,
    "container": CONTAINER_NS,
}

PAGE_TITLE_RE = re.compile(r"^Strona \d+$", re.IGNORECASE)
PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")
SOLUTION_PAGE_RE = re.compile(r"Solutions page \d+", re.IGNORECASE)
TRUE_SOLUTION_ENTRY_RE = re.compile(r"^(?P<num>\d+)\.\s+.+\s[–-]\s.+$")
MERGED_SOLUTION_ENTRY_RE = re.compile(
    r"^(?P<num>\d+)\.\s+(?P<title>.+?(?:19|20)\d{2})\s+(?P<body>\d+\.(?:\.\.)?\s*.+)$"
)
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
MEMBER_COPY_RE = re.compile(
    r"Complimentary\s+IIBA(?:Â®|®)?\s+Member\s+Copy\.\s+Not\s+for\s+Distribution\s+or\s+Resale\.?",
    re.IGNORECASE,
)
GAME_CAPTION_RE = re.compile(
    r"^[A-Z].{2,}\s[\u2013-]\s.+(?:18|19|20)\d{2}$"
)
GAME_TITLE_FRAGMENT_RE = re.compile(
    r"([A-Z][A-Za-z.'\u2019/\-]*(?:\s+[A-Z][A-Za-z.'\u2019/\-]*){0,3}\s+[–-]\s+[A-Z][A-Za-z.'\u2019/\-]*(?:\s+[A-Z][A-Za-z.'\u2019/\-]*){0,3})"
)
PLACEHOLDER_TITLE_RE = re.compile(r"^(?:[0-9a-f]{16,}|untitled|unknown)$", re.IGNORECASE)
PLACEHOLDER_IDENTIFIER_RE = re.compile(r"^(?:id|uid|uuid|bookid|publication-id|unknown|untitled)$", re.IGNORECASE)
COPYRIGHT_AUTHOR_RE = re.compile(
    r"Copyright\s+©\s+\d{4}\s+(?P<authors>[A-Z][A-Za-z .'\u2019-]+(?:\s+[A-Z][A-Za-z .'\u2019-]+)+)"
)
AUTHOR_LINE_RE = re.compile(r"^(?:by|author|autor|autorka)\s*[:\-]?\s+(?P<author>.+)$", re.IGNORECASE)
SOLUTION_PAGE_HEADING_RE = re.compile(r"solutions page \d+", re.IGNORECASE)
NOTATION_TOKEN_RE = re.compile(
    r"\b(?:\d+\.(?:\.\.)?|O-O(?:-O)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|[a-h]x?[a-h]?[1-8](?:=[QRBN])?[+#]?|1-0|0-1|1/2-1/2|½-½|0\.5-0\.5|\*)\b"
)
INLINE_EVAL_RE = re.compile(r"(\+=|=\+|\u00b1|\u2213|\+\u2013|\u2013\+)(?=[A-Za-z(])")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“])")
INDEX_ENTRY_RE = re.compile(
    r"(?P<label>[A-Z][A-Za-z0-9&®/\-–—()'., ]{2,}?)\s+(?P<pages>\d{1,4}(?:,\s*\d{1,4}){0,6})(?=\s+[A-Z]|$)"
)
DEFINITION_INLINE_RE = re.compile(
    r"(?P<term>[A-Z][A-Za-z0-9/\- ]{2,40})\s*(?:—|–|-|:)\s*(?P<desc>[^;]{12,})(?:;|$)"
)
QUOTE_LIKE_RE = re.compile(r"[\"“”‘’].+[\"“”‘’]")
UNSAFE_INLINE_TAG_RE = re.compile(
    r"<(?!/?(?:a|abbr|b|bdi|bdo|br|cite|code|data|del|dfn|em|i|img|ins|kbd|mark|q|ruby|s|samp|small|span|strong|sub|sup|time|u|var|wbr)\b|!--)",
    re.IGNORECASE,
)
VARIATION_START_RE = re.compile(
    r"(?:(?:Or\s+)?\d+\.(?:\.\.)?|[a-z]\)\s*\d+\.(?:\.\.)?)",
    re.IGNORECASE,
)
MOJIBAKE_MAP = {
    "BABOKŽ": "BABOK®",
    "IIBAŽ": "IIBA®",
    "â€™": "’",
    "â€“": "–",
    "â€”": "—",
    "â€˜": "‘",
    "â€œ": "“",
    "â€¦": "…",
    "â€ˇ": "‡",
    "âś“": "✓",
    "Â ": " ",
}
MOJIBAKE_MAP = {
    key: value
    for key, value in MOJIBAKE_MAP.items()
    if not (key.endswith("Ĺ˝") and len(key) <= 8 and value.endswith("Â®"))
}
MOJIBAKE_MAP = {
    key: value
    for key, value in MOJIBAKE_MAP.items()
    if not re.fullmatch(r"[A-Za-z0-9]{3,}(?:\u0139\u02dd|\u017d)", key)
}
MOJIBAKE_MAP.update(
    {
        "Â®": "®",
        "Â©": "©",
        "Â·": "·",
        "â€”": "—",
        "â€“": "–",
        "â€ś": "\"",
        "â€ť": "\"",
        "â€": "'",
        "â€™": "'",
    }
)
MOJIBAKE_MAP.update(
    {
        "â€“": "\u2013",
        "â€”": "\u2014",
        "â€˜": "\u2018",
        "â€™": "\u2019",
        "â€œ": "\u201c",
        "â€": "\u201d",
        "â“": "\u2213",
        "Â½": "\u00bd",
        "Â˝": "\u00bd",
        "Â±": "\u00b1",
    }
)
MERGED_TOKEN_FIXES = {
    "unclearis": "unclear is",
    "theonly": "the only",
    "beaway": "be a way",
    "ofe3": "of e3",
    "pinnedandis": "pinned and is",
}
PROMOTION_SPACING_RE = re.compile(r"=\s+([QRBN])\b")
TERMINAL_MATE_RE = re.compile(r"(?<=[KQRBNa-h0-9+#])\s+mate\b(?!\s+in\b)", re.IGNORECASE)
CHECKMARK_RE = re.compile(r"[✓\u2713]")
DAGGER_RE = re.compile(r"[†‡\u2020\u2021]")

REGISTERED_SUFFIX_MOJIBAKE_RE = re.compile("(?P<word>[A-Za-z0-9][A-Za-z0-9&+._/-]{1,})(?:\u0139\u02dd|\u017d)(?=\\W|$)")

PRESERVE_FIRST_REPEAT_PATTERNS = (
    re.compile(r"^Solutions to\b", re.IGNORECASE),
    re.compile(r"^(Easy|Intermediate|Advanced) Exercises\b", re.IGNORECASE),
    re.compile(r"^(Part|Chapter|Appendix)\b", re.IGNORECASE),
    re.compile(r"^(Name|Opening) Index\b", re.IGNORECASE),
)
BAD_HEADING_TERMS = (
    "copyright",
    "all rights reserved",
    "first edition",
    "paperback isbn",
    "hardcover isbn",
    "phone",
    "distributed",
    "printed",
    "published",
    "isbn",
    "typeset by",
    "proofreading by",
    "edited by",
    "cover design",
    "picture credit",
    "author photo",
    "photo on page",
    "photos on pages",
    "central chambers",
    "sunrise handicrafts",
    "material sponsorowany",
    "materiał sponsorowany",
    "advertorial",
    "reklama",
    "www.",
    "@",
)
MINOR_HEADING_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
TOC_HEADING_PATTERNS = (
    re.compile(
        r"^(front cover|title|copyright|contents|key to symbols used|quick start guide|sharing the method with others|a final session|general introduction|summary of tactical motifs|the exercises|world champions|resources)$",
        re.IGNORECASE,
    ),
    re.compile(r"^\d+\.\s+(easy|intermediate|advanced) exercises$", re.IGNORECASE),
    re.compile(r"^solutions to\b", re.IGNORECASE),
    re.compile(r"^(easy|intermediate|advanced) exercises$", re.IGNORECASE),
    re.compile(r"^(name|opening) index\b", re.IGNORECASE),
    re.compile(r"^(part|chapter|appendix)\b", re.IGNORECASE),
)
LETTER_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿĀ-ž]+")
FIGURE_CAPTION_RE = re.compile(
    r"^(?:(?:Figure|Table|Diagram|Chart|Exhibit|Photo|Photos?)\s+[A-Za-z0-9.\-: ]{1,120}|(?:Rys(?:unek|\.)|Tabela|Diagram|Wykres)\s+[A-Za-z0-9.\-: ]{1,120})$",
    re.IGNORECASE,
)
ROLE_WORD_RE = re.compile(
    r"(?i)\b(?:editor|redaktor|redaktor naczelny|autor|autorka|author|designer|lead|manager|chief|director|publisher|wydawca|coach|consultant|specjalista)\b"
)
MINOR_WORDS = {
    "a", "an", "and", "as", "at", "by", "for", "if", "in", "is", "of", "on", "or",
    "the", "to", "with", "w", "z", "i", "na", "do", "od", "o", "u", "oraz", "ale",
}
LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "pl": "pl",
    "pol": "pl",
    "polish": "pl",
    "polski": "pl",
}
POLISH_LANGUAGE_MARKERS = {
    " i ",
    " w ",
    " z ",
    " na ",
    " do ",
    " jest ",
    " się ",
    " oraz ",
    " nie ",
    " dla ",
    " jako ",
    " przez ",
    " które ",
    " który ",
    " tego ",
    " została ",
    " został ",
}
ENGLISH_LANGUAGE_MARKERS = {
    " the ",
    " and ",
    " with ",
    " from ",
    " this ",
    " that ",
    " which ",
    " guide ",
    " chapter ",
    " section ",
    " introduction ",
}
MAX_SUBSECTION_NAV_PER_CHAPTER = 10
PLACEHOLDER_AUTHOR_KEYS = {
    "",
    "author",
    "creator",
    "ebook-lib",
    "ebooklib",
    "kindlemaster",
    "python-docx",
    "emvc",
    "unknown",
}
TECHNICAL_TITLE_MARKERS = (
    "emvc",
    "pdfdrive",
    "python-docx",
    "ebooklib",
    "kindlemaster",
    "technical converter",
    "generated by",
    "converted by",
)
TECHNICAL_AUTHOR_MARKERS = (
    "ebook-lib",
    "ebooklib",
    "kindlemaster",
    "python-docx",
    "technical converter",
    "generated",
    "converter",
    "producer",
    "creator",
    "openai",
    "chatgpt",
    "codex",
)
PACKAGE_PREFIXES = {
    "dcterms": "http://purl.org/dc/terms/",
}
DESCRIPTION_SKIP_KEYS = {
    "cover",
    "contents",
    "front cover",
    "spis treści",
    "spis tresci",
    "table of contents",
}
FRONTMATTER_TITLES = {
    "foreword",
    "preface",
    "introduction",
    "acknowledgments",
    "acknowledgements",
    "author's note",
    "authors note",
    "about the author",
    "od autora",
    "wstęp",
    "wstep",
    "przedmowa",
    "podziękowania",
    "podziekowania",
}
KNOWLEDGE_TOPIC_LABELS = {
    "definitions": "Definicje",
    "process": "Proces",
    "architecture": "Architektura",
    "dependencies": "Zależności systemowe",
}
KNOWLEDGE_SCHEMA_LABELS = {
    "what": "Co to jest",
    "how": "Jak działa",
    "example": "Przykład",
    "business": "Implikacje biznesowe",
}
KNOWLEDGE_TOPIC_PATTERNS = {
    "definitions": (
        re.compile(r"(?i)\b(?:definicj\w*|pojęci\w*|glosariusz|słownik pojęć|oznacza|jest to|defined as|refers to|means)\b"),
        re.compile(r"(?i)\b(?:termin|koncept|concept|term)\b"),
    ),
    "process": (
        re.compile(r"(?i)\b(?:proces\w*|workflow|pipeline|krok\w*|step\w*|najpierw|następnie|potem|dalej|wreszcie|przebiega|uruchamia|wykonuje|przetwarza)\b"),
        re.compile(r"(?i)\b(?:ingest|transform|analy[sz]e|render|convert|publish|walid\w*)\b"),
    ),
    "architecture": (
        re.compile(r"(?i)\b(?:architektur\w*|komponent\w*|warstw\w*|moduł\w*|modul\w*|serwis\w*|service\w*|adapter\w*|interfejs\w*|api|rdzeń|rdzen|engine|renderer)\b"),
        re.compile(r"(?i)\b(?:storage|cache|queue|kolejk\w*|repozytor\w*|orchestr\w*)\b"),
    ),
    "dependencies": (
        re.compile(r"(?i)\b(?:zależno\w*|zalezn\w*|integracj\w*|requires|wymaga|depends on|dependency|upstream|downstream|external system|system zewnętrzny|system zewnetrzny)\b"),
        re.compile(r"(?i)\b(?:api|baza danych|database|storage|cache|kolejk\w*|czytnik\w*|vendor\w*)\b"),
    ),
}
KNOWLEDGE_TOPIC_OPENERS = {
    "definitions": re.compile(r"(?i)^(?:definicj\w*|pojęci\w*|koncept\w*|termin\w*)\b"),
    "process": re.compile(r"(?i)^(?:proces\w*|workflow|pipeline)\b"),
    "architecture": re.compile(r"(?i)^(?:architektur\w*|komponent\w*|warstw\w*)\b"),
    "dependencies": re.compile(r"(?i)^(?:zależności systemowe|zaleznosci systemowe|zależno\w*|zalezn\w*)\b"),
}
KNOWLEDGE_SCHEMA_PATTERNS = {
    "what": (
        re.compile(r"(?i)\b(?:jest to|to jest|oznacza|defined as|refers to|means|stanowi|opisuje)\b"),
        re.compile(r"(?i)\b(?:pojęci\w*|definicj\w*|concept|term)\b"),
    ),
    "how": (
        re.compile(r"(?i)\b(?:działa|dziala|jak działa|jak dziala|polega|przebiega|workflow|pipeline|proces\w*|krok\w*|step\w*|najpierw|następnie|potem|dalej|wreszcie|wykonuje|przetwarza|analizuje|renderuje)\b"),
    ),
    "example": (
        re.compile(r"(?i)\b(?:przykład\w*|przyklad\w*|na przykład|na przyklad|np\.|for example|for instance|w praktyce)\b"),
    ),
    "business": (
        re.compile(r"(?i)\b(?:biznes\w*|implikacj\w*|wartoś\w*|wartosc\w*|korzyś\w*|korzys\w*|ryzyk\w*|koszt\w*|wpływ\w*|wplyw\w*|compliance|sla|organizac\w*|operacyjn\w*|klient\w*)\b"),
        re.compile(r"(?i)\b(?:pozwala|oznacza to|dzięki temu|dzieki temu|w efekcie|therefore|this means|as a result)\b"),
    ),
}
KNOWLEDGE_STEP_RE = re.compile(
    r"(?i)\b(?:najpierw|następnie|potem|dalej|wreszcie|po pierwsze|po drugie|po trzecie|first|second|third|then|finally)\b"
)
KNOWLEDGE_INLINE_SPLIT_RE = re.compile(r"\s*[;•]\s*")
KNOWLEDGE_STEP_SPLIT_RE = re.compile(
    r"(?i)(?=\b(?:najpierw|następnie|potem|dalej|wreszcie|po pierwsze|po drugie|po trzecie|first|second|third|then|finally)\b)"
)
SIGNATURE_DATE_RE = re.compile(
    r"^(?:\w[\w.\- ]{1,40}),\s*(?:19|20)\d{2}$",
    re.UNICODE,
)
SIGNATURE_NAME_RE = re.compile(
    r"^(?:\w[\w'’.\-]+(?:\s+\w[\w'’.\-]+){1,3})$",
    re.UNICODE,
)
SIGNATURE_META_RE = re.compile(
    r"^(?:[\w0-9][\w0-9&/'’().\-]+(?:\s+[\w0-9][\w0-9&/'’().\-]+){0,4})$",
    re.UNICODE,
)
SPLIT_JOIN_STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "if", "in", "into", "is",
    "it", "of", "on", "or", "the", "to", "up", "we", "w", "z", "i", "na", "do", "od",
    "o", "u", "że", "się", "czy", "nie", "oraz", "ale", "po", "za", "dla", "pod", "nad",
}
MAGAZINE_TOC_LINE_RE = re.compile(r"^(?P<page>\d{1,3})\.\s+(?P<title>.+)$")
MAGAZINE_SPECIAL_TITLE_RE = re.compile(
    r"(?i)^(?:galeria|reklama|materia[łl]\s+sponsorowany|material\s+sponsorowany|advertorial)\b"
)
MAGAZINE_PROMO_TEXT_RE = re.compile(
    r"(?i)\b(?:prenumerata|subskrypcja|zam[oó]w|oferta|partner|sponsorowan|reklama|kino na leżakach|kpo|dotacj)\b"
)
MAGAZINE_BYLINE_RE = re.compile(
    r"(?i)^(?:n\s+)?(?:tekst|rozmawia|autor|autorka|ilustracja|zdj[eę]cia|fot\.?|rys\.?|oprac\.?)\b"
)
MAGAZINE_SECTION_SKIP_RE = re.compile(r"(?i)^(?:spis treści|table of contents|contents)$")
MAGAZINE_FEATURE_SKIP_KEYS = {
    "galeria",
    "reklama",
    "material sponsorowany",
    "materiał sponsorowany",
    "spis treści",
}
MAGAZINE_EXTRA_TITLE_HINTS = (
    "material sponsorowany",
    "materiał sponsorowany",
    "reklama",
    "galeria",
    "prenumerata",
    "dotacji z kpo",
)
MAGAZINE_MINOR_WORDS = MINOR_WORDS | {
    "dla",
    "czy",
    "jak",
    "się",
    "sie",
    "oraz",
    "który",
    "która",
    "które",
    "którego",
    "ktory",
    "ktora",
    "ktore",
    "jego",
    "jej",
    "ich",
}

KINDLE_CSS = """\
html {
  font-size: 100%;
}

body {
  margin: 0;
  padding: 0;
  color: #111;
  background: transparent;
  font-family: serif;
  font-size: 1em;
  line-height: 1.45;
  text-align: justify;
  -webkit-hyphens: auto;
  hyphens: auto;
  orphans: 2;
  widows: 2;
}

section {
  margin: 0;
  padding: 0;
}

h1, h2, h3 {
  margin: 1.1em 0 0.45em;
  line-height: 1.22;
  font-weight: 700;
  page-break-after: avoid;
  break-after: avoid;
}

h1 { font-size: 1.55em; }
h2 { font-size: 1.28em; }
h3 { font-size: 1.08em; }

p {
  margin: 0 0 0.38em;
  text-indent: 1.2em;
}

h1 + p,
h2 + p,
h3 + p,
figcaption + p,
.solution-entry + p,
.problem-solution-link + p,
.subtitle,
.author,
.byline,
.signature,
.dateline,
.signature-meta,
blockquote p {
  text-indent: 0;
}

figure {
  margin: 1em 0 1.1em;
  text-align: center;
  break-inside: avoid;
  page-break-inside: avoid;
}

ul,
ol {
  margin: 0.5em 0 0.8em 1.2em;
  padding: 0 0 0 0.6em;
}

ul { list-style-type: disc; }
ol { list-style-type: decimal; }

li {
  margin: 0.18em 0;
  line-height: 1.22;
}

dl {
  margin: 0.55em 0 0.85em;
}

dt,
dd {
  margin: 0;
  text-indent: 0;
}

dt {
  font-weight: 700;
}

dd {
  margin: 0 0 0.4em 1.1em;
}

table {
  width: 100%;
  margin: 0.8em 0 1em;
  border-collapse: collapse;
  break-inside: avoid;
  page-break-inside: avoid;
}

th,
td {
  padding: 0.3em 0.45em;
  border: 0.05rem solid #cfc8ba;
  text-align: left;
  vertical-align: top;
}

th {
  font-weight: 700;
  background: #f4f1e8;
}

.reference-entry {
  text-indent: 0;
}

.reference-label,
.reference-links,
.reference-review-note {
  margin: 0.18em 0;
}

.reference-id {
  font-weight: 700;
  margin-right: 0.28em;
}

.reference-title {
  margin-right: 0.28em;
  font-weight: 600;
}

.reference-description {
  color: #5d564a;
}

.reference-links {
  display: block;
}

.reference-link {
  word-break: break-all;
  hyphens: none;
}

.reference-unresolved {
  color: #8a4b2f;
  font-style: italic;
}

.toc-entry {
  text-indent: 0;
}

img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 0 auto;
  page-break-inside: avoid;
  break-inside: avoid;
}

figcaption {
  margin: 0 0 0.45em;
  text-align: center;
  text-indent: 0;
  line-height: 1.28;
  font-weight: 600;
  hyphens: none;
}

.chess-problem .problem-solution-link {
  margin: 0 0 0.55em;
}

.chess-problem img {
  width: 100%;
  max-width: 28rem;
  padding: 0.18rem;
  border: 0.08rem solid #d8d2c3;
  background: #fff;
  box-sizing: border-box;
  image-rendering: -webkit-optimize-contrast;
  image-rendering: crisp-edges;
}

.exercise-number {
  color: #555;
  font-weight: 700;
}

.page-marker {
  display: block;
  width: 0;
  height: 0;
  overflow: hidden;
}

.exercise-marker {
  display: block;
  width: 0;
  height: 0;
  overflow: hidden;
  text-indent: 0;
}

.problem-solution-link,
.problem-page-link,
.diagram-tail {
  margin-top: 0.35em;
  text-indent: 0;
  text-align: center;
  font-size: 0.95em;
}

.solution-entry {
  margin-top: 1.1em;
}

.kicker,
.byline,
.author,
.subtitle,
.signature,
.dateline,
.signature-meta {
  margin-top: 0.7em;
  text-indent: 0;
  text-align: left;
  letter-spacing: 0.01em;
}

.kicker {
  text-transform: uppercase;
  font-weight: 700;
  color: #555;
}

.author {
  margin-bottom: 0.45em;
  font-size: 0.92em;
  font-weight: 700;
}

.subtitle {
  margin-top: 0.25em;
  margin-bottom: 0.55em;
  font-size: 0.98em;
  line-height: 1.34;
  color: #333;
}

.byline {
  font-size: 0.9em;
}

.dateline,
.signature,
.signature-meta {
  text-align: right;
  font-size: 0.92em;
  color: #444;
}

.signature {
  font-style: italic;
  font-weight: 600;
}

.signature-meta {
  margin-top: 0.2em;
  font-size: 0.88em;
}

.lead {
  margin-bottom: 0.55em;
  text-indent: 0;
  font-size: 1.05em;
  line-height: 1.42;
}

.knowledge-body,
.knowledge-point,
.knowledge-kicker {
  text-indent: 0;
}

.knowledge-body {
  margin-bottom: 0.6em;
  padding: 0.12em 0 0.12em 0.75em;
  border-left: 0.18rem solid #cbbda3;
}

.knowledge-example {
  border-left-color: #93a57e;
}

.knowledge-business {
  border-left-color: #b98c63;
}

.knowledge-kicker {
  margin-top: 0.8em;
}

.aside,
.sidebar {
  margin: 0.8em 1em;
  text-indent: 0;
  font-size: 0.94em;
  line-height: 1.44;
  color: #555;
  font-style: italic;
}

blockquote {
  margin: 0.9em 1.2em;
  padding: 0.2em 0 0.2em 0.9em;
  border-left: 0.24rem solid rgba(0, 0, 0, 0.2);
  text-indent: 0;
  font-style: italic;
}

blockquote p {
  text-indent: 0;
}

.figure-caption {
  text-indent: 0;
  text-align: center;
  font-size: 0.92em;
  line-height: 1.28;
}

.symbol-legend {
  margin: 1em 0 0.4em;
  padding-left: 0;
  list-style: none;
}

.symbol-legend-item {
  display: flex;
  gap: 0.8em;
  align-items: baseline;
  margin: 0.28em 0;
  text-indent: 0;
}

.notation-symbol {
  min-width: 3.2em;
  font-family: sans-serif;
  font-weight: 700;
}

.notation-label {
  flex: 1;
}

.solution-text {
  font-size: 0.98em;
}

.notation-heavy {
  font-family: sans-serif;
  font-size: 0.98em;
  line-height: 1.42;
  text-align: left;
  text-indent: 0;
  letter-spacing: 0.01em;
  word-spacing: 0.02em;
  -webkit-hyphens: none;
  hyphens: none;
}

.solution-backlink,
.problem-solution-link a,
.problem-page-link a,
.diagram-tail a {
  color: inherit;
  text-decoration: underline;
  text-underline-offset: 0.12em;
}

.cover-page img {
  max-width: 100%;
  border-radius: 0.45rem;
}

figure.photo img,
figure.illustration img,
.figure.photo img,
.figure.illustration img {
  border-radius: 0.35rem;
}

figure.technical-figure img,
.figure.technical-figure img {
  width: 100%;
  max-width: 32rem;
  border-radius: 0;
  image-rendering: -webkit-optimize-contrast;
  image-rendering: crisp-edges;
}

figure.detail-diagram img,
.figure.detail-diagram img,
figure.low-res-diagram img,
.figure.low-res-diagram img {
  width: 100%;
  max-width: 32rem;
  height: auto;
}

figure.low-res-diagram img,
.figure.low-res-diagram img {
  image-rendering: -webkit-optimize-contrast;
  image-rendering: crisp-edges;
}

figure.magazine-special,
.figure.magazine-special {
  margin: 1.1em 0;
}

figure.magazine-special img,
.figure.magazine-special img {
  width: 100%;
  max-width: 100%;
  height: auto;
  border-radius: 0.4rem;
}

nav[epub|type="landmarks"] {
  display: none;
}
"""


@dataclass
class ProcessedChapter:
    xhtml: str
    nav_entries: list[dict]
    solution_targets: dict[str, str]
    problem_refs: list[dict]
    reference_report: dict[str, int]


def finalize_epub_for_kindle(
    epub_bytes: bytes,
    *,
    title: str,
    author: str,
    language: str,
    publication_profile: str | None = None,
    return_report: bool = False,
    report_mode: str = "reference",
) -> bytes | tuple[bytes, dict[str, object]]:
    """Clean up a generated EPUB unless it is fixed-layout / pre-paginated."""
    rich_report_enabled = return_report and _wants_rich_finalize_report(report_mode)
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir)
            _extract_epub(epub_bytes, root_dir)
            opf_path = _locate_opf(root_dir)
            chapter_paths = _get_spine_xhtml_paths(opf_path)

            phase_report = (
                _initialize_finalize_phase_report(
                    title=title,
                    author=author,
                    language=language,
                    publication_profile=publication_profile,
                )
                if rich_report_enabled
                else None
            )
            metadata_before = _snapshot_package_metadata(opf_path) if rich_report_enabled else {}
            navigation_before = _inventory_navigation_document(opf_path) if rich_report_enabled else {}
            spine_before = _inventory_spine(opf_path) if rich_report_enabled else {}
            chapter_heading_inventory_before: dict[str, list[dict[str, object]]] = {}
            heading_decisions: list[dict[str, object]] = []
            manual_review_queue: list[dict[str, object]] = []
            if rich_report_enabled:
                for chapter_path in chapter_paths:
                    if chapter_path.name == "cover.xhtml":
                        continue
                    chapter_heading_inventory_before[chapter_path.name] = _collect_heading_candidates_from_path(
                        chapter_path,
                        include_pseudo=True,
                    )
                inventory_conflicts = _build_inventory_conflicts(
                    chapter_paths,
                    metadata_snapshot=metadata_before,
                    requested_title=title,
                    requested_author=author,
                    requested_language=language,
                )
                phase_report["phases"]["inventory"] = {
                    "status": "completed",
                    "package_document": _safe_relative_path(opf_path, root_dir),
                    "metadata": metadata_before,
                    "spine": spine_before,
                    "navigation": navigation_before,
                    "heading_inventory": _summarize_heading_inventory(chapter_heading_inventory_before),
                    "conflicts": inventory_conflicts,
                }
                manual_review_queue.extend(inventory_conflicts)
                phase_report["gates"]["A"] = _evaluate_inventory_gate(
                    spine_before=spine_before,
                    navigation_before=navigation_before,
                    chapter_count=len(chapter_paths),
                    pre_paginated=_is_pre_paginated(opf_path),
                )

            if _is_pre_paginated(opf_path):
                if return_report:
                    if rich_report_enabled and phase_report is not None:
                        phase_report["status"] = "skipped"
                        phase_report["summary"] = {
                            "cleanup_scope": "pre-paginated",
                            "chapter_count": len(chapter_paths),
                            "reference_cleanup": _finalize_reference_report(_empty_reference_report()),
                            "manual_review_count": len(manual_review_queue),
                        }
                        phase_report["reference_cleanup"] = _finalize_reference_report(_empty_reference_report())
                        phase_report["manual_review_queue"] = manual_review_queue
                        phase_report["gates"]["F"] = _gate_result(
                            "skipped",
                            warnings=["EPUB is pre-paginated; semantic finisher skipped."],
                        )
                        return epub_bytes, phase_report
                    return epub_bytes, _finalize_reference_report(_empty_reference_report())
                return epub_bytes

            if not chapter_paths:
                if return_report:
                    if rich_report_enabled and phase_report is not None:
                        phase_report["status"] = "skipped"
                        phase_report["summary"] = {
                            "cleanup_scope": "empty-spine",
                            "chapter_count": 0,
                            "reference_cleanup": _finalize_reference_report(_empty_reference_report()),
                            "manual_review_count": len(manual_review_queue),
                        }
                        phase_report["reference_cleanup"] = _finalize_reference_report(_empty_reference_report())
                        phase_report["manual_review_queue"] = manual_review_queue
                        phase_report["gates"]["F"] = _gate_result(
                            "fail",
                            blockers=["Package spine does not contain XHTML reading-order documents."],
                        )
                        return epub_bytes, phase_report
                    return epub_bytes, _finalize_reference_report(_empty_reference_report())
                return epub_bytes

            repeated_counts = _collect_repeated_short_texts(chapter_paths)
            keep_first_seen: set[str] = set()
            processed: dict[Path, ProcessedChapter] = {}
            solution_targets: dict[str, str] = {}
            toc_entries: list[dict] = []
            problem_refs_by_chapter: dict[str, list[dict]] = {}
            reference_report = _empty_reference_report()
            raw_author_candidate = ""
            raw_language_samples: list[str] = []
            raw_description_candidate = ""

            for chapter_path in chapter_paths:
                if chapter_path.name == "cover.xhtml":
                    _normalize_cover_page(chapter_path, title=title, language=language)
                    continue
                original_xhtml = chapter_path.read_text(encoding="utf-8")
                if not raw_author_candidate:
                    raw_author_candidate = _extract_author_from_chapters([chapter_path])
                if not raw_description_candidate:
                    raw_description_candidate = _extract_description_from_chapters(
                        [chapter_path],
                        title=title,
                        author=raw_author_candidate or author,
                    )
                if len(raw_language_samples) < 6:
                    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
                    raw_language_samples.append(_normalize_text(soup.get_text(" ", strip=True))[:800])

                chapter_result = _process_chapter(
                    chapter_path,
                    repeated_counts=repeated_counts,
                    keep_first_seen=keep_first_seen,
                    title=title,
                    author=author,
                    language=language,
                )
                processed[chapter_path] = chapter_result
                if rich_report_enabled:
                    after_heading_inventory = _collect_heading_candidates_from_text(
                        chapter_result.xhtml,
                        file_name=chapter_path.name,
                        include_pseudo=False,
                    )
                    chapter_decisions = _build_heading_decisions(
                        file_name=chapter_path.name,
                        before_candidates=chapter_heading_inventory_before.get(chapter_path.name)
                        or _collect_heading_candidates_from_text(
                            original_xhtml,
                            file_name=chapter_path.name,
                            include_pseudo=True,
                        ),
                        after_candidates=after_heading_inventory,
                        repeated_counts=repeated_counts,
                    )
                    heading_decisions.extend(chapter_decisions)
                    manual_review_queue.extend(_manual_review_from_heading_decisions(chapter_decisions))
                toc_entries.extend(chapter_result.nav_entries)
                solution_targets.update(chapter_result.solution_targets)
                _merge_reference_report(reference_report, chapter_result.reference_report)
                for ref in chapter_result.problem_refs:
                    problem_refs_by_chapter.setdefault(ref["problem_file"], []).append(ref)

            exercise_problem_targets: dict[str, str] = {}
            for problem_file, refs in problem_refs_by_chapter.items():
                for ref in refs:
                    exercise_num = ref.get("exercise_num", "")
                    if exercise_num and problem_file:
                        exercise_problem_targets.setdefault(exercise_num, f"{problem_file}#exercise-{exercise_num}")

            for chapter_path, chapter_result in processed.items():
                updated_xhtml = _inject_problem_solution_links(
                    chapter_result.xhtml,
                    chapter_name=chapter_path.name,
                    solution_targets=solution_targets,
                    ordered_problem_refs=problem_refs_by_chapter.get(chapter_path.name, []),
                )
                updated_xhtml = _rewrite_solution_backlinks(
                    updated_xhtml,
                    exercise_problem_targets=exercise_problem_targets,
                )
                chapter_path.write_text(updated_xhtml, encoding="utf-8")

            chapter_list = list(processed.keys())
            cleanup_scope = _detect_cleanup_scope(
                chapter_list,
                title=title,
                publication_profile=publication_profile,
            )
            if cleanup_scope == "training-book":
                package_overrides = _repair_training_book_package(
                    chapter_list,
                    title=title,
                    author=author,
                    language=language,
                )
            elif cleanup_scope == "magazine":
                package_overrides = _repair_magazine_package(
                    chapter_list,
                    title=title,
                    author=author,
                    language=language,
                )
            else:
                package_overrides = _repair_generic_package(
                    chapter_list,
                    title=title,
                    author=author,
                    language=language,
                    toc_entries=toc_entries,
                    cleanup_scope=cleanup_scope,
                )
            title = str(package_overrides.get("title") or title)
            author = str(package_overrides.get("author") or author)
            language = str(package_overrides.get("language") or language)
            if _is_placeholder_author(author) and raw_author_candidate:
                author = raw_author_candidate
            language = _resolve_publication_language(language, samples=raw_language_samples)
            toc_entries = list(package_overrides.get("toc_entries") or toc_entries)
            spine_order = list(package_overrides.get("spine_order") or [])
            if not spine_order:
                spine_order = [path.name for path in chapter_list if path.name != "cover.xhtml"]

            for chapter_path in chapter_list:
                if chapter_path.name == "cover.xhtml":
                    continue
                _normalize_chapter_dom_ids(chapter_path)
            _strip_unresolved_fragment_links(processed.keys())
            _audit_diagram_presentation(opf_path.parent, language=language)
            _write_default_css(root_dir)
            toc_entries = _rebuild_toc_entries_from_final_chapters(
                chapter_list,
                fallback_entries=toc_entries,
            )
            metadata_before_update = _snapshot_package_metadata(opf_path) if rich_report_enabled else {}
            _update_opf_metadata(
                opf_path,
                title=title,
                author=author,
                language=language,
                chapter_paths=chapter_list,
                toc_entries=toc_entries,
                description_seed=raw_description_candidate,
            )
            _rewrite_navigation(root_dir, opf_path, toc_entries=toc_entries, title=title, language=language)
            _synchronize_xhtml_language(opf_path.parent, language=language)
            _reorder_opf_spine(opf_path, spine_order)
            metadata_after = _snapshot_package_metadata(opf_path) if rich_report_enabled else {}
            navigation_after = _inventory_navigation_document(opf_path) if rich_report_enabled else {}
            toc_map = (
                _build_toc_map(toc_entries, chapter_paths=list(processed.keys()), package_dir=opf_path.parent)
                if rich_report_enabled
                else []
            )
            structural_integrity = (
                _collect_structural_integrity_summary(
                    opf_path,
                    root_dir=root_dir,
                    chapter_paths=list(processed.keys()),
                    toc_map=toc_map,
                )
                if rich_report_enabled
                else {}
            )
            packed_epub = _pack_epub(root_dir)
            if return_report:
                if rich_report_enabled and phase_report is not None:
                    metadata_phase = _build_metadata_phase_report(
                        before=metadata_before_update or metadata_before,
                        after=metadata_after,
                        requested_title=title,
                        requested_author=author,
                        requested_language=language,
                        chapter_paths=list(processed.keys()),
                    )
                    heading_phase = _build_heading_phase_report(
                        heading_decisions,
                        chapter_paths=list(processed.keys()),
                        package_dir=opf_path.parent,
                    )
                    toc_phase = _build_toc_phase_report(
                        before=navigation_before,
                        after=navigation_after,
                        toc_map=toc_map,
                        toc_entries=toc_entries,
                        spine_order=spine_order,
                    )
                    structural_phase = {
                        "status": "completed",
                        **structural_integrity,
                    }
                    phase_report["reference_cleanup"] = _finalize_reference_report(reference_report)
                    phase_report["phases"]["metadata_repair"] = metadata_phase
                    phase_report["phases"]["heading_recovery"] = heading_phase
                    phase_report["phases"]["toc_rebuild"] = toc_phase
                    phase_report["phases"]["structural_integrity"] = structural_phase

                    phase_report["gates"]["B"] = _evaluate_metadata_gate(metadata_after)
                    phase_report["gates"]["C"] = _evaluate_heading_gate(heading_phase)
                    phase_report["gates"]["D"] = _evaluate_toc_gate(toc_phase)
                    phase_report["gates"]["E"] = _evaluate_structural_gate(structural_integrity)
                    phase_report["gates"]["F"] = _evaluate_release_gate(
                        phase_report["gates"],
                        manual_review_queue=manual_review_queue,
                    )

                    manual_review_queue.extend(metadata_phase.get("manual_review", []))
                    manual_review_queue.extend(toc_phase.get("manual_review", []))
                    manual_review_queue.extend(structural_integrity.get("manual_review", []))
                    phase_report["manual_review_queue"] = _dedupe_manual_review_items(manual_review_queue)
                    phase_report["summary"] = {
                        "cleanup_scope": cleanup_scope,
                        "chapter_count": len(processed),
                        "toc_entry_count_before": int(navigation_before.get("entry_count", 0) or 0),
                        "toc_entry_count_after": len(toc_entries),
                        "heading_decision_count": len(heading_decisions),
                        "manual_review_count": len(phase_report["manual_review_queue"]),
                        "reference_cleanup": phase_report["reference_cleanup"],
                    }
                    phase_report["status"] = phase_report["gates"]["F"]["status"]
                    return packed_epub, phase_report
                return packed_epub, _finalize_reference_report(reference_report)
            return packed_epub
    except Exception as exc:
        print(f"Kindle semantic cleanup skipped due to error: {exc}")
        if return_report:
            if rich_report_enabled:
                failure_report = _initialize_finalize_phase_report(
                    title=title,
                    author=author,
                    language=language,
                    publication_profile=publication_profile,
                )
                failure_report["status"] = "error"
                failure_report["reference_cleanup"] = _finalize_reference_report(_empty_reference_report())
                failure_report["summary"] = {
                    "cleanup_scope": "",
                    "chapter_count": 0,
                    "reference_cleanup": failure_report["reference_cleanup"],
                    "manual_review_count": 0,
                    "error": str(exc),
                }
                failure_report["gates"]["F"] = _gate_result(
                    "fail",
                    blockers=[f"Finalizer raised an exception: {exc}"],
                )
                return epub_bytes, failure_report
            return epub_bytes, _finalize_reference_report(_empty_reference_report())
        return epub_bytes


def _extract_epub(epub_bytes: bytes, root_dir: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as archive:
        archive.extractall(root_dir)


def _locate_opf(root_dir: Path) -> Path:
    container_path = root_dir / "META-INF" / "container.xml"
    container_root = etree.parse(str(container_path)).getroot()
    rootfile = container_root.find(".//container:rootfile", NS)
    if rootfile is None:
        raise RuntimeError("EPUB container.xml does not define rootfile")
    full_path = rootfile.get("full-path")
    if not full_path:
        raise RuntimeError("EPUB rootfile path missing")
    return root_dir / full_path


def _is_pre_paginated(opf_path: Path) -> bool:
    root = etree.parse(str(opf_path)).getroot()
    for meta in root.findall(".//opf:meta", NS):
        property_name = meta.get("property") or meta.get("name") or ""
        meta_text = "".join(meta.itertext()).strip()
        if "rendition:layout" in property_name and meta_text == "pre-paginated":
            return True
    return False


def _get_spine_xhtml_paths(opf_path: Path) -> list[Path]:
    root = etree.parse(str(opf_path)).getroot()
    manifest_by_id = {}
    for item in root.findall(".//opf:manifest/opf:item", NS):
        manifest_by_id[item.get("id")] = item

    ordered_paths: list[Path] = []
    for itemref in root.findall(".//opf:spine/opf:itemref", NS):
        manifest_item = manifest_by_id.get(itemref.get("idref"))
        if manifest_item is None:
            continue
        href = manifest_item.get("href") or ""
        media_type = manifest_item.get("media-type") or ""
        if media_type != "application/xhtml+xml":
            continue
        if href.endswith("nav.xhtml"):
            continue
        ordered_paths.append((opf_path.parent / href).resolve())
    return ordered_paths


def _empty_reference_report() -> dict[str, int]:
    return {
        "sections_detected": 0,
        "records_detected": 0,
        "entries_rebuilt": 0,
        "split_record_count": 0,
        "clickable_link_count": 0,
        "repaired_link_count": 0,
        "review_entry_count": 0,
        "unresolved_fragment_count": 0,
        "numbering_issue_count": 0,
        "scope_replaced_count": 0,
    }


def _merge_reference_report(target: dict[str, int], source: dict[str, int] | None) -> None:
    if not source:
        return
    for key, value in source.items():
        target[key] = target.get(key, 0) + int(value or 0)


def _finalize_reference_report(report: dict[str, int] | None) -> dict[str, int]:
    return {key: int(value or 0) for key, value in (report or _empty_reference_report()).items()}


def _wants_rich_finalize_report(report_mode: str | None) -> bool:
    return _normalize_key(report_mode or "") in {"rich", "phase", "phases", "full", "detailed"}


def _initialize_finalize_phase_report(
    *,
    title: str,
    author: str,
    language: str,
    publication_profile: str | None,
) -> dict[str, object]:
    return {
        "report_type": "kindlemaster.finalize_epub_for_kindle.phase-report",
        "version": 1,
        "status": "pending",
        "input": {
            "title": _normalize_text(title),
            "author": _normalize_text(author),
            "language": _canonicalize_language(language),
            "publication_profile": publication_profile or "",
        },
        "reference_cleanup": _finalize_reference_report(_empty_reference_report()),
        "summary": {},
        "phases": {
            "inventory": {"status": "pending"},
            "metadata_repair": {"status": "pending"},
            "heading_recovery": {"status": "pending"},
            "toc_rebuild": {"status": "pending"},
            "structural_integrity": {"status": "pending"},
        },
        "manual_review_queue": [],
        "gates": {
            "A": _gate_result("pending"),
            "B": _gate_result("pending"),
            "C": _gate_result("pending"),
            "D": _gate_result("pending"),
            "E": _gate_result("pending"),
            "F": _gate_result("pending"),
        },
    }


def _gate_result(
    status: str,
    *,
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "blockers": list(blockers or []),
        "warnings": list(warnings or []),
        "details": dict(details or {}),
    }


def _safe_relative_path(path: Path, root_dir: Path) -> str:
    try:
        return path.relative_to(root_dir).as_posix()
    except Exception:
        return path.as_posix()


def _snapshot_package_metadata(opf_path: Path) -> dict[str, object]:
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(opf_path), parser)
    root = tree.getroot()
    metadata = root.find(".//opf:metadata", NS)
    if metadata is None:
        return {
            "title": "",
            "creator": "",
            "description": "",
            "language": "",
            "identifier": "",
            "modified": "",
            "counts": {
                "title": 0,
                "creator": 0,
                "description": 0,
                "language": 0,
                "identifier": 0,
                "modified": 0,
            },
            "title_values": [],
            "creator_values": [],
            "description_values": [],
            "language_values": [],
            "identifier_values": [],
        }

    def values(local_name: str) -> list[str]:
        return [
            _normalize_text(element.text or "")
            for element in metadata.findall(f"dc:{local_name}", NS)
            if _normalize_text(element.text or "")
        ]

    title_values = values("title")
    creator_values = values("creator")
    description_values = values("description")
    language_values = values("language")
    identifier_values = values("identifier")
    modified_values = [
        _normalize_text(meta.text or "")
        for meta in metadata.findall(f"{{{OPF_NS}}}meta")
        if meta.get("property") == "dcterms:modified" and _normalize_text(meta.text or "")
    ]

    unique_identifier_id = root.get("unique-identifier", "")
    resolved_identifier = ""
    if unique_identifier_id:
        resolved_identifier = next(
            (
                _normalize_text(element.text or "")
                for element in metadata.findall("dc:identifier", NS)
                if element.get("id") == unique_identifier_id and _normalize_text(element.text or "")
            ),
            "",
        )
    if not resolved_identifier:
        resolved_identifier = identifier_values[0] if identifier_values else ""

    return {
        "title": title_values[0] if title_values else "",
        "creator": creator_values[0] if creator_values else "",
        "description": description_values[0] if description_values else "",
        "language": language_values[0] if language_values else "",
        "identifier": resolved_identifier,
        "modified": modified_values[0] if modified_values else "",
        "counts": {
            "title": len(title_values),
            "creator": len(creator_values),
            "description": len(description_values),
            "language": len(language_values),
            "identifier": len(identifier_values),
            "modified": len(modified_values),
        },
        "title_values": title_values,
        "creator_values": creator_values,
        "description_values": description_values,
        "language_values": language_values,
        "identifier_values": identifier_values,
        "modified_values": modified_values,
    }


def _metadata_diff(before: dict[str, object], after: dict[str, object]) -> list[dict[str, object]]:
    diff: list[dict[str, object]] = []
    for field in ("title", "creator", "description", "language", "identifier", "modified"):
        before_value = str(before.get(field, "") or "")
        after_value = str(after.get(field, "") or "")
        if before_value == after_value:
            continue
        diff.append(
            {
                "field": field,
                "before": before_value,
                "after": after_value,
            }
        )
    return diff


def _build_inventory_conflicts(
    chapter_paths,
    *,
    metadata_snapshot: dict[str, object],
    requested_title: str,
    requested_author: str,
    requested_language: str,
) -> list[dict[str, object]]:
    conflicts: list[dict[str, object]] = []
    dominant_heading = _dominant_publication_heading(chapter_paths)
    metadata_title = str(metadata_snapshot.get("title", "") or "")
    metadata_author = str(metadata_snapshot.get("creator", "") or "")
    metadata_language = str(metadata_snapshot.get("language", "") or "")

    if metadata_title and dominant_heading and not _looks_technical_title(metadata_title) and not _title_fragments_match(metadata_title, dominant_heading):
        conflicts.append(
            _manual_review_item(
                phase="inventory",
                file="package.opf",
                element="dc:title",
                before=metadata_title,
                after=dominant_heading,
                reason="metadata-heading-conflict",
                confidence=0.78,
            )
        )
    if _is_placeholder_author(metadata_author):
        conflicts.append(
            _manual_review_item(
                phase="inventory",
                file="package.opf",
                element="dc:creator",
                before=metadata_author,
                after=_normalize_text(requested_author),
                reason="placeholder-author",
                confidence=0.98,
            )
        )
    expected_language = _resolve_publication_language(requested_language, samples=[dominant_heading] if dominant_heading else [])
    if metadata_language and _canonicalize_language(metadata_language) != _canonicalize_language(expected_language):
        conflicts.append(
            _manual_review_item(
                phase="inventory",
                file="package.opf",
                element="dc:language",
                before=metadata_language,
                after=expected_language,
                reason="language-mismatch",
                confidence=0.7,
            )
        )
    if _looks_technical_title(_normalize_text(requested_title)) and dominant_heading:
        conflicts.append(
            _manual_review_item(
                phase="inventory",
                file="package.opf",
                element="requested-title",
                before=requested_title,
                after=dominant_heading,
                reason="requested-title-technical",
                confidence=0.86,
            )
        )
    return conflicts


def _dominant_publication_heading(chapter_paths) -> str:
    candidates: list[str] = []
    front_matter_seen = False
    for chapter_path in chapter_paths[:6]:
        if chapter_path.name == "cover.xhtml":
            continue
        title_page_candidate = _extract_front_matter_title_candidate(chapter_path)
        if title_page_candidate:
            return title_page_candidate
        heading_text, _ = _resolve_heading_target(chapter_path)
        normalized = _normalize_text(heading_text)
        heading_key = _training_book_key(normalized)
        if _is_front_matter_heading_key(heading_key):
            front_matter_seen = True
            continue
        if (
            normalized
            and not _looks_technical_title(normalized, reference_stem=chapter_path.stem.replace("_", " "))
        ):
            candidates.append(normalized)
    if front_matter_seen:
        return ""
    return candidates[0] if candidates else ""


def _is_front_matter_heading_key(key: str) -> bool:
    return key in {
        "front cover",
        "title",
        "copyright",
        "contents",
        "table of contents",
        "key to symbols used",
        "quick start guide",
        "sample record sheet",
        "sample record sheets",
        "back cover",
    }


def _extract_front_matter_title_candidate(chapter_path: Path) -> str:
    try:
        soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    except Exception:
        return ""

    primary_heading = soup.find("h1")
    primary_key = _training_book_key(primary_heading.get_text(" ", strip=True)) if primary_heading is not None else ""
    title_node = soup.find("title")
    title_key = _training_book_key(title_node.get_text(" ", strip=True)) if title_node is not None else ""
    if "title" not in {primary_key, title_key}:
        return ""

    candidates: list[str] = []
    for node in soup.find_all(["h2", "h1", "p", "div", "span"]):
        candidate = _normalize_text(node.get_text(" ", strip=True))
        if not candidate:
            continue
        candidate_key = _training_book_key(candidate)
        if not candidate_key or _is_front_matter_heading_key(candidate_key):
            continue
        if candidate_key in {"by", "author"}:
            continue
        if "www." in candidate.lower() or "://" in candidate or "@" in candidate:
            continue
        if _looks_technical_title(candidate, reference_stem=chapter_path.stem.replace("_", " ")):
            continue
        if AUTHOR_LINE_RE.match(candidate):
            continue
        if _looks_like_reference_entry_text(candidate):
            continue
        if not _looks_like_publication_title_candidate(candidate):
            continue
        classes = {_normalize_key(class_name) for class_name in _class_list(node)}
        if {"author", "byline"} & classes:
            continue
        candidates.append(candidate)

    if not candidates:
        return ""
    return max(candidates, key=lambda value: (len(value.split()), len(value)))


def _looks_like_publication_title_candidate(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if normalized.endswith("."):
        return False
    lowered = normalized.lower()
    if any(marker in lowered for marker in BAD_HEADING_TERMS):
        return False
    words = LETTER_TOKEN_RE.findall(normalized)
    if len(words) < 2 or len(words) > 12:
        return False
    capitalized = sum(1 for word in words if word[:1].isupper())
    return capitalized * 2 >= len(words)


def _inventory_navigation_document(opf_path: Path) -> dict[str, object]:
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(opf_path), parser)
    root = tree.getroot()
    manifest = root.find(".//opf:manifest", NS)
    package_dir = opf_path.parent
    nav_href = ""
    if manifest is not None:
        for item in manifest.findall("opf:item", NS):
            properties = {part for part in (item.get("properties") or "").split() if part}
            href = item.get("href", "")
            if "nav" in properties or href.endswith("nav.xhtml"):
                nav_href = href
                break
    nav_path = package_dir / nav_href if nav_href else package_dir / "nav.xhtml"
    result: dict[str, object] = {
        "nav_found": nav_path.exists(),
        "nav_path": nav_path.name if nav_path.exists() else (nav_href or ""),
        "toc_nav_count": 0,
        "entry_count": 0,
        "entries_sample": [],
        "warnings": [],
    }
    if not nav_path.exists():
        result["warnings"] = ["Navigation document missing."]
        return result

    soup = BeautifulSoup(nav_path.read_text(encoding="utf-8"), "xml")
    toc_navs = [node for node in soup.find_all("nav") if "toc" in _node_epub_types(node)]
    result["toc_nav_count"] = len(toc_navs)
    if toc_navs:
        entries = []
        for anchor in toc_navs[0].find_all("a"):
            label = _normalize_text(anchor.get_text(" ", strip=True))
            href = _normalize_text(anchor.get("href", ""))
            if not label:
                continue
            entries.append({"label": label, "href": href})
        result["entry_count"] = len(entries)
        result["entries_sample"] = entries[:12]
    if len(toc_navs) != 1:
        result["warnings"] = [f"Expected exactly one toc nav, found {len(toc_navs)}."]
    return result


def _node_epub_types(node: Tag) -> set[str]:
    values = []
    for key, value in node.attrs.items():
        if key in {"epub:type", "type"} or key.endswith(":type"):
            values.extend(str(value).split())
    return {part.strip().lower() for part in values if str(part).strip()}


def _inventory_spine(opf_path: Path) -> dict[str, object]:
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(opf_path), parser)
    root = tree.getroot()
    manifest = root.find(".//opf:manifest", NS)
    spine = root.find(".//opf:spine", NS)
    manifest_by_id = {
        item.get("id", ""): item
        for item in (manifest.findall("opf:item", NS) if manifest is not None else [])
        if item.get("id")
    }
    order: list[dict[str, str]] = []
    missing_manifest_refs: list[str] = []
    if spine is not None:
        for itemref in spine.findall("opf:itemref", NS):
            idref = itemref.get("idref", "")
            manifest_item = manifest_by_id.get(idref)
            if manifest_item is None:
                if idref:
                    missing_manifest_refs.append(idref)
                continue
            order.append(
                {
                    "idref": idref,
                    "href": manifest_item.get("href", ""),
                    "linear": itemref.get("linear", "yes"),
                }
            )
    return {
        "item_count": len(order),
        "items": order,
        "missing_manifest_refs": missing_manifest_refs,
    }


def _collect_heading_candidates_from_path(chapter_path: Path, *, include_pseudo: bool) -> list[dict[str, object]]:
    return _collect_heading_candidates_from_text(
        chapter_path.read_text(encoding="utf-8"),
        file_name=chapter_path.name,
        include_pseudo=include_pseudo,
    )


def _collect_heading_candidates_from_text(
    xhtml_text: str,
    *,
    file_name: str,
    include_pseudo: bool,
) -> list[dict[str, object]]:
    soup = BeautifulSoup(xhtml_text, "xml")
    body = soup.find("body")
    if body is None:
        return []

    candidates: list[dict[str, object]] = []
    order = 0
    for node in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "span"]):
        if node.find_parent(["figure", "figcaption", "table", "thead", "tbody", "tfoot", "ul", "ol", "li", "dl", "blockquote"]) is not None:
            continue
        text = _normalize_text(node.get_text(" ", strip=True))
        if not text:
            continue
        order += 1
        if node.name.startswith("h"):
            candidates.append(
                {
                    "file_name": file_name,
                    "order": order,
                    "element": node.name,
                    "id": node.get("id", ""),
                    "text": text,
                    "level": int(node.name[1]),
                    "candidate_type": "real",
                }
            )
            continue
        if not include_pseudo or not _is_pseudo_heading_candidate(node, text):
            continue
        candidates.append(
            {
                "file_name": file_name,
                "order": order,
                "element": node.name,
                "id": node.get("id", ""),
                "text": text,
                "level": None,
                "candidate_type": "pseudo",
            }
        )
    return candidates


def _is_pseudo_heading_candidate(node: Tag, text: str) -> bool:
    if not _looks_like_heading_text(text):
        return False
    if _looks_like_author_line(text) or _looks_like_game_caption(text) or _looks_like_figure_caption(text):
        return False
    class_tokens = {_normalize_key(token) for token in _class_list(node)}
    if any(token in class_tokens for token in {"heading", "title", "subtitle", "chapter-title", "section-title", "toc-entry"}):
        return True
    return len(text.split()) <= 12


def _summarize_heading_inventory(chapter_heading_inventory: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    chapters: list[dict[str, object]] = []
    total_real = 0
    total_pseudo = 0
    for file_name, items in chapter_heading_inventory.items():
        real_count = sum(1 for item in items if item.get("candidate_type") == "real")
        pseudo_count = sum(1 for item in items if item.get("candidate_type") == "pseudo")
        chapters.append(
            {
                "file": file_name,
                "real_heading_count": real_count,
                "pseudo_heading_candidate_count": pseudo_count,
            }
        )
        total_real += real_count
        total_pseudo += pseudo_count
    return {
        "chapter_count": len(chapters),
        "total_real_headings": total_real,
        "total_pseudo_heading_candidates": total_pseudo,
        "chapters": chapters,
    }


def _build_heading_decisions(
    *,
    file_name: str,
    before_candidates: list[dict[str, object]],
    after_candidates: list[dict[str, object]],
    repeated_counts: Counter,
) -> list[dict[str, object]]:
    decisions: list[dict[str, object]] = []
    remaining_after = list(after_candidates)

    for before in before_candidates:
        match_index = _find_matching_heading_candidate(before, remaining_after)
        after = remaining_after.pop(match_index) if match_index is not None else None
        status, reason, confidence = _classify_heading_decision(before=before, after=after, repeated_counts=repeated_counts)
        decisions.append(
            {
                "file": file_name,
                "status": status,
                "reason": reason,
                "confidence": confidence,
                "element": str(before.get("element") or (after or {}).get("element") or ""),
                "before": {
                    "id": str(before.get("id", "") or ""),
                    "text": str(before.get("text", "") or ""),
                    "level": before.get("level"),
                    "element": str(before.get("element", "") or ""),
                },
                "after": {
                    "id": str(after.get("id", "") or "") if after else "",
                    "text": str(after.get("text", "") or "") if after else "",
                    "level": after.get("level") if after else None,
                    "element": str(after.get("element", "") or "") if after else "",
                },
            }
        )

    for after in remaining_after:
        status, reason, confidence = _classify_heading_decision(before=None, after=after, repeated_counts=repeated_counts)
        decisions.append(
            {
                "file": file_name,
                "status": status,
                "reason": reason,
                "confidence": confidence,
                "element": str(after.get("element", "") or ""),
                "before": {"id": "", "text": "", "level": None, "element": ""},
                "after": {
                    "id": str(after.get("id", "") or ""),
                    "text": str(after.get("text", "") or ""),
                    "level": after.get("level"),
                    "element": str(after.get("element", "") or ""),
                },
            }
        )
    return decisions


def _find_matching_heading_candidate(before: dict[str, object], after_candidates: list[dict[str, object]]) -> int | None:
    best_index = None
    best_score = -1
    before_id = str(before.get("id", "") or "")
    before_text = str(before.get("text", "") or "")
    before_key = _canonical_heading_text(before_text)
    for index, after in enumerate(after_candidates):
        score = 0
        after_id = str(after.get("id", "") or "")
        after_text = str(after.get("text", "") or "")
        after_key = _canonical_heading_text(after_text)
        if before_id and after_id and before_id == after_id:
            score = max(score, 4)
        if before_key and after_key and before_key == after_key:
            score = max(score, 3)
        elif before_text and after_text and _title_fragments_match(before_text, after_text):
            score = max(score, 2)
        if score > best_score:
            best_score = score
            best_index = index
    return best_index if best_score > 0 else None


def _classify_heading_decision(
    *,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    repeated_counts: Counter,
) -> tuple[str, str, float]:
    if before and after:
        if before.get("candidate_type") == "pseudo":
            return "promoted", "promoted-pseudo-heading", 0.87
        before_level = int(before.get("level") or 0)
        after_level = int(after.get("level") or 0)
        if before_level and after_level and before_level != after_level:
            return "releveled", "heading-hierarchy-normalized", 0.91
        return "kept", "heading-preserved", 0.99
    if before:
        if _heading_candidate_looks_like_layout_artifact(before, repeated_counts=repeated_counts):
            return "removed", "layout-artifact-removed", 0.97
        if before.get("candidate_type") == "pseudo":
            return "removed", "pseudo-heading-rejected", 0.88
        return "removed", "ambiguous-heading-removed", 0.62
    if after:
        if int(after.get("level") or 0) == 1:
            return "added", "ensured-primary-heading", 0.78
        return "added", "reconstructed-heading", 0.72
    return "unchanged", "no-op", 1.0


def _heading_candidate_looks_like_layout_artifact(candidate: dict[str, object], *, repeated_counts: Counter) -> bool:
    text = str(candidate.get("text", "") or "")
    normalized = _normalize_text(text)
    lowered = normalized.lower()
    if not normalized:
        return False
    if repeated_counts.get(normalized, 0) >= 4:
        return True
    if _looks_like_game_caption(normalized) or _looks_like_figure_caption(normalized):
        return True
    if re.search(r"(?i)\b(material sponsorowany|materiaĹ‚ sponsorowany|advertorial|reklama)\b", normalized):
        return True
    if any(term in lowered for term in BAD_HEADING_TERMS):
        return True
    return False


def _manual_review_from_heading_decisions(decisions: list[dict[str, object]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for decision in decisions:
        if float(decision.get("confidence") or 0.0) >= 0.75:
            continue
        if _should_suppress_heading_review_item(decision):
            continue
        items.append(
            _manual_review_item(
                phase="heading_recovery",
                file=str(decision.get("file", "") or ""),
                element=str(decision.get("element", "") or ""),
                before=str(((decision.get("before") or {}) or {}).get("text", "") or ""),
                after=str(((decision.get("after") or {}) or {}).get("text", "") or ""),
                reason=str(decision.get("reason", "") or ""),
                confidence=float(decision.get("confidence") or 0.0),
            )
        )
    return items


def _should_suppress_heading_review_item(decision: dict[str, object]) -> bool:
    reason = str(decision.get("reason", "") or "")
    before_text = _normalize_text(str(((decision.get("before") or {}) or {}).get("text", "") or ""))
    after_text = _normalize_text(str(((decision.get("after") or {}) or {}).get("text", "") or ""))

    if reason == "ambiguous-heading-removed":
        if _looks_like_synthetic_section_label(before_text):
            return True
        if _looks_like_reference_section_title(before_text):
            return True
        if any(pattern.match(before_text) for pattern in TOC_HEADING_PATTERNS):
            return True

    if reason == "reconstructed-heading":
        if _looks_like_game_caption(after_text):
            return True
        if re.match(r"^\d+\.\s+[A-Z][^,]{3,},\s+.+\b\d{4}\b", after_text):
            return True

    return False


def _manual_review_item(
    *,
    phase: str,
    file: str,
    element: str,
    before: str,
    after: str,
    reason: str,
    confidence: float,
) -> dict[str, object]:
    return {
        "phase": phase,
        "file": file,
        "element": element,
        "before": _normalize_text(before),
        "after": _normalize_text(after),
        "reason": reason,
        "confidence": round(float(confidence), 4),
        "status": "review-needed",
    }


def _dedupe_manual_review_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for item in items:
        key = (
            str(item.get("phase", "") or ""),
            str(item.get("file", "") or ""),
            str(item.get("element", "") or ""),
            str(item.get("before", "") or ""),
            str(item.get("reason", "") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _build_metadata_phase_report(
    *,
    before: dict[str, object],
    after: dict[str, object],
    requested_title: str,
    requested_author: str,
    requested_language: str,
    chapter_paths,
) -> dict[str, object]:
    diff = _metadata_diff(before, after)
    manual_review: list[dict[str, object]] = []
    dominant_heading = _dominant_publication_heading(chapter_paths)
    after_title = str(after.get("title", "") or "")
    after_author = str(after.get("creator", "") or "")
    if dominant_heading and after_title and not _title_fragments_match(after_title, dominant_heading):
        manual_review.append(
            _manual_review_item(
                phase="metadata_repair",
                file="package.opf",
                element="dc:title",
                before=after_title,
                after=dominant_heading,
                reason="title-does-not-match-dominant-heading",
                confidence=0.69,
            )
        )
    if _is_placeholder_author(after_author):
        manual_review.append(
            _manual_review_item(
                phase="metadata_repair",
                file="package.opf",
                element="dc:creator",
                before=after_author,
                after=_normalize_text(requested_author),
                reason="author-still-placeholder",
                confidence=0.97,
            )
        )
    if _canonicalize_language(str(after.get("language", "") or "")) != _canonicalize_language(requested_language):
        manual_review.append(
            _manual_review_item(
                phase="metadata_repair",
                file="package.opf",
                element="dc:language",
                before=str(after.get("language", "") or ""),
                after=_canonicalize_language(requested_language),
                reason="language-differs-from-requested",
                confidence=0.66,
            )
        )
    return {
        "status": "completed",
        "before": before,
        "after": after,
        "diff": diff,
        "manual_review": manual_review,
    }


def _build_heading_phase_report(
    heading_decisions: list[dict[str, object]],
    *,
    chapter_paths,
    package_dir: Path,
) -> dict[str, object]:
    final_h1_counts: dict[str, int] = {}
    suspicious_final_headings: list[dict[str, object]] = []
    for chapter_path in chapter_paths:
        candidates = _collect_heading_candidates_from_path(chapter_path, include_pseudo=False)
        final_h1_counts[chapter_path.name] = sum(1 for item in candidates if int(item.get("level") or 0) == 1)
        for item in candidates:
            text = str(item.get("text", "") or "")
            if _heading_candidate_looks_like_layout_artifact(item, repeated_counts=Counter()):
                suspicious_final_headings.append(
                    {
                        "file": chapter_path.name,
                        "id": item.get("id", ""),
                        "text": text,
                    }
                )
    counts = Counter(str(decision.get("status", "") or "") for decision in heading_decisions)
    return {
        "status": "completed",
        "summary": {
            "decision_count": len(heading_decisions),
            "status_counts": dict(counts),
            "chapters_with_multiple_h1": sorted(file_name for file_name, count in final_h1_counts.items() if count > 1),
            "chapters_without_h1": sorted(file_name for file_name, count in final_h1_counts.items() if count == 0),
            "suspicious_final_heading_count": len(suspicious_final_headings),
        },
        "decisions": heading_decisions,
        "suspicious_final_headings": suspicious_final_headings[:50],
    }


def _build_toc_map(toc_entries: list[dict], *, chapter_paths, package_dir: Path) -> list[dict[str, object]]:
    heading_lookup: dict[str, dict[str, str]] = {}
    primary_heading_lookup: dict[str, str] = {}
    for chapter_path in chapter_paths:
        candidates = _collect_heading_candidates_from_path(chapter_path, include_pseudo=False)
        heading_lookup[chapter_path.name] = {
            str(item.get("id", "") or ""): str(item.get("text", "") or "")
            for item in candidates
            if item.get("id")
        }
        primary_heading_lookup[chapter_path.name] = next(
            (str(item.get("text", "") or "") for item in candidates if str(item.get("text", "") or "")),
            "",
        )
    toc_map: list[dict[str, object]] = []
    seen_targets: set[str] = set()
    for entry in _normalize_toc_entries_for_render(toc_entries):
        file_name = str(entry.get("file_name", "") or "")
        anchor_id = str(entry.get("id", "") or "")
        target = _toc_entry_href(entry)
        file_exists = (package_dir / file_name).exists() if file_name else False
        heading_text = heading_lookup.get(file_name, {}).get(anchor_id, "")
        if not heading_text and not anchor_id:
            heading_text = primary_heading_lookup.get(file_name, "")
        issues: list[str] = []
        if not file_exists:
            issues.append("missing-file")
        elif anchor_id and anchor_id not in heading_lookup.get(file_name, {}):
            issues.append("missing-anchor")
        if target in seen_targets:
            issues.append("duplicate-target")
        if heading_text and not _title_fragments_match(str(entry.get("text", "") or ""), heading_text):
            issues.append("label-heading-mismatch")
        seen_targets.add(target)
        toc_map.append(
            {
                "label": str(entry.get("text", "") or ""),
                "file": file_name,
                "anchor": anchor_id,
                "target": target,
                "heading_text": heading_text,
                "level": int(entry.get("level", 1) or 1),
                "status": "pass" if not issues else ("warning" if "label-heading-mismatch" in issues and len(issues) == 1 else "fail"),
                "issues": issues,
            }
        )
    return toc_map


def _build_toc_phase_report(
    *,
    before: dict[str, object],
    after: dict[str, object],
    toc_map: list[dict[str, object]],
    toc_entries: list[dict],
    spine_order: list[str],
) -> dict[str, object]:
    manual_review = [
        _manual_review_item(
            phase="toc_rebuild",
            file=str(item.get("file", "") or ""),
            element=str(item.get("anchor", "") or "toc"),
            before=str(item.get("label", "") or ""),
            after=str(item.get("heading_text", "") or ""),
            reason=" / ".join(item.get("issues", [])),
            confidence=0.7 if "label-heading-mismatch" in item.get("issues", []) else 0.95,
        )
        for item in toc_map
        if item.get("issues")
    ]
    return {
        "status": "completed",
        "before": before,
        "after": after,
        "toc_map": toc_map,
        "summary": {
            "entry_count": len(toc_entries),
            "broken_target_count": sum(1 for item in toc_map if any(issue in {"missing-file", "missing-anchor"} for issue in item["issues"])),
            "duplicate_target_count": sum(1 for item in toc_map if "duplicate-target" in item["issues"]),
            "label_mismatch_count": sum(1 for item in toc_map if "label-heading-mismatch" in item["issues"]),
            "spine_order_matches": _toc_entries_follow_spine_order(toc_entries, spine_order=spine_order),
        },
        "manual_review": manual_review,
    }


def _toc_entries_follow_spine_order(toc_entries: list[dict], *, spine_order: list[str]) -> bool:
    if not spine_order:
        return True
    file_positions = {file_name: index for index, file_name in enumerate(spine_order)}
    last_position = -1
    for entry in toc_entries:
        file_name = str(entry.get("file_name", "") or "")
        if file_name not in file_positions:
            continue
        position = file_positions[file_name]
        if position < last_position:
            return False
        last_position = position
    return True


def _collect_structural_integrity_summary(
    opf_path: Path,
    *,
    root_dir: Path,
    chapter_paths,
    toc_map: list[dict[str, object]],
) -> dict[str, object]:
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(opf_path), parser)
    root = tree.getroot()
    package_dir = opf_path.parent
    manifest = root.find(".//opf:manifest", NS)
    spine = root.find(".//opf:spine", NS)

    manifest_items = list(manifest.findall("opf:item", NS)) if manifest is not None else []
    manifest_hrefs = [item.get("href", "") for item in manifest_items if item.get("href")]
    missing_manifest_files = [
        href
        for href in manifest_hrefs
        if not (package_dir / href).exists()
    ]
    manifest_by_id = {
        item.get("id", ""): item
        for item in manifest_items
        if item.get("id")
    }
    spine_missing_manifest_refs = [
        itemref.get("idref", "")
        for itemref in (spine.findall("opf:itemref", NS) if spine is not None else [])
        if itemref.get("idref") and itemref.get("idref") not in manifest_by_id
    ]
    nav_summary = _inventory_navigation_document(opf_path)
    file_id_map: dict[str, set[str]] = {}
    duplicate_ids: list[dict[str, object]] = []
    broken_internal_links: list[dict[str, object]] = []

    for href in manifest_hrefs:
        if not href.endswith(".xhtml"):
            continue
        file_path = package_dir / href
        if not file_path.exists():
            continue
        soup = BeautifulSoup(file_path.read_text(encoding="utf-8"), "xml")
        ids_seen: set[str] = set()
        file_ids: set[str] = set()
        for node in soup.find_all(attrs={"id": True}):
            node_id = str(node.get("id", "") or "")
            if not node_id:
                continue
            if node_id in ids_seen:
                duplicate_ids.append({"file": href, "id": node_id})
            ids_seen.add(node_id)
            file_ids.add(node_id)
        file_id_map[href] = file_ids

    for href in manifest_hrefs:
        if not href.endswith(".xhtml"):
            continue
        file_path = package_dir / href
        if not file_path.exists():
            continue
        soup = BeautifulSoup(file_path.read_text(encoding="utf-8"), "xml")
        for anchor in soup.find_all(href=True):
            target_href = str(anchor.get("href", "") or "")
            if not target_href or re.match(r"^[a-z][a-z0-9+.\-]*:", target_href, flags=re.IGNORECASE):
                continue
            file_part, _, fragment = target_href.partition("#")
            if not file_part:
                target_file = href
            else:
                target_file = _resolve_relative_href(href, file_part)
            target_exists = target_file in file_id_map or (package_dir / target_file).exists()
            if not target_exists:
                broken_internal_links.append({"file": href, "href": target_href, "reason": "missing-file"})
                continue
            if fragment and fragment not in file_id_map.get(target_file, set()):
                broken_internal_links.append({"file": href, "href": target_href, "reason": "missing-anchor"})
        for node in soup.find_all(attrs={"aria-labelledby": True}):
            for ref_id in str(node.get("aria-labelledby", "") or "").split():
                if ref_id and ref_id not in file_id_map.get(href, set()):
                    broken_internal_links.append(
                        {
                            "file": href,
                            "href": f"#{ref_id}",
                            "reason": "missing-aria-labelledby-target",
                        }
                    )

    manual_review = [
        _manual_review_item(
            phase="structural_integrity",
            file=item.get("file", ""),
            element=item.get("id", item.get("href", "")),
            before=item.get("href", item.get("id", "")),
            after="",
            reason=item.get("reason", "structural-integrity-issue"),
            confidence=0.99,
        )
        for item in broken_internal_links[:50]
    ]
    return {
        "summary": {
            "manifest_item_count": len(manifest_items),
            "spine_item_count": len(spine.findall("opf:itemref", NS) if spine is not None else []),
            "missing_manifest_file_count": len(missing_manifest_files),
            "spine_missing_manifest_ref_count": len(spine_missing_manifest_refs),
            "duplicate_id_count": len(duplicate_ids),
            "broken_internal_link_count": len(broken_internal_links),
            "toc_issue_count": sum(1 for item in toc_map if item.get("issues")),
            "toc_nav_count": int(nav_summary.get("toc_nav_count", 0) or 0),
        },
        "manifest_missing_files": missing_manifest_files[:50],
        "spine_missing_manifest_refs": spine_missing_manifest_refs[:50],
        "duplicate_ids": duplicate_ids[:50],
        "broken_internal_links": broken_internal_links[:50],
        "navigation": nav_summary,
        "manual_review": manual_review,
    }


def _resolve_relative_href(current_file: str, target_file: str) -> str:
    candidate = Path(current_file).parent / target_file
    normalized_parts: list[str] = []
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
            continue
        normalized_parts.append(str(part))
    return "/".join(normalized_parts)


def _evaluate_inventory_gate(
    *,
    spine_before: dict[str, object],
    navigation_before: dict[str, object],
    chapter_count: int,
    pre_paginated: bool,
) -> dict[str, object]:
    if pre_paginated:
        return _gate_result("skipped", warnings=["Pre-paginated EPUB skipped before semantic repair."])
    blockers: list[str] = []
    warnings: list[str] = []
    if chapter_count <= 0:
        blockers.append("No spine XHTML chapters discovered.")
    if spine_before.get("missing_manifest_refs"):
        blockers.append("Spine references items missing from manifest.")
    if int(navigation_before.get("toc_nav_count", 0) or 0) != 1:
        blockers.append("Navigation document must contain exactly one toc nav.")
    if not navigation_before.get("nav_found"):
        blockers.append("Navigation document missing.")
    if navigation_before.get("warnings"):
        warnings.extend(str(item) for item in navigation_before.get("warnings", []))
    return _gate_result("pass" if not blockers else "fail", blockers=blockers, warnings=warnings)


def _evaluate_metadata_gate(metadata_after: dict[str, object]) -> dict[str, object]:
    blockers: list[str] = []
    if not str(metadata_after.get("title", "") or "").strip():
        blockers.append("dc:title is empty after metadata repair.")
    if _is_placeholder_author(str(metadata_after.get("creator", "") or "")):
        blockers.append("dc:creator is still a placeholder.")
    if _canonicalize_language(str(metadata_after.get("language", "") or "")) not in {"en", "pl"}:
        blockers.append("dc:language is not canonicalized.")
    modified_values = int(((metadata_after.get("counts") or {}) or {}).get("modified", 0) or 0)
    modified = str(metadata_after.get("modified", "") or "")
    if modified_values != 1 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", modified):
        blockers.append("dcterms:modified is missing or not in UTC W3CDTF format.")
    return _gate_result("pass" if not blockers else "fail", blockers=blockers)


def _evaluate_heading_gate(heading_phase: dict[str, object]) -> dict[str, object]:
    summary = (heading_phase.get("summary") or {}) if heading_phase else {}
    blockers: list[str] = []
    warnings: list[str] = []
    if summary.get("chapters_with_multiple_h1"):
        blockers.append("One or more chapters still contain multiple H1 headings.")
    if summary.get("chapters_without_h1"):
        warnings.append("Some chapters do not contain an H1 after heading recovery.")
    if int(summary.get("suspicious_final_heading_count", 0) or 0) > 0:
        blockers.append("Suspicious layout-like headings remain after heading recovery.")
    return _gate_result("pass" if not blockers else "fail", blockers=blockers, warnings=warnings)


def _evaluate_toc_gate(toc_phase: dict[str, object]) -> dict[str, object]:
    summary = (toc_phase.get("summary") or {}) if toc_phase else {}
    after = (toc_phase.get("after") or {}) if toc_phase else {}
    blockers: list[str] = []
    warnings: list[str] = []
    if int(after.get("toc_nav_count", 0) or 0) != 1:
        blockers.append("Navigation document does not contain exactly one toc nav after rebuild.")
    if int(summary.get("broken_target_count", 0) or 0) > 0:
        blockers.append("TOC contains broken targets.")
    if not bool(summary.get("spine_order_matches", False)):
        blockers.append("TOC order does not match spine order.")
    if int(summary.get("label_mismatch_count", 0) or 0) > 0:
        warnings.append("Some TOC labels do not closely match their target heading text.")
    return _gate_result("pass" if not blockers else "fail", blockers=blockers, warnings=warnings)


def _evaluate_structural_gate(structural_integrity: dict[str, object]) -> dict[str, object]:
    summary = (structural_integrity.get("summary") or {}) if structural_integrity else {}
    blockers: list[str] = []
    if int(summary.get("missing_manifest_file_count", 0) or 0) > 0:
        blockers.append("Manifest contains missing files.")
    if int(summary.get("spine_missing_manifest_ref_count", 0) or 0) > 0:
        blockers.append("Spine references missing manifest items.")
    if int(summary.get("duplicate_id_count", 0) or 0) > 0:
        blockers.append("Duplicate DOM ids remain in package content.")
    if int(summary.get("broken_internal_link_count", 0) or 0) > 0:
        blockers.append("Broken internal links remain after structural repair.")
    return _gate_result("pass" if not blockers else "fail", blockers=blockers)


def _evaluate_release_gate(
    gates: dict[str, dict[str, object]],
    *,
    manual_review_queue: list[dict[str, object]],
) -> dict[str, object]:
    blockers: list[str] = []
    warnings: list[str] = []
    for gate_name in ("B", "C", "D", "E"):
        gate = gates.get(gate_name) or {}
        if gate.get("status") == "fail":
            blockers.extend(str(item) for item in gate.get("blockers", []))
    if blockers:
        return _gate_result("fail", blockers=blockers)
    if manual_review_queue:
        warnings.append("Manual review queue is not empty; external QA and EPUBCheck are still required.")
        return _gate_result("pass_with_review", warnings=warnings, details={"manual_review_count": len(manual_review_queue)})
    return _gate_result(
        "pass",
        warnings=["Semantic finisher passed internal gates; external QA / EPUBCheck still required upstream."],
    )


def _collect_repeated_short_texts(chapter_paths: list[Path]) -> Counter:
    counter: Counter = Counter()
    for chapter_path in chapter_paths:
        soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
        body = soup.find("body")
        if body is None:
            continue
        top_level = [node for node in body.children if isinstance(node, Tag)]
        short_texts = []
        for node in top_level:
            if node.name not in {"p", "h1", "h2", "h3"}:
                continue
            text = _normalize_text(node.get_text(" ", strip=True))
            if text and len(text) <= 90 and not PAGE_TITLE_RE.match(text):
                short_texts.append(text)
        for text in short_texts[:3] + short_texts[-3:]:
            counter[text] += 1
    return counter


def _process_chapter(
    chapter_path: Path,
    *,
    repeated_counts: Counter,
    keep_first_seen: set[str],
    title: str,
    author: str,
    language: str,
) -> ProcessedChapter:
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    chapter_title = soup.find("title").get_text(strip=True) if soup.find("title") else ""
    body = soup.find("body")
    reference_report = _empty_reference_report()
    if body is None:
        return ProcessedChapter(
            xhtml=chapter_path.read_text(encoding="utf-8"),
            nav_entries=[],
            solution_targets={},
            problem_refs=[],
            reference_report=reference_report,
        )

    raw_nodes = [node for node in body.children if isinstance(node, Tag)]
    if _looks_technical_title(chapter_title, reference_stem=chapter_path.stem.replace("_", " ")):
        fallback_heading = body.find(["h1", "h2"])
        fallback_text = _normalize_text(fallback_heading.get_text(" ", strip=True)) if fallback_heading is not None else ""
        if fallback_text:
            chapter_title = fallback_text
    if raw_nodes and all(node.name in {"section", "article"} for node in raw_nodes):
        flattened_nodes: list[Tag] = []
        for wrapper in raw_nodes:
            flattened_nodes.extend(child for child in wrapper.children if isinstance(child, Tag))
        raw_nodes = flattened_nodes
    section_context = _infer_section_context(chapter_title)
    logical_blocks = _extract_logical_blocks(
        raw_nodes,
        repeated_counts=repeated_counts,
        keep_first_seen=keep_first_seen,
        title=title,
        author=author,
        section_context=section_context,
    )
    if section_context == "body" and _looks_like_reference_section(logical_blocks, chapter_title=chapter_title):
        section_context = "references"
    if section_context != "contents":
        logical_blocks = _split_inline_solution_entries(logical_blocks)
    logical_blocks = _attach_caption_paragraphs_to_following_figures(logical_blocks)
    logical_blocks = _promote_heading_blocks(logical_blocks, section_context=section_context)
    logical_blocks = _merge_heading_runs(logical_blocks)
    logical_blocks = _demote_false_headings(logical_blocks, section_context=section_context)
    logical_blocks = _merge_paragraph_blocks(logical_blocks)
    logical_blocks = _prune_redundant_headings(logical_blocks, chapter_title=chapter_title, section_context=section_context)
    logical_blocks = _clean_paragraph_heading_artifacts(logical_blocks, chapter_title=chapter_title)
    logical_blocks = _expand_semantic_blocks(logical_blocks, section_context=section_context)
    logical_blocks = _rebuild_reference_sections(
        logical_blocks,
        section_context=section_context,
        chapter_title=chapter_title,
        reference_report=reference_report,
    )
    logical_blocks = _rebuild_knowledge_structure(logical_blocks, section_context=section_context)
    logical_blocks = _enforce_heading_hierarchy(logical_blocks, chapter_title=chapter_title, section_context=section_context)
    logical_blocks = _demote_repetitive_schema_headings(logical_blocks)
    logical_blocks = _classify_intro_metadata(logical_blocks, section_context=section_context)
    logical_blocks = _remove_redundant_leading_title_fragments(logical_blocks, chapter_title=chapter_title)
    logical_blocks = _merge_leading_heading_fragments(logical_blocks)
    logical_blocks = _classify_frontmatter_signature_blocks(
        logical_blocks,
        chapter_title=chapter_title,
        section_context=section_context,
    )
    if section_context != "contents":
        logical_blocks = _annotate_solution_paragraphs(logical_blocks)
    non_marker_blocks = [block for block in logical_blocks if block.get("type") != "page-marker"]
    if len(non_marker_blocks) >= 2:
        first, second = non_marker_blocks[0], non_marker_blocks[1]
        if (
            first.get("type") == "paragraph"
            and second.get("type") == "heading"
            and _normalize_text(first.get("text", "")) == _normalize_text(second.get("text", ""))
        ):
            removed = False
            cleaned_blocks = []
            for block in logical_blocks:
                if not removed and block is first:
                    removed = True
                    continue
                cleaned_blocks.append(block)
            logical_blocks = cleaned_blocks
    if chapter_title:
        chapter_title_key = _normalize_text(chapter_title)
        removed = False
        cleaned_blocks = []
        for block in logical_blocks:
            if (
                not removed
                and block.get("type") == "paragraph"
                and _normalize_text(block.get("text", "")) == chapter_title_key
            ):
                removed = True
                continue
            cleaned_blocks.append(block)
        logical_blocks = cleaned_blocks

    nav_entries: list[dict] = []
    solution_targets: dict[str, str] = {}
    problem_refs: list[dict] = []
    body_parts = []
    section_id = f"section-{chapter_path.stem}"
    section_attrs = [f'id="{section_id}"']
    if section_context == "references":
        section_attrs.append('epub:type="bibliography"')
        section_attrs.append('class="reference-section"')
    body_parts.append(f"<section {' '.join(section_attrs)}>")
    heading_counter = 0
    used_ids: set[str] = {section_id}
    nav_targets_seen: set[str] = set()
    nav_text_keys_seen: set[str] = set()
    subsection_nav_count = 0
    current_list_tag = ""

    def close_list_if_needed() -> None:
        nonlocal current_list_tag
        if current_list_tag:
            body_parts.append(f"</{current_list_tag}>")
            current_list_tag = ""

    def maybe_add_nav_entry(*, heading_text: str, heading_level: int, heading_id: str) -> None:
        nonlocal subsection_nav_count
        entry_level = max(1, min(int(heading_level or 1), 3))
        nav_text = chapter_title if not nav_entries and entry_level == 1 and chapter_title else heading_text
        nav_text = _normalize_text(nav_text) or _normalize_text(heading_text)
        if not nav_text:
            return
        if not _should_include_in_toc(nav_text, entry_level):
            fallback_text = _normalize_text(heading_text)
            if fallback_text and fallback_text != nav_text and _should_include_in_toc(fallback_text, entry_level):
                nav_text = fallback_text
            else:
                return
        if section_context == "contents" and nav_entries:
            return
        if section_context == "index" and entry_level > 1:
            return
        if entry_level == 3 and subsection_nav_count >= MAX_SUBSECTION_NAV_PER_CHAPTER:
            return

        href = f"{chapter_path.name}#{heading_id}"
        text_key = _normalize_key(nav_text)
        if href in nav_targets_seen:
            return
        if entry_level > 1 and text_key in nav_text_keys_seen:
            return

        nav_entries.append(
            {
                "file_name": chapter_path.name,
                "id": heading_id,
                "text": nav_text,
                "level": entry_level,
            }
        )
        nav_targets_seen.add(href)
        nav_text_keys_seen.add(text_key)
        if entry_level == 3:
            subsection_nav_count += 1

    for block in logical_blocks:
        block_type = block["type"]
        if block_type == "page-marker":
            close_list_if_needed()
            body_parts.append(block["html"])
            continue

        if block_type == "exercise-marker":
            close_list_if_needed()
            marker_id = _unique_dom_id(block["id"], used_ids, fallback="exercise-marker")
            body_parts.append(
                f'<p id="{html.escape(marker_id)}" class="exercise-marker">{html.escape(block["exercise_num"])}</p>'
            )
            continue

        if block_type == "heading":
            close_list_if_needed()
            heading_counter += 1
            heading_level = block["level"]
            heading_id = _unique_dom_id(
                block.get("id") or f"{chapter_path.stem}-heading-{heading_counter}",
                used_ids,
                fallback=f"{chapter_path.stem}-heading-{heading_counter}",
            )
            heading_html = html.escape(block["text"])
            body_parts.append(f'<h{heading_level} id="{heading_id}">{heading_html}</h{heading_level}>')
            maybe_add_nav_entry(
                heading_text=block.get("nav_text") or block["text"],
                heading_level=heading_level,
                heading_id=heading_id,
            )
            continue

        if block_type == "solution-heading":
            close_list_if_needed()
            exercise_num = block["exercise_num"]
            target = block.get("target", "")
            problem_file = _problem_file_from_href(target) or block.get("problem_file", "")
            if not target and problem_file:
                target = f"{problem_file}#exercise-{exercise_num}"
            heading_id = _unique_dom_id(f"solution-{exercise_num}", used_ids, fallback="solution")
            solution_href = f"{chapter_path.name}#{heading_id}"
            solution_targets.setdefault(exercise_num, solution_href)
            if problem_file:
                problem_refs.append(
                    {
                        "problem_file": problem_file,
                        "exercise_num": exercise_num,
                        "solution_href": solution_href,
                    }
                )
            if target:
                heading_html = (
                    f'<a class="solution-backlink" href="{html.escape(target)}">'
                    f"{html.escape(block['text'])}</a>"
                )
            else:
                heading_html = html.escape(block["text"])
            body_parts.append(f'<h3 id="{heading_id}" class="solution-entry">{heading_html}</h3>')
            continue

        if block_type == "paragraph":
            close_list_if_needed()
            class_name = block.get("class_name", "")
            class_attr = f' class="{html.escape(class_name)}"' if class_name else ""
            paragraph_html = block["html"]
            if "solution-text" in class_name and "notation-heavy" in class_name:
                paragraph_html = _format_solution_variation_html(block.get("text", ""))
            body_parts.append(f"<p{class_attr}>{paragraph_html}</p>")
            continue

        if block_type == "blockquote":
            close_list_if_needed()
            class_attr = f' class="{html.escape(block["class_name"])}"' if block.get("class_name") else ""
            body_parts.append(f"<blockquote{class_attr}><p>{block['html']}</p></blockquote>")
            continue

        if block_type == "list-item":
            list_tag = "ol" if block.get("list_kind") == "ol" else "ul"
            if current_list_tag != list_tag:
                close_list_if_needed()
                body_parts.append(f"<{list_tag}>")
                current_list_tag = list_tag
            class_attr = f' class="{html.escape(block["class_name"])}"' if block.get("class_name") else ""
            body_parts.append(f"<li{class_attr}>{block['html']}</li>")
            continue

        if block_type == "definition-list":
            close_list_if_needed()
            class_attr = f' class="{html.escape(block["class_name"])}"' if block.get("class_name") else ""
            definition_parts = [f"<dl{class_attr}>"]
            for item in block.get("items", []):
                definition_parts.append(f"<dt>{html.escape(item['term'])}</dt>")
                definition_parts.append(f"<dd>{html.escape(item['desc'])}</dd>")
            definition_parts.append("</dl>")
            body_parts.append("".join(definition_parts))
            continue

        if block_type == "table":
            close_list_if_needed()
            if block.get("html"):
                body_parts.append(block["html"])
                continue
            class_attr = f' class="{html.escape(block["class_name"])}"' if block.get("class_name") else ""
            table_parts = [f"<table{class_attr}>"]
            headers = block.get("headers") or []
            if headers:
                table_parts.append("<thead><tr>")
                for header in headers:
                    table_parts.append(f"<th>{html.escape(header)}</th>")
                table_parts.append("</tr></thead>")
            table_parts.append("<tbody>")
            for row in block.get("rows") or []:
                table_parts.append("<tr>")
                for cell in row:
                    table_parts.append(f"<td>{html.escape(cell)}</td>")
                table_parts.append("</tr>")
            table_parts.append("</tbody></table>")
            body_parts.append("".join(table_parts))
            continue

        if block_type == "problem-page-link":
            close_list_if_needed()
            body_parts.append(
                f'<p class="problem-page-link"><a href="{html.escape(block["href"])}">'
                f"{html.escape(block['text'])}</a></p>"
            )
            continue

        if block_type == "figure":
            close_list_if_needed()
            body_parts.append(block["html"])

    close_list_if_needed()
    body_parts.append("</section>")

    document_title = _resolve_document_title(
        chapter_path=chapter_path,
        nav_entries=nav_entries,
        logical_blocks=logical_blocks,
        chapter_title=chapter_title,
    )
    xhtml = _build_xhtml_document(
        title=document_title,
        body_html="\n".join(body_parts),
        language=language,
    )
    chapter_path.write_text(xhtml, encoding="utf-8")
    return ProcessedChapter(
        xhtml=xhtml,
        nav_entries=nav_entries,
        solution_targets=solution_targets,
        problem_refs=problem_refs,
        reference_report=reference_report,
    )


def _extract_logical_blocks(
    raw_nodes: list[Tag],
    *,
    repeated_counts: Counter,
    keep_first_seen: set[str],
    title: str,
    author: str,
    section_context: str = "body",
) -> list[dict]:
    content_nodes = [node for node in raw_nodes if not (node.name == "h1" and PAGE_TITLE_RE.match(_normalize_text(node.get_text(" ", strip=True))))]
    blocks: list[dict] = []
    total = len(content_nodes)

    for index, node in enumerate(content_nodes):
        plain_text = _normalize_text(node.get_text(" ", strip=True))
        is_top = index <= 2

        if node.name == "span" and "page-marker" in _class_list(node):
            node["class"] = "page-marker"
            blocks.append({"type": "page-marker", "html": str(node)})
            continue

        if node.name in {"div", "figure", "img"}:
            figure_html = _normalize_figure_html(node)
            if figure_html:
                blocks.append({"type": "figure", "html": figure_html})
            continue

        if node.name == "table":
            table_html = _normalize_existing_table_html(node)
            if table_html:
                blocks.append(
                    {
                        "type": "table",
                        "text": plain_text,
                        "html": table_html,
                        "class_name": _append_class_name(" ".join(_class_list(node)), "semantic-table"),
                        "is_top": is_top,
                    }
                )
            continue

        if node.name in {"ul", "ol"}:
            for item in node.find_all("li", recursive=False):
                item_text = _normalize_text(item.get_text(" ", strip=True))
                if not item_text:
                    continue
                item_classes = " ".join(_class_list(item)).strip()
                class_name = _append_class_name(item_classes, "list-item")
                blocks.append(
                    {
                        "type": "list-item",
                        "text": item_text,
                        "html": _normalize_list_item_html(item) or html.escape(item_text),
                        "class_name": class_name,
                        "list_kind": "ol" if node.name == "ol" else "ul",
                        "is_top": is_top,
                    }
                )
            continue

        if node.name not in {"p", "h1", "h2", "h3", "h4"}:
            continue

        if not plain_text:
            continue
        if re.fullmatch(r"[®©™]+", plain_text):
            continue
        if node.get("id", "").startswith("exercise-") and PAGE_NUMBER_RE.match(plain_text):
            blocks.append(
                {
                    "type": "exercise-marker",
                    "exercise_num": plain_text,
                    "id": node.get("id", ""),
                }
            )
            continue
        if PAGE_TITLE_RE.match(plain_text) or PAGE_NUMBER_RE.match(plain_text):
            continue

        repeat_action = _repeated_text_action(
            plain_text,
            repeated_counts=repeated_counts,
            title=title,
            author=author,
            is_top=is_top,
        )
        if repeat_action == "drop":
            continue
        if repeat_action == "keep-first-heading":
            key = _normalize_key(plain_text)
            if key in keep_first_seen or not is_top:
                continue
            keep_first_seen.add(key)
            blocks.append(
                {
                    "type": "heading",
                    "text": plain_text,
                    "level": 1,
                    "id": _slugify(plain_text),
                }
            )
            continue

        if EMAIL_RE.search(plain_text):
            plain_text = EMAIL_RE.sub("", plain_text).strip()
            if not plain_text:
                continue

        if node.name in {"h1", "h2", "h3", "h4"}:
            blocks.append(
                {
                    "type": "heading",
                    "text": plain_text,
                    "level": min(max(int(node.name[1]), 1), 3),
                    "id": node.get("id", "") or _slugify(plain_text),
                    "is_top": is_top,
                }
            )
            continue

        if section_context == "contents" and _looks_like_contents_entry(plain_text):
            blocks.append(
                {
                    "type": "paragraph",
                    "text": plain_text,
                    "html": html.escape(plain_text),
                    "class_name": "toc-entry",
                }
            )
            continue

        anchor = node.find("a", class_="solution-backlink")
        if anchor:
            if _is_true_solution_entry(plain_text):
                exercise_num = TRUE_SOLUTION_ENTRY_RE.match(plain_text).group("num")
                blocks.append(
                    {
                        "type": "solution-heading",
                        "exercise_num": exercise_num,
                        "target": anchor.get("href", ""),
                        "text": plain_text,
                    }
                )
                continue

            blocks.append(
                {
                    "type": "paragraph",
                    "text": plain_text,
                    "html": html.escape(plain_text),
                }
            )
            continue

        link = node.find("a")
        if link and SOLUTION_PAGE_RE.search(plain_text):
            blocks.append(
                {
                    "type": "problem-page-link",
                    "href": link.get("href", ""),
                    "text": plain_text,
                }
            )
            continue

        class_names = _class_list(node)
        class_name = ""
        if "diagram-tail" in class_names:
            class_name = "diagram-tail"
        elif "author" in class_names:
            class_name = "author"
        elif "subtitle" in class_names:
            class_name = "subtitle"
        elif "byline" in class_names:
            class_name = "byline"

        blocks.append(
            {
                "type": "paragraph",
                "text": plain_text,
                "html": _sanitize_inline_html(_inner_html(node)),
                "class_name": class_name,
                "is_top": is_top,
            }
        )

    return blocks


def _infer_section_context(chapter_title: str) -> str:
    normalized = _normalize_key(chapter_title)
    if normalized in {"index", "name index", "opening index"}:
        return "index"
    if normalized in {"glossary", "appendix a glossary"}:
        return "glossary"
    if normalized in {"table of contents", "contents", "spis treści", "spis tresci"}:
        return "contents"
    if _looks_like_reference_section_title(normalized):
        return "references"
    if normalized.startswith("appendix"):
        return "appendix"
    return "body"


def _promote_heading_blocks(blocks: list[dict], *, section_context: str = "body") -> list[dict]:
    promoted = []
    seen_primary_heading = False
    dense_short_frontmatter = sum(
        1
        for block in blocks[:6]
        if block["type"] == "paragraph" and len(block["text"]) <= 90
    ) >= 4
    contents_mode = any(
        block["type"] == "paragraph" and _normalize_key(block["text"]) in {"contents", "spis treści"}
        for block in blocks[:5]
    )

    for index, block in enumerate(blocks):
        if block["type"] != "paragraph":
            promoted.append(block)
            continue

        text = block["text"]
        if section_context == "contents" and "toc-entry" in (block.get("class_name") or ""):
            promoted.append(block)
            continue
        if section_context == "index":
            if re.fullmatch(r"[A-Z]", text):
                promoted.append(
                    {
                        "type": "heading",
                        "text": text,
                        "level": 3,
                        "id": _slugify(text),
                    }
                )
            else:
                promoted.append(block)
            continue
        if section_context == "references" and (
            _extract_reference_entries_from_block(block)
            or _looks_like_reference_entry_text(text)
            or len(_split_reference_entries_from_text(text)) >= 2
        ):
            promoted.append(block)
            continue
        if section_context in {"glossary", "appendix"} and _looks_like_definition_paragraph(text):
            promoted.append(block)
            continue
        if _looks_like_author_line(text):
            promoted.append(block)
            continue
        if (
            _looks_like_ordered_list_item(text)
            or _looks_like_bullet_item(text)
            or _split_inline_ordered_list_items(block)
            or _split_inline_bullet_list_items(block)
            or _split_inline_semicolon_list_items(block)
            or _build_definition_list_block(block)
            or _build_table_block(block)
        ):
            promoted.append(block)
            continue
        if not _looks_like_heading_text(text):
            promoted.append(block)
            continue

        words = LETTER_TOKEN_RE.findall(_normalize_text(text))
        if (
            not block.get("is_top")
            and len(words) == 1
            and words[0].lower() in MINOR_HEADING_WORDS
        ):
            promoted.append(block)
            continue

        if dense_short_frontmatter and block.get("is_top") and _normalize_key(text) not in {
            "contents",
            "spis treści",
        }:
            promoted.append(block)
            continue

        if contents_mode and seen_primary_heading:
            promoted.append(block)
            continue

        prev_type = promoted[-1]["type"] if promoted else None
        next_text = ""
        for candidate in blocks[index + 1:]:
            if candidate["type"] == "paragraph":
                next_text = candidate["text"]
                break
            if candidate["type"] in {"figure", "heading", "solution-heading"}:
                break

        if prev_type == "paragraph" and not block.get("is_top"):
            level = 3
        elif seen_primary_heading:
            level = 3
        else:
            level = 2

        if next_text and len(next_text) < 40 and _looks_like_heading_text(next_text):
            level = 2

        promoted.append(
            {
                "type": "heading",
                "text": text,
                "level": level,
                "id": _slugify(text),
            }
        )
        if level <= 2:
            seen_primary_heading = True

    return promoted


def _merge_heading_runs(blocks: list[dict]) -> list[dict]:
    merged: list[dict] = []
    index = 0

    while index < len(blocks):
        block = blocks[index]
        next_block = blocks[index + 1] if index + 1 < len(blocks) else None
        if block.get("type") == "heading" and next_block and next_block.get("type") == "heading":
            current_text = _normalize_text(block.get("text", ""))
            next_text = _normalize_text(next_block.get("text", ""))
            current_numeric = bool(re.fullmatch(r"\d+(?:\.\d+)*", current_text))
            next_numeric = bool(re.fullmatch(r"\d+(?:\.\d+)*", next_text))

            if current_numeric and next_text:
                level = 2 if current_text.count(".") <= 1 else 3
                merged.append(
                    {
                        "type": "heading",
                        "text": f"{current_text} {next_text}".strip(),
                        "level": level,
                        "id": _slugify(f"{current_text}-{next_text}"),
                    }
                )
                index += 2
                continue

            if next_numeric and current_text:
                level = 2 if next_text.count(".") <= 1 else 3
                merged.append(
                    {
                        "type": "heading",
                        "text": f"{next_text} {current_text}".strip(),
                        "level": level,
                        "id": _slugify(f"{next_text}-{current_text}"),
                    }
                )
                index += 2
                continue

        merged.append(block)
        index += 1

    return merged


def _prune_redundant_headings(blocks: list[dict], *, chapter_title: str, section_context: str = "body") -> list[dict]:
    pruned: list[dict] = []
    chapter_key = _normalize_key(chapter_title)
    seen_heading_keys: list[str] = []

    for block in blocks:
        if block.get("type") != "heading":
            pruned.append(block)
            continue

        text = _normalize_text(block.get("text", ""))
        text = re.sub(r"^\.(\d+)\s+", r"\1. ", text)
        heading_key = _normalize_key(text)
        if not text or re.fullmatch(r"\d+(?:\.\d+)*", text):
            continue
        if section_context == "index" and not re.fullmatch(r"[A-Z]", text):
            continue
        if chapter_key and heading_key == chapter_key and any(item == chapter_key for item in seen_heading_keys):
            continue
        if seen_heading_keys and heading_key == seen_heading_keys[-1]:
            continue

        seen_heading_keys.append(heading_key)
        pruned.append({**block, "text": text})

    return pruned


def _maybe_fix_mojibake(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    try:
        repaired = normalized.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return normalized
    return repaired or normalized


def _matching_text_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for candidate in {_normalize_text(text), _maybe_fix_mojibake(text)}:
        if not candidate:
            continue
        keys.add(_normalize_key(candidate))
        folded = "".join(
            char
            for char in unicodedata.normalize("NFKD", candidate)
            if not unicodedata.combining(char)
        )
        if folded:
            keys.add(_normalize_key(folded))
        transliterated = candidate.translate(
            str.maketrans(
                {
                    "ą": "a",
                    "ć": "c",
                    "ę": "e",
                    "ł": "l",
                    "ń": "n",
                    "ó": "o",
                    "ś": "s",
                    "ź": "z",
                    "ż": "z",
                    "Ą": "A",
                    "Ć": "C",
                    "Ę": "E",
                    "Ł": "L",
                    "Ń": "N",
                    "Ó": "O",
                    "Ś": "S",
                    "Ź": "Z",
                    "Ż": "Z",
                }
            )
        )
        if transliterated:
            keys.add(_normalize_key(transliterated))
    return {key for key in keys if key}


def _is_generic_schema_heading_label(text: str) -> bool:
    return bool(
        _matching_text_keys(text)
        & {"co to jest", "jak dziala", "implikacje biznesowe", "przyklad"}
    )


def _looks_like_table_header_heading(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if len(normalized) < 24:
        return False
    tokens = [token.strip("()[]{}.,;:!?") for token in normalized.replace("/", " / ").split() if token.strip("()[]{}.,;:!?")]
    if len(tokens) < 5:
        return False
    capitalized_tokens = sum(1 for token in tokens if token[:1].isupper() or token.isupper())
    long_tokens = sum(1 for token in tokens if len(token) >= 4)
    connector_tokens = sum(
        1
        for token in tokens
        if token.lower() in {"i", "oraz", "to", "co", "po", "for", "and", "of", "the"}
    )
    if "/" in normalized and capitalized_tokens >= 3 and long_tokens >= 4:
        return True
    return capitalized_tokens >= 4 and long_tokens >= 4 and connector_tokens <= 2


def _demote_repetitive_schema_headings(blocks: list[dict]) -> list[dict]:
    schema_heading_keys = {
        key
        for label in KNOWLEDGE_SCHEMA_LABELS.values()
        for key in _matching_text_keys(label)
    }
    heading_counts = Counter(
        next((key for key in _matching_text_keys(block.get("text", "")) if key in schema_heading_keys), "")
        for block in blocks
        if block.get("type") == "heading"
    )
    heading_counts.pop("", None)
    if not any(count > 2 for count in heading_counts.values()):
        return blocks

    seen_counts: Counter[str] = Counter()
    adjusted: list[dict] = []
    for block in blocks:
        if block.get("type") != "heading":
            adjusted.append(block)
            continue
        text = _normalize_text(block.get("text", ""))
        key = next((candidate for candidate in _matching_text_keys(text) if candidate in schema_heading_keys), "")
        if not key:
            adjusted.append(block)
            continue
        seen_counts[key] += 1
        if seen_counts[key] <= 2:
            adjusted.append(block)
            continue
        adjusted.append(
            {
                "type": "paragraph",
                "text": text,
                "html": html.escape(text),
                "class_name": "kicker knowledge-kicker demoted-schema-heading",
            }
        )
    return adjusted


def _clean_paragraph_heading_artifacts(blocks: list[dict], *, chapter_title: str) -> list[dict]:
    heading_texts = [
        _normalize_text(block.get("text", ""))
        for block in blocks
        if block.get("type") == "heading" and _normalize_text(block.get("text", ""))
    ]
    heading_texts = sorted(set(heading_texts), key=len, reverse=True)
    cleaned: list[dict] = []

    for index, block in enumerate(blocks):
        if block.get("type") != "paragraph":
            cleaned.append(block)
            continue

        text = _normalize_text(block.get("text", ""))
        html_text = block.get("html", "")
        if re.fullmatch(r"\d+(?:\.\d+)*", text):
            prev_type = blocks[index - 1].get("type") if index > 0 else ""
            next_type = blocks[index + 1].get("type") if index + 1 < len(blocks) else ""
            if prev_type == "heading" or next_type == "heading":
                continue

        text = _strip_heading_artifacts(text, chapter_title=chapter_title, heading_texts=heading_texts)
        html_text = _sanitize_inline_html(_strip_heading_artifacts(_normalize_text(html_text), chapter_title=chapter_title, heading_texts=heading_texts))
        if not text:
            continue

        cleaned.append(
            {
                **block,
                "text": text,
                "html": html_text or html.escape(text),
            }
        )

    return cleaned


def _strip_heading_artifacts(text: str, *, chapter_title: str, heading_texts: list[str]) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return normalized

    candidates = [candidate for candidate in heading_texts if len(candidate) >= 10]
    if chapter_title:
        candidates.append(_normalize_text(chapter_title))

    for candidate in candidates:
        if not candidate:
            continue
        if len(normalized) <= len(candidate) + 25:
            continue
        leading_pattern = re.compile(
            rf"^\s*(?:\d+(?:\.\d+)*\s+)?{re.escape(candidate)}(?:\s*[:\-–—]\s*|\s{{2,}})"
        )
        if leading_pattern.search(normalized):
            normalized = leading_pattern.sub("", normalized, count=1)
            continue
        if len(candidate.split()) <= 4:
            simple_leading_pattern = re.compile(
                rf"^\s*(?:\d+(?:\.\d+)*\s+)?{re.escape(candidate)}(?=\s+\w)"
            )
            if simple_leading_pattern.search(normalized):
                normalized = simple_leading_pattern.sub("", normalized, count=1)

    normalized = re.sub(r"^\d+(?:\.\d+)*\s+", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _split_inline_solution_entries(blocks: list[dict]) -> list[dict]:
    split_blocks: list[dict] = []

    for index, block in enumerate(blocks):
        if block["type"] != "paragraph":
            split_blocks.append(block)
            continue

        if _is_true_solution_entry(block["text"]):
            exercise_num = TRUE_SOLUTION_ENTRY_RE.match(block["text"]).group("num")
            problem_file = _infer_neighbor_problem_file(blocks, index)
            target = f"{problem_file}#exercise-{exercise_num}" if problem_file else ""
            split_blocks.append(
                {
                    "type": "solution-heading",
                    "exercise_num": exercise_num,
                    "target": target,
                    "problem_file": problem_file,
                    "text": block["text"],
                }
            )
            continue

        match = MERGED_SOLUTION_ENTRY_RE.match(block["text"])
        if not match:
            split_blocks.append(block)
            continue

        exercise_num = match.group("num")
        title_text = _normalize_text(f'{exercise_num}. {match.group("title")}')
        body_text = _normalize_text(match.group("body"))
        problem_file = _infer_neighbor_problem_file(blocks, index)
        target = f"{problem_file}#exercise-{exercise_num}" if problem_file else ""

        split_blocks.append(
            {
                "type": "solution-heading",
                "exercise_num": exercise_num,
                "target": target,
                "problem_file": problem_file,
                "text": title_text,
            }
        )
        if body_text:
            split_blocks.append(
                {
                    "type": "paragraph",
                    "text": body_text,
                    "html": html.escape(body_text),
                    "class_name": block.get("class_name", ""),
                }
            )

    return split_blocks


def _attach_caption_paragraphs_to_following_figures(blocks: list[dict]) -> list[dict]:
    attached: list[dict] = []
    index = 0

    while index < len(blocks):
        block = blocks[index]
        if (
            block["type"] in {"paragraph", "heading", "solution-heading"}
            and (_looks_like_game_caption(block["text"]) or _looks_like_figure_caption(block["text"]))
        ):
            next_block = blocks[index + 1] if index + 1 < len(blocks) else None
            if next_block and next_block["type"] == "figure":
                caption_html = block.get("html", "") or html.escape(block.get("text", ""))
                attached.append(
                    {
                        **next_block,
                        "html": _inject_caption_into_figure_html(
                            next_block["html"],
                            caption_html=caption_html,
                        ),
                    }
                )
                index += 2
                continue
        if block["type"] == "figure":
            next_block = blocks[index + 1] if index + 1 < len(blocks) else None
            if (
                next_block
                and next_block["type"] in {"paragraph", "heading", "solution-heading"}
                and (_looks_like_game_caption(next_block["text"]) or _looks_like_figure_caption(next_block["text"]))
            ):
                caption_html = next_block.get("html", "") or html.escape(next_block.get("text", ""))
                attached.append(
                    {
                        **block,
                        "html": _inject_caption_into_figure_html(
                            block["html"],
                            caption_html=caption_html,
                        ),
                    }
                )
                index += 2
                continue
        attached.append(block)
        index += 1

    return attached


def _looks_like_promotional_banner(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    return bool(PROMO_BANNER_RE.match(normalized))


def _demote_false_headings(blocks: list[dict], *, section_context: str) -> list[dict]:
    adjusted: list[dict] = []
    for block in blocks:
        if block.get("type") != "heading":
            adjusted.append(block)
            continue
        text = _normalize_text(block.get("text", ""))
        if not text:
            continue
        if _looks_like_promotional_banner(text):
            adjusted.append(
                {
                    "type": "paragraph",
                    "text": text,
                    "html": html.escape(text),
                    "class_name": "promo-banner",
                    "is_top": block.get("is_top", False),
                }
            )
            continue
        adjusted.append(block)
    return adjusted


def _merge_paragraph_blocks(blocks: list[dict]) -> list[dict]:
    merged: list[dict] = []

    for block in blocks:
        if block["type"] != "paragraph":
            merged.append(block)
            continue

        if not merged or merged[-1]["type"] != "paragraph":
            merged.append(dict(block))
            continue

        previous = merged[-1]
        if "toc-entry" in (previous.get("class_name", "") or "") or "toc-entry" in (block.get("class_name", "") or ""):
            merged.append(dict(block))
            continue
        metadata_classes = {"author", "subtitle", "byline", "signature", "dateline", "signature-meta", "promo-banner"}
        previous_classes = set((previous.get("class_name", "") or "").split())
        current_classes = set((block.get("class_name", "") or "").split())
        if previous_classes & metadata_classes or current_classes & metadata_classes:
            merged.append(dict(block))
            continue
        if _looks_like_reference_entry_text(previous["text"]) or _looks_like_reference_entry_text(block["text"]):
            merged.append(dict(block))
            continue
        if _should_merge_paragraphs(previous["text"], block["text"]):
            separator = _merge_separator(previous["text"], block["text"])
            previous["text"] = _normalize_text(f'{previous["text"]}{separator}{block["text"]}')
            previous["html"] = _sanitize_inline_html(f'{previous["html"]}{separator}{block["html"]}')
            previous["class_name"] = previous.get("class_name") or block.get("class_name") or ""
        else:
            merged.append(dict(block))

    return merged


DEFINITION_LINE_RE = re.compile(
    r"^(?P<term>[A-Z][A-Za-z0-9/&()'.\u00c0-\u017f \/-]{1,40})\s*(?:—|–|-|:)\s*(?P<desc>.+)$"
)
ORDERED_LIST_ITEM_RE = re.compile(r"^(?P<marker>\(?\d{1,3}[.)])\s+(?P<body>.+)$")
INLINE_ORDERED_MARKER_RE = re.compile(r"(?:^|(?<=[\s;:]))(?P<marker>\(?\d{1,3}[.)])\s+")
BULLET_MARKER_CHARS = "•*·▪‣◦●\uf0b7"
INLINE_BULLET_MARKER_RE = re.compile(rf"(?:^|(?<=[\s;:]))(?P<marker>[{re.escape(BULLET_MARKER_CHARS)}])\s+")

INLINE_SEMICOLON_SPLIT_RE = re.compile(r"\s*;\s*")
REFERENCE_REVIEW_LABEL_RE = re.compile(
    r"(?i)\b(?:unresolved\s+url|url\s+fragment\s+for\s+review|link\s+requires\s+manual\s+review)\s*:?\s*"
)
REFERENCE_RAW_LINK_RE = re.compile(
    r"(?i)\b(?:https?://[^\s<>'\"]+|www\.[^\s<>'\"]*|doi:\S*)"
)
REFERENCE_LINK_RE = re.compile(
    r"(?i)\b(?:https?://[^\s<>'\"]+|www\.[^\s<>'\"]+|doi:10\.[A-Za-z0-9./;()_:-]+)"
)
REFERENCE_GLUE_BOUNDARY_RE = re.compile(
    r"(?i)((?:https?://|www\.)[^\s<>'\"]+?)(?=(?:https?://|www\.|doi:10\.))"
)
REFERENCE_DOI_GLUE_BOUNDARY_RE = re.compile(
    r"(?i)(doi:10\.[A-Za-z0-9./;()_:-]+?)(?=(?:https?://|www\.|doi:10\.))"
)
REFERENCE_URL_START_RE = re.compile(r"(?i)(?:https?://|www\.|doi:)")
REFERENCE_URL_CONTINUATION_PREFIX_RE = re.compile(
    r"^\s*(?:\.[A-Za-z0-9-]+(?:/[A-Za-z0-9._~%!$&'()*+,;=:@/?#-]*)?|/[A-Za-z0-9._~%!$&'()*+,;=:@/?#-]+|[?#&=][A-Za-z0-9._~%!$&'()*+,;=:@/?#-]+|:[0-9]+(?:/[A-Za-z0-9._~%!$&'()*+,;=:@/?#-]*)?)"
)
REFERENCE_ENTRY_ID_RE = re.compile(
    r"^(?P<id>(?:\[\s*[A-Za-z0-9][A-Za-z0-9,\s-]{0,31}\s*\]|\d{1,3}[.)]|[A-Z]{1,6}-?\d+[A-Za-z0-9-]*))\s+(?P<body>.+)$"
)
REFERENCE_INLINE_ID_RE = re.compile(
    r"(?P<id>(?:\[\s*[A-Za-z0-9][A-Za-z0-9,\s-]{0,31}\s*\]|\d{1,3}[.)]|[A-Z]{1,6}-?\d+[A-Za-z0-9-]*))\s+"
)
REFERENCE_NUMERIC_ID_RE = re.compile(r"^(?:\[\s*\d+[A-Za-z]?(?:\s*,\s*\d+[A-Za-z]?)*\s*\]|\d{1,3}[.)])$")
SPLIT_NUMERIC_VALUE_RE = re.compile(r"^\d+[.,]$")
NUMERIC_VALUE_CONTINUATION_RE = re.compile(
    r"^\d+(?:[.,]\d+)?\s*(?:%|‰|\b(?:proc(?:ent|\.?)?|percent|procent|per\s+cent|zł|zl|pln|usd|eur|gbp|kg|g|mg|km|m|cm|mm|ml|l|pkt|pp|bps)\b)",
    re.IGNORECASE,
)
PROMO_BANNER_RE = re.compile(
    r"(?i)^(?:materia[łl]\s+sponsorowany|material\s+sponsorowany|reklama|advertorial)(?:\s*[-–—:]\s*\S.*)?$"
)


def _make_list_item_block(
    source_block: dict,
    *,
    item_text: str,
    list_kind: str = "ul",
    class_name_extra: str = "",
    item_html: str = "",
) -> dict:
    class_name = source_block.get("class_name", "") or ""
    if class_name_extra:
        class_name = _append_class_name(class_name, class_name_extra)
    return {
        "type": "list-item",
        "text": item_text,
        "html": item_html or html.escape(item_text),
        "class_name": class_name,
        "list_kind": list_kind,
        "is_top": source_block.get("is_top", False),
    }


def _extract_inline_list_items(text: str, matches: list[re.Match]) -> list[str]:
    items: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        item_text = _normalize_text(text[start:end].strip(" ;,"))
        if len(item_text) < 2 or len(item_text) > 280:
            return []
        items.append(item_text)
    return items


def _split_inline_ordered_list_items(block: dict) -> list[dict]:
    text = _normalize_text(block.get("text", ""))
    matches = list(INLINE_ORDERED_MARKER_RE.finditer(text))
    if len(matches) < 2:
        return []

    numbers: list[int] = []
    for match in matches:
        marker = match.group("marker")
        number_match = re.search(r"\d+", marker)
        if not number_match:
            return []
        numbers.append(int(number_match.group()))

    sequential_pairs = sum(1 for left, right in zip(numbers, numbers[1:]) if right == left + 1)
    if sequential_pairs < max(1, len(numbers) - 2):
        return []

    items = _extract_inline_list_items(text, matches)
    if len(items) < 2:
        return []
    for match, item_text in zip(matches, items):
        marker_match = re.search(r"\d+", match.group("marker"))
        if marker_match and int(marker_match.group()) == 0 and NUMERIC_VALUE_CONTINUATION_RE.match(item_text):
            return []

    return [
        _make_list_item_block(block, item_text=item_text, list_kind="ol", class_name_extra="ordered-item")
        for item_text in items
    ]


def _split_inline_bullet_list_items(block: dict) -> list[dict]:
    text = _normalize_bullet_markers(block.get("text", ""))
    matches = list(INLINE_BULLET_MARKER_RE.finditer(text))
    if len(matches) < 2:
        return []

    items = _extract_inline_list_items(text, matches)
    if len(items) < 2:
        return []

    return [
        _make_list_item_block(block, item_text=item_text, list_kind="ul", class_name_extra="bullet-item")
        for item_text in items
    ]

def _split_inline_semicolon_list_items(block: dict) -> list[dict]:
    text = _normalize_text(block.get("text", ""))
    if text.count(";") < 2:
        return []
    if (
        _looks_like_reference_entry_text(text)
        or _looks_like_definition_paragraph(text)
        or _looks_like_quote_paragraph(text)
        or _looks_like_figure_caption(text)
    ):
        return []

    raw_items = [
        _normalize_text(part.strip(" ;"))
        for part in INLINE_SEMICOLON_SPLIT_RE.split(text)
        if _normalize_text(part.strip(" ;"))
    ]
    if len(raw_items) < 3:
        return []

    items: list[str] = []
    ordered_numbers: list[int] = []
    for raw_item in raw_items:
        if len(raw_item) < 2 or len(raw_item) > 140:
            return []
        if REFERENCE_LINK_RE.search(_compact_reference_link_text(raw_item)):
            return []

        ordered_match = ORDERED_LIST_ITEM_RE.match(raw_item)
        if ordered_match and not _looks_like_false_ordered_marker(raw_item):
            number_match = re.search(r"\d+", ordered_match.group("marker"))
            if not number_match:
                return []
            ordered_numbers.append(int(number_match.group()))
            item_text = _normalize_text(ordered_match.group("body"))
        elif _looks_like_bullet_item(raw_item):
            item_text = _strip_leading_bullet(raw_item)
        else:
            item_text = raw_item.strip(" -")

        if len(item_text) < 2 or len(item_text.split()) > 16:
            return []
        if item_text.endswith((".", "!", "?")) and len(item_text.split()) > 8:
            return []
        items.append(item_text)

    list_kind = "ul"
    class_name_extra = "bullet-item"
    if ordered_numbers:
        if len(ordered_numbers) != len(items):
            return []
        sequential_pairs = sum(1 for left, right in zip(ordered_numbers, ordered_numbers[1:]) if right == left + 1)
        if sequential_pairs < max(1, len(ordered_numbers) - 2):
            return []
        list_kind = "ol"
        class_name_extra = "ordered-item"

    return [
        _make_list_item_block(block, item_text=item_text, list_kind=list_kind, class_name_extra=class_name_extra)
        for item_text in items
    ]


def _looks_like_ordered_list_item(text: str) -> bool:
    normalized = _normalize_text(text)
    match = ORDERED_LIST_ITEM_RE.match(normalized)
    if not match:
        return False
    if _looks_like_false_ordered_marker(normalized):
        return False
    body = _normalize_text(match.group("body"))
    if len(body) < 4 or len(body) > 240:
        return False
    if _looks_like_heading_text(body):
        return False
    return True


def _strip_leading_ordered_marker(text: str) -> str:
    normalized = _normalize_text(text)
    match = ORDERED_LIST_ITEM_RE.match(normalized)
    if not match:
        return normalized
    return _normalize_text(match.group("body"))


def _looks_like_false_ordered_marker(text: str) -> bool:
    normalized = _normalize_text(text)
    match = ORDERED_LIST_ITEM_RE.match(normalized)
    if not match:
        return False
    marker_match = re.search(r"\d+", match.group("marker"))
    if not marker_match:
        return False
    marker_number = int(marker_match.group())
    body = _normalize_text(match.group("body"))
    return marker_number == 0 and bool(NUMERIC_VALUE_CONTINUATION_RE.match(body))


def _extract_block_lines(block: dict) -> list[str]:
    fragment_html = block.get("html", "")
    if not fragment_html:
        return []
    wrapper = BeautifulSoup(f"<wrapper>{fragment_html}</wrapper>", "xml")
    return [
        _normalize_text(line)
        for line in wrapper.get_text("\n", strip=True).splitlines()
        if _normalize_text(line)
    ]


def _extract_raw_block_lines(block: dict) -> list[str]:
    fragment_html = block.get("html", "")
    if not fragment_html:
        return []
    wrapper = BeautifulSoup(f"<wrapper>{fragment_html}</wrapper>", "xml")
    root = wrapper.find("wrapper")
    if root is None:
        return []
    for line_break in root.find_all("br"):
        line_break.replace_with("\n")
    return [
        _repair_text_node(line).replace("\xa0", " ").strip()
        for line in root.get_text("", strip=False).splitlines()
        if _normalize_text(line)
    ]


def _extract_definition_items_from_lines(block: dict) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for line in _extract_block_lines(block):
        match = DEFINITION_LINE_RE.match(line)
        if not match:
            return []
        term = _normalize_text(match.group("term"))
        desc = _normalize_text(match.group("desc"))
        if len(term) < 2 or len(desc) < 4:
            return []
        if term.lower() in MINOR_HEADING_WORDS:
            return []
        items.append({"term": term, "desc": desc})
    return items if len(items) >= 2 else []


def _build_definition_list_block(block: dict) -> dict | None:
    text = _normalize_text(block.get("text", ""))
    matches = list(DEFINITION_INLINE_RE.finditer(text))
    items: list[dict[str, str]] = []

    if matches:
        covered = 0
        for match in matches:
            term = _normalize_text(match.group("term"))
            desc = _normalize_text(match.group("desc"))
            if len(term) < 3 or len(desc) < 8 or term.lower() in MINOR_HEADING_WORDS:
                continue
            covered += match.end() - match.start()
            items.append({"term": term, "desc": desc})
        if len(items) < 2 or covered / max(len(text), 1) < 0.45:
            items = []

    if not items:
        items = _extract_definition_items_from_lines(block)

    if len(items) < 2:
        return None

    class_name = _append_class_name(block.get("class_name", "") or "", "definition-list")
    return {
        "type": "definition-list",
        "items": items,
        "text": "; ".join(f'{item["term"]}: {item["desc"]}' for item in items),
        "class_name": class_name,
    }


def _looks_like_table_header_row(row: list[str], body_rows: list[list[str]]) -> bool:
    if not row or any(len(cell) > 42 for cell in row):
        return False
    if not all(re.search(r"[A-Za-zÀ-ÿĀ-ž]", cell) for cell in row):
        return False
    if any(cell.endswith(".") for cell in row):
        return False
    if not body_rows:
        return False
    if any(cell == value for cell, value in zip(row, body_rows[0])):
        return False
    return all(len(cell.split()) <= 5 for cell in row)

def _split_table_cells_from_line(line: str) -> list[str]:
    stripped = (line or "").strip()
    if not stripped:
        return []
    if "|" in stripped:
        cells = [_normalize_text(cell) for cell in stripped.split("|") if _normalize_text(cell)]
    else:
        if not re.search(r"\t|\s{2,}", stripped):
            return []
        cells = [_normalize_text(cell) for cell in re.split(r"\t+|\s{2,}", stripped) if _normalize_text(cell)]
    if len(cells) < 2 or len(cells) > 6:
        return []
    return cells


def _build_table_block(block: dict) -> dict | None:
    rows: list[list[str]] = []
    for line in _extract_raw_block_lines(block):
        cells = _split_table_cells_from_line(line)
        if not cells:
            return None
        rows.append(cells)

    if len(rows) < 2:
        return None

    column_counts = {len(row) for row in rows}
    if len(column_counts) != 1:
        return None

    headers: list[str] = []
    body_rows = rows
    if _looks_like_table_header_row(rows[0], rows[1:]):
        headers = rows[0]
        body_rows = rows[1:]
    if not body_rows:
        return None

    return {
        "type": "table",
        "headers": headers,
        "rows": body_rows,
        "text": " | ".join(rows[0]),
        "class_name": _append_class_name(block.get("class_name", "") or "", "semantic-table"),
    }

def _looks_like_reference_section_title(text: str) -> bool:
    normalized = _normalize_key(text)
    folded = _normalize_key(
        "".join(
            char
            for char in unicodedata.normalize("NFKD", _normalize_text(text))
            if not unicodedata.combining(char)
        )
    )
    candidate_keys = {candidate for candidate in {normalized, folded} if candidate}
    if not candidate_keys:
        return False
    markers = {
        "references",
        "selected references",
        "bibliography",
        "resources",
        "sources",
        "source list",
        "notes",
        "endnotes",
        "bibliografia",
        "zrodla",
        "źródła",
        "odnosniki",
        "odnośniki",
    }
    if candidate_keys & markers:
        return True
    return any(
        candidate.startswith(f"{marker} ") or candidate.endswith(f" {marker}")
        for candidate in candidate_keys
        for marker in {"references", "bibliography", "resources", "sources", "bibliografia", "zrodla", "źródła"}
    )


def _compact_reference_link_text(text: str) -> str:
    compact = _normalize_text(text)
    compact = REFERENCE_REVIEW_LABEL_RE.sub("", compact)
    compact = re.sub(r"(?i)\bhttps\s*:\s*/\s*/\s*", "https://", compact)
    compact = re.sub(r"(?i)\bhttp\s*:\s*/\s*/\s*", "http://", compact)
    compact = re.sub(r"(?i)\bwww\s*\.\s*", "www.", compact)
    compact = re.sub(r"(?i)\bdoi\s*:\s*", "doi:", compact)

    for _ in range(4):
        updated = re.sub(
            r"\b((?:https?://|www\.)[^\s<>()]*[A-Za-z0-9-])\s*\.\s*([a-z0-9-]+)",
            r"\1.\2",
            compact,
        )
        updated = re.sub(
            r"(?i)\b((?:https?://|www\.)\S+)\s*:\s*(\d{2,5})(?=(?:/|\b))",
            r"\1:\2",
            updated,
        )
        updated = re.sub(
            r"(?i)\b((?:https?://|www\.)\S+)\s*/\s*([A-Za-z0-9._~%!$&'()*+,;=:@-]+)",
            r"\1/\2",
            updated,
        )
        updated = re.sub(
            r"(?i)\b((?:https?://|www\.)\S+)\s*([?#&=])\s*([A-Za-z0-9._~%!$'()*+,;:@/-]+)",
            r"\1\2\3",
            updated,
        )
        updated = re.sub(
            r"(?i)\bdoi:10\.\s*(\d{4,9})\s*/\s*([A-Za-z0-9./;()_:-]+)",
            r"doi:10.\1/\2",
            updated,
        )
        updated = REFERENCE_GLUE_BOUNDARY_RE.sub(r"\1 ", updated)
        updated = REFERENCE_DOI_GLUE_BOUNDARY_RE.sub(r"\1 ", updated)
        if updated == compact:
            break
        compact = updated

    return compact


def _extract_reference_url_continuation_prefix(text: str) -> str:
    match = REFERENCE_URL_CONTINUATION_PREFIX_RE.match(text or "")
    if not match:
        return ""
    prefix = match.group(0)
    stripped = prefix.lstrip()
    if stripped.startswith(".") and len(stripped) > 1 and not (stripped[1].islower() or stripped[1].isdigit()):
        return ""
    return prefix


def _merge_reference_fragment_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    for raw_line in lines:
        line = _repair_text_node(raw_line).replace("\xa0", " ").strip()
        if not line:
            continue
        if merged:
            prefix = _extract_reference_url_continuation_prefix(line)
            if prefix and _extract_reference_link_token(merged[-1]):
                merged[-1] = _compact_reference_link_text(f"{merged[-1]}{prefix}")
                remainder = line[len(prefix):].strip()
                if remainder:
                    merged.append(remainder)
                continue
        merged.append(line)
    return merged


def _trim_reference_link_token(token: str) -> str:
    trimmed = (token or "").strip().strip("<>\"'")
    while trimmed and trimmed[-1] in ".,;:!?":
        trimmed = trimmed[:-1]
    for closing, opening in ((")", "("), ("]", "["), ("}", "{")):
        while trimmed.endswith(closing) and trimmed.count(closing) > trimmed.count(opening):
            trimmed = trimmed[:-1]
    return trimmed


def _split_raw_reference_link_candidate(raw: str) -> list[str]:
    compact = _trim_reference_link_token(raw)
    if not compact:
        return []
    starts: list[int] = []
    for match in REFERENCE_URL_START_RE.finditer(compact):
        start = match.start()
        token = match.group(0).lower()
        if token == "www." and start >= 3 and compact[start - 3 : start] == "://":
            continue
        starts.append(start)
    if len(starts) <= 1:
        return [compact]
    pieces: list[str] = []
    starts.append(len(compact))
    for start, end in zip(starts, starts[1:]):
        piece = _trim_reference_link_token(compact[start:end])
        if piece:
            pieces.append(piece)
    return pieces or [compact]


def _reference_tail_window(text: str) -> str:
    if not text:
        return ""
    next_link = REFERENCE_URL_START_RE.search(text)
    return text[: next_link.start()] if next_link else text


def _should_extend_reference_candidate(raw: str) -> bool:
    compact = _trim_reference_link_token(raw)
    if not compact:
        return False
    if compact.endswith(("/", "-")):
        return True
    if re.search(r"%(?:[0-9A-Fa-f]{1,2})?$", compact):
        return True
    return compact.count("://") > 1


def _compact_reference_tail_fragment(text: str) -> str:
    fragment = _normalize_text(text).replace("\xa0", " ")
    if not fragment:
        return ""
    fragment = re.sub(r"(?<=%[0-9A-Fa-f]{2})\s+(?=[A-Za-z0-9])", "", fragment)
    fragment = re.sub(
        r"(?<=[A-Za-z0-9._~%!$&'()*+,;=:@/?#-])\s+(?=[A-Za-z0-9._~%!$&'()*+,;=:@/?#-])",
        "",
        fragment,
    )
    return fragment.strip(" ;,:-")


def _extend_reference_candidate_from_tail(raw: str, tail: str) -> str:
    window = _reference_tail_window(tail)
    if not window:
        return raw
    extension = _compact_reference_tail_fragment(window)
    if not extension or len(extension) < 3:
        return raw
    return f"{raw}{extension}"


def _collect_reference_link_candidates(text: str) -> list[dict[str, object]]:
    compact = _compact_reference_link_text(text)
    candidates: list[dict[str, object]] = []
    seen_ranges: set[tuple[int, int, str]] = set()
    for match in REFERENCE_RAW_LINK_RE.finditer(compact):
        raw = _trim_reference_link_token(match.group(0))
        if not raw:
            continue
        pieces = _split_raw_reference_link_candidate(raw)
        relative_cursor = 0
        for piece_index, piece in enumerate(pieces):
            if not piece:
                continue
            if piece_index == len(pieces) - 1 and _should_extend_reference_candidate(piece):
                piece = _extend_reference_candidate_from_tail(piece, compact[match.end() :])
            relative_start = raw.find(piece[: min(len(piece), len(raw))], relative_cursor)
            if relative_start < 0:
                relative_start = relative_cursor
            start = match.start() + relative_start
            end = start + len(piece)
            relative_cursor = max(relative_start + len(piece), relative_cursor)
            marker = (start, end, piece)
            if marker in seen_ranges:
                continue
            seen_ranges.add(marker)
            normalized = _normalize_reference_href(piece)
            candidates.append(
                {
                    "raw": piece,
                    "normalized": normalized,
                    "start": start,
                    "end": end,
                }
            )
    return candidates


def _strip_reference_link_candidates(text: str) -> str:
    stripped = REFERENCE_RAW_LINK_RE.sub(" ", _compact_reference_link_text(text))
    return _normalize_text(stripped).strip(" ;,:-")


def _reference_requires_review(entry: dict[str, object]) -> bool:
    return bool(entry.get("raw_unresolved_fragments")) or not bool(entry.get("links") or entry.get("link"))


def _is_reference_href_ambiguous(href: str) -> bool:
    normalized = _trim_reference_link_token(_compact_reference_link_text(href))
    if not normalized:
        return True
    lowered = normalized.lower()
    if lowered.startswith("https://doi.org/") or lowered.startswith("http://doi.org/"):
        suffix = normalized.split("doi.org/", 1)[-1].strip("/")
        return not suffix or not suffix.startswith("10.")
    if lowered.startswith("doi:"):
        suffix = normalized[4:].strip("/")
        return not suffix or not suffix.startswith("10.")
    if lowered.startswith("www."):
        host = re.split(r"[/?#:]", normalized[4:], maxsplit=1)[0].lower()
        if not host or "." not in host:
            return True
        return len(host.rsplit(".", 1)[-1]) < 2
    if lowered.startswith(("https://", "http://")):
        try:
            parsed = urlsplit(normalized)
        except Exception:
            return True
        host = (parsed.hostname or "").lower()
        if not host or host in {"the", "www"}:
            return True
        if "." not in host:
            return True
        if len(host.rsplit(".", 1)[-1]) < 2:
            return True
        if parsed.scheme and not parsed.netloc:
            return True
    return False


def _extract_reference_link_token(text: str) -> str:
    tokens = _extract_reference_link_tokens(text)
    return tokens[0] if tokens else ""


def _extract_reference_link_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for candidate in _collect_reference_link_candidates(text):
        normalized = str(candidate.get("normalized", "") or "")
        if normalized and normalized not in seen:
            seen.add(normalized)
            tokens.append(normalized)
    return tokens


def _extract_unresolved_reference_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    seen: set[str] = set()
    for candidate in _collect_reference_link_candidates(text):
        raw = _normalize_text(str(candidate.get("raw", "") or ""))
        normalized = str(candidate.get("normalized", "") or "")
        if raw and not normalized and raw not in seen:
            seen.add(raw)
            fragments.append(raw)
    return fragments


def _normalize_reference_href(token: str) -> str:
    compact = _trim_reference_link_token(_compact_reference_link_text(token))
    compact = re.sub(r"\s+", "", compact)
    compact = re.sub(r"(?i)^(https?://)(?:https?://)+", r"\1", compact)
    compact = re.sub(r"(?i)^(www\.)(?:www\.)+", r"\1", compact)
    compact = re.sub(r"%(?:[0-9A-Fa-f])?$", "", compact).rstrip()
    if not compact or _is_reference_href_ambiguous(compact):
        return ""
    lowered = compact.lower()
    if lowered.startswith("doi:"):
        return f"https://doi.org/{compact[4:]}"
    if lowered.startswith("www."):
        return f"https://{compact}"
    return compact


def _reference_numeric_value(source_id: str) -> int | None:
    normalized = _normalize_text(source_id)
    if not normalized:
        return None
    match = re.search(r"\d{1,3}", normalized)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _reference_display_id_from_number(number: int, *, style: str = "bracket") -> str:
    if style == "dot":
        return f"{number}."
    return f"[{number}]"


def _infer_reference_name_fields(title: str) -> tuple[str, str]:
    normalized = _normalize_text(title)
    if not normalized:
        return "", ""
    if len(normalized.split()) <= 8:
        return normalized, normalized
    separators = (r"\s+[—–-]\s+", r":\s+")
    for pattern in separators:
        match = re.search(pattern, normalized)
        if not match:
            continue
        source_name = normalized[: match.start()].strip(" ;,:-")
        source_title = normalized.strip(" ;,:-")
        if source_name and source_title:
            return source_name, source_title
    return "", normalized


def _compute_reference_confidence(
    *,
    source_id: str,
    title: str,
    links: list[str],
    unresolved_fragments: list[str],
    repaired_link_count: int,
) -> float:
    score = 0.2 if title else 0.0
    if source_id:
        score += 0.1
    if links:
        score += 0.4
    if title and not _looks_like_invalid_reference_title(title):
        score += 0.15
    if repaired_link_count:
        score += min(0.1, 0.04 * repaired_link_count)
    if unresolved_fragments:
        score -= min(0.35, 0.12 * len(unresolved_fragments))
    if not links:
        score -= 0.2
    return max(0.0, min(0.99, round(score, 3)))


def _is_numeric_reference_id(source_id: str) -> bool:
    return bool(REFERENCE_NUMERIC_ID_RE.fullmatch(_normalize_text(source_id)))


def _looks_like_reference_entry_text(text: str) -> bool:
    normalized = _compact_reference_link_text(text)
    if not normalized or len(normalized) > 320:
        return False
    if _looks_like_heading_text(normalized) or _looks_like_figure_caption(normalized):
        return False
    if not _extract_reference_link_tokens(normalized):
        return False
    if REFERENCE_ENTRY_ID_RE.match(normalized):
        return True
    if len(normalized.split()) <= 26:
        return True
    return bool(re.search(r"(?i)\b(?:doi|retrieved|accessed|available|www\.)\b", normalized))


def _split_reference_entries_from_text(text: str) -> list[str]:
    normalized = _compact_reference_link_text(text)
    if not normalized:
        return []

    segments: list[str] = []
    for line in re.split(r"\s*\n+\s*", normalized):
        stripped_line = line.strip(" ;")
        if not stripped_line:
            continue

        line_parts = [part.strip() for part in INLINE_SEMICOLON_SPLIT_RE.split(stripped_line) if part.strip()]
        for part in line_parts or [stripped_line]:
            inline_matches = list(REFERENCE_INLINE_ID_RE.finditer(part))
            if len(inline_matches) >= 2 and len(list(REFERENCE_LINK_RE.finditer(part))) >= 2:
                positions = [match.start("id") for match in inline_matches]
                positions.append(len(part))
                for start, end in zip(positions, positions[1:]):
                    candidate = part[start:end].strip(" ;")
                    if candidate:
                        segments.append(candidate)
                continue
            segments.append(part)

    deduped: list[str] = []
    for segment in segments:
        if segment and segment not in deduped:
            deduped.append(segment)
    return deduped


def _split_reference_title_and_description(text: str) -> tuple[str, str]:
    normalized = _normalize_text(text).strip(" ;,:-")
    if not normalized:
        return "", ""

    slash_match = re.search(r"\s+/\s+", normalized)
    if slash_match:
        title = normalized[: slash_match.start()].strip(" ;,:-")
        description = normalized[slash_match.end() :].strip(" ;,:-")
        if title and description:
            return title, description

    separators = (
        r":\s+",
        r"\.\s+(?=[A-ZÀ-ÿĀ-ž0-9])",
        r",\s+(?=[A-ZÀ-ÿĀ-ž0-9])",
        r"\s+[—–-]\s+",
    )
    for pattern in separators:
        match = re.search(pattern, normalized)
        if not match:
            continue
        title = normalized[: match.start()].strip(" ;,:-")
        description = normalized[match.end() :].strip(" ;,:-")
        if title and description:
            return title, description
    return normalized, ""


def _looks_like_invalid_reference_title(title: str) -> bool:
    normalized = _normalize_text(title)
    if not normalized or len(normalized) < 2:
        return True
    lowered = normalized.lower()
    if lowered in {"reference requires review", "source requires review"}:
        return False
    return bool(re.match(r"(?i)^(?:https?://|www\.|doi:)", lowered))


def _parse_reference_entry(text: str) -> dict[str, object] | None:
    normalized = _compact_reference_link_text(text).strip(" ;")
    if not normalized:
        return None

    source_id = ""
    body = normalized
    id_match = REFERENCE_ENTRY_ID_RE.match(normalized)
    if id_match:
        source_id = _normalize_text(id_match.group("id"))
        body = _normalize_text(id_match.group("body"))

    compact_body = _compact_reference_link_text(body)
    candidates = _collect_reference_link_candidates(compact_body)
    links: list[str] = []
    seen_links: set[str] = set()
    for candidate in candidates:
        normalized_link = str(candidate.get("normalized", "") or "")
        if normalized_link and normalized_link not in seen_links:
            seen_links.add(normalized_link)
            links.append(normalized_link)
    unresolved_fragments = _extract_unresolved_reference_fragments(compact_body)
    if not links and not unresolved_fragments:
        return None
    first_valid = next((candidate for candidate in candidates if candidate.get("normalized")), None)

    if first_valid is not None:
        lead_text = _normalize_text(compact_body[: int(first_valid["start"])].strip(" ;,:-"))
        trailing_source = compact_body[int(first_valid["end"]) :]
        trailing_text = _strip_reference_link_candidates(trailing_source)
    else:
        lead_text = _strip_reference_link_candidates(compact_body)
        trailing_text = ""

    title, description = _split_reference_title_and_description(lead_text)
    if trailing_text:
        description = _normalize_text(" ".join(part for part in [description, trailing_text] if part))
    if not title:
        stripped_body = _strip_reference_link_candidates(compact_body)
        title, fallback_description = _split_reference_title_and_description(stripped_body)
        if fallback_description and not description:
            description = fallback_description
    if _looks_like_invalid_reference_title(title):
        return None
    source_name, source_title = _infer_reference_name_fields(title)
    repaired_link_count = sum(
        1
        for candidate in candidates
        if candidate.get("normalized") and str(candidate.get("raw", "")) != str(candidate.get("normalized", ""))
    )
    confidence = _compute_reference_confidence(
        source_id=source_id,
        title=source_title or title,
        links=links,
        unresolved_fragments=unresolved_fragments,
        repaired_link_count=repaired_link_count,
    )

    entry = {
        "source_id": source_id,
        "source_name": source_name,
        "source_title": source_title or title,
        "title": source_title or title,
        "description": description,
        "link": links[0] if links else "",
        "links": links,
        "raw_unresolved_fragments": unresolved_fragments,
        "review_needed": bool(unresolved_fragments) or not bool(links),
        "repaired_link_count": repaired_link_count,
        "confidence": confidence,
        "original_text": normalized,
        "original_fragments": [normalized],
    }
    return entry


def _build_unresolved_reference_entry(text: str) -> dict[str, object] | None:
    normalized = _compact_reference_link_text(text).strip(" ;")
    if not normalized:
        return None

    source_id = ""
    body = normalized
    id_match = REFERENCE_ENTRY_ID_RE.match(normalized)
    if id_match:
        source_id = _normalize_text(id_match.group("id"))
        body = _normalize_text(id_match.group("body"))

    unresolved_fragments = _extract_unresolved_reference_fragments(body)
    if not unresolved_fragments:
        return None
    visible_text = _strip_reference_link_candidates(body)
    title, description = _split_reference_title_and_description(visible_text)
    if _looks_like_invalid_reference_title(title):
        if not source_id and not description and unresolved_fragments:
            return None
        title = "Reference requires review"
    source_name, source_title = _infer_reference_name_fields(title)
    return {
        "source_id": source_id,
        "source_name": source_name,
        "source_title": source_title or title,
        "title": source_title or title,
        "description": description,
        "link": "",
        "links": [],
        "raw_unresolved_fragments": unresolved_fragments,
        "review_needed": True,
        "repaired_link_count": 0,
        "confidence": _compute_reference_confidence(
            source_id=source_id,
            title=source_title or title,
            links=[],
            unresolved_fragments=unresolved_fragments,
            repaired_link_count=0,
        ),
        "original_text": normalized,
        "original_fragments": [normalized],
    }


def _parse_existing_reference_entry_block(block: dict) -> dict[str, object] | None:
    fragment_html = block.get("html", "")
    if not fragment_html:
        return None
    wrapper = BeautifulSoup(f"<wrapper>{fragment_html}</wrapper>", "xml")
    root = wrapper.find("wrapper")
    if root is None:
        return None

    label = root.find(class_="reference-label")
    review_note = root.find(class_="reference-review-note")
    display_id = ""
    source_id = ""
    title = ""
    description = ""
    links: list[str] = []
    unresolved_fragments: list[str] = []

    id_node = root.find(class_="reference-id")
    if id_node is not None:
        source_id = _normalize_text(id_node.get_text(" ", strip=True))
        display_id = source_id
    title_node = root.find(class_="reference-title")
    if title_node is not None:
        title = _normalize_text(title_node.get_text(" ", strip=True))
    description_node = root.find(class_="reference-description")
    if description_node is not None:
        description = _normalize_text(description_node.get_text(" ", strip=True))

    for anchor in root.find_all("a", href=True):
        normalized_href = _normalize_reference_href(anchor.get("href", ""))
        if normalized_href and normalized_href not in links:
            links.append(normalized_href)

    if not title and label is not None:
        title = _strip_reference_link_candidates(label.get_text(" ", strip=True))
        if source_id and title.startswith(source_id):
            title = _normalize_text(title[len(source_id) :].strip(" -:;,."))
    if review_note is not None:
        note_text = _normalize_text(review_note.get_text(" ", strip=True))
        if note_text:
            unresolved_fragments.append(note_text)
    if not title and not links and not unresolved_fragments:
        return None

    source_name, source_title = _infer_reference_name_fields(title)
    return {
        "source_id": source_id,
        "display_source_id": display_id,
        "source_name": source_name,
        "source_title": source_title or title,
        "title": source_title or title,
        "description": description,
        "link": links[0] if links else "",
        "links": links,
        "raw_unresolved_fragments": unresolved_fragments,
        "review_needed": bool(unresolved_fragments) or not bool(links),
        "repaired_link_count": 0,
        "confidence": _compute_reference_confidence(
            source_id=source_id,
            title=source_title or title,
            links=links,
            unresolved_fragments=unresolved_fragments,
            repaired_link_count=0,
        ),
        "original_text": _normalize_text(root.get_text(" ", strip=True)),
        "original_fragments": [_normalize_text(root.get_text(" ", strip=True))],
    }


def _looks_like_reference_descriptor_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 260:
        return False
    if _looks_like_heading_text(normalized) or _looks_like_figure_caption(normalized):
        return False
    if _extract_reference_link_tokens(normalized) or _extract_unresolved_reference_fragments(normalized):
        return False
    if len(normalized.split()) < 3:
        return False
    return bool(re.search(r"[-:]", normalized) or len(normalized.split()) <= 22)


def _build_reference_entries_from_descriptor_links(
    descriptors: list[dict],
    link_block: dict,
) -> list[dict[str, object]]:
    urls = _extract_reference_link_tokens(link_block.get("text", ""))
    if not urls:
        urls = _extract_reference_link_tokens(link_block.get("html", ""))
    if not urls:
        return []
    if descriptors and len(urls) > len(descriptors) * 2:
        return []

    entries: list[dict[str, object]] = []
    descriptor_queue = [dict(item) for item in descriptors]
    for url in urls:
        descriptor = descriptor_queue.pop(0) if descriptor_queue else {}
        descriptor_text = _normalize_text(str(descriptor.get("text", "") or ""))
        if descriptor_text:
            parsed = _parse_reference_entry(descriptor_text) or _build_unresolved_reference_entry(descriptor_text)
        else:
            parsed = None
        if parsed is None:
            source_id = ""
            descriptor_body = descriptor_text
            id_match = REFERENCE_ENTRY_ID_RE.match(descriptor_text)
            if id_match:
                source_id = _normalize_text(id_match.group("id"))
                descriptor_body = _normalize_text(id_match.group("body"))
            title, description = _split_reference_title_and_description(descriptor_body)
            source_name, source_title = _infer_reference_name_fields(title)
            parsed = {
                "source_id": source_id,
                "source_name": source_name,
                "source_title": source_title or title,
                "title": source_title or title,
                "description": description,
                "link": url,
                "links": [url],
                "raw_unresolved_fragments": [],
                "review_needed": False,
                "repaired_link_count": 0,
                "confidence": _compute_reference_confidence(
                    source_id=source_id,
                    title=source_title or title,
                    links=[url],
                    unresolved_fragments=[],
                    repaired_link_count=0,
                ),
                "original_text": descriptor_text or url,
                "original_fragments": [descriptor_text or url],
            }
        else:
            existing_links = [str(link) for link in (parsed.get("links") or []) if str(link)]
            if url not in existing_links:
                existing_links.append(url)
            parsed["links"] = existing_links
            parsed["link"] = existing_links[0] if existing_links else url
            parsed["review_needed"] = bool(parsed.get("raw_unresolved_fragments")) or not bool(existing_links)
            parsed["confidence"] = _compute_reference_confidence(
                source_id=str(parsed.get("source_id", "") or ""),
                title=str(parsed.get("source_title", "") or parsed.get("title", "") or ""),
                links=existing_links,
                unresolved_fragments=list(parsed.get("raw_unresolved_fragments") or []),
                repaired_link_count=int(parsed.get("repaired_link_count", 0) or 0),
            )
        entries.append(parsed)

    return [entry for entry in entries if entry.get("links") or entry.get("raw_unresolved_fragments")]


def _extract_reference_block_lines(block: dict) -> list[str]:
    fragment_html = block.get("html", "")
    if not fragment_html:
        fallback_text = _normalize_text(block.get("text", ""))
        return [fallback_text] if fallback_text else []

    wrapper = BeautifulSoup(f"<wrapper>{fragment_html}</wrapper>", "xml")
    root = wrapper.find("wrapper")
    if root is None:
        return []
    for line_break in root.find_all("br"):
        line_break.replace_with("\n")
    for anchor in root.find_all("a"):
        href = _normalize_text(anchor.get("href", ""))
        anchor_text = _normalize_text(anchor.get_text(" ", strip=True))
        is_generic_label = bool(re.fullmatch(r"(?i)(?:official\s+source|source|details|detail|link|website|web)", anchor_text))
        parts = [anchor_text] if anchor_text and not is_generic_label else []
        if href and re.match(r"(?i)^(?:https?://|www\.|doi:10\.)", href):
            normalized_href = _normalize_reference_href(href)
            if normalized_href and normalized_href not in parts:
                parts.append(normalized_href)
        replacement = " ".join(part for part in parts if part)
        anchor.replace_with(replacement or anchor_text or href)
    return [
        _repair_text_node(line).replace("\xa0", " ").strip()
        for line in root.get_text("", strip=False).splitlines()
        if _normalize_text(line)
    ]


def _extract_reference_entries_from_block(block: dict) -> list[dict[str, object]]:
    block_type = block.get("type")
    if block_type == "table":
        entries: list[dict[str, object]] = []
        for row in block.get("rows") or []:
            cells = [_normalize_text(cell) for cell in row if _normalize_text(cell)]
            if not cells:
                continue
            links: list[str] = []
            unresolved_fragments: list[str] = []
            non_link_cells: list[str] = []
            for cell in cells:
                cell_links = _extract_reference_link_tokens(cell)
                if cell_links:
                    links.extend(link for link in cell_links if link not in links)
                unresolved_fragments.extend(
                    fragment
                    for fragment in _extract_unresolved_reference_fragments(cell)
                    if fragment not in unresolved_fragments
                )
                if not cell_links or _strip_reference_link_candidates(cell):
                    stripped = _strip_reference_link_candidates(cell)
                    if stripped:
                        non_link_cells.append(stripped)
            source_id = ""
            if non_link_cells and REFERENCE_ENTRY_ID_RE.match(f"{non_link_cells[0]} placeholder"):
                source_id = non_link_cells.pop(0)
            title = non_link_cells.pop(0) if non_link_cells else ""
            description = " ".join(non_link_cells).strip()
            if title and not description:
                title, description = _split_reference_title_and_description(title)
            if _looks_like_invalid_reference_title(title):
                row_line = " | ".join(cells)
                parsed = _parse_reference_entry(row_line) or _build_unresolved_reference_entry(row_line)
                if parsed:
                    entries.append(parsed)
                continue
            source_name, source_title = _infer_reference_name_fields(title)
            parsed = {
                "source_id": source_id,
                "source_name": source_name,
                "source_title": source_title or title,
                "title": source_title or title,
                "description": description,
                "link": links[0] if links else "",
                "links": links,
                "raw_unresolved_fragments": unresolved_fragments,
                "review_needed": bool(unresolved_fragments) or not bool(links),
                "repaired_link_count": 0,
                "confidence": _compute_reference_confidence(
                    source_id=source_id,
                    title=source_title or title,
                    links=links,
                    unresolved_fragments=unresolved_fragments,
                    repaired_link_count=0,
                ),
                "original_text": " | ".join(cells),
                "original_fragments": [" | ".join(cells)],
            }
            if not parsed["links"] and not parsed["raw_unresolved_fragments"]:
                continue
            if parsed:
                entries.append(parsed)
        return entries

    if block_type not in {"paragraph", "list-item"}:
        return []
    class_name = block.get("class_name", "") or ""
    class_tokens = {token for token in class_name.split() if token}
    if "reference-entry" in class_tokens:
        parsed_existing = _parse_existing_reference_entry_block(block)
        return [parsed_existing] if parsed_existing is not None else []

    candidates: list[str] = []
    raw_lines = _merge_reference_fragment_lines(_extract_reference_block_lines(block) or [block.get("text", "")])
    for line in raw_lines:
        split_candidates = _split_reference_entries_from_text(line)
        if split_candidates:
            candidates.extend(split_candidates)
        elif line.strip():
            candidates.append(_compact_reference_link_text(line))

    entries = [entry for candidate in candidates if (entry := _parse_reference_entry(candidate))]
    if not entries and raw_lines:
        joined_block = _compact_reference_link_text(" ".join(raw_lines))
        joined_candidates = _split_reference_entries_from_text(joined_block) or [joined_block]
        entries = [entry for candidate in joined_candidates if (entry := _parse_reference_entry(candidate))]
    if not entries and raw_lines:
        joined_block = _compact_reference_link_text(" ".join(raw_lines))
        unresolved_entry = _build_unresolved_reference_entry(joined_block)
        if unresolved_entry is not None:
            entries = [unresolved_entry]
    if not entries:
        return []

    if len(entries) == 1:
        block_text = _compact_reference_link_text(" ".join(raw_lines) or block.get("text", ""))
        has_explicit_reference_marker = bool(REFERENCE_ENTRY_ID_RE.match(block_text)) and bool(
            _extract_reference_link_token(block_text)
        )
        if (
            not _looks_like_reference_entry_text(block_text)
            and not has_explicit_reference_marker
            and not _reference_requires_review(entries[0])
        ):
            return []

    return entries


def _looks_like_reference_section(blocks: list[dict], *, chapter_title: str) -> bool:
    if _looks_like_reference_section_title(chapter_title):
        return True
    if any(block.get("type") == "heading" and _looks_like_reference_section_title(block.get("text", "")) for block in blocks):
        return False

    paragraph_texts = [
        _compact_reference_link_text(block.get("text", ""))
        for block in blocks
        if block.get("type") in {"paragraph", "list-item"} and _normalize_text(block.get("text", ""))
    ]
    if len(paragraph_texts) < 2:
        return False

    link_count = sum(1 for text in paragraph_texts if _extract_reference_link_token(text))
    reference_like_count = sum(1 for text in paragraph_texts if _looks_like_reference_entry_text(text))
    multi_entry_count = sum(1 for text in paragraph_texts if len(_split_reference_entries_from_text(text)) >= 2)
    short_entry_count = sum(1 for text in paragraph_texts if len(text) <= 260)

    return link_count >= 2 and (reference_like_count + multi_entry_count) >= 2 and short_entry_count >= 2


def _normalize_reference_batch_numbering(entries: list[dict[str, object]], report: dict[str, int]) -> None:
    numeric_values = [_reference_numeric_value(str(entry.get("source_id", "") or "")) for entry in entries]
    numeric_present = [value for value in numeric_values if value is not None]
    if not numeric_present:
        for entry in entries:
            if entry.get("source_id"):
                entry["display_source_id"] = entry.get("source_id", "")
        return

    style = "dot" if any(str(entry.get("source_id", "") or "").strip().endswith(".") for entry in entries if entry.get("source_id")) else "bracket"
    start_value = numeric_present[0]
    if numeric_values[0] is None:
        start_value = max(1, numeric_present[0] - len([value for value in numeric_values[: numeric_values.index(numeric_present[0])] if value is None]))

    repaired_count = 0
    for index, entry in enumerate(entries):
        expected_value = start_value + index
        current_value = _reference_numeric_value(str(entry.get("source_id", "") or ""))
        original_id = _normalize_text(str(entry.get("source_id", "") or ""))
        if current_value == expected_value and original_id:
            entry["display_source_id"] = original_id
            continue
        entry["display_source_id"] = _reference_display_id_from_number(expected_value, style=style)
        entry["numbering_repaired"] = True
        if original_id != entry["display_source_id"]:
            repaired_count += 1
    if repaired_count:
        report["numbering_issue_count"] = report.get("numbering_issue_count", 0) + repaired_count


def _make_reference_list_item_block(source_block: dict, entry: dict[str, object], *, list_kind: str) -> dict:
    cleaned_source_block = dict(source_block)
    source_class_tokens = [token for token in (cleaned_source_block.get("class_name", "") or "").split() if token != "list-item"]
    cleaned_source_block["class_name"] = " ".join(source_class_tokens)
    unresolved_fragments = [
        _normalize_text(str(fragment))
        for fragment in (entry.get("raw_unresolved_fragments") or [])
        if _normalize_text(str(fragment))
    ]
    item_text = _normalize_text(
        " ".join(
            part
            for part in [
                str(entry.get("display_source_id", "") or entry.get("source_id", "") or ""),
                str(entry.get("source_title", "") or entry.get("title", "") or ""),
                str(entry.get("description", "") or ""),
                str(entry.get("link", "") or ""),
                "Link requires manual review" if unresolved_fragments else "",
            ]
            if part
        )
    )
    display_source_id = _normalize_text(str(entry.get("display_source_id", "") or entry.get("source_id", "") or ""))
    source_title = _normalize_text(str(entry.get("source_title", "") or entry.get("title", "") or ""))
    description = _normalize_text(str(entry.get("description", "") or ""))
    label_parts: list[str] = []
    if display_source_id:
        label_parts.append(f'<span class="reference-id"><strong>{html.escape(display_source_id)}</strong></span>')
    if source_title:
        label_parts.append(f'<span class="reference-title">{html.escape(source_title)}</span>')
    if description:
        separator = " " if not label_parts else " - "
        label_parts.append(f'{separator}<span class="reference-description">{html.escape(description)}</span>')
    links = [str(link) for link in (entry.get("links") or []) if str(link)]
    paragraphs: list[str] = []
    if label_parts:
        paragraphs.append(f'<p class="reference-label">{" ".join(label_parts)}</p>')
    link_markup = ""
    if links:
        link_markup = "<br/>".join(
            f'<a class="reference-link" href="{html.escape(link)}">{html.escape(link)}</a>'
            for link in links
        )
        paragraphs.append(f'<p class="reference-links">{link_markup}</p>')
    unresolved_markup = ""
    if unresolved_fragments:
        unresolved_markup = (
            '<p class="reference-review-note"><span class="reference-unresolved">'
            f'{html.escape("Link requires manual review.")}'
            "</span></p>"
        )
    if unresolved_markup:
        paragraphs.append(unresolved_markup)
    item_html = "".join(paragraphs)
    return _make_list_item_block(
        cleaned_source_block,
        item_text=item_text,
        list_kind=list_kind,
        class_name_extra="reference-entry",
        item_html=item_html or html.escape(item_text),
    )


def _rebuild_reference_sections(
    blocks: list[dict],
    *,
    section_context: str = "body",
    chapter_title: str = "",
    reference_report: dict[str, int] | None = None,
    reference_details: list[dict[str, object]] | None = None,
) -> list[dict]:
    has_reference_subsection = any(
        block.get("type") == "heading" and _looks_like_reference_section_title(block.get("text", ""))
        for block in blocks
    )
    if section_context != "references" and not has_reference_subsection and not _looks_like_reference_section(blocks, chapter_title=chapter_title):
        return blocks

    rebuilt: list[dict] = []
    pending_entries: list[tuple[dict, dict[str, object]]] = []
    pending_descriptors: list[dict] = []
    in_reference_subsection = section_context == "references"
    reference_heading_level = 1
    report = reference_report if reference_report is not None else _empty_reference_report()
    counted_open_reference_scope = False

    def start_reference_scope() -> None:
        nonlocal counted_open_reference_scope
        if counted_open_reference_scope:
            return
        report["sections_detected"] = report.get("sections_detected", 0) + 1
        counted_open_reference_scope = True

    def stop_reference_scope() -> None:
        nonlocal counted_open_reference_scope
        counted_open_reference_scope = False

    def flush_pending_descriptors() -> None:
        nonlocal pending_descriptors
        if not pending_descriptors:
            return
        rebuilt.extend(dict(block) for block in pending_descriptors)
        pending_descriptors = []

    def flush_pending_entries() -> None:
        nonlocal pending_entries
        if not pending_entries:
            return

        report["scope_replaced_count"] = report.get("scope_replaced_count", 0) + 1
        report["records_detected"] = report.get("records_detected", 0) + len(pending_entries)
        ids_with_values = [entry.get("source_id", "") for _, entry in pending_entries if entry.get("source_id")]
        numeric_ids = sum(1 for source_id in ids_with_values if _is_numeric_reference_id(source_id))
        list_kind = "ol" if ids_with_values and numeric_ids * 2 >= len(ids_with_values) else "ul"
        pending_entry_dicts = [entry for _, entry in pending_entries]
        _normalize_reference_batch_numbering(pending_entry_dicts, report)
        for source_block, entry in pending_entries:
            if entry.get("source_id") and not entry.get("display_source_id"):
                entry["display_source_id"] = entry.get("source_id", "")
            report["entries_rebuilt"] = report.get("entries_rebuilt", 0) + 1
            links = [str(link) for link in (entry.get("links") or []) if str(link)]
            report["clickable_link_count"] = report.get("clickable_link_count", 0) + len(links)
            report["repaired_link_count"] = report.get("repaired_link_count", 0) + int(
                entry.get("repaired_link_count", 0) or 0
            )
            report["unresolved_fragment_count"] = report.get("unresolved_fragment_count", 0) + len(
                entry.get("raw_unresolved_fragments") or []
            )
            if _reference_requires_review(entry):
                report["review_entry_count"] = report.get("review_entry_count", 0) + 1
            if reference_details is not None:
                reference_details.append(
                    {
                        "source_id": _normalize_text(str(entry.get("source_id", "") or "")),
                        "display_source_id": _normalize_text(str(entry.get("display_source_id", "") or "")),
                        "source_name": _normalize_text(str(entry.get("source_name", "") or "")),
                        "source_title": _normalize_text(str(entry.get("source_title", "") or entry.get("title", "") or "")),
                        "description": _normalize_text(str(entry.get("description", "") or "")),
                        "links": links,
                        "url": links[0] if links else "",
                        "confidence": float(entry.get("confidence", 0.0) or 0.0),
                        "review_flag": bool(_reference_requires_review(entry)),
                        "numbering_repaired": bool(entry.get("numbering_repaired")),
                        "original_fragments": list(entry.get("original_fragments") or []),
                        "raw_unresolved_fragments": list(entry.get("raw_unresolved_fragments") or []),
                        "source_block_text": _normalize_text(str(source_block.get("text", "") or "")),
                        "source_block_html": str(source_block.get("html", "") or ""),
                    }
                )
            rebuilt.append(_make_reference_list_item_block(source_block, entry, list_kind=list_kind))
        pending_entries = []

    if in_reference_subsection:
        start_reference_scope()

    for block in blocks:
        if block.get("type") == "heading":
            heading_level = max(1, int(block.get("level", 1) or 1))
            heading_is_reference = _looks_like_reference_section_title(block.get("text", ""))
            if in_reference_subsection and section_context != "references" and reference_heading_level and heading_level <= reference_heading_level and not heading_is_reference:
                flush_pending_entries()
                flush_pending_descriptors()
                in_reference_subsection = False
                stop_reference_scope()
            if heading_is_reference:
                flush_pending_entries()
                flush_pending_descriptors()
                in_reference_subsection = True
                reference_heading_level = heading_level
                start_reference_scope()
                rebuilt.append(block)
                continue

        if in_reference_subsection:
            entries = _extract_reference_entries_from_block(block)
            if entries:
                flush_pending_descriptors()
                if len(entries) > 1:
                    report["split_record_count"] = report.get("split_record_count", 0) + (len(entries) - 1)
                for entry in entries:
                    pending_entries.append((block, entry))
                continue

            if block.get("type") in {"paragraph", "list-item"} and pending_descriptors:
                descriptor_entries = _build_reference_entries_from_descriptor_links(pending_descriptors, block)
                if descriptor_entries:
                    flush_pending_entries()
                    used_descriptor_count = min(len(pending_descriptors), len(descriptor_entries))
                    if len(descriptor_entries) > 1:
                        report["split_record_count"] = report.get("split_record_count", 0) + (len(descriptor_entries) - 1)
                    for index, entry in enumerate(descriptor_entries):
                        source_block = pending_descriptors[index] if index < used_descriptor_count else block
                        pending_entries.append((source_block, entry))
                    pending_descriptors = pending_descriptors[used_descriptor_count:]
                    continue

            if block.get("type") in {"paragraph", "list-item"} and _looks_like_reference_descriptor_text(block.get("text", "")):
                flush_pending_entries()
                pending_descriptors.append(dict(block))
                continue

            flush_pending_entries()
            if block.get("type") in {"paragraph", "list-item"}:
                unresolved_text = _compact_reference_link_text(block.get("text", ""))
                if unresolved_text and not _looks_like_heading_text(unresolved_text):
                    fallback_entry = _build_unresolved_reference_entry(unresolved_text)
                    if fallback_entry is None:
                        flush_pending_descriptors()
                        rebuilt.append(block)
                        continue
                    flush_pending_descriptors()
                    report["records_detected"] = report.get("records_detected", 0) + 1
                    report["scope_replaced_count"] = report.get("scope_replaced_count", 0) + 1
                    report["review_entry_count"] = report.get("review_entry_count", 0) + 1
                    report["unresolved_fragment_count"] = report.get("unresolved_fragment_count", 0) + len(
                        fallback_entry.get("raw_unresolved_fragments") or []
                    )
                    report["entries_rebuilt"] = report.get("entries_rebuilt", 0) + 1
                    rebuilt.append(
                        _make_reference_list_item_block(
                            block,
                            fallback_entry,
                            list_kind="ul",
                        )
                    )
                    continue

        entries = _extract_reference_entries_from_block(block)
        if entries:
            start_reference_scope()
            flush_pending_descriptors()
            if len(entries) > 1:
                report["split_record_count"] = report.get("split_record_count", 0) + (len(entries) - 1)
            for entry in entries:
                pending_entries.append((block, entry))
            continue

        flush_pending_entries()
        flush_pending_descriptors()
        rebuilt.append(block)

    flush_pending_entries()
    flush_pending_descriptors()
    stop_reference_scope()
    return rebuilt


def _score_pattern_group(patterns: tuple[re.Pattern, ...], text: str) -> int:
    score = 0
    for pattern in patterns:
        score += len(pattern.findall(text)) or int(bool(pattern.search(text)))
    return score


def _infer_knowledge_topic_from_text(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""

    lead_fragment = _split_knowledge_sentences(normalized)
    lead_text = lead_fragment[0] if lead_fragment else normalized[:160]
    for topic_key, opener in KNOWLEDGE_TOPIC_OPENERS.items():
        if opener.search(lead_text):
            return topic_key

    lead_best_topic = ""
    lead_best_score = 0
    for topic_key, patterns in KNOWLEDGE_TOPIC_PATTERNS.items():
        score = _score_pattern_group(patterns, lead_text)
        if score > lead_best_score:
            lead_best_topic = topic_key
            lead_best_score = score
    if lead_best_score >= 2:
        return lead_best_topic

    best_topic = ""
    best_score = 0
    for topic_key, patterns in KNOWLEDGE_TOPIC_PATTERNS.items():
        score = _score_pattern_group(patterns, normalized)
        if score > best_score:
            best_topic = topic_key
            best_score = score

    return best_topic if best_score >= 2 else ""


def _heading_matches_knowledge_topic(heading_text: str, topic_key: str) -> bool:
    if not heading_text or not topic_key:
        return False
    normalized = _normalize_key(heading_text)
    label_key = _normalize_key(KNOWLEDGE_TOPIC_LABELS.get(topic_key, ""))
    if label_key and label_key in normalized:
        return True
    return _infer_knowledge_topic_from_text(heading_text) == topic_key


def _infer_block_knowledge_topic(block: dict) -> str:
    block_type = block.get("type")
    if block_type == "definition-list":
        return "definitions"
    if block_type == "knowledge-section":
        return block.get("topic_key", "")
    if block_type == "table":
        return _infer_knowledge_topic_from_text(" ".join(block.get("headers") or []) + " " + " ".join(block.get("text", "").split()))
    if block_type in {"paragraph", "blockquote", "list-item"}:
        return _infer_knowledge_topic_from_text(block.get("text", ""))
    return ""


def _split_knowledge_sentences(text: str) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []

    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(normalized) if part.strip()]
    if len(sentences) <= 1 and normalized.count(";") >= 2:
        sentences = [part.strip() for part in KNOWLEDGE_INLINE_SPLIT_RE.split(normalized) if part.strip()]
    return sentences


def _split_knowledge_items(sentence: str) -> list[str]:
    normalized = _normalize_text(sentence)
    if not normalized:
        return []
    step_fragments = [part.strip(" ,.-") for part in KNOWLEDGE_STEP_SPLIT_RE.split(normalized) if part.strip(" ,.-")]
    if len(step_fragments) >= 2:
        return step_fragments
    items = [part.strip(" -") for part in KNOWLEDGE_INLINE_SPLIT_RE.split(normalized) if part.strip(" -")]
    return items if len(items) >= 2 else [normalized]


def _classify_knowledge_sentence(sentence: str, *, index: int, total: int) -> tuple[str, bool]:
    normalized = _normalize_text(sentence)
    if not normalized:
        return "", False

    for role in ("example", "business", "how", "what"):
        if any(pattern.search(normalized) for pattern in KNOWLEDGE_SCHEMA_PATTERNS[role]):
            return role, True

    if index == 0:
        return "what", False
    if total >= 4 and index == total - 1 and any(pattern.search(normalized) for pattern in KNOWLEDGE_SCHEMA_PATTERNS["business"]):
        return "business", False
    return "how", False


def _should_render_knowledge_group_as_list(role: str, items: list[str]) -> bool:
    if len(items) < 2:
        return False
    if role == "how":
        return True
    if role in {"example", "business"}:
        return all(len(item) <= 180 for item in items)
    return False


def _knowledge_nav_text(label: str, *, context_text: str = "") -> str:
    context = _normalize_text(context_text)
    if not context:
        return label
    if _normalize_key(label) == _normalize_key(context):
        return label
    return f"{context} - {label}"


def _build_knowledge_section_block(block: dict, *, section_context: str = "body") -> dict | None:
    if section_context not in {"body", "appendix", "glossary"}:
        return None

    text = _normalize_text(block.get("text", ""))
    if len(text) < 220:
        return None

    sentences = _split_knowledge_sentences(text)
    if len(sentences) < 3:
        return None

    groups: list[dict] = []
    explicit_role_count = 0
    for index, sentence in enumerate(sentences):
        role, explicit = _classify_knowledge_sentence(sentence, index=index, total=len(sentences))
        if not role:
            continue
        explicit_role_count += int(explicit)
        if groups and groups[-1]["role"] == role:
            groups[-1]["items"].append(sentence)
            continue
        groups.append({"role": role, "items": [sentence]})

    if len(groups) < 2 and explicit_role_count < 2:
        return None

    topic_key = _infer_knowledge_topic_from_text(text)
    rendered_groups: list[dict] = []
    for group in groups:
        items: list[str] = []
        for sentence in group["items"]:
            items.extend(_split_knowledge_items(sentence))
        items = [_normalize_text(item) for item in items if _normalize_text(item)]
        if not items:
            continue
        rendered_groups.append(
            {
                "role": group["role"],
                "items": items,
                "render_as_list": _should_render_knowledge_group_as_list(group["role"], items),
            }
        )

    if len(rendered_groups) < 2:
        return None

    return {
        "type": "knowledge-section",
        "text": text,
        "class_name": block.get("class_name", "") or "",
        "topic_key": topic_key,
        "groups": rendered_groups,
    }


def _render_knowledge_section_blocks(
    block: dict,
    *,
    current_heading_text: str,
    current_heading_level: int,
    active_topic: str,
) -> tuple[list[dict], str]:
    emitted: list[dict] = []
    base_heading_level = max(1, int(current_heading_level or 1))
    topic_key = block.get("topic_key", "") or ""
    topic_label = KNOWLEDGE_TOPIC_LABELS.get(topic_key, "")
    should_insert_topic_heading = bool(
        topic_label and base_heading_level < 3 and active_topic != topic_key and not _heading_matches_knowledge_topic(current_heading_text, topic_key)
    )

    if should_insert_topic_heading:
        topic_level = min(base_heading_level + 1, 3)
        emitted.append(
            {
                "type": "heading",
                "text": topic_label,
                "nav_text": _knowledge_nav_text(topic_label, context_text=current_heading_text),
                "level": topic_level,
                "id": _slugify(_knowledge_nav_text(topic_label, context_text=current_heading_text)),
            }
        )
        active_topic = topic_key
        working_heading_level = topic_level
        context_text = topic_label
    else:
        working_heading_level = base_heading_level
        context_text = topic_label or current_heading_text
        if topic_key:
            active_topic = topic_key

    schema_heading_level = min(working_heading_level + 1, 3)
    render_schema_headings = working_heading_level < 3
    base_class_name = block.get("class_name", "") or ""

    for group in block.get("groups", []):
        role = group.get("role", "")
        items = group.get("items") or []
        if not role or not items:
            continue

        label = KNOWLEDGE_SCHEMA_LABELS.get(role, "")
        role_class = _append_class_name(base_class_name, f"knowledge-body knowledge-{role}")
        if label:
            if render_schema_headings:
                emitted.append(
                    {
                        "type": "heading",
                        "text": label,
                        "nav_text": _knowledge_nav_text(label, context_text=context_text),
                        "level": schema_heading_level,
                        "id": _slugify(_knowledge_nav_text(label, context_text=context_text)),
                    }
                )
            else:
                emitted.append(
                    {
                        "type": "paragraph",
                        "text": label,
                        "html": html.escape(label),
                        "class_name": _append_class_name(base_class_name, "kicker knowledge-kicker"),
                    }
                )

        if group.get("render_as_list"):
            list_kind = "ol" if role == "how" and any(KNOWLEDGE_STEP_RE.search(item) for item in items) else "ul"
            for item in items:
                emitted.append(
                    {
                        "type": "list-item",
                        "text": item,
                        "html": html.escape(item),
                        "class_name": _append_class_name(base_class_name, f"knowledge-point knowledge-{role}"),
                        "list_kind": list_kind,
                    }
                )
            continue

        paragraph_text = _normalize_text(" ".join(items))
        paragraph_block = {
            "type": "paragraph",
            "text": paragraph_text,
            "html": html.escape(paragraph_text),
            "class_name": role_class,
        }
        emitted.extend(_split_long_paragraph_block(paragraph_block))

    return emitted or [block], active_topic


def _rebuild_knowledge_structure(blocks: list[dict], *, section_context: str = "body") -> list[dict]:
    if section_context not in {"body", "appendix", "glossary"}:
        return blocks

    rebuilt: list[dict] = []
    current_heading_text = ""
    current_heading_level = 1
    active_topic = ""

    for block in blocks:
        block_type = block.get("type")
        if block_type == "heading":
            rebuilt.append(block)
            current_heading_text = _normalize_text(block.get("text", ""))
            current_heading_level = max(1, int(block.get("level", 1) or 1))
            active_topic = _infer_knowledge_topic_from_text(current_heading_text) if current_heading_level <= 3 else ""
            continue

        if block_type == "knowledge-section":
            rendered_blocks, active_topic = _render_knowledge_section_blocks(
                block,
                current_heading_text=current_heading_text,
                current_heading_level=current_heading_level,
                active_topic=active_topic,
            )
            rebuilt.extend(rendered_blocks)
            continue

        topic_key = _infer_block_knowledge_topic(block)
        if (
            topic_key
            and current_heading_level < 3
            and active_topic != topic_key
            and not _heading_matches_knowledge_topic(current_heading_text, topic_key)
        ):
            topic_label = KNOWLEDGE_TOPIC_LABELS.get(topic_key, "")
            if topic_label:
                rebuilt.append(
                    {
                        "type": "heading",
                        "text": topic_label,
                        "nav_text": _knowledge_nav_text(topic_label, context_text=current_heading_text),
                        "level": min(current_heading_level + 1, 3),
                        "id": _slugify(_knowledge_nav_text(topic_label, context_text=current_heading_text)),
                    }
                )
                active_topic = topic_key
                current_heading_text = topic_label
                current_heading_level = min(current_heading_level + 1, 3)

        rebuilt.append(block)

    return rebuilt


def _expand_semantic_blocks(blocks: list[dict], *, section_context: str = "body") -> list[dict]:
    expanded: list[dict] = []

    for block in blocks:
        if block.get("type") != "paragraph":
            expanded.append(block)
            continue

        class_name = block.get("class_name", "") or ""
        text = _normalize_text(block.get("text", ""))
        if not text:
            expanded.append(block)
            continue

        if section_context == "contents" and _looks_like_contents_entry(text):
            expanded.append(
                {
                    **block,
                    "type": "list-item",
                    "class_name": _append_class_name(class_name, "toc-entry"),
                    "list_kind": "ul",
                }
            )
            continue

        if section_context == "references":
            expanded.append(block)
            continue

        table_block = _build_table_block(block)
        if table_block:
            expanded.append(table_block)
            continue

        definition_block = _build_definition_list_block(block)
        if definition_block:
            expanded.append(definition_block)
            continue

        inline_ordered_list = _split_inline_ordered_list_items(block)
        if inline_ordered_list:
            expanded.extend(inline_ordered_list)
            continue

        inline_bullet_list = _split_inline_bullet_list_items(block)
        if inline_bullet_list:
            expanded.extend(inline_bullet_list)
            continue

        semicolon_list = _split_inline_semicolon_list_items(block)
        if semicolon_list:
            expanded.extend(semicolon_list)
            continue

        if section_context == "index" or _looks_like_index_paragraph(text):
            list_blocks = _split_index_like_entries(block)
            if list_blocks:
                expanded.extend(list_blocks)
                continue

        if _looks_like_ordered_list_item(text):
            expanded.append(
                _make_list_item_block(
                    block,
                    item_text=_strip_leading_ordered_marker(text),
                    list_kind="ol",
                    class_name_extra="ordered-item",
                )
            )
            continue

        if _looks_like_bullet_item(text):
            expanded.append(_make_list_item_block(block, item_text=_strip_leading_bullet(text), class_name_extra="bullet-item"))
            continue

        if _should_skip_semantic_expansion(block, class_name, text):
            expanded.append(block)
            continue

        if _looks_like_quote_paragraph(text):
            expanded.append(
                {
                    **block,
                    "type": "blockquote",
                    "class_name": _append_class_name(class_name, "source-quote"),
                }
            )
            continue

        knowledge_block = _build_knowledge_section_block(block, section_context=section_context)
        if knowledge_block:
            expanded.append(knowledge_block)
            continue

        split_blocks = _split_long_paragraph_block(block)
        expanded.extend(split_blocks)

    return expanded


def _enforce_heading_hierarchy(blocks: list[dict], *, chapter_title: str, section_context: str) -> list[dict]:
    if section_context == "index":
        return blocks

    normalized_title = _normalize_text(chapter_title)
    adjusted: list[dict] = []
    first_heading_seen = False

    content_blocks = [block for block in blocks if block.get("type") not in {"page-marker", "exercise-marker"}]
    leading_title_fragments: list[str] = []
    for block in content_blocks[:4]:
        if block.get("type") not in {"heading", "paragraph"}:
            break
        text = _normalize_text(block.get("text", ""))
        if not text or len(text) > 90:
            break
        leading_title_fragments.append(text)

    composite_title = " ".join(leading_title_fragments[:3]).strip()
    first_heading_index = next((idx for idx, block in enumerate(blocks) if block.get("type") == "heading"), None)
    intro_paragraphs = 0
    intro_chars = 0
    if first_heading_index is not None:
        for block in blocks[:first_heading_index]:
            if block.get("type") != "paragraph":
                continue
            text = _normalize_text(block.get("text", ""))
            if not text:
                continue
            intro_paragraphs += 1
            intro_chars += len(text)

    title_visible_near_top = bool(
        normalized_title
        and (
            any(_title_fragments_match(fragment, normalized_title) for fragment in leading_title_fragments)
            or _title_fragments_match(composite_title, normalized_title)
        )
    )
    insert_synthetic_title = bool(
        normalized_title
        and (
            not title_visible_near_top
            or (first_heading_index is not None and intro_paragraphs >= 2 and intro_chars >= 120)
        )
    )

    if insert_synthetic_title:
        first_heading_seen = True

    for block in blocks:
        if block.get("type") != "heading":
            adjusted.append(block)
            continue

        updated = dict(block)
        if not first_heading_seen:
            updated["level"] = 1
            first_text = _normalize_text(updated.get("text", ""))
            if normalized_title and (
                not first_text
                or (
                    _title_fragments_match(composite_title, normalized_title)
                    and not _title_fragments_match(first_text, normalized_title)
                )
            ):
                updated["text"] = normalized_title
                updated["id"] = _slugify(normalized_title)
            first_heading_seen = True
        else:
            updated["level"] = 2 if int(block.get("level", 2)) <= 2 else 3
        adjusted.append(updated)

    if insert_synthetic_title:
        insert_at = 0
        while insert_at < len(adjusted) and adjusted[insert_at].get("type") in {"page-marker", "exercise-marker"}:
            insert_at += 1
        adjusted.insert(
            insert_at,
            {
                "type": "heading",
                "text": normalized_title,
                "level": 1,
                "id": _slugify(normalized_title),
            },
        )
        return adjusted

    if first_heading_seen or not normalized_title:
        return adjusted

    insert_at = 0
    while insert_at < len(adjusted) and adjusted[insert_at].get("type") in {"page-marker", "exercise-marker"}:
        insert_at += 1
    adjusted.insert(
        insert_at,
        {
            "type": "heading",
            "text": normalized_title,
            "level": 1,
            "id": _slugify(normalized_title),
        },
    )
    return adjusted


def _classify_intro_metadata(blocks: list[dict], *, section_context: str) -> list[dict]:
    if section_context not in {"body", "appendix", "glossary"}:
        return blocks

    first_heading_index = next((idx for idx, block in enumerate(blocks) if block.get("type") == "heading"), None)
    if first_heading_index is None:
        return blocks

    classified: list[dict] = []
    author_used = False
    subtitle_used = False
    post_heading_paragraphs = 0

    for index, block in enumerate(blocks):
        if index <= first_heading_index or block.get("type") != "paragraph":
            classified.append(block)
            continue

        if post_heading_paragraphs >= 3:
            classified.append(block)
            continue

        current = dict(block)
        text = _normalize_text(current.get("text", ""))
        class_name = current.get("class_name", "") or ""
        if class_name or not text:
            classified.append(current)
            post_heading_paragraphs += 1
            continue

        if not author_used and _looks_like_author_line(text):
            current["class_name"] = "author"
            author_used = True
        elif not subtitle_used and _looks_like_subtitle_line(text):
            current["class_name"] = "subtitle"
            subtitle_used = True

        classified.append(current)
        post_heading_paragraphs += 1

    return classified


def _remove_redundant_leading_title_fragments(blocks: list[dict], *, chapter_title: str) -> list[dict]:
    first_heading_index = next((idx for idx, block in enumerate(blocks) if block.get("type") == "heading"), None)
    if first_heading_index is None:
        return blocks

    title_text = blocks[first_heading_index].get("text") or chapter_title
    cleaned: list[dict] = []
    inspected = 0

    for index, block in enumerate(blocks):
        if index <= first_heading_index:
            cleaned.append(block)
            continue

        if inspected >= 12:
            cleaned.extend(blocks[index:])
            break

        if block.get("type") in {"page-marker", "exercise-marker"}:
            cleaned.append(block)
            continue

        if block.get("type") == "figure":
            cleaned.append(block)
            inspected += 1
            continue

        if block.get("type") not in {"heading", "paragraph"}:
            cleaned.extend(blocks[index:])
            break

        text = _normalize_text(block.get("text", ""))
        if not text:
            continue
        if len(text) > 60:
            cleaned.extend(blocks[index:])
            break
        inspected += 1
        if _title_fragments_match(text, title_text) or _title_fragments_match(text, chapter_title):
            continue
        cleaned.extend(blocks[index:])
        break
    else:
        return cleaned

    return cleaned


def _merge_leading_heading_fragments(blocks: list[dict]) -> list[dict]:
    first_heading_index = next((idx for idx, block in enumerate(blocks) if block.get("type") == "heading"), None)
    if first_heading_index is None:
        return blocks

    candidate_indexes: list[int] = []
    candidate_texts: list[str] = []
    cursor = first_heading_index + 1

    while cursor < len(blocks):
        block = blocks[cursor]
        if block.get("type") in {"page-marker", "exercise-marker"}:
            cursor += 1
            continue
        if block.get("type") not in {"heading", "paragraph"}:
            break

        text = _normalize_text(block.get("text", ""))
        if not text or len(text) > 28:
            break
        if NUMERIC_VALUE_CONTINUATION_RE.match(text):
            break
        if block.get("type") == "paragraph" and text.endswith((".", "!", "?")):
            break

        candidate_indexes.append(cursor)
        candidate_texts.append(text)
        cursor += 1
        if len(candidate_indexes) >= 4:
            break

    if len(candidate_indexes) < 2:
        return blocks

    knowledge_heading_keys = {
        _normalize_key(label)
        for label in [*KNOWLEDGE_TOPIC_LABELS.values(), *KNOWLEDGE_SCHEMA_LABELS.values()]
    }
    if any(_normalize_key(text) in knowledge_heading_keys for text in candidate_texts):
        return blocks

    merged_text = _normalize_text(" ".join(candidate_texts))
    if not merged_text or len(merged_text) > 90:
        return blocks

    result: list[dict] = []
    inserted = False
    first_candidate = candidate_indexes[0]
    for index, block in enumerate(blocks):
        if index == first_candidate:
            result.append(
                {
                    "type": "heading",
                    "text": merged_text,
                    "level": 2,
                    "id": _slugify(merged_text),
                }
            )
            inserted = True
            continue
        if index in candidate_indexes[1:]:
            continue
        result.append(block)

    return result if inserted else blocks


def _classify_frontmatter_signature_blocks(
    blocks: list[dict],
    *,
    chapter_title: str,
    section_context: str,
) -> list[dict]:
    if section_context != "body":
        return blocks

    normalized_title = _normalize_key(chapter_title)
    if normalized_title not in FRONTMATTER_TITLES:
        return blocks

    paragraph_indexes = [idx for idx, block in enumerate(blocks) if block.get("type") in {"paragraph", "heading"}]
    if len(paragraph_indexes) < 2:
        return blocks

    candidate_indexes = paragraph_indexes[-6:]
    result = [dict(block) for block in blocks]
    signature_seen = False
    dateline_seen = False

    for index in reversed(candidate_indexes):
        block = result[index]
        text = _normalize_text(block.get("text", ""))
        if not text:
            continue
        next_paragraph_text = ""
        for probe in result[index + 1:]:
            if probe.get("type") == "paragraph":
                next_paragraph_text = _normalize_text(probe.get("text", ""))
                break
            if probe.get("type") == "heading":
                break
        if _looks_like_signature_date_line(text):
            block["type"] = "paragraph"
            block["class_name"] = _append_class_name(block.get("class_name", ""), "dateline")
            block.pop("level", None)
            block["html"] = html.escape(text)
            dateline_seen = True
            continue
        if _looks_like_signature_line(text):
            block["type"] = "paragraph"
            block["class_name"] = _append_class_name(block.get("class_name", ""), "signature")
            block.pop("level", None)
            block["html"] = html.escape(text)
            signature_seen = True
            continue
        if (
            block.get("type") == "heading"
            and index > 0
            and (
                text.endswith(("for", "-", "–", "—"))
                or (next_paragraph_text and next_paragraph_text[:1].islower())
            )
        ):
            block["type"] = "paragraph"
            block.pop("level", None)
            block["html"] = html.escape(text)
            continue
        if (signature_seen or dateline_seen) and _looks_like_signature_meta_line(text):
            block["type"] = "paragraph"
            block["class_name"] = _append_class_name(block.get("class_name", ""), "signature-meta")
            block.pop("level", None)
            block["html"] = html.escape(text)

    context_detected = signature_seen or dateline_seen or any(
        "dateline" in (block.get("class_name") or "") or "signature-meta" in (block.get("class_name") or "")
        for block in result
    )
    if context_detected:
        for index in reversed(candidate_indexes):
            block = result[index]
            text = _normalize_text(block.get("text", ""))
            if block.get("type") != "heading" or not _looks_like_signature_person_name(text):
                continue
            block["type"] = "paragraph"
            block["class_name"] = _append_class_name(block.get("class_name", ""), "signature")
            block.pop("level", None)
            block["html"] = html.escape(text)
            break

    return result


def _should_skip_semantic_expansion(block: dict, class_name: str, text: str) -> bool:
    if block.get("type") != "paragraph":
        return True
    skip_tokens = (
        "solution-text",
        "notation-heavy",
        "problem-solution-link",
        "problem-page-link",
        "diagram-tail",
        "diagram-caption",
        "byline",
        "lead",
    )
    if any(token in class_name for token in skip_tokens):
        return True
    if _is_notation_heavy(text) or _looks_like_chess_fragment(text):
        return True
    if len(text) < 70:
        return True
    return False


def _looks_like_index_paragraph(text: str) -> bool:
    matches = list(INDEX_ENTRY_RE.finditer(text))
    if len(matches) < 3:
        return False
    total_span = sum(match.end() - match.start() for match in matches)
    coverage = total_span / max(len(text), 1)
    return coverage >= 0.55


def _normalize_bullet_markers(text: str) -> str:
    normalized = _normalize_text(text)
    return normalized.replace("â€˘", "•").replace("\uf0b7", "•")


def _looks_like_bullet_item(text: str) -> bool:
    stripped = _normalize_bullet_markers(text)
    return bool(re.match(rf"^[{re.escape(BULLET_MARKER_CHARS)}-]\s+", stripped))


def _looks_like_contents_entry(text: str) -> bool:
    normalized = _normalize_text(text)
    return bool(re.match(r"^(?:\d{1,3}[./]|[A-Z])\s+.+", normalized))


def _strip_leading_bullet(text: str) -> str:
    return re.sub(rf"^(?:[{re.escape(BULLET_MARKER_CHARS)}]|\-)\s*", "", _normalize_bullet_markers(text))


def _split_index_like_entries(block: dict) -> list[dict]:
    text = _normalize_text(block.get("text", ""))
    entries: list[dict] = []
    for match in INDEX_ENTRY_RE.finditer(text):
        label = _normalize_text(match.group("label"))
        pages = _normalize_text(match.group("pages"))
        if len(label) < 2 or not pages:
            continue
        entry_text = f"{label} {pages}"
        entries.append(
            {
                "type": "list-item",
                "text": entry_text,
                "html": f"<strong>{html.escape(label)}</strong> {html.escape(pages)}",
                "class_name": _append_class_name(block.get("class_name", ""), "index-entry"),
            }
        )
    return entries


def _looks_like_definition_paragraph(text: str) -> bool:
    matches = list(DEFINITION_INLINE_RE.finditer(text))
    if len(matches) >= 2:
        return True
    separators = text.count(";")
    return separators >= 2 and bool(re.search(r"\b(?:include|means|refers to|focuses on|requires)\b", text, re.IGNORECASE))


def _split_definition_list_items(block: dict) -> list[dict]:
    text = _normalize_text(block.get("text", ""))
    matches = list(DEFINITION_INLINE_RE.finditer(text))
    if not matches:
        return []

    items: list[dict] = []
    for match in matches:
        term = _normalize_text(match.group("term"))
        desc = _normalize_text(match.group("desc"))
        if len(term) < 3 or len(desc) < 8:
            continue
        if term.lower() in MINOR_HEADING_WORDS:
            continue
        item_text = f"{term} - {desc}"
        item_html = f"<strong>{html.escape(term)}</strong> - {html.escape(desc)}"
        items.append(
            {
                "type": "list-item",
                "text": item_text,
                "html": item_html,
                "class_name": _append_class_name(block.get("class_name", ""), "definition-item"),
            }
        )
    return items


def _looks_like_bullet_item(text: str) -> bool:
    stripped = _normalize_bullet_markers(text)
    return bool(re.match(rf"^[{re.escape(BULLET_MARKER_CHARS)}-]\s+", stripped))


def _strip_leading_bullet(text: str) -> str:
    return re.sub(rf"^(?:[{re.escape(BULLET_MARKER_CHARS)}]|\-)\s*", "", _normalize_bullet_markers(text))


def _looks_like_quote_paragraph(text: str) -> bool:
    normalized = _normalize_text(text)
    if len(normalized) < 140 or len(normalized) > 900:
        return False
    if not QUOTE_LIKE_RE.search(normalized):
        return False
    if _looks_like_index_paragraph(normalized) or _looks_like_definition_paragraph(normalized):
        return False
    return True


def _split_long_paragraph_block(block: dict) -> list[dict]:
    text = _normalize_text(block.get("text", ""))
    if len(text) < 320:
        return [block]

    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(text) if part.strip()]
    if len(sentences) < 4:
        return [block]

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        proposed = f"{current} {sentence}".strip() if current else sentence
        if current and len(proposed) > 190:
            chunks.append(current)
            current = sentence
        else:
            current = proposed
    if current:
        chunks.append(current)

    if len(chunks) <= 1:
        return [block]

    split_blocks: list[dict] = []
    for chunk in chunks:
        split_blocks.append(
            {
                **block,
                "text": chunk,
                "html": html.escape(chunk),
            }
        )
    return split_blocks


def _annotate_solution_paragraphs(blocks: list[dict]) -> list[dict]:
    annotated: list[dict] = []
    in_solution_run = False

    for block in blocks:
        block_type = block["type"]
        if block_type == "solution-heading":
            in_solution_run = True
            annotated.append(block)
            continue
        if block_type in {"heading", "figure", "problem-page-link"}:
            in_solution_run = False
            annotated.append(block)
            continue
        if block_type == "paragraph" and in_solution_run:
            updated = dict(block)
            class_name = _append_class_name(block.get("class_name", ""), "solution-text")
            if _is_notation_heavy(block["text"]):
                class_name = _append_class_name(class_name, "notation-heavy")
            updated["class_name"] = class_name
            annotated.append(updated)
            continue
        if block_type == "paragraph" and _is_notation_heavy(block["text"]):
            updated = dict(block)
            updated["class_name"] = _append_class_name(block.get("class_name", ""), "notation-heavy")
            annotated.append(updated)
            continue
        annotated.append(block)

    return annotated


def _format_solution_variation_html(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""

    parts: list[str] = []
    last_index = 0
    seen_move_tokens: set[str] = set()
    inserted_break = False

    for match in VARIATION_START_RE.finditer(normalized):
        token = match.group(0)
        token_key_match = re.search(r"\d+\.(?:\.\.)?", token)
        if token_key_match is None:
            continue

        token_key = token_key_match.group(0)
        should_break = False
        if match.start() > 0:
            prefix = normalized[max(0, match.start() - 3):match.start()]
            if token.lower().startswith("or "):
                should_break = True
            elif re.match(r"^[a-z]\)", token, re.IGNORECASE):
                should_break = True
            elif token_key in seen_move_tokens:
                should_break = True
            elif prefix.endswith(("; ", "✓ ", "! ", "? ", ") ")):
                should_break = True

        if should_break:
            parts.append(html.escape(normalized[last_index:match.start()].rstrip()))
            parts.append("<br/>")
            last_index = match.start()
            inserted_break = True

        seen_move_tokens.add(token_key)

    parts.append(html.escape(normalized[last_index:]))
    rendered = "".join(parts).strip()
    if not inserted_break:
        return rendered
    return re.sub(r"(?:<br/>){2,}", "<br/>", rendered)


def _normalize_figure_html(node: Tag) -> str:
    fragment = BeautifulSoup(str(node), "xml")
    img = fragment.find("img")
    if img is None:
        return ""

    img.attrs.pop("style", None)
    img["class"] = _normalized_classes(img.get("class"), fallback=["figure-image"])

    is_chess = "chess-diagram" in img.get("class", []) or "chess-problem" in _class_list(node)
    exercise_id = node.get("id", "")

    if is_chess:
        img["class"] = _normalized_classes(img.get("class"), fallback=["chess-diagram"])
        caption = fragment.find(class_="diagram-caption")
        caption_text = _normalize_text(caption.get_text(" ", strip=True)) if caption else ""
        caption_html = _sanitize_inline_html(_inner_html(caption)) if caption else ""
        if not img.get("alt"):
            img["alt"] = caption_text or "Diagram szachowy"
        figure_attrs = ' class="chess-problem"'
        if exercise_id:
            figure_attrs += f' id="{html.escape(exercise_id)}"'
        figure_html = [f"<figure{figure_attrs}>"]
        if caption_html:
            figure_html.append(f'<figcaption class="diagram-caption">{caption_html}</figcaption>')
        figure_html.append(str(img))
        figure_html.append("</figure>")
        return "".join(figure_html)

    if not img.get("alt"):
        existing_caption = fragment.find("figcaption")
        img["alt"] = _normalize_text(existing_caption.get_text(" ", strip=True)) if existing_caption else ""
    existing_caption = fragment.find("figcaption")
    caption_text = _normalize_text(existing_caption.get_text(" ", strip=True)) if existing_caption else ""
    caption_html = _sanitize_inline_html(_inner_html(existing_caption)) if existing_caption else ""

    figure_classes = ["figure"]
    node_classes = _class_list(node)
    if "technical-figure" in node_classes or caption_text and _looks_like_figure_caption(caption_text):
        figure_classes.append("technical-figure")
    elif "magazine-special" in node_classes:
        figure_classes.append("magazine-special")
    elif "illustration" in node_classes:
        figure_classes.append("illustration")
    else:
        figure_classes.append("photo")

    figure_html = [f'<figure class="{" ".join(figure_classes)}">']
    figure_html.append(str(img))
    if caption_html:
        figure_html.append(f'<figcaption class="figure-caption">{caption_html}</figcaption>')
    figure_html.append("</figure>")
    return "".join(figure_html)


def _normalize_existing_table_html(node: Tag) -> str:
    fragment = BeautifulSoup(str(node), "xml")
    table = fragment.find("table")
    if table is None:
        return ""

    allowed_tags = {
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "p",
        "br",
        "strong",
        "em",
        "b",
        "i",
        "a",
        "img",
        "span",
        "ul",
        "ol",
        "li",
        "code",
        "sup",
        "sub",
    }
    attrs_by_tag = {
        "table": {"class"},
        "th": {"class", "colspan", "rowspan", "scope"},
        "td": {"class", "colspan", "rowspan"},
        "a": {"href", "title"},
        "img": {"src", "alt", "class"},
        "span": {"class"},
        "ul": {"class"},
        "ol": {"class"},
        "li": {"class"},
    }

    for tag in list(table.find_all(True)):
        if tag.name not in allowed_tags:
            tag.unwrap()
            continue
        allowed_attrs = attrs_by_tag.get(tag.name, set())
        next_attrs: dict[str, str | list[str]] = {}
        for attr_name, attr_value in list(tag.attrs.items()):
            if attr_name not in allowed_attrs:
                continue
            if attr_name in {"href", "src"}:
                uri = str(attr_value or "").strip()
                if not uri or re.match(r"(?i)^(?:javascript|data):", uri):
                    continue
                next_attrs[attr_name] = uri
                continue
            if attr_name in {"colspan", "rowspan"}:
                numeric = str(attr_value or "").strip()
                if numeric.isdigit():
                    next_attrs[attr_name] = numeric
                continue
            if attr_name == "class":
                classes = _normalized_classes(attr_value, fallback=[])
                if classes:
                    next_attrs[attr_name] = classes
                continue
            text_value = _normalize_text(str(attr_value or ""))
            if text_value:
                next_attrs[attr_name] = text_value
        tag.attrs = next_attrs

    table["class"] = _normalized_classes(table.get("class"), fallback=["semantic-table"])
    return str(table)


def _inject_problem_solution_links(
    xhtml: str,
    *,
    chapter_name: str,
    solution_targets: dict[str, str],
    ordered_problem_refs: list[dict],
) -> str:
    soup = BeautifulSoup(xhtml, "xml")
    body = soup.find("body")
    if body is None:
        return xhtml

    def build_link_paragraph(exercise_num: str, href: str) -> Tag:
        link_tag = soup.new_tag("a", href=href)
        link_tag.string = f"Przejdź do rozwiązania {exercise_num}"
        para = soup.new_tag("p")
        para["class"] = "problem-solution-link"
        para.append(link_tag)
        return para

    for existing in body.find_all("p", class_="problem-solution-link"):
        existing.decompose()

    figures = body.find_all("figure", class_="chess-problem")
    inserted_count = 0
    marker_inserted = 0
    assigned_nums: set[str] = set()
    linked_marker_nums: set[str] = set()

    ordered_refs = _dedupe_problem_refs(
        [
            ref
            for ref in ordered_problem_refs
            if ref.get("problem_file") == chapter_name and ref.get("exercise_num") and ref.get("solution_href")
        ]
    )
    ordered_ref_map = {ref["exercise_num"]: ref["solution_href"] for ref in ordered_refs}
    remaining_refs = list(ordered_refs)
    has_explicit_exercise_markers = any(
        _extract_exercise_num_from_chess_figure(figure) for figure in figures
    ) or any(
        re.search(r"exercise-(\d+)", marker.get("id", ""))
        for marker in body.find_all("p", class_="exercise-marker")
    )

    for figure in figures:
        exercise_num = _extract_exercise_num_from_chess_figure(figure)
        assigned_ref = None

        if exercise_num:
            target = ordered_ref_map.get(exercise_num) or solution_targets.get(exercise_num)
            if target:
                assigned_ref = {"exercise_num": exercise_num, "solution_href": target}
                remaining_refs = [ref for ref in remaining_refs if ref["exercise_num"] != exercise_num]

        if assigned_ref is None and remaining_refs and not has_explicit_exercise_markers:
            assigned_ref = remaining_refs.pop(0)

        if assigned_ref is None:
            continue

        exercise_num = assigned_ref["exercise_num"]
        target = assigned_ref["solution_href"]
        figure["id"] = f"exercise-{exercise_num}"
        existing = figure.find("p", class_="problem-solution-link")
        if existing is None:
            link_paragraph = build_link_paragraph(exercise_num, target)
            caption = figure.find("figcaption")
            if caption is not None:
                caption.insert_after(link_paragraph)
            else:
                figure.insert(0, link_paragraph)
        inserted_count += 1
        assigned_nums.add(exercise_num)

    for marker in body.find_all("p", class_="exercise-marker"):
        marker_id = marker.get("id", "")
        match = re.search(r"exercise-(\d+)", marker_id)
        if not match:
            continue
        exercise_num = match.group(1)
        if exercise_num in assigned_nums:
            marker.decompose()
            continue
        target = solution_targets.get(exercise_num)
        if not target:
            continue
        if marker.find_next_sibling("p", class_="problem-solution-link") is None:
            marker.insert_after(build_link_paragraph(exercise_num, target))
        marker_inserted += 1
        linked_marker_nums.add(exercise_num)

    unresolved_refs = [
        ref["exercise_num"]
        for ref in ordered_refs
        if ref["exercise_num"] not in assigned_nums and ref["exercise_num"] not in linked_marker_nums
    ]

    if (inserted_count or marker_inserted) and not unresolved_refs:
        for page_link in body.find_all("p", class_="problem-page-link"):
            page_link.decompose()
        for diagram_tail in body.find_all("p", class_="diagram-tail"):
            if PAGE_NUMBER_RE.match(_normalize_text(diagram_tail.get_text(" ", strip=True))):
                diagram_tail.decompose()

    _dedupe_dom_ids(body)
    return _serialize_soup_document(soup)


def _extract_exercise_num_from_chess_figure(figure: Tag) -> str:
    figure_id = figure.get("id", "")
    match = re.search(r"exercise-(\d+)", figure_id)
    if match:
        return match.group(1)

    caption = figure.find("figcaption")
    if caption is None:
        return ""

    number_span = caption.find(class_="exercise-number")
    if number_span is not None:
        match = re.search(r"(\d+)", _normalize_text(number_span.get_text(" ", strip=True)))
        if match:
            return match.group(1)

    caption_text = _normalize_text(caption.get_text(" ", strip=True))
    match = re.match(r"^(?P<num>\d+)\.\s+", caption_text)
    if match:
        return match.group("num")
    return ""


def _normalize_solution_game_title(text: str) -> str:
    normalized = _normalize_text(text)
    normalized = re.sub(r"^\d+\.\s*", "", normalized)
    return normalized


def _solution_title_match_score(caption_text: str, solution_title: str) -> int:
    caption_title = _normalize_solution_game_title(caption_text)
    solution_title = _normalize_solution_game_title(solution_title)
    if not caption_title or not solution_title:
        return 0

    caption_key = _normalize_key(caption_title)
    solution_key = _normalize_key(solution_title)
    if caption_key == solution_key:
        return 100
    if _training_book_key(caption_title) == _training_book_key(solution_title):
        return 90
    if caption_key in solution_key or solution_key in caption_key:
        return 70

    caption_tokens = set(LETTER_TOKEN_RE.findall(caption_key))
    solution_tokens = set(LETTER_TOKEN_RE.findall(solution_key))
    overlap = len(caption_tokens & solution_tokens)
    if overlap >= 4:
        return 50
    if overlap >= 2:
        return 25
    return 0


def _looks_like_complete_game_caption(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if _looks_like_game_caption(normalized):
        return True
    has_year = bool(re.search(r"(?:18|19|20)\d{2}", normalized))
    has_players = " – " in normalized or " - " in normalized
    return has_year and has_players and len(normalized) >= 18


def _set_chess_figure_caption(figure: Tag, *, exercise_num: str, solution_title: str, soup: BeautifulSoup) -> bool:
    normalized_title = _normalize_solution_game_title(solution_title)
    if not normalized_title:
        return False

    caption = figure.find("figcaption", class_="diagram-caption") or figure.find("figcaption")
    created = False
    if caption is None:
        caption = soup.new_tag("figcaption")
        caption["class"] = "diagram-caption"
        image = figure.find("img")
        if image is not None:
            image.insert_before(caption)
        else:
            figure.insert(0, caption)
        created = True
    else:
        caption["class"] = "diagram-caption"

    current_text = _normalize_text(caption.get_text(" ", strip=True))
    if not created and _solution_title_match_score(current_text, solution_title) >= 90:
        return False

    caption.clear()
    if exercise_num:
        number_span = soup.new_tag("span")
        number_span["class"] = "exercise-number"
        number_span.string = f"{exercise_num}."
        caption.append(number_span)
        caption.append(" ")
    caption.append(normalized_title)
    return True


def _repair_chess_problem_captions(
    soup: BeautifulSoup,
    *,
    solution_titles: dict[str, str],
) -> bool:
    changed = False
    grouped_figures: defaultdict[str, list[Tag]] = defaultdict(list)

    for figure in soup.find_all("figure", class_="chess-problem"):
        exercise_num = _extract_exercise_num_from_chess_figure(figure)
        if exercise_num:
            grouped_figures[exercise_num].append(figure)

    for exercise_num, figures in grouped_figures.items():
        solution_title = solution_titles.get(exercise_num, "")

        best_figure = None
        best_score = -1
        for figure in figures:
            caption = figure.find("figcaption", class_="diagram-caption") or figure.find("figcaption")
            caption_text = _normalize_text(caption.get_text(" ", strip=True)) if caption is not None else ""
            score = _solution_title_match_score(caption_text, solution_title)
            if _looks_like_complete_game_caption(caption_text):
                score += 15
            if caption_text.startswith(f"{exercise_num}."):
                score += 10
            if score > best_score:
                best_score = score
                best_figure = figure

        if best_figure is None:
            continue

        if best_figure.get("id") != f"exercise-{exercise_num}":
            best_figure["id"] = f"exercise-{exercise_num}"
            changed = True

        if solution_title:
            caption = best_figure.find("figcaption", class_="diagram-caption") or best_figure.find("figcaption")
            caption_text = _normalize_text(caption.get_text(" ", strip=True)) if caption is not None else ""
            if not _looks_like_complete_game_caption(caption_text):
                if _set_chess_figure_caption(
                    best_figure,
                    exercise_num=exercise_num,
                    solution_title=solution_title,
                    soup=soup,
                ):
                    changed = True

        for duplicate in figures:
            if duplicate is best_figure:
                continue
            if duplicate.get("id"):
                del duplicate["id"]
                changed = True
            for link_para in duplicate.find_all("p", class_="problem-solution-link"):
                link_para.decompose()
                changed = True

    return changed


def _rewrite_solution_backlinks(
    xhtml: str,
    *,
    exercise_problem_targets: dict[str, str],
    expected_problem_file: str | None = None,
    fallback_game_targets: dict[str, str] | None = None,
) -> str:
    soup = BeautifulSoup(xhtml, "xml")
    changed = False
    fallback_game_targets = fallback_game_targets or {}

    def ensure_solution_link(heading: Tag, href: str) -> Tag:
        existing_link = heading.find("a", class_="solution-backlink")
        if existing_link is not None:
            return existing_link

        link_tag = soup.new_tag("a", href=href)
        link_tag["class"] = "solution-backlink"
        for child in list(heading.contents):
            link_tag.append(child.extract())
        heading.append(link_tag)
        return link_tag

    def resolve_fallback_target(title_key: str) -> str:
        if not title_key:
            return ""
        direct_target = fallback_game_targets.get(title_key, "")
        if direct_target:
            return direct_target
        candidates = [
            target
            for candidate_key, target in fallback_game_targets.items()
            if _title_fragments_match(title_key, candidate_key)
        ]
        if len(set(candidates)) == 1:
            return candidates[0]
        return ""

    for heading in soup.find_all("h3", class_="solution-entry"):
        link = heading.find("a", class_="solution-backlink")

        exercise_num = ""
        heading_id = heading.get("id", "")
        match = re.search(r"solution-(\d+)", heading_id)
        if match:
            exercise_num = match.group(1)
        if not exercise_num:
            source_text = link.get_text(" ", strip=True) if link is not None else heading.get_text(" ", strip=True)
            text_match = TRUE_SOLUTION_ENTRY_RE.match(_normalize_text(source_text))
            if text_match:
                exercise_num = text_match.group("num")

        target = exercise_problem_targets.get(exercise_num, "")
        if expected_problem_file and target and not target.startswith(f"{expected_problem_file}#"):
            target = ""
        if not target:
            title_key = _extract_game_title_key(
                link.get_text(" ", strip=True) if link is not None else heading.get_text(" ", strip=True)
            )
            target = resolve_fallback_target(title_key)
        if not target:
            if link is not None:
                link.unwrap()
                changed = True
            continue
        if link is None:
            link = ensure_solution_link(heading, target)
            changed = True
        if link.get("href") != target:
            link["href"] = target
            changed = True

    return _serialize_soup_document(soup) if changed else xhtml


def _strip_unresolved_fragment_links(chapter_paths) -> None:
    fragment_index: dict[str, set[str]] = {}
    for chapter_path in chapter_paths:
        soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
        ids = {node.get("id", "") for node in soup.find_all(attrs={"id": True}) if node.get("id")}
        fragment_index[chapter_path.name] = ids

    for chapter_path in chapter_paths:
        soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
        changed = False
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "")
            if not href or "#" not in href or href.startswith(("http://", "https://", "mailto:")):
                continue
            file_part, fragment = href.split("#", 1)
            target_file = file_part or chapter_path.name
            target_ids = fragment_index.get(target_file, set())
            if fragment and fragment in target_ids:
                continue
            anchor.unwrap()
            changed = True
        if changed:
            chapter_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _extract_game_title_key(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    normalized = re.sub(r"^\d+\.\s*", "", normalized)
    candidates = GAME_TITLE_FRAGMENT_RE.findall(normalized)
    if candidates:
        return _training_book_key(candidates[-1])
    if (" – " in normalized or " - " in normalized) and "," in normalized:
        return _training_book_key(normalized.split(",", 1)[0])
    return ""


def _collect_fallback_game_targets(chapter_paths: list[Path]) -> dict[str, str]:
    targets_by_key: defaultdict[str, set[str]] = defaultdict(set)

    for chapter_path in chapter_paths:
        soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
        used_ids = {
            node.get("id", "")
            for node in soup.find_all(attrs={"id": True})
            if node.get("id")
        }
        changed = False

        def register_target(node: Tag, text: str) -> None:
            nonlocal changed

            game_key = _extract_game_title_key(text)
            if not game_key:
                return

            node_id = node.get("id", "")
            if not node_id:
                node_id = _unique_dom_id(f"game-ref-{game_key}", used_ids, fallback="game-ref")
                node["id"] = node_id
                changed = True

            targets_by_key[game_key].add(f"{chapter_path.name}#{node_id}")

        for heading in soup.find_all(["h2", "h3", "h4"]):
            if "solution-entry" in (heading.get("class") or []):
                continue
            register_target(heading, heading.get_text(" ", strip=True))

        for figure in soup.find_all("figure"):
            caption = figure.find("figcaption")
            if caption is None:
                continue
            register_target(figure, caption.get_text(" ", strip=True))

        if changed:
            chapter_path.write_text(_serialize_soup_document(soup), encoding="utf-8")

    return {
        game_key: next(iter(targets))
        for game_key, targets in targets_by_key.items()
        if len(targets) == 1
    }


def _normalize_cover_page(chapter_path: Path, *, title: str, language: str) -> None:
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    html_tag = soup.find("html")
    if html_tag is not None:
        html_tag["lang"] = language
        html_tag["xml:lang"] = language
    body = soup.find("body")
    if body is not None:
        body["class"] = "cover-page"
        img = body.find("img")
        if img is not None and not img.get("alt"):
            img["alt"] = title
            img.attrs.pop("style", None)
    chapter_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _synchronize_xhtml_language(package_dir: Path, *, language: str) -> None:
    resolved_language = _canonicalize_language(language)
    for xhtml_path in package_dir.glob("*.xhtml"):
        soup = BeautifulSoup(xhtml_path.read_text(encoding="utf-8"), "xml")
        html_tag = soup.find("html")
        if html_tag is None:
            continue
        changed = False
        if html_tag.get("lang") != resolved_language:
            html_tag["lang"] = resolved_language
            changed = True
        if html_tag.get("xml:lang") != resolved_language:
            html_tag["xml:lang"] = resolved_language
            changed = True
        if changed:
            xhtml_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _maybe_enhance_diagram_asset(asset_path: Path, *, min_long_edge: int) -> dict[str, object]:
    result: dict[str, object] = {"width": 0, "height": 0, "low_res": False, "enhanced": False}
    if _PILImage is None or not asset_path.exists() or not asset_path.is_file():
        return result

    try:
        with _PILImage.open(asset_path) as image:
            width, height = image.size
            result["width"] = width
            result["height"] = height
            longest_edge = max(width, height)
            result["low_res"] = longest_edge < min_long_edge
            if longest_edge <= 0 or longest_edge >= min_long_edge:
                return result

            scale = min(3.0, max(1.0, min_long_edge / max(longest_edge, 1)))
            if scale <= 1.05:
                return result

            new_size = (max(int(round(width * scale)), width), max(int(round(height * scale)), height))
            resample = getattr(getattr(_PILImage, "Resampling", _PILImage), "LANCZOS")
            enhanced = image.resize(new_size, resample)
            save_kwargs: dict[str, object] = {"optimize": True}
            target_format = (image.format or "").upper()
            if target_format in {"JPEG", "JPG"}:
                save_kwargs["quality"] = 92
            elif target_format == "PNG":
                save_kwargs["compress_level"] = 9
            if image.format:
                enhanced.save(asset_path, format=image.format, **save_kwargs)
            else:
                enhanced.save(asset_path, **save_kwargs)
            result["enhanced"] = True
            result["width"] = new_size[0]
            result["height"] = new_size[1]
            result["low_res"] = max(new_size) < min_long_edge
    except Exception:
        return result

    return result


def _audit_diagram_presentation(package_dir: Path, *, language: str) -> None:
    resolved_language = _canonicalize_language(language)
    generic_chess_alt = "Diagram szachowy" if resolved_language == "pl" else "Chess diagram"
    generic_technical_alt = "Diagram techniczny" if resolved_language == "pl" else "Technical diagram"

    for xhtml_path in package_dir.glob("*.xhtml"):
        soup = BeautifulSoup(xhtml_path.read_text(encoding="utf-8"), "xml")
        changed = False

        for figure in soup.find_all("figure"):
            image = figure.find("img")
            if image is None:
                continue

            figure_classes = set(_class_list(figure))
            image_classes = set(_class_list(image))
            caption = figure.find("figcaption")
            caption_text = _normalize_text(caption.get_text(" ", strip=True)) if caption is not None else ""
            is_chess = "chess-problem" in figure_classes or "chess-diagram" in image_classes
            is_technical = is_chess or "technical-figure" in figure_classes or _looks_like_figure_caption(caption_text)
            if not is_technical:
                continue

            detail_class = "detail-diagram"
            current_classes = _class_list(figure)
            updated_classes = _normalized_classes(current_classes, fallback=[detail_class])
            if updated_classes != current_classes:
                figure["class"] = updated_classes
                changed = True

            alt_text = _normalize_text(image.get("alt", ""))
            generic_alt = generic_chess_alt if is_chess else generic_technical_alt
            if not alt_text or _normalize_key(alt_text) in {_normalize_key(generic_chess_alt), _normalize_key(generic_technical_alt)}:
                desired_alt = caption_text or generic_alt
                if desired_alt and alt_text != desired_alt:
                    image["alt"] = desired_alt
                    changed = True

            src = image.get("src", "")
            if not src or "://" in src:
                continue

            asset_path = package_dir / Path(src)
            min_long_edge = 960 if is_chess else 1200
            audit = _maybe_enhance_diagram_asset(asset_path, min_long_edge=min_long_edge)
            if audit.get("low_res"):
                current_classes = _class_list(figure)
                low_res_classes = _normalized_classes(current_classes, fallback=["low-res-diagram"])
                if low_res_classes != current_classes:
                    figure["class"] = low_res_classes
                    changed = True

        if changed:
            xhtml_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _resolve_heading_target(chapter_path: Path) -> tuple[str, str]:
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    title_node = soup.find("title")
    title_text = _normalize_text(title_node.get_text(" ", strip=True)) if title_node is not None else ""
    section = soup.find("section")
    section_id = section.get("id", "") if section is not None else ""
    primary_heading = soup.find("h1")
    primary_heading_text = _normalize_text(primary_heading.get_text(" ", strip=True)) if primary_heading is not None else ""
    if primary_heading_text and (
        not title_text
        or title_text.lower() in {"front cover", "title"}
        or _looks_technical_title(title_text, reference_stem=chapter_path.stem.replace("_", " "))
        or not _title_fragments_match(primary_heading_text, title_text)
    ):
        return primary_heading_text, primary_heading.get("id", "") or section_id
    if title_text and title_text.lower() not in {"front cover", "title"}:
        if primary_heading is not None:
            return title_text, primary_heading.get("id", "") or section_id
        for tag_name in ("h2", "h3"):
            for tag in soup.find_all(tag_name):
                text = _normalize_text(tag.get_text(" ", strip=True))
                if text and _title_fragments_match(text, title_text):
                    return title_text, tag.get("id", "") or section_id
        return title_text, section_id
    for tag_name in ("h1", "h2", "h3"):
        for tag in soup.find_all(tag_name):
            text = _normalize_text(tag.get_text(" ", strip=True))
            if text:
                return text, tag.get("id", "")
    if title_text:
        return title_text, ""
    return "", ""


def _normalize_toc_label(text: str) -> str:
    normalized = _training_book_key(text)
    mapping = {
        "front cover": "Front Cover",
        "title": "Title",
        "copyright": "Copyright",
        "contents": "Contents",
        "key to symbols used": "Key to Symbols",
        "quick start guide": "Quick Start",
        "a final session": "Final Session",
        "general introduction": "Introduction",
        "summary of tactical motifs": "Tactical Motifs",
        "instructions": "Instructions",
        "easy exercises": "Easy Exercises",
        "intermediate exercises": "Intermediate Exercises",
        "advanced exercises": "Advanced Exercises",
        "solutions to easy exercises": "Solutions: Easy Exercises",
        "solutions to intermediate exercises": "Solutions: Intermediate Exercises",
        "solutions to advanced exercises": "Solutions: Advanced Exercises",
        "name index": "Index",
        "sample record sheet": "Record Sheets",
        "sample record sheets": "Record Sheets",
        "back cover": "Back Cover",
    }
    cleaned = re.sub(r"^\d+\.\s*", "", _normalize_text(text)).strip()
    return mapping.get(normalized, cleaned or text)


def _detect_cleanup_scope(
    chapter_paths,
    *,
    title: str,
    publication_profile: str | None,
) -> str:
    profile = (publication_profile or "").strip().lower()
    if _looks_like_training_book(chapter_paths, title=title):
        return "training-book"
    if profile == "magazine_reflow":
        return "magazine"
    return "book"


def _looks_like_training_book(chapter_paths, *, title: str) -> bool:
    del title
    heading_signals = 0
    solution_entries = 0
    exercise_markers = 0

    for chapter_path in chapter_paths[:10]:
        try:
            soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
        except Exception:
            continue

        chapter_title, _ = _resolve_heading_target(chapter_path)
        chapter_key = _training_book_key(chapter_title)
        if chapter_key in {
            "easy exercises",
            "intermediate exercises",
            "advanced exercises",
            "solutions to easy exercises",
            "solutions to intermediate exercises",
            "solutions to advanced exercises",
            "key to symbols used",
            "summary of tactical motifs",
        }:
            heading_signals += 1

        solution_entries += len(soup.find_all("h3", class_="solution-entry"))
        exercise_markers += sum(
            1
            for node in soup.find_all(True, id=True)
            if str(node.get("id", "")).startswith("exercise-")
        )

        body_text = _normalize_text(soup.get_text(" ", strip=True)[:1200]).lower()
        if "solutions page" in body_text or "exercises" in body_text and "solutions" in body_text:
            heading_signals += 1

    return heading_signals >= 2 or solution_entries >= 8 or exercise_markers >= 8


def _looks_like_training_book_outline(chapter_paths, *, toc_entries: list[dict] | None = None) -> bool:
    front_keys = {
        "title",
        "copyright",
        "contents",
        "key to symbols used",
        "quick start guide",
        "a final session",
        "general introduction",
        "summary of tactical motifs",
        "instructions",
    }
    exercise_keys = {"easy exercises", "intermediate exercises", "advanced exercises"}
    solution_keys = {
        "solutions to easy exercises",
        "solutions to intermediate exercises",
        "solutions to advanced exercises",
    }
    back_keys = {"name index", "sample record sheet", "sample record sheets", "back cover"}

    groups: set[str] = set()

    def absorb_key(key: str) -> None:
        if key in front_keys:
            groups.add("front")
        elif key in exercise_keys:
            groups.add("exercises")
        elif key in solution_keys:
            groups.add("solutions")
        elif key in back_keys:
            groups.add("back")

    for chapter_path in chapter_paths:
        heading_text, _ = _resolve_heading_target(chapter_path)
        absorb_key(_training_book_key(heading_text))

    for entry in toc_entries or []:
        absorb_key(_training_book_key(str(entry.get("text", ""))))

    return len(groups) >= 2 or {"exercises", "solutions"} <= groups or ("exercises" in groups and "front" in groups)


def _repair_generic_package(
    chapter_paths,
    *,
    title: str,
    author: str,
    language: str,
    toc_entries: list[dict],
    cleanup_scope: str,
) -> dict[str, object]:
    resolved_title, resolved_author, resolved_language = _derive_package_metadata(
        chapter_paths,
        title=title,
        author=author,
        language=language,
        allow_training_defaults=False,
    )
    for chapter_path in chapter_paths:
        heading_text, _ = _resolve_heading_target(chapter_path)
        if heading_text:
            _ensure_primary_heading(chapter_path, fallback_title=heading_text)
    resolved_toc_entries = toc_entries if _toc_entries_look_useful(toc_entries) and _toc_entries_align_with_chapters(toc_entries, chapter_paths) else []
    if not resolved_toc_entries:
        if _looks_like_training_book_outline(chapter_paths, toc_entries=toc_entries):
            resolved_toc_entries = _build_curated_toc_entries(chapter_paths, language=resolved_language)
        if not resolved_toc_entries:
            resolved_toc_entries = _build_generic_toc_entries(
                chapter_paths,
                cleanup_scope=cleanup_scope,
                language=resolved_language,
            )
    return {
        "title": resolved_title,
        "author": resolved_author,
        "language": resolved_language,
        "toc_entries": resolved_toc_entries,
    }


def _repair_magazine_package(
    chapter_paths,
    *,
    title: str,
    author: str,
    language: str,
) -> dict[str, object]:
    resolved_title, resolved_author, resolved_language = _derive_package_metadata(
        chapter_paths,
        title=title,
        author=author,
        language=language,
        allow_training_defaults=False,
    )
    issue_outline = _extract_magazine_issue_outline(chapter_paths)
    if not issue_outline["entries"]:
        return _repair_generic_package(
            chapter_paths,
            title=resolved_title,
            author=resolved_author,
            language=resolved_language,
            toc_entries=[],
            cleanup_scope="magazine",
        )
    ordered_issue_entries = _sort_magazine_issue_entries(issue_outline["entries"])

    chapter_infos = [_build_magazine_chapter_info(path, index=index) for index, path in enumerate(chapter_paths)]
    chapter_info_by_name = {info["file_name"]: info for info in chapter_infos}
    assignments = _plan_magazine_issue_assignments(ordered_issue_entries, chapter_infos)
    assignments_by_file: dict[str, list[dict]] = defaultdict(list)
    for assignment in assignments:
        if assignment:
            assignments_by_file[assignment["file_name"]].append(assignment)

    for file_name, file_assignments in assignments_by_file.items():
        info = chapter_info_by_name[file_name]
        _apply_magazine_assignments(info, file_assignments)

    front_features, additional_features, extras = _classify_magazine_feature_buckets(
        chapter_infos,
        assignments,
        contents_file=issue_outline["file_name"],
    )
    toc_entries = _build_magazine_toc_entries(
        issue_outline=issue_outline,
        ordered_issue_entries=ordered_issue_entries,
        assignments=assignments,
        front_features=front_features,
        additional_features=additional_features,
    )
    spine_order = [
        info["file_name"]
        for info in chapter_infos
        if info["file_name"] not in extras
    ] + [
        info["file_name"]
        for info in chapter_infos
        if info["file_name"] in extras
    ]
    return {
        "title": resolved_title,
        "author": resolved_author,
        "language": resolved_language,
        "toc_entries": toc_entries,
        "spine_order": spine_order,
    }


def _extract_magazine_issue_outline(chapter_paths) -> dict[str, object]:
    for chapter_path in chapter_paths:
        info = _build_magazine_chapter_info(chapter_path, index=-1)
        if not info["is_contents"]:
            continue

        soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
        section = soup.find("section") or soup.find("body")
        if section is None:
            continue

        entries: list[dict[str, str]] = []
        current_section = ""
        for node in [child for child in section.children if isinstance(child, Tag)]:
            text = _normalize_text(node.get_text(" ", strip=True))
            if not text:
                continue
            classes = set(node.get("class") or [])
            toc_match = MAGAZINE_TOC_LINE_RE.match(text)
            if "toc-entry" in classes or toc_match:
                if toc_match is None:
                    continue
                entries.append(
                    {
                        "page": toc_match.group("page"),
                        "title": toc_match.group("title").strip(),
                        "section": current_section or "W numerze",
                    }
                )
                continue
            if node.name in {"h2", "h3"} or (
                node.name == "p"
                and len(text) <= 40
                and not MAGAZINE_SECTION_SKIP_RE.match(text)
            ):
                current_section = _titlecase_magazine_label(text)

        return {
            "file_name": chapter_path.name,
            "entries": entries,
        }

    return {"file_name": "", "entries": []}


def _build_magazine_chapter_info(chapter_path: Path, *, index: int) -> dict[str, object]:
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    title_node = soup.find("title")
    title_text = _normalize_text(title_node.get_text(" ", strip=True)) if title_node is not None else ""
    section = soup.find("section") or soup.find("body")
    section_id = section.get("id", "") if section is not None else ""
    top_nodes = [child for child in section.children if isinstance(child, Tag)] if section is not None else []
    start_heading = next((node for node in top_nodes if node.name == "h1"), None)
    start_text = _normalize_text(start_heading.get_text(" ", strip=True)) if start_heading is not None else title_text
    start_id = start_heading.get("id", "") if start_heading is not None else section_id

    candidates: list[dict[str, object]] = []
    for node_index, node in enumerate(top_nodes):
        text = _normalize_text(node.get_text(" ", strip=True))
        if not text:
            continue
        node_id = node.get("id", "")
        context_text = _build_magazine_candidate_context(top_nodes, node_index)
        if node is start_heading:
            candidates.append(
                {
                    "file_name": chapter_path.name,
                    "chapter_index": index,
                    "node_index": node_index,
                    "id": node_id,
                    "text": text,
                    "context_text": context_text,
                    "kind": "start",
                }
            )
            continue
        if node.name in {"h2", "h3"}:
            if _is_magazine_byline(text):
                candidates.append(
                    {
                        "file_name": chapter_path.name,
                        "chapter_index": index,
                        "node_index": node_index,
                        "id": node_id,
                        "text": text,
                        "context_text": context_text,
                        "kind": "byline",
                    }
                )
            elif _looks_like_magazine_boundary_text(text):
                candidates.append(
                    {
                        "file_name": chapter_path.name,
                        "chapter_index": index,
                        "node_index": node_index,
                        "id": node_id,
                        "text": text,
                        "context_text": context_text,
                        "kind": "heading",
                    }
                )
            continue
        if node.name == "p":
            if _is_magazine_byline(text):
                candidates.append(
                    {
                        "file_name": chapter_path.name,
                        "chapter_index": index,
                        "node_index": node_index,
                        "id": node_id,
                        "text": text,
                        "context_text": context_text,
                        "kind": "byline",
                    }
                )
            elif _looks_like_magazine_boundary_text(text):
                candidates.append(
                    {
                        "file_name": chapter_path.name,
                        "chapter_index": index,
                        "node_index": node_index,
                        "id": node_id,
                        "text": text,
                        "context_text": context_text,
                        "kind": "titlelike",
                    }
                )

    full_text = _normalize_text(soup.get_text(" ", strip=True))
    title_key = _magazine_key(title_text or start_text)
    if title_key.startswith("galeria"):
        special_type = "gallery"
    elif title_key.startswith("reklama"):
        special_type = "advertisement"
    elif title_key.startswith("material sponsorowany") or title_key.startswith("materiał sponsorowany"):
        special_type = "sponsored"
    else:
        special_type = ""
    return {
        "path": chapter_path,
        "file_name": chapter_path.name,
        "index": index,
        "title": title_text or start_text,
        "title_key": title_key,
        "start_text": start_text or title_text,
        "start_id": start_id or section_id,
        "start_node_index": next((item["node_index"] for item in candidates if item["kind"] == "start"), 0),
        "is_contents": bool(MAGAZINE_SECTION_SKIP_RE.match(start_text or title_text)),
        "special_type": special_type,
        "is_special_hint": bool(
            MAGAZINE_SPECIAL_TITLE_RE.match(title_text or start_text)
            or any(hint in title_key for hint in MAGAZINE_EXTRA_TITLE_HINTS)
            or MAGAZINE_PROMO_TEXT_RE.search(full_text[:1200])
        ),
        "has_byline": any(candidate["kind"] == "byline" for candidate in candidates),
        "candidates": candidates,
    }


def _build_magazine_candidate_context(top_nodes: list[Tag], start_index: int, *, max_nodes: int = 5, max_chars: int = 320) -> str:
    fragments: list[str] = []
    total = 0
    for node in top_nodes[start_index : start_index + max_nodes]:
        text = _normalize_text(node.get_text(" ", strip=True))
        if not text:
            continue
        fragments.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return " ".join(fragments)[:max_chars]


def _plan_magazine_issue_assignments(issue_entries: list[dict[str, str]], chapter_infos: list[dict[str, object]]) -> list[dict | None]:
    assignments: list[dict | None] = []
    used_slots: set[tuple[str, int]] = set()
    min_position = (-1, -1)

    for entry in issue_entries:
        match = _find_direct_magazine_match(
            entry,
            chapter_infos,
            used_slots=used_slots,
            min_position=min_position,
        )
        if match is None:
            assignments.append(None)
            continue
        assignment = {
            "entry": entry,
            "file_name": match["file_name"],
            "chapter_index": match["chapter_index"],
            "node_index": match["node_index"],
            "candidate_kind": match["kind"],
            "id": match.get("id", ""),
            "label": _select_magazine_label(entry, match),
            "promote_existing_heading": match["kind"] in {"heading", "titlelike"},
            "override_start_title": match["kind"] == "start",
        }
        assignments.append(assignment)
        used_slots.add((match["file_name"], match["node_index"]))
        min_position = (match["chapter_index"], match["node_index"])

    for index, assignment in enumerate(assignments):
        if assignment is not None:
            continue
        previous_position = max(
            (
                (item["chapter_index"], item["node_index"])
                for item in assignments[:index]
                if item is not None
            ),
            default=(-1, -1),
        )
        next_position = min(
            (
                (item["chapter_index"], item["node_index"])
                for item in assignments[index + 1 :]
                if item is not None
            ),
            default=(10**9, 10**9),
        )
        fallback = _find_fallback_magazine_boundary(
            chapter_infos,
            used_slots=used_slots,
            start_after=previous_position,
            stop_before=next_position,
        )
        if fallback is None:
            continue
        assignment = {
            "entry": issue_entries[index],
            "file_name": fallback["file_name"],
            "chapter_index": fallback["chapter_index"],
            "node_index": fallback["node_index"],
            "candidate_kind": fallback["kind"],
            "id": "",
            "label": _select_magazine_label(issue_entries[index], fallback),
            "promote_existing_heading": False,
            "override_start_title": fallback["kind"] == "start",
        }
        assignments[index] = assignment
        used_slots.add((fallback["file_name"], fallback["node_index"]))

    return assignments


def _find_direct_magazine_match(
    entry: dict[str, str],
    chapter_infos: list[dict[str, object]],
    *,
    used_slots: set[tuple[str, int]],
    min_position: tuple[int, int],
) -> dict[str, object] | None:
    best: dict[str, object] | None = None
    best_score = 0
    for info in chapter_infos:
        if info["is_contents"]:
            continue
        if info["special_type"] in {"gallery", "advertisement"}:
            continue
        if info["special_type"] == "sponsored" and not info["has_byline"]:
            continue
        for candidate in info["candidates"]:
            slot = (candidate["file_name"], int(candidate["node_index"]))
            position = (int(candidate["chapter_index"]), int(candidate["node_index"]))
            if slot in used_slots or position < min_position:
                continue
            score = _magazine_match_score(entry["title"], str(candidate["text"]))
            context_text = _normalize_text(str(candidate.get("context_text", "")))
            if context_text:
                context_score = _magazine_match_score(entry["title"], context_text)
                context_score = max(context_score, _magazine_context_hit_score(entry["title"], context_text))
                if candidate["kind"] == "byline":
                    score = max(score, context_score + 6)
                elif candidate["kind"] == "titlelike":
                    score = max(score, context_score + 4)
                else:
                    score = max(score, context_score)
            if score <= 0:
                continue
            if candidate["kind"] == "start":
                score += 8
            elif candidate["kind"] == "heading":
                score += 4
            if score > best_score:
                best = candidate
                best_score = score
    return best if best_score >= 76 else None


def _find_fallback_magazine_boundary(
    chapter_infos: list[dict[str, object]],
    *,
    used_slots: set[tuple[str, int]],
    start_after: tuple[int, int],
    stop_before: tuple[int, int],
) -> dict[str, object] | None:
    ranked: list[tuple[int, dict[str, object]]] = []
    for info in chapter_infos:
        if info["is_contents"]:
            continue
        if info["special_type"] in {"gallery", "advertisement"}:
            continue
        if info["special_type"] == "sponsored" and not info["has_byline"]:
            continue
        chapter_start_used = (info["file_name"], int(info["start_node_index"])) in used_slots
        for candidate in info["candidates"]:
            slot = (candidate["file_name"], int(candidate["node_index"]))
            position = (int(candidate["chapter_index"]), int(candidate["node_index"]))
            if slot in used_slots or position <= start_after or position >= stop_before:
                continue
            if chapter_start_used and candidate["kind"] not in {"start", "byline"}:
                continue
            if chapter_start_used and int(candidate["node_index"]) <= int(info["start_node_index"]) + 3:
                continue
            if (
                candidate["kind"] != "start"
                and not info["is_special_hint"]
                and int(candidate["node_index"]) <= int(info["start_node_index"]) + 3
            ):
                continue
            if candidate["kind"] == "start":
                weight = 3
            elif candidate["kind"] == "heading":
                weight = 2
            elif candidate["kind"] == "titlelike":
                weight = 1
            else:
                weight = 0
            ranked.append((weight, candidate))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[1]["chapter_index"], item[1]["node_index"], -item[0]))
    return ranked[0][1]


def _apply_magazine_assignments(chapter_info: dict[str, object], assignments: list[dict]) -> None:
    chapter_path = Path(chapter_info["path"])
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    section = soup.find("section") or soup.find("body")
    if section is None:
        return
    top_nodes = [child for child in section.children if isinstance(child, Tag)]
    used_ids = {
        node.get("id", "")
        for node in soup.find_all(attrs={"id": True})
        if node.get("id", "")
    }
    title_node = soup.find("title")
    changed = False

    for assignment in sorted(assignments, key=lambda item: item["node_index"], reverse=True):
        if assignment["node_index"] >= len(top_nodes):
            continue
        node = top_nodes[assignment["node_index"]]
        label = assignment["label"]
        current_text = _normalize_text(node.get_text(" ", strip=True))
        is_start = assignment["candidate_kind"] == "start"
        if is_start:
            if assignment["override_start_title"] and _should_override_magazine_start_title(str(chapter_info["title"]), label):
                node.clear()
                node.string = label
                desired_id = _slugify(label)
                if desired_id and node.get("id", "") != desired_id:
                    node["id"] = _unique_dom_id(desired_id, used_ids, fallback="section")
                if title_node is not None:
                    title_node.string = label
                changed = True
            if node.name != "h1":
                node.name = "h1"
                changed = True
            node_id = node.get("id", "")
            if not node_id:
                node_id = _unique_dom_id(_slugify(label), used_ids, fallback="section")
                node["id"] = node_id
                changed = True
            assignment["id"] = node_id
            continue

        if assignment["promote_existing_heading"] and node.name in {"h2", "h3"}:
            if _should_normalize_magazine_heading(current_text, label):
                node.clear()
                node.string = label
                changed = True
            if node.name != "h2":
                node.name = "h2"
                changed = True
            node_id = node.get("id", "")
            if not node_id:
                node_id = _unique_dom_id(_slugify(label), used_ids, fallback="article")
                node["id"] = node_id
                changed = True
            assignment["id"] = node_id
            continue

        new_heading = soup.new_tag("h2")
        anchor_id = _unique_dom_id(_slugify(label), used_ids, fallback="article")
        new_heading["id"] = anchor_id
        new_heading.string = label
        node.insert_before(new_heading)
        assignment["id"] = anchor_id
        changed = True

    if changed:
        chapter_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _classify_magazine_feature_buckets(
    chapter_infos: list[dict[str, object]],
    assignments: list[dict | None],
    *,
    contents_file: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]], set[str]]:
    assigned_files = {item["file_name"] for item in assignments if item is not None}
    assigned_start_files = {
        item["file_name"]
        for item in assignments
        if item is not None and item["candidate_kind"] == "start"
    }
    first_assigned_index = min(
        (item["chapter_index"] for item in assignments if item is not None),
        default=10**9,
    )
    front_features: list[dict[str, str]] = []
    additional_features: list[dict[str, str]] = []
    extras: set[str] = set()

    for info in chapter_infos:
        file_name = str(info["file_name"])
        if file_name == contents_file:
            continue
        title = _normalize_text(str(info["title"]))
        title_key = _magazine_key(title)
        if re.search(r"(?i)\b(ciąg dalszy|continued)\b", title):
            continue
        if title_key.startswith("rozmowa z ") and file_name not in assigned_files:
            continue
        if info["is_special_hint"] and file_name not in assigned_files:
            extras.add(file_name)
            continue
        if file_name in assigned_start_files:
            continue
        if title_key in MAGAZINE_FEATURE_SKIP_KEYS:
            extras.add(file_name)
            continue
        entry = {
            "file_name": file_name,
            "id": str(info["start_id"]),
            "text": title,
            "chapter_index": str(info["index"]),
            "node_index": str(info["start_node_index"]),
        }
        if file_name in assigned_files:
            additional_features.append(entry)
            continue
        if int(info["index"]) < first_assigned_index:
            front_features.append(entry)
        elif _looks_like_magazine_extra_title(title_key):
            extras.add(file_name)
        else:
            additional_features.append(entry)

    return front_features, additional_features, extras


def _build_magazine_toc_entries(
    *,
    issue_outline: dict[str, object],
    ordered_issue_entries: list[dict[str, str]],
    assignments: list[dict | None],
    front_features: list[dict[str, str]],
    additional_features: list[dict[str, str]],
) -> list[dict]:
    toc_entries = [
        {"file_name": "cover.xhtml", "id": "", "text": "Cover", "level": 1},
    ]
    if issue_outline["file_name"]:
        toc_entries.append(
            {
                "file_name": str(issue_outline["file_name"]),
                "id": "",
                "text": "Table of Contents",
                "level": 1,
            }
        )

    if front_features:
        front_features = sorted(
            front_features,
            key=lambda item: (int(item.get("chapter_index") or 10**9), int(item.get("node_index") or 0)),
        )
        first = front_features[0]
        toc_entries.append(
            {
                "file_name": first["file_name"],
                "id": first["id"],
                "text": "Front Matter",
                "level": 1,
            }
        )
        for item in front_features:
            toc_entries.append(
                {
                    "file_name": item["file_name"],
                    "id": item["id"],
                    "text": item["text"],
                    "level": 2,
                }
            )

    article_entries = []
    for entry, assignment in zip(ordered_issue_entries, assignments):
        if assignment is None or not assignment.get("id"):
            continue
        article_entries.append(
            {
                "file_name": assignment["file_name"],
                "id": assignment["id"],
                "text": assignment["label"],
                "chapter_index": assignment["chapter_index"],
                "node_index": assignment["node_index"],
            }
        )

    combined_articles: list[dict[str, object]] = []
    seen_targets: set[tuple[str, str]] = set()
    for item in article_entries + list(additional_features):
        target = (str(item["file_name"]), str(item["id"]))
        if target in seen_targets:
            continue
        seen_targets.add(target)
        combined_articles.append(item)
    combined_articles.sort(
        key=lambda item: (int(item.get("chapter_index") or 10**9), int(item.get("node_index") or 0), str(item["text"])),
    )

    if combined_articles:
        first = combined_articles[0]
        toc_entries.append(
            {
                "file_name": first["file_name"],
                "id": first["id"],
                "text": "Articles",
                "level": 1,
            }
        )
        for item in combined_articles:
            toc_entries.append(
                {
                    "file_name": item["file_name"],
                    "id": item["id"],
                    "text": item["text"],
                    "level": 2,
                }
            )

    return toc_entries


def _sort_magazine_issue_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        entries,
        key=lambda entry: (int(entry.get("page") or 10**9), _titlecase_magazine_label(entry.get("title", ""))),
    )


def _is_magazine_byline(text: str) -> bool:
    normalized = _normalize_text(text)
    return bool(normalized and MAGAZINE_BYLINE_RE.match(normalized))


def _looks_like_magazine_boundary_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 120:
        return False
    if normalized.endswith(".") and len(normalized.split()) > 6:
        return False
    if _is_magazine_byline(normalized):
        return False
    if _looks_like_author_line(normalized):
        return False
    if re.search(r"(?i)\b(newsweek|ringier|redaktor naczelny|wydawca|prenumerata|www\.)\b", normalized):
        return False
    if _looks_like_heading_text(normalized):
        return True
    if len(normalized.split()) <= 8 and normalized == normalized.upper():
        return True
    return bool(re.search(r"[A-ZĄĆĘŁŃÓŚŹŻ]{8,}", normalized))


def _magazine_key(text: str) -> str:
    normalized = _normalize_text(text)
    normalized = re.sub(r"^\d+\.\s*", "", normalized)
    normalized = re.sub(r"\s+[—–-]\s+ciąg dalszy$", "", normalized, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", normalized).strip(" .,:;!?-").lower()


def _magazine_compact_key(text: str) -> str:
    return re.sub(r"[^0-9a-ząćęłńóśźż]", "", _magazine_key(text))


def _magazine_significant_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[0-9A-Za-zÀ-ÿĀ-žąćęłńóśźż]+", _normalize_text(text).lower())
    return [token for token in tokens if token not in MAGAZINE_MINOR_WORDS]


def _magazine_match_score(expected_title: str, candidate_text: str) -> int:
    expected_key = _magazine_key(expected_title)
    candidate_key = _magazine_key(candidate_text)
    if not expected_key or not candidate_key:
        return 0
    if expected_key == candidate_key:
        return 100

    expected_compact = _magazine_compact_key(expected_title)
    candidate_compact = _magazine_compact_key(candidate_text)
    if expected_compact and expected_compact == candidate_compact:
        return 98
    if expected_compact and candidate_compact:
        if expected_compact in candidate_compact or candidate_compact in expected_compact:
            if min(len(expected_compact), len(candidate_compact)) >= 6:
                return 88

    expected_tokens = _magazine_significant_tokens(expected_title)
    candidate_tokens = _magazine_significant_tokens(candidate_text)
    if not expected_tokens or not candidate_tokens:
        return 0
    if expected_tokens[:2] and candidate_tokens[:2] == expected_tokens[:2]:
        return 84
    overlap = len(set(expected_tokens) & set(candidate_tokens))
    if overlap >= 3:
        return 78
    if overlap == 2:
        return 68
    return 0


def _magazine_context_hit_score(expected_title: str, candidate_text: str) -> int:
    expected_tokens = [
        token
        for token in _slugify(_normalize_text(expected_title)).split("-")
        if token and token not in MAGAZINE_MINOR_WORDS and len(token) >= 4
    ]
    if not expected_tokens:
        return 0
    candidate_slug = _slugify(_normalize_text(candidate_text))
    hits = sum(1 for token in expected_tokens if token in candidate_slug)
    if hits >= 4:
        return 92
    if hits >= 3:
        return 84
    if hits >= 2:
        return 76
    return 0


def _select_magazine_label(entry: dict[str, str], candidate: dict[str, object]) -> str:
    desired_label = _titlecase_magazine_label(entry["title"])
    candidate_text = _normalize_text(str(candidate.get("text", "")))
    if candidate_text and candidate.get("kind") == "heading" and not _should_normalize_magazine_heading(candidate_text, desired_label):
        return candidate_text
    return desired_label


def _titlecase_magazine_label(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized.isupper():
        return normalized.title()
    return normalized


def _should_override_magazine_start_title(current_title: str, desired_title: str) -> bool:
    current_key = _magazine_key(current_title)
    if not current_key:
        return True
    if MAGAZINE_SPECIAL_TITLE_RE.match(current_title):
        return True
    if any(hint in current_key for hint in MAGAZINE_EXTRA_TITLE_HINTS):
        return True
    current_tokens = _magazine_significant_tokens(current_title)
    desired_tokens = _magazine_significant_tokens(desired_title)
    if re.match(r"(?i)^(?:z|o|na|w|od|po)\b", current_title.strip()) and len(current_tokens) <= 2 and len(desired_tokens) >= 3:
        return True
    if current_tokens and desired_tokens and set(current_tokens).issubset(set(desired_tokens)) and len(desired_tokens) > len(current_tokens) + 1:
        return True
    return False


def _should_normalize_magazine_heading(current_text: str, desired_title: str) -> bool:
    normalized = _normalize_text(current_text)
    if not normalized:
        return True
    if normalized == desired_title:
        return False
    if normalized.isupper():
        return True
    if re.search(r"[A-ZĄĆĘŁŃÓŚŹŻ]{8,}", normalized):
        return True
    if _magazine_match_score(desired_title, normalized) >= 96:
        return False
    return "  " in normalized or "/" in normalized


def _looks_like_magazine_extra_title(title_key: str) -> bool:
    if not title_key:
        return False
    return any(hint in title_key for hint in MAGAZINE_EXTRA_TITLE_HINTS)


def _toc_entries_look_useful(toc_entries: list[dict]) -> bool:
    meaningful = 0
    for entry in toc_entries:
        text = _normalize_text(entry.get("text", ""))
        if not text:
            continue
        if re.fullmatch(r"(section|sekcja)\s+\d+", text, flags=re.IGNORECASE):
            continue
        meaningful += 1
    return meaningful >= 2


def _toc_entries_align_with_chapters(toc_entries: list[dict], chapter_paths) -> bool:
    expected_labels = {
        chapter_path.name: _normalize_key(_resolve_heading_target(chapter_path)[0])
        for chapter_path in chapter_paths
    }
    compared = 0
    matches = 0
    for entry in toc_entries:
        file_name = entry.get("file_name", "")
        expected = expected_labels.get(file_name)
        if not expected:
            continue
        actual = _normalize_key(entry.get("text", ""))
        if not actual:
            continue
        compared += 1
        if actual == expected or _title_fragments_match(actual, expected):
            matches += 1
    return compared > 0 and matches * 2 >= compared


def _generic_toc_group_labels(*, cleanup_scope: str, language: str) -> list[tuple[str, str]]:
    if _canonicalize_language(language) == "pl":
        return [
            ("front", "Wstęp"),
            ("body", "Artykuły" if cleanup_scope == "magazine" else "Rozdziały"),
            ("back", "Dodatki"),
        ]
    return [
        ("front", "Front Matter"),
        ("body", "Articles" if cleanup_scope == "magazine" else "Chapters"),
        ("back", "Back Matter"),
    ]


def _build_generic_toc_entries(chapter_paths, *, cleanup_scope: str, language: str) -> list[dict]:
    chapter_info = []
    for chapter_path in chapter_paths:
        if chapter_path.name == "cover.xhtml":
            continue
        heading_text, heading_id = _resolve_heading_target(chapter_path)
        label = _normalize_text(heading_text) or chapter_path.stem.replace("_", " ").title()
        chapter_info.append(
            {
                "file_name": chapter_path.name,
                "id": heading_id,
                "text": label,
                "bucket": _classify_generic_toc_bucket(label),
            }
        )

    cover_label = "Okładka" if _canonicalize_language(language) == "pl" else "Cover"
    toc_entries = [{"file_name": "cover.xhtml", "id": "", "text": cover_label, "level": 1}]
    groups = _generic_toc_group_labels(cleanup_scope=cleanup_scope, language=language)
    has_multiple_groups = len({entry["bucket"] for entry in chapter_info}) > 1

    for bucket_key, bucket_label in groups:
        members = [entry for entry in chapter_info if entry["bucket"] == bucket_key]
        if not members:
            continue
        if has_multiple_groups:
            first = members[0]
            toc_entries.append(
                {
                    "file_name": first["file_name"],
                    "id": first["id"],
                    "text": bucket_label,
                    "level": 1,
                }
            )
            level = 2
        else:
            level = 1
        for entry in members:
            toc_entries.append(
                {
                    "file_name": entry["file_name"],
                    "id": entry["id"],
                    "text": entry["text"],
                    "level": level,
                }
            )

    return toc_entries


def _classify_generic_toc_bucket(title: str) -> str:
    key = _normalize_key(title)
    if key in {
        "cover",
        "front cover",
        "title",
        "copyright",
        "contents",
        "table of contents",
        "masthead",
        "editor's note",
        "editors note",
        "from the editor",
        "contributors",
        "letters",
        "foreword",
        "preface",
        "introduction",
    }:
        return "front"
    if (
        key.startswith("appendix")
        or key.endswith("index")
        or key.endswith("glossary")
        or key.endswith("references")
        or key.endswith("bibliography")
        or key.endswith("notes")
        or key in {"about the author", "about the authors", "record sheets", "sample record sheet", "sample record sheets", "back cover"}
    ):
        return "back"
    return "body"


def _derive_package_metadata(
    chapter_paths,
    *,
    title: str,
    author: str,
    language: str,
    allow_training_defaults: bool = False,
) -> tuple[str, str, str]:
    resolved_title = _normalize_text(title)
    resolved_author = _normalize_text(author)
    language_samples: list[str] = []
    resolved_language = _canonicalize_language(language)
    resolved_title = re.sub(r"\(\s*PDFDrive\s*\)", "", resolved_title, flags=re.IGNORECASE).strip()

    if _looks_technical_title(resolved_title):
        for chapter_path in chapter_paths:
            if chapter_path.name not in {"chapter_002.xhtml", "chapter_001.xhtml"}:
                continue
            soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
            candidate_nodes = list(soup.find_all(["h1", "h2"])) + list(soup.find_all("p"))
            for node in candidate_nodes:
                candidate = _normalize_text(node.get_text(" ", strip=True))
                classes = {_normalize_key(class_name) for class_name in _class_list(node)}
                if {"author", "byline"} & classes:
                    continue
                if AUTHOR_LINE_RE.match(candidate):
                    continue
                if (
                    candidate
                    and candidate.lower() not in {"title", "by", "front cover"}
                    and not _looks_technical_title(candidate, reference_stem=chapter_path.stem.replace("_", " "))
                ):
                    resolved_title = candidate
                    break
            if resolved_title and not _looks_technical_title(resolved_title):
                break
    if _looks_technical_title(resolved_title):
        resolved_title = "Untitled"

    if not resolved_author or _is_placeholder_author(resolved_author):
        inferred_author = _extract_author_from_chapters(chapter_paths)
        if inferred_author:
            resolved_author = inferred_author
    if not resolved_author or _is_placeholder_author(resolved_author):
        for chapter_path in chapter_paths:
            if chapter_path.name != "chapter_003.xhtml":
                continue
            text = _normalize_text(BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml").get_text(" ", strip=True))
            match = COPYRIGHT_AUTHOR_RE.search(text)
            if match:
                resolved_author = re.sub(r"\s{2,}", " ", match.group("authors")).strip()
                resolved_author = re.sub(r"\s+(?:Copyright|All rights reserved).*$", "", resolved_author, flags=re.IGNORECASE).strip()
                break
    if not resolved_author or _is_placeholder_author(resolved_author):
        resolved_author = "Unknown"

    for chapter_path in chapter_paths[:6]:
        soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
        language_samples.append(_normalize_text(soup.get_text(" ", strip=True))[:800])

    resolved_language = _resolve_publication_language(resolved_language, samples=language_samples)

    return resolved_title, resolved_author, resolved_language


def _canonicalize_language(value: str | None) -> str:
    normalized = _normalize_key(value)
    if not normalized:
        return "en"
    return LANGUAGE_ALIASES.get(normalized, normalized)


def _looks_polish_text(value: str) -> bool:
    sample_text = f" {_normalize_text(value).lower()} "
    diacritic_hits = len(re.findall(r"[ąćęłńóśźż]", sample_text))
    marker_hits = sum(marker in sample_text for marker in POLISH_LANGUAGE_MARKERS)
    english_hits = sum(marker in sample_text for marker in ENGLISH_LANGUAGE_MARKERS)
    return diacritic_hits >= 3 or marker_hits >= 4 or (marker_hits >= 2 and english_hits == 0)


def _resolve_publication_language(language: str, *, samples: list[str]) -> str:
    resolved_language = _canonicalize_language(language)
    sample_text = " ".join(sample for sample in samples if sample)
    if not sample_text:
        return resolved_language
    if _looks_polish_text(sample_text):
        return "pl"
    if resolved_language == "pl" and _looks_english_text(sample_text):
        return "en"
    return resolved_language


def _is_placeholder_author(value: str | None) -> bool:
    normalized = _normalize_key(value)
    if not normalized:
        return True
    if normalized in PLACEHOLDER_AUTHOR_KEYS:
        return True
    if any(token in normalized for token in TECHNICAL_AUTHOR_MARKERS):
        return True
    return bool(re.search(r"(?i)\b(?:technical|generated|converter|conversion|autogenerated|codex|openai|chatgpt|ai)\b", normalized))


def _looks_english_text(value: str) -> bool:
    sample_text = f" {_normalize_text(value).lower()} "
    return any(token in sample_text for token in (" the ", " and ", " with ", " how ", " field ", " guide ", " navigation "))


def _extract_author_from_chapters(chapter_paths) -> str:
    for chapter_path in chapter_paths[:4]:
        soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
        for node in soup.find_all(["p", "h2", "h3", "span", "div"]):
            text = _normalize_text(node.get_text(" ", strip=True))
            if not text:
                continue
            raw_classes = node.get("class") or []
            if isinstance(raw_classes, str):
                raw_classes = raw_classes.split()
            classes = {_normalize_key(class_name) for class_name in raw_classes}
            candidate = ""
            if {"author", "byline"} & classes:
                candidate = text
            else:
                match = AUTHOR_LINE_RE.match(text)
                if match:
                    candidate = _normalize_text(match.group("author"))
            candidate = re.sub(r"\s+[|/].*$", "", candidate).strip(" -:,;")
            if not candidate or _is_placeholder_author(candidate):
                continue
            if 1 <= len(candidate.split()) <= 6 and len(candidate) <= 80:
                return candidate
    return ""


def _remove_following_solution_paragraphs(node: Tag) -> None:
    sibling = node.find_next_sibling()
    while sibling is not None and sibling.name == "p":
        classes = set(sibling.get("class") or [])
        if "solution-text" not in classes:
            break
        next_sibling = sibling.find_next_sibling()
        sibling.decompose()
        sibling = next_sibling


def _ensure_primary_heading(chapter_path: Path, *, fallback_title: str) -> None:
    desired_title = _normalize_toc_label(fallback_title)
    if not desired_title:
        return

    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    title_node = soup.find("title")
    section = soup.find("section")
    if section is None:
        return

    changed = False
    desired_id = _slugify(desired_title)
    heading = section.find("h1")
    if heading is not None:
        current_text = _normalize_text(heading.get_text(" ", strip=True))
        if _training_book_key(current_text) != _training_book_key(desired_title):
            heading.clear()
            heading.string = desired_title
            changed = True
        unique_id = _unique_dom_id(desired_id, _collect_used_dom_ids(soup, skip_node=heading), fallback="section")
        if heading.get("id", "") != unique_id:
            heading["id"] = unique_id
            changed = True

    if title_node is not None:
        current_title = _normalize_text(title_node.get_text(" ", strip=True))
        if _training_book_key(current_title) != _training_book_key(fallback_title):
            title_node.string = fallback_title
            changed = True

    if changed:
        chapter_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _trim_trailing_nonessential_figures(section: Tag) -> bool:
    direct_children = [node for node in section.children if isinstance(node, Tag)]
    figure_signatures: list[tuple[str, str]] = []
    for node in direct_children:
        if node.name != "figure":
            continue
        image = node.find("img")
        if image is None:
            continue
        caption = node.find("figcaption")
        figure_signatures.append(
            (
                image.get("src", ""),
                _normalize_text(caption.get_text(" ", strip=True)) if caption is not None else "",
            )
        )

    changed = False
    while direct_children:
        node = direct_children[-1]
        if node.name == "p" and not _normalize_text(node.get_text(" ", strip=True)):
            node.decompose()
            direct_children.pop()
            changed = True
            continue
        if node.name != "figure":
            break

        image = node.find("img")
        if image is None:
            break

        caption = node.find("figcaption")
        caption_text = _normalize_text(caption.get_text(" ", strip=True)) if caption is not None else ""
        classes = set(node.get("class") or [])
        signature = (image.get("src", ""), caption_text)
        previous_figure_count = sum(1 for child in direct_children[:-1] if child.name == "figure")
        earlier_signatures = figure_signatures[:previous_figure_count]

        drop_node = False
        if "photo" in classes and not caption_text and not image.get("alt", "").strip():
            drop_node = True
        elif "chess-problem" in classes:
            has_link = node.find("p", class_="problem-solution-link") is not None
            if not node.get("id") and not has_link and signature in earlier_signatures:
                drop_node = True

        if not drop_node:
            break

        node.decompose()
        direct_children.pop()
        changed = True
        if signature in figure_signatures:
            figure_signatures.remove(signature)

    return changed


def _cleanup_solution_chapter(chapter_path: Path) -> None:
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    changed = False
    last_num = -1
    chapter_title_key = ""
    primary_heading = soup.find("h1")
    if primary_heading is not None:
        chapter_title_key = _training_book_key(primary_heading.get_text(" ", strip=True))

    for heading in list(soup.find_all("h3", class_="solution-entry")):
        heading_text = _normalize_text(heading.get_text(" ", strip=True))
        match = re.search(r"solution-(\d+)", heading.get("id", ""))
        if not match:
            continue
        exercise_num = int(match.group(1))
        looks_like_entry = bool(re.match(rf"^{exercise_num}\.\s+.+(?:18|19|20)\d{{2}}$", heading_text))
        if not looks_like_entry or exercise_num < last_num:
            _remove_following_solution_paragraphs(heading)
            heading.decompose()
            changed = True
            continue
        last_num = exercise_num

    removable_banner_keys = {key for key in {chapter_title_key} if key}
    for heading in list(soup.find_all(["h2", "h3", "h4"])):
        if "solution-entry" in (heading.get("class") or []):
            continue
        heading_key = _training_book_key(heading.get_text(" ", strip=True))
        if heading_key in removable_banner_keys or heading_key.startswith("solutions to "):
            heading.decompose()
            changed = True

    section = soup.find("section")
    if section is not None and _trim_trailing_nonessential_figures(section):
        changed = True

    if changed:
        chapter_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _strip_problem_navigation_from_examples(chapter_path: Path) -> None:
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    changed = False
    for figure in soup.find_all("figure", class_="chess-problem"):
        if figure.has_attr("id"):
            del figure["id"]
            changed = True
        for link_para in figure.find_all("p", class_="problem-solution-link"):
            link_para.decompose()
            changed = True
    for paragraph in list(soup.find_all("p", class_=["problem-solution-link", "problem-page-link", "exercise-marker"])):
        paragraph.decompose()
        changed = True
    section = soup.find("section")
    if section is not None and _trim_trailing_nonessential_figures(section):
        changed = True
    if changed:
        chapter_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _repair_exercise_chapter(
    chapter_path: Path,
    *,
    solution_targets: dict[str, str],
    solution_titles: dict[str, str],
) -> dict[str, str]:
    _ensure_primary_heading(chapter_path, fallback_title=_resolve_heading_target(chapter_path)[0])
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    changed = False

    for heading in list(soup.find_all(["h2", "h3"])):
        if SOLUTION_PAGE_HEADING_RE.search(_normalize_text(heading.get_text(" ", strip=True))):
            heading.decompose()
            changed = True

    for heading in list(soup.find_all("h3", class_="solution-entry")):
        _remove_following_solution_paragraphs(heading)
        heading.decompose()
        changed = True

    for paragraph in list(soup.find_all("p")):
        text = _normalize_text(paragraph.get_text(" ", strip=True))
        if not text:
            continue
        if paragraph.get("class") and "problem-solution-link" in (paragraph.get("class") or []):
            continue
        if SOLUTION_PAGE_RE.search(text) and len(text) <= 80:
            paragraph.decompose()
            changed = True
            continue
        if _looks_like_game_caption(text) or len(re.findall(r"(?:18|19|20)\d{2}", text)) >= 2:
            paragraph.decompose()
            changed = True

    if _repair_chess_problem_captions(
        soup,
        solution_titles=solution_titles,
    ):
        changed = True

    exercise_problem_targets: dict[str, str] = {}
    for figure in soup.find_all("figure", class_="chess-problem"):
        caption = figure.find("figcaption", class_="diagram-caption")
        if caption is not None:
            caption_text = _normalize_text(caption.get_text(" ", strip=True))
            if (
                SOLUTION_PAGE_RE.search(caption_text)
                or len(re.findall(r"(?:18|19|20)\d{2}", caption_text)) >= 2
                or caption_text.count(" – ") >= 2
                or caption_text.count(" - ") >= 2
            ):
                caption.decompose()
                changed = True
        exercise_num = _extract_exercise_num_from_chess_figure(figure)
        if not exercise_num:
            for link_para in figure.find_all("p", class_="problem-solution-link"):
                link_para.decompose()
                changed = True
            continue
        target = solution_targets.get(exercise_num, "")
        existing = figure.find("p", class_="problem-solution-link")
        if not target:
            if existing is not None:
                existing.decompose()
                changed = True
            continue
        exercise_problem_targets[exercise_num] = f"{chapter_path.name}#exercise-{exercise_num}"
        if existing is None:
            existing = soup.new_tag("p")
            existing["class"] = "problem-solution-link"
            anchor = soup.new_tag("a", href=target)
            anchor.string = f"Go to solution {exercise_num}"
            existing.append(anchor)
            caption = figure.find("figcaption")
            if caption is not None:
                caption.insert_after(existing)
            else:
                figure.insert(0, existing)
            changed = True
        else:
            link = existing.find("a", href=True)
            if link is None:
                existing.clear()
                link = soup.new_tag("a", href=target)
                existing.append(link)
            if link.get("href") != target:
                link["href"] = target
                changed = True
            if _normalize_text(link.get_text(" ", strip=True)) != f"Go to solution {exercise_num}":
                link.string = f"Go to solution {exercise_num}"
                changed = True

    section = soup.find("section")
    if section is not None and _trim_trailing_nonessential_figures(section):
        changed = True

    if changed:
        chapter_path.write_text(_serialize_soup_document(soup), encoding="utf-8")
    return exercise_problem_targets


def _repair_symbol_key_chapter(chapter_path: Path) -> None:
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    section = soup.find("section")
    if section is None:
        return
    first_paragraph = next((node for node in section.find_all("p", recursive=False) if _normalize_text(node.get_text(" ", strip=True))), None)
    if first_paragraph is None:
        return

    legend_items = [
        ("+=", "White is slightly better"),
        ("=+", "Black is slightly better"),
        ("±", "White is better"),
        ("∓", "Black is better"),
        ("+–", "White has a decisive advantage"),
        ("–+", "Black has a decisive advantage"),
        ("=", "Equality"),
        ("!", "A good move"),
        ("!!", "An excellent move"),
        ("?", "A weak move"),
        ("??", "A blunder"),
        ("!?", "A move worth considering"),
        ("?!", "A move of doubtful value"),
        ("✓", "A move that should be seen as part of the solution"),
    ]

    legend_items = [
        ("=", "Equality"),
        ("=+", "Black is slightly better"),
        ("+=", "White is slightly better"),
        ("\u2213", "Black is much better"),
        ("\u00b1", "White is much better"),
        ("\u2013+", "Black has a decisive advantage"),
        ("+\u2013", "White has a decisive advantage"),
        ("!", "A good move"),
        ("!!", "An excellent move"),
        ("?", "A weak move"),
        ("??", "A blunder"),
        ("!?", "A move worth considering"),
        ("?!", "A move of doubtful value"),
        ("+", "Check"),
        ("#", "Mate"),
        ("O-O", "Kingside castling"),
        ("O-O-O", "Queenside castling"),
        ("=Q", "Promotion"),
        ("1-0", "White won"),
        ("0-1", "Black won"),
        ("\u00bd-\u00bd", "Draw"),
        ("*", "Game unfinished"),
    ]

    legend = soup.new_tag("ul")
    legend["class"] = "symbol-legend"
    for symbol, label in legend_items:
        item = soup.new_tag("li")
        item["class"] = "symbol-legend-item"
        symbol_span = soup.new_tag("span")
        symbol_span["class"] = "notation-symbol"
        symbol_span.string = symbol
        label_span = soup.new_tag("span")
        label_span["class"] = "notation-label"
        label_span.string = label
        item.append(symbol_span)
        item.append(label_span)
        legend.append(item)

    first_paragraph.replace_with(legend)
    chapter_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _repair_name_index_chapter(chapter_path: Path) -> None:
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    section = soup.find("section")
    if section is None:
        return

    changed = False
    for node in section.find_all("p", recursive=False):
        if _normalize_key(node.get_text(" ", strip=True)) == "name index":
            heading = soup.new_tag("h1", id="name-index")
            heading.string = "Name Index"
            node.replace_with(heading)
            changed = True
            break

    for heading in list(section.find_all("h2")):
        text = _normalize_text(heading.get_text(" ", strip=True))
        if any(char.isdigit() for char in text):
            paragraph = soup.new_tag("p")
            paragraph.string = text
            heading.replace_with(paragraph)
            changed = True

    if changed:
        chapter_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _build_curated_toc_entries(chapter_paths, *, language: str = "en") -> list[dict]:
    chapter_info = []
    for chapter_path in chapter_paths:
        if chapter_path.name == "cover.xhtml":
            continue
        heading_text, heading_id = _resolve_heading_target(chapter_path)
        chapter_info.append(
            {
                "path": chapter_path,
                "file_name": chapter_path.name,
                "title": heading_text,
                "id": heading_id,
                "key": _training_book_key(heading_text),
            }
        )

    front_order = [
        "title",
        "copyright",
        "contents",
        "key to symbols used",
        "quick start guide",
        "a final session",
        "general introduction",
        "summary of tactical motifs",
        "instructions",
    ]
    exercise_order = ["easy exercises", "intermediate exercises", "advanced exercises"]
    solution_order = [
        "solutions to easy exercises",
        "solutions to intermediate exercises",
        "solutions to advanced exercises",
    ]
    back_order = ["name index", "sample record sheet", "sample record sheets", "back cover"]

    keyed = {entry["key"]: entry for entry in chapter_info}
    localized = _canonicalize_language(language) == "pl"
    matched_files: set[str] = set()
    toc_entries = [{"file_name": "cover.xhtml", "id": "", "text": "Okładka" if localized else "Cover", "level": 1}]

    def append_group(group_name: str, keys: list[str]) -> None:
        group_entries = [keyed[key] for key in keys if key in keyed]
        if not group_entries:
            return
        first = group_entries[0]
        toc_entries.append(
            {
                "file_name": first["file_name"],
                "id": first["id"],
                "text": group_name,
                "level": 1,
            }
        )
        for entry in group_entries:
            matched_files.add(entry["file_name"])
            toc_entries.append(
                {
                    "file_name": entry["file_name"],
                    "id": entry["id"],
                    "text": _normalize_toc_label(entry["title"]),
                    "level": 2,
                }
            )

    append_group("Wstęp" if localized else "Front Matter", front_order)
    append_group("Ćwiczenia" if localized else "Exercises", exercise_order)
    append_group("Rozwiązania" if localized else "Solutions", solution_order)
    append_group("Dodatki" if localized else "Back Matter", back_order)
    unmatched_entries = [entry for entry in chapter_info if entry["file_name"] not in matched_files]
    if unmatched_entries:
        if len(toc_entries) > 1:
            first = unmatched_entries[0]
            toc_entries.append(
                {
                    "file_name": first["file_name"],
                    "id": first["id"],
                    "text": "Dodatkowe sekcje" if localized else "Additional Sections",
                    "level": 1,
                }
            )
            unmatched_level = 2
        else:
            unmatched_level = 1
        for entry in unmatched_entries:
            toc_entries.append(
                {
                    "file_name": entry["file_name"],
                    "id": entry["id"],
                    "text": _normalize_toc_label(entry["title"]),
                    "level": unmatched_level,
                }
            )
    return toc_entries


def _repair_training_book_package(
    chapter_paths,
    *,
    title: str,
    author: str,
    language: str,
) -> dict[str, object]:
    resolved_title, resolved_author, resolved_language = _derive_package_metadata(
        chapter_paths,
        title=title,
        author=author,
        language=language,
        allow_training_defaults=False,
    )

    exercise_chapters: list[Path] = []
    solution_chapters: list[Path] = []
    exercise_chapter_by_key: dict[str, str] = {}
    for chapter_path in chapter_paths:
        heading_text, _ = _resolve_heading_target(chapter_path)
        key = _training_book_key(heading_text)
        if key in {"easy exercises", "intermediate exercises", "advanced exercises"}:
            _ensure_primary_heading(chapter_path, fallback_title=heading_text)
            exercise_chapters.append(chapter_path)
            exercise_chapter_by_key[key] = chapter_path.name
        elif key.startswith("solutions to"):
            _ensure_primary_heading(chapter_path, fallback_title=heading_text)
            solution_chapters.append(chapter_path)
        elif key == "key to symbols used":
            _repair_symbol_key_chapter(chapter_path)
        elif key == "name index":
            _repair_name_index_chapter(chapter_path)
        elif chapter_path.name in {"chapter_009.xhtml", "chapter_010.xhtml"} or key in {"general introduction", "summary of tactical motifs"}:
            _strip_problem_navigation_from_examples(chapter_path)

    for chapter_path in solution_chapters:
        _cleanup_solution_chapter(chapter_path)

    solution_targets: dict[str, str] = {}
    solution_titles: dict[str, str] = {}
    for chapter_path in solution_chapters:
        soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
        for heading in soup.find_all("h3", class_="solution-entry"):
            match = re.search(r"solution-(\d+)", heading.get("id", ""))
            if not match:
                continue
            exercise_num = match.group(1)
            solution_targets.setdefault(exercise_num, f"{chapter_path.name}#{heading.get('id', '')}")
            solution_titles.setdefault(exercise_num, _normalize_text(heading.get_text(" ", strip=True)))

    exercise_problem_targets: dict[str, str] = {}
    for chapter_path in exercise_chapters:
        exercise_problem_targets.update(
            _repair_exercise_chapter(
                chapter_path,
                solution_targets=solution_targets,
                solution_titles=solution_titles,
            )
        )

    fallback_game_targets = _collect_fallback_game_targets(
        [chapter_path for chapter_path in chapter_paths if chapter_path not in solution_chapters]
    )

    for chapter_path in solution_chapters:
        heading_text, _ = _resolve_heading_target(chapter_path)
        solution_key = _training_book_key(heading_text)
        expected_problem_file = {
            "solutions to easy exercises": exercise_chapter_by_key.get("easy exercises"),
            "solutions to intermediate exercises": exercise_chapter_by_key.get("intermediate exercises"),
            "solutions to advanced exercises": exercise_chapter_by_key.get("advanced exercises"),
        }.get(solution_key)
        updated_xhtml = _rewrite_solution_backlinks(
            chapter_path.read_text(encoding="utf-8"),
            exercise_problem_targets=exercise_problem_targets,
            expected_problem_file=expected_problem_file,
            fallback_game_targets=fallback_game_targets,
        )
        chapter_path.write_text(updated_xhtml, encoding="utf-8")

    return {
        "title": resolved_title,
        "author": resolved_author,
        "language": resolved_language,
        "toc_entries": _build_curated_toc_entries(chapter_paths, language=resolved_language),
    }


def _rewrite_navigation(root_dir: Path, opf_path: Path, *, toc_entries: list[dict], title: str, language: str) -> None:
    nav_path = opf_path.parent / "nav.xhtml"
    toc_path = opf_path.parent / "toc.ncx"
    package_identifier = _resolve_package_identifier(opf_path)
    nav_path.write_text(_build_nav_xhtml(toc_entries=toc_entries, title=title, language=language), encoding="utf-8")
    toc_path.write_text(
        _build_toc_ncx(toc_entries=toc_entries, title=title, package_identifier=package_identifier),
        encoding="utf-8",
    )


def _update_opf_metadata(
    opf_path: Path,
    *,
    title: str,
    author: str,
    language: str,
    chapter_paths,
    toc_entries: list[dict],
    description_seed: str = "",
) -> None:
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(opf_path), parser)
    root = tree.getroot()

    metadata = root.find(".//opf:metadata", NS)
    if metadata is None:
        return

    resolved_title = _normalize_text(title) or "Untitled"
    resolved_author = _normalize_text(author)
    resolved_language = _canonicalize_language(language)
    if _is_placeholder_author(resolved_author):
        resolved_author = "Unknown"
    resolved_description = _resolve_package_description(
        metadata,
        chapter_paths=chapter_paths,
        toc_entries=toc_entries,
        title=resolved_title,
        author=resolved_author,
        language=resolved_language,
        description_seed=description_seed,
    )

    _ensure_package_prefixes(root)
    _ensure_package_identifier(
        root,
        metadata,
        title=resolved_title,
        author=resolved_author,
        language=resolved_language,
    )

    _set_dc_value(metadata, "title", resolved_title)
    creator = _set_dc_value(metadata, "creator", resolved_author)
    creator.set("id", creator.get("id") or "creator")
    _set_dc_value(metadata, "language", resolved_language)
    _set_dc_value(metadata, "description", resolved_description)
    _set_dc_value(metadata, "date", _resolve_publication_date(metadata))
    _upsert_modified_timestamp(metadata)

    _normalize_manifest_image_assets(root, opf_path.parent)
    cover_id = _mark_cover_image(root)
    if cover_id:
        _upsert_named_meta(metadata, "cover", cover_id)
    _ensure_nav_manifest_item(root)
    _ensure_ncx_manifest_item(root)

    tree.write(str(opf_path), encoding="utf-8", xml_declaration=True, pretty_print=False)


def _reorder_opf_spine(opf_path: Path, ordered_files: list[str]) -> None:
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(opf_path), parser)
    root = tree.getroot()
    manifest = root.find(".//opf:manifest", NS)
    spine = root.find(".//opf:spine", NS)
    if manifest is None or spine is None:
        return

    href_to_id: dict[str, str] = {}
    for item in manifest.findall("opf:item", NS):
        href = item.get("href", "")
        item_id = item.get("id", "")
        if href and item_id:
            href_to_id[href] = item_id

    itemrefs = list(spine.findall("opf:itemref", NS))
    itemref_by_idref = {itemref.get("idref", ""): itemref for itemref in itemrefs if itemref.get("idref")}
    ordered_idrefs: list[str] = []
    cover_id = href_to_id.get("cover.xhtml", "")
    nav_id = href_to_id.get("nav.xhtml", "")

    for href in ordered_files:
        item_id = href_to_id.get(href)
        if item_id and item_id not in {cover_id, nav_id} and item_id not in ordered_idrefs:
            ordered_idrefs.append(item_id)

    for itemref in itemrefs:
        item_id = itemref.get("idref", "")
        href = next((name for name, ref_id in href_to_id.items() if ref_id == item_id), "")
        if href == "cover.xhtml":
            continue
        if href == "nav.xhtml":
            continue
        if item_id and item_id not in ordered_idrefs:
            ordered_idrefs.append(item_id)

    def ensure_itemref(item_id: str) -> etree._Element | None:
        if not item_id:
            return None
        itemref = itemref_by_idref.get(item_id)
        if itemref is None:
            itemref = etree.Element(f"{{{OPF_NS}}}itemref")
            itemref.set("idref", item_id)
            itemref_by_idref[item_id] = itemref
        return itemref

    new_sequence: list[etree._Element] = []
    cover_ref = ensure_itemref(cover_id)
    nav_ref = ensure_itemref(nav_id)
    if cover_ref is not None:
        if cover_ref.get("linear") == "no":
            del cover_ref.attrib["linear"]
        new_sequence.append(cover_ref)
    for item_id in ordered_idrefs:
        itemref = ensure_itemref(item_id)
        if itemref is not None:
            new_sequence.append(itemref)
    if nav_ref is not None:
        nav_ref.set("linear", "no")
        new_sequence.append(nav_ref)

    for child in list(spine):
        if child.tag == f"{{{OPF_NS}}}itemref":
            spine.remove(child)
    for itemref in new_sequence:
        spine.append(itemref)

    tree.write(str(opf_path), encoding="utf-8", xml_declaration=True, pretty_print=False)


def _write_default_css(root_dir: Path) -> None:
    css_path = root_dir / "EPUB" / "style" / "default.css"
    if css_path.exists():
        css_path.write_text(KINDLE_CSS, encoding="utf-8")


def _pack_epub(root_dir: Path) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        mimetype_path = root_dir / "mimetype"
        if mimetype_path.exists():
            archive.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)

        for file_path in sorted(root_dir.rglob("*")):
            if file_path.is_dir() or file_path == mimetype_path:
                continue
            archive.write(
                file_path,
                file_path.relative_to(root_dir).as_posix(),
                compress_type=zipfile.ZIP_DEFLATED,
            )
    return output.getvalue()


def _set_dc_value(metadata: etree._Element, local_name: str, value: str) -> etree._Element:
    elements = metadata.findall(f"dc:{local_name}", NS)
    element = elements[0] if elements else etree.SubElement(metadata, f"{{{DC_NS}}}{local_name}")
    element.text = value
    for duplicate in elements[1:]:
        metadata.remove(duplicate)
    return element


def _upsert_modified_timestamp(metadata: etree._Element) -> None:
    modified = None
    for meta in metadata.findall(f"{{{OPF_NS}}}meta"):
        if meta.get("property") == "dcterms:modified":
            modified = meta
            break
    if modified is None:
        modified = etree.SubElement(metadata, f"{{{OPF_NS}}}meta")
        modified.set("property", "dcterms:modified")
    modified.text = _current_utc_timestamp()


def _mark_cover_image(root: etree._Element) -> str | None:
    manifest = root.find(".//opf:manifest", NS)
    if manifest is None:
        return None
    cover_candidates: list[etree._Element] = []
    for item in manifest.findall("opf:item", NS):
        href = item.get("href", "")
        item_id = item.get("id", "")
        media_type = item.get("media-type", "")
        if not media_type.startswith("image/"):
            continue
        if item_id == "cover-image":
            cover_candidates.insert(0, item)
        elif "cover" in href.lower():
            cover_candidates.append(item)

    chosen_cover = cover_candidates[0] if cover_candidates else None
    for item in manifest.findall("opf:item", NS):
        properties = [part for part in (item.get("properties") or "").split() if part and part != "cover-image"]
        if chosen_cover is not None and item is chosen_cover:
            properties.append("cover-image")
        if properties:
            item.set("properties", " ".join(dict.fromkeys(properties)))
        elif "properties" in item.attrib:
            del item.attrib["properties"]
    return chosen_cover.get("id") if chosen_cover is not None else None


def _ensure_nav_manifest_item(root: etree._Element) -> None:
    manifest = root.find(".//opf:manifest", NS)
    if manifest is None:
        return
    nav_item = None
    for item in manifest.findall("opf:item", NS):
        href = item.get("href", "")
        if href.endswith("nav.xhtml"):
            nav_item = item
            break
    if nav_item is None:
        nav_item = etree.SubElement(manifest, f"{{{OPF_NS}}}item")
        nav_item.set("id", "nav")
        nav_item.set("href", "nav.xhtml")
        nav_item.set("media-type", "application/xhtml+xml")
    properties = [part for part in (nav_item.get("properties") or "").split() if part and part != "nav"]
    properties.append("nav")
    nav_item.set("properties", " ".join(dict.fromkeys(properties)))


def _ensure_ncx_manifest_item(root: etree._Element) -> None:
    manifest = root.find(".//opf:manifest", NS)
    spine = root.find(".//opf:spine", NS)
    if manifest is None or spine is None:
        return
    ncx_item = None
    for item in manifest.findall("opf:item", NS):
        if item.get("href", "").endswith("toc.ncx"):
            ncx_item = item
            break
    if ncx_item is None:
        ncx_item = etree.SubElement(manifest, f"{{{OPF_NS}}}item")
        ncx_item.set("id", "ncx")
        ncx_item.set("href", "toc.ncx")
    ncx_item.set("media-type", "application/x-dtbncx+xml")
    if ncx_item.get("id"):
        spine.set("toc", ncx_item.get("id"))


def _ensure_package_prefixes(root: etree._Element) -> None:
    prefix_pairs = _parse_package_prefixes(root.get("prefix", ""))
    for prefix, uri in PACKAGE_PREFIXES.items():
        prefix_pairs.setdefault(prefix, uri)
    if prefix_pairs:
        root.set("prefix", " ".join(f"{prefix}: {uri}" for prefix, uri in prefix_pairs.items()))


def _parse_package_prefixes(prefix_value: str) -> dict[str, str]:
    tokens = [token for token in (prefix_value or "").split() if token]
    prefix_pairs: dict[str, str] = {}
    index = 0
    while index + 1 < len(tokens):
        key = tokens[index].rstrip(":")
        value = tokens[index + 1]
        prefix_pairs[key] = value
        index += 2
    return prefix_pairs


def _ensure_package_identifier(
    root: etree._Element,
    metadata: etree._Element,
    *,
    title: str,
    author: str,
    language: str,
) -> str:
    identifier_id = root.get("unique-identifier", "") or "publication-id"
    identifiers = metadata.findall("dc:identifier", NS)
    target = next((item for item in identifiers if item.get("id") == identifier_id), None)
    if target is None:
        target = identifiers[0] if identifiers else etree.SubElement(metadata, f"{{{DC_NS}}}identifier")
    target.set("id", target.get("id") or identifier_id)
    root.set("unique-identifier", target.get("id"))

    current_value = _normalize_text(target.text or "")
    if not current_value or PLACEHOLDER_IDENTIFIER_RE.fullmatch(current_value):
        current_value = _build_package_identifier(title=title, author=author, language=language)
    target.text = current_value
    return current_value


def _build_package_identifier(*, title: str, author: str, language: str) -> str:
    seed = "|".join(
        [
            _normalize_key(title) or "untitled",
            _normalize_key(author) or "unknown",
            _canonicalize_language(language),
        ]
    )
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'kindlemaster:{seed}')}"


def _resolve_publication_date(metadata: etree._Element) -> str:
    for element in metadata.findall("dc:date", NS):
        value = _normalize_text(element.text or "")
        if _looks_like_w3cdtf(value):
            return value
    for meta in metadata.findall(f"{{{OPF_NS}}}meta"):
        if meta.get("property") == "dcterms:modified":
            value = _normalize_text(meta.text or "")
            if _looks_like_w3cdtf(value):
                return value[:10]
    return _current_utc_date()


def _looks_like_w3cdtf(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"\d{4}(?:-\d{2}(?:-\d{2})?)?(?:T\d{2}:\d{2}:\d{2}Z)?",
            value or "",
        )
    )


def _current_utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _current_utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _resolve_package_description(
    metadata: etree._Element,
    *,
    chapter_paths,
    toc_entries: list[dict],
    title: str,
    author: str,
    language: str,
    description_seed: str = "",
) -> str:
    existing = next(
        (
            _normalize_text(element.text or "")
            for element in metadata.findall("dc:description", NS)
            if _description_is_useful(_normalize_text(element.text or ""), title=title, author=author)
        ),
        "",
    )
    if existing:
        return existing
    if _description_is_useful(description_seed, title=title, author=author):
        return _truncate_metadata_text(description_seed, limit=320)
    return _derive_package_description(
        chapter_paths,
        toc_entries=toc_entries,
        title=title,
        author=author,
        language=language,
    )


def _description_is_useful(value: str, *, title: str, author: str) -> bool:
    normalized = _normalize_text(value)
    if len(normalized) < 24:
        return False
    normalized_key = _normalize_key(normalized)
    return normalized_key not in {
        _normalize_key(title),
        _normalize_key(author),
    }


def _extract_description_from_chapters(chapter_paths, *, title: str, author: str) -> str:
    skip_keys = {
        _normalize_key(title),
        _normalize_key(author),
        *DESCRIPTION_SKIP_KEYS,
    }
    for chapter_path in chapter_paths[:4]:
        soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
        for node in soup.find_all(["p", "blockquote"]):
            text = _normalize_text(node.get_text(" ", strip=True))
            if len(text) < 60 or len(text) > 450:
                continue
            normalized_key = _normalize_key(text)
            if normalized_key in skip_keys:
                continue
            if PAGE_TITLE_RE.fullmatch(text) or AUTHOR_LINE_RE.match(text):
                continue
            if re.search(r"(?i)\b(?:https?://|www\.|doi:)", text) or REFERENCE_LINK_RE.search(text):
                continue
            if _looks_like_reference_entry_text(text):
                continue
            if node.find_parent(["ul", "ol", "dl", "table"]) is not None:
                continue
            if any(bad_term in normalized_key for bad_term in BAD_HEADING_TERMS):
                continue
            return _truncate_metadata_text(text, limit=320)
    return ""


def _derive_package_description(
    chapter_paths,
    *,
    toc_entries: list[dict],
    title: str,
    author: str,
    language: str,
) -> str:
    extracted = _extract_description_from_chapters(chapter_paths, title=title, author=author)
    if extracted:
        return extracted

    summary_labels = [
        _normalize_text(entry.get("text", ""))
        for entry in toc_entries
        if _normalize_key(entry.get("text", "")) not in DESCRIPTION_SKIP_KEYS
    ]
    if _canonicalize_language(language) == "pl":
        intro = f"Wydanie EPUB publikacji {title}"
        if not _is_placeholder_author(author):
            intro += f", autorstwa {author}"
        if summary_labels:
            preview = ", ".join(summary_labels[:3])
            return _truncate_metadata_text(f"{intro}. Zawiera sekcje: {preview}.", limit=320)
        return _truncate_metadata_text(f"{intro}.", limit=320)

    intro = f"Reflowable EPUB edition of {title}"
    if not _is_placeholder_author(author):
        intro += f" by {author}"
    if summary_labels:
        preview = ", ".join(summary_labels[:3])
        return _truncate_metadata_text(f"{intro}. Includes {preview}.", limit=320)
    return _truncate_metadata_text(f"{intro}.", limit=320)


def _truncate_metadata_text(value: str, *, limit: int) -> str:
    normalized = _normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    truncated = normalized[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{truncated}..."


def _upsert_named_meta(metadata: etree._Element, name: str, content: str) -> None:
    metas = [meta for meta in metadata.findall(f"{{{OPF_NS}}}meta") if meta.get("name") == name]
    target = metas[0] if metas else etree.SubElement(metadata, f"{{{OPF_NS}}}meta")
    target.set("name", name)
    target.set("content", content)
    for duplicate in metas[1:]:
        metadata.remove(duplicate)


def _normalize_manifest_image_assets(root: etree._Element, package_dir: Path) -> None:
    manifest = root.find(".//opf:manifest", NS)
    if manifest is None:
        return
    for item in manifest.findall("opf:item", NS):
        media_type = item.get("media-type", "")
        if not media_type.startswith("image/"):
            continue
        href = item.get("href", "")
        relative_path = Path(href)
        asset_path = package_dir / relative_path
        detected_media_type = _sniff_image_media_type(asset_path)
        if detected_media_type:
            item.set("media-type", detected_media_type)
        desired_suffix = _media_type_suffix(detected_media_type)
        if not desired_suffix or relative_path.suffix.lower() == desired_suffix:
            continue
        target_relative_path = _next_available_asset_path(relative_path, desired_suffix, package_dir=package_dir)
        target_path = package_dir / target_relative_path
        if asset_path.exists() and asset_path != target_path:
            asset_path.rename(target_path)
            item.set("href", target_relative_path.as_posix())
            _rewrite_asset_references(package_dir, href, target_relative_path.as_posix())


def _sniff_image_media_type(file_path: Path) -> str | None:
    if not file_path.exists() or not file_path.is_file():
        return None
    header = file_path.read_bytes()[:512]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    lowered = header.lstrip().lower()
    if lowered.startswith(b"<svg") or (lowered.startswith(b"<?xml") and b"<svg" in lowered):
        return "image/svg+xml"
    return None


def _media_type_suffix(media_type: str | None) -> str:
    return {
        "image/gif": ".gif",
        "image/jpeg": ".jpeg",
        "image/png": ".png",
        "image/svg+xml": ".svg",
        "image/webp": ".webp",
    }.get(media_type or "", "")


def _next_available_asset_path(relative_path: Path, suffix: str, *, package_dir: Path) -> Path:
    candidate = relative_path.with_suffix(suffix)
    if not (package_dir / candidate).exists() or candidate == relative_path:
        return candidate
    counter = 2
    while True:
        candidate = relative_path.with_name(f"{relative_path.stem}-{counter}{suffix}")
        if not (package_dir / candidate).exists():
            return candidate
        counter += 1


def _rewrite_asset_references(package_dir: Path, old_href: str, new_href: str) -> None:
    for xhtml_path in package_dir.glob("*.xhtml"):
        soup = BeautifulSoup(xhtml_path.read_text(encoding="utf-8"), "xml")
        changed = False
        for node in soup.find_all(src=True):
            if node.get("src") == old_href:
                node["src"] = new_href
                changed = True
        for node in soup.find_all(href=True):
            if node.get("href") == old_href:
                node["href"] = new_href
                changed = True
        if changed:
            xhtml_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _toc_entry_href(entry: dict) -> str:
    return entry["file_name"] + (f'#{entry["id"]}' if entry.get("id") else "")


def _normalize_toc_entries_for_render(toc_entries: list[dict]) -> list[dict]:
    normalized_entries: list[dict] = []
    for entry in toc_entries:
        label = _normalize_text(entry.get("text", ""))
        if not label:
            continue
        try:
            level = int(entry.get("level", 1) or 1)
        except (TypeError, ValueError):
            level = 1
        normalized_entries.append({**entry, "text": label, "level": max(1, min(level, 3))})
    if normalized_entries:
        return normalized_entries
    return [{"file_name": "chapter_001.xhtml", "id": "", "text": "Start", "level": 1}]


def _build_toc_tree(toc_entries: list[dict]) -> list[dict]:
    roots: list[dict] = []
    stack: list[dict] = []

    for entry in _normalize_toc_entries_for_render(toc_entries):
        node_level = entry["level"]
        while stack and stack[-1]["level"] >= node_level:
            stack.pop()
        if node_level > 1 and not stack:
            node_level = 1
        elif stack and node_level > stack[-1]["level"] + 1:
            node_level = stack[-1]["level"] + 1
        node = {**entry, "level": node_level, "children": []}
        if stack:
            stack[-1]["children"].append(node)
        else:
            roots.append(node)
        stack.append(node)

    return roots


def _render_nav_list_items(nodes: list[dict]) -> str:
    parts: list[str] = []
    for node in nodes:
        href = html.escape(_toc_entry_href(node))
        label = html.escape(node["text"])
        parts.append(f'<li><a href="{href}">{label}</a>')
        if node["children"]:
            parts.append("<ol>")
            parts.append(_render_nav_list_items(node["children"]))
            parts.append("</ol>")
        parts.append("</li>")
    return "".join(parts)


def _iter_toc_nodes(nodes: list[dict]):
    for node in nodes:
        yield node
        yield from _iter_toc_nodes(node["children"])


def _append_ncx_nav_points(nodes: list[dict], lines: list[str], state: dict[str, object], *, indent: str = "    ") -> None:
    for node in nodes:
        href = _toc_entry_href(node)
        label = html.escape(node["text"])
        play_order_by_href = state.setdefault("play_order_by_href", {})
        if not isinstance(play_order_by_href, dict):
            play_order_by_href = {}
            state["play_order_by_href"] = play_order_by_href
        play_order = play_order_by_href.get(href)
        if play_order is None:
            play_order = int(state["play_order"])
            play_order_by_href[href] = play_order
            state["play_order"] = play_order + 1
        nav_id = int(state["nav_id"])
        state["nav_id"] = nav_id + 1
        lines.append(f'{indent}<navPoint id="navPoint-{nav_id}" playOrder="{play_order}">')
        lines.append(f"{indent}  <navLabel><text>{label}</text></navLabel>")
        lines.append(f'{indent}  <content src="{html.escape(href)}"/>')
        if node["children"]:
            _append_ncx_nav_points(node["children"], lines, state=state, indent=indent + "  ")
        lines.append(f"{indent}</navPoint>")


def _navigation_labels(language: str) -> dict[str, str]:
    if _canonicalize_language(language) == "pl":
        return {
            "doc_title": "Spis treści",
            "toc_heading": "Spis treści",
            "landmarks_heading": "Punkty orientacyjne",
            "cover": "Okładka",
            "toc": "Spis treści",
            "body": "Początek tekstu",
            "start": "Start",
        }
    return {
        "doc_title": "Table of Contents",
        "toc_heading": "Table of Contents",
        "landmarks_heading": "Landmarks",
        "cover": "Cover",
        "toc": "Table of Contents",
        "body": "Start of Text",
        "start": "Start",
    }


def _build_nav_xhtml(*, toc_entries: list[dict], title: str, language: str) -> str:
    normalized_entries = _normalize_toc_entries_for_render(toc_entries)
    toc_tree = _build_toc_tree(normalized_entries)
    labels = _navigation_labels(language)
    list_items = _render_nav_list_items(toc_tree) or f'<li><a href="chapter_001.xhtml">{html.escape(labels["start"])}</a></li>'

    first_content_href = "chapter_001.xhtml"
    if normalized_entries:
        first = next(
            (
                entry
                for entry in normalized_entries
                if not (
                    entry["file_name"] == "cover.xhtml"
                    or _normalize_key(entry["text"]) in {"cover", "table of contents", "contents", "spis treĹ›ci"}
                )
            ),
            normalized_entries[0],
        )
        first_content_href = _toc_entry_href(first)
    nav_body = (
        '<nav epub:type="toc" id="toc">'
        f"<h1>{html.escape(labels['toc_heading'])}</h1>"
        "<ol>"
        + list_items
        + "</ol></nav>"
        '<nav epub:type="landmarks" id="landmarks" hidden="">'
        f"<h2>{html.escape(labels['landmarks_heading'])}</h2>"
        "<ol>"
        f'<li><a epub:type="cover" href="cover.xhtml">{html.escape(labels["cover"])}</a></li>'
        f'<li><a epub:type="toc" href="nav.xhtml">{html.escape(labels["toc"])}</a></li>'
        f'<li><a epub:type="bodymatter" href="{html.escape(first_content_href)}">{html.escape(labels["body"])}</a></li>'
        "</ol></nav>"
    )
    return _build_xhtml_document(title=labels["doc_title"], body_html=nav_body, language=language)


def _build_toc_ncx(*, toc_entries: list[dict], title: str, package_identifier: str) -> str:
    toc_tree = _build_toc_tree(toc_entries)
    flat_nodes = list(_iter_toc_nodes(toc_tree))
    max_depth = max((node["level"] for node in flat_nodes), default=1)

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">',
        "  <head>",
        f'    <meta name="dtb:uid" content="{html.escape(package_identifier)}"/>',
        f'    <meta name="dtb:depth" content="{max_depth}"/>',
        '    <meta name="dtb:totalPageCount" content="0"/>',
        '    <meta name="dtb:maxPageNumber" content="0"/>',
        "  </head>",
        f"  <docTitle><text>{html.escape(title)}</text></docTitle>",
        "  <navMap>",
    ]
    _append_ncx_nav_points(toc_tree, lines, state={"nav_id": 1, "play_order": 1, "play_order_by_href": {}})
    lines.extend(["  </navMap>", "</ncx>"])
    return "\n".join(lines)


def _resolve_package_identifier(opf_path: Path) -> str:
    root = etree.parse(str(opf_path)).getroot()
    package_identifier_id = root.get("unique-identifier", "")
    metadata = root.find(".//opf:metadata", NS)
    if metadata is not None and package_identifier_id:
        for identifier in metadata.findall("dc:identifier", NS):
            if identifier.get("id") == package_identifier_id:
                value = (identifier.text or "").strip()
                if value:
                    return value
    if metadata is not None:
        for identifier in metadata.findall("dc:identifier", NS):
            value = (identifier.text or "").strip()
            if value:
                return value
    return "kindlemaster-cleanup"


def _build_xhtml_document(*, title: str, body_html: str, language: str) -> str:
    document = (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<!DOCTYPE html>\n"
        f'<html xmlns="{XHTML_NS}" xmlns:epub="http://www.idpf.org/2007/ops" '
        'epub:prefix="z3998: http://www.daisy.org/z3998/2012/vocab/structure/#" '
        f'lang="{html.escape(language)}" xml:lang="{html.escape(language)}">\n'
        "  <head>\n"
        f"    <title>{html.escape(title)}</title>\n"
        '    <link href="style/default.css" rel="stylesheet" type="text/css"/>\n'
        "  </head>\n"
        f"  <body>{body_html}</body>\n"
        "</html>\n"
    )
    return _repair_orphaned_list_markup(document)


def _repair_orphaned_list_markup(document: str) -> str:
    repaired = re.sub(
        r"<ul\s*/>\s*((?:<li\b[^>]*>.*?</li>\s*)+)",
        lambda match: f"<ul>{match.group(1)}</ul>",
        document,
        flags=re.DOTALL,
    )
    repaired = re.sub(
        r"<ol\s*/>\s*((?:<li\b[^>]*>.*?</li>\s*)+)",
        lambda match: f"<ol>{match.group(1)}</ol>",
        repaired,
        flags=re.DOTALL,
    )
    return repaired


def _serialize_soup_document(soup: BeautifulSoup) -> str:
    html_tag = soup.find("html")
    if html_tag is not None:
        html_tag["xmlns"] = XHTML_NS
        html_tag["xmlns:epub"] = "http://www.idpf.org/2007/ops"
        html_tag["epub:prefix"] = "z3998: http://www.daisy.org/z3998/2012/vocab/structure/#"
    serialized = str(soup)
    serialized = re.sub(r"^\s*<\?xml[^>]*>\s*", "", serialized)
    serialized = re.sub(r"^\s*<!DOCTYPE[^>]*>\s*", "", serialized, flags=re.IGNORECASE)
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<!DOCTYPE html>\n"
        + serialized
    )


def _repeated_text_action(text: str, *, repeated_counts: Counter, title: str, author: str, is_top: bool) -> str | None:
    normalized_key = _normalize_key(text)
    if normalized_key in {_normalize_key(title), _normalize_key(author)}:
        return "drop"
    count = repeated_counts.get(text, 0)
    if count < 4:
        return None
    if any(pattern.search(text) for pattern in PRESERVE_FIRST_REPEAT_PATTERNS):
        return "keep-first-heading" if is_top else "drop"
    return "drop"


def _is_true_solution_entry(text: str) -> bool:
    if len(text) > 160:
        return False
    return TRUE_SOLUTION_ENTRY_RE.match(text) is not None


def _looks_like_heading_text(text: str) -> bool:
    lower = text.lower()
    if not text or len(text) > 90:
        return False
    if _looks_like_bullet_item(text):
        return False
    if _is_true_solution_entry(text):
        return False
    if _looks_like_game_caption(text):
        return False
    if _looks_like_figure_caption(text):
        return False
    if _looks_like_chess_fragment(text):
        return False
    if _looks_like_index_paragraph(text):
        return False
    if PAGE_NUMBER_RE.match(text):
        return False
    if re.search(r"\(\d{4}\)$", text):
        return False
    if text[:1].islower():
        return False
    if re.search(r"\b\d{1,4}(?:,\s*\d{1,4}){2,}\b", text):
        return False
    if any(term in lower for term in BAD_HEADING_TERMS):
        return False
    if text.endswith((".", "?", "!")) and len(text.split()) > 4:
        return False
    words = [word for word in re.split(r"\s+", text.replace("–", " ")) if any(ch.isalpha() for ch in word)]
    if not words:
        return False
    significant_words = [word for word in words if word.lower() not in MINOR_HEADING_WORDS]
    if not significant_words:
        significant_words = words
    capitalized = sum(1 for word in significant_words if word[0].isupper())
    ratio = capitalized / len(significant_words)
    return ratio >= 0.55


def _looks_like_figure_caption(text: str) -> bool:
    normalized = _normalize_text(text)
    return bool(FIGURE_CAPTION_RE.match(normalized))


def _looks_like_author_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 90:
        return False
    if ROLE_WORD_RE.search(normalized):
        return True
    words = LETTER_TOKEN_RE.findall(normalized)
    if not (2 <= len(words) <= 4):
        return False
    if any(word.lower() in MINOR_WORDS for word in words):
        return False
    return all(word[:1].isupper() for word in words)


def _looks_like_signature_date_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 64:
        return False
    return bool(SIGNATURE_DATE_RE.match(normalized))


def _looks_like_signature_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 70:
        return False
    if ROLE_WORD_RE.search(normalized) or _looks_like_heading_text(normalized):
        return False
    simplified = re.sub(r"[`´'’]", "", normalized)
    words = LETTER_TOKEN_RE.findall(simplified)
    if not (2 <= len(words) <= 4):
        return False
    if any(word.lower() in MINOR_WORDS for word in words):
        return False
    return bool(SIGNATURE_NAME_RE.match(simplified))


def _looks_like_signature_person_name(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 70:
        return False
    simplified = re.sub(r"[`Â´'â€™.-]", "", normalized)
    words = LETTER_TOKEN_RE.findall(simplified)
    if not (2 <= len(words) <= 4):
        return False
    if any(word.lower() in MINOR_WORDS for word in words):
        return False
    if not all(word[:1].isupper() for word in words):
        return False
    return True


def _looks_like_signature_meta_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 60:
        return False
    if _looks_like_signature_date_line(normalized) or _looks_like_signature_line(normalized):
        return False
    if normalized.endswith((".", "!", "?")):
        return False
    if len(normalized.split()) > 5:
        return False
    simplified = re.sub(r"[`´'’]", "", normalized)
    return bool(SIGNATURE_META_RE.match(simplified))


def _looks_like_subtitle_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) < 16 or len(normalized) > 170:
        return False
    if NUMERIC_VALUE_CONTINUATION_RE.match(normalized):
        return False
    if normalized.endswith((".", "!", "?")) and len(normalized.split()) > 14:
        return False
    if _looks_like_heading_text(normalized) or _looks_like_author_line(normalized):
        return False
    if _looks_like_figure_caption(normalized):
        return False
    return len(normalized.split()) >= 3


def _should_merge_paragraphs(previous_text: str, current_text: str) -> bool:
    previous_text = previous_text.strip()
    current_text = current_text.strip()
    if not previous_text or not current_text:
        return False
    if SPLIT_NUMERIC_VALUE_RE.fullmatch(current_text):
        return False
    if _looks_like_split_numeric_value(previous_text, current_text):
        return True
    if _looks_like_heading_text(current_text):
        return False
    if current_text.startswith(("✓", "†", "‡", ")", "]", ",")):
        return True
    if previous_text.endswith(("-", "–", "—", "(", "/", "†", "‡", ",")):
        return True
    if previous_text.endswith((";", ":")):
        return True
    if current_text[:1].islower():
        return True
    if _looks_like_chess_fragment(current_text) and not previous_text.endswith((".", "!", "?")):
        return True
    if not previous_text.endswith((".", "!", "?", "…")):
        return True
    return False


def _looks_like_chess_fragment(text: str) -> bool:
    return bool(
        re.match(r"^\d+\.(?:\.\.)?\s*\S+", text)
        or re.match(r"^[KQRBN](?:[a-h1-8xO\-+=#†‡!?]{1,6})(?:\b|$)", text)
        or re.match(r"^[a-h](?:x?[a-h]?[1-8](?:=[QRBN])?[+#]?|[1-8])(?:\b|$)", text)
    )


def _looks_like_game_caption(text: str) -> bool:
    normalized = _normalize_text(text)
    normalized = re.sub(r"^\d+\.\s+", "", normalized)
    return bool(GAME_CAPTION_RE.match(normalized))


def _should_include_in_toc(text: str, level: int) -> bool:
    if level > 3:
        return False
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if _looks_like_synthetic_section_label(normalized):
        return False
    if _looks_like_truncated_heading(normalized):
        return False
    if re.match(r"^[•·▪◦]\s*", normalized):
        return False
    if re.match(r"^\.\d+\b", normalized):
        return False
    if _is_generic_schema_heading_label(normalized):
        return False
    if _looks_like_table_header_heading(normalized):
        return False
    if _looks_like_game_caption(normalized):
        return False
    if re.search(r"(?i)\b(ciąg dalszy|wprowadzenie|continued)\b", normalized):
        return False
    if re.search(r"(?i)\b(reklama|materiał sponsorowany|material sponsorowany|advertorial)\b", normalized):
        return False
    # Always accept explicit "chapter/part/appendix/contents" style headings.
    if any(pattern.match(normalized) for pattern in TOC_HEADING_PATTERNS):
        return True
    # Otherwise accept any reasonable heading text so real chapter titles
    # (e.g. "Introduction", "Business Analysis Key Concepts") reach the TOC.
    stripped = normalized.strip()
    if len(stripped) < 2 or len(stripped) > 160:
        return False
    # Reject pure numeric/bullet fragments.
    if re.fullmatch(r"[\d\.\s•·\-–—]+", stripped):
        return False
    # Must contain at least one letter.
    if not re.search(r"[A-Za-zÀ-ÿĀ-ſ]", stripped):
        return False
    # Reject sentences (end with a period) unless they are short titles.
    if stripped.endswith(".") and len(stripped) > 80:
        return False
    return True


def _looks_like_synthetic_section_label(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    pattern = re.compile(
        r"^(?:chapter|section|part|appendix|page|node|item|chapter[_ -]?page)[_ -]?\d+[a-z]?$",
        re.IGNORECASE,
    )
    if pattern.fullmatch(normalized):
        return True
    flattened = re.sub(r"[_\s]+", " ", normalized).strip().lower()
    return bool(pattern.fullmatch(flattened))


def _looks_like_truncated_heading(text: str) -> bool:
    normalized = _normalize_text(text).strip(" -:;,.")
    if not normalized or len(normalized) > 120:
        return False
    if normalized.endswith(("/", "&", "-", "–", "—", "(")):
        return True
    words = [token.strip("()[]{}.,;:!?/") for token in normalized.split()]
    words = [token for token in words if token]
    if len(words) < 2 or len(words) > 8:
        return False
    last_word = words[-1].lower()
    if last_word in MINOR_WORDS:
        return True
    if len(words) >= 2 and " ".join(word.lower() for word in words[-2:]) in {
        "of the",
        "in the",
        "to the",
        "for the",
        "and the",
        "or the",
    }:
        return True
    return False


def _is_notation_heavy(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    token_count = len(NOTATION_TOKEN_RE.findall(normalized))
    return token_count >= 3 or bool(re.match(r"^\d+\.(?:\.\.)?\s*\S+", normalized))


def _looks_like_split_numeric_value(previous_text: str, current_text: str) -> bool:
    previous = _normalize_text(previous_text)
    current = _normalize_text(current_text)
    if not previous or not current:
        return False
    if not SPLIT_NUMERIC_VALUE_RE.fullmatch(previous):
        return False
    return bool(NUMERIC_VALUE_CONTINUATION_RE.match(current))


def _merge_separator(previous_text: str, current_text: str) -> str:
    if _looks_like_split_numeric_value(previous_text, current_text):
        return ""
    if previous_text.endswith("-") and current_text[:1].isalpha():
        return ""
    if previous_text.endswith(("(", "/")) or current_text.startswith((")", ",", ".", ";", ":", "!", "?")):
        return ""
    return " "


def _lexical_zipf(word: str) -> float:
    if not word or _zipf_frequency is None:
        return 0.0
    lowered = word.lower()
    try:
        return max(_zipf_frequency(lowered, "pl"), _zipf_frequency(lowered, "en"))
    except Exception:
        return 0.0


def _should_join_split_word(left: str, right: str) -> bool:
    left_clean = left.strip()
    right_clean = right.strip()
    if len(left_clean) < 2 or len(right_clean) < 2:
        return False
    total_len = len(left_clean) + len(right_clean)
    if total_len < 6 or total_len > 22:
        return False
    left_is_stop = left_clean.lower() in SPLIT_JOIN_STOPWORDS
    right_is_stop = right_clean.lower() in SPLIT_JOIN_STOPWORDS
    if not left_clean[-1].isalpha() or not right_clean[0].isalpha():
        return False

    combined = f"{left_clean}{right_clean}"
    joined_score = _lexical_zipf(combined)
    left_score = _lexical_zipf(left_clean)
    right_score = _lexical_zipf(right_clean)
    if joined_score < 2.8:
        return False
    if (left_is_stop or right_is_stop) and not ((left_score <= 2.0 or right_score <= 2.0) and joined_score >= 3.3):
        return False
    if (left_score <= 1.8 or right_score <= 1.8) and joined_score >= 3.0:
        return True
    if min(len(left_clean), len(right_clean)) <= 3 and joined_score >= 3.5 and joined_score >= max(left_score, right_score) - 1.4:
        return True
    if joined_score >= max(left_score, right_score) + 1.15:
        return True
    if joined_score >= 4.5 and (left_score + right_score) / 2 <= 4.0:
        return True
    return False


def _repair_pdf_split_words(text: str) -> str:
    if not text or " " not in text:
        return text

    pattern = re.compile(r"\b([A-Za-zÀ-ÿĀ-ž]{2,10})\s+([A-Za-zÀ-ÿĀ-ž]{2,14})\b")
    repaired = text
    for _ in range(3):
        changed = False

        def _replace(match: re.Match) -> str:
            nonlocal changed
            left, right = match.group(1), match.group(2)
            if _should_join_split_word(left, right):
                changed = True
                return f"{left}{right}"
            return match.group(0)

        repaired = pattern.sub(_replace, repaired)
        if not changed:
            break
    return repaired


def _repair_text_node(node_text: str) -> str:
    repaired = node_text or ""
    if _ftfy_fix_text is not None:
        try:
            repaired = _ftfy_fix_text(repaired)
        except Exception:
            pass
    return _repair_pdf_split_words(repaired)


def _should_apply_chess_normalization(text: str) -> bool:
    sample = _normalize_text_light(text)
    if not sample:
        return False
    token_count = len(NOTATION_TOKEN_RE.findall(sample))
    if token_count >= 3 or _looks_like_chess_fragment(sample):
        return True
    if re.search(r"\b(?:O-O(?:-O)?|1-0|0-1|0\.5-0\.5|\u00bd-\u00bd|\*)\b", sample):
        return True
    if re.search(r"\b[a-h]x?[a-h]?[1-8](?:=[QRBN])?[+#]?\b", sample):
        return True
    return False


def _normalize_text_light(text: str) -> str:
    normalized = html.unescape(text or "")
    normalized = normalized.replace("\u00ad", "")
    normalized = normalized.replace("\xa0", " ")
    for broken, fixed in MOJIBAKE_MAP.items():
        normalized = normalized.replace(broken, fixed)
    normalized = REGISTERED_SUFFIX_MOJIBAKE_RE.sub(r"\g<word>®", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_chess_notation_text(text: str) -> str:
    normalized = text or ""
    san_token = r"(?:O-O(?:-O)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?)"
    eval_token = r"(?:\+\u2013|\u2013\+|\+=|=\+|\u00b1|\u2213)"
    normalized = normalized.replace("â€ ", "\u2020").replace("â€ˇ", "\u2021").replace("âś“", "\u2713")
    normalized = normalized.replace("1–0", "1-0").replace("0–1", "0-1").replace("½–½", "½-½")
    normalized = normalized.replace("0.5–0.5", "0.5-0.5")
    normalized = DAGGER_RE.sub("+", normalized)
    normalized = CHECKMARK_RE.sub(" ", normalized)
    normalized = PROMOTION_SPACING_RE.sub(r"=\1", normalized)
    normalized = re.sub(rf"({san_token})\s+mate\b(?!\s+in\b)", r"\1#", normalized, flags=re.IGNORECASE)
    normalized = re.sub(rf"({san_token})\s+\+(?=[!?.,;:]|\s|$)", r"\1+", normalized)
    normalized = re.sub(rf"({san_token})\s+#(?=[!?.,;:]|\s|$)", r"\1#", normalized)
    normalized = re.sub(rf"(?<=[A-Za-z0-9])([+#])\s*({eval_token})", r"\1 (\2)", normalized)
    normalized = re.sub(r"([A-Za-z]{3,})#(?=\s)", r"\1 #", normalized)
    normalized = re.sub(r"\bwith\s+#(?=\s|[.,;:!?]|$)", "with mate", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bavoid\s+#(?=\s|[.,;:!?]|$)", "avoid mate", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bback-rank\s+#(?=\s|[.,;:!?]|$)", "back-rank mate", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bsmothered\s+#(?=\s|[.,;:!?]|$)", "smothered mate", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bof\s+#(?=\s|[.,;:!?]|$)", "of mate", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bthe\s+#(?=\s|[.,;:!?]|$)", "the mate", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\+\s+([!?])", r"+\1", normalized)
    normalized = re.sub(r"#\s+([!?])", r"#\1", normalized)
    normalized = re.sub(r"\s{2,}", " ", normalized)
    return normalized


def _normalize_text(text: str) -> str:
    normalized = html.unescape(text or "")
    normalized = _repair_text_node(normalized)
    normalized = normalized.replace("Â®", "®").replace("Â©", "©").replace("Â·", "·")
    normalized = normalized.replace("â€™", "'").replace("â€ś", "\"").replace("â€ť", "\"")
    normalized = normalized.replace("â€“", "–").replace("â€”", "—")
    for broken, fixed in MOJIBAKE_MAP.items():
        normalized = normalized.replace(broken, fixed)
    normalized = REGISTERED_SUFFIX_MOJIBAKE_RE.sub(r"\g<word>®", normalized)
    for broken, fixed in MERGED_TOKEN_FIXES.items():
        normalized = normalized.replace(broken, fixed)
    normalized = normalized.replace("\u00ad", "")
    normalized = normalized.replace("\xa0", " ")
    if _should_apply_chess_normalization(normalized):
        normalized = normalized.replace("+/-", "\u00b1")
        normalized = normalized.replace("-/+", "\u2213")
        normalized = _normalize_chess_notation_text(normalized)
    normalized = EMAIL_RE.sub("", normalized)
    normalized = MEMBER_COPY_RE.sub("", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = INLINE_EVAL_RE.sub(r"\1 ", normalized)
    normalized = re.sub(r"(?<=[0-9])(?=[A-Z][a-z])", " ", normalized)
    normalized = re.sub(r"(?<=[0-9])(?=mate\b)", " ", normalized)
    normalized = re.sub(r"(?<=[!?])(?=[A-Z][a-z])", " ", normalized)
    normalized = re.sub(r"(?<=[✓†‡])(?=[A-Z][a-z])", " ", normalized)
    normalized = re.sub(r"(?<=\w)([✓†‡])", r" \1", normalized)
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    return normalized.strip()


def _sanitize_inline_html(fragment_html: str) -> str:
    normalized = fragment_html or ""
    if normalized:
        fragment = BeautifulSoup(f"<wrapper>{normalized}</wrapper>", "xml")
        wrapper = fragment.find("wrapper")
        if wrapper is not None:
            for text_node in list(wrapper.descendants):
                if isinstance(text_node, NavigableString):
                    repaired = _repair_text_node(str(text_node))
                    if repaired != str(text_node):
                        text_node.replace_with(repaired)
            normalized = "".join(str(child) for child in wrapper.contents)
    normalized = normalized.replace("Â®", "®").replace("Â©", "©").replace("Â·", "·")
    normalized = normalized.replace("â€™", "'").replace("â€ś", "\"").replace("â€ť", "\"")
    normalized = normalized.replace("â€“", "–").replace("â€”", "—")
    for broken, fixed in MOJIBAKE_MAP.items():
        normalized = normalized.replace(broken, fixed)
    normalized = REGISTERED_SUFFIX_MOJIBAKE_RE.sub(r"\g<word>®", normalized)
    for broken, fixed in MERGED_TOKEN_FIXES.items():
        normalized = normalized.replace(broken, fixed)
    normalized = normalized.replace("\u00ad", "")
    normalized = normalized.replace("&nbsp;", " ")
    if _should_apply_chess_normalization(normalized):
        normalized = normalized.replace("+/-", "\u00b1")
        normalized = normalized.replace("-/+", "\u2213")
        normalized = _normalize_chess_notation_text(normalized)
    normalized = UNSAFE_INLINE_TAG_RE.sub("&lt;", normalized)
    normalized = EMAIL_RE.sub("", normalized)
    normalized = MEMBER_COPY_RE.sub("", normalized)
    normalized = re.sub(r"[ \t\r\n]+", " ", normalized)
    normalized = INLINE_EVAL_RE.sub(r"\1 ", normalized)
    normalized = re.sub(r"(?<=[0-9])(?=[A-Z][a-z])", " ", normalized)
    normalized = re.sub(r"(?<=[0-9])(?=mate\b)", " ", normalized)
    normalized = re.sub(r"(?<=[!?])(?=[A-Z][a-z])", " ", normalized)
    normalized = re.sub(r"(?<=[✓†‡])(?=[A-Z][a-z])", " ", normalized)
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    return normalized.strip()


def _normalize_list_item_html(node: Tag | None) -> str:
    if node is None:
        return ""
    fragment_html = _inner_html(node)
    if not fragment_html:
        return ""
    fragment = BeautifulSoup(f"<wrapper>{fragment_html}</wrapper>", "xml")
    wrapper = fragment.find("wrapper")
    if wrapper is None:
        return _sanitize_inline_html(fragment_html)
    for text_node in list(wrapper.descendants):
        if isinstance(text_node, NavigableString):
            repaired = _repair_text_node(str(text_node))
            if repaired != str(text_node):
                text_node.replace_with(repaired)
    normalized = "".join(str(child) for child in wrapper.contents).strip()
    return _sanitize_inline_html(normalized or fragment_html)


def _inner_html(node: Tag | None) -> str:
    if node is None:
        return ""
    return "".join(str(child) for child in node.contents).strip()


def _class_list(node: Tag) -> list[str]:
    classes = node.get("class", [])
    if isinstance(classes, str):
        return classes.split()
    return list(classes)


def _normalized_classes(existing: list[str] | str | None, *, fallback: list[str]) -> list[str]:
    classes = []
    if isinstance(existing, str):
        classes.extend(existing.split())
    elif isinstance(existing, list):
        classes.extend(existing)
    classes.extend(fallback)
    seen = []
    for class_name in classes:
        if class_name and class_name not in seen:
            seen.append(class_name)
    return seen


def _append_class_name(existing: str, extra: str) -> str:
    class_names = [name for name in (existing or "").split() if name]
    if extra and extra not in class_names:
        class_names.append(extra)
    return " ".join(class_names)


def _slugify(text: str) -> str:
    asciiish = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[-\s]+", "-", asciiish).strip("-")
    return slug or "section"


def _collect_used_dom_ids(scope: Tag | BeautifulSoup, *, skip_node: Tag | None = None) -> set[str]:
    used_ids: set[str] = set()
    for node in scope.find_all(attrs={"id": True}):
        if skip_node is not None and node is skip_node:
            continue
        node_id = node.get("id", "")
        if node_id:
            used_ids.add(node_id)
    return used_ids


def _unique_dom_id(base: str, used_ids: set[str], *, fallback: str) -> str:
    seed = _slugify(base or fallback) or _slugify(fallback)
    candidate = seed
    suffix = 2
    while candidate in used_ids:
        candidate = f"{seed}-{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _dedupe_dom_ids(scope: Tag) -> bool:
    used_ids: set[str] = set()
    changed = False
    for node in scope.find_all(attrs={"id": True}):
        current_id = node.get("id", "")
        if not current_id:
            continue
        if current_id not in used_ids:
            used_ids.add(current_id)
            continue
        node["id"] = _unique_dom_id(current_id, used_ids, fallback="anchor")
        changed = True
    return changed


def _normalize_chapter_dom_ids(chapter_path: Path) -> None:
    xhtml = chapter_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(xhtml, "xml")
    body = soup.find("body")
    if body is None:
        return
    if not _dedupe_dom_ids(body):
        return
    chapter_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _collect_nav_entries_from_heading_candidates(
    candidates: list[dict[str, object]],
    *,
    file_name: str,
) -> list[dict[str, object]]:
    nav_entries: list[dict[str, object]] = []
    nav_targets_seen: set[str] = set()
    nav_text_keys_seen: set[str] = set()
    subsection_nav_count = 0
    chapter_primary_key = _training_book_key(str((candidates[0] if candidates else {}).get("text", "") or ""))
    front_matter_primary = _is_front_matter_heading_key(chapter_primary_key)

    for candidate in candidates:
        level = max(1, min(int(candidate.get("level") or 1), 3))
        text = _normalize_text(str(candidate.get("text", "") or ""))
        anchor_id = _normalize_text(str(candidate.get("id", "") or ""))
        if not text or not anchor_id:
            continue
        if not _should_include_in_toc(text, level):
            continue
        if front_matter_primary and level > 1:
            continue
        if level == 3 and subsection_nav_count >= MAX_SUBSECTION_NAV_PER_CHAPTER:
            continue

        href = f"{file_name}#{anchor_id}"
        text_key = _normalize_key(text)
        if href in nav_targets_seen:
            continue
        if level > 1 and text_key in nav_text_keys_seen:
            continue

        nav_entries.append(
            {
                "file_name": file_name,
                "id": anchor_id,
                "text": text,
                "level": level,
            }
        )
        nav_targets_seen.add(href)
        nav_text_keys_seen.add(text_key)
        if level == 3:
            subsection_nav_count += 1

    return nav_entries


def _rebuild_toc_entries_from_final_chapters(
    chapter_paths: list[Path],
    *,
    fallback_entries: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    rebuilt: list[dict[str, object]] = []
    for chapter_path in chapter_paths:
        if chapter_path.name == "cover.xhtml" or not chapter_path.exists():
            continue
        candidates = _collect_heading_candidates_from_path(chapter_path, include_pseudo=False)
        rebuilt.extend(
            _collect_nav_entries_from_heading_candidates(
                candidates,
                file_name=chapter_path.name,
            )
        )
    if rebuilt:
        return _dedupe_repeated_subsection_toc_labels(rebuilt)
    return list(fallback_entries or [])


def _dedupe_repeated_subsection_toc_labels(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen_text_keys: set[str] = set()
    for entry in entries:
        text = _normalize_text(str(entry.get("text", "") or ""))
        text_key = _normalize_key(text)
        level = int(entry.get("level") or 1)
        if level > 1 and text_key and text_key in seen_text_keys:
            continue
        if text_key:
            seen_text_keys.add(text_key)
        deduped.append(entry)
    return deduped


def _normalize_key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _canonical_heading_text(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    normalized = re.sub(
        r"(?i)^(?:chapter|part|appendix|section|rozdział|sekcja)\s+\d+[A-Za-z\-]*\s*(?:[:\-–—]\s*)?",
        "",
        normalized,
    )
    normalized = re.sub(r"^\d+(?:\.\d+)*\s*(?:[:\-–—]\s*)?", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" -:;,.")
    return normalized.lower()


def _looks_technical_title(value: str, *, reference_stem: str = "") -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return True
    lowered = normalized.lower()
    if PLACEHOLDER_TITLE_RE.fullmatch(normalized):
        return True
    if _looks_like_synthetic_section_label(normalized):
        return True
    if any(marker in lowered for marker in TECHNICAL_TITLE_MARKERS):
        return True
    if reference_stem and lowered == _normalize_text(reference_stem).lower():
        return True
    return False


def _training_book_key(text: str) -> str:
    return _canonical_heading_text(text)


def _title_fragments_match(left: str, right: str) -> bool:
    left_key = _canonical_heading_text(left)
    right_key = _canonical_heading_text(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    short = left_key if len(left_key) <= len(right_key) else right_key
    long = right_key if len(left_key) <= len(right_key) else left_key
    return len(short) >= 4 and short in long


def _problem_file_from_href(href: str) -> str:
    if not href or ".xhtml" not in href:
        return ""
    return href.split("#", 1)[0]


def _infer_neighbor_problem_file(blocks: list[dict], index: int) -> str:
    for direction in (-1, 1):
        cursor = index + direction
        while 0 <= cursor < len(blocks):
            candidate = blocks[cursor]
            if candidate["type"] == "solution-heading":
                problem_file = _problem_file_from_href(candidate.get("target", ""))
                if problem_file:
                    return problem_file
            cursor += direction
    return ""


def _inject_caption_into_figure_html(figure_html: str, *, caption_html: str) -> str:
    fragment = BeautifulSoup(figure_html, "xml")
    figure = fragment.find("figure")
    if figure is None or figure.find("figcaption") is not None:
        return figure_html
    figcaption = fragment.new_tag("figcaption")
    figcaption["class"] = "figure-caption"
    caption_fragment = BeautifulSoup(f"<wrapper>{caption_html}</wrapper>", "xml")
    wrapper = caption_fragment.find("wrapper")
    if wrapper is not None:
        for child in list(wrapper.contents):
            figcaption.append(child)
    figure.insert(0, figcaption)
    return str(figure)


def _dedupe_problem_refs(problem_refs: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for ref in problem_refs:
        key = (ref["exercise_num"], ref["solution_href"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def _first_nonempty(values, *, default: str) -> str:
    for value in values:
        if value:
            return value
    return default


def _resolve_document_title(*, chapter_path: Path, nav_entries: list[dict], logical_blocks: list[dict], chapter_title: str) -> str:
    nav_title = _first_nonempty(
        (entry["text"] for entry in nav_entries if entry["file_name"] == chapter_path.name),
        default="",
    )
    if nav_title:
        return nav_title
    normalized_chapter_title = _normalize_text(chapter_title)
    if normalized_chapter_title and not re.fullmatch(r"(?i)(chapter|section|rozdział|sekcja|artykuł)[_ -]?\d+", normalized_chapter_title):
        return normalized_chapter_title

    for block in logical_blocks:
        if block["type"] == "solution-heading":
            return block["text"]
        if block["type"] == "heading":
            return block["text"]
        if block["type"] == "figure":
            figure_soup = BeautifulSoup(block["html"], "xml")
            caption = figure_soup.find("figcaption")
            if caption:
                return _normalize_text(caption.get_text(" ", strip=True))
    return "Sekcja"
