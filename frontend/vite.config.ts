import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type Plugin } from "vite-plus";

const srcPath = fileURLToPath(new URL("./src", import.meta.url));
const frontendVersion = (
  JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")) as {
    version: string;
  }
).version;

function cacheFontResponseHeaders(): Plugin {
  return {
    name: "cache-font-response-headers",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (/\.(woff2?|ttf|otf|eot)(?:\?.*)?$/i.test(request.url ?? "")) {
          response.setHeader("Cache-Control", "public, max-age=3600");
        }
        next();
      });
    },
  };
}

/** Only the Vite dev server serves this isolated editor regression page. */
function editorSaveRegressionPage(): Plugin {
  return {
    name: "editor-save-regression-page",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        if (request.url?.split("?")[0] !== "/__editor-save-regressions__/") {
          next();
          return;
        }
        try {
          const html =
            '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"></head>' +
            '<body><div id="root"></div><script type="module" src="/e2e/editor-save-regressions.harness.tsx"></script></body></html>';
          response.setHeader("Content-Type", "text/html; charset=utf-8");
          response.end(await server.transformIndexHtml(request.url ?? "", html));
        } catch (error) {
          next(error);
        }
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendTarget = (env.VITE_BACKEND_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");

  return {
    define: {
      __OPENFIC_FRONTEND_VERSION__: JSON.stringify(frontendVersion),
    },
    plugins: [
      ...(react() as unknown as Plugin[]),
      cacheFontResponseHeaders(),
      editorSaveRegressionPage(),
    ],
    resolve: {
      alias: {
        "@": srcPath,
      },
    },
    build: {
      outDir: "dist",
      target: "esnext",
      rolldownOptions: {
        output: {
          assetFileNames: (assetInfo) => {
            const name = assetInfo.name ?? "";
            return /\.(?:woff2?|ttf|otf|eot)$/i.test(name)
              ? "fonts/[name][extname]"
              : "assets/[name]-[hash][extname]";
          },
        },
      },
    },
    server: {
      host: "127.0.0.1",
      port: 9000,
      cors: true,
      proxy: {
        "/api": {
          target: backendTarget,
          changeOrigin: true,
        },
        "/icons": {
          target: backendTarget,
          changeOrigin: true,
        },
        "/socket.io": {
          target: backendTarget,
          changeOrigin: true,
          ws: true,
        },
        "/covers": {
          target: backendTarget,
          changeOrigin: true,
        },
        "/character-images": {
          target: backendTarget,
          changeOrigin: true,
        },
        "/agent-attachments": {
          target: backendTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
