from __future__ import annotations

import json
import re
import zipfile
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup


BLOCK_TAGS = ("p", "h1", "h2", "h3")
LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
SPLIT_WORD_RE = re.compile(r"\b(?P<left>[^\W\d_]{2,})-\s+(?P<right>[^\W\d_]{2,})\b", re.UNICODE)
LEADING_LOWER_RE = re.compile(r"^[\"'“”‘’(\[]*(?P<letter>[a-ząćęłńóśźż])", re.IGNORECASE)
TRAILING_SENTENCE_STOP_RE = re.compile(r"[.!?…:;][\"'”’)\]]*$")
PAGE_LABEL_RE = re.compile(r"^(?:strona|page|s\.)\s*\d{1,4}(?:\s*/\s*\d{1,4})?$", re.IGNORECASE)
KNOWN_BRAND_TOKENS = {
    "ChatGPT",
    "GitHub",
    "LinkedIn",
    "OpenAI",
    "PwC",
    "SteerCo",
    "StrefaPMI",
    "YouTube",
}
KNOWN_JOINED_WORD_FALSE_POSITIVES = {
    "aeropress",
    "inplus",
    "intercontinental",
    "macdonnel",
    "macmurray",
    "mccudden",
    "mcinerney",
    "mcdonald",
    "mcdonaldzie",
    "youtubie",
}
MC_MAC_PREFIX_RE = re.compile(r"^(?:Mc|Mac)[A-Z][A-Za-ząćęłńóśźż-]*$")


def normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def iter_epub_blocks(epub_path: Path):
    with zipfile.ZipFile(epub_path) as zf:
        xhtml_names = sorted(
            name
            for name in zf.namelist()
            if name.endswith(".xhtml") and not name.endswith("nav.xhtml")
        )
        for name in xhtml_names:
            soup = BeautifulSoup(zf.read(name), "xml")
            blocks = []
            for tag in soup.find_all(BLOCK_TAGS):
                text = normalize_text(tag.get_text(" ", strip=True))
                if not text:
                    continue
                classes = tag.get("class") or []
                if isinstance(classes, str):
                    classes = classes.split()
                else:
                    classes = list(classes)
                blocks.append(
                    {
                        "file": name,
                        "tag": tag.name,
                        "text": text,
                        "classes": classes,
                    }
                )
            yield name, blocks


def count_split_word_matches(text: str) -> int:
    return len(list(find_split_word_candidates(text)))


def find_split_word_candidates(text: str):
    for match in SPLIT_WORD_RE.finditer(text or ""):
        left = match.group("left")
        right = match.group("right")
        if left.islower() and right.islower():
            confidence = "high"
            decision = "auto_fix"
            reason = "lowercase_hyphen_split"
        elif left[:1].isupper() and left[1:].islower() and right.islower():
            confidence = "high"
            decision = "auto_fix"
            reason = "titlecase_first_word_hyphen_split"
        else:
            confidence = "medium"
            decision = "review_only"
            reason = "proper_name_or_ambiguous_hyphen_split"
        yield {
            "match": match.group(0),
            "left": left,
            "right": right,
            "confidence": confidence,
            "decision": decision,
            "reason": reason,
        }


def _clean_token(token: str) -> str:
    return token.strip(".,;:!?\"'“”‘’()[]{}<>")


def classify_joined_word_token(token: str) -> dict | None:
    cleaned = _clean_token(token)
    if cleaned in KNOWN_BRAND_TOKENS:
        return None
    if cleaned.casefold() in KNOWN_JOINED_WORD_FALSE_POSITIVES:
        return None
    if MC_MAC_PREFIX_RE.match(cleaned):
        return None
    if any(char.isdigit() for char in cleaned):
        return None
    if any(char in cleaned for char in "?!/\\@#%&*=+|~"):
        return None
    if not cleaned or not all(char.isalpha() for char in cleaned):
        return None

    boundaries = [
        index
        for index in range(1, len(cleaned))
        if cleaned[index - 1].islower() and cleaned[index].isupper()
    ]
    if len(boundaries) != 1:
        return None

    boundary = boundaries[0]
    left = cleaned[:boundary]
    right = cleaned[boundary:]
    if len(left) < 2 or len(right) < 2:
        return None
    if right.isupper():
        return None

    confidence = "medium"
    decision = "review_only"
    reason = "lower_to_upper_token_boundary"
    if cleaned[:1].islower() and right[:1].isupper() and right[1:].islower():
        if len(left) >= 4 and len(right) >= 4:
            confidence = "high"
            decision = "auto_fix"
            reason = "embedded_titlecase_join_strong"
        else:
            reason = "embedded_titlecase_join_short"

    return {
        "match": cleaned,
        "left": left,
        "right": right,
        "confidence": confidence,
        "decision": decision,
        "reason": reason,
    }


def count_joined_word_candidates(text: str) -> int:
    count = 0
    for token in normalize_text(text).split():
        if classify_joined_word_token(token):
            count += 1
    return count


def _looks_like_boundary_candidate(previous_text: str, current_text: str) -> tuple[bool, str]:
    if not previous_text or not current_text:
        return False, ""
    if PAGE_LABEL_RE.match(previous_text) or PAGE_LABEL_RE.match(current_text):
        return False, "page_label_context"
    if TRAILING_SENTENCE_STOP_RE.search(previous_text):
        return False, "sentence_closed"
    if len(previous_text) < 24 or len(current_text) < 12:
        return False, "too_short"
    leading = LEADING_LOWER_RE.search(current_text)
    if not leading:
        return False, "not_lowercase_continuation"
    if current_text.startswith(("•", "-", "—")):
        return False, "list_or_dialogue_context"
    alpha_chars = [char for char in current_text if char.isalpha()]
    if alpha_chars:
        upper_ratio = sum(1 for char in alpha_chars if char.isupper()) / len(alpha_chars)
        if upper_ratio > 0.6:
            return False, "heading_like_current"
    return True, "lowercase_continuation"


def audit_epub_text(epub_path: Path) -> dict:
    split_matches = []
    joined_matches = []
    boundary_matches = []
    files_scanned = 0

    for name, blocks in iter_epub_blocks(epub_path):
        files_scanned += 1
        for block in blocks:
            for split in find_split_word_candidates(block["text"]):
                split_matches.append(
                    {
                        "file": name,
                        "tag": block["tag"],
                        "snippet": block["text"][:160],
                        **split,
                    }
                )
            for token in normalize_text(block["text"]).split():
                candidate = classify_joined_word_token(token)
                if candidate:
                    joined_matches.append(
                        {
                            "file": name,
                            "tag": block["tag"],
                            "snippet": block["text"][:160],
                            **candidate,
                        }
                    )
        for previous, current in zip(blocks, blocks[1:]):
            if Path(name).name.lower() in {"title.xhtml"}:
                continue
            if previous["tag"] != "p" or current["tag"] != "p":
                continue
            previous_classes = set(previous.get("classes") or [])
            current_classes = set(current.get("classes") or [])
            if previous_classes.intersection({"byline", "organizational-line", "section-banner"}):
                continue
            if current_classes.intersection({"byline", "organizational-line", "section-banner"}):
                continue
            is_candidate, reason = _looks_like_boundary_candidate(previous["text"], current["text"])
            if is_candidate:
                boundary_matches.append(
                    {
                        "file": name,
                        "previous_tag": previous["tag"],
                        "current_tag": current["tag"],
                        "previous_text": previous["text"][:160],
                        "current_text": current["text"][:160],
                        "confidence": "medium",
                        "decision": "review_only",
                        "reason": reason,
                    }
                )

    epub_display = epub_path.as_posix()
    return {
        "date": date.today().isoformat(),
        "epub": epub_display,
        "split_word_scan": {
            "date": date.today().isoformat(),
            "epub": epub_display,
            "scan_scope": "all_final_xhtml_content",
            "files_scanned": files_scanned,
            "matches_total": len(split_matches),
            "matches": split_matches,
        },
        "joined_word_scan": {
            "date": date.today().isoformat(),
            "epub": epub_display,
            "scan_scope": "all_final_xhtml_content_token_level",
            "files_scanned": files_scanned,
            "matches_total": len(joined_matches),
            "matches": joined_matches,
        },
        "boundary_scan": {
            "date": date.today().isoformat(),
            "epub": epub_display,
            "scan_scope": "adjacent_block_context",
            "files_scanned": files_scanned,
            "matches_total": len(boundary_matches),
            "matches": boundary_matches,
        },
    }


def write_audit_reports(epub_path: Path, split_report: Path, joined_report: Path, boundary_report: Path) -> dict:
    audit = audit_epub_text(epub_path)
    split_report.write_text(json.dumps(audit["split_word_scan"], ensure_ascii=False, indent=2), encoding="utf-8")
    joined_report.write_text(json.dumps(audit["joined_word_scan"], ensure_ascii=False, indent=2), encoding="utf-8")
    boundary_report.write_text(json.dumps(audit["boundary_scan"], ensure_ascii=False, indent=2), encoding="utf-8")
    return audit
