import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

function redirectOperatorRoot() {
  return (
    req: { url?: string },
    res: { statusCode: number; setHeader: (name: string, value: string) => void; end: () => void },
    next: () => void,
  ) => {
    const requestUrl = req.url ?? "";
    if (requestUrl !== "/operator" && !requestUrl.startsWith("/operator?")) {
      next();
      return;
    }
    const query = requestUrl.slice("/operator".length);
    res.statusCode = 302;
    res.setHeader("Location", `/operator/${query}`);
    res.end();
  };
}

export default defineConfig({
  base: "/operator/",
  plugins: [
    react(),
    {
      name: "operator-root-redirect",
      configureServer(server) {
        server.middlewares.use(redirectOperatorRoot());
      },
      configurePreviewServer(server) {
        server.middlewares.use(redirectOperatorRoot());
      },
    },
  ],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/health": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  preview: { host: "0.0.0.0", port: 4173 },
});
