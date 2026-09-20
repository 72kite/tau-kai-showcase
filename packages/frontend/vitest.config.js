import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config.js'

// Reuse the real build config (plugins, the __TAU_* defines) so the hooks compile under test
// exactly as they ship, then layer the test-only settings on top. jsdom gives the hooks a DOM +
// window to attach the mocked SpeechRecognition / AudioContext to; globals lets tests use
// describe/it/expect without importing them everywhere.
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./test/setup.js'],
      include: ['test/**/*.test.{js,jsx}'],
    },
  })
)
