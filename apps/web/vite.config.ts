import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    // Development only: the stack's edge serves the API. Its CSRF check accepts the stack's own
    // origin, so the proxy presents that origin; sign in at http://localhost:8080 first (the
    // session cookie is shared across ports on localhost), then work at http://localhost:5173.
    proxy: {
      "/api": { target: "http://localhost:8080", headers: { Origin: "http://localhost:8080" } },
    },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
