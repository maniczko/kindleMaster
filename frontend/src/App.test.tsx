import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const supabaseMocks = vi.hoisted(() => {
  const client = {
    auth: {
      getSession: vi.fn(async () => ({ data: { session: null } })),
      onAuthStateChange: vi.fn(() => ({ data: { subscription: { unsubscribe: vi.fn() } } })),
      signInWithPassword: vi.fn(async () => ({ data: { session: null }, error: null })),
      signUp: vi.fn(async () => ({ data: { session: null }, error: null })),
      signOut: vi.fn(async () => ({ error: null })),
    },
  };
  return {
    client,
    createClient: vi.fn(() => client),
  };
});

vi.mock("@supabase/supabase-js", () => ({
  createClient: supabaseMocks.createClient,
}));

import App from "./App";

const fetchMock = vi.fn();

const defaultProfile = {
  conversion: {
    default_profile: "auto-premium",
    default_language: "pl",
    force_ocr: false,
    heading_repair: true,
  },
  email_delivery: {
    enabled: false,
    host: "",
    port: 587,
    security: "starttls",
    username: "",
    from_address: "",
    default_recipient: "",
    max_attachment_bytes: 52428800,
    secret_configured: false,
  },
};

function installDefaultFetchMock() {
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("/convert/jobs")) {
      return { ok: true, json: async () => ({ jobs: [] }) };
    }
    if (url === "/auth/config") {
      return { ok: true, json: async () => ({ success: true, auth: { enabled: false, configured: false } }) };
    }
    if (url === "/user/profile") {
      if (init?.method === "PUT") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            profile: JSON.parse(String(init.body)).profile ?? JSON.parse(String(init.body)),
          }),
        };
      }
      return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
    }
    if (url === "/convert/delivery/config") {
      return {
        ok: true,
        json: async () => ({
          success: true,
          delivery: {
            enabled: false,
            configured: false,
            provider: "smtp",
            secret_configured: false,
            profile_configured: false,
            missing_config: [],
          },
        }),
      };
    }
    return { ok: true, json: async () => ({}) };
  });
}

describe("Premium React shell", () => {
  beforeEach(() => {
    installDefaultFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    supabaseMocks.createClient.mockClear();
    supabaseMocks.client.auth.getSession.mockReset();
    supabaseMocks.client.auth.getSession.mockResolvedValue({ data: { session: null } });
    supabaseMocks.client.auth.onAuthStateChange.mockClear();
    supabaseMocks.client.auth.signInWithPassword.mockReset();
    supabaseMocks.client.auth.signInWithPassword.mockResolvedValue({ data: { session: null }, error: null });
    supabaseMocks.client.auth.signUp.mockReset();
    supabaseMocks.client.auth.signUp.mockResolvedValue({ data: { session: null }, error: null });
    supabaseMocks.client.auth.signOut.mockReset();
    supabaseMocks.client.auth.signOut.mockResolvedValue({ error: null });
    window.localStorage.clear();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:kindlemaster-preview") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    window.localStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  it("renders a Polish premium workspace with primary views and no debug nav", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Konwersja" })).toBeInTheDocument();
    const mainNav = screen.getByRole("navigation", { name: "Główne widoki" });
    for (const label of ["Konwersja", "Biblioteka"]) {
      expect(within(mainNav).getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(mainNav.querySelectorAll(".km-nav-icon")).toHaveLength(2);
    expect(within(mainNav).queryByRole("button", { name: "Jakość" })).not.toBeInTheDocument();
    expect(within(mainNav).queryByRole("button", { name: "Podgląd PDF" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Strona główna KindleMaster" })).toBeInTheDocument();
    expect(within(mainNav).queryByRole("button", { name: "Dostawa" })).not.toBeInTheDocument();
    expect(within(mainNav).queryByRole("button", { name: "Ustawienia" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ustawienia" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Debug" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Debug" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Odśwież zadania" })).not.toBeInTheDocument();
    expect(screen.queryByText("Lokalna produkcja Kindle")).not.toBeInTheDocument();
    expect(screen.queryByText("Następna akcja")).not.toBeInTheDocument();
    expect(screen.queryByText("Advanced technical payload")).not.toBeInTheDocument();
    expect(screen.queryByText("Convert workspace")).not.toBeInTheDocument();
    expect(screen.getByText("Historia lokalna")).toBeInTheDocument();
  });

  it("shows Supabase login form when cloud accounts are configured", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/auth/config") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            auth: {
              enabled: true,
              configured: true,
              provider: "supabase",
              supabase_url: "https://project.supabase.co",
              publishable_key: "sb_publishable_public",
            },
          }),
        };
      }
      if (url.startsWith("/convert/jobs")) return { ok: true, json: async () => ({ jobs: [] }) };
      if (url === "/user/profile") return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      if (url === "/convert/delivery/config") return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      return { ok: true, json: async () => ({}) };
    });

    render(<App />);

    expect(await screen.findByLabelText("Email konta")).toBeInTheDocument();
    expect(screen.getByLabelText("Hasło konta")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zaloguj" })).toBeDisabled();
    expect(supabaseMocks.createClient).toHaveBeenCalledWith("https://project.supabase.co", "sb_publishable_public");
  });

  it("lets a configured unauthenticated user continue into the local workspace", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/auth/config") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            auth: {
              enabled: true,
              configured: true,
              provider: "supabase",
              supabase_url: "https://project.supabase.co",
              publishable_key: "sb_publishable_public",
            },
          }),
        };
      }
      if (url.startsWith("/convert/jobs")) return { ok: true, json: async () => ({ jobs: [] }) };
      if (url === "/user/profile") return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      if (url === "/convert/delivery/config") return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      return { ok: true, json: async () => ({}) };
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Zaloguj się albo załóż konto" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Kontynuuj lokalnie" }));

    expect(await screen.findByRole("heading", { name: "Konwersja" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zaloguj / konto" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ustawienia" }));
    expect(await screen.findByRole("button", { name: "Zaloguj / konto" })).toBeInTheDocument();
  });

  it("logs in with email and password through the Supabase client", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/auth/config") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            auth: {
              enabled: true,
              configured: true,
              supabase_url: "https://project.supabase.co",
              publishable_key: "sb_publishable_public",
            },
          }),
        };
      }
      if (url.startsWith("/convert/jobs")) return { ok: true, json: async () => ({ jobs: [] }) };
      if (url === "/user/profile") return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      if (url === "/convert/delivery/config") return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      return { ok: true, json: async () => ({}) };
    });
    supabaseMocks.client.auth.signInWithPassword.mockResolvedValue({
      data: {
        session: {
          access_token: "access-token",
          user: { id: "user-1", email: "reader@example.com" },
        },
      },
      error: null,
    } as any);
    render(<App />);

    await user.type(await screen.findByLabelText("Email konta"), "reader@example.com");
    await user.type(screen.getByLabelText("Hasło konta"), "secret123");
    await user.click(screen.getByRole("button", { name: "Zaloguj" }));

    await waitFor(() => {
      expect(supabaseMocks.client.auth.signInWithPassword).toHaveBeenCalledWith({
        email: "reader@example.com",
        password: "secret123",
      });
    });
    expect(screen.queryByRole("button", { name: "Wyloguj" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Ustawienia" }));
    expect(await screen.findByText("r***@example.com")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Wyloguj" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Wyloguj" }));
    await waitFor(() => {
      expect(supabaseMocks.client.auth.signOut).toHaveBeenCalled();
    });
  });

  it("adds bearer token to API calls after login and imports local history", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/config") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            auth: {
              enabled: true,
              configured: true,
              supabase_url: "https://project.supabase.co",
              publishable_key: "sb_publishable_public",
            },
          }),
        };
      }
      if (url.startsWith("/convert/jobs")) return { ok: true, json: async () => ({ jobs: [] }) };
      if (url === "/user/profile") return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      if (url === "/convert/delivery/config") return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      if (url === "/user/library/import-local" && init?.method === "POST") {
        return { ok: true, json: async () => ({ success: true, import: { imported: 2, skipped: 1, failed: 0 } }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    supabaseMocks.client.auth.getSession.mockResolvedValue({
      data: {
        session: {
          access_token: "access-token",
          user: { id: "user-1", email: "reader@example.com" },
        },
      },
    } as any);
    render(<App />);

    expect(await screen.findByText("Import lokalnej historii")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Importuj lokalną historię" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/user/library/import-local",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({ Authorization: "Bearer access-token" }),
        }),
      );
    });
    expect(await screen.findByText("Zaimportowano 2, pominięto 1.")).toBeInTheDocument();
  });

  it("enables conversion when a file is selected using profile defaults", async () => {
    const user = userEvent.setup();
    render(<App />);

    const file = new File(["pdf"], "sample.pdf", { type: "application/pdf" });
    await user.upload(await screen.findByLabelText("Wgraj PDF albo DOCX"), file);

    expect(screen.getAllByText("sample.pdf").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: "Rozpocznij konwersję" })).toBeEnabled();
  });

  it("shows an active processing job in the Library before it reaches persisted history", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) return { ok: true, json: async () => ({ jobs: [] }) };
      if (url === "/convert/start" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            job_id: "job-processing",
            status: "running",
            message: "Ekstrakcja tekstu z PDF...",
          }),
        };
      }
      if (url === "/convert/status/job-processing") {
        return new Promise<Response>(() => undefined);
      }
      if (url === "/auth/config") {
        return { ok: true, json: async () => ({ success: true, auth: { enabled: false, configured: false } }) };
      }
      if (url === "/user/profile") {
        return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    const file = new File(["pdf"], "processing.pdf", { type: "application/pdf" });
    await user.upload(await screen.findByLabelText("Wgraj PDF albo DOCX"), file);
    await user.click(screen.getByRole("button", { name: "Rozpocznij konwersję" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/convert/start",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });

    await user.click(screen.getByRole("button", { name: "Biblioteka" }));

    expect(await screen.findByRole("button", { name: "processing.pdf" })).toBeInTheDocument();
    expect(screen.getByText("Trwa przetwarzanie")).toBeInTheDocument();
    expect(screen.getByText("Ekstrakcja tekstu z PDF...")).toBeInTheDocument();
    expect(screen.queryByText("Brak ostatnich zadań.")).not.toBeInTheDocument();
  });

  it("keeps a long-running conversion in the background instead of failing the interactive wait", async () => {
    const user = userEvent.setup();
    let statusCalls = 0;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) return { ok: true, json: async () => ({ jobs: [] }) };
      if (url === "/convert/start" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            job_id: "job-long-running",
            status: "running",
            message: "Konwersja PDF do EPUB...",
          }),
        };
      }
      if (url === "/convert/status/job-long-running") {
        statusCalls += 1;
        return {
          ok: true,
          json: async () => ({
            success: true,
            job_id: "job-long-running",
            status: "running",
            filename: "long-running.pdf",
            message: "Konwersja PDF do EPUB...",
            progress: { health: "long_running", elapsed_seconds: 121 },
          }),
        };
      }
      if (url === "/auth/config") {
        return { ok: true, json: async () => ({ success: true, auth: { enabled: false, configured: false } }) };
      }
      if (url === "/user/profile") {
        return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      }
      return { ok: true, json: async () => ({}) };
    });

    render(<App />);

    const file = new File(["pdf"], "long-running.pdf", { type: "application/pdf" });
    await user.upload(await screen.findByLabelText("Wgraj PDF albo DOCX"), file);
    await user.click(screen.getByTestId("start-conversion-button"));

    expect(await screen.findByText("Konwersja nadal trwa w tle. Możesz wrócić do Biblioteki i odświeżyć status później.")).toBeInTheDocument();
    expect(screen.getAllByText("Przetwarzanie").length).toBeGreaterThan(0);
    expect(screen.queryByText("Konwersja trwa zbyt długo dla interaktywnego podglądu.")).not.toBeInTheDocument();
    expect(statusCalls).toBe(1);
  });

  it("filters and sorts Library rows without losing aligned action controls", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-zeta",
                filename: "zeta.pdf",
                status: "ready",
                updated_at: "2026-05-22T12:01:00Z",
                quality_state: { score: 60, release_verdict: "ready_with_review" },
              },
              {
                job_id: "job-alpha",
                filename: "alpha.pdf",
                status: "ready",
                updated_at: "2026-05-22T12:03:00Z",
                quality_state: { score: 96, release_verdict: "release_ready", premium_ready: true },
              },
              {
                job_id: "job-mid",
                filename: "mid.pdf",
                status: "failed",
                updated_at: "2026-05-22T12:02:00Z",
                quality_state: { score: 20, release_verdict: "failed" },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      if (url === "/convert/delivery/config") return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      if (url === "/auth/config") return { ok: true, json: async () => ({ success: true, auth: { enabled: false, configured: false } }) };
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    expect(screen.getByLabelText("Szukaj w bibliotece")).toBeInTheDocument();
    expect(screen.getByLabelText("Sortowanie biblioteki")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Szukaj w bibliotece"), "alpha");
    expect(screen.getByRole("button", { name: "alpha.pdf" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "zeta.pdf" })).not.toBeInTheDocument();
    expect(screen.getByText("1 z 3")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Szukaj w bibliotece"));
    await user.selectOptions(screen.getByLabelText("Sortowanie biblioteki"), "name_asc");
    const table = screen.getByRole("table", { name: "Ostatnie zadania" });
    const fileButtons = within(table)
      .getAllByRole("button")
      .filter((button) => ["alpha.pdf", "mid.pdf", "zeta.pdf"].includes(button.textContent?.trim() || ""));
    expect(fileButtons.map((button) => button.textContent?.trim())).toEqual(["alpha.pdf", "mid.pdf", "zeta.pdf"]);

    await user.click(within(table).getByRole("button", { name: /Plik/ }));
    const fileButtonsDesc = within(table)
      .getAllByRole("button")
      .filter((button) => ["alpha.pdf", "mid.pdf", "zeta.pdf"].includes(button.textContent?.trim() || ""));
    expect(fileButtonsDesc.map((button) => button.textContent?.trim())).toEqual(["zeta.pdf", "mid.pdf", "alpha.pdf"]);

    await user.click(within(table).getByRole("button", { name: /Jakość/ }));
    const qualitySortedButtons = within(table)
      .getAllByRole("button")
      .filter((button) => ["alpha.pdf", "mid.pdf", "zeta.pdf"].includes(button.textContent?.trim() || ""));
    expect(qualitySortedButtons.map((button) => button.textContent?.trim())).toEqual(["alpha.pdf", "zeta.pdf", "mid.pdf"]);
  });

  it("restores PDF preview and crop workspace for an uploaded PDF", async () => {
    const user = userEvent.setup();
    render(<App />);

    const file = new File(["%PDF"], "sample.pdf", { type: "application/pdf" });
    await user.upload(await screen.findByLabelText("Wgraj PDF albo DOCX"), file);
    await user.click(screen.getByRole("button", { name: "Podgląd PDF / kadrowanie" }));

    expect(screen.getByRole("heading", { name: "Podgląd PDF i kadrowanie" })).toBeInTheDocument();
    expect(screen.getByTitle("Podgląd PDF")).toHaveAttribute("data-preview-src", "blob:kindlemaster-preview");
    expect(screen.getByRole("link", { name: "Kadruj do A4" })).toHaveAttribute("href", "/legacy");
    expect(screen.getByText("Tryb kadrowania jest dostępny dla PDF przed konwersją.")).toBeInTheDocument();
  });

  it("sends release-ready artifacts directly from the Library row", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-ready",
                filename: "sample.pdf",
                source_type: "pdf",
                source_preview_url: "/convert/preview/job-ready/input",
                status: "ready",
                download_url: "/convert/download/job-ready",
                quality_state_url: "/convert/quality/job-ready",
                quality_state: {
                  score: 97,
                  release_verdict: "release_ready",
                  send_to_kindle_ready: true,
                  send_to_kindle_blockers: [],
                  user_facing_verdict: { label: "Publikuj", detail: "Ready." },
                },
              },
            ],
          }),
        };
      }
      if (url === "/convert/quality/job-ready") {
        throw new Error("Biblioteka nie powinna pobierać quality_state osobnym requestem, jeśli /convert/jobs zwraca quality_state inline.");
      }
      if (url === "/user/profile") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            profile: {
              ...defaultProfile,
              email_delivery: { ...defaultProfile.email_delivery, default_recipient: "reader@kindle.com" },
            },
          }),
        };
      }
      if (url === "/convert/delivery/config") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            delivery: { enabled: true, configured: true, provider: "smtp", secret_configured: true },
          }),
        };
      }
      if (url === "/convert/delivery/job-ready/email") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            delivery: { status: "sent", masked_recipient: "r***@kindle.com" },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    expect(screen.queryByRole("button", { name: "Dostawa" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "sample.pdf" })).toBeInTheDocument();
    expect(screen.getByLabelText(/Jakość konwersji:/)).toBeInTheDocument();
    expect(screen.queryByText("Gotowe do wysyłki")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "PDF" })).toHaveAttribute("href", "/convert/preview/job-ready/input");
    expect(screen.queryByLabelText("Adres Kindle dla sample.pdf")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Wyślij na Kindle" })).toHaveAttribute("title", "Wyślij na r***@kindle.com");
    await user.click(screen.getByRole("button", { name: "Wyślij na Kindle" }));

    expect(await screen.findByText("Wysłano EPUB do r***@kindle.com")).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([url]) => String(url))).not.toContain("/convert/quality/job-ready");
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/convert/delivery/job-ready/email",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to: "reader@kindle.com", artifact: "epub" }),
        }),
      );
    });
  });

  it("allows email delivery for quality-blocked artifacts and keeps quality warnings visible", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-review",
                filename: "review.pdf",
                status: "ready",
                quality_state: {
                  release_verdict: "ready_with_review",
                  send_to_kindle_ready: false,
                  send_to_kindle_blockers: [
                    {
                      code: "kindle_delivery_release_not_ready",
                      message: "EPUB is generated, but release quality is not ready for Kindle delivery.",
                    },
                  ],
                },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            profile: { ...defaultProfile, email_delivery: { ...defaultProfile.email_delivery, default_recipient: "reader@kindle.com" } },
          }),
        };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: true } }) };
      }
      if (url === "/convert/delivery/job-review/email" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            delivery: {
              status: "sent",
              masked_recipient: "r***@kindle.com",
              quality_gate: { warning_only: true, release_verdict: "ready_with_review" },
            },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));

    expect(
      screen.getAllByTitle(
        "Bramka jakości ma status „Nie publikuj”. Mail może zostać wysłany, ale plik nie jest gotowy do publikacji; użyj „Napraw ponownie”, jeśli chcesz poprawić jakość.",
      ),
    ).not.toHaveLength(0);
    expect(screen.queryByText("Można wysłać z uwagami")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Adres Kindle dla review.pdf")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Wyślij na Kindle" })).toHaveAttribute(
      "title",
      expect.stringContaining("Uwagi jakości"),
    );
    await user.click(screen.getByRole("button", { name: "Wyślij na Kindle" }));
    expect(await screen.findByText("Wysłano EPUB do r***@kindle.com")).toBeInTheDocument();
    expect(screen.queryByText("Wysyłka niedostępna")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/convert/delivery/job-review/email",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to: "reader@kindle.com", artifact: "epub" }),
        }),
      );
    });
  });

  it("opens dedicated file details from the Library row", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-ready",
                filename: "sample.pdf",
                status: "ready",
                elapsed_seconds: 12.4,
                output_size_bytes: 2048,
                quality_state: { release_verdict: "release_ready", premium_ready: true },
                email_delivery: {
                  diagnostics: {
                    smtp: {
                      host: "smtp.example.com",
                      port: 587,
                      security: "starttls",
                      accepted_by_smtp: true,
                      from_matches_smtp_username: true,
                    },
                    message: { content_type: "multipart/mixed", has_plain_text_body: true },
                    attachment: {
                      filename: "sample.epub",
                      content_type: "application/epub+zip",
                      content_disposition: "attachment",
                      content_transfer_encoding: "base64",
                      size_bytes: 2048,
                      sha256: "a".repeat(64),
                    },
                  },
                },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            profile: { ...defaultProfile, email_delivery: { ...defaultProfile.email_delivery, default_recipient: "reader@kindle.com" } },
          }),
        };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    await user.click(await screen.findByRole("button", { name: "sample.pdf" }));

    expect(screen.getByRole("heading", { name: "Szczegóły pliku" })).toBeInTheDocument();
    expect(screen.getAllByText("sample.pdf").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("job-ready")).toBeInTheDocument();
    expect(screen.getAllByText("2.0 KB").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByLabelText("Informacje o aktywnym zadaniu")).toBeInTheDocument();
    expect(screen.getAllByText("Rozmiar").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("heading", { name: "Diagnostyka SMTP" })).toBeInTheDocument();
    expect(screen.getByText("smtp.example.com:587 / starttls")).toBeInTheDocument();
    expect(screen.getByText("sample.epub / application/epub+zip / attachment")).toBeInTheDocument();
    expect(screen.queryByText("Advanced technical payload")).not.toBeInTheDocument();
    expect(screen.queryByText("Error and debug panel")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Strona główna KindleMaster" }));
    expect(await screen.findByRole("heading", { name: "Konwersja" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Informacje o aktywnym zadaniu")).not.toBeInTheDocument();
  });

  it("saves post-conversion feedback from file details", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-feedback-ui",
                filename: "feedback.pdf",
                status: "ready",
                elapsed_seconds: 7,
                output_size_bytes: 1024,
                quality_state: { release_verdict: "ready_with_review", premium_ready: false },
              },
            ],
          }),
        };
      }
      if (url === "/convert/feedback/job-feedback-ui" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            feedback_record: {
              record_id: "fb_ui",
              created_at: "2026-07-02T10:00:00Z",
              job_id: "job-feedback-ui",
              status: "accepted",
              quality_label: "good",
              route_label: "book_reflow",
              issue_tags: ["layout"],
              notes: "Layout checked.",
              reviewer: "Iwo",
              include_in_training_requested: true,
              include_in_training: true,
              dataset_reason: "ready",
              learning_ledger: { status: "recorded" },
            },
          }),
        };
      }
      if (url === "/convert/feedback/job-feedback-ui") {
        return {
          ok: true,
          json: async () => ({ success: true, feedback_records: [], latest_feedback: null, feedback_count: 0 }),
        };
      }
      if (url === "/user/profile") {
        return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    await user.click(await screen.findByRole("button", { name: "feedback.pdf" }));

    expect(await screen.findByRole("heading", { name: "Feedback po konwersji" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Akceptuję" }));
    await user.selectOptions(screen.getByLabelText("Jakość wyniku"), "good");
    await user.selectOptions(screen.getByLabelText("Poprawna trasa konwersji"), "book_reflow");
    await user.click(screen.getByRole("button", { name: "Layout" }));
    await user.type(screen.getByLabelText("Recenzent"), "Iwo");
    await user.type(screen.getByLabelText("Notatka"), "Layout checked.");
    await user.click(screen.getByLabelText("Użyj tej oceny do uczenia po weryfikacji"));
    await user.click(screen.getByRole("button", { name: "Zapisz feedback" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/convert/feedback/job-feedback-ui",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
        }),
      );
    });
    expect(await screen.findByText("Feedback zapisany lokalnie.")).toBeInTheDocument();
    expect(screen.getByText("etykieta treningowa")).toBeInTheDocument();
  });

  it("lets file details send PDF to Kindle and hides markdown report artifact", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-pdf",
                filename: "cropped-source.pdf",
                source_type: "pdf",
                source_preview_url: "/convert/preview/job-pdf/input",
                status: "ready",
                download_url: "/convert/download/job-pdf",
                quality_state: {
                  score: 91,
                  release_verdict: "release_ready",
                  send_to_kindle_ready: true,
                  reports: {
                    report_json: "/convert/report/job-pdf.json",
                    report_markdown: "/convert/report/job-pdf.md",
                  },
                },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            profile: { ...defaultProfile, email_delivery: { ...defaultProfile.email_delivery, default_recipient: "reader@kindle.com" } },
          }),
        };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: true } }) };
      }
      if (url === "/convert/delivery/job-pdf/email" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            delivery: { status: "sent", artifact: "pdf", masked_recipient: "r***@kindle.com" },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    await user.click(await screen.findByRole("button", { name: "cropped-source.pdf" }));

    expect(screen.getByRole("link", { name: /PDF źródłowy/ })).toHaveAttribute("href", "/convert/preview/job-pdf/input");
    expect(screen.getByRole("button", { name: "Kadruj" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Kadruj" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Raport jakości JSON" })).toHaveAttribute("href", "/convert/report/job-pdf.json");
    expect(screen.getByRole("link", { name: "Raport jakości Markdown" })).toHaveAttribute("href", "/convert/report/job-pdf.md");

    await user.selectOptions(screen.getByLabelText("Format wysyłki na Kindle"), "pdf");
    await user.click(screen.getByRole("button", { name: "Wyślij na Kindle" }));

    expect(await screen.findByText("Wysłano PDF do r***@kindle.com")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/convert/delivery/job-pdf/email",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to: "reader@kindle.com", artifact: "pdf" }),
        }),
      );
    });
  });

  it("runs manual EPUB repair from file details and shows the result", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-blocked",
                filename: "blocked.pdf",
                status: "ready",
                quality_state: {
                  release_verdict: "release_blocked",
                  send_to_kindle_ready: false,
                  send_to_kindle_blockers: [{ code: "kindle_delivery_progressive_jpeg", message: "1 progressive JPEG" }],
                },
              },
            ],
          }),
        };
      }
      if (url === "/convert/repair/job-blocked" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            job: { job_id: "job-blocked", filename: "blocked.pdf", status: "ready" },
            quality_state: {
              release_verdict: "release_ready",
              send_to_kindle_ready: true,
              send_to_kindle_blockers: [],
              auto_repair: { status: "applied", actions: ["reencode_progressive_jpeg"], selected_candidate: "auto_repair" },
            },
            auto_repair: { status: "applied", actions: ["reencode_progressive_jpeg"], selected_candidate: "auto_repair" },
          }),
        };
      }
      if (url === "/user/profile") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            profile: { ...defaultProfile, email_delivery: { ...defaultProfile.email_delivery, default_recipient: "reader@kindle.com" } },
          }),
        };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: true } }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    await user.click(await screen.findByRole("button", { name: "blocked.pdf" }));
    expect(
      screen.getAllByText("EPUB zawiera obrazy progressive JPEG (1); mail może zostać wysłany, ale zalecane jest przekodowanie do baseline JPEG albo PNG.").length,
    ).toBeGreaterThanOrEqual(1);
    const repairCard = screen.getByRole("heading", { name: "Wysyłka na Kindle" }).closest(".km-card");
    expect(repairCard).not.toHaveTextContent("EPUB zawiera obrazy progressive JPEG");
    expect(screen.queryByLabelText("Adres Kindle dla blocked.pdf")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Napraw ponownie" }));

    expect(await screen.findByText("Naprawa zastosowana i jakość przeliczona ponownie.")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/convert/repair/job-blocked", { method: "POST" });
    });
  });

  it("saves user profile settings without SMTP secrets", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Ustawienia" }));
    expect(screen.getByText("Wysyłka na Kindle")).toBeInTheDocument();
    expect(screen.queryByText("Dostawca")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Domyślny profil konwersji"), "magazine");
    await user.selectOptions(screen.getByLabelText("Domyślny język OCR"), "en");
    await user.type(screen.getByLabelText("Host SMTP"), "smtp.example.com");
    await user.type(screen.getByLabelText("Użytkownik SMTP"), "apikey");
    await user.type(screen.getByLabelText("Adres nadawcy SMTP"), "operator@example.com");
    await user.type(screen.getByLabelText("Domyślny adres Kindle"), "reader@kindle.com");
    await user.click(screen.getByRole("button", { name: "Zapisz ustawienia" }));

    expect(await screen.findByText("Ustawienia zapisane")).toBeInTheDocument();
    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([url, init]) => String(url) === "/user/profile" && init?.method === "PUT");
      expect(putCall).toBeTruthy();
      const body = JSON.parse(String(putCall?.[1]?.body));
      expect(body.email_delivery.host).toBe("smtp.example.com");
      expect(body.email_delivery.default_recipient).toBe("reader@kindle.com");
      expect(JSON.stringify(body).toLowerCase()).not.toContain("password");
    });
  });

  it("sends release-ready artifacts directly from the Library row", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-ready",
                filename: "sample.pdf",
                source_type: "pdf",
                source_preview_url: "/convert/preview/job-ready/input",
                status: "ready",
                download_url: "/convert/download/job-ready",
                quality_state_url: "/convert/quality/job-ready",
                quality_state: {
                  score: 97,
                  release_verdict: "release_ready",
                  send_to_kindle_ready: true,
                  send_to_kindle_blockers: [],
                  user_facing_verdict: { label: "Publikuj", detail: "Ready." },
                },
              },
            ],
          }),
        };
      }
      if (url === "/convert/quality/job-ready") {
        throw new Error("Biblioteka nie powinna pobierać quality_state osobnym requestem, jeśli /convert/jobs zwraca quality_state inline.");
      }
      if (url === "/user/profile") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            profile: {
              ...defaultProfile,
              email_delivery: { ...defaultProfile.email_delivery, default_recipient: "reader@kindle.com" },
            },
          }),
        };
      }
      if (url === "/convert/delivery/config") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            delivery: { enabled: true, configured: true, provider: "smtp", secret_configured: true },
          }),
        };
      }
      if (url === "/convert/delivery/job-ready/email") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            delivery: { status: "sent", artifact: "input", masked_recipient: "r***@kindle.com" },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    expect(screen.queryByRole("button", { name: "Dostawa" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "sample.pdf" })).toBeInTheDocument();
    expect(screen.getByLabelText(/Jakość konwersji:/)).toBeInTheDocument();
    expect(screen.queryByText("Gotowe do wysyłki")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "PDF" })).toHaveAttribute("href", "/convert/preview/job-ready/input");
    expect(screen.queryByLabelText("Adres Kindle dla sample.pdf")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Format wysyłki dla sample.pdf"), "input");
    expect(screen.getByRole("button", { name: "Wyślij na Kindle" })).toHaveAttribute("title", "Wyślij na r***@kindle.com");
    await user.click(screen.getByRole("button", { name: "Wyślij na Kindle" }));

    expect(await screen.findByText("Wysłano PDF do r***@kindle.com")).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([url]) => String(url))).not.toContain("/convert/quality/job-ready");
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/convert/delivery/job-ready/email",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to: "reader@kindle.com", artifact: "input" }),
        }),
      );
    });
  });

  it("allows email delivery for quality-blocked artifacts and keeps quality warnings visible", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-review",
                filename: "review.pdf",
                status: "ready",
                quality_state: {
                  release_verdict: "ready_with_review",
                  send_to_kindle_ready: false,
                  send_to_kindle_blockers: [
                    {
                      code: "kindle_delivery_release_not_ready",
                      message: "EPUB is generated, but release quality is not ready for Kindle delivery.",
                    },
                  ],
                },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            profile: { ...defaultProfile, email_delivery: { ...defaultProfile.email_delivery, default_recipient: "reader@kindle.com" } },
          }),
        };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: true } }) };
      }
      if (url === "/convert/delivery/job-review/email" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            delivery: {
              status: "sent",
              masked_recipient: "r***@kindle.com",
              quality_gate: { warning_only: true, release_verdict: "ready_with_review" },
            },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));

    expect(
      screen.getAllByTitle(
        "Bramka jakości ma status „Nie publikuj”. Mail może zostać wysłany, ale plik nie jest gotowy do publikacji; użyj „Napraw ponownie”, jeśli chcesz poprawić jakość.",
      ),
    ).not.toHaveLength(0);
    expect(screen.queryByText("Można wysłać z uwagami")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Adres Kindle dla review.pdf")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Wyślij na Kindle" })).toHaveAttribute(
      "title",
      expect.stringContaining("Uwagi jakości"),
    );
    await user.click(screen.getByRole("button", { name: "Wyślij na Kindle" }));
    expect(await screen.findByText("Wysłano EPUB do r***@kindle.com")).toBeInTheDocument();
    expect(screen.queryByText("Wysyłka niedostępna")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/convert/delivery/job-review/email",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to: "reader@kindle.com", artifact: "epub" }),
        }),
      );
    });
  });

  it("opens dedicated file details from the Library row", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-ready",
                filename: "sample.pdf",
                status: "ready",
                source_type: "pdf",
                elapsed_seconds: 12.4,
                output_size_bytes: 2048,
                artifacts: { input: { filename: "sample.pdf", location: "output/artifacts/job-ready/input/sample.pdf" } },
                quality_state: { release_verdict: "release_ready", premium_ready: true },
                email_delivery: {
                  diagnostics: {
                    smtp: {
                      host: "smtp.example.com",
                      port: 587,
                      security: "starttls",
                      accepted_by_smtp: true,
                      from_matches_smtp_username: true,
                    },
                    message: { content_type: "multipart/mixed", has_plain_text_body: true },
                    attachment: {
                      filename: "sample.epub",
                      content_type: "application/epub+zip",
                      content_disposition: "attachment",
                      content_transfer_encoding: "base64",
                      size_bytes: 2048,
                      sha256: "a".repeat(64),
                    },
                  },
                },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            profile: { ...defaultProfile, email_delivery: { ...defaultProfile.email_delivery, default_recipient: "reader@kindle.com" } },
          }),
        };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    await user.click(await screen.findByRole("button", { name: "sample.pdf" }));

    expect(screen.getByRole("heading", { name: "Szczegóły pliku" })).toBeInTheDocument();
    expect(screen.getAllByText("sample.pdf").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("job-ready")).toBeInTheDocument();
    expect(screen.getAllByText("2.0 KB").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByLabelText("Informacje o aktywnym zadaniu")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Zmniejsz rozmiar/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kadruj" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Usuń strony" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Kadruj" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Rozmiar").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("heading", { name: "Diagnostyka SMTP" })).toBeInTheDocument();
    expect(screen.getByText("smtp.example.com:587 / starttls")).toBeInTheDocument();
    expect(screen.getByText("sample.epub / application/epub+zip / attachment")).toBeInTheDocument();
    expect(screen.queryByText("Advanced technical payload")).not.toBeInTheDocument();
    expect(screen.queryByText("Error and debug panel")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Strona główna KindleMaster" }));
    expect(await screen.findByRole("heading", { name: "Konwersja" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Informacje o aktywnym zadaniu")).not.toBeInTheDocument();
  });

  it("opens final HTML PGN/FEN as the primary chess reader action", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-chess",
                filename: "chess.pdf",
                status: "ready",
                source_type: "pdf",
                download_url: "/convert/download/job-chess",
                artifacts: {
                  chess_pgn: {
                    filename: "chess_games.pgn",
                    download_url: "/convert/artifact/job-chess/chess_pgn",
                    content_type: "application/x-chess-pgn",
                    label: "PGN",
                    available: true,
                    status: "available",
                    message: "PGN gotowy do pobrania.",
                  },
                  chess_pgn_html: {
                    filename: "chess_games.html",
                    download_url: "/convert/artifact/job-chess/chess_pgn_html",
                    content_type: "text/html",
                    artifact_type: "final_pdf_two_crop_reader",
                    final_reader_available: true,
                    final_reader_health: {
                      status: "PASS",
                      side_unknown_count: 0,
                      trusted_marker_count: 12,
                      side_marker_crop_count: 12,
                      empty_img_src_count: 0,
                      diagrams_total: 12,
                      fen_accepted: 10,
                    },
                  },
                  pdf_layout_preview: {
                    filename: "pdf_layout_preview.html",
                    download_url: "/convert/artifact/job-chess/pdf_layout_preview",
                    content_type: "text/html",
                  },
                  chess_diagrams: {
                    filename: "chess_diagrams.json",
                    download_url: "/convert/artifact/job-chess/chess_diagrams",
                    content_type: "application/json",
                  },
                  chess_glyph_diagnostics: {
                    filename: "chess_glyph_diagnostics.json",
                    download_url: "/convert/artifact/job-chess/chess_glyph_diagnostics",
                    content_type: "application/json",
                  },
                },
                chess_files: {
                  chess_pgn: {
                    key: "chess_pgn",
                    label: "PGN",
                    available: true,
                    status: "available",
                    download_url: "/convert/artifact/job-chess/chess_pgn",
                    message: "PGN gotowy do pobrania.",
                    exportable_pgn_count: 1,
                  },
                  chess_pgn_html: {
                    key: "chess_pgn_html",
                    label: "HTML PGN/FEN",
                    available: true,
                    status: "available",
                    artifact_type: "final_pdf_two_crop_reader",
                    download_url: "/convert/artifact/job-chess/chess_pgn_html",
                    final_reader_available: true,
                  },
                },
                artifact_type: "final_pdf_two_crop_reader",
                final_reader_available: true,
                final_reader_health: {
                  status: "PASS",
                  side_unknown_count: 0,
                  trusted_marker_count: 12,
                  side_marker_crop_count: 12,
                  empty_img_src_count: 0,
                  diagrams_total: 12,
                  fen_accepted: 10,
                },
                quality_state: { release_verdict: "ready_with_review", premium_ready: false },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") {
        return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    const pgnLink = await screen.findByRole("link", { name: /Pobierz PGN/ });
    expect(pgnLink).toHaveAttribute("href", "/convert/artifact/job-chess/chess_pgn");
    const primaryReaderLink = await screen.findByRole("link", { name: /HTML PGN\/FEN/ });
    expect(primaryReaderLink).toHaveAttribute("href", "/convert/artifact/job-chess/chess_pgn_html");
    expect(primaryReaderLink).toHaveClass("km-button-primary");
    await user.click(await screen.findByRole("button", { name: "chess.pdf" }));

    expect(screen.getByRole("heading", { name: "Pliki końcowe" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Diagnostyka" })).toBeInTheDocument();
    const finalFiles = within(screen.getByLabelText("Pliki końcowe"));
    expect(finalFiles.getByRole("link", { name: "Finalny EPUB" })).toHaveAttribute("href", "/convert/download/job-chess");
    expect(finalFiles.getByRole("link", { name: "Finalny HTML PGN/FEN" })).toHaveAttribute(
      "href",
      "/convert/artifact/job-chess/chess_pgn_html",
    );
    expect(finalFiles.queryByRole("link", { name: /PGN$/ })).not.toBeInTheDocument();
    expect(finalFiles.queryByRole("link", { name: /PDF layout preview/ })).not.toBeInTheDocument();
    expect(finalFiles.queryByRole("link", { name: /chess_diagrams\.json/ })).not.toBeInTheDocument();
    const readiness = within(screen.getByRole("heading", { name: "Gotowość szachowa" }).closest(".km-card") as HTMLElement);
    expect(readiness.getByText("Ready")).toBeInTheDocument();
    expect(readiness.getByText("Finalny HTML PGN/FEN ma zaakceptowane dane i zaufany marker ruchu.")).toBeInTheDocument();
    const readinessMetrics = within(readiness.getByLabelText("Metryki gotowości szachowej"));
    expect(readinessMetrics.getByText("Diagramy")).toBeInTheDocument();
    expect(readinessMetrics.getByText("FEN accepted")).toBeInTheDocument();
    expect(readinessMetrics.getByText("PGN accepted")).toBeInTheDocument();
    expect(readinessMetrics.getByText("Trusted marker")).toBeInTheDocument();
    expect(readinessMetrics.getByText("Marker crop")).toBeInTheDocument();
    expect(readinessMetrics.getByText("10")).toBeInTheDocument();
    expect(readinessMetrics.getAllByText("12").length).toBeGreaterThanOrEqual(3);
    const diagnostics = within(screen.getByLabelText("Diagnostyka"));
    expect(diagnostics.getByRole("link", { name: "PGN" })).toHaveAttribute("href", "/convert/artifact/job-chess/chess_pgn");
    expect(diagnostics.getByRole("link", { name: /PDF layout preview \(audyt layoutu\)/ })).toHaveAttribute(
      "href",
      "/convert/artifact/job-chess/pdf_layout_preview",
    );
    expect(diagnostics.getByRole("link", { name: "chess_diagrams.json" })).toHaveAttribute(
      "href",
      "/convert/artifact/job-chess/chess_diagrams",
    );
    expect(diagnostics.getByRole("link", { name: "chess_glyph_diagnostics.json" })).toHaveAttribute(
      "href",
      "/convert/artifact/job-chess/chess_glyph_diagnostics",
    );
  });

  it("shows a chess reader blocker instead of falling back to PDF layout preview", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-chess-blocked",
                filename: "blocked-chess.pdf",
                status: "ready",
                source_type: "pdf",
                download_url: "/convert/download/job-chess-blocked",
                artifacts: {
                  chess_pgn: {
                    filename: "chess_games.pgn",
                    content_type: "application/x-chess-pgn",
                    label: "PGN",
                    available: false,
                    status: "unavailable",
                    reason: "no_accepted_pgn_records",
                    message: "PGN niedostepny: brak zaakceptowanych partii",
                  },
                  chess_pgn_html: {
                    filename: "chess_games.html",
                    download_url: "/convert/artifact/job-chess-blocked/chess_pgn_html",
                    content_type: "text/html",
                    artifact_type: "final_pdf_two_crop_reader",
                    final_reader_available: false,
                    final_reader_health: {
                      status: "FAIL",
                      decision: "fail",
                      blockers: ["mass_side_to_move_unknown", "empty_img_src"],
                      diagrams_total: 4,
                      fen_accepted: 0,
                      side_unknown_count: 4,
                      trusted_marker_count: 0,
                      side_marker_crop_count: 0,
                    },
                    final_reader_blockers: ["mass_side_to_move_unknown", "empty_img_src"],
                  },
                  pdf_layout_preview: {
                    filename: "pdf_layout_preview.html",
                    download_url: "/convert/artifact/job-chess-blocked/pdf_layout_preview",
                    content_type: "text/html",
                  },
                },
                chess_files: {
                  chess_pgn: {
                    key: "chess_pgn",
                    label: "PGN",
                    available: false,
                    status: "unavailable",
                    reason: "no_accepted_pgn_records",
                    message: "PGN niedostepny: brak zaakceptowanych partii",
                    candidate_game_count: 2,
                    exportable_pgn_count: 0,
                  },
                  chess_pgn_html: {
                    key: "chess_pgn_html",
                    label: "HTML PGN/FEN",
                    available: false,
                    status: "blocked",
                    artifact_type: "final_pdf_two_crop_reader",
                    final_reader_available: false,
                    final_reader_blockers: ["mass_side_to_move_unknown", "empty_img_src"],
                  },
                },
                artifact_type: "final_pdf_two_crop_reader",
                final_reader_available: false,
                final_reader_blockers: ["mass_side_to_move_unknown", "empty_img_src"],
                engine_analysis_gate: {
                  schema: "kindlemaster.chess_engine.gate.v1",
                  diagram_count: 4,
                  eligible_count: 0,
                  analyzed_count: 0,
                  unavailable_count: 4,
                  engine_available: false,
                  engine_reader_available: false,
                  availability: "unavailable",
                  top_reasons: [{ reason: "fen_not_accepted", count: 4 }],
                },
                quality_state: { release_verdict: "ready_with_review", premium_ready: false },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") {
        return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    expect(screen.queryByRole("link", { name: /HTML PGN\/FEN/ })).not.toBeInTheDocument();
    expect(screen.getByText("HTML PGN/FEN niedostepny")).toBeInTheDocument();
    expect(screen.getByText("PGN niedostepny")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "blocked-chess.pdf" }));
    const finalFiles = within(screen.getByLabelText("Pliki końcowe"));
    expect(finalFiles.getByText("HTML PGN/FEN niedostepny")).toBeInTheDocument();
    expect(
      finalFiles.getAllByText((_content, element) =>
        Boolean(element?.textContent?.includes("mass_side_to_move_unknown") && element.textContent.includes("empty_img_src")),
      ).length,
    ).toBeGreaterThan(0);
    const readiness = within(screen.getByRole("heading", { name: "Gotowość szachowa" }).closest(".km-card") as HTMLElement);
    expect(readiness.getByText("Review only")).toBeInTheDocument();
    expect(readiness.getByText("Diagramy albo partie wymagają przeglądu; finalny reader nie udaje pełnego rozczytania.")).toBeInTheDocument();
    expect(readiness.getByText("Co blokuje finalny reader")).toBeInTheDocument();
    expect(
      readiness.getAllByText((_content, element) =>
        Boolean(
          element?.textContent?.includes("mass_side_to_move_unknown")
            && element.textContent.includes("empty_img_src")
            && element.textContent.includes("FEN accepted=0")
            && element.textContent.includes("trusted_marker_count=0"),
        ),
      ).length,
    ).toBeGreaterThan(0);
    const readinessMetrics = within(readiness.getByLabelText("Metryki gotowości szachowej"));
    expect(readinessMetrics.getByText("Diagramy")).toBeInTheDocument();
    expect(readinessMetrics.getByText("FEN accepted")).toBeInTheDocument();
    expect(readinessMetrics.getByText("Trusted marker")).toBeInTheDocument();
    expect(readinessMetrics.getAllByText("0").length).toBeGreaterThanOrEqual(4);
    const engineGate = within(screen.getByRole("heading", { name: "Engine analysis" }).closest(".km-card") as HTMLElement);
    expect(engineGate.getByText(/Engine analysis: unavailable/)).toBeInTheDocument();
    expect(engineGate.getByText(/Reason: fen_not_accepted/)).toBeInTheDocument();
    expect(engineGate.getByText("Reader nie pokazuje aktywnej analizy silnika.")).toBeInTheDocument();
    expect(within(engineGate.getByLabelText("Metryki dostÄ™pnoĹ›ci analizy silnika")).getByText("Unavailable")).toBeInTheDocument();
    expect(finalFiles.queryByRole("link", { name: /HTML PGN\/FEN/ })).not.toBeInTheDocument();
    const diagnostics = within(screen.getByLabelText("Diagnostyka"));
    expect(diagnostics.getByRole("link", { name: /PDF layout preview \(audyt layoutu\)/ })).toHaveAttribute(
      "href",
      "/convert/artifact/job-chess-blocked/pdf_layout_preview",
    );
  });

  it("shows not available when chess payload exists but no diagrams or accepted data are present", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-chess-empty",
                filename: "empty-chess.pdf",
                status: "ready",
                source_type: "pdf",
                download_url: "/convert/download/job-chess-empty",
                chess_files: {
                  chess_pgn_html: {
                    key: "chess_pgn_html",
                    label: "HTML PGN/FEN",
                    available: false,
                    status: "blocked",
                    artifact_type: "final_pdf_two_crop_reader",
                    final_reader_available: false,
                    final_reader_blockers: ["final_reader_missing"],
                  },
                },
                artifact_type: "final_pdf_two_crop_reader",
                final_reader_available: false,
                final_reader_blockers: ["final_reader_missing"],
                quality_state: { release_verdict: "ready_with_review", premium_ready: false },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") {
        return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    await user.click(await screen.findByRole("button", { name: "empty-chess.pdf" }));

    const readiness = within(screen.getByRole("heading", { name: "Gotowość szachowa" }).closest(".km-card") as HTMLElement);
    expect(readiness.getByText("Not available")).toBeInTheDocument();
    expect(readiness.getByText("Brak wykrytych diagramów lub zaakceptowanych danych szachowych.")).toBeInTheDocument();
    expect(
      readiness.getAllByText((_content, element) =>
        Boolean(element?.textContent?.includes("final_reader_missing") && element.textContent.includes("diagrams_detected=0")),
      ).length,
    ).toBeGreaterThan(0);
  });

  it("compresses the saved source PDF from file details", async () => {
    const user = userEvent.setup();
    let resolveCompression!: (response: Response) => void;
    const pendingCompression = new Promise<Response>((resolve) => {
      resolveCompression = resolve;
    });
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-source",
                filename: "source.pdf",
                source_type: "pdf",
                status: "ready",
                output_size_bytes: 4096,
                artifacts: {
                  input: {
                    filename: "source.pdf",
                    download_url: "https://storage.example.com/signed-source.pdf",
                    signed_url: { available: true, url: "https://storage.example.com/signed-source.pdf" },
                  },
                },
                quality_state: { release_verdict: "release_ready", premium_ready: true },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") {
        return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      }
      if (url === "/pdf/compress/job/job-source" && init?.method === "POST") {
        const formData = init.body as FormData;
        expect(formData.get("file")).toBeNull();
        expect(formData.get("profile")).toBe("balanced");
        return pendingCompression;
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    await user.click(await screen.findByRole("button", { name: "source.pdf" }));
    await user.click(screen.getByRole("button", { name: /Zmniejsz rozmiar/ }));

    expect(screen.getByText("Zmniejszanie PDF")).toBeInTheDocument();
    expect(screen.getByText(/przygotowuję bezpieczny profil kompresji/)).toBeInTheDocument();
    expect(screen.getByText(/Rekoduję obrazy/)).toBeInTheDocument();

    resolveCompression({
          ok: true,
          json: async () => ({
            success: true,
            job_id: "compress-source",
            original_size_bytes: 4096,
            compressed_size_bytes: 2048,
            reduction_percent: 50,
            download_url: "/pdf/compress/download/compress-source",
            download_name: "source.compressed.pdf",
            quality_profile: "balanced",
            method: "ghostscript+qpdf",
            warnings: [],
          }),
        } as Response);

    expect(await screen.findByText("PDF zmniejszony")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Pobierz mniejszy PDF/ })).toHaveAttribute("href", "/pdf/compress/download/compress-source");
    expect(screen.queryByRole("button", { name: /Użyj mniejszego PDF do konwersji/ })).not.toBeInTheDocument();
  });

  it("deletes a publication from the Library after confirmation", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/convert/jobs/delete-me" && init?.method === "DELETE") {
        return { ok: true, json: async () => ({ success: true, status: "deleted", job_id: "delete-me" }) };
      }
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "delete-me",
                filename: "delete-me.pdf",
                source_type: "pdf",
                status: "ready",
                output_size_bytes: 2048,
                quality_state: { release_verdict: "release_ready", premium_ready: true },
              },
              {
                job_id: "keep-me",
                filename: "keep-me.pdf",
                source_type: "pdf",
                status: "ready",
                output_size_bytes: 1024,
                quality_state: { release_verdict: "release_ready", premium_ready: true },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") {
        return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    expect(await screen.findByRole("button", { name: "delete-me.pdf" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Usuń publikację delete-me.pdf" }));

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("delete-me.pdf"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "delete-me.pdf" })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "keep-me.pdf" })).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/convert/jobs/delete-me", expect.objectContaining({ method: "DELETE" }));
    });
    confirmSpy.mockRestore();
  });

  it("lets file details send the final EPUB to Kindle and hides the details report card", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-pdf",
                filename: "cropped-source.pdf",
                source_type: "pdf",
                source_preview_url: "/convert/preview/job-pdf/input",
                status: "ready",
                download_url: "/convert/download/job-pdf",
                quality_state: {
                  score: 91,
                  release_verdict: "release_ready",
                  send_to_kindle_ready: true,
                  reports: {
                    report_json: "/convert/report/job-pdf.json",
                    report_markdown: "/convert/report/job-pdf.md",
                  },
                },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            profile: { ...defaultProfile, email_delivery: { ...defaultProfile.email_delivery, default_recipient: "reader@kindle.com" } },
          }),
        };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: true } }) };
      }
      if (url === "/convert/artifact/job-pdf/input") {
        return {
          ok: true,
          arrayBuffer: async () => new Uint8Array([37, 80, 68, 70]).buffer,
        };
      }
      if (url === "/convert/delivery/job-pdf/email" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            delivery: { status: "sent", artifact: "input", masked_recipient: "r***@kindle.com" },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    await user.click(await screen.findByRole("button", { name: "cropped-source.pdf" }));

    expect(screen.getByRole("link", { name: /PDF źródłowy/ })).toHaveAttribute("href", "/convert/preview/job-pdf/input");
    expect(screen.getByRole("button", { name: "Kadruj" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Kadruj" }));
    expect(screen.getByLabelText("Kadrowanie PDF w nowym układzie")).toBeInTheDocument();
    expect(screen.getByText("Podgląd i ustawienia kadrowania są dostępne bez przechodzenia do starego panelu.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Otwórz PDF" })).toHaveAttribute("href", "/convert/artifact/job-pdf/input");
    expect(await screen.findByText("Podgląd strony 1 z 2.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Poprzednia strona" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Następna strona" })).toBeEnabled();
    expect(screen.getByLabelText("Warstwa zaznaczania kadru")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kadruj cały PDF" })).toBeEnabled();
    expect(screen.getByLabelText("Usuwanie stron PDF")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("np. 2,4,5 albo 1-3")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Kadruj" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Raport jakości" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "JSON" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Markdown" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Format wysyłki na Kindle")).toBeInTheDocument();
    expect(screen.queryByText("Do: r***@kindle.com")).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Format wysyłki na Kindle"), "input");
    await user.click(screen.getByRole("button", { name: "Wyślij na Kindle" }));

    expect(await screen.findByText("Wysłano PDF do r***@kindle.com")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/convert/delivery/job-pdf/email",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to: "reader@kindle.com", artifact: "input" }),
        }),
      );
    });
  });

  it("shows a stable source PDF unavailable message without retrying missing crop preview", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "missing-pdf",
                filename: "missing-source.pdf",
                source_type: "pdf",
                status: "ready",
                download_url: "/convert/download/missing-pdf",
                quality_state: { score: 70, release_verdict: "review" },
              },
            ],
          }),
        };
      }
      if (url === "/user/profile") {
        return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: true } }) };
      }
      if (url === "/convert/artifact/missing-pdf/input") {
        return {
          ok: false,
          status: 404,
          json: async () => ({ error_code: "source_artifact_unavailable" }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    await user.click(await screen.findByRole("button", { name: "missing-source.pdf" }));
    await user.click(screen.getByRole("button", { name: "Kadruj" }));

    expect(await screen.findByText("Nie udało się pobrać PDF źródłowego; odśwież bibliotekę albo wgraj plik ponownie.")).toBeInTheDocument();
    const previewCalls = fetchMock.mock.calls.filter(([input]) => String(input) === "/convert/artifact/missing-pdf/input");
    expect(previewCalls).toHaveLength(1);
  });

  it("runs manual EPUB repair from file details and shows the result", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "job-blocked",
                filename: "blocked.pdf",
                status: "ready",
                quality_state: {
                  release_verdict: "release_blocked",
                  send_to_kindle_ready: false,
                  send_to_kindle_blockers: [{ code: "kindle_delivery_progressive_jpeg", message: "1 progressive JPEG" }],
                },
              },
            ],
          }),
        };
      }
      if (url === "/convert/repair/job-blocked" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            job: { job_id: "job-blocked", filename: "blocked.pdf", status: "ready" },
            quality_state: {
              release_verdict: "release_ready",
              send_to_kindle_ready: true,
              send_to_kindle_blockers: [],
              auto_repair: { status: "applied", actions: ["reencode_progressive_jpeg"], selected_candidate: "auto_repair" },
            },
            auto_repair: { status: "applied", actions: ["reencode_progressive_jpeg"], selected_candidate: "auto_repair" },
          }),
        };
      }
      if (url === "/user/profile") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            profile: { ...defaultProfile, email_delivery: { ...defaultProfile.email_delivery, default_recipient: "reader@kindle.com" } },
          }),
        };
      }
      if (url === "/convert/delivery/config") {
        return { ok: true, json: async () => ({ success: true, delivery: { configured: true } }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    await user.click(await screen.findByRole("button", { name: "blocked.pdf" }));
    expect(
      screen.getAllByText("EPUB zawiera obrazy progressive JPEG (1); mail może zostać wysłany, ale zalecane jest przekodowanie do baseline JPEG albo PNG.").length,
    ).toBeGreaterThanOrEqual(1);
    const repairCard = screen.getByRole("heading", { name: "Wysyłka na Kindle" }).closest(".km-card");
    expect(repairCard).not.toHaveTextContent("EPUB zawiera obrazy progressive JPEG");
    expect(screen.queryByLabelText("Adres Kindle dla blocked.pdf")).not.toBeInTheDocument();
    expect(screen.queryByText("Do: r***@kindle.com")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Napraw ponownie" }));

    expect(await screen.findByText("Naprawa zastosowana i jakość przeliczona ponownie.")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/convert/repair/job-blocked", { method: "POST" });
    });
  });

  it("retries an interrupted conversion from the preserved input artifact", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/convert/jobs")) {
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                job_id: "interrupted-job",
                filename: "Fundamenty 1-1.pdf",
                status: "failed",
                error_code: "application_restart",
                message: "Konwersja przerwana przez restart aplikacji.",
                artifacts: { input: { location: "output/artifacts/interrupted/input/Fundamenty-1-1.pdf" } },
                quality_state: { score: 0, release_verdict: "failed" },
              },
            ],
          }),
        };
      }
      if (url === "/convert/retry/interrupted-job" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            job_id: "retry-job",
            retry_of: "interrupted-job",
            status: "queued",
            filename: "Fundamenty 1-1.pdf",
            source_type: "pdf",
          }),
        };
      }
      if (url === "/convert/status/retry-job") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            job_id: "retry-job",
            filename: "Fundamenty 1-1.pdf",
            status: "ready",
            message: "EPUB gotowy do pobrania.",
            quality_state: { score: 92, release_verdict: "release_ready", premium_ready: true },
          }),
        };
      }
      if (url === "/user/profile") return { ok: true, json: async () => ({ success: true, profile: defaultProfile }) };
      if (url === "/convert/delivery/config") return { ok: true, json: async () => ({ success: true, delivery: { configured: false } }) };
      if (url === "/auth/config") return { ok: true, json: async () => ({ success: true, auth: { enabled: false, configured: false } }) };
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    await user.click(await screen.findByRole("button", { name: "Fundamenty 1-1.pdf" }));
    await user.click(await screen.findByRole("button", { name: "Ponów rozczytanie" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/convert/retry/interrupted-job", expect.objectContaining({ method: "POST" }));
    });
    expect(await screen.findByText("EPUB gotowy do pobrania.")).toBeInTheDocument();
  }, 10000);

  it("saves user profile settings without SMTP secrets", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Ustawienia" }));
    expect(screen.getByText(/Wysyłka na Kindle/)).toBeInTheDocument();
    expect(screen.queryByText("Dostawca")).not.toBeInTheDocument();
    const advancedSettings = screen.getByText("Zaawansowane ustawienia konwersji").closest("details");
    expect(advancedSettings).not.toHaveAttribute("open");
    await user.click(screen.getByText("Zaawansowane ustawienia konwersji"));
    expect(advancedSettings).toHaveAttribute("open");
    await user.selectOptions(screen.getByLabelText("Domyślny profil konwersji"), "magazine");
    await user.selectOptions(screen.getByLabelText("Domyślny język OCR"), "en");
    await user.type(screen.getByLabelText("Host SMTP"), "smtp.example.com");
    await user.type(screen.getByLabelText("Użytkownik SMTP"), "apikey");
    await user.type(screen.getByLabelText("Adres nadawcy SMTP"), "operator@example.com");
    await user.type(screen.getByLabelText("Domyślny adres Kindle"), "reader@kindle.com");
    await user.click(screen.getByRole("button", { name: "Zapisz ustawienia" }));

    expect(await screen.findByText("Ustawienia zapisane")).toBeInTheDocument();
    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([url, init]) => String(url) === "/user/profile" && init?.method === "PUT");
      expect(putCall).toBeTruthy();
      const body = JSON.parse(String(putCall?.[1]?.body));
      expect(body.email_delivery.host).toBe("smtp.example.com");
      expect(body.email_delivery.default_recipient).toBe("reader@kindle.com");
      expect(JSON.stringify(body).toLowerCase()).not.toContain("password");
    });
  }, 10000);
});
