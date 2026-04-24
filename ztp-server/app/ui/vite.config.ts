import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ztp": "http://localhost:8000",
      "/configs": "http://localhost:8000",
      "/log": "http://localhost:8000",
    },
  },
});
