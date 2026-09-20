import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// The build stamps its own identity in, so the running kiosk can say what it is (see
// StatusFooter.jsx). Version is the repo's semantic version from package.json - the single source
// of truth; the build stamp comes from the environment.
//
// TAU_BUILD_SHA is passed in by scripts/bring-up-tau.ps1 and docker-compose's build args rather
// than being read from git here: `.git` is not in the Docker build context, so a `git rev-parse`
// at this point would fail inside the image and silently stamp every container "unknown".
const pkg = JSON.parse(
  readFileSync(fileURLToPath(new URL('./package.json', import.meta.url)), 'utf8')
)

export default defineConfig({
  plugins: [react()],
  define: {
    __TAU_VERSION__: JSON.stringify(pkg.version),
    __TAU_BUILD__: JSON.stringify(process.env.TAU_BUILD_SHA || 'dev'),
  },
  server: {
    port: 3000,
    host: 'localhost'
  }
})
