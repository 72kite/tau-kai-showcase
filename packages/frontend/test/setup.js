import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// jsdom defaults isSecureContext to false, which makes useVoiceInvoke bail out to the
// insecure-context fallback before it ever touches SpeechRecognition. Real deployment targets
// (HTTPS / localhost) are secure contexts, so present that to the hooks under test.
Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true })

// Unmount anything a test rendered so hook effects (recognition instances, timers) tear down
// between tests and don't leak into the next one.
afterEach(() => {
  cleanup()
})
