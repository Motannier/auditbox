import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { chrome80Styles } from "./browser-compat-css.mjs";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../dist", emptyOutDir: true, target: "chrome80", cssTarget: "chrome80" },
  css: {
    postcss: {
      plugins: [chrome80Styles()],
    },
  },
});
