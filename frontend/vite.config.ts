import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Một cổng duy nhất cho toàn bộ frontend; strictPort để không tự nhảy sang 5174 khi 5173 bị chiếm
export default defineConfig({
  plugins: [react()],
  resolve: { preserveSymlinks: true },
  server: { port: 5173, strictPort: true },
  preview: { port: 5173, strictPort: true },
});
