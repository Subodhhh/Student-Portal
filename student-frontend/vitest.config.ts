import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const API = "https://student.test/api";

// Kept separate from vite.config.ts because vitest bundles its own Vite types.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test/setup.ts"],
    env: Object.fromEntries(
      ["AUTH", "PROFILE", "ENROLLMENT", "TEST", "ATTEMPT", "RESULT", "RANKING", "NOTIFICATION"]
        .map((name) => [`VITE_${name}_API_URL`, API])
    ),
  },
});
