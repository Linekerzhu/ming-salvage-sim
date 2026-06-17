import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name]-[hash]-r2.js",
        chunkFileNames: "assets/[name]-[hash]-r2.js",
        assetFileNames: "assets/[name]-[hash]-r2[extname]",
        manualChunks(id: string) {
          if (!id.includes("node_modules")) {
            return undefined;
          }
          if (id.includes("lucide-react")) {
            return "icons";
          }
          if (id.includes("react") || id.includes("scheduler")) {
            return "react-vendor";
          }
          return "vendor";
        }
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8010",
      "/portraits/generated": "http://127.0.0.1:8010",
      "/portraits/custom": "http://127.0.0.1:8010"
    }
  }
});
