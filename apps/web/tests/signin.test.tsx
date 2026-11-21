import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const signIn = vi.fn();

vi.mock("../src/shared/api/client", async (original) => {
  const actual = await original<typeof import("../src/shared/api/client")>();
  return {
    ...actual,
    signIn,
    fetchMe: () => Promise.reject(new actual.SignedOut()),
  };
});

const { App } = await import("../src/app/App");

afterEach(() => {
  cleanup();
  signIn.mockClear();
  window.history.replaceState(null, "", "/");
});

describe("sign-in screen", () => {
  it("goes straight to the one sign-in page without a session", async () => {
    render(<App />);
    await vi.waitFor(() => {
      expect(signIn).toHaveBeenCalledOnce();
    });
  });

  it("says why a sign-in did not complete", async () => {
    window.history.replaceState(null, "", "/?auth_error=role");
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("no PalletWise role");
  });
});
