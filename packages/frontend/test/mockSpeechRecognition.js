import { vi } from 'vitest'

/**
 * Minimal stand-in for the browser SpeechRecognition API, just enough to drive useVoiceInvoke's
 * state machine under test: start/stop lifecycle callbacks, and an emit() that shapes results the
 * way the real event does (`event.results[last][0].transcript` + `result.isFinal`).
 */
export class MockSpeechRecognition {
  constructor() {
    MockSpeechRecognition.instances.push(this)
    this.continuous = false
    this.interimResults = false
    this.lang = ''
    this.onstart = null
    this.onresult = null
    this.onerror = null
    this.onend = null
    this.started = false
    this.start = vi.fn(() => {
      this.started = true
      this.onstart?.()
    })
    this.stop = vi.fn(() => {
      this.started = false
    })
  }

  /** Fire a recognition result the way the browser would. */
  emit(transcript, { isFinal = true } = {}) {
    const result = [{ transcript }]
    result.isFinal = isFinal
    this.onresult?.({ results: [result] })
  }
}
MockSpeechRecognition.instances = []

/** The instance the hook's effect created (the last one, after any restart churn). */
export function latestRecognition() {
  return MockSpeechRecognition.instances[MockSpeechRecognition.instances.length - 1]
}

/** Install the mock as window.SpeechRecognition and reset the instance registry. */
export function installSpeechRecognition() {
  MockSpeechRecognition.instances = []
  window.SpeechRecognition = MockSpeechRecognition
  window.webkitSpeechRecognition = MockSpeechRecognition
}

export function uninstallSpeechRecognition() {
  delete window.SpeechRecognition
  delete window.webkitSpeechRecognition
  MockSpeechRecognition.instances = []
}
