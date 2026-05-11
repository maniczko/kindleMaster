from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PublicationBlock:
    block_type: str
    text: str = ""
    raw_html: str = ""
    bbox: tuple[float, float, float, float] | None = None
    page_index: int = 0
    confidence: float = 1.0
    source_type: str = "text"
    level: int = 0
    style_class: str | None = None
    asset_name: str | None = None
    alt_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_type": self.block_type,
            "text": self.text,
            "page_index": self.page_index,
            "confidence": round(self.confidence, 3),
            "source_type": self.source_type,
            "level": self.level,
            "style_class": self.style_class,
            "asset_name": self.asset_name,
            "alt_text": self.alt_text,
            "metadata": self.metadata,
        }


@dataclass
class PublicationSection:
    section_id: str
    title: str
    level: int = 1
    kind: str = "section"
    confidence: float = 1.0
    page_start: int = 0
    page_end: int = 0
    blocks: list[PublicationBlock] = field(default_factory=list)
    assets: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "level": self.level,
            "kind": self.kind,
            "confidence": round(self.confidence, 3),
            "page_start": self.page_start,
            "page_end": self.page_end,
            "blocks": [block.to_dict() for block in self.blocks],
            "asset_count": len(self.assets),
            "metadata": self.metadata,
        }


@dataclass
class PublicationAnalysis:
    profile: str
    confidence: float
    page_count: int
    render_budget_class: str
    has_toc: bool
    has_tables: bool
    has_diagrams: bool
    has_meaningful_images: bool
    estimated_sections: int
    fallback_recommendation: str
    ui_profile: str
    legacy_strategy: str
    has_text_layer: bool
    is_scanned: bool
    layout_heavy: bool
    text_heavy: bool
    scanned_pages: int = 0
    text_pages: int = 0
    image_pages: int = 0
    estimated_columns: int = 1
    heading_density: float = 0.0
    font_consistency: float = 0.0
    detected_features: list[str] = field(default_factory=list)
    external_tools: dict[str, Any] = field(default_factory=dict)
    profile_reason: str = ""
    route_decision: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "confidence": round(self.confidence, 3),
            "page_count": self.page_count,
            "render_budget_class": self.render_budget_class,
            "has_toc": self.has_toc,
            "has_tables": self.has_tables,
            "has_diagrams": self.has_diagrams,
            "has_meaningful_images": self.has_meaningful_images,
            "estimated_sections": self.estimated_sections,
            "fallback_recommendation": self.fallback_recommendation,
            "ui_profile": self.ui_profile,
            "legacy_strategy": self.legacy_strategy,
            "has_text_layer": self.has_text_layer,
            "is_scanned": self.is_scanned,
            "layout_heavy": self.layout_heavy,
            "text_heavy": self.text_heavy,
            "scanned_pages": self.scanned_pages,
            "text_pages": self.text_pages,
            "image_pages": self.image_pages,
            "estimated_columns": self.estimated_columns,
            "heading_density": round(self.heading_density, 3),
            "font_consistency": round(self.font_consistency, 3),
            "detected_features": self.detected_features,
            "external_tools": self.external_tools,
            "profile_reason": self.profile_reason,
            "route_decision": self.route_decision,
        }


@dataclass
class PublicationQualityReport:
    section_count: int = 0
    figure_count: int = 0
    diagram_count: int = 0
    table_count: int = 0
    page_marker_count: int = 0
    detected_figures: int = 0
    detected_diagrams: int = 0
    detected_tables: int = 0
    fallback_pages: list[int] = field(default_factory=list)
    fallback_sections: list[str] = field(default_factory=list)
    fallback_regions: list[dict[str, Any]] = field(default_factory=list)
    high_risk_sections: list[dict[str, Any]] = field(default_factory=list)
    high_risk_pages: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    external_tools_used: dict[str, Any] = field(default_factory=dict)
    source_toc_entries: int = 0
    source_table_count: int = 0
    xhtml_table_count: int = 0
    table_cell_count: int = 0
    table_row_count: int = 0
    table_cell_coverage: float = 1.0
    table_page_count: int = 0
    multi_page_table_count: int = 0
    wide_table_count: int = 0
    low_confidence_table_count: int = 0
    fragment_table_count: int = 0
    table_summary: dict[str, Any] = field(default_factory=dict)
    figure_summary: dict[str, Any] = field(default_factory=dict)
    reading_flow: dict[str, Any] = field(default_factory=dict)
    ocr_quality: dict[str, Any] = field(default_factory=dict)
    tiny_tail_sections: list[dict[str, Any]] = field(default_factory=list)
    asset_budget_status: str = ""
    validation_status: str = "unavailable"
    validation_messages: list[str] = field(default_factory=list)
    validation_tool: str = "none"
    text_cleanup: dict[str, Any] = field(default_factory=dict)
    extractor_contract_warnings: list[dict[str, Any]] = field(default_factory=list)

    def content_metrics_dict(self) -> dict[str, Any]:
        return {
            "section_count": self.section_count,
            "figure_count": self.figure_count,
            "diagram_count": self.diagram_count,
            "table_count": self.table_count,
            "page_marker_count": self.page_marker_count,
            "detected_figures": self.detected_figures or self.figure_count,
            "detected_diagrams": self.detected_diagrams or self.diagram_count,
            "detected_tables": self.detected_tables or self.table_count,
            "fallback_pages": self.fallback_pages,
            "fallback_sections": self.fallback_sections,
            "fallback_regions": self.fallback_regions,
            "high_risk_sections": self.high_risk_sections,
            "high_risk_pages": self.high_risk_pages,
            "warnings": self.warnings,
            "external_tools_used": self.external_tools_used,
            "source_toc_entries": self.source_toc_entries,
            "source_table_count": self.source_table_count,
            "xhtml_table_count": self.xhtml_table_count,
            "table_cell_count": self.table_cell_count,
            "table_row_count": self.table_row_count,
            "table_cell_coverage": self.table_cell_coverage,
            "table_page_count": self.table_page_count,
            "multi_page_table_count": self.multi_page_table_count,
            "wide_table_count": self.wide_table_count,
            "low_confidence_table_count": self.low_confidence_table_count,
            "fragment_table_count": self.fragment_table_count,
            "table_summary": self.table_summary,
            "figure_summary": self.figure_summary,
            "reading_flow": self.reading_flow,
            "ocr_quality": self.ocr_quality,
            "tiny_tail_sections": self.tiny_tail_sections,
            "tiny_tail_section_count": len(self.tiny_tail_sections),
            "asset_budget_status": self.asset_budget_status,
            "text_cleanup": self.text_cleanup,
            "extractor_contract_warnings": self.extractor_contract_warnings,
        }

    def validation_payload(self) -> dict[str, Any]:
        return {
            "epubcheck_status": self.validation_status,
            "validation_status": self.validation_status,
            "validation_messages": self.validation_messages,
            "validation_tool": self.validation_tool,
        }

    def set_validation_result(self, validation: dict[str, Any]) -> None:
        self.validation_status = validation["status"]
        self.validation_messages = validation["messages"]
        self.validation_tool = validation["tool"]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.content_metrics_dict(),
            **self.validation_payload(),
            "ocr_quality": self.ocr_quality,
        }


@dataclass
class PublicationDocument:
    title: str
    author: str
    language: str
    profile: str
    analysis: PublicationAnalysis
    sections: list[PublicationSection] = field(default_factory=list)
    assets: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    quality_report: PublicationQualityReport = field(default_factory=PublicationQualityReport)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "language": self.language,
            "profile": self.profile,
            "analysis": self.analysis.to_dict(),
            "sections": [section.to_dict() for section in self.sections],
            "asset_count": len(self.assets),
            "metadata": self.metadata,
            "quality_report": self.quality_report.to_dict(),
        }
