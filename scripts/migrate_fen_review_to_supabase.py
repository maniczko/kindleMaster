from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chess_fen_review_store import load_fen_review_progress
from supabase_fen_review import SupabaseFenReviewClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a source-bound FEN review snapshot into Supabase.")
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--owner-user-id", default="")
    args = parser.parse_args()

    payload = load_fen_review_progress(args.review_dir)
    rows = payload["rows"]
    source_digest = next(
        (
            str(row.get("source_document_sha256") or row.get("source_artifact_sha256") or "").strip()
            for row in rows
            if row.get("source_document_sha256") or row.get("source_artifact_sha256")
        ),
        "",
    )
    client = SupabaseFenReviewClient()
    if not client.available:
        raise SystemExit("Supabase FEN review storage is not configured.")
    saved = client.save_review(
        artifact_id=args.artifact_id,
        source_document_sha256=source_digest,
        rows=rows,
        summary=payload["summary"],
        owner_user_id=args.owner_user_id,
    )
    loaded = client.load_review(artifact_id=args.artifact_id)
    if loaded is None or len(loaded["rows"]) != len(rows):
        raise SystemExit("Supabase verification failed after import.")
    print(
        json.dumps(
            {
                "artifact_id": args.artifact_id,
                "storage": saved["storage"],
                "saved_at": saved["saved_at"],
                "rows": len(loaded["rows"]),
                "summary": loaded["summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
