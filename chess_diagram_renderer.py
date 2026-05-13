"""
KindleMaster - Chess Diagram Renderer v2
========================================
Render chess diagrams as images instead of EPUB text spans.

This module targets the actual board representation used by
"The Woodpecker Method": 10-line Chess-Merida diagrams composed from
PUA glyphs. Detecting complete boards avoids false positives from
Wingdings arrows and inline figurines.
"""

import io
from dataclasses import dataclass

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageOps


CHESS_FONT_INDICATORS = [
    "chess",
    "merida",
    "leipzig",
    "casablanca",
    "skak",
    "alpha",
    "chesspieces",
    "chessbase",
    "chess-merida",
    "chess alpha",
]

CHESS_PUA_RANGES = [
    (0xF000, 0xF0FF),
    (0xF02B, 0xF0C9),
]

MERIDA_TOP_LEFT = "\uf031"
MERIDA_TOP_RIGHT = "\uf033"
MERIDA_BOTTOM_LEFT = "\uf037"
MERIDA_BOTTOM_RIGHT = "\uf039"

BOARD_LINE_COUNT = 10
BOARD_X_TOLERANCE = 3.0
BOARD_WIDTH_TOLERANCE = 28.0
BOARD_LINE_MIN_GAP = 10.0
BOARD_LINE_MAX_GAP = 25.0
BOARD_PADDING = 8.0
MAX_MERIDA_LINE_WIDTH = 240.0
SKAK_BOARD_LINE_COUNT = 8
SKAK_BOARD_MIN_WIDTH = 180.0
SKAK_TEXT_MIN_LENGTH = 6
SKAK_BOARD_MIN_GAP = 24.0
SKAK_BOARD_MAX_GAP = 36.0
SKAK_LEFT_LABEL_PADDING = 18.0
SKAK_BOTTOM_LABEL_PADDING = 18.0
SKAK_SIDE_PADDING = 8.0
CHESS_PNG_COLORS = 16
DARK_PIXEL_THRESHOLD = 232
BOARD_DENSITY_RATIO = 0.05
BOARD_BORDER_RUN_RATIO = 0.34
BOARD_BORDER_DENSITY_RATIO = 0.42


def is_chess_text(text: str, font_name: str) -> bool:
    """Return True when a span belongs to a chess font or contains chess PUA."""
    font_lower = (font_name or "").lower()
    if any(indicator in font_lower for indicator in CHESS_FONT_INDICATORS):
        return True

    for char in text or "":
        code = ord(char)
        for start, end in CHESS_PUA_RANGES:
            if start <= code <= end:
                return True

    return False


@dataclass
class ChessDiagramRegion:
    """A detected chess diagram region to render as a single image."""

    page_num: int
    bbox: tuple  # (x0, y0, x1, y1)
    text_span_indices: list[int]
    region_kind: str = "diagram"
    source_font: str = ""


@dataclass
class _BoardLineCandidate:
    """Synthetic board line built from one or more fragmented PDF spans."""

    page_num: int
    text: str
    font_name: str
    x: float
    y: float
    width: float
    height: float
    bbox: tuple
    source_indices: list[int]


def find_chess_diagram_regions(text_items: list) -> list[ChessDiagramRegion]:
    """
    Detect complete Chess-Merida boards.

    A board is represented by 10 consecutive wide text spans in the same
    column: top border, 8 ranks, bottom border.
    """
    candidates = _collect_board_line_candidates(text_items)
    if not candidates:
        return []

    merida_candidates = [
        (candidate.source_indices[0], candidate)
        for candidate in candidates
        if _looks_like_merida_board_line(candidate)
    ]
    skak_candidates = [
        (candidate.source_indices[0], candidate)
        for candidate in candidates
        if _looks_like_skak_board_line(candidate)
    ]

    regions = []
    regions.extend(_find_merida_regions(merida_candidates))
    regions.extend(_find_skak_regions(skak_candidates))
    return _dedupe_regions(regions)


def _item_index(item, fallback_idx: int) -> int:
    return getattr(item, "index", fallback_idx)


def _normalized_text(text: str) -> str:
    return (text or "").strip().replace("\n", "")


def _font_name(item) -> str:
    return getattr(item, "font_name", "") or ""


def _collect_board_line_candidates(text_items: list) -> list[_BoardLineCandidate]:
    """Merge fragmented chess-font spans that belong to one board row."""

    raw_spans = []
    for fallback_idx, item in enumerate(text_items):
        text = _normalized_text(getattr(item, "text", ""))
        if not text:
            continue
        font_name = _font_name(item)
        if not is_chess_text(text, font_name):
            continue

        raw_spans.append(
            {
                "idx": _item_index(item, fallback_idx),
                "text": text,
                "font_name": font_name,
                "x": item.x,
                "y": item.y,
                "width": item.width,
                "height": item.height,
                "bbox": getattr(item, "bbox", (item.x, item.y, item.x + item.width, item.y + item.height)),
                "page_num": getattr(item, "page_num", 0),
            }
        )

    if not raw_spans:
        return []

    raw_spans.sort(key=lambda span: (span["y"], span["x"], span["idx"]))
    merged = []
    row_tolerance = 1.5
    gap_tolerance = 20.0
    row_buffer = []

    def flush_row(spans_for_row: list[dict]) -> None:
        if not spans_for_row:
            return

        spans_for_row.sort(key=lambda span: span["x"])
        current_cluster = [spans_for_row[0]]

        for span in spans_for_row[1:]:
            prev = current_cluster[-1]
            prev_end = prev["bbox"][2]
            cluster_x0 = min(item["bbox"][0] for item in current_cluster)
            projected_width = span["bbox"][2] - cluster_x0
            if (
                span["x"] - prev_end <= gap_tolerance
                and projected_width <= MAX_MERIDA_LINE_WIDTH
            ):
                current_cluster.append(span)
            else:
                merged.append(_merge_line_cluster(current_cluster))
                current_cluster = [span]

        if current_cluster:
            merged.append(_merge_line_cluster(current_cluster))

    for span in raw_spans:
        if not row_buffer:
            row_buffer = [span]
            continue

        if abs(span["y"] - row_buffer[0]["y"]) <= row_tolerance:
            row_buffer.append(span)
        else:
            flush_row(row_buffer)
            row_buffer = [span]

    flush_row(row_buffer)
    merged.sort(key=lambda candidate: (round(candidate.x, 1), candidate.y, candidate.source_indices[0]))
    return merged


def _merge_line_cluster(cluster: list[dict]) -> _BoardLineCandidate:
    x0 = min(item["bbox"][0] for item in cluster)
    y0 = min(item["bbox"][1] for item in cluster)
    x1 = max(item["bbox"][2] for item in cluster)
    y1 = max(item["bbox"][3] for item in cluster)

    ordered = sorted(cluster, key=lambda item: item["x"])
    text = "".join(item["text"] for item in ordered)
    source_indices = [item["idx"] for item in ordered]

    return _BoardLineCandidate(
        page_num=ordered[0]["page_num"],
        text=text,
        font_name=ordered[0]["font_name"],
        x=x0,
        y=y0,
        width=x1 - x0,
        height=y1 - y0,
        bbox=(x0, y0, x1, y1),
        source_indices=source_indices,
    )


def _looks_like_merida_board_line(item) -> bool:
    text = _normalized_text(getattr(item, "text", ""))
    if not text:
        return False

    if _is_top_border(text) or _is_bottom_border(text):
        return True

    pua_count = sum(
        1
        for char in text
        if any(start <= ord(char) <= end for start, end in CHESS_PUA_RANGES)
    )
    font_lower = _font_name(item).lower()
    has_merida_hint = "merida" in font_lower or "chess" in font_lower
    return pua_count >= 6 and getattr(item, "width", 0) >= 150 and (has_merida_hint or pua_count >= 8)


def _looks_like_skak_board_line(item) -> bool:
    text = _normalized_text(getattr(item, "text", ""))
    if not text:
        return False

    font_lower = _font_name(item).lower()
    if "skak" not in font_lower:
        return False
    if getattr(item, "width", 0) < SKAK_BOARD_MIN_WIDTH:
        return False
    if len(text) < SKAK_TEXT_MIN_LENGTH:
        return False
    if any(ch.isspace() for ch in text):
        return False

    board_chars = sum(1 for char in text if char.isalnum() or char in "Z0")
    return board_chars / max(len(text), 1) >= 0.95


def _is_top_border(text: str) -> bool:
    cleaned = _normalized_text(text)
    return cleaned.startswith(MERIDA_TOP_LEFT) and MERIDA_TOP_RIGHT in cleaned


def _is_bottom_border(text: str) -> bool:
    cleaned = _normalized_text(text)
    return cleaned.startswith(MERIDA_BOTTOM_LEFT) and cleaned.endswith(MERIDA_BOTTOM_RIGHT)


def _same_board_column(first_item, second_item) -> bool:
    return (
        abs(first_item.x - second_item.x) <= BOARD_X_TOLERANCE
        and abs(first_item.width - second_item.width) <= BOARD_WIDTH_TOLERANCE
    )


def _find_merida_regions(board_spans: list[tuple[int, _BoardLineCandidate]]) -> list[ChessDiagramRegion]:
    if not board_spans:
        return []

    board_spans.sort(key=lambda pair: (round(pair[1].x, 1), pair[1].y, pair[0]))
    board_spans_by_y = sorted(board_spans, key=lambda pair: (pair[1].y, pair[1].x, pair[0]))
    regions = []
    used_indices = set()

    for start_idx, start_item in board_spans:
        if start_idx in used_indices or not _is_top_border(start_item.text):
            continue

        current_spans = [(start_idx, start_item)]
        prev_item = start_item

        for candidate_idx, candidate_item in board_spans_by_y:
            if candidate_idx in used_indices or candidate_idx == start_idx:
                continue
            if candidate_item.y <= prev_item.y:
                continue
            if not _same_board_column(start_item, candidate_item):
                continue

            y_gap = candidate_item.y - prev_item.y
            if not (BOARD_LINE_MIN_GAP <= y_gap <= BOARD_LINE_MAX_GAP):
                continue

            current_spans.append((candidate_idx, candidate_item))
            prev_item = candidate_item

            if len(current_spans) == BOARD_LINE_COUNT:
                break

        if len(current_spans) != BOARD_LINE_COUNT:
            continue
        if not _is_bottom_border(current_spans[-1][1].text):
            continue

        regions.append(
            _create_region_from_spans(
                current_spans,
                region_kind="diagram",
                source_font=start_item.font_name,
            )
        )
        used_indices.update(idx for idx, _ in current_spans)

    return regions


def _find_skak_regions(board_spans: list[tuple[int, _BoardLineCandidate]]) -> list[ChessDiagramRegion]:
    if not board_spans:
        return []

    board_spans.sort(key=lambda pair: (round(pair[1].x, 1), pair[1].y, pair[0]))
    board_spans_by_y = sorted(board_spans, key=lambda pair: (pair[1].y, pair[1].x, pair[0]))
    regions = []
    used_indices = set()

    for start_idx, start_item in board_spans:
        if start_idx in used_indices:
            continue

        current_spans = [(start_idx, start_item)]
        prev_item = start_item

        for candidate_idx, candidate_item in board_spans_by_y:
            if candidate_idx in used_indices or candidate_idx == start_idx:
                continue
            if candidate_item.y <= prev_item.y:
                continue
            if not _same_board_column(start_item, candidate_item):
                continue

            y_gap = candidate_item.y - prev_item.y
            if not (SKAK_BOARD_MIN_GAP <= y_gap <= SKAK_BOARD_MAX_GAP):
                continue

            current_spans.append((candidate_idx, candidate_item))
            prev_item = candidate_item

            if len(current_spans) == SKAK_BOARD_LINE_COUNT:
                break

        if len(current_spans) != SKAK_BOARD_LINE_COUNT:
            continue

        regions.append(
            _create_region_from_spans(
                current_spans,
                padding=(
                    SKAK_LEFT_LABEL_PADDING,
                    BOARD_PADDING,
                    SKAK_SIDE_PADDING,
                    SKAK_BOTTOM_LABEL_PADDING,
                ),
                region_kind="diagram",
                source_font=start_item.font_name,
            )
        )
        used_indices.update(idx for idx, _ in current_spans)

    return regions


def _dedupe_regions(regions: list[ChessDiagramRegion]) -> list[ChessDiagramRegion]:
    deduped: list[ChessDiagramRegion] = []
    seen_boxes: list[tuple[int, int, int, int]] = []
    for region in sorted(regions, key=lambda item: (item.page_num, item.bbox[1], item.bbox[0])):
        normalized_box = tuple(int(round(value)) for value in region.bbox)
        if normalized_box in seen_boxes:
            continue
        seen_boxes.append(normalized_box)
        deduped.append(region)
    return deduped


def _create_region_from_spans(
    spans: list,
    *,
    padding: tuple[float, float, float, float] | None = None,
    region_kind: str = "diagram",
    source_font: str = "",
) -> ChessDiagramRegion:
    indices = []
    items = [item for _, item in spans]
    for _, item in spans:
        indices.extend(getattr(item, "source_indices", [getattr(item, "index", 0)]))

    if padding is None:
        padding = (BOARD_PADDING, BOARD_PADDING, BOARD_PADDING, BOARD_PADDING)
    pad_left, pad_top, pad_right, pad_bottom = padding

    x0 = min(item.x for item in items) - pad_left
    y0 = min(item.y for item in items) - pad_top
    x1 = max(item.x + item.width for item in items) + pad_right
    y1 = max(item.y + item.height for item in items) + pad_bottom

    return ChessDiagramRegion(
        page_num=getattr(items[0], "page_num", 0),
        bbox=(x0, y0, x1, y1),
        text_span_indices=indices,
        region_kind=region_kind,
        source_font=source_font or getattr(items[0], "font_name", ""),
    )


def render_chess_diagram_to_png(
    page: fitz.Page,
    region: ChessDiagramRegion,
    dpi: int = 300,
    *,
    optimize: bool = True,
) -> tuple[bytes, int, int]:
    """Render a chess diagram region as a high-quality PNG."""

    clip_rect = fitz.Rect(*region.bbox) & page.rect
    zoom = dpi / 72.0
    pix = page.get_pixmap(
        matrix=fitz.Matrix(zoom, zoom),
        clip=clip_rect,
        alpha=False,
        colorspace=fitz.csRGB,
    )
    png_bytes = pix.tobytes("png")

    # Trim away captions and surrounding page debris. Keep the crop tight
    # instead of adding a square white canvas; coordinates and side-to-move
    # markers can legitimately make a diagram non-square, and extra canvas
    # reads as a frame while increasing PNG cost.
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    img = _crop_to_board_region(img)

    if optimize:
        optimized = _optimize_chess_diagram_image(img)
        return optimized, img.width, img.height

    output = io.BytesIO()
    img.save(output, format="PNG", optimize=True, compress_level=6)
    return output.getvalue(), img.width, img.height


def _optimize_chess_diagram_image(img: Image.Image) -> bytes:
    """
    Compress diagrams aggressively without sacrificing readability.

    Chess boards are grayscale assets with limited tonal variation, so a
    palette-optimized PNG is much smaller than a full RGB bitmap while
    remaining crisp in EPUB readers.
    """
    grayscale = ImageOps.grayscale(img)
    quantized = grayscale.quantize(colors=CHESS_PNG_COLORS, method=Image.MEDIANCUT, dither=Image.Dither.NONE)
    output = io.BytesIO()
    quantized.save(output, format="PNG", optimize=True, compress_level=9)
    return output.getvalue()


def _largest_dense_band(counts: list[int], minimum_count: int) -> tuple[int, int] | None:
    start = None
    bands = []

    for idx, count in enumerate(counts):
        if count >= minimum_count and start is None:
            start = idx
        elif count < minimum_count and start is not None:
            bands.append((start, idx - 1))
            start = None

    if start is not None:
        bands.append((start, len(counts) - 1))

    if not bands:
        return None

    return max(bands, key=lambda band: band[1] - band[0])


def _longest_dark_runs(gray: Image.Image) -> tuple[list[int], list[int], list[int], list[int]]:
    """Measure dark pixel density plus the longest continuous run per row/column."""
    pixels = gray.load()
    width, height = gray.size

    row_counts = [0] * height
    col_counts = [0] * width
    row_runs = [0] * height
    col_runs = [0] * width

    for y in range(height):
        row_dark = 0
        current_run = 0
        longest_run = 0
        for x in range(width):
            if pixels[x, y] < DARK_PIXEL_THRESHOLD:
                row_dark += 1
                current_run += 1
                col_counts[x] += 1
                if current_run > longest_run:
                    longest_run = current_run
            else:
                current_run = 0
        row_counts[y] = row_dark
        row_runs[y] = longest_run

    for x in range(width):
        current_run = 0
        longest_run = 0
        for y in range(height):
            if pixels[x, y] < DARK_PIXEL_THRESHOLD:
                current_run += 1
                if current_run > longest_run:
                    longest_run = current_run
            else:
                current_run = 0
        col_runs[x] = longest_run

    return row_counts, col_counts, row_runs, col_runs


def _contiguous_groups(indices: list[int]) -> list[tuple[int, int]]:
    if not indices:
        return []

    groups = []
    start = indices[0]
    prev = indices[0]
    for idx in indices[1:]:
        if idx == prev + 1:
            prev = idx
            continue
        groups.append((start, prev))
        start = idx
        prev = idx
    groups.append((start, prev))
    return groups


def _find_border_band(
    counts: list[int],
    runs: list[int],
    minimum_count: int,
    minimum_run: int,
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Find the first and last contiguous border bands for board lines."""
    groups = _find_border_groups(counts, runs, minimum_count, minimum_run)
    if len(groups) < 2:
        return None
    return groups[0], groups[-1]


def _find_border_groups(
    counts: list[int],
    runs: list[int],
    minimum_count: int,
    minimum_run: int,
) -> list[tuple[int, int]]:
    """Find contiguous border-line candidates."""
    candidates = [
        idx for idx, (count, run) in enumerate(zip(counts, runs))
        if count >= minimum_count and run >= minimum_run
    ]
    return _contiguous_groups(candidates)


def _select_board_border_box(
    row_groups: list[tuple[int, int]],
    col_groups: list[tuple[int, int]],
) -> tuple[int, int, int, int] | None:
    """Choose the square-like border pair for one board from possibly many boards."""
    best: tuple[float, int, int, int, int] | None = None

    for row_start_idx in range(len(row_groups)):
        for row_end_idx in range(row_start_idx + 1, len(row_groups)):
            top = row_groups[row_start_idx][0]
            bottom = row_groups[row_end_idx][1]
            board_height = bottom - top + 1
            if board_height < 80:
                continue

            for col_start_idx in range(len(col_groups)):
                for col_end_idx in range(col_start_idx + 1, len(col_groups)):
                    left = col_groups[col_start_idx][0]
                    right = col_groups[col_end_idx][1]
                    board_width = right - left + 1
                    if board_width < 80:
                        continue

                    ratio = board_width / float(board_height)
                    if not 0.72 <= ratio <= 1.28:
                        continue

                    square_penalty = abs(board_width - board_height) / float(max(board_width, board_height))
                    position_penalty = (top + left) * 0.0001
                    span_penalty = (row_end_idx - row_start_idx + col_end_idx - col_start_idx) * 0.00001
                    size_bonus = min(board_width, board_height) * 0.0002
                    score = square_penalty + position_penalty + span_penalty - size_bonus
                    candidate = (score, top, bottom, left, right)
                    if best is None or candidate < best:
                        best = candidate

    if best is None:
        return None
    _, top, bottom, left, right = best
    return top, bottom, left, right


def _bottom_crop_limit_after_coordinates(
    gray: Image.Image,
    *,
    left: int,
    right: int,
    bottom: int,
    board_width: int,
    board_height: int,
    pad_left: int,
    pad_right: int,
    max_pad_bottom: int,
) -> int:
    """Keep file labels below the board while trimming the next exercise."""
    width, height = gray.size
    scan_start = min(height - 1, bottom + 1)
    scan_end = min(height - 1, bottom + max_pad_bottom)
    if scan_start >= scan_end:
        return min(height, bottom + max_pad_bottom + 1)

    x0 = max(0, left - pad_left)
    x1 = min(width, right + pad_right + 1)
    dark_row_threshold = max(2, int((x1 - x0) * 0.006))
    dark_rows: list[int] = []
    pixels = gray.load()
    for y in range(scan_start, scan_end + 1):
        dark_count = 0
        for x in range(x0, x1):
            if pixels[x, y] < 96:
                dark_count += 1
                if dark_count >= dark_row_threshold:
                    dark_rows.append(y)
                    break

    groups = _contiguous_groups(dark_rows)
    min_pad_bottom = max(24, int(board_height * 0.075))
    label_attach_limit = bottom + max(34, int(board_height * 0.105))
    gap_limit = max(8, int(board_height * 0.025))
    safety_margin = max(8, int(board_height * 0.02))

    keep_until = bottom
    kept_label_group = False
    next_group_start: int | None = None
    for group_start, group_end in groups:
        if not kept_label_group:
            if group_start <= label_attach_limit:
                keep_until = max(keep_until, group_end)
                kept_label_group = True
                continue
            next_group_start = group_start
            break

        if group_start - keep_until <= gap_limit:
            keep_until = max(keep_until, group_end)
            continue

        next_group_start = group_start
        break

    target_bottom = max(bottom + min_pad_bottom, keep_until + safety_margin)
    if next_group_start is not None:
        target_bottom = min(target_bottom, max(bottom + 1, next_group_start - safety_margin))

    return min(height, max(bottom + 1, target_bottom + 1))


def _right_crop_limit_after_marker(
    gray: Image.Image,
    *,
    top: int,
    bottom: int,
    right: int,
    board_width: int,
    board_height: int,
    default_pad_right: int,
    max_pad_right: int,
) -> int:
    """Keep side-to-move markers beside the board without swallowing neighbors."""
    width, _height = gray.size
    scan_start = min(width - 1, right + 1)
    scan_end = min(width - 1, right + max_pad_right)
    if scan_start >= scan_end:
        return min(width, right + default_pad_right + 1)

    marker_y0 = max(0, top - int(board_height * 0.03))
    marker_y1 = min(gray.height, bottom + int(board_height * 0.08) + 1)
    dark_col_threshold = max(2, int((marker_y1 - marker_y0) * 0.008))
    pixels = gray.load()
    dark_cols: list[int] = []
    for x in range(scan_start, scan_end + 1):
        dark_count = 0
        for y in range(marker_y0, marker_y1):
            if pixels[x, y] < 96:
                dark_count += 1
                if dark_count >= dark_col_threshold:
                    dark_cols.append(x)
                    break

    groups = _contiguous_groups(dark_cols)
    marker_attach_limit = right + max(54, int(board_width * 0.13))
    safety_margin = max(7, int(board_width * 0.018))
    default_limit = min(width, right + default_pad_right + 1)
    if not groups:
        return default_limit

    keep_until = right + default_pad_right
    for group_start, group_end in groups:
        if group_start > marker_attach_limit:
            break
        keep_until = max(keep_until, group_end + safety_margin)

    return min(width, max(default_limit, keep_until + 1))


def _redraw_right_side_marker(
    img: Image.Image,
    *,
    board_top: int,
    board_bottom: int,
    board_right: int,
    board_width: int,
    board_height: int,
) -> Image.Image:
    """Replace fragile PDF side-to-move glyphs with clean line-art markers."""
    scan_x0 = min(img.width, board_right + max(5, int(board_width * 0.012)))
    scan_y0 = max(0, board_top - int(board_height * 0.03))
    scan_y1 = min(img.height, board_bottom + max(16, int(board_height * 0.10)) + 1)
    if scan_x0 >= img.width or scan_y0 >= scan_y1:
        return img

    gray = img.convert("L")
    dark_pixels: list[tuple[int, int]] = []
    pixels = gray.load()
    for y in range(scan_y0, scan_y1):
        for x in range(scan_x0, img.width):
            if pixels[x, y] < 96:
                dark_pixels.append((x, y))

    min_dark_pixels = max(24, int(board_height * 0.045))
    if len(dark_pixels) < min_dark_pixels:
        return img

    marker_left = min(x for x, _y in dark_pixels)
    marker_right = max(x for x, _y in dark_pixels)
    marker_top = min(y for _x, y in dark_pixels)
    marker_bottom = max(y for _x, y in dark_pixels)
    marker_width = marker_right - marker_left + 1
    marker_height = marker_bottom - marker_top + 1
    if marker_width > board_width * 0.22 or marker_height > board_height * 0.35:
        return img

    output = img.copy()
    draw = ImageDraw.Draw(output)
    white = (255, 255, 255) if output.mode == "RGB" else 255
    black = (0, 0, 0) if output.mode == "RGB" else 0

    erase_box = (
        max(0, marker_left - 4),
        max(0, marker_top - 4),
        min(output.width - 1, marker_right + 4),
        min(output.height - 1, marker_bottom + 4),
    )
    draw.rectangle(erase_box, fill=white)

    marker_size = max(24, min(40, int(board_width * 0.075), int(board_height * 0.09)))
    center_y = (marker_top + marker_bottom) // 2
    triangle_top = max(3, min(output.height - marker_size - 4, center_y - marker_size // 2))
    triangle_left = board_right + max(10, int(board_width * 0.026))
    triangle_left = min(triangle_left, output.width - marker_size - 6)
    if triangle_left <= board_right + 2:
        return output

    points = [
        (triangle_left + marker_size // 2, triangle_top),
        (triangle_left, triangle_top + marker_size),
        (triangle_left + marker_size, triangle_top + marker_size),
        (triangle_left + marker_size // 2, triangle_top),
    ]
    line_width = max(2, int(marker_size * 0.07))
    draw.line(points, fill=black, width=line_width, joint="curve")
    return output


def _crop_to_board_region(img: Image.Image) -> Image.Image:
    """Trim captions and page debris while keeping the board and coordinates."""
    gray = img.convert("L")
    width, height = gray.size

    row_counts, col_counts, row_runs, col_runs = _longest_dark_runs(gray)

    row_minimum_count = max(20, int(width * BOARD_BORDER_DENSITY_RATIO))
    row_minimum_run = max(80, int(width * BOARD_BORDER_RUN_RATIO))
    col_minimum_count = max(20, int(height * BOARD_BORDER_DENSITY_RATIO))
    col_minimum_run = max(80, int(height * BOARD_BORDER_RUN_RATIO))

    border_row_groups = _find_border_groups(
        row_counts,
        row_runs,
        minimum_count=row_minimum_count,
        minimum_run=row_minimum_run,
    )
    border_col_groups = _find_border_groups(
        col_counts,
        col_runs,
        minimum_count=col_minimum_count,
        minimum_run=col_minimum_run,
    )

    selected_board = _select_board_border_box(border_row_groups, border_col_groups)

    if selected_board:
        top, bottom, left, right = selected_board
    else:
        row_band = _largest_dense_band(row_counts, max(10, int(width * BOARD_DENSITY_RATIO)))
        col_band = _largest_dense_band(col_counts, max(10, int(height * BOARD_DENSITY_RATIO)))
        if not row_band or not col_band:
            return img
        left, right = col_band
        top, bottom = row_band

    board_width = right - left + 1
    board_height = bottom - top + 1

    # Keep rank/file coordinates near the board, but avoid pulling in exercise
    # numbers or neighboring diagrams from dense multi-board pages.
    pad_left = max(18, int(board_width * 0.075))
    pad_right = max(24, int(board_width * 0.10))
    max_pad_right = max(74, int(board_width * 0.20))
    pad_top = max(2, int(board_height * 0.012))
    pad_bottom = max(34, int(board_height * 0.12))
    bottom_limit = _bottom_crop_limit_after_coordinates(
        gray,
        left=left,
        right=right,
        bottom=bottom,
        board_width=board_width,
        board_height=board_height,
        pad_left=pad_left,
        pad_right=pad_right,
        max_pad_bottom=pad_bottom,
    )
    right_limit = _right_crop_limit_after_marker(
        gray,
        top=top,
        bottom=bottom,
        right=right,
        board_width=board_width,
        board_height=board_height,
        default_pad_right=pad_right,
        max_pad_right=max_pad_right,
    )

    crop_box = (
        max(0, left - pad_left),
        max(0, top - pad_top),
        right_limit,
        bottom_limit,
    )
    cropped = img.crop(crop_box)
    if cropped.size[0] <= 0 or cropped.size[1] <= 0:
        return img

    crop_left, crop_top, _crop_right, _crop_bottom = crop_box
    return _redraw_right_side_marker(
        cropped,
        board_top=top - crop_top,
        board_bottom=bottom - crop_top,
        board_right=right - crop_left,
        board_width=board_width,
        board_height=board_height,
    )


def generate_chess_diagram_css() -> str:
    """Generate CSS for absolutely positioned chess diagram images."""
    return """\
.chess-diagram {
  position: absolute;
  z-index: 5;
  pointer-events: auto;
  image-rendering: auto;
}

.chess-diagram-container {
  position: absolute;
  z-index: 5;
}
"""
