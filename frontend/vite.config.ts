import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Сборку (dist/) раздаёт FastAPI: /t/{token} -> index.html, /assets/* -> dist/assets.
export default defineConfig({
  plugins: [react()],
  base: "/",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
