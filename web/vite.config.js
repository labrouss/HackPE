import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { viteSingleFile } from 'vite-plugin-singlefile';

// base './' -> works under https://<user>.github.io/<repo>/ without knowing the repo name.
// `npm run build:single` makes one self-contained HTML file (handy to share or open offline).
export default defineConfig(({ mode }) => ({
  base: './',
  plugins: [react(), ...(mode === 'single' ? [viteSingleFile()] : [])],
  build: { outDir: mode === 'single' ? 'dist-single' : 'dist', emptyOutDir: true },
}));
