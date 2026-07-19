from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "test_sprint2_playwright_smoke.py",
    ROOT / "test_ui_state_screenshot_pack.py",
)


def main() -> None:
    total = 0
    for path in TARGETS:
        text = path.read_text(encoding="utf-8")
        old = 'f"**/convert/download/{job_id}"'
        new = 'f"**/convert/download/{job_id}**"'
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"{path.name}: expected one exact download route, found {count}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        total += count
    if total != 2:
        raise RuntimeError(f"Expected two patched download routes, found {total}")


if __name__ == "__main__":
    main()
