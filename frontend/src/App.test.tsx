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
    });
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
    expect(await screen.findByText("reader@example.com")).toBeInTheDocument();
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
    });
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
    expect(screen.getByText("Przetwarzanie")).toBeInTheDocument();
    expect(screen.getByText("Ekstrakcja tekstu z PDF...")).toBeInTheDocument();
    expect(screen.queryByText("Brak ostatnich zadań.")).not.toBeInTheDocument();
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
    expect(screen.queryByLabelText("Sortowanie biblioteki")).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Szukaj w bibliotece"), "alpha");
    expect(screen.getByRole("button", { name: "alpha.pdf" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "zeta.pdf" })).not.toBeInTheDocument();
    expect(screen.getByText("1 z 3")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Szukaj w bibliotece"));
    const table = screen.getByRole("table", { name: "Ostatnie zadania" });
    await user.click(within(table).getByRole("button", { name: /Plik/ }));
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

    expect(await screen.findByText("Wysłano do r***@kindle.com")).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([url]) => String(url))).not.toContain("/convert/quality/job-ready");
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/convert/delivery/job-ready/email",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to: "reader@kindle.com" }),
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
    expect(await screen.findByText("Wysłano do r***@kindle.com")).toBeInTheDocument();
    expect(screen.queryByText("Wysyłka niedostępna")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/convert/delivery/job-review/email",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to: "reader@kindle.com" }),
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
      if (url === "/convert/delivery/job-pdf/email" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            success: true,
            delivery: { status: "sent", artifact: "epub", masked_recipient: "r***@kindle.com" },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Biblioteka" }));
    await user.click(await screen.findByRole("button", { name: "cropped-source.pdf" }));

    expect(screen.getByRole("link", { name: /PDF źródłowy/ })).toHaveAttribute("href", "/convert/preview/job-pdf/input");
    expect(screen.getByRole("link", { name: "Kadruj PDF" })).toHaveAttribute("href", "/legacy");
    expect(screen.queryByRole("heading", { name: "Raport jakości" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "JSON" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Markdown" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Format załącznika Kindle")).not.toBeInTheDocument();
    expect(screen.queryByText("Do: r***@kindle.com")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Wyślij na Kindle" }));

    expect(await screen.findByText("Wysłano EPUB do r***@kindle.com")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/convert/delivery/job-pdf/email",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to: "reader@kindle.com", artifact: "epub" }),
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
    const repairCard = screen.getByRole("heading", { name: "Naprawa i wysyłka" }).closest(".km-card");
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
