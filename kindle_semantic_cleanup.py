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
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from lxml import etree
from kindlemaster_structured_lists import detect_inline_ordered_list


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
PAGE_LABEL_RE = re.compile(r"^(?:Strona|Page|S\.)\s*\d{1,4}(?:\s*/\s*\d{1,4})?$", re.IGNORECASE)
TOC_PAGE_RE = re.compile(r"^spis\s+tre", re.IGNORECASE)
BIBLIOGRAPHY_RE = re.compile(r"^(?:bibliografia|bibliography|references?)\b", re.IGNORECASE)
NUMBERED_SECTION_RE = re.compile(r"^\d+[.)]\s+")
PAGE_SPAN_RE = re.compile(r"\bS\.\s*\d{1,4}\b", re.IGNORECASE)
SOLUTION_PAGE_RE = re.compile(r"Solutions page \d+", re.IGNORECASE)
TRUE_SOLUTION_ENTRY_RE = re.compile(r"^(?P<num>\d+)\.\s+.+\s[–-]\s.+$")
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
LETTER_FRAGMENT_RE = re.compile(r"(?P<left>[^\W\d_]{2,})-\s+(?P<right>[^\W\d_]{2,})", re.UNICODE)
MOJIBAKE_MAP = {
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
    "printed",
    "published",
    "isbn",
    "issn",
    "quality chess",
    "www.",
    "fot.",
    "fotografie",
    "fotografia",
    "rys.",
    "źródło",
    "source:",
    "pic.",
    "@",
)
PERSON_ROLE_TERMS = (
    "redaktor",
    "redaktorka",
    "naczelna",
    "naczelny",
    "prowadzący",
    "prowadzaca",
    "zastępca",
    "zastepca",
    "dyrektor",
    "autorka",
    "autor",
)
SPECIAL_SECTION_TERMS = (
    "zespół",
    "redakc",
    "sponsor",
    "partner",
    "portal",
    "marketing",
    "organiz",
    "wydania",
    "recenzji",
    "wywiadu",
    "studenta",
    "temat numeru",
    "kontakt:",
    "projekt i sk",
    "strefa pmi pc",
    "strefa pmi w liczbach",
    "strefa na luzie",
    "członkost",
    "business acumen",
    "power skills",
    "ways of working",
)
PROMO_SECTION_TERMS = (
    "czÅ‚onkostwo",
    "czlonkostwo",
    "networking",
    "mentoring",
    "doÅ‚Ä…cz",
    "dolacz",
    "sponsor",
    "partner",
)

TOC_SECTION_TERMS = (
    "w numerze",
    "spis tresci",
    "spis treści",
    "contents",
)
PROMO_BANNER_TERMS = (
    "materiał sponsorowany",
    "material sponsorowany",
    "materiały prasowe",
    "materialy prasowe",
)
FRONT_MATTER_HINT_TERMS = (
    "od redaktora",
    "poznaj nasz zespol",
    "poznaj nasz zespół",
    "adres redakcji",
    "redaktor naczelny",
    "wydawca",
    "biuro reklamy",
    "prenumerata",
)
TRUNCATED_TITLE_END_WORDS = {"do", "ku", "od", "of", "the", "for", "with", "to", "in"}
GENERIC_NAV_HEADING_PREFIXES = {"w", "na", "po", "od", "ku"}

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
  line-height: 1.4;
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
  margin: 0;
  text-indent: 1.5em;
}

p.lead,
p.byline,
p.organizational-line,
p.section-banner,
p.toc-line,
p.back-matter-line {
  text-indent: 0;
}

p.lead {
  margin: 0.35em 0 0.7em;
  font-size: 1.03em;
  line-height: 1.52;
}

p.byline {
  margin: 0.1em 0 0.5em;
  font-style: italic;
  color: #333;
}

p.organizational-line,
p.back-matter-line {
  margin: 0.1em 0;
  font-size: 0.94em;
  color: #4b4b4b;
}

p.section-banner,
p.toc-line {
  margin: 0.75em 0 0.25em;
  font-size: 0.92em;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

h1 + p,
h2 + p,
h3 + p,
figcaption + p,
.solution-entry + p,
.problem-solution-link + p {
  text-indent: 0;
}

figure {
  margin: 1em 0 1.1em;
  text-align: center;
  break-inside: avoid;
  page-break-inside: avoid;
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

.chess-problem img {
  max-width: 19rem;
  image-rendering: -webkit-optimize-contrast;
  image-rendering: crisp-edges;
}

.exercise-number {
  color: #555;
  font-weight: 700;
}

.page-marker {
  display: none;
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
}
"""


@dataclass
class ProcessedChapter:
    xhtml: str
    nav_entries: list[dict]
    solution_targets: dict[str, str]
    chapter_profile: str = "article"


@dataclass
class SemanticRecoveryPlan:
    processed: dict[Path, ProcessedChapter]
    toc_entries: list[dict]
    solution_targets: dict[str, str]


@dataclass
class FinalizerStageReport:
    stage: str
    proofs: dict[str, object]
    acceptance_checks: list[str] = field(default_factory=list)
    accepted: bool = True
    notes: list[str] = field(default_factory=list)


@dataclass
class FinalizerWorkspace:
    root_dir: Path
    opf_path: Path
    chapter_paths: list[Path]
    artifact_root: Path | None = None
    css_path: Path | None = None
    repeated_counts: Counter = field(default_factory=Counter)
    keep_first_seen: set[str] = field(default_factory=set)
    processed: dict[Path, ProcessedChapter] = field(default_factory=dict)
    solution_targets: dict[str, str] = field(default_factory=dict)
    toc_entries: list[dict] = field(default_factory=list)
    semantic_plan: SemanticRecoveryPlan | None = None
    stage_artifacts: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class FinalizerStageDefinition:
    stage: str
    dependencies: tuple[str, ...]
    acceptance_checks: tuple[str, ...]


class FinalizerPipeline:
    def __init__(
        self,
        *,
        workspace: FinalizerWorkspace,
        source_epub_bytes: bytes,
        title: str,
        author: str,
        language: str,
        temp_dir_handle: tempfile.TemporaryDirectory[str],
    ) -> None:
        self.workspace = workspace
        self.source_epub_bytes = source_epub_bytes
        self.title = title
        self.author = author
        self.language = language
        self._temp_dir_handle = temp_dir_handle
        self.stage_reports: list[FinalizerStageReport] = []
        self.packaged_bytes: bytes | None = None

    @classmethod
    def from_epub_bytes(
        cls,
        epub_bytes: bytes,
        *,
        title: str,
        author: str,
        language: str,
        artifact_dir: Path | None = None,
    ) -> "FinalizerPipeline":
        temp_dir_handle: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        try:
            workspace = _create_finalizer_workspace(
                epub_bytes,
                Path(temp_dir_handle.name),
                artifact_root=artifact_dir,
            )
            return cls(
                workspace=workspace,
                source_epub_bytes=epub_bytes,
                title=title,
                author=author,
                language=language,
                temp_dir_handle=temp_dir_handle,
            )
        except Exception:
            temp_dir_handle.cleanup()
            raise

    def __enter__(self) -> "FinalizerPipeline":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._temp_dir_handle is not None:
            self._temp_dir_handle.cleanup()
            self._temp_dir_handle = None

    @property
    def completed_stage_names(self) -> list[str]:
        return [stage.stage for stage in self.stage_reports]

    def get_stage_report(self, stage_name: str) -> FinalizerStageReport | None:
        return next((stage for stage in self.stage_reports if stage.stage == stage_name), None)

    def run_stage(self, stage_name: str) -> FinalizerStageReport:
        existing = self.get_stage_report(stage_name)
        if existing is not None:
            return existing

        definition = FINALIZER_STAGE_DEFINITIONS.get(stage_name)
        if definition is None:
            raise KeyError(f"Unknown finalizer stage: {stage_name}")

        missing_dependencies = [
            dependency for dependency in definition.dependencies if dependency not in self.completed_stage_names
        ]
        if missing_dependencies:
            raise RuntimeError(
                f"Finalizer stage '{stage_name}' requires completed stages: {', '.join(missing_dependencies)}"
            )

        proofs = self._execute_stage(definition.stage)
        report = _build_stage_report(
            definition.stage,
            proofs,
            acceptance_checks=definition.acceptance_checks,
        )
        self.stage_reports.append(report)
        _persist_stage_artifacts(
            self.workspace,
            report,
            packed_bytes=self.packaged_bytes if definition.stage == "packaging" else None,
        )
        if not report.accepted:
            failed_checks = [
                check for check in report.acceptance_checks if not bool(report.proofs.get(check))
            ]
            raise RuntimeError(
                f"Finalizer stage '{stage_name}' failed acceptance boundary: {', '.join(failed_checks)}"
            )
        return report

    def run_all(self) -> tuple[bytes, dict[str, object]]:
        self.run_stage("extract")
        if _is_pre_paginated(self.workspace.opf_path):
            passthrough = _build_stage_report(
                "pre_paginated_passthrough",
                {"pre_paginated": True},
                acceptance_checks=("pre_paginated",),
                notes=["Cleanup skipped because the EPUB is pre-paginated."],
            )
            self.stage_reports.append(passthrough)
            _persist_stage_artifacts(self.workspace, passthrough)
            return self.source_epub_bytes, _serialize_finalizer_report(
                self.stage_reports,
                artifact_manifest=_build_stage_artifact_manifest(self.workspace),
            )

        if not self.workspace.chapter_paths:
            spine_scan = _build_stage_report(
                "spine_scan",
                {"chapter_count_scanned": True, "chapter_count": 0},
                acceptance_checks=("chapter_count_scanned",),
                notes=["Cleanup skipped because the EPUB spine exposes no XHTML chapters."],
            )
            self.stage_reports.append(spine_scan)
            _persist_stage_artifacts(self.workspace, spine_scan)
            return self.source_epub_bytes, _serialize_finalizer_report(
                self.stage_reports,
                artifact_manifest=_build_stage_artifact_manifest(self.workspace),
            )

        for stage_name in EXPECTED_FINALIZER_STAGE_SEQUENCE[1:]:
            self.run_stage(stage_name)

        if self.packaged_bytes is None:
            raise RuntimeError("Packaging stage completed without emitting EPUB bytes.")
        return self.packaged_bytes, _serialize_finalizer_report(
            self.stage_reports,
            artifact_manifest=_build_stage_artifact_manifest(self.workspace),
        )

    def _execute_stage(self, stage_name: str) -> dict[str, object]:
        if stage_name == "extract":
            return _stage_extract_proof(self.workspace.root_dir, self.workspace.opf_path)
        if stage_name == "css_normalization":
            return _run_css_normalization_stage(self.workspace)
        if stage_name == "semantic_planning":
            return _run_semantic_planning_stage(
                self.workspace,
                title=self.title,
                author=self.author,
                language=self.language,
            )
        if stage_name == "semantic_apply":
            return _run_semantic_apply_stage(
                self.workspace,
                title=self.title,
                author=self.author,
                language=self.language,
            )
        if stage_name == "navigation_rebuild":
            return _run_navigation_rebuild_stage(
                self.workspace,
                title=self.title,
                language=self.language,
            )
        if stage_name == "metadata_normalization":
            return _run_metadata_normalization_stage(
                self.workspace,
                title=self.title,
                author=self.author,
                language=self.language,
            )
        if stage_name == "packaging":
            self.packaged_bytes, proofs = _run_packaging_stage(self.workspace)
            return proofs
        raise KeyError(f"Unknown finalizer stage: {stage_name}")


def create_finalizer_pipeline(
    epub_bytes: bytes,
    *,
    title: str,
    author: str,
    language: str,
    artifact_dir: Path | None = None,
) -> FinalizerPipeline:
    return FinalizerPipeline.from_epub_bytes(
        epub_bytes,
        title=title,
        author=author,
        language=language,
        artifact_dir=artifact_dir,
    )


def finalize_epub_for_kindle(
    epub_bytes: bytes,
    *,
    title: str,
    author: str,
    language: str,
) -> bytes:
    return finalize_epub_for_kindle_detailed(
        epub_bytes,
        title=title,
        author=author,
        language=language,
    )[0]


def finalize_epub_for_kindle_detailed(
    epub_bytes: bytes,
    *,
    title: str,
    author: str,
    language: str,
    artifact_dir: Path | None = None,
) -> tuple[bytes, dict]:
    """Clean up a generated EPUB unless it is fixed-layout / pre-paginated."""
    try:
        with create_finalizer_pipeline(
            epub_bytes,
            title=title,
            author=author,
            language=language,
            artifact_dir=artifact_dir,
        ) as pipeline:
            return pipeline.run_all()
    except Exception as exc:
        print(f"Kindle semantic cleanup skipped due to error: {exc}")
        return epub_bytes, _serialize_finalizer_report(
            [_build_stage_report("exception", {"exception_raised": False}, notes=[str(exc)])]
        )


def _create_finalizer_workspace(epub_bytes: bytes, root_dir: Path, *, artifact_root: Path | None = None) -> FinalizerWorkspace:
    _extract_epub(epub_bytes, root_dir)
    opf_path = _locate_opf(root_dir)
    chapter_paths = _get_spine_xhtml_paths(opf_path)
    if artifact_root is not None:
        artifact_root.mkdir(parents=True, exist_ok=True)
    return FinalizerWorkspace(root_dir=root_dir, opf_path=opf_path, chapter_paths=chapter_paths, artifact_root=artifact_root)


def _run_css_normalization_stage(workspace: FinalizerWorkspace) -> dict[str, object]:
    workspace.css_path = _resolve_css_asset_path(workspace.opf_path)
    _write_default_css(workspace.css_path)
    return _stage_css_proof(workspace.opf_path, workspace.css_path)


def _compute_semantic_plan(
    workspace: FinalizerWorkspace,
    *,
    title: str,
    author: str,
    language: str,
) -> SemanticRecoveryPlan:
    if workspace.css_path is None:
        raise RuntimeError("CSS normalization must run before semantic recovery.")

    workspace.repeated_counts = _collect_repeated_short_texts(workspace.chapter_paths)
    workspace.keep_first_seen = set()
    processed: dict[Path, ProcessedChapter] = {}
    solution_targets: dict[str, str] = {}
    toc_entries: list[dict] = []

    for chapter_path in workspace.chapter_paths:
        if chapter_path.name in {"cover.xhtml", "title.xhtml"}:
            continue

        chapter_result = _process_chapter(
            chapter_path,
            chapter_href=_relative_href(workspace.opf_path.parent, chapter_path),
            css_href=_relative_href(chapter_path.parent, workspace.css_path),
            repeated_counts=workspace.repeated_counts,
            keep_first_seen=workspace.keep_first_seen,
            title=title,
            author=author,
            language=language,
        )
        processed[chapter_path] = chapter_result
        toc_entries.extend(chapter_result.nav_entries)
        solution_targets.update(chapter_result.solution_targets)

    return SemanticRecoveryPlan(
        processed=processed,
        toc_entries=_prune_toc_entries(toc_entries),
        solution_targets=solution_targets,
    )


def _run_semantic_planning_stage(
    workspace: FinalizerWorkspace,
    *,
    title: str,
    author: str,
    language: str,
) -> dict[str, object]:
    workspace.semantic_plan = _compute_semantic_plan(
        workspace,
        title=title,
        author=author,
        language=language,
    )
    workspace.processed = dict(workspace.semantic_plan.processed)
    workspace.toc_entries = list(workspace.semantic_plan.toc_entries)
    workspace.solution_targets = dict(workspace.semantic_plan.solution_targets)
    return _stage_semantic_plan_proof(workspace.processed, workspace.toc_entries)


def _run_semantic_apply_stage(
    workspace: FinalizerWorkspace,
    *,
    title: str,
    author: str,
    language: str,
) -> dict[str, object]:
    if workspace.css_path is None:
        raise RuntimeError("CSS normalization must run before semantic recovery.")
    if workspace.semantic_plan is None:
        raise RuntimeError("Semantic planning must run before semantic apply.")

    for chapter_path in workspace.chapter_paths:
        if chapter_path.name == "cover.xhtml":
            _normalize_cover_page(chapter_path, title=title, language=language)
            continue
        if chapter_path.name == "title.xhtml":
            _normalize_title_page(
                chapter_path,
                title=title,
                author=author,
                language=language,
                css_href=_relative_href(chapter_path.parent, workspace.css_path),
            )
            continue

    for chapter_path, chapter_result in workspace.semantic_plan.processed.items():
        updated_xhtml = _inject_problem_solution_links(
            chapter_result.xhtml,
            solution_targets=workspace.semantic_plan.solution_targets,
        )
        chapter_path.write_text(updated_xhtml, encoding="utf-8")

    workspace.processed = dict(workspace.semantic_plan.processed)
    workspace.toc_entries = list(workspace.semantic_plan.toc_entries)
    workspace.solution_targets = dict(workspace.semantic_plan.solution_targets)
    return _stage_semantic_apply_proof(workspace)


def _run_navigation_rebuild_stage(
    workspace: FinalizerWorkspace,
    *,
    title: str,
    language: str,
) -> dict[str, object]:
    if workspace.css_path is None:
        raise RuntimeError("CSS normalization must run before navigation rebuild.")

    _rewrite_navigation(
        workspace.root_dir,
        workspace.opf_path,
        toc_entries=workspace.toc_entries,
        title=title,
        language=language,
        css_href=_relative_href(workspace.opf_path.parent, workspace.css_path),
    )
    return _stage_navigation_proof(workspace.opf_path, toc_entries=workspace.toc_entries)


def _run_metadata_normalization_stage(
    workspace: FinalizerWorkspace,
    *,
    title: str,
    author: str,
    language: str,
) -> dict[str, object]:
    _update_opf_metadata(workspace.opf_path, title=title, author=author, language=language)
    return _stage_metadata_proof(workspace.opf_path, title=title, author=author, language=language)


def _run_packaging_stage(workspace: FinalizerWorkspace) -> tuple[bytes, dict[str, object]]:
    packed = _pack_epub(workspace.root_dir)
    return packed, _stage_packaging_proof(workspace.root_dir)


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


def _resolve_css_asset_path(opf_path: Path) -> Path:
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(opf_path), parser)
    root = tree.getroot()
    manifest = root.find(".//opf:manifest", NS)

    if manifest is not None:
        for item in manifest.findall("opf:item", NS):
            if item.get("media-type") == "text/css":
                href = item.get("href") or ""
                if href:
                    return (opf_path.parent / href).resolve()

    css_href = "styles/baseline.css"
    css_path = (opf_path.parent / css_href).resolve()
    css_path.parent.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        css_item = etree.SubElement(manifest, f"{{{OPF_NS}}}item")
        css_item.set("id", "kindlemaster-default-css")
        css_item.set("href", css_href)
        css_item.set("media-type", "text/css")
        tree.write(str(opf_path), encoding="utf-8", xml_declaration=True, pretty_print=False)
    return css_path


def _relative_href(from_dir: Path, target_path: Path) -> str:
    return Path(os.path.relpath(target_path, from_dir)).as_posix()


def _collect_repeated_short_texts(chapter_paths: list[Path]) -> Counter:
    counter: Counter = Counter()
    for chapter_path in chapter_paths:
        soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
        body = soup.find("body")
        if body is None:
            continue
        top_level = _collect_content_nodes(body)
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
    chapter_href: str,
    css_href: str,
    repeated_counts: Counter,
    keep_first_seen: set[str],
    title: str,
    author: str,
    language: str,
) -> ProcessedChapter:
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    body = soup.find("body")
    if body is None:
        return ProcessedChapter(
            xhtml=chapter_path.read_text(encoding="utf-8"),
            nav_entries=[],
            solution_targets={},
            chapter_profile="article",
        )

    raw_nodes = _collect_content_nodes(body)
    logical_blocks = _extract_logical_blocks(
        raw_nodes,
        repeated_counts=repeated_counts,
        keep_first_seen=keep_first_seen,
        title=title,
        author=author,
    )
    chapter_profile = _classify_chapter_profile(logical_blocks)
    logical_blocks = _promote_heading_blocks(logical_blocks, chapter_profile=chapter_profile)
    logical_blocks = _merge_paragraph_blocks(logical_blocks)

    nav_entries: list[dict] = []
    solution_targets: dict[str, str] = {}
    body_parts = []
    section_id = f"section-{chapter_path.stem}"
    body_parts.append(f"<section {_chapter_profile_section_attrs(chapter_profile=chapter_profile, section_id=section_id)}>")
    heading_counter = 0
    primary_heading_open = False
    primary_heading_lead_written = False
    current_section_label = ""

    for block_index, block in enumerate(logical_blocks):
        block_type = block["type"]
        if block_type == "page-marker":
            body_parts.append(block["html"])
            continue

        if block_type == "heading":
            heading_counter += 1
            heading_level = block["level"]
            heading_id = block.get("id") or f"{chapter_path.stem}-heading-{heading_counter}"
            heading_html = html.escape(block["text"])
            body_parts.append(f'<h{heading_level} id="{heading_id}">{heading_html}</h{heading_level}>')
            previous_text = _neighbor_paragraph_text(logical_blocks, block_index, reverse=True)
            next_text = _neighbor_paragraph_window_text(logical_blocks, block_index, limit=3)
            nav_label = _derive_nav_label_safe(
                block["text"],
                chapter_profile=chapter_profile,
                previous_text=previous_text,
                next_text=next_text,
            )
            if block.get("nav_eligible") and _should_include_nav_entry(nav_label):
                nav_entries.append(
                    {
                        "file_name": chapter_href,
                        "id": heading_id,
                        "text": nav_label,
                        "level": heading_level,
                        "section_label": current_section_label,
                    }
                )
            primary_heading_open = heading_level == 1
            primary_heading_lead_written = False
            continue

        if block_type == "solution-heading":
            exercise_num = block["exercise_num"]
            target = block["target"]
            heading_id = f"solution-{exercise_num}"
            solution_targets[exercise_num] = f"{chapter_href}#{heading_id}"
            heading_html = (
                f'<a class="solution-backlink" href="{html.escape(target)}">'
                f"{html.escape(block['text'])}</a>"
            )
            body_parts.append(f'<h3 id="{heading_id}" class="solution-entry">{heading_html}</h3>')
            continue

        if block_type == "paragraph":
            class_name = block.get("class_name") or ""
            if class_name in {"section-banner", "organizational-line"} and len(block["text"]) <= 80:
                current_section_label = block["text"]
            if chapter_profile == "toc" and class_name == "organizational-line":
                class_name = "toc-line"
            elif chapter_profile == "back_matter" and class_name == "organizational-line":
                class_name = "back-matter-line"
            if (
                primary_heading_open
                and not primary_heading_lead_written
                and not class_name
                and len(block["text"]) >= 90
            ):
                class_name = "lead"
                primary_heading_lead_written = True
            ordered_list = detect_inline_ordered_list(block["text"]) if not class_name else None
            if ordered_list:
                body_parts.append(_render_detected_ordered_list(ordered_list))
                primary_heading_open = False
                continue
            class_attr = f' class="{html.escape(class_name)}"' if class_name else ""
            body_parts.append(f"<p{class_attr}>{block['html']}</p>")
            if class_name not in {"byline", "organizational-line", "section-banner"}:
                primary_heading_open = False
            continue

        if block_type == "problem-page-link":
            body_parts.append(
                f'<p class="problem-page-link"><a href="{html.escape(block["href"])}">'
                f"{html.escape(block['text'])}</a></p>"
            )
            continue

        if block_type == "figure":
            body_parts.append(block["html"])

    body_parts.append("</section>")
    if chapter_profile == "article" and nav_entries and not any(entry["level"] == 1 for entry in nav_entries):
        nav_entries[0]["level"] = 1

    document_title = _first_nonempty(
        (entry["text"] for entry in nav_entries if entry["file_name"] == chapter_href),
        (block["text"] for block in logical_blocks if block["type"] == "heading" and block.get("nav_eligible")),
        default=chapter_path.stem,
    )
    xhtml = _build_xhtml_document(
        title=document_title,
        body_html="\n".join(body_parts),
        language=language,
        css_href=css_href,
        body_attrs=_chapter_profile_body_attrs(chapter_profile),
    )
    chapter_path.write_text(xhtml, encoding="utf-8")
    return ProcessedChapter(
        xhtml=xhtml,
        nav_entries=nav_entries,
        solution_targets=solution_targets,
        chapter_profile=chapter_profile,
    )


def _collect_content_nodes(container: Tag) -> list[Tag]:
    nodes: list[Tag] = []
    for child in container.children:
        if not isinstance(child, Tag):
            continue
        if child.name in {"p", "h1", "h2", "h3", "span", "figure", "img"}:
            nodes.append(child)
            continue
        if child.name in {"section", "article", "main", "aside", "blockquote", "div"}:
            # Many incoming EPUBs wrap real content in nested sections or blockquotes.
            # Flatten those containers so cleanup works on meaningful reading blocks
            # instead of silently dropping entire chapters.
            nested_nodes = _collect_content_nodes(child)
            if nested_nodes:
                nodes.extend(nested_nodes)
                continue
        nodes.append(child)
    return nodes


def _chapter_profile_body_attrs(chapter_profile: str) -> str:
    css_profile = chapter_profile.replace("_", "-")
    body_classes = [f"km-profile-{css_profile}"]
    epub_type = {
        "article": "bodymatter",
        "front_matter": "frontmatter",
        "toc": "frontmatter toc",
        "back_matter": "backmatter",
    }.get(chapter_profile)
    attrs = [f'class="{" ".join(body_classes)}"', f'data-km-profile="{html.escape(chapter_profile)}"']
    if epub_type:
        attrs.append(f'epub:type="{html.escape(epub_type)}"')
    return " " + " ".join(attrs)


def _chapter_profile_section_attrs(*, chapter_profile: str, section_id: str) -> str:
    css_profile = chapter_profile.replace("_", "-")
    section_classes = [f"km-section", f"km-profile-{css_profile}"]
    epub_type = {
        "article": "bodymatter chapter",
        "front_matter": "frontmatter",
        "toc": "frontmatter toc",
        "back_matter": "backmatter",
    }.get(chapter_profile)
    attrs = [
        f'id="{html.escape(section_id)}"',
        f'class="{" ".join(section_classes)}"',
        f'data-km-profile="{html.escape(chapter_profile)}"',
    ]
    if epub_type:
        attrs.append(f'epub:type="{html.escape(epub_type)}"')
    return " ".join(attrs)


def _extract_logical_blocks(
    raw_nodes: list[Tag],
    *,
    repeated_counts: Counter,
    keep_first_seen: set[str],
    title: str,
    author: str,
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

        if node.name not in {"p", "h1", "h2", "h3"}:
            continue

        if not plain_text:
            continue
        if PAGE_TITLE_RE.match(plain_text) or PAGE_NUMBER_RE.match(plain_text) or PAGE_LABEL_RE.match(plain_text):
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

        class_name = ""
        if "diagram-tail" in _class_list(node):
            class_name = "diagram-tail"

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


def _classify_chapter_profile(blocks: list[dict]) -> str:
    texts = [block["text"] for block in blocks if block["type"] == "paragraph"][:14]
    if not texts:
        return "article"

    lowered = [_normalize_text(text).lower() for text in texts]
    joined = " \n ".join(lowered)
    special_hits = sum(1 for text in texts if _looks_like_section_banner_text(text))
    role_hits = sum(1 for text in texts if _looks_like_person_role_line(text) or _looks_like_person_name_line(text))
    promo_hits = sum(
        1
        for text in texts
        if _matches_signal_term(text, PROMO_SECTION_TERMS + PROMO_BANNER_TERMS)
    )
    toc_hits = sum(1 for text in texts if _matches_signal_term(text, TOC_SECTION_TERMS))
    toc_teaser_hits = sum(1 for text in texts[:12] if _looks_like_toc_teaser_line(text))

    if any(TOC_PAGE_RE.search(text) for text in texts) or toc_hits >= 1 or toc_teaser_hits >= 4:
        return "toc"
    if any(BIBLIOGRAPHY_RE.match(text.strip()) for text in texts):
        return "back_matter"
    if "w kioskach" in joined or "nr indeksu" in joined:
        return "front_matter"
    if "www." in joined and ("wellness" in joined or "zwycięzca" in joined or "radisson" in joined):
        return "promo"
    if _has_direct_article_opening(blocks):
        return "article"
    if _has_banner_then_article_opening(blocks):
        return "article"
    if "drodzy czytelnicy" in joined or _matches_signal_term(joined, FRONT_MATTER_HINT_TERMS):
        return "front_matter"
    if "kontakt:" in joined or "projekt i sk" in joined:
        return "front_matter"
    if "issn" in joined or "kwartalnik" in joined or "temat numeru" in joined:
        return "front_matter"
    if "issn" in joined and PAGE_SPAN_RE.search(" ".join(texts)):
        return "front_matter"
    if promo_hits >= 1:
        return "promo"
    if special_hits + role_hits >= 4:
        return "front_matter"
    return "article"


def _has_banner_then_article_opening(blocks: list[dict]) -> bool:
    paragraph_blocks = [block for block in blocks if block["type"] == "paragraph"][:6]
    if len(paragraph_blocks) < 2:
        return False

    for index, block in enumerate(paragraph_blocks[:4]):
        text = block["text"]
        if not _looks_like_primary_title_candidate(text):
            continue

        previous_texts = [candidate["text"] for candidate in paragraph_blocks[:index]]
        next_texts = [candidate["text"] for candidate in paragraph_blocks[index + 1 : index + 3]]

        has_banner_before = any(_looks_like_section_banner_text(candidate) for candidate in previous_texts)
        if not has_banner_before:
            continue

        if any(len(candidate) >= 180 for candidate in previous_texts):
            continue

        if any(_looks_like_opening_support_line(candidate) for candidate in next_texts):
            return True

    return False


def _looks_like_toc_teaser_line(text: str) -> bool:
    cleaned = _normalize_text(text)
    if not re.match(r"^\d{1,3}\s+", cleaned):
        return False
    words = [word for word in re.split(r"\s+", cleaned) if any(ch.isalpha() for ch in word)]
    if len(words) < 4:
        return False
    alpha_chars = [ch for ch in cleaned if ch.isalpha()]
    if not alpha_chars:
        return False
    upper_ratio = sum(1 for ch in alpha_chars if ch.isupper()) / len(alpha_chars)
    return upper_ratio >= 0.55


def _starts_alpha_upper(word: str) -> bool:
    for char in word:
        if char.isalpha():
            return char.isupper()
    return False


def _starts_alpha_lower(word: str) -> bool:
    for char in word:
        if char.isalpha():
            return char.islower()
    return False


def _split_merged_opening_title_and_lead(text: str) -> tuple[str, str] | None:
    cleaned = _normalize_text(text)
    if not cleaned or len(cleaned) < 24 or len(cleaned) > 280:
        return None
    if PAGE_LABEL_RE.match(cleaned) or PAGE_SPAN_RE.search(cleaned):
        return None
    if _looks_like_toc_teaser_line(cleaned):
        return None
    if _matches_signal_term(cleaned, TOC_SECTION_TERMS + PROMO_BANNER_TERMS + FRONT_MATTER_HINT_TERMS):
        return None

    words = [word for word in re.split(r"\s+", cleaned) if word]
    if len(words) < 6:
        return None

    upper_bound = min(len(words) - 2, 16)
    for index in range(3, upper_bound):
        token = words[index]
        next_token = words[index + 1]
        previous_window = words[max(0, index - 3) : index]
        previous_lower = sum(1 for candidate in previous_window if _starts_alpha_lower(candidate))
        if previous_lower < 2:
            continue
        if not _starts_alpha_upper(token) or not _starts_alpha_lower(next_token):
            continue

        title_text = " ".join(words[:index]).strip(" ,;:-")
        lead_text = " ".join(words[index:]).strip()
        title_words = [word for word in re.split(r"\s+", title_text) if any(ch.isalpha() for ch in word)]
        lead_words = [word for word in re.split(r"\s+", lead_text) if any(ch.isalpha() for ch in word)]
        if len(title_words) < 3 or len(title_words) > 12:
            continue
        if title_text.endswith((".", ":", ";")):
            continue
        if _looks_like_person_name_line(title_text) or _looks_like_person_role_line(title_text):
            continue
        if _matches_signal_term(title_text, TOC_SECTION_TERMS + PROMO_BANNER_TERMS + FRONT_MATTER_HINT_TERMS):
            continue
        if len(lead_words) >= 8 and len(lead_text) >= 45:
            return title_text, lead_text
        if (
            2 <= len(lead_words) <= 8
            and 10 <= len(lead_text) <= 60
            and not any(marker in cleaned for marker in ".!:;")
        ):
            return title_text, lead_text

    return None


def _extract_short_opening_phrase(text: str) -> str | None:
    cleaned = _normalize_text(text)
    if not cleaned or PAGE_LABEL_RE.match(cleaned) or PAGE_SPAN_RE.search(cleaned):
        return None
    if _looks_like_person_name_line(cleaned) or _looks_like_person_role_line(cleaned):
        return None

    quoted_match = re.match(r'^[„"“]([^"”]{2,40})["”]', cleaned)
    if quoted_match:
        phrase = quoted_match.group(1).strip(" ,;:-")
        phrase_words = [word for word in re.split(r"\s+", phrase) if any(ch.isalpha() for ch in word)]
        if 1 <= len(phrase_words) <= 5:
            return phrase

    for marker in ("?", "!"):
        marker_index = cleaned.find(marker)
        if 0 < marker_index < 90:
            phrase = cleaned[: marker_index + 1].strip()
            phrase_words = [word for word in re.split(r"\s+", phrase) if any(ch.isalpha() for ch in word)]
            if 3 <= len(phrase_words) <= 14:
                return phrase

    words = [word for word in re.split(r"\s+", cleaned) if word]
    upper_bound = min(len(words) - 2, 10)
    for index in range(2, upper_bound):
        token = words[index]
        next_token = words[index + 1]
        previous_window = words[max(0, index - 3) : index]
        previous_lower = sum(1 for candidate in previous_window if _starts_alpha_lower(candidate))
        if previous_lower < 1:
            continue
        if not _starts_alpha_upper(token) or not _starts_alpha_lower(next_token):
            continue
        phrase = " ".join(words[:index]).strip(" ,;:-")
        phrase_words = [word for word in re.split(r"\s+", phrase) if any(ch.isalpha() for ch in word)]
        if 2 <= len(phrase_words) <= 8:
            return phrase

    alpha_words = [word for word in words if any(ch.isalpha() for ch in word)]
    if 2 <= len(alpha_words) <= 6 and len(cleaned) <= 48 and cleaned[:1].isupper():
        return cleaned.strip(" ,;:-")
    return None


def _trim_dangling_nav_tail(text: str) -> str:
    cleaned = _normalize_text(text).strip()
    if not cleaned:
        return ""

    for marker in ("?", "!"):
        marker_index = cleaned.rfind(marker)
        if marker_index == -1 or marker_index == len(cleaned) - 1:
            continue
        tail = cleaned[marker_index + 1 :].strip()
        tail_words = [word for word in re.split(r"\s+", tail) if any(ch.isalpha() for ch in word)]
        if 0 < len(tail_words) <= 2 and len(tail) <= 18:
            cleaned = cleaned[: marker_index + 1].strip()
            break

    return cleaned.strip(" ,;:-")


def _looks_like_dangling_nav_label(text: str) -> bool:
    cleaned = _trim_dangling_nav_tail(text)
    if not cleaned:
        return False
    if cleaned.endswith((",", ";", ":", "-", "â€“", "â€”", "(", "/")):
        return True
    if cleaned.count("(") > cleaned.count(")"):
        return True
    words = [word for word in re.split(r"\s+", cleaned) if any(ch.isalpha() for ch in word)]
    if 1 < len(words) <= 4 and words[-1].strip(" ,;:!?").lower() in TRUNCATED_TITLE_END_WORDS:
        return True
    return False


def _looks_like_generic_article_heading(text: str) -> bool:
    cleaned = _normalize_text(text)
    if not cleaned:
        return False
    words = [word for word in re.split(r"\s+", cleaned) if any(ch.isalpha() for ch in word)]
    if not 3 <= len(words) <= 5:
        return False
    if any(marker in cleaned for marker in ".!?:;()"):
        return False
    return words[0].lower() in GENERIC_NAV_HEADING_PREFIXES


def _extend_label_with_parenthetical_tail(label: str, next_text: str) -> str | None:
    if label.count("(") <= label.count(")") or not next_text:
        return None
    cleaned_next = _normalize_text(next_text)
    if not cleaned_next:
        return None
    match = re.match(r"^[-â€“â€”,\s]*([^)]{1,30}\))", cleaned_next)
    if not match:
        return None
    tail = match.group(1).lstrip("-â€“â€” ").strip()
    if not tail:
        return None
    combined = _normalize_text(f'{label.rstrip(" ,;:-")} - {tail}')
    return combined.strip(" ,;:-")


def _neighbor_paragraph_text(blocks: list[dict], index: int, *, reverse: bool = False) -> str:
    iterator = range(index - 1, -1, -1) if reverse else range(index + 1, len(blocks))
    for pointer in iterator:
        candidate = blocks[pointer]
        if candidate["type"] != "paragraph":
            if candidate["type"] in {"heading", "solution-heading"}:
                break
            continue
        return candidate["text"]
    return ""


def _neighbor_paragraph_window_text(blocks: list[dict], index: int, *, limit: int = 3) -> str:
    texts: list[str] = []
    for pointer in range(index + 1, len(blocks)):
        candidate = blocks[pointer]
        if candidate["type"] != "paragraph":
            if candidate["type"] in {"heading", "solution-heading"}:
                break
            continue
        texts.append(candidate["text"])
        if len(texts) >= limit:
            break
    return _normalize_text(" ".join(texts))


def _derive_nav_label(
    heading_text: str,
    *,
    chapter_profile: str,
    previous_text: str = "",
    next_text: str = "",
) -> str:
    label = _trim_dangling_nav_tail(heading_text)
    if not label:
        return heading_text

    if chapter_profile == "article":
        merged_opening = _split_merged_opening_title_and_lead(label)
        if merged_opening:
            label = merged_opening[0]

    extended_parenthetical = _extend_label_with_parenthetical_tail(label, next_text)
    if extended_parenthetical:
        label = extended_parenthetical

    next_phrase = _extract_short_opening_phrase(next_text) if next_text else None
    previous_phrase = _extract_short_opening_phrase(previous_text) if previous_text else None

    if _looks_like_dangling_nav_label(label) and next_phrase:
        candidate = _normalize_text(f"{label} {next_phrase}").strip(" ,;:-")
        if len(candidate) <= 90:
            label = candidate
    elif (
        next_phrase
        and chapter_profile == "article"
        and (
            _looks_like_generic_article_heading(label)
            or len([word for word in re.split(r"\s+", label) if any(ch.isalpha() for ch in word)]) <= 3
            or sum(1 for ch in label if ch.isalpha() and ch.isupper())
            >= max(3, len([ch for ch in label if ch.isalpha()]) * 0.7)
        )
    ):
        candidate = _normalize_text(f"{label} â€” {next_phrase}").strip(" ,;:-")
        if len(candidate) <= 90:
            label = candidate

    if (
        previous_phrase
        and chapter_profile == "article"
        and previous_phrase.lower() not in label.lower()
        and len(previous_phrase) <= 48
    ):
        candidate = _normalize_text(f"{previous_phrase} â€” {label}").strip(" ,;:-")
        if len(candidate) <= 90:
            label = candidate

    return _trim_dangling_nav_tail(label)


def _extract_short_nav_phrase_safe(text: str) -> str | None:
    cleaned = _normalize_text(text)
    if not cleaned or PAGE_LABEL_RE.match(cleaned) or PAGE_SPAN_RE.search(cleaned):
        return None
    if _looks_like_person_name_line(cleaned) or _looks_like_person_role_line(cleaned):
        return None

    quoted_match = re.match(r'^[\u201e"\u201c]([^"\u201d]{2,40})["\u201d]', cleaned)
    if quoted_match:
        phrase = quoted_match.group(1).strip(" ,;:-")
        phrase_words = [word for word in re.split(r"\s+", phrase) if any(ch.isalpha() for ch in word)]
        if 1 <= len(phrase_words) <= 5:
            return phrase

    for marker in ("?", "!"):
        marker_index = cleaned.find(marker)
        if 0 < marker_index < 90:
            phrase = cleaned[: marker_index + 1].strip()
            phrase_words = [word for word in re.split(r"\s+", phrase) if any(ch.isalpha() for ch in word)]
            if 3 <= len(phrase_words) <= 14:
                return phrase

    words = [word for word in re.split(r"\s+", cleaned) if word]
    upper_bound = min(len(words) - 2, 10)
    for index in range(2, upper_bound):
        token = words[index]
        next_token = words[index + 1]
        previous_window = words[max(0, index - 3) : index]
        previous_lower = sum(1 for candidate in previous_window if _starts_alpha_lower(candidate))
        if previous_lower < 1:
            continue
        if not _starts_alpha_upper(token) or not _starts_alpha_lower(next_token):
            continue
        phrase = " ".join(words[:index]).strip(" ,;:-")
        phrase_words = [word for word in re.split(r"\s+", phrase) if any(ch.isalpha() for ch in word)]
        if 2 <= len(phrase_words) <= 8:
            return phrase

    alpha_words = [word for word in words if any(ch.isalpha() for ch in word)]
    if 2 <= len(alpha_words) <= 6 and len(cleaned) <= 48 and cleaned[:1].isupper():
        return cleaned.strip(" ,;:-")
    return None


def _looks_like_dangling_nav_label_safe(text: str) -> bool:
    cleaned = _trim_dangling_nav_tail(text)
    if not cleaned:
        return False
    if cleaned.endswith((",", ";", ":", "-", "\u2013", "\u2014", "(", "/")):
        return True
    if cleaned.count("(") > cleaned.count(")"):
        return True
    words = [word for word in re.split(r"\s+", cleaned) if any(ch.isalpha() for ch in word)]
    if 1 < len(words) <= 4 and words[-1].strip(" ,;:!?").lower() in TRUNCATED_TITLE_END_WORDS:
        return True
    return False


def _extend_label_with_parenthetical_tail_safe(label: str, next_text: str) -> str | None:
    if label.count("(") <= label.count(")") or not next_text:
        return None
    cleaned_next = _normalize_text(next_text)
    if not cleaned_next:
        return None
    match = re.search(r"(?:(?:-|–|—)\s*)?((?:\d{4}|Date unknown)\))", cleaned_next, re.IGNORECASE)
    if not match:
        return None
    tail = match.group(1).strip()
    if not tail:
        return None
    combined = _normalize_text(f'{label.rstrip(" ,;:-")} - {tail}')
    return combined.strip(" ,;:-")


def _derive_nav_label_safe(
    heading_text: str,
    *,
    chapter_profile: str,
    previous_text: str = "",
    next_text: str = "",
) -> str:
    label = _trim_dangling_nav_tail(heading_text)
    if not label:
        return heading_text

    if chapter_profile == "article":
        merged_opening = _split_merged_opening_title_and_lead(label)
        if merged_opening:
            label = merged_opening[0]

    extended_parenthetical = _extend_label_with_parenthetical_tail_safe(label, next_text)
    if extended_parenthetical:
        label = extended_parenthetical

    next_phrase = _extract_short_nav_phrase_safe(next_text) if next_text else None
    previous_phrase = _extract_short_nav_phrase_safe(previous_text) if previous_text else None
    alpha_chars = [ch for ch in label if ch.isalpha()]
    uppercase_ratio = (
        sum(1 for ch in alpha_chars if ch.isupper()) / len(alpha_chars) if alpha_chars else 0.0
    )
    label_word_count = len([word for word in re.split(r"\s+", label) if any(ch.isalpha() for ch in word)])

    if _looks_like_dangling_nav_label_safe(label) and next_phrase:
        candidate = _normalize_text(f"{label} {next_phrase}").strip(" ,;:-")
        if len(candidate) <= 90:
            label = candidate
    elif (
        next_phrase
        and chapter_profile == "article"
        and (
            _looks_like_generic_article_heading(label)
            or label_word_count <= 3
            or uppercase_ratio >= 0.7
        )
    ):
        candidate = _normalize_text(f"{label} - {next_phrase}").strip(" ,;:-")
        if len(candidate) <= 90:
            label = candidate

    if (
        previous_phrase
        and chapter_profile == "article"
        and previous_phrase.lower() not in label.lower()
        and len(previous_phrase) <= 48
    ):
        candidate = _normalize_text(f"{previous_phrase} - {label}").strip(" ,;:-")
        if len(candidate) <= 90:
            label = candidate

    return _trim_dangling_nav_tail(label)


def _has_direct_article_opening(blocks: list[dict]) -> bool:
    paragraph_blocks = [block for block in blocks if block["type"] == "paragraph"][:8]
    if len(paragraph_blocks) < 3:
        return False

    for index, block in enumerate(paragraph_blocks[:4]):
        text = block["text"]
        if not _looks_like_primary_title_candidate(text):
            continue

        previous_texts = [candidate["text"] for candidate in paragraph_blocks[:index]]
        next_blocks = paragraph_blocks[index + 1 : index + 5]
        next_texts = [candidate["text"] for candidate in next_blocks]

        if any(len(candidate) >= 180 for candidate in previous_texts):
            continue

        has_byline = any(
            candidate.startswith(("â€”", "-")) or _looks_like_person_name_line(candidate)
            for candidate in next_texts[:2]
        )
        has_long_opening = any(len(candidate) >= 110 for candidate in next_texts[:4])
        has_support_line = any(_looks_like_opening_support_line(candidate) for candidate in next_texts[:3])
        first_is_support = _looks_like_opening_support_line(next_texts[0]) if next_texts else False

        if has_byline and (has_long_opening or has_support_line):
            return True
        if first_is_support and has_long_opening:
            return True

        if index == 0 and len(text) >= 45 and has_long_opening:
            return True

    return False


def _has_local_article_opening_signal(blocks: list[dict], index: int) -> bool:
    next_texts: list[str] = []
    for candidate in blocks[index + 1 :]:
        if candidate["type"] != "paragraph":
            if candidate["type"] in {"heading", "solution-heading"}:
                break
            continue
        next_texts.append(candidate["text"])
        if len(next_texts) >= 4:
            break

    if not next_texts:
        return False

    first_text = next_texts[0]
    if _starts_with_lowercase_continuation(first_text):
        return False

    has_byline = any(
        candidate.startswith(("—", "-")) or _looks_like_person_name_line(candidate)
        for candidate in next_texts[:2]
    )
    has_long_opening = any(len(candidate) >= 110 for candidate in next_texts[:4])
    has_support_line = any(_looks_like_opening_support_line(candidate) for candidate in next_texts[:3])
    if has_byline and (has_long_opening or has_support_line):
        return True
    first_is_support = _looks_like_opening_support_line(first_text)
    return first_is_support and has_long_opening


def _promote_heading_blocks(blocks: list[dict], *, chapter_profile: str) -> list[dict]:
    promoted = []
    seen_primary_heading = False

    for index, block in enumerate(blocks):
        if block["type"] != "paragraph":
            promoted.append(block)
            continue

        text = block["text"]
        if _looks_like_person_role_line(text):
            downgraded = dict(block)
            downgraded["class_name"] = downgraded.get("class_name") or "organizational-line"
            promoted.append(downgraded)
            continue
        if _looks_like_person_name_line(text):
            downgraded = dict(block)
            downgraded["class_name"] = downgraded.get("class_name") or "byline"
            promoted.append(downgraded)
            continue
        if text.startswith(("—", "-")):
            downgraded = dict(block)
            downgraded["class_name"] = downgraded.get("class_name") or "byline"
            promoted.append(downgraded)
            continue
        next_text = ""
        for candidate in blocks[index + 1:]:
            if candidate["type"] == "paragraph":
                next_text = candidate["text"]
                break
            if candidate["type"] in {"figure", "heading", "solution-heading"}:
                break

        prior_long_paragraph = any(
            candidate["type"] == "paragraph" and len(candidate["text"]) >= 40
            for candidate in blocks[:index]
        )
        has_local_opening_signal = _has_local_article_opening_signal(blocks, index)
        merged_opening = (
            _split_merged_opening_title_and_lead(text)
            if chapter_profile == "article"
            and not seen_primary_heading
            and (block.get("is_top") or index <= 4)
            and not prior_long_paragraph
            and has_local_opening_signal
            else None
        )
        is_primary_title_candidate = (
            chapter_profile == "article"
            and not seen_primary_heading
            and (block.get("is_top") or index <= 4)
            and not prior_long_paragraph
            and has_local_opening_signal
            and _looks_like_primary_title_candidate(text)
        )

        if merged_opening:
            title_text, lead_text = merged_opening
            promoted.append(
                {
                    "type": "heading",
                    "text": title_text,
                    "level": 1,
                    "id": _slugify(title_text),
                    "nav_eligible": True,
                }
            )
            promoted.append(
                {
                    "type": "paragraph",
                    "text": lead_text,
                    "html": html.escape(lead_text),
                    "class_name": "lead",
                }
            )
            seen_primary_heading = True
            continue

        if (
            is_primary_title_candidate
            and index == 0
            and text.endswith("?")
            and next_text.startswith(("—", "-"))
        ):
            promoted.append(
                {
                    "type": "heading",
                    "text": text,
                    "level": 1,
                    "id": _slugify(text),
                    "nav_eligible": has_local_opening_signal,
                }
            )
            seen_primary_heading = True
            continue

        if is_primary_title_candidate:
            promoted.append(
                {
                    "type": "heading",
                    "text": text,
                    "level": 1,
                    "id": _slugify(text),
                    "nav_eligible": has_local_opening_signal,
                }
            )
            seen_primary_heading = True
            continue

        if any(term in text.lower() for term in SPECIAL_SECTION_TERMS):
            downgraded = dict(block)
            downgraded["class_name"] = downgraded.get("class_name") or "section-banner"
            promoted.append(downgraded)
            continue
        if chapter_profile in {"front_matter", "promo", "toc", "back_matter"}:
            downgraded = dict(block)
            downgraded["class_name"] = downgraded.get("class_name") or (
                "section-banner" if _looks_like_section_banner_text(text) else "organizational-line"
            )
            promoted.append(downgraded)
            continue
        if not _looks_like_heading_text(text):
            promoted.append(block)
            continue

        nav_eligible = False
        prev_type = promoted[-1]["type"] if promoted else None

        if (
            is_primary_title_candidate
        ):
            level = 1
            nav_eligible = has_local_opening_signal
        elif prev_type == "paragraph" and not block.get("is_top"):
            level = 3
        else:
            level = 2 if not seen_primary_heading else 3

        if chapter_profile != "article":
            level = 3
        elif next_text and len(next_text) < 40 and _looks_like_heading_text(next_text):
            level = 2

        promoted.append(
            {
                "type": "heading",
                "text": text,
                "level": level,
                "id": _slugify(text),
                "nav_eligible": nav_eligible,
            }
        )
        if level <= 2:
            seen_primary_heading = True

    return promoted


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
        if _should_merge_paragraphs(previous, block):
            separator = _merge_separator(previous["text"], block["text"])
            previous["text"] = _normalize_text(f'{previous["text"]}{separator}{block["text"]}')
            previous["html"] = _sanitize_inline_html(f'{previous["html"]}{separator}{block["html"]}')
            previous["class_name"] = previous.get("class_name") or block.get("class_name") or ""
        else:
            merged.append(dict(block))

    return merged


def _render_detected_ordered_list(ordered_list: dict[str, object]) -> str:
    list_style = "upper-alpha" if ordered_list.get("list_style") == "upper-alpha" else "decimal"
    items = "".join(
        f"<li>{html.escape(str(item))}</li>"
        for item in (ordered_list.get("items") or [])
    )
    return f'<ol class="inline-choice-list {html.escape(list_style)}">{items}</ol>'


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
        img["alt"] = ""

    return f'<figure class="figure">{str(img)}</figure>'


def _inject_problem_solution_links(xhtml: str, *, solution_targets: dict[str, str]) -> str:
    soup = BeautifulSoup(xhtml, "xml")
    body = soup.find("body")
    if body is None:
        return xhtml

    figures = body.find_all("figure", class_="chess-problem")
    inserted_count = 0
    missing_target = False

    for figure in figures:
        figure_id = figure.get("id", "")
        match = re.search(r"exercise-(\d+)", figure_id)
        if not match:
            missing_target = True
            continue
        exercise_num = match.group(1)
        target = solution_targets.get(exercise_num)
        if not target:
            missing_target = True
            continue
        existing = figure.find_next_sibling("p", class_="problem-solution-link")
        if existing is not None:
            continue
        link_tag = soup.new_tag("a", href=target)
        link_tag.string = f"Przejdź do rozwiązania {exercise_num}"
        para = soup.new_tag("p")
        para["class"] = "problem-solution-link"
        para.append(link_tag)
        figure.insert_after(para)
        inserted_count += 1

    if inserted_count and not missing_target:
        for page_link in body.find_all("p", class_="problem-page-link"):
            page_link.decompose()

    return _serialize_soup_document(soup)


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


def _normalize_title_page(chapter_path: Path, *, title: str, author: str, language: str, css_href: str) -> None:
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    html_tag = soup.find("html")
    if html_tag is not None:
        html_tag["lang"] = language
        html_tag["xml:lang"] = language

    head = soup.find("head")
    if head is None and html_tag is not None:
        head = soup.new_tag("head")
        html_tag.insert(0, head)
    if head is not None:
        title_tag = head.find("title")
        if title_tag is None:
            title_tag = soup.new_tag("title")
            head.append(title_tag)
        title_tag.string = title

        link_tag = head.find("link", rel="stylesheet")
        if link_tag is None:
            link_tag = soup.new_tag("link", rel="stylesheet", type="text/css")
            head.append(link_tag)
        link_tag["href"] = css_href

    body = soup.find("body")
    if body is None and html_tag is not None:
        body = soup.new_tag("body")
        html_tag.append(body)
    if body is not None and not _normalize_text(body.get_text(" ", strip=True)):
        section = soup.new_tag("section", id="title-page")
        heading = soup.new_tag("h1")
        heading.string = title
        section.append(heading)
        if author:
            byline = soup.new_tag("p")
            byline.string = author
            section.append(byline)
        body.clear()
        body.append(section)

    chapter_path.write_text(_serialize_soup_document(soup), encoding="utf-8")


def _rewrite_navigation(root_dir: Path, opf_path: Path, *, toc_entries: list[dict], title: str, language: str, css_href: str) -> None:
    nav_path = opf_path.parent / "nav.xhtml"
    toc_path = opf_path.parent / "toc.ncx"
    nav_path.write_text(
        _build_nav_xhtml(toc_entries=toc_entries, title=title, language=language, css_href=css_href),
        encoding="utf-8",
    )
    toc_path.write_text(_build_toc_ncx(toc_entries=toc_entries, title=title), encoding="utf-8")


def _update_opf_metadata(opf_path: Path, *, title: str, author: str, language: str) -> None:
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(opf_path), parser)
    root = tree.getroot()

    metadata = root.find(".//opf:metadata", NS)
    if metadata is None:
        return

    _set_dc_value(metadata, "title", title)
    _set_dc_value(metadata, "creator", author)
    _set_dc_value(metadata, "language", language)
    _upsert_modified_timestamp(metadata)
    _mark_cover_image(root)
    _ensure_nav_manifest_property(root)

    tree.write(str(opf_path), encoding="utf-8", xml_declaration=True, pretty_print=False)


def _write_default_css(css_path: Path) -> None:
    css_path.parent.mkdir(parents=True, exist_ok=True)
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


def _stage_snapshot_candidates(workspace: FinalizerWorkspace) -> list[Path]:
    candidates: list[Path] = [workspace.opf_path]
    if workspace.css_path is not None:
        candidates.append(workspace.css_path)
    for relative_name in ("EPUB/title.xhtml", "EPUB/cover.xhtml", "EPUB/nav.xhtml", "EPUB/toc.ncx"):
        candidate = workspace.root_dir / relative_name
        if candidate.exists():
            candidates.append(candidate)
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_stage_report(
    stage: str,
    proofs: dict[str, object],
    *,
    acceptance_checks: tuple[str, ...] | list[str] = (),
    notes: list[str] | None = None,
) -> FinalizerStageReport:
    acceptance_list = list(acceptance_checks)
    accepted = all(bool(proofs.get(check)) for check in acceptance_list)
    report_notes = list(notes or [])
    if acceptance_list and not accepted:
        failed_checks = [check for check in acceptance_list if not bool(proofs.get(check))]
        report_notes.append(f"Acceptance boundary failed: {', '.join(failed_checks)}")
    return FinalizerStageReport(
        stage=stage,
        proofs=proofs,
        acceptance_checks=acceptance_list,
        accepted=accepted,
        notes=report_notes,
    )


def _persist_stage_artifacts(
    workspace: FinalizerWorkspace,
    stage_report: FinalizerStageReport,
    *,
    packed_bytes: bytes | None = None,
) -> None:
    if workspace.artifact_root is None:
        return

    stage_dir = workspace.artifact_root / stage_report.stage
    stage_dir.mkdir(parents=True, exist_ok=True)

    snapshot_files: list[dict[str, object]] = []
    for source_path in _stage_snapshot_candidates(workspace):
        source_relative = source_path.relative_to(workspace.root_dir).as_posix()
        artifact_name = source_relative.replace("/", "__")
        destination_path = stage_dir / artifact_name
        shutil.copy2(source_path, destination_path)
        snapshot_files.append(
            {
                "source_relative": source_relative,
                "artifact_path": str(destination_path),
                "artifact_name": artifact_name,
                "sha256": _sha256_path(destination_path),
            }
        )

    extra_artifacts: list[dict[str, object]] = []
    if workspace.toc_entries and stage_report.stage in {"semantic_planning", "semantic_apply", "navigation_rebuild"}:
        toc_entries_path = stage_dir / "toc_entries.json"
        toc_entries_path.write_text(json.dumps(workspace.toc_entries, ensure_ascii=False, indent=2), encoding="utf-8")
        extra_artifacts.append(
            {
                "artifact_type": "toc_entries",
                "artifact_path": str(toc_entries_path),
                "sha256": _sha256_path(toc_entries_path),
            }
        )
    if stage_report.stage == "semantic_planning" and workspace.processed:
        semantic_plan_path = stage_dir / "semantic_plan.json"
        semantic_plan_payload = {
            "chapter_count": len(workspace.processed),
            "toc_entry_count": len(workspace.toc_entries),
            "solution_target_count": len(workspace.solution_targets),
            "chapters": [
                {
                    "chapter_relative": path.relative_to(workspace.root_dir).as_posix(),
                    "chapter_profile": chapter.chapter_profile,
                    "nav_entry_count": len(chapter.nav_entries),
                    "solution_target_count": len(chapter.solution_targets),
                }
                for path, chapter in sorted(
                    workspace.processed.items(),
                    key=lambda item: item[0].relative_to(workspace.root_dir).as_posix(),
                )
            ],
        }
        semantic_plan_path.write_text(json.dumps(semantic_plan_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        extra_artifacts.append(
            {
                "artifact_type": "semantic_plan",
                "artifact_path": str(semantic_plan_path),
                "sha256": _sha256_path(semantic_plan_path),
            }
        )
    if stage_report.stage == "semantic_apply" and workspace.processed:
        chapter_write_manifest_path = stage_dir / "chapter_write_manifest.json"
        chapter_write_manifest_payload = {
            "chapter_count": len(workspace.processed),
            "chapters": [
                {
                    "chapter_relative": path.relative_to(workspace.root_dir).as_posix(),
                    "sha256": _sha256_path(path),
                }
                for path in sorted(workspace.processed, key=lambda item: item.relative_to(workspace.root_dir).as_posix())
                if path.exists()
            ],
        }
        chapter_write_manifest_path.write_text(
            json.dumps(chapter_write_manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        extra_artifacts.append(
            {
                "artifact_type": "chapter_write_manifest",
                "artifact_path": str(chapter_write_manifest_path),
                "sha256": _sha256_path(chapter_write_manifest_path),
            }
        )
    if packed_bytes is not None:
        packaged_epub_path = stage_dir / "packaged.epub"
        packaged_epub_path.write_bytes(packed_bytes)
        extra_artifacts.append(
            {
                "artifact_type": "packaged_epub",
                "artifact_path": str(packaged_epub_path),
                "sha256": hashlib.sha256(packed_bytes).hexdigest(),
            }
        )

    proof_path = stage_dir / "proof.json"
    proof_payload = {
        "stage": stage_report.stage,
        "proofs": stage_report.proofs,
        "acceptance_checks": list(stage_report.acceptance_checks),
        "accepted": stage_report.accepted,
        "notes": list(stage_report.notes),
        "snapshot_files": snapshot_files,
        "extra_artifacts": extra_artifacts,
    }
    proof_path.write_text(json.dumps(proof_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    workspace.stage_artifacts.append(
        {
            "stage": stage_report.stage,
            "stage_dir": str(stage_dir),
            "proof_path": str(proof_path),
            "snapshot_files": snapshot_files,
            "extra_artifacts": extra_artifacts,
            "acceptance_checks": list(stage_report.acceptance_checks),
            "accepted": stage_report.accepted,
            "proofs": dict(stage_report.proofs),
        }
    )


def _build_stage_artifact_manifest(workspace: FinalizerWorkspace) -> dict[str, object]:
    if workspace.artifact_root is None:
        return {"enabled": False, "stages": []}

    manifest_path = workspace.artifact_root / "manifest.json"
    payload = {
        "enabled": True,
        "artifact_root": str(workspace.artifact_root),
        "stage_sequence": [item["stage"] for item in workspace.stage_artifacts],
        "stages": workspace.stage_artifacts,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["manifest_path"] = str(manifest_path)
    return payload


def _stage_extract_proof(root_dir: Path, opf_path: Path) -> dict[str, object]:
    return {
        "container_present": (root_dir / "META-INF" / "container.xml").exists(),
        "opf_present": opf_path.exists(),
        "mimetype_present": (root_dir / "mimetype").exists(),
    }


def _stage_css_proof(opf_path: Path, css_path: Path) -> dict[str, object]:
    root = etree.parse(str(opf_path)).getroot()
    css_refs = [
        (item.get("href") or "")
        for item in root.findall(".//opf:manifest/opf:item", NS)
        if item.get("media-type") == "text/css"
    ]
    return {
        "css_exists": css_path.exists(),
        "css_manifest_ref_present": bool(css_refs),
    }


def _stage_semantic_plan_proof(processed_chapters: dict[Path, ProcessedChapter], toc_entries: list[dict]) -> dict[str, object]:
    heading_count = 0
    chapter_profile_counts: Counter[str] = Counter()
    for chapter_path, processed in processed_chapters.items():
        if not chapter_path.exists():
            continue
        soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
        heading_count += len(soup.find_all(["h1", "h2", "h3"]))
        chapter_profile_counts[processed.chapter_profile] += 1
    return {
        "semantic_plan_ready": len(processed_chapters) > 0,
        "chapter_count": len(processed_chapters),
        "heading_count": heading_count > 0,
        "toc_entries_present": len(toc_entries) > 0,
        "title_page_non_empty": chapter_profile_counts["article"] > 0,
        "chapter_profile_counts": dict(chapter_profile_counts),
        "special_profile_count": sum(
            count for profile, count in chapter_profile_counts.items() if profile != "article"
        ),
    }


def _stage_semantic_apply_proof(workspace: FinalizerWorkspace) -> dict[str, object]:
    title_path = workspace.root_dir / "EPUB" / "title.xhtml"
    title_page_non_empty = False
    if title_path.exists():
        soup = BeautifulSoup(title_path.read_text(encoding="utf-8"), "xml")
        title_page_non_empty = bool(soup.find("h1") or soup.find("p"))

    chapter_profile_counts: Counter[str] = Counter(
        chapter.chapter_profile for chapter in workspace.processed.values()
    )
    return {
        "semantic_plan_consumed": workspace.semantic_plan is not None,
        "chapter_writes_match_plan": len(workspace.processed) == len(workspace.semantic_plan.processed) if workspace.semantic_plan else False,
        "title_page_non_empty": title_page_non_empty,
        "cover_page_state_valid": True,
        "written_chapter_count": len(workspace.processed),
        "toc_entries_present": len(workspace.toc_entries) > 0,
        "chapter_profile_counts": dict(chapter_profile_counts),
        "special_profile_count": sum(
            count for profile, count in chapter_profile_counts.items() if profile != "article"
        ),
    }


def _resolve_epub_target(base_dir: Path, href: str) -> Path:
    href_path = (href or "").split("#", 1)[0].strip()
    if not href_path:
        return base_dir
    return (base_dir / href_path).resolve()


def _stage_navigation_proof(opf_path: Path, *, toc_entries: list[dict]) -> dict[str, object]:
    nav_path = opf_path.parent / "nav.xhtml"
    toc_path = opf_path.parent / "toc.ncx"
    semantic_toc_entry_count = len(toc_entries)
    semantic_top_toc_entry_count = len([entry for entry in toc_entries if entry.get("level", 0) <= 2])
    nav_entry_count = 0
    toc_entry_count = 0
    nav_target_count = 0
    toc_target_count = 0
    nav_dead_target_count = 0
    toc_dead_target_count = 0
    nav_labels: list[str] = []
    toc_labels: list[str] = []

    if nav_path.exists():
        soup = BeautifulSoup(nav_path.read_text(encoding="utf-8"), "xml")
        base_dir = nav_path.parent
        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue
            nav_entry_count += 1
            nav_target_count += 1
            nav_labels.append(" ".join(anchor.get_text(" ", strip=True).split()))
            target_path = _resolve_epub_target(base_dir, href)
            if not target_path.exists():
                nav_dead_target_count += 1
                continue
            _, _, fragment = href.partition("#")
            if fragment:
                target_soup = BeautifulSoup(target_path.read_text(encoding="utf-8"), "xml")
                if target_soup.find(id=fragment) is None:
                    nav_dead_target_count += 1
    if toc_path.exists():
        toc_root = etree.parse(str(toc_path)).getroot()
        base_dir = toc_path.parent
        for content in toc_root.findall(".//{http://www.daisy.org/z3986/2005/ncx/}content"):
            src = (content.get("src") or "").strip()
            if not src:
                continue
            toc_entry_count += 1
            toc_target_count += 1
            toc_labels.append(" ".join((content.getparent().findtext("{http://www.daisy.org/z3986/2005/ncx/}navLabel/{http://www.daisy.org/z3986/2005/ncx/}text", default="") or "").split()))
            target_path = _resolve_epub_target(base_dir, src)
            if not target_path.exists():
                toc_dead_target_count += 1
                continue
            _, _, fragment = src.partition("#")
            if fragment:
                target_soup = BeautifulSoup(target_path.read_text(encoding="utf-8"), "xml")
                if target_soup.find(id=fragment) is None:
                    toc_dead_target_count += 1
    return {
        "nav_exists": nav_path.exists(),
        "toc_exists": toc_path.exists(),
        "semantic_toc_entry_count": semantic_toc_entry_count,
        "semantic_top_toc_entry_count": semantic_top_toc_entry_count,
        "nav_entry_count": nav_entry_count,
        "toc_entry_count": toc_entry_count,
        "nav_target_count": nav_target_count,
        "toc_target_count": toc_target_count,
        "nav_dead_target_count": nav_dead_target_count,
        "toc_dead_target_count": toc_dead_target_count,
        "navigation_survives_semantic_rebuild": (
            nav_path.exists()
            and toc_path.exists()
            and semantic_top_toc_entry_count > 0
            and nav_entry_count == semantic_top_toc_entry_count
            and toc_entry_count == semantic_top_toc_entry_count
            and nav_dead_target_count == 0
            and toc_dead_target_count == 0
        ),
        "stage_integrity_ok": (
            nav_path.exists()
            and toc_path.exists()
            and nav_entry_count == toc_entry_count
            and nav_entry_count == semantic_top_toc_entry_count
            and nav_dead_target_count == 0
            and toc_dead_target_count == 0
        ),
        "nav_labels_sample": nav_labels[:6],
        "toc_labels_sample": toc_labels[:6],
    }


def _stage_metadata_proof(opf_path: Path, *, title: str, author: str, language: str) -> dict[str, object]:
    root = etree.parse(str(opf_path)).getroot()
    actual_title = (root.findtext(".//dc:title", default="", namespaces=NS) or "").strip()
    actual_creator = (root.findtext(".//dc:creator", default="", namespaces=NS) or "").strip()
    actual_language = (root.findtext(".//dc:language", default="", namespaces=NS) or "").strip()
    return {
        "title_matches": actual_title == title,
        "creator_matches": actual_creator == author,
        "language_matches": actual_language == language,
    }


def _stage_packaging_proof(root_dir: Path) -> dict[str, object]:
    title_path = root_dir / "EPUB" / "title.xhtml"
    title_page_non_empty = False
    if title_path.exists():
        soup = BeautifulSoup(title_path.read_text(encoding="utf-8"), "xml")
        title_page_non_empty = bool(soup.find("h1") or soup.find("p"))
    return {
        "title_page_non_empty": title_page_non_empty,
        "nav_exists": (root_dir / "EPUB" / "nav.xhtml").exists(),
        "ncx_exists": (root_dir / "EPUB" / "toc.ncx").exists(),
    }


EXPECTED_FINALIZER_STAGE_SEQUENCE = [
    "extract",
    "css_normalization",
    "semantic_planning",
    "semantic_apply",
    "navigation_rebuild",
    "metadata_normalization",
    "packaging",
]

FINALIZER_STAGE_DEFINITIONS: dict[str, FinalizerStageDefinition] = {
    "extract": FinalizerStageDefinition(
        stage="extract",
        dependencies=(),
        acceptance_checks=("container_present", "opf_present", "mimetype_present"),
    ),
    "css_normalization": FinalizerStageDefinition(
        stage="css_normalization",
        dependencies=("extract",),
        acceptance_checks=("css_exists", "css_manifest_ref_present"),
    ),
    "semantic_planning": FinalizerStageDefinition(
        stage="semantic_planning",
        dependencies=("css_normalization",),
        acceptance_checks=("semantic_plan_ready", "heading_count", "toc_entries_present"),
    ),
    "semantic_apply": FinalizerStageDefinition(
        stage="semantic_apply",
        dependencies=("semantic_planning",),
        acceptance_checks=(
            "semantic_plan_consumed",
            "chapter_writes_match_plan",
            "title_page_non_empty",
            "cover_page_state_valid",
            "toc_entries_present",
        ),
    ),
    "navigation_rebuild": FinalizerStageDefinition(
        stage="navigation_rebuild",
        dependencies=("semantic_apply",),
        acceptance_checks=("nav_exists", "toc_exists", "navigation_survives_semantic_rebuild", "stage_integrity_ok"),
    ),
    "metadata_normalization": FinalizerStageDefinition(
        stage="metadata_normalization",
        dependencies=("navigation_rebuild",),
        acceptance_checks=("title_matches", "creator_matches", "language_matches"),
    ),
    "packaging": FinalizerStageDefinition(
        stage="packaging",
        dependencies=("metadata_normalization",),
        acceptance_checks=("title_page_non_empty", "nav_exists", "ncx_exists"),
    ),
}


def _serialize_finalizer_report(
    stage_reports: list[FinalizerStageReport],
    *,
    artifact_manifest: dict[str, object] | None = None,
) -> dict[str, object]:
    serialized = []
    final_pass = True
    stage_sequence = [stage.stage for stage in stage_reports]
    for stage in stage_reports:
        serialized.append(
            {
                "stage": stage.stage,
                "proofs": stage.proofs,
                "acceptance_checks": list(stage.acceptance_checks),
                "accepted": stage.accepted,
                "notes": list(stage.notes),
            }
        )
        final_pass = final_pass and all(value for value in stage.proofs.values() if isinstance(value, bool))
    stage_sequence_valid = stage_sequence == EXPECTED_FINALIZER_STAGE_SEQUENCE[: len(stage_sequence)]
    navigation_stage = next((stage for stage in serialized if stage["stage"] == "navigation_rebuild"), None)
    navigation_quality_proof = navigation_stage["proofs"] if navigation_stage is not None else {}
    acceptance_boundaries_ok = all(stage["accepted"] for stage in serialized)
    stage_integrity = {
        "stage_sequence": stage_sequence,
        "expected_stage_sequence": EXPECTED_FINALIZER_STAGE_SEQUENCE[: len(stage_sequence)],
        "stage_sequence_valid": stage_sequence_valid,
        "acceptance_boundaries_ok": acceptance_boundaries_ok,
        "navigation_stage_present": navigation_stage is not None,
        "navigation_stage_integrity_ok": bool(navigation_quality_proof.get("stage_integrity_ok")) if navigation_quality_proof else False,
    }
    final_pass = (
        final_pass
        and stage_sequence_valid
        and acceptance_boundaries_ok
        and stage_integrity["navigation_stage_integrity_ok"]
    )
    return {
        "stages": serialized,
        "stage_sequence": stage_sequence,
        "stage_integrity": stage_integrity,
        "navigation_quality_proof": navigation_quality_proof,
        "artifact_manifest": artifact_manifest or {"enabled": False, "stages": []},
        "final_pass": final_pass,
    }


def _set_dc_value(metadata: etree._Element, local_name: str, value: str) -> None:
    element = metadata.find(f"dc:{local_name}", NS)
    if element is None:
        element = etree.SubElement(metadata, f"{{{DC_NS}}}{local_name}")
    element.text = value


def _upsert_modified_timestamp(metadata: etree._Element) -> None:
    modified = None
    for meta in metadata.findall(f"{{{OPF_NS}}}meta"):
        if meta.get("property") == "dcterms:modified":
            modified = meta
            break
    if modified is None:
        modified = etree.SubElement(metadata, f"{{{OPF_NS}}}meta")
        modified.set("property", "dcterms:modified")
    modified.text = "2026-04-09T00:00:00Z"


def _mark_cover_image(root: etree._Element) -> None:
    manifest = root.find(".//opf:manifest", NS)
    if manifest is None:
        return
    for item in manifest.findall("opf:item", NS):
        href = item.get("href", "")
        item_id = item.get("id", "")
        if "cover" in href.lower() and item.get("media-type", "").startswith("image/"):
            item.set("properties", "cover-image")
        if item_id == "cover-image" and item.get("media-type", "").startswith("image/"):
            item.set("properties", "cover-image")


def _ensure_nav_manifest_property(root: etree._Element) -> None:
    manifest = root.find(".//opf:manifest", NS)
    if manifest is None:
        return
    for item in manifest.findall("opf:item", NS):
        href = item.get("href", "")
        if href.endswith("nav.xhtml"):
            item.set("properties", "nav")


def _build_nav_xhtml(*, toc_entries: list[dict], title: str, language: str, css_href: str) -> str:
    top_entries = [entry for entry in toc_entries if entry["level"] <= 2]
    list_items = []
    for entry in top_entries:
        href = f'{entry["file_name"]}#{entry["id"]}'
        label = html.escape(entry["text"])
        list_items.append(f'<li><a href="{href}">{label}</a></li>')

    if not list_items:
        list_items.append('<li><a href="title.xhtml#title-page">Start</a></li>')

    nav_body = (
        '<nav epub:type="toc" id="toc">'
        f"<h1>{html.escape(title)}</h1>"
        "<ol>"
        + "".join(list_items)
        + "</ol></nav>"
    )
    return _build_xhtml_document(title="Spis treści", body_html=nav_body, language=language, css_href=css_href)


def _build_toc_ncx(*, toc_entries: list[dict], title: str) -> str:
    top_entries = [entry for entry in toc_entries if entry["level"] <= 2]
    if not top_entries:
        top_entries = [{"file_name": "title.xhtml", "id": "title-page", "text": "Start", "level": 1}]

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">',
        "  <head>",
        '    <meta name="dtb:uid" content="kindlemaster-cleanup"/>',
        '    <meta name="dtb:depth" content="2"/>',
        '    <meta name="dtb:totalPageCount" content="0"/>',
        '    <meta name="dtb:maxPageNumber" content="0"/>',
        "  </head>",
        f"  <docTitle><text>{html.escape(title)}</text></docTitle>",
        "  <navMap>",
    ]
    play_order = 1
    for entry in top_entries:
        href = entry["file_name"] + (f'#{entry["id"]}' if entry["id"] else "")
        label = html.escape(entry["text"])
        lines.append(f'    <navPoint id="navPoint-{play_order}" playOrder="{play_order}">')
        lines.append(f"      <navLabel><text>{label}</text></navLabel>")
        lines.append(f'      <content src="{html.escape(href)}"/>')
        lines.append("    </navPoint>")
        play_order += 1
    lines.extend(["  </navMap>", "</ncx>"])
    return "\n".join(lines)


def _build_xhtml_document(
    *,
    title: str,
    body_html: str,
    language: str,
    css_href: str = "styles/baseline.css",
    body_attrs: str = "",
) -> str:
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<!DOCTYPE html>\n"
        f'<html xmlns="{XHTML_NS}" xmlns:epub="http://www.idpf.org/2007/ops" '
        'epub:prefix="z3998: http://www.daisy.org/z3998/2012/vocab/structure/#" '
        f'lang="{html.escape(language)}" xml:lang="{html.escape(language)}">\n'
        "  <head>\n"
        f"    <title>{html.escape(title)}</title>\n"
        f'    <link href="{html.escape(css_href)}" rel="stylesheet" type="text/css"/>\n'
        "  </head>\n"
        f"  <body{body_attrs}>{body_html}</body>\n"
        "</html>\n"
    )


def _serialize_soup_document(soup: BeautifulSoup) -> str:
    html_tag = soup.find("html")
    if html_tag is not None:
        html_tag["xmlns"] = XHTML_NS
        html_tag["xmlns:epub"] = "http://www.idpf.org/2007/ops"
        html_tag["epub:prefix"] = "z3998: http://www.daisy.org/z3998/2012/vocab/structure/#"
    document = str(soup)
    document = re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", document, count=1)
    document = re.sub(r"^\s*<!DOCTYPE[^>]*>\s*", "", document, count=1, flags=re.IGNORECASE)
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<!DOCTYPE html>\n"
        + document
    )


def _prune_toc_entries(toc_entries: list[dict]) -> list[dict]:
    if len(toc_entries) <= 12:
        return toc_entries

    section_groups = [entry.get("section_label", "").strip() for entry in toc_entries if entry.get("section_label")]
    if len(set(section_groups)) < 4:
        return toc_entries

    def section_limit(label: str) -> int:
        lowered = (label or "").lower()
        if any(token in lowered for token in ("power skills", "ways of working", "business acumen", "temat numeru")):
            return 2
        if lowered:
            return 1
        return 2

    kept: list[dict] = []
    section_counts: Counter[str] = Counter()
    for entry in toc_entries:
        section_label = (entry.get("section_label") or "").strip()
        if section_counts[section_label] >= section_limit(section_label):
            continue
        kept.append(entry)
        section_counts[section_label] += 1

    return kept or toc_entries


def _repeated_text_action(text: str, *, repeated_counts: Counter, title: str, author: str, is_top: bool) -> str | None:
    normalized_key = _normalize_key(text)
    if normalized_key in {_normalize_key(title), _normalize_key(author)}:
        return "drop"
    count = repeated_counts.get(text, 0)
    if count < 4:
        return None
    if _matches_signal_term(text, SPECIAL_SECTION_TERMS + PROMO_SECTION_TERMS + TOC_SECTION_TERMS + PROMO_BANNER_TERMS):
        return "drop"
    if PAGE_SPAN_RE.search(text):
        return "drop"
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
    if PAGE_LABEL_RE.match(text):
        return False
    if NUMBERED_SECTION_RE.match(text):
        return False
    if BIBLIOGRAPHY_RE.match(text):
        return False
    if _is_true_solution_entry(text):
        return False
    if _looks_like_chess_heading_noise(text):
        return False
    if _looks_like_caption_or_credit_heading(text):
        return False
    if _looks_like_toc_teaser_line(text):
        return False
    if _looks_like_person_name_line(text):
        return False
    if PAGE_NUMBER_RE.match(text):
        return False
    if any(term in lower for term in BAD_HEADING_TERMS):
        return False
    if _matches_signal_term(text, TOC_SECTION_TERMS + PROMO_BANNER_TERMS):
        return False
    if text.endswith((".", "?", "!")) and len(text.split()) > 4:
        return False
    words = [word for word in re.split(r"\s+", text.replace("–", " ")) if any(ch.isalpha() for ch in word)]
    if not words:
        return False
    if len(words) == 1 and len(text) < 18:
        return False
    capitalized = sum(1 for word in words if word[0].isupper())
    ratio = capitalized / len(words)
    return ratio >= 0.55


def _looks_like_section_banner_text(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) > 70:
        return False
    if not _matches_signal_term(cleaned, SPECIAL_SECTION_TERMS + PROMO_SECTION_TERMS + TOC_SECTION_TERMS + PROMO_BANNER_TERMS):
        return False

    words = [word for word in re.split(r"\s+", cleaned.replace("â€“", " ").replace("â€”", " ")) if any(ch.isalpha() for ch in word)]
    if not words:
        return False

    alpha_chars = [ch for ch in cleaned if ch.isalpha()]
    upper_ratio = sum(1 for ch in alpha_chars if ch.isupper()) / len(alpha_chars) if alpha_chars else 0
    return upper_ratio >= 0.55 or len(words) <= 4


def _looks_like_spaced_banner_text(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    words = [word for word in re.split(r"\s+", cleaned) if any(ch.isalpha() for ch in word)]
    if len(words) < 4:
        return False
    alpha_lengths = [sum(1 for ch in word if ch.isalpha()) for word in words]
    if not alpha_lengths:
        return False
    single_letter_words = sum(1 for length in alpha_lengths if length <= 1)
    average_length = sum(alpha_lengths) / len(alpha_lengths)
    return single_letter_words >= max(3, len(alpha_lengths) // 2) or average_length <= 1.6


def _looks_like_opening_support_line(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if cleaned.startswith(("â€”", "-")):
        return True
    if _looks_like_person_name_line(cleaned):
        return True
    if _looks_like_person_role_line(cleaned):
        return True
    if len(cleaned) >= 60 and cleaned[:1].isupper() and not PAGE_LABEL_RE.match(cleaned):
        return True
    return False


def _looks_like_primary_title_candidate(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if len(cleaned) > 90:
        return False
    if PAGE_LABEL_RE.match(cleaned):
        return False
    if NUMBERED_SECTION_RE.match(cleaned):
        return False
    if BIBLIOGRAPHY_RE.match(cleaned):
        return False
    if PAGE_SPAN_RE.search(cleaned):
        return False
    if _looks_like_person_name_line(cleaned):
        return False
    if _looks_like_person_role_line(cleaned):
        return False
    if _looks_like_spaced_banner_text(cleaned):
        return False
    if _looks_like_chess_heading_noise(cleaned):
        return False
    if _looks_like_caption_or_credit_heading(cleaned):
        return False
    if _looks_like_toc_teaser_line(cleaned):
        return False
    if _matches_signal_term(cleaned, TOC_SECTION_TERMS + PROMO_BANNER_TERMS + FRONT_MATTER_HINT_TERMS):
        return False
    lowered = cleaned.lower()
    if lowered in {"redaktor działu", "redaktorka działu", "o- o", "o-o", "o-o-o", "o- o- o"}:
        return False
    if lowered.startswith("newsweek:"):
        return False
    if cleaned[:1].islower():
        return False
    if cleaned.startswith(("—", "-")):
        return False
    if cleaned.endswith("."):
        return False
    words = [word for word in re.split(r"\s+", cleaned) if any(ch.isalpha() for ch in word)]
    if len(words) < 3 and not cleaned.endswith(("?", "!")):
        return False
    if cleaned.endswith(("”", "\"")) and len(words) > 12:
        return False
    if "?" in cleaned and len(words) > 12 and not cleaned.endswith("?"):
        return False

    alpha_chars = [ch for ch in cleaned if ch.isalpha()]
    if alpha_chars:
        upper_ratio = sum(1 for ch in alpha_chars if ch.isupper()) / len(alpha_chars)
        if upper_ratio >= 0.82 and len(words) <= 6:
            return False
    if any(term in lowered for term in SPECIAL_SECTION_TERMS) and _looks_like_section_banner_text(cleaned):
        return False
    return True


def _starts_with_lowercase_continuation(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    for char in cleaned:
        if not char.isalpha():
            continue
        return char.islower()
    return False


def _looks_like_person_name_line(text: str) -> bool:
    if not text or len(text) > 80 or any(char.isdigit() for char in text):
        return False
    cleaned = text.replace("—", " ").replace("–", " ").replace("-", " ")
    words = [word for word in re.split(r"\s+", cleaned) if any(ch.isalpha() for ch in word)]
    if len(words) < 2 or len(words) > 4:
        return False
    if any(word.isupper() for word in words):
        return False
    allowed_starters = {"dr", "mgr", "prof", "jr", "sr"}
    capitalized_words = 0
    for word in words:
        normalized = word.strip(".").lower()
        if normalized in allowed_starters:
            capitalized_words += 1
            continue
        if word[:1].isupper() and word[1:].islower():
            capitalized_words += 1
    return capitalized_words == len(words)


def _looks_like_caption_or_credit_heading(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered.startswith(("issn", "fot.", "fotografie", "fotografia", "rys.", "źródło", "source:", "pic.")):
        return True
    return bool(re.match(r"^(?:rys|fig|pic|fot)\.?\s*\d+", lowered))


def _looks_like_person_role_line(text: str) -> bool:
    if not text or len(text) > 140:
        return False
    lower = text.lower()
    if not any(term in lower for term in PERSON_ROLE_TERMS):
        return False
    words = [word for word in re.split(r"\s+", text) if any(ch.isalpha() for ch in word)]
    if len(words) < 3:
        return False
    alpha_chars = [ch for ch in text if ch.isalpha()]
    if not alpha_chars:
        return False
    upper_ratio = sum(1 for ch in alpha_chars if ch.isupper()) / len(alpha_chars)
    return upper_ratio >= 0.6


def _looks_like_chess_heading_noise(text: str) -> bool:
    if not text or len(text) < 20:
        return False
    move_number_count = len(re.findall(r"\b\d+\.(?:\.\.)?", text))
    castle_count = len(re.findall(r"\bO-\s?O(?:-\s?O)?\b", text))
    move_token_count = len(
        re.findall(
            r"\b(?:[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?|[KQRBN][a-zA-Z0-9x=+#\-]+)\b",
            text,
        )
    )
    return move_number_count >= 2 or castle_count >= 1 or move_token_count >= 5


def _should_include_nav_entry(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if len(cleaned) > 90:
        return False
    if PAGE_LABEL_RE.match(cleaned):
        return False
    if NUMBERED_SECTION_RE.match(cleaned):
        return False
    if BIBLIOGRAPHY_RE.match(cleaned):
        return False
    if PAGE_SPAN_RE.search(cleaned):
        return False
    if _looks_like_person_name_line(cleaned):
        return False
    if _looks_like_person_role_line(cleaned):
        return False
    if _looks_like_spaced_banner_text(cleaned):
        return False
    if _looks_like_chess_heading_noise(cleaned):
        return False
    if _looks_like_caption_or_credit_heading(cleaned):
        return False
    if _looks_like_toc_teaser_line(cleaned):
        return False
    lowered = cleaned.lower()
    if lowered in {"redaktor działu", "redaktorka działu", "o- o", "o-o", "o-o-o", "o- o- o"}:
        return False
    if lowered.startswith("newsweek:"):
        return False
    if any(term in lowered for term in SPECIAL_SECTION_TERMS):
        return False
    if _matches_signal_term(cleaned, TOC_SECTION_TERMS + PROMO_BANNER_TERMS + FRONT_MATTER_HINT_TERMS):
        return False
    words = [word for word in re.split(r"\s+", cleaned) if any(ch.isalpha() for ch in word)]
    if len(words) <= 1:
        return False
    if len(words) < 3 and not cleaned.endswith(("?", "!")) and ":" not in cleaned:
        return False
    if cleaned.endswith(("”", "\"")) and len(words) > 12:
        return False
    if "?" in cleaned and len(words) > 12 and not cleaned.endswith("?"):
        return False
    return True


def _should_merge_paragraphs(previous_block: dict, current_block: dict) -> bool:
    previous_text = previous_block["text"].strip()
    current_text = current_block["text"].strip()
    previous_class = (previous_block.get("class_name") or "").strip()
    current_class = (current_block.get("class_name") or "").strip()
    if not previous_text or not current_text:
        return False
    if detect_inline_ordered_list(previous_text) or detect_inline_ordered_list(current_text):
        return False
    if previous_class in {"byline", "section-banner"} and current_class != previous_class:
        return False
    if current_class in {"byline", "section-banner"} and previous_class != current_class:
        return False
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
    return bool(re.match(r"^(?:\d+\.\.\.|\d+\.|[KQRBNa-hO][A-Za-z0-9x=+#†‡!?+\-–/]+)", text))


def _merge_separator(previous_text: str, current_text: str) -> str:
    if previous_text.endswith("-") and current_text[:1].isalpha():
        return ""
    if previous_text.endswith(("(", "/")) or current_text.startswith((")", ",", ".", ";", ":", "!", "?")):
        return ""
    return " "


def _repair_high_confidence_hyphen_splits(text: str) -> str:
    if not text or "-" not in text:
        return text

    def repl(match: re.Match[str]) -> str:
        left = match.group("left")
        right = match.group("right")
        if left.islower() and right.islower():
            return f"{left}{right}"
        if left[:1].isupper() and left[1:].islower() and right.islower():
            return f"{left}{right}"
        return match.group(0)

    return LETTER_FRAGMENT_RE.sub(repl, text)


def _repair_high_confidence_hyphen_splits_in_html(fragment_html: str) -> str:
    if not fragment_html or "-" not in fragment_html:
        return fragment_html

    parts = re.split(r"(<[^>]+>)", fragment_html)
    repaired_parts = []
    for part in parts:
        if not part or part.startswith("<"):
            repaired_parts.append(part)
            continue
        repaired_parts.append(_repair_high_confidence_hyphen_splits(part))
    return "".join(repaired_parts)


def _normalize_text(text: str) -> str:
    normalized = html.unescape(text or "")
    for broken, fixed in MOJIBAKE_MAP.items():
        normalized = normalized.replace(broken, fixed)
    normalized = normalized.replace("\u00ad", "")
    normalized = normalized.replace("\xa0", " ")
    normalized = EMAIL_RE.sub("", normalized)
    normalized = _repair_high_confidence_hyphen_splits(normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"(?<=\w)([✓†‡])", r" \1", normalized)
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    return normalized.strip()


def _sanitize_inline_html(fragment_html: str) -> str:
    normalized = fragment_html or ""
    for broken, fixed in MOJIBAKE_MAP.items():
        normalized = normalized.replace(broken, fixed)
    normalized = normalized.replace("\u00ad", "")
    normalized = normalized.replace("&nbsp;", " ")
    normalized = EMAIL_RE.sub("", normalized)
    normalized = _repair_high_confidence_hyphen_splits_in_html(normalized)
    normalized = re.sub(r"[ \t\r\n]+", " ", normalized)
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    return normalized.strip()


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


def _slugify(text: str) -> str:
    asciiish = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[-\s]+", "-", asciiish).strip("-")
    return slug or "section"


def _normalize_key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _compact_signal_key(text: str) -> str:
    normalized = _normalize_key(_normalize_text(text))
    return re.sub(r"[^0-9a-ząćęłńóśźż]+", "", normalized)


def _matches_signal_term(text: str, terms: tuple[str, ...]) -> bool:
    normalized = _normalize_key(_normalize_text(text))
    compact = _compact_signal_key(text)
    for term in terms:
        normalized_term = _normalize_key(term)
        if normalized_term and normalized_term in normalized:
            return True
        compact_term = re.sub(r"[^0-9a-ząćęłńóśźż]+", "", normalized_term)
        if compact_term and compact_term in compact:
            return True
    return False


def _first_nonempty(*value_groups, default: str) -> str:
    for values in value_groups:
        if isinstance(values, (str, bytes)):
            values = [values]
        for value in values:
            if value:
                return value
    return default
