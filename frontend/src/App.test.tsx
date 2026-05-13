import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const fetchMock = vi.fn();

describe("Sprint 4 React shell", () => {
  beforeEach(() => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ jobs: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("renders the operational dashboard without a landing page wrapper", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Conversion dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /convert/i })).toBeDisabled();
    expect(screen.getByRole("tab", { name: "Quality report" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "Artifacts" }));
    expect(screen.getByText("Artifact and download panel")).toBeInTheDocument();
  });

  it("enables conversion when a file is selected", async () => {
    const user = userEvent.setup();
    render(<App />);

    const file = new File(["pdf"], "sample.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("Upload PDF or DOCX"), file);

    expect(screen.getByText("sample.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /convert/i })).toBeEnabled();
  });

  it("switches tabs and exposes the debug panel", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("tab", { name: "Debug" }));

    expect(screen.getByRole("tab", { name: "Debug" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Error and debug panel")).toBeInTheDocument();
    expect(screen.getByText(/No Sentry event/)).toBeInTheDocument();
  });
});
