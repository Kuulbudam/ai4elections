import { useState } from 'react'
import { ApiError } from '../lib/api'
import { canRecord, canSpeak, hasVoice, MAX_SECONDS, speak, stopSpeaking, transcribe, useRecorder, type Lang } from '../lib/voice'

const NAME: Record<Lang, string> = { en: 'English', pcm: 'Pidgin', ha: 'Hausa', yo: 'Yoruba', ig: 'Igbo' }

/** Press to talk, press again to stop. The words go to onText; the recording is not kept. */
export function MicButton({
  projectId,
  language,
  onText,
  onError,
  disabled,
  label = 'Speak',
}: {
  projectId: string
  language: Lang
  onText: (text: string) => void
  onError: (message: string) => void
  disabled?: boolean
  label?: string
}) {
  const { recording, seconds, start, stop } = useRecorder()
  const [sending, setSending] = useState(false)
  if (!canRecord()) return null

  async function toggle() {
    if (recording) return stop()
    let clip: Blob | null
    try {
      clip = await start()
    } catch {
      return onError('Allow the microphone for this site to speak your question.')
    }
    if (!clip) return
    setSending(true)
    try {
      onText(await transcribe(projectId, clip, language))
    } catch (err) {
      onError(err instanceof ApiError ? err.message : 'Could not turn the recording into text. Try again.')
    } finally {
      setSending(false)
    }
  }

  return (
    <button
      type="button"
      className={`mic${recording ? ' mic-on' : ''}`}
      aria-pressed={recording}
      disabled={disabled || sending}
      onClick={() => void toggle()}
      title={`Speak in ${NAME[language]}. Up to ${MAX_SECONDS} seconds.`}
    >
      <svg className="icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z M5 11a7 7 0 0 0 14 0 M12 18v3" />
      </svg>
      <span>{sending ? 'Listening…' : recording ? `Stop (${seconds}s)` : label}</span>
    </button>
  )
}

/** Read an answer aloud with the device's voice. Hidden when the device has no voice for the language. */
export function ReadAloud({ text, language }: { text: string; language: Lang }) {
  const [on, setOn] = useState(false)
  if (!canSpeak()) return null
  if (!hasVoice(language)) return <span className="small muted">This device has no {NAME[language]} voice to read it aloud.</span>
  return (
    <button
      type="button"
      className="link"
      aria-pressed={on}
      onClick={() => {
        if (on) {
          stopSpeaking()
          setOn(false)
          return
        }
        setOn(true)
        void speak(text, language).then(() => setOn(false))
      }}
    >
      {on ? 'Stop reading' : 'Read aloud'}
    </button>
  )
}
