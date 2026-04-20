from __future__ import annotations

import html as html_module
import io
import re
import zipfile

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

try:
    from wordfreq import zipf_frequency as _zipf_frequency
except Exception:
    _zipf_frequency = None


WORD_RE = re.compile(r"\b[^\W\d_]{2,24}\b", re.UNICODE)
SPLIT_WORD_RE = re.compile(r"\b([^\W\d_]{2,10})\s+([^\W\d_]{2,14})\b", re.UNICODE)
BRACKETED_REF_RE = re.compile(r"\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]")
REF_LABEL_RE = re.compile(
    r"(?i)\b(fig|figure|ref|refs|tab|table|eq|equation|sec|section|chap|chapter|pp|p)\s+\.\s*"
)
DOI_RE = re.compile(r"(?i)\bdoi\s*:\s*10\.\s*(\d{4,9})\s*/\s*([A-Za-z0-9./;()_:-]+)")
URL_HREF_RE = re.compile(r"(?i)^(?:https?://|www\.|doi:)")
RAW_LINK_TEXT_RE = re.compile(r"(?i)\b(?:https?://[^\s<>()]+|www\.[^\s<>()]+|doi:10\.[^\s<>()]+)")
URL_CONTINUATION_PREFIX_RE = re.compile(
    r"^\s*(?:\.[A-Za-z0-9-]+(?:/[A-Za-z0-9._~%!$&'()*+,;=:@/?#-]*)?|/[A-Za-z0-9._~%!$&'()*+,;=:@/?#-]+|[?#&=][A-Za-z0-9._~%!$&'()*+,;=:@/?#-]+|:[0-9]+(?:/[A-Za-z0-9._~%!$&'()*+,;=:@/?#-]*)?)"
)
OCR_CHAPTER_RE = re.compile(r"(?i)^(chapter|rozdzial|rozdzia\u0142|appendix|dodatek)\b")
EPUB_TEXT_MEMBER_RE = re.compile(r"(?i)(?:^|/)(?!nav\.xhtml$|cover\.xhtml$).+\.(?:xhtml|html)$")
OCR_HEADING_REJECT_RE = re.compile(
    r"(?i)\b(?:material sponsorowany|materia\u0142 sponsorowany|advertorial|reklama|www\.|https?://|doi:)\b"
)
OCR_VALUE_LINE_RE = re.compile(
    r"(?i)(?:\b\d+[.,]\s+\d+%|\b\d+[.,]?\d*\s*(?:%|PLN|USD|EUR|GBP|z\u0142|kg|g|mg|km|m|cm|mm|MB|GB|TB|ms|s|h|pkt)\b)"
)

SPLIT_JOIN_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "if",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "w",
    "z",
    "i",
    "na",
    "do",
    "od",
    "o",
    "u",
    "oraz",
    "ale",
    "this",
    "that",
}

SKIP_SPLIT_TAGS = {"style", "script", "code", "pre"}
BLOCK_URL_REPAIR_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "dd",
    "div",
    "dl",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "section",
    "table",
    "td",
    "th",
    "ul",
}


def normalize_text(text: str, *, preserve_edges: bool = False) -> str:
    if not text:
        return ""

    has_leading_space = bool(text[:1].isspace())
    has_trailing_space = bool(text[-1:].isspace())

    normalized = html_module.unescape(text)
    normalized = normalized.replace("\u00ad", "")
    normalized = normalized.replace("\xa0", " ")
    normalized = normalized.replace("\r", " ").replace("\n", " ")
    normalized = _compact_external_references(normalized)
    protected, placeholders = _protect_non_lexical_segments(normalized)
    protected = _repair_pdf_split_words(protected)
    protected = _insert_missing_sentence_spaces(protected)
    protected = _repair_glued_words(protected)
    protected = re.sub(r"(\d+[.,])\s+(\d+%)", r"\1\2", protected)
    protected = re.sub(r"\s+([,.;:!?])", r"\1", protected)
    protected = re.sub(r"([(\[{])\s+", r"\1", protected)
    protected = re.sub(r"\s+([)\]}])", r"\1", protected)
    protected = re.sub(r"\s{2,}", " ", protected).strip()
    normalized = _restore_placeholders(protected, placeholders)

    if preserve_edges and normalized:
        if has_leading_space and not normalized.startswith((",", ".", ";", ":", "!", "?", ")", "]", "}")):
            normalized = f" {normalized}"
        if has_trailing_space and not normalized.endswith(("(", "[", "{", "/")):
            normalized = f"{normalized} "
    return normalized


def normalize_html_fragment(fragment: str) -> str:
    if not fragment or "<" not in fragment:
        normalized = normalize_text(fragment)
        return _linkify_text_to_markup(normalized) or normalized

    soup = BeautifulSoup(f"<wrapper>{fragment}</wrapper>", "xml")
    wrapper = soup.find("wrapper")
    if wrapper is None:
        return fragment

    for node in list(wrapper.descendants):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent is None or parent.name in SKIP_SPLIT_TAGS or parent.name == "a":
            continue
        normalized = normalize_text(str(node), preserve_edges=True)
        if normalized != str(node):
            node.replace_with(normalized)

    for node in list(wrapper.descendants):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent is None or parent.name in SKIP_SPLIT_TAGS or parent.name == "a":
            continue
        _linkify_text_node(node)

    _repair_split_external_anchors(wrapper)

    for anchor in wrapper.find_all("a", href=True):
        href = anchor.get("href", "")
        normalized_href = _normalize_href(href)
        if normalized_href != href:
            anchor["href"] = normalized_href

    return "".join(str(child) for child in wrapper.contents)


def normalize_xhtml_document(document: str) -> str:
    if not document or "<html" not in document.lower():
        return document

    soup = BeautifulSoup(document, "xml")
    body = soup.find("body")
    if body is None:
        return document

    for node in list(body.descendants):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent is None or parent.name in SKIP_SPLIT_TAGS or parent.name == "a":
            continue
        if _should_skip_parent(parent):
            continue
        normalized = normalize_text(str(node), preserve_edges=True)
        if normalized != str(node):
            node.replace_with(normalized)

    for node in list(body.descendants):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent is None or parent.name in SKIP_SPLIT_TAGS or parent.name == "a":
            continue
        if _should_skip_parent(parent):
            continue
        _linkify_text_node(node)

    _repair_split_external_anchors(body)

    for anchor in body.find_all("a", href=True):
        href = anchor.get("href", "")
        normalized_href = _normalize_href(href)
        if normalized_href != href:
            anchor["href"] = normalized_href

    return str(soup)


def normalize_epub_text_package(epub_bytes: bytes, *, publication_profile: str | None = None) -> bytes:
    from text_cleanup_engine import clean_epub_text_package

    result = clean_epub_text_package(epub_bytes, publication_profile=publication_profile)
    return result.epub_bytes


def ocr_text_to_html_parts(text: str) -> list[str]:
    normalized = re.sub(r"\r\n?", "\n", text or "")
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", normalized) if block.strip()]
    html_parts: list[str] = []

    for block_index, block in enumerate(blocks):
        lines = [_normalize_ocr_line(line) for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        paragraphs = _split_ocr_lines_into_paragraphs(lines)
        for paragraph_index, paragraph_lines in enumerate(paragraphs):
            merged = _merge_ocr_lines(paragraph_lines)
            if not merged:
                continue

            if (
                not html_parts
                and block_index == 0
                and paragraph_index == 0
                and len(merged.split()) <= 10
                and merged[:1].isupper()
                and not _looks_like_ocr_non_heading_line(merged)
            ):
                html_parts.append(f"<h1>{html_module.escape(merged)}</h1>")
                continue

            if _looks_like_ocr_heading_line(merged):
                tag = "h2" if OCR_CHAPTER_RE.match(merged) else "h3"
                html_parts.append(f"<{tag}>{html_module.escape(merged)}</{tag}>")
                continue

            html_parts.append(f"<p>{html_module.escape(merged)}</p>")

    return html_parts


def _normalize_href(href: str) -> str:
    href = (href or "").strip()
    if not href or not URL_HREF_RE.match(href):
        return href
    normalized = _canonicalize_raw_link(normalize_text(href))
    return _trim_invalid_percent_escape_tail(normalized)


def _trim_link_token(token: str) -> str:
    trimmed = (token or "").strip().strip("<>\"'")
    while trimmed and trimmed[-1] in ".,;:!?":
        trimmed = trimmed[:-1]
    for closing, opening in ((")", "("), ("]", "["), ("}", "{")):
        while trimmed.endswith(closing) and trimmed.count(closing) > trimmed.count(opening):
            trimmed = trimmed[:-1]
    return trimmed


def _canonicalize_raw_link(raw: str) -> str:
    normalized = _trim_link_token(normalize_text(raw))
    lowered = normalized.lower()
    if lowered.startswith("doi:"):
        return f"https://doi.org/{normalized[4:]}"
    if lowered.startswith("www."):
        return f"https://{normalized}"
    return normalized


def _trim_invalid_percent_escape_tail(value: str) -> str:
    compact = (value or "").strip()
    while True:
        updated = re.sub(r"%(?:[0-9A-Fa-f])?$", "", compact)
        if updated == compact:
            return compact
        compact = updated.rstrip()


def _linkify_text_to_markup(text: str) -> str:
    if not text:
        return ""

    parts: list[str] = []
    last_index = 0
    matched = False
    for match in RAW_LINK_TEXT_RE.finditer(text):
        raw_token = match.group(0)
        trimmed_token = _trim_link_token(raw_token)
        if not trimmed_token:
            continue
        href = _canonicalize_raw_link(trimmed_token)
        if not href:
            continue
        matched = True
        parts.append(html_module.escape(text[last_index:match.start()]))
        parts.append(f'<a href="{html_module.escape(href)}">{html_module.escape(href)}</a>')
        trailing = raw_token[len(trimmed_token):]
        if trailing:
            parts.append(html_module.escape(trailing))
        last_index = match.end()

    if not matched:
        return ""

    parts.append(html_module.escape(text[last_index:]))
    return "".join(parts)


def _replace_text_node_with_markup(node: NavigableString, markup: str) -> None:
    fragment = BeautifulSoup(f"<wrapper>{markup}</wrapper>", "xml")
    wrapper = fragment.find("wrapper")
    if wrapper is None:
        return
    children = list(wrapper.contents)
    if not children:
        return

    first_child = children[0]
    node.replace_with(first_child)
    previous = first_child
    for child in children[1:]:
        previous.insert_after(child)
        previous = child


def _linkify_text_node(node: NavigableString) -> None:
    markup = _linkify_text_to_markup(str(node))
    if markup:
        _replace_text_node_with_markup(node, markup)


def _repair_split_external_anchors(scope: Tag) -> None:
    for anchor in list(scope.find_all("a", href=True)):
        href = _normalize_href(anchor.get("href", ""))
        if not href.startswith(("http://", "https://")):
            continue

        suffix, consumed_nodes = _collect_url_continuation(anchor)
        if not suffix:
            anchor["href"] = href
            continue

        repaired_href = _canonicalize_raw_link(f"{href}{suffix}")
        if not _looks_like_valid_url_repair(href, repaired_href, suffix):
            anchor["href"] = href
            continue

        anchor["href"] = repaired_href
        anchor_text = normalize_text(anchor.get_text("", strip=False))
        if _anchor_text_should_track_href(anchor_text, href):
            anchor.clear()
            anchor.append(repaired_href)
        for node, consumed_length in consumed_nodes:
            _consume_leading_text(node, consumed_length)


def _collect_url_continuation(anchor: Tag) -> tuple[str, list[tuple[NavigableString | Tag, int | None]]]:
    suffix_parts: list[str] = []
    consumed_nodes: list[tuple[NavigableString | Tag, int | None]] = []
    provisional_nodes: list[tuple[NavigableString | Tag, int | None]] = []

    sibling = anchor.next_sibling
    while sibling is not None:
        next_sibling = sibling.next_sibling

        if isinstance(sibling, NavigableString):
            text = str(sibling)
            if not text:
                sibling = next_sibling
                continue
            if not text.strip():
                provisional_nodes.append((sibling, len(text)))
                sibling = next_sibling
                continue
            prefix = _extract_url_continuation_prefix(text)
            if not prefix:
                break
            consumed_nodes.extend(provisional_nodes)
            provisional_nodes = []
            consumed_nodes.append((sibling, len(prefix)))
            suffix_parts.append(prefix)
            sibling = next_sibling
            continue

        if not isinstance(sibling, Tag):
            break
        if sibling.name in SKIP_SPLIT_TAGS or sibling.name in BLOCK_URL_REPAIR_TAGS or sibling.name in {"a", "br"}:
            break
        if sibling.name == "wbr":
            provisional_nodes.append((sibling, None))
            sibling = next_sibling
            continue

        text = sibling.get_text("", strip=False)
        if not text:
            break
        if not text.strip():
            provisional_nodes.append((sibling, len(text)))
            sibling = next_sibling
            continue
        prefix = _extract_url_continuation_prefix(text)
        if not prefix:
            break
        consumed_nodes.extend(provisional_nodes)
        provisional_nodes = []
        consumed_nodes.append((sibling, len(prefix)))
        suffix_parts.append(prefix)
        sibling = next_sibling

    return "".join(suffix_parts), consumed_nodes


def _extract_url_continuation_prefix(text: str) -> str:
    match = URL_CONTINUATION_PREFIX_RE.match(text or "")
    if not match:
        return ""
    prefix = match.group(0)
    stripped = prefix.lstrip()
    if stripped.startswith(".") and len(stripped) > 1 and not (stripped[1].islower() or stripped[1].isdigit()):
        return ""
    return prefix


def _anchor_text_should_track_href(anchor_text: str, href: str) -> bool:
    normalized_text = normalize_text(anchor_text)
    if not normalized_text:
        return True
    if URL_HREF_RE.match(normalized_text):
        return True
    href_without_scheme = re.sub(r"(?i)^https?://", "", href)
    return normalized_text == href_without_scheme or normalized_text == href


def _looks_like_valid_url_repair(original_href: str, repaired_href: str, suffix: str) -> bool:
    if not repaired_href or repaired_href == original_href:
        return False
    stripped_suffix = suffix.lstrip()
    if stripped_suffix.startswith(".") and len(stripped_suffix) > 1:
        return stripped_suffix[1].islower() or stripped_suffix[1].isdigit()
    return True


def _consume_leading_text(node: NavigableString | Tag, length: int | None) -> int:
    if length is None:
        if isinstance(node, Tag):
            node.decompose()
        else:
            node.extract()
        return 0

    if length <= 0:
        return 0
    if isinstance(node, NavigableString):
        text = str(node)
        consumed = min(length, len(text))
        remainder = text[consumed:]
        if remainder:
            node.replace_with(remainder)
        else:
            node.extract()
        return consumed

    remaining = length
    for child in list(node.contents):
        if remaining <= 0:
            break
        consumed = _consume_leading_text(child, remaining)
        remaining -= consumed

    if not node.get_text("", strip=False):
        node.decompose()
    return length - remaining


def _compact_external_references(text: str) -> str:
    compact = text
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
        if updated == compact:
            break
        compact = updated

    compact = DOI_RE.sub(r"doi:10.\1/\2", compact)
    compact = REF_LABEL_RE.sub(lambda match: f"{match.group(1)}. ", compact)
    compact = BRACKETED_REF_RE.sub(lambda match: f'[{re.sub(r"\s+", "", match.group(1))}]', compact)
    return compact


def _protect_non_lexical_segments(text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    protected = text

    def _replace(match: re.Match) -> str:
        key = f"__KM_PLACEHOLDER_{len(placeholders)}__"
        placeholders[key] = match.group(0)
        return key

    protected = re.sub(r"(?i)\b(?:https?://|www\.)\S+", _replace, protected)
    protected = re.sub(r"(?i)\bdoi:\S+", _replace, protected)
    return protected, placeholders


def _restore_placeholders(text: str, placeholders: dict[str, str]) -> str:
    restored = text
    for key, value in placeholders.items():
        restored = restored.replace(key, value)
    return restored


def _insert_missing_sentence_spaces(text: str) -> str:
    if not text:
        return text

    pieces: list[str] = []
    for index, char in enumerate(text):
        pieces.append(char)
        if index + 1 >= len(text):
            continue

        next_char = text[index + 1]
        if next_char.isspace():
            continue

        previous_char = text[index - 1] if index > 0 else ""
        if char in "!?":
            if next_char.isupper() or next_char in {'"', "'"}:
                pieces.append(" ")
            continue
        if char == ".":
            if previous_char.isdigit() and next_char.isdigit():
                continue
            if next_char.isupper() and not previous_char.isupper():
                pieces.append(" ")
            continue
        if char == ":" and next_char.isupper():
            pieces.append(" ")

    return "".join(pieces)


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
    if not left_clean[-1].isalpha() or not right_clean[0].isalpha():
        return False

    left_is_stop = left_clean.lower() in SPLIT_JOIN_STOPWORDS
    right_is_stop = right_clean.lower() in SPLIT_JOIN_STOPWORDS
    combined = f"{left_clean}{right_clean}"
    joined_score = _lexical_zipf(combined)
    left_score = _lexical_zipf(left_clean)
    right_score = _lexical_zipf(right_clean)

    if joined_score < 2.8:
        return False
    if (left_is_stop or right_is_stop) and not (
        (left_score <= 2.0 or right_score <= 2.0) and joined_score >= 3.3
    ):
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

        repaired = SPLIT_WORD_RE.sub(_replace, repaired)
        if not changed:
            break
    return repaired


def _repair_glued_words(text: str) -> str:
    def _replace(match: re.Match) -> str:
        token = match.group(0)
        camel_split = _split_camel_case_token(token)
        if camel_split != token:
            return camel_split
        return _split_glued_token(token)

    return WORD_RE.sub(_replace, text)


def _split_camel_case_token(token: str) -> str:
    pieces: list[str] = []
    for index, char in enumerate(token):
        if (
            index > 0
            and char.isupper()
            and token[index - 1].islower()
            and (index + 1 < len(token) and token[index + 1].islower())
        ):
            pieces.append(" ")
        pieces.append(char)
    return "".join(pieces)


def _split_glued_token(token: str) -> str:
    if _zipf_frequency is None or len(token) < 7 or len(token) > 24:
        return token
    if not token.isalpha() or token.isupper():
        return token

    lowered = token.lower()
    whole_score = _lexical_zipf(lowered)
    best_candidate: tuple[float, str] | None = None

    for split_index in range(2, len(token) - 1):
        left = token[:split_index]
        right = token[split_index:]
        if not left[-1].isalpha() or not right[0].isalpha():
            continue
        if min(len(left), len(right)) < 3:
            continue

        left_score = _lexical_zipf(left)
        right_score = _lexical_zipf(right)
        if not _should_split_glued_word(left, right, whole_score, left_score, right_score):
            continue

        candidate_score = min(left_score, right_score) + ((left_score + right_score) / 2.0) - whole_score
        replacement = f"{left} {right}"
        if best_candidate is None or candidate_score > best_candidate[0]:
            best_candidate = (candidate_score, replacement)

    return best_candidate[1] if best_candidate else token


def _should_split_glued_word(left: str, right: str, whole_score: float, left_score: float, right_score: float) -> bool:
    left_is_stop = left.lower() in SPLIT_JOIN_STOPWORDS
    right_is_stop = right.lower() in SPLIT_JOIN_STOPWORDS

    if left_is_stop or right_is_stop:
        content_score = right_score if left_is_stop else left_score
        return whole_score <= 1.6 and content_score >= 4.4

    if whole_score > 2.4:
        return False
    return left_score >= 3.0 and right_score >= 3.0


def _normalize_ocr_line(line: str) -> str:
    normalized = re.sub(r"\s+", " ", (line or "").strip())
    return normalize_text(normalized)


def _split_ocr_lines_into_paragraphs(lines: list[str]) -> list[list[str]]:
    paragraphs: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if not line:
            if current:
                paragraphs.append(current)
                current = []
            continue

        if not current:
            current = [line]
            continue

        previous = current[-1]
        if _looks_like_ocr_heading_line(line) or _starts_new_ocr_paragraph(previous, line):
            paragraphs.append(current)
            current = [line]
            continue

        current.append(line)

    if current:
        paragraphs.append(current)
    return paragraphs


def _starts_new_ocr_paragraph(previous_line: str, current_line: str) -> bool:
    if not previous_line or not current_line:
        return False
    if _looks_like_ocr_heading_line(previous_line):
        return True
    if current_line.startswith(("-", "*", "\u2022")):
        return True
    if re.match(r"^\d+[.)]\s+\S", current_line):
        return True
    if re.match(r"^[A-Z]\.\s", current_line):
        return True
    if previous_line.endswith((".", "!", "?", '"', "'", "\u201d")) and current_line[:1].isupper():
        return len(previous_line) >= 45 and len(current_line) >= 30
    return False


def _merge_ocr_lines(lines: list[str]) -> str:
    merged = " ".join(line.strip() for line in lines if line.strip())
    merged = re.sub(r"(?<=\w)- (?=\w)", "", merged)
    return normalize_text(merged)


def _looks_like_ocr_heading_line(line: str) -> bool:
    candidate = re.sub(r"\s+", " ", (line or "").strip())
    if not candidate or len(candidate) > 120:
        return False
    if _looks_like_ocr_non_heading_line(candidate):
        return False
    if re.search(r"[.!?;:]$", candidate):
        return False
    if OCR_CHAPTER_RE.match(candidate):
        return True
    if re.match(r"^[0-9]+(\.[0-9]+)*\s+[A-Z]", candidate):
        return True
    words = candidate.split()
    if 1 <= len(words) <= 8 and candidate[:1].isupper():
        title_case_words = sum(word[:1].isupper() for word in words if word[:1].isalpha())
        return title_case_words >= max(1, len(words) - 1)
    return False


def _looks_like_ocr_non_heading_line(line: str) -> bool:
    candidate = re.sub(r"\s+", " ", (line or "").strip())
    if not candidate:
        return True
    if candidate.startswith(("-", "*", "\u2022", "\uf0b7")):
        return True
    if OCR_HEADING_REJECT_RE.search(candidate):
        return True
    if OCR_VALUE_LINE_RE.search(candidate):
        return True
    return False


def _should_skip_parent(parent: Tag) -> bool:
    classes = parent.get("class", [])
    if isinstance(classes, str):
        classes = classes.split()
    class_names = set(classes or [])
    if "page-marker" in class_names:
        return True
    if parent.name == "span" and "page-marker" in class_names:
        return True
    return False


def _is_pre_paginated_epub(epub_bytes: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as epub_zip:
            for name in epub_zip.namelist():
                if not name.lower().endswith(".opf"):
                    continue
                opf_data = epub_zip.read(name).decode("utf-8", errors="ignore")
                if "pre-paginated" in opf_data and "rendition:layout" in opf_data:
                    return True
    except zipfile.BadZipFile:
        return False
    return False


def __getattr__(name: str):
    if name in {
        "CleanupDecision",
        "TextCleanupConfig",
        "TextCleanupResult",
        "clean_epub_text_package",
    }:
        from text_cleanup_engine import (
            CleanupDecision,
            TextCleanupConfig,
            TextCleanupResult,
            clean_epub_text_package,
        )

        exports = {
            "CleanupDecision": CleanupDecision,
            "TextCleanupConfig": TextCleanupConfig,
            "TextCleanupResult": TextCleanupResult,
            "clean_epub_text_package": clean_epub_text_package,
        }
        return exports[name]
    raise AttributeError(name)


__all__ = [
    "normalize_text",
    "normalize_html_fragment",
    "normalize_xhtml_document",
    "normalize_epub_text_package",
    "ocr_text_to_html_parts",
    "CleanupDecision",
    "TextCleanupConfig",
    "TextCleanupResult",
    "clean_epub_text_package",
]
