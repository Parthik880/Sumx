import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: {
    headers: { "Cross-Origin-Opener-Policy": "same-origin-allow-popups" },
    port: 5173,
    strictPort: true,
    proxy: { "/api": process.env.API_TARGET || "http://127.0.0.1:8000" },
  },
});
