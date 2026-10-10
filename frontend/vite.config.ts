import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on :8000; in the Docker image the API serves the built files itself.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
