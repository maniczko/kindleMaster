import io
import unittest

from PIL import Image

from converter import ConversionConfig
from magazine_kindle_reflow import (
    MagazineBlock,
    PageModel,
    _chapter_should_be_toc_excluded,
    _clean_article_title,
    _classify_page_content,
    _coalesce_fragile_chapters,
    _infer_publication_creator,
    _infer_publication_title,
    _optimize_image,
)


def _text_block(text: str, *, role: str = "body", page_number: int = 1) -> MagazineBlock:
    return MagazineBlock(
        kind="text",
        text=text,
        bbox=(40.0, 100.0, 520.0, 140.0),
        avg_font=10.0,
        max_font=10.0,
        page_number=page_number,
        role=role,
    )


def _page(
    index: int,
    *,
    title: str | None = None,
    body: str = "Treść artykułu.",
    content_type: str = "article",
) -> PageModel:
    blocks = []
    if title:
        blocks.append(_text_block(title, role="title", page_number=index + 1))
    blocks.append(_text_block(body, page_number=index + 1))
    return PageModel(
        page_index=index,
        page_label=str(index + 1),
        width=595.0,
        height=842.0,
        blocks=blocks,
        title=title,
        content_type=content_type,
        title_quality="strong" if title else "missing",
    )


class MagazineKindleReflowTests(unittest.TestCase):
    def test_coalesces_incomplete_article_tail_when_next_page_has_no_strong_title(self) -> None:
        chapters = [
            [_page(0, title="Pierwszy artykuł", body="Zespół musi widzieć, że")],
            [_page(1, title=None, body="kolejny akapit domyka rozpoczętą myśl.")],
        ]

        merged = _coalesce_fragile_chapters(chapters)

        self.assertEqual(len(merged), 1)
        self.assertEqual([page.page_index for page in merged[0]], [0, 1])

    def test_does_not_merge_incomplete_tail_into_next_strong_article(self) -> None:
        chapters = [
            [_page(0, title="Pierwszy artykuł", body="Zespół musi widzieć, że")],
            [_page(1, title="Drugi mocny artykuł", body="Nowy tekst ma własny tytuł.")],
        ]

        merged = _coalesce_fragile_chapters(chapters)

        self.assertEqual(len(merged), 2)

    def test_special_magazine_sections_are_not_primary_toc_entries(self) -> None:
        self.assertTrue(_chapter_should_be_toc_excluded("gallery"))
        self.assertTrue(_chapter_should_be_toc_excluded("advertisement"))
        self.assertTrue(_chapter_should_be_toc_excluded("sponsored"))
        self.assertTrue(_chapter_should_be_toc_excluded("newsletter"))
        self.assertTrue(_chapter_should_be_toc_excluded("contents"))
        self.assertFalse(_chapter_should_be_toc_excluded("article"))
        self.assertFalse(_chapter_should_be_toc_excluded("interview"))

    def test_newsletter_pages_are_classified_as_auxiliary_sections(self) -> None:
        model = _page(
            0,
            title="Dołącz do społeczności Strefy PMI",
            body="Newsletter, zapisz się i odbieraj rabaty oraz aktualności prosto na e-mail.",
        )
        text_blocks = [block for block in model.blocks if block.kind == "text"]

        page_type = _classify_page_content(model, text_blocks, [], 120)

        self.assertEqual(page_type, "newsletter")

    def test_article_title_cleanup_removes_author_tail_and_generic_suffix(self) -> None:
        self.assertEqual(
            _clean_article_title("Kiedy projekt nie rusza do przodu – 5 barier, które blokują zmiany — Jarosław Rubin,"),
            "Kiedy projekt nie rusza do przodu – 5 barier, które blokują zmiany",
        )
        self.assertEqual(
            _clean_article_title("Listening to Quiet, Learning When to Speak - Co to jest"),
            "Listening to Quiet, Learning When to Speak",
        )

    def test_issue_slug_title_is_normalized_for_magazine_metadata(self) -> None:
        self.assertEqual(
            _infer_publication_title([], {"title": "strefa-pmi-52-2026"}),
            "Strefa PMI 52/2026",
        )

    def test_creator_is_inferred_from_organization_masthead_not_editorial_role(self) -> None:
        pages = [
            _page(
                0,
                title=None,
                body="KWARTALNIK PROJECT MANAGEMENT INSTITUTE POLAND CHAPTER | WWW.STREFAPMI.PL | MARZEC 2026",
            )
        ]

        creator = _infer_publication_creator(pages, {"author": "Z-ca Redaktor Naczelnej"})

        self.assertEqual(creator, "Project Management Institute Poland Chapter")

    def test_magazine_images_are_saved_as_baseline_jpeg(self) -> None:
        image = Image.new("RGB", (64, 64), color=(220, 220, 220))
        raw = io.BytesIO()
        image.save(raw, format="PNG")

        optimized, ext = _optimize_image(raw.getvalue(), "png", ConversionConfig())

        self.assertEqual(ext, "jpeg")
        self.assertIn(b"\xff\xc0", optimized)
        self.assertNotIn(b"\xff\xc2", optimized)


if __name__ == "__main__":
    unittest.main()
