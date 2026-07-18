from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "frontend" / "src" / "App.tsx"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = APP_PATH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'import { apiRequestInput, apiUrl } from "./lib/api-base";',
        'import { GUEST_ACCESS_HEADER, apiRequestInput, apiUrl, getOrCreateGuestAccessId } from "./lib/api-base";',
        "api-base import",
    )
    old = '''  async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
    const token = await accessTokenFromClient(authClientRef.current);
    const requestInput = apiRequestInput(input);
    if (!token) return fetch(requestInput, init);
    const baseHeaders =
      init.headers instanceof Headers
        ? Object.fromEntries(init.headers.entries())
        : Array.isArray(init.headers)
          ? Object.fromEntries(init.headers)
          : { ...(init.headers as Record<string, string> | undefined) };
    return fetch(requestInput, {
      ...init,
      headers: {
        ...baseHeaders,
        Authorization: `Bearer ${token}`,
      },
    });
  }
'''
    new = '''  async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
    const token = await accessTokenFromClient(authClientRef.current);
    const requestInput = apiRequestInput(input);
    const baseHeaders =
      init.headers instanceof Headers
        ? Object.fromEntries(init.headers.entries())
        : Array.isArray(init.headers)
          ? Object.fromEntries(init.headers)
          : { ...(init.headers as Record<string, string> | undefined) };
    const guestAccessId = getOrCreateGuestAccessId();
    return fetch(requestInput, {
      ...init,
      headers: {
        ...baseHeaders,
        ...(guestAccessId ? { [GUEST_ACCESS_HEADER]: guestAccessId } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
  }
'''
    text = replace_once(text, old, new, "apiFetch")
    APP_PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
