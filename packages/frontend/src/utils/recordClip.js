/**
 * Record a fixed-length microphone clip and return it base64-encoded, ready for tau-core's
 * voice endpoints (/api/voice/enroll, /api/voice/challenge/{id}/verify). Fully local capture -
 * used by voiceprint enrollment (PeoplePanel) and the approval challenge flow (ApprovalQueue).
 *
 * Resolves { data_b64, mimeType, filename } or rejects (mic denied / unsupported).
 */
import { MIC_AUDIO_CONSTRAINTS } from './audioConstraints'

export async function recordClip(seconds = 5) {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    throw new Error('audio recording is not supported in this browser')
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: MIC_AUDIO_CONSTRAINTS })
  try {
    const recorder = new MediaRecorder(stream)
    const chunks = []
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data)
    }
    const stopped = new Promise((resolve) => {
      recorder.onstop = resolve
    })
    recorder.start(250)
    await new Promise((resolve) => setTimeout(resolve, seconds * 1000))
    recorder.stop()
    await stopped

    const mimeType = recorder.mimeType || 'audio/webm'
    const blob = new Blob(chunks, { type: mimeType })
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = () => reject(reader.error)
      reader.readAsDataURL(blob)
    })
    return {
      data_b64: dataUrl.slice(dataUrl.indexOf(',') + 1),
      mimeType,
      filename: mimeType.includes('mp4') ? 'clip.mp4' : 'clip.webm',
    }
  } finally {
    stream.getTracks().forEach((t) => t.stop())
  }
}
