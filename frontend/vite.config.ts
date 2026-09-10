import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiBaseUrl = env.API_BASE_URL || env.VITE_API_BASE_URL || "";

  return {
    plugins: [react()],
    define: {
      __API_BASE_URL__: JSON.stringify(apiBaseUrl),
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": "http://127.0.0.1:8012",
      },
    },
    preview: {
      host: "127.0.0.1",
      port: 4173,
    },
  };
});
