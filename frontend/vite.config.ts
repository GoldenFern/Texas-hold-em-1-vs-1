import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  base: mode === "production" ? "/static/dist/" : "/",
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5050",
        changeOrigin: true,
      },
      "/socket.io": {
        target: "http://127.0.0.1:5050",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: "../static/dist",
    emptyOutDir: true,
    assetsDir: "assets",
    sourcemap: false,
  },
}));
