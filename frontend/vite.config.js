import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" so the built files load from whatever path Streamlit serves the component at
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", emptyOutDir: true, assetsInlineLimit: 120000 },
});
