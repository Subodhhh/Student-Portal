import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    chunkSizeWarningLimit: 1600,
  },
  server: {
    port: 5174,
    proxy: {
      "/api": {
        target: "http://localhost:8010",
        changeOrigin: true,
      },
    },
  },
});