import { defineConfig } from 'vitest/config'

export default defineConfig({
  // The automatic JSX runtime, as the Vite build uses through the React
  // plugin. Without it tests compile JSX the classic way and every component
  // file needs an otherwise-unused `import React` just to pass here.
  esbuild: {
    jsx: 'automatic',
  },
  test: {
    environment: 'jsdom',
    globals: true,
    reporters: ['dot'],
  },
})
