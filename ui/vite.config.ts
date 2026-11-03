import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The UI talks only to agent-service. In dev, Vite proxies /api to it (nginx does the same in Docker),
// so the browser sees one origin and never needs CORS or ledger credentials.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8090", rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
});
