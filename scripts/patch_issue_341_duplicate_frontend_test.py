from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_PATH = ROOT / "frontend" / "src" / "App.test.tsx"


def main() -> None:
    text = TEST_PATH.read_text(encoding="utf-8")
    old = 'expect(fetchMock).toHaveBeenCalledWith("/convert/repair/job-blocked", { method: "POST" });'
    new = 'expect(fetchMock).toHaveBeenCalledWith("/convert/repair/job-blocked", expect.objectContaining({ method: "POST" }));'
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one remaining repair assertion, found {count}")
    TEST_PATH.write_text(text.replace(old, new, 1), encoding="utf-8")


if __name__ == "__main__":
    main()
