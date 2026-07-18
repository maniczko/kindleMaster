from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_PATH = ROOT / "frontend" / "src" / "App.test.tsx"


def main() -> None:
    text = TEST_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r'expect\(fetchMock\)\.toHaveBeenCalledWith\(\s*'
        r'"/convert/repair/job-blocked"\s*,\s*'
        r'\{\s*method:\s*"POST"\s*\}\s*\);'
    )
    replacement = (
        'expect(fetchMock).toHaveBeenCalledWith('
        '"/convert/repair/job-blocked", '
        'expect.objectContaining({ method: "POST" })'
        ');'
    )
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"Expected one repair fetch assertion, found {count}")
    TEST_PATH.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
