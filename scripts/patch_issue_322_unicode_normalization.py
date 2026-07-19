from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "chess_exercise_reconciliation.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''_PLAYER_SPLIT_PATTERN = re.compile(r"\\s+(?:-|–|—|vs\\.?|v\\.?)\\s+", re.IGNORECASE)
''',
        '''_PLAYER_SPLIT_PATTERN = re.compile(r"\\s+(?:-|–|—|vs\\.?|v\\.?)\\s+", re.IGNORECASE)
_IDENTITY_TRANSLITERATION = str.maketrans(
    {
        "ł": "l",
        "đ": "d",
        "ð": "d",
        "þ": "th",
        "æ": "ae",
        "œ": "oe",
        "ø": "o",
    }
)
''',
        "identity transliteration table",
    )
    text = replace_once(
        text,
        '''def normalize_identity_text(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", _text(value).casefold())
''',
        '''def normalize_identity_text(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", _text(value).casefold().translate(_IDENTITY_TRANSLITERATION))
''',
        "identity transliteration usage",
    )
    MODULE_PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
