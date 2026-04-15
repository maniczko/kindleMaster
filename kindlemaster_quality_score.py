from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from bs4 import BeautifulSoup

from kindlemaster_image_layout_audit import analyze_image_layout
from kindlemaster_manifest import get_publication
from kindlemaster_release_gate import PAGE_LABEL_RE, ROLE_RE, SPECIAL_SECTION_RE, is_suspicious_nav_label, run_checks
from kindlemaster_text_audit import audit_epub_text


TITLE_PAGE_NAMES = {"title.xhtml"}
CSS_BODY_LINE_HEIGHT_RE = re.compile(r"body\s*\{[^}]*line-height:\s*([0-9.]+)", re.IGNORECASE | re.DOTALL)
CSS_HEADING_SIZE_RE = re.compile(r"(h[123])\s*\{\s*[^}]*font-size:\s*([0-9.]+)em", re.IGNORECASE | re.DOTALL)
GENERIC_FRONT_MATTER_RE = re.compile(
    r"(?:contents|table of contents|foreword|introduction|preface|acknowledg|editorial|about the author|biograph|appendix|index|notes)\b",
    re.IGNORECASE,
)
TOC_STYLE_LINE_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*)?\s*[^\n]{3,}(?:\.{2,}|\s)\d{1,4}$",
    re.IGNORECASE,
)
FRONT_MATTER_CLASSES = {"section-banner", "organizational-line"}
SPECIAL_PROFILES = {"front_matter", "toc", "back_matter", "promo"}


def _normalize_epub_href(base_name: str, href: str) -> str:
    base = PurePosixPath(base_name)
    target = (base.parent / href.split("#", 1)[0]).as_posix()
    parts: list[str] = []
    for part in target.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _ordered_xhtml_names(zf: zipfile.ZipFile) -> list[str]:
    return sorted(
        name
        for name in zf.namelist()
        if name.endswith(".xhtml") and not name.endswith("nav.xhtml")
    )


def _digit_ratio(text: str) -> float:
    cleaned = "".join(ch for ch in text if ch.isalnum())
    if not cleaned:
        return 0.0
    return sum(1 for ch in cleaned if ch.isdigit()) / len(cleaned)


def _normalize_texts(tags: list) -> list[str]:
    return [" ".join(tag.get_text(" ", strip=True).split()) for tag in tags if " ".join(tag.get_text(" ", strip=True).split())]


def _is_front_matter_hint_text(text: str) -> bool:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return False
    return bool(
        PAGE_LABEL_RE.match(cleaned)
        or ROLE_RE.search(cleaned)
        or SPECIAL_SECTION_RE.search(cleaned)
        or GENERIC_FRONT_MATTER_RE.search(cleaned)
    )


def _is_toc_like_text(text: str) -> bool:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return False
    if TOC_STYLE_LINE_RE.match(cleaned):
        return True
    if cleaned.count(".") >= 6 and cleaned.rstrip().rsplit(" ", 1)[-1].isdigit():
        return True
    return False


def _is_article_like_heading(text: str) -> bool:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return False
    if _is_front_matter_hint_text(cleaned):
        return False
    if cleaned[:1].islower():
        return False
    if cleaned.startswith(("by ", "-", "\u2013", "\u2014")):
        return False
    if len(cleaned.split()) == 1 and len(cleaned) < 10:
        return False
    if is_suspicious_nav_label(cleaned):
        return False
    return True


def _extract_explicit_profile(soup: BeautifulSoup) -> str | None:
    for tag in filter(None, [soup.find("body"), soup.find("section")]):
        data_profile = (tag.get("data-km-profile") or "").strip()
        if data_profile:
            return data_profile
        classes = tag.get("class") or []
        for class_name in classes:
            if isinstance(class_name, str) and class_name.startswith("km-profile-"):
                return class_name.removeprefix("km-profile-").replace("-", "_")
        epub_type = " ".join(tag.get("epub:type", "").split()).lower()
        if " toc" in f" {epub_type} " or epub_type.endswith(" toc"):
            return "toc"
        if "frontmatter" in epub_type:
            return "front_matter"
        if "backmatter" in epub_type:
            return "back_matter"
        if "bodymatter" in epub_type:
            return "article"
    return None


def _summarize_front_matter_file(name: str, soup: BeautifulSoup) -> dict[str, object]:
    headings = [
        (tag.name, " ".join(tag.get_text(" ", strip=True).split()))
        for tag in soup.find_all(["h1", "h2", "h3"])
        if " ".join(tag.get_text(" ", strip=True).split())
    ]
    paragraphs = _normalize_texts(soup.find_all("p"))
    classes = {
        cls
        for tag in soup.find_all(class_=True)
        for cls in (tag.get("class") if isinstance(tag.get("class"), list) else [tag.get("class")])
        if cls
    }
    texts = [text for _, text in headings] + paragraphs
    figure_count = len(soup.find_all("figure"))
    figure_only = figure_count > 0 and not texts
    front_hint_hits = sum(1 for text in texts if _is_front_matter_hint_text(text))
    toc_hits = sum(1 for text in texts if _is_toc_like_text(text))
    article_heading_hits = sum(1 for _, text in headings if _is_article_like_heading(text))
    long_prose_hits = sum(
        1
        for text in paragraphs
        if len(text) >= 80 and not _is_front_matter_hint_text(text) and not _is_toc_like_text(text)
    )
    numeric_dense_hits = sum(1 for text in paragraphs if len(text) >= 40 and _digit_ratio(text) >= 0.16)
    front_like = bool(classes & FRONT_MATTER_CLASSES) or front_hint_hits > 0 or toc_hits > 0
    continuation_like = long_prose_hits > 0 and numeric_dense_hits == 0 and article_heading_hits == 0 and not figure_only
    strong_content = article_heading_hits > 0 or len(headings) >= 2 or numeric_dense_hits > 0
    editorial_front_matter = front_like and long_prose_hits > 0 and numeric_dense_hits == 0 and len(headings) <= 1
    explicit_profile = _extract_explicit_profile(soup)
    return {
        "name": name,
        "headings": headings,
        "paragraphs": paragraphs,
        "figure_count": figure_count,
        "classes": sorted(classes),
        "front_like": front_like,
        "continuation_like": continuation_like,
        "strong_content": strong_content,
        "figure_only": figure_only,
        "editorial_front_matter": editorial_front_matter,
        "explicit_profile": explicit_profile,
    }


def _leading_explicit_front_matter_names(file_summaries: list[dict[str, object]]) -> list[str]:
    front_matter_names: list[str] = []
    for summary in file_summaries:
        explicit_profile = summary.get("explicit_profile")
        if explicit_profile in SPECIAL_PROFILES:
            front_matter_names.append(str(summary["name"]))
            continue
        if explicit_profile == "article":
            break
        break
    return front_matter_names


def _leading_front_matter_names(file_summaries: list[dict[str, object]]) -> list[str]:
    pending_cover_names: list[str] = []
    front_matter_names: list[str] = []
    front_matter_started = False
    continuation_pages = 0

    for summary in file_summaries:
        name = str(summary["name"])
        if not front_matter_started:
            if summary["figure_only"]:
                pending_cover_names.append(name)
                continue
            if summary["front_like"]:
                front_matter_started = True
                front_matter_names.extend(pending_cover_names)
                pending_cover_names.clear()
                front_matter_names.append(name)
                continuation_pages = 0
                continue
            front_matter_names.extend(pending_cover_names)
            break

        if summary["front_like"]:
            front_matter_names.append(name)
            continuation_pages = 0
            continue
        if summary["figure_only"] or summary["strong_content"]:
            break
        if summary["continuation_like"] and continuation_pages < 12:
            front_matter_names.append(name)
            continuation_pages += 1
            continue
        break

    return front_matter_names


def analyze_front_matter(epub_path: Path) -> dict[str, object]:
    with zipfile.ZipFile(epub_path) as zf:
        xhtml_names = _ordered_xhtml_names(zf)
        non_title_names = [name for name in xhtml_names if Path(name).name.lower() not in TITLE_PAGE_NAMES]
        file_summaries: list[dict[str, object]] = []
        for name in non_title_names:
            soup = BeautifulSoup(zf.read(name), "xml")
            file_summaries.append(_summarize_front_matter_file(name, soup))

        explicit_front_matter_names = _leading_explicit_front_matter_names(file_summaries)
        if explicit_front_matter_names:
            front_matter_names = explicit_front_matter_names
            detection_mode = "explicit_semantic_profile_markup"
        else:
            front_matter_names = _leading_front_matter_names(file_summaries)
            detection_mode = "leading_front_hint_then_content_boundary"
        first_article_index = len(front_matter_names)
        headings_by_file = {str(summary["name"]): list(summary["headings"]) for summary in file_summaries}
        summaries_by_file = {str(summary["name"]): summary for summary in file_summaries}
        article_heading_leaks = 0
        heading_noise_count = 0
        organizational_heading_count = 0
        for name in front_matter_names:
            summary = summaries_by_file.get(name) or {}
            editorial_front_matter = bool(summary.get("editorial_front_matter"))
            for level, text in headings_by_file.get(name, []):
                if level in {"h1", "h2"} and _is_article_like_heading(text) and not editorial_front_matter:
                    article_heading_leaks += 1
                if _is_front_matter_hint_text(text) or text.lower().startswith("by "):
                    organizational_heading_count += 1
                elif (PAGE_LABEL_RE.match(text) or is_suspicious_nav_label(text)) and not editorial_front_matter:
                    heading_noise_count += 1
                elif len(text.split()) <= 3 and not _is_article_like_heading(text) and not editorial_front_matter:
                    heading_noise_count += 1

        nav_targets = []
        if "EPUB/nav.xhtml" in zf.namelist():
            nav_soup = BeautifulSoup(zf.read("EPUB/nav.xhtml"), "xml")
            for anchor in nav_soup.find_all("a", href=True):
                nav_targets.append(_normalize_epub_href("EPUB/nav.xhtml", anchor["href"]))

        front_matter_target_set = set(front_matter_names)
        nav_pollution_count = sum(
            1
            for target in nav_targets
            if target in front_matter_target_set and not bool((summaries_by_file.get(target) or {}).get("editorial_front_matter"))
        )
        distinctness_pass = nav_pollution_count == 0 and article_heading_leaks == 0 and heading_noise_count <= 1
        return {
            "front_matter_files": front_matter_names,
            "front_matter_file_count": len(front_matter_names),
            "first_article_index": first_article_index,
            "first_content_file": non_title_names[first_article_index] if first_article_index < len(non_title_names) else None,
            "detection_mode": detection_mode,
            "editorial_front_matter_file_count": sum(
                1 for name in front_matter_names if bool((summaries_by_file.get(name) or {}).get("editorial_front_matter"))
            ),
            "article_heading_leaks": article_heading_leaks,
            "heading_noise_count": heading_noise_count,
            "organizational_heading_count": organizational_heading_count,
            "nav_pollution_count": nav_pollution_count,
            "distinctness_pass": distinctness_pass,
        }


def analyze_typography_ux(epub_path: Path) -> dict[str, object]:
    with zipfile.ZipFile(epub_path) as zf:
        css_names = sorted(name for name in zf.namelist() if name.endswith(".css"))
        css_text = "\n".join(zf.read(name).decode("utf-8", errors="replace") for name in css_names)
        line_height_match = CSS_BODY_LINE_HEIGHT_RE.search(css_text)
        line_height = float(line_height_match.group(1)) if line_height_match else 0.0
        heading_sizes = {match.group(1).lower(): float(match.group(2)) for match in CSS_HEADING_SIZE_RE.finditer(css_text)}
        styled_classes = {
            "byline": ".byline" in css_text,
            "lead": ".lead" in css_text,
            "organizational_line": ".organizational-line" in css_text,
            "section_banner": ".section-banner" in css_text,
            "figcaption": "figcaption" in css_text,
            "page_marker": ".page-marker" in css_text,
        }
        page_marker_hidden = ".page-marker" in css_text and "display: none" in css_text

        class_instances = {"byline": 0, "lead": 0, "organizational-line": 0, "section-banner": 0}
        for name in _ordered_xhtml_names(zf):
            soup = BeautifulSoup(zf.read(name), "xml")
            for class_name in list(class_instances):
                class_instances[class_name] += len(soup.find_all(class_=class_name))

        heading_hierarchy_pass = (
            heading_sizes.get("h1", 0) > heading_sizes.get("h2", 0) > heading_sizes.get("h3", 0) > 0
        )
        distinction_pass = styled_classes["byline"] and styled_classes["lead"] and (
            class_instances["byline"] > 0 or class_instances["lead"] > 0
        )
        ux_pass = (
            line_height >= 1.4
            and heading_hierarchy_pass
            and styled_classes["organizational_line"]
            and styled_classes["section_banner"]
            and styled_classes["figcaption"]
            and page_marker_hidden
        )
        return {
            "css_files": css_names,
            "body_line_height": line_height,
            "heading_sizes": heading_sizes,
            "styled_classes": styled_classes,
            "class_instances": class_instances,
            "heading_hierarchy_pass": heading_hierarchy_pass,
            "title_author_lead_distinction_pass": distinction_pass,
            "page_marker_hidden": page_marker_hidden,
            "ux_pass": ux_pass,
        }


def _threshold_score(actual: int, *, target: int, hard_limit: int) -> float:
    if actual <= target:
        return 10.0
    if actual >= hard_limit:
        return 0.0
    span = max(1, hard_limit - target)
    return max(0.0, 10.0 * (1.0 - ((actual - target) / span)))


def _append_unique(target: list[str], value: str) -> None:
    if value not in target:
        target.append(value)


def _premium_verdict(weighted_score: float, *, hard_failures: bool) -> str:
    if hard_failures:
        return "NOT_PREMIUM"
    if weighted_score >= 9.4:
        return "PREMIUM_STRONG"
    if weighted_score >= 8.8:
        return "PREMIUM_PASS"
    if weighted_score >= 7.5:
        return "NEAR_PREMIUM"
    return "NEEDS_WORK"


def _build_premium_report(
    *,
    weighted_score: float,
    smoke: dict[str, object],
    front_matter: dict[str, object],
    typography: dict[str, object],
    release_required: bool,
    split_count: int,
    joined_count: int,
    boundary_count: int,
    image_layout: dict[str, object],
) -> dict[str, object]:
    checks = smoke["checks"]
    counts = smoke["counts"]
    good: list[str] = []
    bad: list[str] = []

    if checks["valid_nav_paths"] and checks["valid_ncx_paths"] and checks["valid_anchors"]:
        _append_unique(good, "Nawigacja i anchory są technicznie poprawne.")
    else:
        _append_unique(bad, "Nawigacja ma błędne ścieżki albo martwe anchory.")

    if checks["no_duplicate_low_value_entries"] and checks["no_page_label_dominance"] and checks["no_truncated_or_dangling_nav_labels"]:
        _append_unique(good, "Zakładki reprezentują realne sekcje i nie są zaśmiecone niskowartościowymi wpisami.")
    else:
        if not checks["no_duplicate_low_value_entries"]:
            _append_unique(bad, "Zakładki zawierają duplikaty lub niskowartościowy szum.")
        if not checks["no_page_label_dominance"]:
            _append_unique(bad, "Zakładki są zdominowane przez etykiety stron zamiast tytułów sekcji.")
        if not checks["no_truncated_or_dangling_nav_labels"]:
            _append_unique(bad, "Zakładki są skrócone albo nie reprezentują pełnych tytułów.")

    if counts.get("flattened_inline_list_count", 0) == 0:
        _append_unique(good, "Listy punktowane i numerowane nie pozostają spłaszczone w akapitach.")
    else:
        _append_unique(
            bad,
            f"W EPUB nadal są spłaszczone listy inline do rozbicia na osobne elementy: {counts['flattened_inline_list_count']}.",
        )

    if split_count <= 1 and joined_count <= 10 and boundary_count <= 10:
        _append_unique(good, "Artefakty PDF w tekście są w granicach premium.")
    else:
        if split_count > 1:
            _append_unique(bad, f"Pozostało zbyt wiele split-word artefaktów: {split_count}.")
        if joined_count > 10:
            _append_unique(bad, f"Pozostało zbyt wiele joined-word artefaktów: {joined_count}.")
        if boundary_count > 10:
            _append_unique(bad, f"Pozostało zbyt wiele uszkodzonych granic zdań lub akapitów: {boundary_count}.")

    if checks["valid_h1_presence_where_applicable"] and checks["no_title_author_lead_page_merge"] and checks["special_sections_not_articles"]:
        _append_unique(good, "Struktura nagłówków i otwarć sekcji jest czytelniczo spójna.")
    else:
        if not checks["valid_h1_presence_where_applicable"]:
            _append_unique(bad, "Brakuje poprawnych głównych nagłówków tam, gdzie powinny istnieć.")
        if not checks["no_title_author_lead_page_merge"]:
            _append_unique(bad, "Tytuły nadal mieszają się z autorem, leadem albo numerem strony.")
        if not checks["special_sections_not_articles"]:
            _append_unique(bad, "Sekcje specjalne nadal udają zwykłe artykuły.")

    if front_matter["distinctness_pass"]:
        _append_unique(good, "Front matter jest odseparowany od głównego przepływu czytania.")
    else:
        _append_unique(bad, "Front matter nadal zanieczyszcza główny przepływ lub nawigację.")

    if typography["ux_pass"] and typography["title_author_lead_distinction_pass"]:
        _append_unique(good, "Typografia i hierarchia wizualna wspierają komfort czytania na Kindle.")
    else:
        if not typography["ux_pass"]:
            _append_unique(bad, "Warstwa CSS i rytm czytania nie spełniają jeszcze pełnego baseline UX.")
        if not typography["title_author_lead_distinction_pass"]:
            _append_unique(bad, "Tytuł, autor i lead nie są jeszcze wystarczająco rozróżnione wizualnie.")

    if checks["no_opaque_or_slug_human_title"] and (checks["creator_not_unknown_for_release"] or not release_required):
        _append_unique(good, "Metadane są czytelne dla użytkownika i nie eksponują technicznych slugów.")
    else:
        if not checks["no_opaque_or_slug_human_title"]:
            _append_unique(bad, "Metadane nadal eksponują techniczny slug albo nieczytelny tytuł.")
        if release_required and not checks["creator_not_unknown_for_release"]:
            _append_unique(bad, "Tryb release nadal ma niepoprawne pole autora lub Unknown creator.")

    if image_layout["image_layout_pass"]:
        total_fallback_count = int(image_layout["hybrid_illustrated_file_count"]) + int(image_layout["image_fallback_file_count"])
        if total_fallback_count == 0:
            _append_unique(good, "Publikacja jest w pelni text-first i nie uzywa page-image fallbacku.")
        else:
            _append_unique(
                good,
                f"Fallback obrazkowy pozostaje ograniczony do uzasadnionych sekcji i nie przecieka do zwyklych stron artykulowych ({total_fallback_count} plikow).",
            )
    else:
        _append_unique(
            bad,
            (
                "Sekcje image-backed lub page-like nadal trafiaja do zwyklych stron artykulowych albo zbyt mocno dominuja glowny przeplyw czytania. "
                f"Nieuzasadniony fallback: {image_layout['unjustified_fallback_count']}."
            ),
        )

    blocking_failures = [
        check
        for check in smoke["failed_checks"]
        if release_required or check != "creator_not_unknown_for_release"
    ]
    hard_failures = bool(blocking_failures)
    if not good:
        _append_unique(good, "Brak mocnych stron, które można jeszcze uznać za stabilne premium.")
    if not bad:
        _append_unique(bad, "Brak istotnych słabych punktów w tym przebiegu.")
    verdict = _premium_verdict(weighted_score, hard_failures=hard_failures)
    return {
        "score_1_10": round(weighted_score, 2),
        "premium_target": 8.8,
        "premium_gap": round(max(0.0, 8.8 - weighted_score), 3),
        "verdict": verdict,
        "what_is_good": good,
        "what_is_bad": bad,
        "top_strengths": good[:3],
        "top_risks": bad[:3],
        "summary": (
            "Publikacja spełnia wymagania premium Kindle."
            if verdict in {"PREMIUM_STRONG", "PREMIUM_PASS"}
            else "Publikacja nie osiąga jeszcze stabilnego poziomu premium Kindle."
        ),
    }


def score_epub(epub_path: Path, *, publication_id: str | None = None) -> dict[str, object]:
    publication = get_publication(publication_id) if publication_id else None
    release_required = bool((publication or {}).get("status", {}).get("release_eligible"))
    smoke = run_checks(epub_path)
    audit = audit_epub_text(epub_path)
    front_matter = analyze_front_matter(epub_path)
    typography = analyze_typography_ux(epub_path)
    image_layout = analyze_image_layout(epub_path)

    split_count = audit["split_word_scan"]["matches_total"]
    joined_count = audit["joined_word_scan"]["matches_total"]
    boundary_count = audit["boundary_scan"]["matches_total"]

    semantics_score = 0.0
    semantics_score += 2.5 if smoke["counts"]["h1_count"] > 0 else 0.0
    semantics_score += 2.0 if smoke["checks"]["no_title_author_lead_page_merge"] else 0.0
    semantics_score += 2.0 if smoke["checks"]["special_sections_not_articles"] else 0.0
    semantics_score += 1.5 if front_matter["article_heading_leaks"] == 0 else 0.0
    semantics_score += 1.0 if smoke["counts"]["h1_count"] >= 5 else 0.0
    semantics_score += 1.0 if front_matter["heading_noise_count"] <= 1 else 0.0

    navigation_score = 0.0
    navigation_score += 2.5 if smoke["checks"]["valid_nav_paths"] else 0.0
    navigation_score += 2.5 if smoke["checks"]["valid_ncx_paths"] else 0.0
    navigation_score += 2.0 if smoke["checks"]["no_duplicate_low_value_entries"] else 0.0
    navigation_score += 1.5 if smoke["checks"]["no_page_label_dominance"] and smoke["checks"]["no_front_matter_toc_pollution"] else 0.0
    navigation_score += 1.5 if smoke["checks"].get("no_truncated_or_dangling_nav_labels") else 0.0

    text_score = (
        _threshold_score(split_count, target=1, hard_limit=6) * 0.35
        + _threshold_score(joined_count, target=10, hard_limit=40) * 0.35
        + _threshold_score(boundary_count, target=10, hard_limit=60) * 0.30
    )

    front_matter_score = 0.0
    front_matter_score += 4.0 if front_matter["nav_pollution_count"] == 0 else 0.0
    front_matter_score += 3.0 if front_matter["article_heading_leaks"] == 0 else 0.0
    front_matter_score += 3.0 if front_matter["heading_noise_count"] <= 1 else 0.0

    ux_score = 0.0
    ux_score += 2.5 if typography["body_line_height"] >= 1.4 else 0.0
    ux_score += 2.5 if typography["heading_hierarchy_pass"] else 0.0
    ux_score += 2.0 if typography["title_author_lead_distinction_pass"] else 0.0
    ux_score += 1.5 if typography["page_marker_hidden"] else 0.0
    ux_score += 1.5 if typography["styled_classes"]["figcaption"] and typography["styled_classes"]["section_banner"] else 0.0

    metadata_score = 0.0
    metadata_score += 5.0 if smoke["checks"]["no_opaque_or_slug_human_title"] else 0.0
    metadata_score += 5.0 if (smoke["checks"]["creator_not_unknown_for_release"] or not release_required) else 0.0

    total_fallback_ratio = (
        (image_layout["hybrid_illustrated_file_count"] + image_layout["image_fallback_file_count"])
        / max(1, image_layout["xhtml_file_count"])
    )
    image_layout_score = 0.0
    image_layout_score += 5.0 if image_layout["unjustified_fallback_count"] == 0 else 0.0
    image_layout_score += 3.0 if image_layout["nav_target_to_image_only_count"] == 0 and image_layout["nav_target_to_page_like_count"] == 0 else 0.0
    image_layout_score += 2.0 if total_fallback_ratio <= 0.15 else 0.0

    weighted_score = round(
        semantics_score * 0.25
        + navigation_score * 0.20
        + text_score * 0.20
        + front_matter_score * 0.10
        + ux_score * 0.10
        + metadata_score * 0.05
        + image_layout_score * 0.10,
        3,
    )
    premium_report = _build_premium_report(
        weighted_score=weighted_score,
        smoke=smoke,
        front_matter=front_matter,
        typography=typography,
        release_required=release_required,
        split_count=split_count,
        joined_count=joined_count,
        boundary_count=boundary_count,
        image_layout=image_layout,
    )

    return {
        "epub": str(epub_path),
        "publication_id": publication_id,
        "release_required": release_required,
        "smoke": smoke,
        "text_audit": {
            "split_word_count": split_count,
            "joined_word_count": joined_count,
            "boundary_count": boundary_count,
        },
        "front_matter": front_matter,
        "typography": typography,
        "image_layout": image_layout,
        "subscores": {
            "semantics": round(semantics_score, 3),
            "navigation": round(navigation_score, 3),
            "text": round(text_score, 3),
            "front_matter": round(front_matter_score, 3),
            "ux": round(ux_score, 3),
            "metadata": round(metadata_score, 3),
            "image_layout": round(image_layout_score, 3),
        },
        "text_first_pass": image_layout["unjustified_fallback_count"] == 0,
        "weighted_score": weighted_score,
        "premium_target": 8.8,
        "premium_gap": round(max(0.0, 8.8 - weighted_score), 3),
        "premium_report": premium_report,
    }


def compare_epub_quality(
    candidate_epub: Path,
    *,
    publication_id: str,
    accepted_epub: Path,
    reference_epub: Path,
    baseline_epub: Path,
) -> dict[str, object]:
    candidate = score_epub(candidate_epub, publication_id=publication_id)
    accepted = score_epub(accepted_epub, publication_id=publication_id)
    reference = score_epub(reference_epub, publication_id=publication_id)
    baseline = score_epub(baseline_epub, publication_id=publication_id)

    candidate_counts = candidate["smoke"]["counts"]
    accepted_counts = accepted["smoke"]["counts"]
    reference_counts = reference["smoke"]["counts"]

    hard_regressions: list[str] = []
    if candidate["smoke"]["failed_checks"]:
        hard_regressions.append("release_smoke_failure")
    if candidate_counts["special_section_toc_count"] > min(accepted_counts["special_section_toc_count"], reference_counts["special_section_toc_count"]):
        hard_regressions.append("special_section_toc_pollution_increase")
    if candidate_counts["page_label_toc_count"] > min(accepted_counts["page_label_toc_count"], reference_counts["page_label_toc_count"]):
        hard_regressions.append("page_label_dominance_increase")
    if candidate_counts.get("suspicious_nav_label_count", 0) > min(
        accepted_counts.get("suspicious_nav_label_count", 0),
        reference_counts.get("suspicious_nav_label_count", 0),
    ):
        hard_regressions.append("truncated_or_dangling_nav_label_increase")
    if candidate_counts["duplicate_low_value_entries"] > min(accepted_counts["duplicate_low_value_entries"], reference_counts["duplicate_low_value_entries"]):
        hard_regressions.append("toc_duplicate_increase")
    if candidate_counts["title_merge_count"] > min(accepted_counts["title_merge_count"], reference_counts["title_merge_count"]):
        hard_regressions.append("title_merge_regression")
    if candidate["front_matter"]["heading_noise_count"] > min(accepted["front_matter"]["heading_noise_count"], reference["front_matter"]["heading_noise_count"]):
        hard_regressions.append("front_matter_heading_noise_increase")
    if candidate["image_layout"]["nav_target_to_page_like_count"] > min(
        accepted["image_layout"]["nav_target_to_page_like_count"],
        reference["image_layout"]["nav_target_to_page_like_count"],
    ):
        hard_regressions.append("page_like_toc_pollution_increase")
    if candidate["image_layout"]["nav_target_to_image_only_count"] > min(
        accepted["image_layout"]["nav_target_to_image_only_count"],
        reference["image_layout"]["nav_target_to_image_only_count"],
    ):
        hard_regressions.append("image_only_toc_pollution_increase")
    if candidate["weighted_score"] < reference["weighted_score"]:
        hard_regressions.append("below_old_best_epub_quality")

    dual_baseline_delta = {
        "vs_accepted": round(candidate["weighted_score"] - accepted["weighted_score"], 3),
        "vs_reference": round(candidate["weighted_score"] - reference["weighted_score"], 3),
        "vs_pdf_baseline": round(candidate["weighted_score"] - baseline["weighted_score"], 3),
    }
    blocker_close = (
        len(candidate["smoke"]["failed_checks"]) < len(accepted["smoke"]["failed_checks"])
        or (
            candidate["front_matter"]["nav_pollution_count"] < accepted["front_matter"]["nav_pollution_count"]
            and candidate["smoke"]["counts"]["special_section_toc_count"] <= accepted["smoke"]["counts"]["special_section_toc_count"]
        )
        or (
            candidate["smoke"]["counts"]["title_merge_count"] < accepted["smoke"]["counts"]["title_merge_count"]
            and candidate["smoke"]["checks"]["no_title_author_lead_page_merge"]
        )
    )
    promotion_allowed = not hard_regressions and (dual_baseline_delta["vs_accepted"] >= 0.1 or blocker_close)
    return {
        "candidate": candidate,
        "accepted": accepted,
        "reference": reference,
        "baseline": baseline,
        "dual_baseline_delta": dual_baseline_delta,
        "blocker_close": blocker_close,
        "hard_regressions": hard_regressions,
        "promotion_allowed": promotion_allowed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score Kindle Master EPUB quality and compare against baselines")
    parser.add_argument("--epub", required=True, help="Candidate EPUB path")
    parser.add_argument("--publication-id", help="Publication manifest identifier")
    parser.add_argument("--accepted-epub", help="Accepted final EPUB path")
    parser.add_argument("--reference-epub", help="Best old EPUB path")
    parser.add_argument("--baseline-epub", help="Baseline EPUB path from PDF")
    args = parser.parse_args()

    candidate_path = Path(args.epub).resolve()
    if args.accepted_epub and args.reference_epub and args.baseline_epub and args.publication_id:
        report = compare_epub_quality(
            candidate_path,
            publication_id=args.publication_id,
            accepted_epub=Path(args.accepted_epub).resolve(),
            reference_epub=Path(args.reference_epub).resolve(),
            baseline_epub=Path(args.baseline_epub).resolve(),
        )
    else:
        report = score_epub(candidate_path, publication_id=args.publication_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
