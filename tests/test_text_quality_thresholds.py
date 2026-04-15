from __future__ import annotations

from pathlib import Path

from kindlemaster_text_audit import audit_epub_text, classify_joined_word_token


def test_text_artifact_thresholds(active_release_epub: Path) -> None:
    audit = audit_epub_text(active_release_epub)
    split_count = audit["split_word_scan"]["matches_total"]
    joined_count = audit["joined_word_scan"]["matches_total"]
    boundary_count = audit["boundary_scan"]["matches_total"]

    assert split_count <= 1
    assert joined_count <= 10
    assert boundary_count <= 15


def test_remaining_split_word_is_review_only(active_release_epub: Path) -> None:
    audit = audit_epub_text(active_release_epub)
    matches = audit["split_word_scan"]["matches"]
    assert all(match["decision"] == "review_only" for match in matches)


def test_known_brand_and_name_tokens_are_suppressed_from_joined_word_audit() -> None:
    for token in [
        "McDonald",
        "MacMurray",
        "McInerney",
        "McCudden",
        "MacDonnel",
        "AeroPress",
        "InterContinental",
        "YouTubie",
        "McDonaldzie",
        "InPlus",
    ]:
        assert classify_joined_word_token(token) is None, token


def test_joined_word_auto_fix_requires_stronger_evidence() -> None:
    weak = classify_joined_word_token("abCdef")
    strong = classify_joined_word_token("abcdEfgh")

    assert weak is not None
    assert weak["decision"] == "review_only"
    assert weak["reason"] == "embedded_titlecase_join_short"
    assert strong is not None
    assert strong["decision"] == "auto_fix"
    assert strong["reason"] == "embedded_titlecase_join_strong"


def test_active_release_epub_no_known_brand_false_positives(active_release_epub: Path) -> None:
    audit = audit_epub_text(active_release_epub)
    joined_matches = {match["match"] for match in audit["joined_word_scan"]["matches"]}
    assert "InPlus" not in joined_matches
