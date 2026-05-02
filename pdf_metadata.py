from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF


PDF_DATE_RE = re.compile(r"^D:(?P<year>\d{4})(?P<month>\d{2})?(?P<day>\d{2})?")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
PDF_TECHNICAL_METADATA_MARKERS = (
    "adobe",
    "acrobat",
    "distiller",
    "ghostscript",
    "pdf",
    "producer",
    "scanner",
    "microsoft word",
    "libreoffice",
    "openoffice",
    "quarkxpress",
    "indesign",
)
PDF_TITLE_NOISE_RE = re.compile(
    r"(?i)\b(?:isbn|issn|copyright|all rights reserved|www\.|https?://|table of contents|contents|spis tresci|spis treści)\b"
)
LETTER_SPACED_TOKEN_RE = re.compile(r"^[A-Z&]{1,3}$")


def _normalize_pdf_metadata_text(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.strip(" \t\r\n\u00a0")


def _collapse_letter_spaced_pdf_line(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    groups = [part.strip() for part in re.split(r"\s{2,}", raw) if part.strip()]
    collapsed_groups: list[str] = []
    for group in groups or [raw]:
        tokens = group.split()
        short_caps = [token for token in tokens if LETTER_SPACED_TOKEN_RE.fullmatch(token)]
        if len(tokens) >= 2 and len(short_caps) == len(tokens):
            collapsed_groups.append("".join(tokens))
        else:
            collapsed_groups.append(group)
    return _normalize_pdf_metadata_text(" ".join(collapsed_groups))


def _clean_pdf_metadata_value(value: object) -> str:
    text = _collapse_letter_spaced_pdf_line(value)
    if not text:
        return ""
    text = text.replace("\u00ad", "")
    text = text.replace("\u017d", "\u00ae")
    return text.strip(" -_,;:")


def _parse_pdf_date(value: object) -> str:
    text = _normalize_pdf_metadata_text(value)
    if not text:
        return ""
    match = PDF_DATE_RE.match(text)
    if match:
        year = match.group("year")
        month = match.group("month") or ""
        day = match.group("day") or ""
        if month and day:
            return f"{year}-{month}-{day}"
        if month:
            return f"{year}-{month}"
        return year
    year_match = YEAR_RE.search(text)
    return year_match.group(0) if year_match else ""


def _looks_like_pdf_technical_value(value: str) -> bool:
    normalized = _normalize_pdf_metadata_text(value)
    if not normalized:
        return True
    lowered = normalized.lower()
    return any(marker in lowered for marker in PDF_TECHNICAL_METADATA_MARKERS)


def _pdf_title_is_weak(value: str | None, *, file_stem: str) -> bool:
    normalized = _normalize_pdf_metadata_text(value)
    if not normalized:
        return True
    lowered = normalized.lower()
    if lowered in {"untitled", "untitled document", "document", "converted document"}:
        return True
    if lowered == _normalize_pdf_metadata_text(file_stem).lower():
        return True
    if re.fullmatch(r"[0-9a-f]{12,}", normalized, flags=re.IGNORECASE):
        return True
    return _looks_like_pdf_technical_value(normalized)


def _pdf_author_is_weak(value: str | None) -> bool:
    normalized = _normalize_pdf_metadata_text(value)
    if not normalized:
        return True
    lowered = normalized.lower()
    if lowered in {"unknown", "author", "creator"}:
        return True
    return _looks_like_pdf_technical_value(normalized)


def _collect_pdf_text_lines(doc: Any, *, max_pages: int = 6) -> list[str]:
    lines: list[str] = []
    for page_num in range(min(max_pages, len(doc))):
        try:
            text = doc[page_num].get_text("text") or ""
        except Exception:
            continue
        for line in text.splitlines():
            normalized = _collapse_letter_spaced_pdf_line(line)
            if normalized:
                lines.append(normalized)
    return lines


def _first_page_line_candidates(doc: Any) -> list[dict[str, object]]:
    if len(doc) == 0:
        return []
    page = doc[0]
    candidates: list[dict[str, object]] = []
    try:
        data = page.get_text("dict")
    except Exception:
        return []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = _collapse_letter_spaced_pdf_line(" ".join(span.get("text", "") for span in spans))
            if not text:
                continue
            size = max(float(span.get("size", 0) or 0) for span in spans) if spans else 0.0
            bbox = line.get("bbox", (0, 0, 0, 0))
            candidates.append({"text": text, "size": size, "y": float(bbox[1] or 0)})
    return sorted(candidates, key=lambda item: (float(item["y"]), -float(item["size"])))


def _looks_like_cover_title_noise(text: str) -> bool:
    normalized = _normalize_pdf_metadata_text(text)
    if not normalized:
        return True
    if PDF_TITLE_NOISE_RE.search(normalized):
        return True
    if YEAR_RE.fullmatch(normalized):
        return True
    if re.fullmatch(r"[A-Z]{2,6}", normalized):
        return True
    return False


def _infer_pdf_title_from_cover(doc: Any, *, file_stem: str) -> str:
    candidates = [
        item
        for item in _first_page_line_candidates(doc)
        if not _looks_like_cover_title_noise(str(item.get("text", "")))
    ]
    if not candidates:
        lines = [
            line
            for line in _collect_pdf_text_lines(doc, max_pages=1)[:12]
            if not _looks_like_cover_title_noise(line)
        ]
        return _clean_pdf_metadata_value(" ".join(lines[:2]))

    max_size = max(float(item["size"]) for item in candidates)
    min_title_size = max(12.0, max_size * 0.62)
    chosen: list[str] = []
    for item in candidates:
        text = str(item["text"])
        size = float(item["size"])
        if size >= min_title_size:
            chosen.append(text)
        elif chosen and re.search(r"(?i)\b(?:version|edition|wydanie)\b", text):
            chosen.append(text)
        if len(chosen) >= 4 or len(" ".join(chosen)) > 180:
            break
    title = _clean_pdf_metadata_value(" ".join(chosen))
    if _pdf_title_is_weak(title, file_stem=file_stem):
        return ""
    return title


def _clean_publisher_candidate(value: str) -> str:
    text = _clean_pdf_metadata_value(value)
    text = re.split(r"(?i)\b(?:all rights reserved|no part of|printed in|isbn|issn)\b", text)[0]
    text = re.sub(r"(?i)^(?:by|the publisher|wydawca)\s+", "", text).strip(" .;:-")
    text = re.sub(r"^(?:\d{4}\s*[,;/&-]?\s*)+", "", text).strip(" .;:-")
    if not text or len(text) < 3:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in ("copyright", "all rights", "www.", "http://", "https://")):
        return ""
    if len(text.split()) > 14:
        return ""
    return text


def _infer_publisher_from_lines(lines: list[str]) -> str:
    patterns = (
        r"(?i)\bpublished\s+by\s+(?P<name>.+)",
        r"(?i)\bpublisher\s*[:\-]\s*(?P<name>.+)",
        r"(?i)\bwydawca\s*[:\-]\s*(?P<name>.+)",
        r"(?i)\bcopyright\s*(?:\(\s*c\s*\)|\u00a9)?\s*(?:\d{4}(?:\s*[-,]\s*\d{4})?\s*)?(?:by\s+)?(?P<name>.+)",
        r"(?i)(?:\u00a9|\(\s*c\s*\))\s*(?:\d{4}(?:\s*[-,]\s*\d{4})?\s*)?(?P<name>.+)",
    )
    for line in lines:
        for pattern in patterns:
            match = re.search(pattern, line)
            if not match:
                continue
            publisher = _clean_publisher_candidate(match.group("name"))
            if publisher:
                return publisher
    return ""


def _infer_subjects_from_publication_text(*, title: str, lines: list[str], publisher: str, raw_subject: str = "") -> list[str]:
    subjects: list[str] = []
    if raw_subject:
        subjects.append(raw_subject)
    corpus = " ".join([title, publisher, *lines[:20]]).lower()
    if "business analysis" in corpus:
        subjects.append("Business Analysis")
    if "requirements" in corpus or "business analysis body of knowledge" in corpus:
        subjects.append("Requirements Management")
    if "body of knowledge" in corpus:
        subjects.append("Business Analysis Body of Knowledge")
    acronym = "".join(word[0] for word in re.findall(r"\b[A-Z][A-Za-z]+\b", publisher))
    if acronym and len(acronym) <= 8:
        subjects.append(acronym)
    deduped: list[str] = []
    seen: set[str] = set()
    for subject in subjects:
        cleaned = _clean_pdf_metadata_value(subject)
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            deduped.append(cleaned)
    return deduped


def _publisher_expands_author_acronym(author: str, publisher: str) -> bool:
    author_key = re.sub(r"[^A-Za-z]", "", author or "").upper()
    if not author_key or len(author_key) > 8 or author_key != author_key.upper():
        return False
    publisher_acronym = "".join(
        word[0].upper()
        for word in re.findall(r"\b[A-Za-z]+\b", publisher or "")
        if word.lower() not in {"of", "the", "and", "for"}
    )
    return bool(publisher_acronym and publisher_acronym == author_key)


def _extract_cover_description(lines: list[str]) -> tuple[str, str]:
    joined_cover = _clean_pdf_metadata_value(" ".join(lines[:12]))
    joined_cover = re.sub(r"\b[^\w\s]{1,2}\b", " ", joined_cover)
    joined_cover = _normalize_pdf_metadata_text(joined_cover)
    if re.search(r"(?i)\bguide to the business analysis body of knowledge\b", joined_cover):
        version = "3" if re.search(r"(?i)\bv\s*3\b|version\s+3", joined_cover) else ""
        return (
            "A Guide to the Business Analysis Body of Knowledge, Version 3."
            if version
            else "A Guide to the Business Analysis Body of Knowledge."
        ), "Version 3" if version else ""

    cover_lines = [
        line
        for line in lines[:12]
        if line
        and not PDF_TITLE_NOISE_RE.search(line)
        and not re.fullmatch(r"[A-Z]{2,6}", line)
        and not re.fullmatch(r"\W{1,3}", line)
    ]
    if not cover_lines:
        return "", ""

    title_lines: list[str] = []
    edition = ""
    for line in cover_lines:
        if re.search(r"(?i)\b(?:version|edition|wydanie)\b", line) and len(line) <= 80:
            edition = line
            continue
        if not title_lines and not re.search(r"(?i)\b(?:guide|handbook|manual|standard|body of knowledge)\b", line):
            continue
        title_lines.append(line)
        if len(title_lines) >= 3 or len(" ".join(title_lines)) > 180:
            break

    description = _clean_pdf_metadata_value(" ".join(title_lines))
    if not description:
        return "", edition
    if edition and edition.lower() not in description.lower():
        description = f"{description}, {edition}"
    return description, edition


def _infer_metadata_from_pdf_text(
    doc: Any,
    *,
    file_stem: str,
    subject_hint: str = "",
    preferred_title: str = "",
) -> dict[str, object]:
    lines = _collect_pdf_text_lines(doc)
    title = _infer_pdf_title_from_cover(doc, file_stem=file_stem)
    publisher = _infer_publisher_from_lines(lines)
    copyright_years: list[int] = []
    for line in lines:
        if re.search(r"(?i)(?:copyright|\u00a9|\(\s*c\s*\))", line):
            copyright_years.extend(int(match.group(0)) for match in YEAR_RE.finditer(line))
    date = str(max(copyright_years)) if copyright_years else ""
    if not date:
        for line in lines[:20]:
            match = YEAR_RE.search(line)
            if match:
                date = match.group(0)
                break

    edition = ""
    cover_description, cover_edition = _extract_cover_description(lines)
    for line in lines[:20]:
        if re.search(r"(?i)\b(?:version|edition|wydanie)\b", line) and len(line) <= 80:
            edition = line
            break
    if cover_edition:
        edition = cover_edition

    resolved_title = preferred_title if preferred_title and not _pdf_title_is_weak(preferred_title, file_stem=file_stem) else title
    subjects = _infer_subjects_from_publication_text(
        title=resolved_title,
        lines=lines,
        publisher=publisher,
        raw_subject=subject_hint,
    )

    description_parts = []
    if cover_description:
        description = cover_description
    else:
        if resolved_title:
            description_parts.append(resolved_title)
        if edition and edition.lower() not in " ".join(description_parts).lower():
            description_parts.append(edition)
        if publisher:
            description_parts.append(f"Published by {publisher}")
        if date:
            description_parts.append(date)
        description = ". ".join(description_parts)
    return {
        key: value
        for key, value in {
            "title": title,
            "author": publisher,
            "publisher": publisher,
            "subjects": subjects,
            "subject": "; ".join(subjects),
            "description": description,
            "date": date,
        }.items()
        if value
    }


def _extract_pdf_metadata(pdf_path: str) -> dict[str, object]:
    """Extract and infer publication metadata for use in EPUB metadata."""

    file_stem = Path(pdf_path).stem
    try:
        doc = fitz.open(pdf_path)
        try:
            metadata = doc.metadata or {}
            creation_date = metadata.get("creationDate", "")
            modification_date = metadata.get("modDate", "")
            raw_title = _clean_pdf_metadata_value(metadata.get("title", ""))
            raw_author = _clean_pdf_metadata_value(metadata.get("author", ""))
            raw_subject = _clean_pdf_metadata_value(metadata.get("subject", ""))
            raw_keywords = _clean_pdf_metadata_value(metadata.get("keywords", ""))
            raw_creator = _clean_pdf_metadata_value(metadata.get("creator", ""))
            raw_producer = _clean_pdf_metadata_value(metadata.get("producer", ""))
            inferred = _infer_metadata_from_pdf_text(
                doc,
                file_stem=file_stem,
                subject_hint=raw_subject or raw_keywords,
                preferred_title=raw_title,
            )
        finally:
            doc.close()

        title = raw_title if not _pdf_title_is_weak(raw_title, file_stem=file_stem) else inferred.get("title", "") or file_stem
        publisher = str(inferred.get("publisher", "") or "")
        if raw_author and publisher and _publisher_expands_author_acronym(raw_author, publisher):
            author = publisher
        else:
            author = raw_author if not _pdf_author_is_weak(raw_author) else str(inferred.get("author", "") or "") or "Unknown"
        subjects = list(inferred.get("subjects", []) or [])
        subject = raw_subject or raw_keywords or str(inferred.get("subject", "") or "")
        description = str(inferred.get("description", "") or "")
        publication_date = inferred.get("date") or _parse_pdf_date(creation_date) or _parse_pdf_date(modification_date)

        return {
            "title": title,
            "author": author,
            "creator": raw_creator,
            "publisher": publisher,
            "description": description,
            "subject": subject,
            "subjects": subjects,
            "keywords": raw_keywords,
            "producer": raw_producer,
            "creation_date": creation_date,
            "modification_date": modification_date,
            "date": publication_date,
        }
    except Exception as error:
        print(f"Warning: Could not extract PDF metadata: {error}")
        return {
            "title": file_stem,
            "author": "Unknown",
            "creator": "",
            "publisher": "",
            "description": "",
            "subject": "",
            "keywords": "",
            "producer": "",
            "creation_date": "",
            "modification_date": "",
            "date": "",
        }


def _metadata_title_is_weak(value: str | None, *, original_filename: str) -> bool:
    normalized = re.sub(r"\s+", " ", (value or "").strip())
    if not normalized:
        return True
    lowered = normalized.lower()
    file_stem = Path(original_filename).stem.lower().replace("_", "-")
    title_key = lowered.replace("_", "-")
    if lowered in {"untitled", "document", "converted document"}:
        return True
    if title_key == file_stem:
        return True
    if re.fullmatch(r"[0-9a-f]{12,}", normalized, flags=re.IGNORECASE):
        return True
    return False


def _metadata_author_is_weak(value: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", (value or "").strip())
    if not normalized:
        return True
    lowered = normalized.lower()
    if lowered in {"unknown", "author", "creator"}:
        return True
    return bool(re.search(r"(?i)\b(?:redaktor|z-ca|zast[eę]pca|koordynator|editor)\b", normalized))
