from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path

from chess_yusupov_acceptance import acceptance_report_markdown


def write_markdown_summary(json_path: str | Path, markdown_path: str | Path) -> None:
    payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Acceptance summary JSON must contain an object.")
    Path(markdown_path).write_text(
        acceptance_report_markdown(payload),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        return 2
    try:
        write_markdown_summary(args[0], args[1])
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
