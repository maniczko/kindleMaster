from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
import difflib
import json
import re
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

from ebooklib import ITEM_DOCUMENT, ITEM_NAVIGATION, epub
from lxml import etree, html as lxml_html

from premium_tools import run_epubcheck
from text_normalization import (
    EPUB_TEXT_MEMBER_RE,
    RAW_LINK_TEXT_RE,
    URL_CONTINUATION_PREFIX_RE,
    URL_HREF_RE,
    _canonicalize_raw_link,
    _compact_external_references,
    _is_pre_paginated_epub,
    _normalize_href,
)

try:
    from wordfreq import zipf_frequency as _zipf_frequency
except Exception:  # pragma: no cover - optional dependency
    _zipf_frequency = None

try:
    import pyphen
except Exception:  # pragma: no cover - dependency may be missing before install
    pyphen = None


XML_NS = "http://www.w3.org/1999/xhtml"
PROTECTED_TAGS = {"code", "pre", "kbd", "samp", "style", "script", "math"}
TABLE_TAGS = {"table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption"}
BLOCK_CONTAINER_TAGS = {
    "p",
    "li",
    "dd",
    "dt",
    "blockquote",
    "div",
    "section",
    "article",
    "aside",
    "figcaption",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "td",
    "th",
}
URL_RE = re.compile(r"(?i)\b(?:https?://|www\.|doi:)\S+")
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
VERSION_RE = re.compile(r"(?i)\bv?\d+(?:\.\d+){1,4}\b")
SYSTEM_ID_RE = re.compile(r"\b[A-Z]{2,12}[-_][A-Z0-9]{2,}\b")
INVOICE_RE = re.compile(r"(?i)\b(?:FV|INV|PO|REF|ID)[-/:]?[A-Z0-9][A-Z0-9/_-]{3,}\b")
WORDISH_RE = re.compile(r"\b[^\W\d_]{1,32}\b", re.UNICODE)
PAIR_WORD_RE = re.compile(r"(?P<left>\b[^\W\d_]{1,20}\b)(?P<gap>\s+)(?P<right>\b[^\W\d_]{1,20}\b)", re.UNICODE)
HYPHEN_BREAK_RE = re.compile(r"(?P<left>\b[^\W\d_]{2,24})[-\u00ad]\s+(?P<right>[^\W\d_]{2,24}\b)", re.UNICODE)
SPACE_BEFORE_PUNCT_RE = re.compile(r"(?P<space>\s+)(?P<punct>[,.;:!?])")
MISSING_SENTENCE_SPACE_RE = re.compile(r"(?P<lead>[.!?;:])(?P<next>[A-ZĄĆĘŁŃÓŚŹŻ])")
NUMBER_PERCENT_RE = re.compile(r"(?P<number>\d)\s+(?P<unit>%)")
INLINE_DECIMAL_PERCENT_RE = re.compile(r"(?P<whole>\d+)(?P<separator>[.,])\s+(?P<fraction>\d+)(?P<unit>%)")
NUMBER_UNIT_RE = re.compile(
    r"(?P<number>\d)\s+(?P<unit>zł|PLN|USD|EUR|GBP|kg|g|mg|km|m|cm|mm|MB|GB|TB|ms|s|h|pkt)\b",
    re.IGNORECASE,
)
URLISH_COMPACTION_RE = re.compile(r"(?i)(?:https\s*:|http\s*:|www\s*\.|doi\s*:)")
POLISH_SINGLE_STOPWORDS = {"a", "i", "o", "u", "w", "z"}
PL_STOPWORDS = {
    "a",
    "ale",
    "by",
    "być",
    "co",
    "czy",
    "dla",
    "do",
    "i",
    "ich",
    "jak",
    "jest",
    "na",
    "nie",
    "oraz",
    "po",
    "przez",
    "się",
    "to",
    "w",
    "z",
    "za",
}
EN_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
POLISH_SUFFIX_FRAGMENTS = {
    "a",
    "ach",
    "ami",
    "owa",
    "ową",
    "owe",
    "owi",
    "owo",
    "owy",
    "owych",
    "owego",
    "owie",
    "ów",
    "ego",
    "ie",
    "iu",
    "ka",
    "ki",
    "ką",
    "cie",
    "cja",
    "cje",
    "cji",
    "ra",
}
PLACEHOLDER_RE = re.compile(r"__KM_PROTECTED_\d+__")
TECHNICAL_CLASS_MARKERS = {"footnote", "endnote", "reference-note", "annotation", "toc-entry"}
DEFAULT_DOMAIN_TERMS = (
    {"canonical": "issuera", "variants": ["issuera"], "protected": True},
    {"canonical": "issuerowi", "variants": ["issuerowi"], "protected": True},
    {"canonical": "issuerem", "variants": ["issuerem"], "protected": True},
    {"canonical": "acquiringowy", "variants": ["acquiringowy"], "protected": True},
    {"canonical": "acquiringowego", "variants": ["acquiringowego"], "protected": True},
    {"canonical": "acquiringowym", "variants": ["acquiringowym"], "protected": True},
    {"canonical": "ownerem", "variants": ["ownerem"], "protected": True},
    {"canonical": "ownera", "variants": ["ownera"], "protected": True},
    {"canonical": "walidowac", "variants": ["walidowac"], "protected": True},
    {"canonical": "walidacja", "variants": ["walidacja"], "protected": True},
    {"canonical": "general ledger", "variants": ["generalledger"], "protected": True},
    {"canonical": "counterplay", "variants": ["counterplay"], "protected": True},
    {"canonical": "kingside", "variants": ["kingside"], "protected": True},
    {"canonical": "queenside", "variants": ["queenside"], "protected": True},
    {"canonical": "simul", "variants": ["simul"], "protected": True},
)
POLISH_MIXED_SUFFIX_FRAGMENTS = POLISH_SUFFIX_FRAGMENTS | {"em", "om", "ie", "ego", "owej", "owym", "owych"}


@dataclass
class TextCleanupConfig:
    language_hint: str | None = None
    domain_dictionary_path: str | None = None
    safe_threshold: float = 0.85
    review_threshold: float = 0.65
    enable_pyphen: bool = True
    emit_markdown_report: bool = True
    emit_text_diff: bool = False
    release_gate: str = "soft"
    long_document_mode: bool = False


@dataclass
class CleanupDecision:
    document_path: str
    node_xpath: str
    before: str
    after: str
    error_class: str
    score: float
    status: str
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_path": self.document_path,
            "node_xpath": self.node_xpath,
            "before": self.before,
            "after": self.after,
            "error_class": self.error_class,
            "score": round(self.score, 3),
            "status": self.status,
            "reason_codes": self.reason_codes,
        }


@dataclass
class TextCleanupResult:
    epub_bytes: bytes
    summary: dict[str, Any]
    decisions: list[CleanupDecision] = field(default_factory=list)
    unknown_terms: list[dict[str, Any]] = field(default_factory=list)
    epubcheck: dict[str, Any] = field(default_factory=dict)
    markdown_report: str = ""
    chapter_diffs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "unknown_terms": self.unknown_terms,
            "epubcheck": self.epubcheck,
            "markdown_report": self.markdown_report,
            "chapter_diffs": self.chapter_diffs,
        }


@dataclass
class _TextNodeContext:
    document_path: str
    node_xpath: str
    paragraph_text: str
    paragraph_language: str
    dom_tag: str
    is_anchor_text: bool
    is_table_like: bool
    is_technical_note: bool
    is_heading_like: bool
    is_caption_like: bool


@dataclass
class _Proposal:
    before: str
    after: str
    error_class: str
    lexical_score: float
    context_score: float
    language_score: float
    dom_score: float
    bonus_score: float
    reason_codes: list[str]
    hard_block_reasons: list[str] = field(default_factory=list)


@dataclass
class _DomainLexicon:
    protected_terms: set[str] = field(default_factory=set)
    variant_to_canonical: dict[str, str] = field(default_factory=dict)
    forced_splits: dict[str, str] = field(default_factory=dict)
    forced_merges: dict[str, str] = field(default_factory=dict)

    def lookup_variant(self, value: str) -> str:
        return self.variant_to_canonical.get(_normalize_lookup_key(value), "")

    def is_protected(self, value: str) -> bool:
        return _normalize_lookup_key(value) in self.protected_terms


@dataclass
class _ParsedDocument:
    tree: etree._ElementTree
    mode: str
    xml_declaration: str
    doctype: str


def clean_epub_text_package(
    epub_bytes: bytes,
    *,
    config: TextCleanupConfig | None = None,
    publication_profile: str | None = None,
) -> TextCleanupResult:
    config = config or TextCleanupConfig()
    lexicon = _load_domain_dictionary(config.domain_dictionary_path)
    if not epub_bytes:
        return TextCleanupResult(
            epub_bytes=epub_bytes,
            summary=_build_summary([], Counter(), {}, config, {"status": "unavailable", "tool": "epubcheck", "messages": []}),
            epubcheck={"status": "unavailable", "tool": "epubcheck", "messages": []},
        )

    book_info = _inspect_epub_package(epub_bytes, config)
    config.long_document_mode = config.long_document_mode or _should_enable_long_document_mode(book_info)
    _augment_runtime_domain_terms(epub_bytes, book_info=book_info, lexicon=lexicon)
    package_blocked = False
    decisions: list[CleanupDecision] = []
    unknown_counter: Counter[str] = Counter()
    chapter_diffs: dict[str, str] = {}

    if _is_pre_paginated_epub(epub_bytes) or publication_profile == "preserve-layout":
        package_blocked = True
        decisions.append(
            CleanupDecision(
                document_path="package",
                node_xpath="/package",
                before="pre-paginated",
                after="pre-paginated",
                error_class="package-skip",
                score=0.0,
                status="blocked",
                reason_codes=["pre-paginated-epub"],
            )
        )
        epubcheck = run_epubcheck(epub_bytes)
        summary = _build_summary(decisions, unknown_counter, chapter_diffs, config, epubcheck, package_blocked=True, book_info=book_info)
        markdown_report = _build_markdown_report(summary, decisions, [], epubcheck) if config.emit_markdown_report else ""
        return TextCleanupResult(
            epub_bytes=epub_bytes,
            summary=summary,
            decisions=decisions,
            unknown_terms=[],
            epubcheck=epubcheck,
            markdown_report=markdown_report,
            chapter_diffs={},
        )

    text_paths = set(book_info["text_paths"])
    if not text_paths:
        text_paths = set()

    source_buffer = BytesIO(epub_bytes)
    output_buffer = BytesIO()
    with zipfile.ZipFile(source_buffer, "r") as source_zip:
        with zipfile.ZipFile(output_buffer, "w") as target_zip:
            for info in source_zip.infolist():
                data = source_zip.read(info.filename)
                if info.filename in text_paths or EPUB_TEXT_MEMBER_RE.search(info.filename):
                    decoded = data.decode("utf-8", errors="ignore")
                    cleaned_document, doc_decisions, doc_unknowns, doc_diff = _clean_document(
                        decoded,
                        document_path=info.filename,
                        config=config,
                        lexicon=lexicon,
                    )
                    data = cleaned_document.encode("utf-8")
                    decisions.extend(doc_decisions)
                    unknown_counter.update(doc_unknowns)
                    if config.emit_text_diff and doc_diff:
                        chapter_diffs[info.filename] = doc_diff

                clone = zipfile.ZipInfo(info.filename)
                clone.date_time = info.date_time
                clone.compress_type = info.compress_type
                clone.comment = info.comment
                clone.extra = info.extra
                clone.create_system = info.create_system
                clone.create_version = info.create_version
                clone.extract_version = info.extract_version
                clone.flag_bits = info.flag_bits
                clone.volume = info.volume
                clone.internal_attr = info.internal_attr
                clone.external_attr = info.external_attr
                target_zip.writestr(clone, data)

    cleaned_epub = output_buffer.getvalue()
    epubcheck = run_epubcheck(cleaned_epub)
    unknown_terms = _top_unknown_terms(unknown_counter)
    summary = _build_summary(decisions, unknown_counter, chapter_diffs, config, epubcheck, book_info=book_info)
    markdown_report = _build_markdown_report(summary, decisions, unknown_terms, epubcheck) if config.emit_markdown_report else ""
    return TextCleanupResult(
        epub_bytes=cleaned_epub,
        summary=summary,
        decisions=decisions,
        unknown_terms=unknown_terms,
        epubcheck=epubcheck,
        markdown_report=markdown_report,
        chapter_diffs=chapter_diffs,
    )


def _inspect_epub_package(epub_bytes: bytes, config: TextCleanupConfig) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        epub_path = Path(temp_dir) / "input.epub"
        epub_path.write_bytes(epub_bytes)
        try:
            book = epub.read_epub(str(epub_path))
        except Exception:
            return {
                "text_paths": [],
                "spine_paths": [],
                "toc_count": 0,
                "language": config.language_hint or "mixed",
            }

    text_paths: list[str] = []
    manifest_by_id: dict[str, Any] = {}
    for item in book.get_items():
        manifest_by_id[getattr(item, "id", "")] = item
        media_type = getattr(item, "media_type", "") or ""
        if item.get_type() in {ITEM_DOCUMENT, ITEM_NAVIGATION} or media_type in {"application/xhtml+xml", "text/html"}:
            text_paths.append(getattr(item, "file_name", ""))

    spine_paths: list[str] = []
    for entry in book.spine:
        item_id = entry[0] if isinstance(entry, (tuple, list)) and entry else str(entry)
        item = manifest_by_id.get(item_id)
        if item is not None:
            spine_paths.append(getattr(item, "file_name", ""))

    languages = [value for value, _ in book.get_metadata("DC", "language")]
    return {
        "text_paths": [path for path in text_paths if path],
        "spine_paths": [path for path in spine_paths if path],
        "toc_count": len(list(book.toc or [])),
        "language": languages[0] if languages else (config.language_hint or "mixed"),
    }


def _should_enable_long_document_mode(book_info: dict[str, Any]) -> bool:
    spine_count = len((book_info or {}).get("spine_paths", []) or [])
    toc_count = int((book_info or {}).get("toc_count", 0) or 0)
    return spine_count >= 80 or toc_count >= 60


def _augment_runtime_domain_terms(
    epub_bytes: bytes,
    *,
    book_info: dict[str, Any],
    lexicon: _DomainLexicon,
) -> None:
    text_paths = set((book_info or {}).get("text_paths", []) or [])
    if not text_paths:
        return

    evidence: dict[str, dict[str, int]] = {}
    with zipfile.ZipFile(BytesIO(epub_bytes), "r") as source_zip:
        for info in source_zip.infolist():
            if info.filename not in text_paths and not EPUB_TEXT_MEMBER_RE.search(info.filename):
                continue
            try:
                decoded = source_zip.read(info.filename).decode("utf-8", errors="ignore")
            except Exception:
                continue

            plain_text = re.sub(r"<[^>]+>", " ", decoded)
            for token in WORDISH_RE.findall(plain_text):
                normalized = _normalize_lookup_key(token)
                if len(normalized) < 4 or lexicon.is_protected(normalized) or lexicon.lookup_variant(normalized):
                    continue
                row = evidence.setdefault(
                    normalized,
                    {"count": 0, "heading_hits": 0, "acronym_hits": 0, "mixed_hits": 0, "lexical_hits": 0},
                )
                row["count"] += 1
                if token.isupper() and 2 <= len(token) <= 8:
                    row["acronym_hits"] += 1
                if _looks_like_mixed_domain_term(token):
                    row["mixed_hits"] += 1
                if _token_score(token, "en") >= 0.22 or _token_score(token, "mixed") >= 0.24:
                    row["lexical_hits"] += 1

            parsed = _parse_document(decoded)
            if parsed is None:
                continue
            for element in parsed.tree.getroot().iter():
                if not isinstance(element.tag, str) or _local_name(element) not in {"h1", "h2", "h3", "title"}:
                    continue
                heading_text = " ".join(part.strip() for part in element.itertext() if part and part.strip())
                for token in WORDISH_RE.findall(heading_text):
                    normalized = _normalize_lookup_key(token)
                    if len(normalized) < 4:
                        continue
                    row = evidence.setdefault(
                        normalized,
                        {"count": 0, "heading_hits": 0, "acronym_hits": 0, "mixed_hits": 0, "lexical_hits": 0},
                    )
                    row["count"] += 1
                    row["heading_hits"] += 1

    for term, row in evidence.items():
        if (
            (row["heading_hits"] >= 2 and row["count"] >= 3)
            or (row["acronym_hits"] >= 2 and row["count"] >= 4)
            or (row["mixed_hits"] >= 1 and row["count"] >= 3)
            or (row["lexical_hits"] >= 2 and row["count"] >= 2 and len(term) >= 7)
        ):
            lexicon.protected_terms.add(term)


def _load_domain_dictionary(path: str | None) -> _DomainLexicon:
    lexicon = _DomainLexicon()
    for term in DEFAULT_DOMAIN_TERMS:
        canonical = str(term.get("canonical") or "").strip()
        if not canonical:
            continue
        normalized_canonical = _normalize_lookup_key(canonical)
        lexicon.variant_to_canonical[normalized_canonical] = canonical
        for variant in term.get("variants", []) or []:
            normalized_variant = _normalize_lookup_key(str(variant or ""))
            if normalized_variant:
                lexicon.variant_to_canonical[normalized_variant] = canonical
        if term.get("protected"):
            lexicon.protected_terms.add(normalized_canonical)

    if not path:
        return lexicon

    dictionary_path = Path(path)
    if not dictionary_path.exists():
        return lexicon

    try:
        payload = json.loads(dictionary_path.read_text(encoding="utf-8"))
    except Exception:
        return lexicon

    for term in payload.get("terms", []) or []:
        canonical = str(term.get("canonical") or "").strip()
        if not canonical:
            continue
        normalized_canonical = _normalize_lookup_key(canonical)
        lexicon.variant_to_canonical[normalized_canonical] = canonical
        for variant in term.get("variants", []) or []:
            normalized_variant = _normalize_lookup_key(str(variant or ""))
            if normalized_variant:
                lexicon.variant_to_canonical[normalized_variant] = canonical
        if term.get("protected"):
            lexicon.protected_terms.add(normalized_canonical)

    forced_splits = payload.get("forced_splits", {}) or {}
    forced_merges = payload.get("forced_merges", {}) or {}
    lexicon.forced_splits = {
        _normalize_lookup_key(key): str(value).strip()
        for key, value in forced_splits.items()
        if str(value).strip()
    }
    lexicon.forced_merges = {
        _normalize_lookup_key(key): str(value).strip()
        for key, value in forced_merges.items()
        if str(value).strip()
    }
    for replacement in lexicon.forced_merges.values():
        lexicon.protected_terms.add(_normalize_lookup_key(replacement))
    return lexicon


def _clean_document(
    document: str,
    *,
    document_path: str,
    config: TextCleanupConfig,
    lexicon: _DomainLexicon,
) -> tuple[str, list[CleanupDecision], Counter[str], str]:
    parsed = _parse_document(document)
    if parsed is None:
        return (
            document,
            [
                CleanupDecision(
                    document_path=document_path,
                    node_xpath="/html",
                    before="parse-error",
                    after="parse-error",
                    error_class="document-parse",
                    score=0.0,
                    status="blocked",
                    reason_codes=["xml-parse-failed", "html-fallback-failed"],
                )
            ],
            Counter(),
            "",
        )

    tree = parsed.tree
    root = tree.getroot()
    decisions: list[CleanupDecision] = []
    unknown_terms: Counter[str] = Counter()

    for element, slot_name, slot_value in _iter_visible_text_slots(root):
        context = _build_text_context(tree, element, slot_name, document_path)
        if not slot_value:
            continue
        cleaned_value, slot_decisions, slot_unknowns = _clean_text_slot(
            slot_value,
            context=context,
            config=config,
            lexicon=lexicon,
        )
        decisions.extend(slot_decisions)
        unknown_terms.update(slot_unknowns)
        if cleaned_value != slot_value:
            if slot_name == "text":
                element.text = cleaned_value
            else:
                element.tail = cleaned_value

    _repair_split_external_anchors(root)
    _linkify_raw_urls(root)
    _normalize_anchor_hrefs(root)
    _dedupe_document_ids(root)

    serialized = _serialize_document(parsed)
    diff = ""
    if config.emit_text_diff and serialized != document:
        diff = "\n".join(
            difflib.unified_diff(
                document.splitlines(),
                serialized.splitlines(),
                fromfile=f"{document_path}:before",
                tofile=f"{document_path}:after",
                lineterm="",
            )
        )
    return serialized, decisions, unknown_terms, diff


def _parse_document(document: str) -> _ParsedDocument | None:
    raw_bytes = document.encode("utf-8", errors="ignore")
    xml_parser = etree.XMLParser(
        recover=False,
        resolve_entities=False,
        remove_blank_text=False,
        strip_cdata=False,
        huge_tree=True,
    )
    try:
        tree = etree.parse(BytesIO(raw_bytes), parser=xml_parser)
        docinfo = tree.docinfo
        xml_declaration = ""
        if docinfo.xml_version:
            encoding = docinfo.encoding or "utf-8"
            xml_declaration = f'<?xml version="{docinfo.xml_version}" encoding="{encoding}"?>'
        return _ParsedDocument(
            tree=tree,
            mode="xml",
            xml_declaration=xml_declaration,
            doctype=docinfo.doctype or "",
        )
    except etree.XMLSyntaxError:
        pass

    try:
        html_parser = lxml_html.HTMLParser(encoding="utf-8")
        root = lxml_html.fromstring(document, parser=html_parser)
        return _ParsedDocument(
            tree=etree.ElementTree(root),
            mode="html",
            xml_declaration="",
            doctype="",
        )
    except Exception:
        return None


def _serialize_document(parsed: _ParsedDocument) -> str:
    root = parsed.tree.getroot()
    if parsed.mode == "xml":
        body = etree.tostring(root, encoding="unicode", method="xml")
        prefix = [part for part in [parsed.xml_declaration, parsed.doctype] if part]
        return ("\n".join(prefix) + ("\n" if prefix else "") + body).strip()
    return etree.tostring(root, encoding="unicode", method="html")


def _iter_visible_text_slots(root: etree._Element):
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if _has_protected_ancestor(element):
            continue
        if element.text:
            yield element, "text", element.text
        if element.tail and element.getparent() is not None and not _has_protected_ancestor(element.getparent()):
            yield element, "tail", element.tail


def _has_protected_ancestor(element: etree._Element | None) -> bool:
    current = element
    while current is not None:
        if _local_name(current) in PROTECTED_TAGS:
            return True
        current = current.getparent()
    return False


def _build_text_context(
    tree: etree._ElementTree,
    element: etree._Element,
    slot_name: str,
    document_path: str,
) -> _TextNodeContext:
    parent = element.getparent()
    base_element = element if slot_name == "text" or parent is None else parent
    paragraph_owner = _nearest_block_container(base_element)
    paragraph_text = " ".join(part.strip() for part in paragraph_owner.itertext() if part and part.strip())
    paragraph_language = _infer_language(paragraph_text)
    node_xpath = tree.getpath(element)
    if slot_name == "tail":
        node_xpath = f"{node_xpath}/tail()"
    else:
        node_xpath = f"{node_xpath}/text()"
    dom_tag = _local_name(base_element)
    class_markers = " ".join((base_element.get("class") or "").split()).lower()
    id_marker = (base_element.get("id") or "").lower()
    marker_blob = f"{class_markers} {id_marker}".strip()
    container_tag = _local_name(paragraph_owner)
    paragraph_text_normalized = " ".join(paragraph_text.split())
    is_heading_like = container_tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or dom_tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
    is_caption_like = container_tag == "figcaption" or "caption" in marker_blob or _looks_like_captionish_text(paragraph_text_normalized)
    return _TextNodeContext(
        document_path=document_path,
        node_xpath=node_xpath,
        paragraph_text=paragraph_text,
        paragraph_language=paragraph_language,
        dom_tag=dom_tag,
        is_anchor_text=dom_tag == "a" or _local_name(base_element.getparent()) == "a",
        is_table_like=any(_local_name(node) in TABLE_TAGS for node in _iter_ancestors(base_element)),
        is_technical_note=any(marker in marker_blob for marker in TECHNICAL_CLASS_MARKERS),
        is_heading_like=is_heading_like,
        is_caption_like=is_caption_like,
    )


def _nearest_block_container(element: etree._Element) -> etree._Element:
    current = element
    while current is not None:
        if _local_name(current) in BLOCK_CONTAINER_TAGS:
            return current
        current = current.getparent()
    return element


def _iter_ancestors(element: etree._Element | None):
    current = element
    while current is not None:
        yield current
        current = current.getparent()


def _looks_like_captionish_text(text: str) -> bool:
    normalized = " ".join((text or "").split())
    if not normalized or len(normalized) > 160:
        return False
    return bool(
        re.match(r"(?i)^(?:figure|table|diagram|chart|exhibit|photo)\s+[A-Za-z0-9.\-: ]{1,120}$", normalized)
        or re.match(r"(?i)^(?:rys(?:unek|\.)|tabela|diagram|wykres)\s+[A-Za-z0-9.\-: ]{1,120}$", normalized)
    )


def _clean_text_slot(
    text: str,
    *,
    context: _TextNodeContext,
    config: TextCleanupConfig,
    lexicon: _DomainLexicon,
) -> tuple[str, list[CleanupDecision], Counter[str]]:
    working, placeholders = _protect_segments(text)
    decisions: list[CleanupDecision] = []

    appliers = (
        _apply_forced_split_pass,
        _apply_forced_merge_pass,
        _apply_hyphen_break_pass,
        _apply_split_word_pass,
        _apply_glued_word_pass,
        _apply_domain_variant_pass,
        _apply_numeric_fragment_pass,
        _apply_spacing_pass,
    )
    if config.long_document_mode and (context.is_heading_like or context.is_caption_like):
        appliers = (
            _apply_domain_variant_pass,
            _apply_numeric_fragment_pass,
            _apply_spacing_pass,
        )

    for applier in appliers:
        working, pass_decisions = applier(working, context=context, config=config, lexicon=lexicon)
        decisions.extend(pass_decisions)

    restored = _restore_placeholders(working, placeholders)
    compacted = _compact_external_references(restored)
    if compacted != restored:
        proposal = _Proposal(
            before=restored,
            after=compacted,
            error_class="external-reference-spacing",
            lexical_score=1.0,
            context_score=0.95,
            language_score=0.9,
            dom_score=1.0,
            bonus_score=0.4,
            reason_codes=["urlish-spacing-pattern"],
        )
        decision = _make_decision(proposal, context=context, config=config)
        decisions.append(decision)
        if decision.status == "safe_auto_fix":
            restored = compacted

    unknown_terms = Counter()
    if not context.is_heading_like and not context.is_caption_like:
        unknown_terms = _collect_unknown_terms(restored, context=context, lexicon=lexicon)
    return restored, decisions, unknown_terms


def _apply_forced_split_pass(text: str, *, context: _TextNodeContext, config: TextCleanupConfig, lexicon: _DomainLexicon):
    if not lexicon.forced_splits:
        return text, []
    return _rewrite_wordish_tokens(
        text,
        context=context,
        config=config,
        builder=lambda token: _forced_split_proposal(token, context=context, lexicon=lexicon),
    )


def _apply_forced_merge_pass(text: str, *, context: _TextNodeContext, config: TextCleanupConfig, lexicon: _DomainLexicon):
    if not lexicon.forced_merges:
        return text, []
    return _rewrite_pair_tokens(
        text,
        context=context,
        config=config,
        builder=lambda left, gap, right: _forced_merge_proposal(left, gap, right, context=context, lexicon=lexicon),
    )


def _apply_hyphen_break_pass(text: str, *, context: _TextNodeContext, config: TextCleanupConfig, lexicon: _DomainLexicon):
    decisions: list[CleanupDecision] = []
    parts: list[str] = []
    last_index = 0
    for match in HYPHEN_BREAK_RE.finditer(text):
        proposal = _hyphen_break_proposal(match.group("left"), match.group("right"), context=context, config=config)
        decision = _make_decision(proposal, context=context, config=config)
        decisions.append(decision)
        parts.append(text[last_index:match.start()])
        parts.append(proposal.after if decision.status == "safe_auto_fix" else match.group(0))
        last_index = match.end()
    if not decisions:
        return text, []
    parts.append(text[last_index:])
    return "".join(parts), decisions


def _apply_glued_word_pass(text: str, *, context: _TextNodeContext, config: TextCleanupConfig, lexicon: _DomainLexicon):
    return _rewrite_wordish_tokens(
        text,
        context=context,
        config=config,
        builder=lambda token: _glued_word_proposal(token, context=context, config=config, lexicon=lexicon),
    )


def _apply_split_word_pass(text: str, *, context: _TextNodeContext, config: TextCleanupConfig, lexicon: _DomainLexicon):
    return _rewrite_pair_tokens(
        text,
        context=context,
        config=config,
        builder=lambda left, gap, right: _split_word_proposal(left, gap, right, context=context, config=config, lexicon=lexicon),
    )


def _apply_domain_variant_pass(text: str, *, context: _TextNodeContext, config: TextCleanupConfig, lexicon: _DomainLexicon):
    return _rewrite_wordish_tokens(
        text,
        context=context,
        config=config,
        builder=lambda token: _domain_variant_proposal(token, context=context, lexicon=lexicon),
    )


def _apply_numeric_fragment_pass(text: str, *, context: _TextNodeContext, config: TextCleanupConfig, lexicon: _DomainLexicon):
    del lexicon
    decisions: list[CleanupDecision] = []
    rebuilt: list[str] = []
    last_index = 0
    for match in INLINE_DECIMAL_PERCENT_RE.finditer(text):
        proposal = _inline_decimal_percent_proposal(match)
        decision = _make_decision(proposal, context=context, config=config)
        decisions.append(decision)
        rebuilt.append(text[last_index:match.start()])
        rebuilt.append(proposal.after if decision.status == "safe_auto_fix" else match.group(0))
        last_index = match.end()
    if not decisions:
        return text, []
    rebuilt.append(text[last_index:])
    return "".join(rebuilt), decisions


def _apply_spacing_pass(text: str, *, context: _TextNodeContext, config: TextCleanupConfig, lexicon: _DomainLexicon):
    del lexicon
    decisions: list[CleanupDecision] = []
    working = text
    for pattern, builder in (
        (SPACE_BEFORE_PUNCT_RE, _space_before_punct_proposal),
        (MISSING_SENTENCE_SPACE_RE, _missing_sentence_space_proposal),
        (NUMBER_PERCENT_RE, _number_unit_spacing_proposal),
        (NUMBER_UNIT_RE, _number_unit_spacing_proposal),
    ):
        rebuilt: list[str] = []
        last_index = 0
        local_decisions: list[CleanupDecision] = []
        for match in pattern.finditer(working):
            proposal = builder(match)
            decision = _make_decision(proposal, context=context, config=config)
            local_decisions.append(decision)
            rebuilt.append(working[last_index:match.start()])
            rebuilt.append(proposal.after if decision.status == "safe_auto_fix" else match.group(0))
            last_index = match.end()
        if local_decisions:
            rebuilt.append(working[last_index:])
            working = "".join(rebuilt)
            decisions.extend(local_decisions)
    return working, decisions


def _rewrite_wordish_tokens(text: str, *, context: _TextNodeContext, config: TextCleanupConfig, builder):
    decisions: list[CleanupDecision] = []
    rebuilt: list[str] = []
    last_index = 0
    for match in WORDISH_RE.finditer(text):
        proposal = builder(match.group(0))
        if proposal is None:
            continue
        decision = _make_decision(proposal, context=context, config=config)
        decisions.append(decision)
        rebuilt.append(text[last_index:match.start()])
        rebuilt.append(proposal.after if decision.status == "safe_auto_fix" else match.group(0))
        last_index = match.end()
    if not decisions:
        return text, []
    rebuilt.append(text[last_index:])
    return "".join(rebuilt), decisions


def _rewrite_pair_tokens(text: str, *, context: _TextNodeContext, config: TextCleanupConfig, builder):
    token_matches = list(WORDISH_RE.finditer(text))
    if len(token_matches) < 2:
        return text, []

    decisions: list[CleanupDecision] = []
    rebuilt: list[str] = []
    cursor = 0
    index = 0
    while index < len(token_matches) - 1:
        left_match = token_matches[index]
        right_match = token_matches[index + 1]
        gap = text[left_match.end() : right_match.start()]
        if not gap.isspace():
            index += 1
            continue
        if "\n" in gap and (left_match.group(0)[:1].isupper() or right_match.group(0)[:1].isupper()):
            index += 1
            continue

        proposal = builder(left_match.group(0), gap, right_match.group(0))
        if proposal is None:
            index += 1
            continue

        decision = _make_decision(proposal, context=context, config=config)
        decisions.append(decision)
        if decision.status == "safe_auto_fix":
            rebuilt.append(text[cursor:left_match.start()])
            rebuilt.append(proposal.after)
            cursor = right_match.end()
            index += 2
            continue
        index += 1

    if not decisions:
        return text, []
    rebuilt.append(text[cursor:])
    return "".join(rebuilt), decisions


def _forced_split_proposal(token: str, *, context: _TextNodeContext, lexicon: _DomainLexicon) -> _Proposal | None:
    del context
    replacement = lexicon.forced_splits.get(_normalize_lookup_key(token))
    if not replacement or replacement == token:
        return None
    return _Proposal(
        before=token,
        after=replacement,
        error_class="forced-split",
        lexical_score=1.0,
        context_score=1.0,
        language_score=0.95,
        dom_score=1.0,
        bonus_score=1.0,
        reason_codes=["domain-dictionary", "forced-split"],
    )


def _forced_merge_proposal(left: str, gap: str, right: str, *, context: _TextNodeContext, lexicon: _DomainLexicon) -> _Proposal | None:
    del gap, context
    joined_key = _normalize_lookup_key(f"{left} {right}")
    replacement = lexicon.forced_merges.get(joined_key)
    if not replacement:
        return None
    return _Proposal(
        before=f"{left} {right}",
        after=replacement,
        error_class="forced-merge",
        lexical_score=1.0,
        context_score=1.0,
        language_score=0.95,
        dom_score=1.0,
        bonus_score=1.0,
        reason_codes=["domain-dictionary", "forced-merge"],
    )


def _hyphen_break_proposal(left: str, right: str, *, context: _TextNodeContext, config: TextCleanupConfig) -> _Proposal:
    merged = f"{left}{right}"
    lexical = max(_token_score(merged, context.paragraph_language), 0.55)
    pyphen_bonus = 1.0 if _pyphen_supports_word(merged, context.paragraph_language, config=config) else 0.0
    reason_codes = ["soft-hyphen-or-line-break"]
    if pyphen_bonus:
        reason_codes.append("pyphen-supported")
    return _Proposal(
        before=f"{left}-{right}",
        after=merged,
        error_class="hyphen-break",
        lexical_score=min(1.0, lexical),
        context_score=0.85,
        language_score=_language_fit_score(merged, context.paragraph_language),
        dom_score=1.0,
        bonus_score=pyphen_bonus,
        reason_codes=reason_codes,
    )


def _glued_word_proposal(
    token: str,
    *,
    context: _TextNodeContext,
    config: TextCleanupConfig,
    lexicon: _DomainLexicon,
) -> _Proposal | None:
    if PLACEHOLDER_RE.fullmatch(token) or len(token) < 5 or len(token) > 28:
        return None
    if lexicon.is_protected(token):
        return None
    if _looks_like_mixed_domain_term(token):
        return None

    forced_variant = lexicon.lookup_variant(token)
    if forced_variant and forced_variant != token and (" " in forced_variant or "-" in forced_variant):
        return _Proposal(
            before=token,
            after=forced_variant,
            error_class="domain-phrase-spacing",
            lexical_score=1.0,
            context_score=0.95,
            language_score=_language_fit_score(forced_variant, context.paragraph_language),
            dom_score=1.0,
            bonus_score=1.0,
            reason_codes=["domain-dictionary", "variant-normalization"],
        )

    whole_score = _token_score(token, context.paragraph_language)
    if config.long_document_mode and (whole_score >= 0.22 or _token_score(token, "en") >= 0.24):
        return None
    if whole_score >= 0.55:
        return None

    split_candidate = _best_split_candidate(token, context=context)
    if split_candidate is None:
        return None
    left, right, lexical_score, reason_codes = split_candidate
    leading_stopword = "leading-stopword" in reason_codes or "single-letter-stopword" in reason_codes
    low_whole_score = "low-whole-score" in reason_codes
    language_score = _language_fit_score(f"{left} {right}", context.paragraph_language)
    if leading_stopword and len(right) >= 6:
        language_score = max(language_score, 0.82 if low_whole_score else 0.72)
    return _Proposal(
        before=token,
        after=f"{left} {right}",
        error_class="glued-word",
        lexical_score=lexical_score,
        context_score=0.9 if leading_stopword else 0.78,
        language_score=language_score,
        dom_score=1.0,
        bonus_score=0.9 if low_whole_score else (0.6 if leading_stopword else 0.0),
        reason_codes=reason_codes,
    )


def _split_word_proposal(
    left: str,
    gap: str,
    right: str,
    *,
    context: _TextNodeContext,
    config: TextCleanupConfig,
    lexicon: _DomainLexicon,
) -> _Proposal | None:
    del gap
    if PLACEHOLDER_RE.fullmatch(left) or PLACEHOLDER_RE.fullmatch(right):
        return None
    if lexicon.is_protected(left) or lexicon.is_protected(right):
        return None
    single_letter_prefix = len(left) == 1 and left.lower() in POLISH_SINGLE_STOPWORDS and len(right) >= 3
    capitalized_prefix = len(left) == 1 and left[:1].isupper() and len(right) >= 4
    if not single_letter_prefix and not capitalized_prefix and (len(left) < 2 or len(right) < 2):
        return None
    merged = f"{left}{right}"
    if lexicon.lookup_variant(merged) or lexicon.is_protected(merged):
        lexical_score = 1.0
        bonus = 1.0
        reason_codes = ["domain-dictionary", "variant-normalization"]
        context_score = 0.95
    else:
        combined_score = _token_score(merged, context.paragraph_language)
        left_score = _token_score(left, context.paragraph_language)
        right_score = _token_score(right, context.paragraph_language)
        suffix_bonus = 1.0 if _looks_like_suffix_fragment(right) else 0.0
        pyphen_bonus = 1.0 if _pyphen_supports_word(merged, context.paragraph_language, config=config) else 0.0
        single_letter_bonus = 1.0 if (single_letter_prefix or capitalized_prefix) else 0.0
        mixed_domain_bonus = 1.0 if _looks_like_mixed_domain_term(merged) else 0.0
        if single_letter_prefix or capitalized_prefix:
            if combined_score < 0.46 and not pyphen_bonus:
                return None
            lexical_score = min(1.0, combined_score + 0.18 * pyphen_bonus + 0.12 * single_letter_bonus + 0.12 * mixed_domain_bonus)
            bonus = max(pyphen_bonus, single_letter_bonus, mixed_domain_bonus)
            reason_codes = ["merge-fragments", "single-letter-prefix"]
            context_score = 0.88
            if capitalized_prefix:
                reason_codes.append("capitalized-fragment")
            if pyphen_bonus:
                reason_codes.append("pyphen-supported")
            if mixed_domain_bonus:
                reason_codes.append("mixed-domain-term")
        else:
            if config.long_document_mode and max(left_score, right_score) >= 0.5 and combined_score < max(left_score, right_score) + 0.12 and not (suffix_bonus or pyphen_bonus or mixed_domain_bonus):
                return None
            if config.long_document_mode and combined_score < 0.52 and not (suffix_bonus or pyphen_bonus or mixed_domain_bonus):
                return None
            if combined_score < 0.38 and not (suffix_bonus and pyphen_bonus) and not mixed_domain_bonus:
                return None
            lexical_score = min(1.0, combined_score + 0.18 * suffix_bonus + 0.12 * pyphen_bonus + 0.14 * mixed_domain_bonus)
            bonus = max(suffix_bonus, pyphen_bonus, mixed_domain_bonus)
            reason_codes = ["merge-fragments"]
            context_score = 0.82 if (suffix_bonus or mixed_domain_bonus) else 0.72
            if suffix_bonus:
                reason_codes.append("suffix-fragment")
            if pyphen_bonus:
                reason_codes.append("pyphen-supported")
            if mixed_domain_bonus:
                reason_codes.append("mixed-domain-term")
        if combined_score <= max(left_score, right_score):
            lexical_score *= 0.82 if not (single_letter_prefix or capitalized_prefix) else 0.92

    return _Proposal(
        before=f"{left} {right}",
        after=merged,
        error_class="split-word",
        lexical_score=lexical_score,
        context_score=context_score,
        language_score=_language_fit_score(merged, context.paragraph_language),
        dom_score=1.0,
        bonus_score=bonus,
        reason_codes=reason_codes,
    )


def _domain_variant_proposal(token: str, *, context: _TextNodeContext, lexicon: _DomainLexicon) -> _Proposal | None:
    canonical = lexicon.lookup_variant(token)
    if not canonical or canonical == token:
        return None
    if canonical.replace(" ", "") == token.replace(" ", ""):
        return _Proposal(
            before=token,
            after=canonical,
            error_class="domain-variant",
            lexical_score=1.0,
            context_score=0.92,
            language_score=_language_fit_score(canonical, context.paragraph_language),
            dom_score=1.0,
            bonus_score=1.0,
            reason_codes=["domain-dictionary", "variant-normalization"],
        )
    return None


def _space_before_punct_proposal(match: re.Match) -> _Proposal:
    punctuation = match.group("punct")
    return _Proposal(
        before=match.group(0),
        after=punctuation,
        error_class="spacing-punctuation",
        lexical_score=1.0,
        context_score=0.95,
        language_score=1.0,
        dom_score=1.0,
        bonus_score=0.0,
        reason_codes=["spacing-before-punctuation"],
    )


def _missing_sentence_space_proposal(match: re.Match) -> _Proposal:
    return _Proposal(
        before=match.group(0),
        after=f"{match.group('lead')} {match.group('next')}",
        error_class="spacing-sentence",
        lexical_score=0.92,
        context_score=0.88,
        language_score=1.0,
        dom_score=1.0,
        bonus_score=0.0,
        reason_codes=["missing-sentence-space"],
    )


def _number_unit_spacing_proposal(match: re.Match) -> _Proposal:
    return _Proposal(
        before=match.group(0),
        after=f"{match.group('number')}{match.group('unit')}",
        error_class="spacing-number-unit",
        lexical_score=1.0,
        context_score=0.95,
        language_score=1.0,
        dom_score=1.0,
        bonus_score=0.0,
        reason_codes=["number-unit-spacing"],
    )


def _inline_decimal_percent_proposal(match: re.Match) -> _Proposal:
    return _Proposal(
        before=match.group(0),
        after=f"{match.group('whole')}{match.group('separator')}{match.group('fraction')}{match.group('unit')}",
        error_class="numeric-fragment",
        lexical_score=1.0,
        context_score=0.98,
        language_score=1.0,
        dom_score=1.0,
        bonus_score=0.0,
        reason_codes=["inline-decimal-percent"],
    )


def _make_decision(proposal: _Proposal, *, context: _TextNodeContext, config: TextCleanupConfig) -> CleanupDecision:
    dom_score = proposal.dom_score
    reason_codes = list(proposal.reason_codes)
    hard_block_reasons = list(proposal.hard_block_reasons)

    if context.is_table_like:
        dom_score = 0.0
        hard_block_reasons.append("table-like-context")
    elif context.is_heading_like:
        dom_score = min(dom_score, 0.6)
        reason_codes.append("heading-context")
    elif context.is_caption_like:
        dom_score = min(dom_score, 0.5)
        reason_codes.append("caption-context")
    elif context.is_technical_note:
        dom_score = min(dom_score, 0.45)
        reason_codes.append("technical-note-context")
    elif context.is_anchor_text:
        dom_score = min(dom_score, 0.75)
        reason_codes.append("anchor-visible-text")

    score = (
        0.35 * proposal.lexical_score
        + 0.25 * proposal.context_score
        + 0.15 * proposal.language_score
        + 0.15 * dom_score
        + 0.10 * proposal.bonus_score
    )
    status = "blocked"
    if hard_block_reasons:
        reason_codes.extend(hard_block_reasons)
        score = 0.0
    elif score >= config.safe_threshold:
        status = "safe_auto_fix"
    elif score >= config.review_threshold:
        status = "review_needed"
    else:
        status = "blocked"

    return CleanupDecision(
        document_path=context.document_path,
        node_xpath=context.node_xpath,
        before=proposal.before,
        after=proposal.after,
        error_class=proposal.error_class,
        score=score,
        status=status,
        reason_codes=_dedupe_list(reason_codes),
    )


def _protect_segments(text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    protected = text

    def _replace(match: re.Match) -> str:
        key = f"__KM_PROTECTED_{len(placeholders)}__"
        placeholders[key] = match.group(0)
        return key

    for pattern in (URL_RE, EMAIL_RE, VERSION_RE, SYSTEM_ID_RE, INVOICE_RE):
        protected = pattern.sub(_replace, protected)
    return protected, placeholders


def _restore_placeholders(text: str, placeholders: dict[str, str]) -> str:
    restored = text
    for key, value in placeholders.items():
        restored = restored.replace(key, value)
    return restored


def _collect_unknown_terms(text: str, *, context: _TextNodeContext, lexicon: _DomainLexicon) -> Counter[str]:
    counter: Counter[str] = Counter()
    if context.is_heading_like or context.is_caption_like:
        return counter
    for token in WORDISH_RE.findall(text):
        lowered = token.lower()
        if len(lowered) < 4:
            continue
        if PLACEHOLDER_RE.fullmatch(token):
            continue
        if lexicon.is_protected(token) or lexicon.lookup_variant(token):
            continue
        if token.isupper() and 2 <= len(token) <= 8:
            continue
        if _looks_like_mixed_domain_term(token):
            continue
        if token[:1].isupper() and not token.isupper():
            continue
        if _token_score(token, context.paragraph_language) >= 0.32 or _token_score(token, "en") >= 0.22:
            continue
        counter[lowered] += 1
    return counter


def _top_unknown_terms(counter: Counter[str], limit: int = 100) -> list[dict[str, Any]]:
    return [{"term": term, "count": count} for term, count in counter.most_common(limit)]


def _best_split_candidate(token: str, *, context: _TextNodeContext) -> tuple[str, str, float, list[str]] | None:
    lowered = token.lower()
    best: tuple[float, tuple[str, str, float, list[str]]] | None = None
    for split_index in range(1, len(token) - 1):
        left = token[:split_index]
        right = token[split_index:]
        if not left[-1].isalpha() or not right[0].isalpha():
            continue
        if len(left) == 1 and left.lower() not in POLISH_SINGLE_STOPWORDS:
            continue
        if len(left) >= 2 and len(right) < 2:
            continue
        left_score = _token_score(left, context.paragraph_language)
        right_score = _token_score(right, context.paragraph_language)
        whole_score = _token_score(lowered, context.paragraph_language)
        if len(left) == 1:
            lexical = min(1.0, max(0.78 if len(right) >= 6 and whole_score <= 0.08 else 0.0, 0.28 + max(right_score, 0.35)))
            if (right_score < 0.58 and not (len(right) >= 6 and whole_score <= 0.08)) or whole_score > 0.15:
                continue
            reason_codes = ["single-letter-stopword", "lexical-split"]
            if len(right) >= 6 and whole_score <= 0.08:
                reason_codes.append("low-whole-score")
        elif left.lower() in PL_STOPWORDS or left.lower() in EN_STOPWORDS:
            lexical = min(1.0, 0.36 + right_score)
            if right_score < 0.58 or whole_score > 0.18:
                continue
            reason_codes = ["leading-stopword", "lexical-split"]
        else:
            if min(left_score, right_score) < 0.55 or whole_score > 0.2:
                continue
            lexical = min(1.0, (left_score + right_score) / 2.0)
            reason_codes = ["lexical-split"]
        score = lexical - whole_score
        candidate = (left, right, lexical, reason_codes)
        if best is None or score > best[0]:
            best = (score, candidate)
    return best[1] if best else None


def _token_score(token: str, paragraph_language: str) -> float:
    if not token or _zipf_frequency is None:
        return 0.0
    lowered = token.lower()
    folded = _fold_diacritics(lowered)
    try:
        pl_score = _zipf_frequency(lowered, "pl")
        en_score = _zipf_frequency(lowered, "en")
        if folded != lowered:
            pl_score = max(pl_score, _zipf_frequency(folded, "pl") * 0.92)
            en_score = max(en_score, _zipf_frequency(folded, "en") * 0.92)
    except Exception:
        return 0.0

    if paragraph_language == "pl":
        return _zipf_to_unit(max(pl_score, en_score * 0.85))
    if paragraph_language == "en":
        return _zipf_to_unit(max(en_score, pl_score * 0.85))
    return _zipf_to_unit(max(pl_score, en_score))


def _looks_like_mixed_domain_term(token: str) -> bool:
    lowered = _normalize_lookup_key(token)
    if len(lowered) < 5 or not lowered.isalpha():
        return False
    for suffix in sorted(POLISH_MIXED_SUFFIX_FRAGMENTS, key=len, reverse=True):
        if not lowered.endswith(suffix) or len(lowered) <= len(suffix) + 3:
            continue
        stem = lowered[: -len(suffix)]
        if len(stem) < 4:
            continue
        if _token_score(stem, "en") >= 0.34:
            return True
    return False


def _zipf_to_unit(value: float) -> float:
    if value <= 0:
        return 0.0
    return min(1.0, value / 6.0)


def _fold_diacritics(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(char)
    )


def _infer_language(text: str) -> str:
    words = [match.group(0).lower() for match in WORDISH_RE.finditer(text)]
    if not words:
        return "mixed"
    pl_score = sum(1 for word in words if word in PL_STOPWORDS) + sum(1 for char in text if char in "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
    en_score = sum(1 for word in words if word in EN_STOPWORDS)
    if pl_score >= en_score + 2:
        return "pl"
    if en_score >= pl_score + 2:
        return "en"
    return "mixed"


def _language_fit_score(value: str, paragraph_language: str) -> float:
    if paragraph_language == "mixed":
        return 0.78
    score = _token_score(value.replace(" ", ""), paragraph_language)
    return max(0.45, score)


def _pyphen_supports_word(word: str, paragraph_language: str, *, config: TextCleanupConfig) -> bool:
    if not config.enable_pyphen or pyphen is None:
        return False
    lang = "pl_PL" if paragraph_language == "pl" else "en_US"
    if paragraph_language == "mixed":
        for candidate_lang in ("pl_PL", "en_US"):
            try:
                if pyphen.Pyphen(lang=candidate_lang).positions(word):
                    return True
            except Exception:
                continue
        return False
    try:
        return bool(pyphen.Pyphen(lang=lang).positions(word))
    except Exception:
        return False


def _normalize_lookup_key(value: str) -> str:
    return re.sub(r"[\s\-_]+", "", (value or "").lower())


def _looks_like_suffix_fragment(value: str) -> bool:
    return value.lower() in POLISH_SUFFIX_FRAGMENTS or (1 <= len(value) <= 3 and value.isalpha())


def _build_summary(
    decisions: list[CleanupDecision],
    unknown_counter: Counter[str],
    chapter_diffs: dict[str, str],
    config: TextCleanupConfig,
    epubcheck: dict[str, Any],
    *,
    package_blocked: bool = False,
    book_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status_counts = Counter(decision.status for decision in decisions)
    error_counts = Counter(decision.error_class for decision in decisions)
    changed_documents = sorted({decision.document_path for decision in decisions if decision.status == "safe_auto_fix"})
    publish_blocked = False
    if epubcheck.get("status") == "failed":
        publish_blocked = True
    elif config.release_gate == "hard" and status_counts.get("review_needed", 0):
        publish_blocked = True

    return {
        "documents_processed": len((book_info or {}).get("text_paths", [])),
        "documents_changed": len(changed_documents),
        "auto_fix_count": status_counts.get("safe_auto_fix", 0),
        "review_needed_count": status_counts.get("review_needed", 0),
        "blocked_count": status_counts.get("blocked", 0),
        "unknown_term_count": sum(unknown_counter.values()),
        "release_gate": config.release_gate,
        "publish_blocked": publish_blocked,
        "package_blocked": package_blocked,
        "epubcheck_status": epubcheck.get("status", "unavailable"),
        "chapter_diff_count": len(chapter_diffs),
        "status_counts": dict(status_counts),
        "error_class_counts": dict(error_counts),
        "language": (book_info or {}).get("language", config.language_hint or "mixed"),
        "spine_document_count": len((book_info or {}).get("spine_paths", [])),
        "toc_count": (book_info or {}).get("toc_count", 0),
    }


def _build_markdown_report(
    summary: dict[str, Any],
    decisions: list[CleanupDecision],
    unknown_terms: list[dict[str, Any]],
    epubcheck: dict[str, Any],
) -> str:
    lines = [
        "# Text Cleanup Report",
        "",
        f"- Documents processed: {summary.get('documents_processed', 0)}",
        f"- Documents changed: {summary.get('documents_changed', 0)}",
        f"- Auto fixes: {summary.get('auto_fix_count', 0)}",
        f"- Review needed: {summary.get('review_needed_count', 0)}",
        f"- Blocked: {summary.get('blocked_count', 0)}",
        f"- Publish blocked: {summary.get('publish_blocked', False)}",
        "",
        "## Top 100 hardest cases",
    ]
    hard_cases = [decision for decision in decisions if decision.status != "safe_auto_fix"]
    hard_cases.sort(key=lambda item: item.score, reverse=True)
    if not hard_cases:
        lines.append("- None")
    else:
        for decision in hard_cases[:100]:
            before = decision.before.replace("\n", " ").strip()
            after = decision.after.replace("\n", " ").strip()
            lines.append(
                f"- [{decision.status}] {decision.error_class} ({decision.score:.2f}) {decision.document_path} :: `{before[:80]}` -> `{after[:80]}`"
            )

    lines.extend(["", "## Unknown terms"])
    if not unknown_terms:
        lines.append("- None")
    else:
        for row in unknown_terms[:100]:
            lines.append(f"- {row['term']} ({row['count']})")

    lines.extend(["", "## EPUBCheck", f"- Status: {epubcheck.get('status', 'unavailable')}"])
    for message in (epubcheck.get("messages") or [])[:20]:
        lines.append(f"- {message}")
    return "\n".join(lines).strip()


def _repair_split_external_anchors(root: etree._Element) -> None:
    for anchor in root.iter():
        if not isinstance(anchor.tag, str) or _local_name(anchor) != "a":
            continue
        href = anchor.get("href", "")
        normalized_href = _normalize_href(href)
        if not URL_HREF_RE.match(normalized_href):
            continue
        suffix, consumers = _collect_anchor_continuation(anchor)
        if not suffix:
            anchor.set("href", normalized_href)
            continue
        repaired_href = _canonicalize_raw_link(f"{normalized_href}{suffix}")
        anchor.set("href", repaired_href)
        anchor_text = "".join(anchor.itertext())
        if URL_HREF_RE.match((anchor_text or "").strip()):
            anchor.text = repaired_href
        for slot_name, node, length in consumers:
            _consume_following_text(slot_name, node, length)


def _collect_anchor_continuation(anchor: etree._Element) -> tuple[str, list[tuple[str, etree._Element, int | None]]]:
    suffix_parts: list[str] = []
    consumers: list[tuple[str, etree._Element, int | None]] = []
    pending: list[tuple[str, etree._Element, int | None]] = []
    current = anchor

    while current is not None:
        tail = current.tail or ""
        if tail:
            if not tail.strip():
                pending.append(("tail", current, len(tail)))
            else:
                prefix = _extract_anchor_continuation_prefix(tail)
                if not prefix:
                    break
                consumers.extend(pending)
                pending = []
                consumers.append(("tail", current, len(prefix)))
                suffix_parts.append(prefix)
                if tail[len(prefix) :].strip():
                    break

        sibling = current.getnext()
        if sibling is None:
            break

        sibling_name = _local_name(sibling)
        if sibling_name in {"a", "br"} or sibling_name in BLOCK_CONTAINER_TAGS:
            break
        if sibling_name == "wbr":
            pending.append(("remove", sibling, None))
            current = sibling
            continue

        sibling_text = sibling.text or ""
        if not sibling_text:
            break
        if not sibling_text.strip():
            pending.append(("text", sibling, len(sibling_text)))
            current = sibling
            continue

        prefix = _extract_anchor_continuation_prefix(sibling_text)
        if not prefix:
            break
        consumers.extend(pending)
        pending = []
        consumers.append(("text", sibling, len(prefix)))
        suffix_parts.append(prefix)
        if sibling_text[len(prefix) :].strip():
            break
        current = sibling

    return "".join(suffix_parts), consumers


def _extract_anchor_continuation_prefix(text: str) -> str:
    match = URL_CONTINUATION_PREFIX_RE.match(text or "")
    if not match:
        return ""
    prefix = match.group(0)
    stripped = prefix.lstrip()
    if stripped.startswith(".") and len(stripped) > 1 and not (stripped[1].islower() or stripped[1].isdigit()):
        return ""
    return prefix


def _consume_following_text(slot_name: str, node: etree._Element, length: int | None) -> None:
    if slot_name == "remove":
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
        return
    if length is None or length <= 0:
        return
    if slot_name == "tail":
        value = node.tail or ""
        node.tail = value[length:]
        return
    value = node.text or ""
    node.text = value[length:]


def _linkify_raw_urls(root: etree._Element) -> None:
    for element in list(root.iter()):
        if not isinstance(element.tag, str):
            continue
        if _local_name(element) == "a" or _has_protected_ancestor(element):
            continue
        if element.text:
            _linkify_slot(element, "text")
        if element.tail and element.getparent() is not None and _local_name(element.getparent()) != "a":
            _linkify_slot(element, "tail")


def _linkify_slot(element: etree._Element, slot_name: str) -> None:
    source_text = element.text if slot_name == "text" else element.tail
    if not source_text:
        return
    fragments = _split_link_fragments(source_text)
    if len(fragments) == 1 and fragments[0][0] == "text":
        return

    namespace_tag = _namespaced_tag(element, "a")
    if slot_name == "text":
        element.text = fragments[0][1] if fragments and fragments[0][0] == "text" else ""
        previous = None
        insert_index = 0
        for kind, value in fragments[1:] if fragments and fragments[0][0] == "text" else fragments:
            if kind == "text":
                if previous is None:
                    element.text = (element.text or "") + value
                else:
                    previous.tail = (previous.tail or "") + value
                continue
            href = _canonicalize_raw_link(value)
            anchor = etree.Element(namespace_tag)
            anchor.set("href", href)
            anchor.text = href
            if previous is None:
                element.insert(insert_index, anchor)
                insert_index += 1
            else:
                previous.addnext(anchor)
            previous = anchor
        return

    parent = element.getparent()
    if parent is None:
        return
    element.tail = fragments[0][1] if fragments and fragments[0][0] == "text" else ""
    previous_node = element
    iterator = fragments[1:] if fragments and fragments[0][0] == "text" else fragments
    for kind, value in iterator:
        if kind == "text":
            previous_node.tail = (previous_node.tail or "") + value
            continue
        href = _canonicalize_raw_link(value)
        anchor = etree.Element(namespace_tag)
        anchor.set("href", href)
        anchor.text = href
        previous_node.addnext(anchor)
        previous_node = anchor


def _split_link_fragments(text: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    last_index = 0
    for match in RAW_LINK_TEXT_RE.finditer(text):
        if match.start() > last_index:
            parts.append(("text", text[last_index:match.start()]))
        token = match.group(0).strip()
        if token:
            parts.append(("link", token))
        last_index = match.end()
    if last_index < len(text):
        parts.append(("text", text[last_index:]))
    return parts or [("text", text)]


def _normalize_anchor_hrefs(root: etree._Element) -> None:
    for anchor in root.iter():
        if not isinstance(anchor.tag, str) or _local_name(anchor) != "a":
            continue
        href = anchor.get("href", "")
        normalized_href = _normalize_href(href)
        if normalized_href != href:
            anchor.set("href", normalized_href)


def _unique_fragment_id(base: str, used_ids: set[str]) -> str:
    seed = re.sub(r"[^a-z0-9_-]+", "-", (base or "").strip().lower()).strip("-_") or "anchor"
    candidate = seed
    suffix = 2
    while candidate in used_ids:
        candidate = f"{seed}-{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _dedupe_document_ids(root: etree._Element) -> None:
    used_ids: set[str] = set()
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        current_id = str(element.get("id", "") or "").strip()
        if not current_id:
            continue
        if current_id not in used_ids:
            used_ids.add(current_id)
            continue
        element.set("id", _unique_fragment_id(current_id, used_ids))


def _local_name(element: etree._Element | None) -> str:
    if element is None or not isinstance(element.tag, str):
        return ""
    if "}" in element.tag:
        return element.tag.rsplit("}", 1)[-1].lower()
    return element.tag.lower()


def _namespaced_tag(element: etree._Element, tag_name: str) -> str:
    if isinstance(element.tag, str) and element.tag.startswith("{"):
        namespace = element.tag.split("}", 1)[0][1:]
        return f"{{{namespace}}}{tag_name}"
    if element.nsmap and None in element.nsmap:
        return f"{{{element.nsmap[None]}}}{tag_name}"
    return tag_name


def _dedupe_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped
