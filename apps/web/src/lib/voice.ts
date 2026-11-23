import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Schemas } from './api'

export type Lang = Schemas['AnswerLanguage']
export const MAX_SECONDS = 90

/** BCP 47 tags for the browser's voices. Pidgin is read with a Nigerian English voice when there is one. */
const VOICE_LANG: Record<Lang, string[]> = { en: ['en-NG', 'en-GB', 'en'], pcm: ['en-NG', 'en-GB', 'en'], ha: ['ha'], yo: ['yo'], ig: ['ig'] }

export const canRecord = () => typeof window !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && typeof window.MediaRecorder !== 'undefined'
export const canSpeak = () => typeof window !== 'undefined' && 'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined'

function pickMime(): string {
  for (const m of ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']) {
    if (MediaRecorder.isTypeSupported?.(m)) return m
  }
  return ''
}

function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',', 2)[1] ?? '')
    r.onerror = () => reject(new Error('The recording could not be read.'))
    r.readAsDataURL(blob)
  })
}

export async function transcribe(projectId: string, blob: Blob, language: Lang): Promise<string> {
  const out = await api<Schemas['Transcript']>('POST', '/assistant/transcribe', {
    project_id: projectId,
    audio_base64: await toBase64(blob),
    mime_type: blob.type || 'audio/webm',
    language,
  })
  return out.text
}

/** Record from the microphone. stop() resolves with the clip; recording also stops by itself after MAX_SECONDS. */
export function useRecorder() {
  const [recording, setRecording] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const rec = useRef<MediaRecorder | null>(null)
  const done = useRef<((b: Blob | null) => void) | null>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const cleanup = useCallback(() => {
    if (timer.current) clearInterval(timer.current)
    timer.current = null
    rec.current?.stream.getTracks().forEach((t) => t.stop())
    rec.current = null
    setRecording(false)
  }, [])

  useEffect(() => cleanup, [cleanup])

  const start = useCallback(async (): Promise<Blob | null> => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    const mime = pickMime()
    const r = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
    const parts: Blob[] = []
    r.ondataavailable = (e) => e.data.size && parts.push(e.data)
    const result = new Promise<Blob | null>((resolve) => {
      done.current = resolve
      r.onstop = () => {
        cleanup()
        resolve(parts.length ? new Blob(parts, { type: (r.mimeType || mime || 'audio/webm').split(';')[0] }) : null)
      }
    })
    rec.current = r
    r.start()
    setSeconds(0)
    setRecording(true)
    const t0 = Date.now()
    timer.current = setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000)
      setSeconds(s)
      if (s >= MAX_SECONDS && r.state === 'recording') r.stop()
    }, 250)
    return result
  }, [cleanup])

  const stop = useCallback(() => {
    if (rec.current?.state === 'recording') rec.current.stop()
    else done.current?.(null)
  }, [])

  return { recording, seconds, start, stop }
}

function voiceFor(lang: Lang): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis.getVoices()
  for (const tag of VOICE_LANG[lang]) {
    const v = voices.find((x) => x.lang.toLowerCase().replace('_', '-').startsWith(tag.toLowerCase()))
    if (v) return v
  }
  return null
}

/** Whether this device can read text aloud in the language (English voices are assumed to exist). */
export function hasVoice(lang: Lang): boolean {
  if (!canSpeak()) return false
  if (lang === 'en' || lang === 'pcm') return true
  return voiceFor(lang) !== null
}

/** Read text aloud. Resolves when finished or stopped. */
export function speak(text: string, lang: Lang): Promise<void> {
  return new Promise((resolve) => {
    if (!canSpeak()) return resolve()
    window.speechSynthesis.cancel()
    const u = new SpeechSynthesisUtterance(text.replace(/\*\*/g, '').replace(/^\s*[-*•]\s+/gm, ''))
    const v = voiceFor(lang)
    if (v) u.voice = v
    u.lang = v?.lang ?? VOICE_LANG[lang][0]
    u.rate = 1
    u.onend = () => resolve()
    u.onerror = () => resolve()
    window.speechSynthesis.speak(u)
  })
}

export function stopSpeaking(): void {
  if (canSpeak()) window.speechSynthesis.cancel()
}
