from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from openai_chess_fen_reviewer import openai_chess_fen_reviewer_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Report OpenAI chess FEN reviewer configuration status.")
    parser.add_argument("--output", default="", help="Optional JSON output path for audit evidence.")
    parser.add_argument("--cwd", default="", help="Optional directory used for .env.local/.env discovery.")
    args = parser.parse_args()

    status = openai_chess_fen_reviewer_status(cwd=args.cwd or None)
    payload = json.dumps(status, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
