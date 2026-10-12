import { defineConfig } from 'vitest/config';

export default defineConfig({
  build: {
    outDir: 'generated',
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        entryFileNames: 'app.js',
        chunkFileNames: 'chunk-[hash].js',
        assetFileNames: 'style.css',
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['src/test-setup.ts'],
    restoreMocks: true,
    clearMocks: true,
  },
});
