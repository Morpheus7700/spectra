import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// One build emits both the installable PWA and the website. They are the same
// artifact; the scene code is never forked between them.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  build: { target: "es2022", sourcemap: true },
});
