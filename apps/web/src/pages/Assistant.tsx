import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, type Schemas } from '../lib/api'
import { getSupabase } from '../lib/supabase'
import { useRole } from '../lib/useRole'

type Answer = Schemas['AssistantAnswer']
type Turn = { role: 'user'; content: string } | { role: 'assistant'; content: string; answer: Answer }

const EXAMPLES = [
  'Which LGAs have the most unusual turnout?',
  'Show the five most unusual polling units and why they were flagged.',
  'What data problems were found in the latest upload?',
  'How many claims are still unverified?',
]

export default function Assistant() {
  const { projectId = '' } = useParams()
  const [project, setProject] = useState<{ org_id: string; election_name: string } | null>(null)
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const role = useRole(project?.org_id)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getSupabase()?.from('projects').select('org_id, election_name').eq('id', projectId).maybeSingle().then(({ data }) => setProject(data))
    api<Schemas['AssistantStatus']>('GET', '/assistant/status')
      .then((s) => setEnabled(s.enabled))
      .catch(() => setEnabled(false))
  }, [projectId])

  useEffect(() => end.current?.scrollIntoView?.({ block: 'end' }), [turns, busy])

  async function send(e?: FormEvent, text?: string) {
    e?.preventDefault()
    const q = (text ?? question).trim()
    if (!q || busy) return
    setError(null)
    setBusy(true)
    const history = turns.map((t) => ({ role: t.role, content: t.content })).slice(-8)
    setTurns((t) => [...t, { role: 'user', content: q }])
    setQuestion('')
    try {
      const answer = await api<Answer>('POST', '/assistant/ask', { project_id: projectId, question: q, history }, { 'Idempotency-Key': crypto.randomUUID() })
      setTurns((t) => [...t, { role: 'assistant', content: answer.answer, answer }])
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The assistant could not answer. Try again.')
    }
    setBusy(false)
  }

  const allowed = role === 'admin' || role === 'analyst'
  return (
    <section className="page assistant" aria-labelledby="assistant-heading">
      <p>
        <Link to={`/projects/${projectId}`}>← Project</Link> · <Link to={`/projects/${projectId}/findings`}>Findings</Link>
      </p>
      <h1 id="assistant-heading">Ask the data{project ? ` — ${project.election_name}` : ''}</h1>
      <p className="page-intro">
        Ask in plain language about this project's results, findings and claims. The assistant can only look things up in this project's data, and
        every figure links to the page where you can check it. It never decides what happened; findings are reasons to check the result sheet.
      </p>

      {role !== null && !allowed && <p className="empty">The data assistant is available to admins and analysts.</p>}
      {allowed && enabled === false && (
        <p className="notice warn">The data assistant is not set up yet. An administrator needs to add an AI provider key to the API settings.</p>
      )}

      {allowed && enabled && (
        <>
          <div className="chat" aria-live="polite">
            {turns.length === 0 && (
              <div className="examples">
                <p className="small muted">Try one of these:</p>
                {EXAMPLES.map((x) => (
                  <button key={x} type="button" className="chip" onClick={() => void send(undefined, x)}>
                    {x}
                  </button>
                ))}
              </div>
            )}
            {turns.map((t, i) =>
              t.role === 'user' ? (
                <div key={i} className="turn turn-user">
                  <span className="sr-only">You asked: </span>
                  {t.content}
                </div>
              ) : (
                <article key={i} className="turn turn-assistant" aria-label="Answer">
                  <Formatted text={t.content} />
                  {t.answer.citations.length > 0 && (
                    <div className="sources">
                      <span className="small muted">Check it: </span>
                      {t.answer.citations.slice(0, 8).map((c) => (
                        <Link key={c.href} to={c.href} className="small">
                          {c.label}
                        </Link>
                      ))}
                    </div>
                  )}
                  <p className="small muted">
                    {t.answer.grounded ? 'Figures matched to the data looked up.' : t.answer.note === 'no_lookup' ? 'This reply did not look anything up in the data.' : 'Some figures could not be matched — check before using.'}{' '}
                    AI-drafted ({t.answer.model}); not a finding.
                  </p>
                </article>
              ),
            )}
            {busy && <p className="muted">Looking it up…</p>}
            <div ref={end} />
          </div>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <form className="ask" aria-label="Ask a question" onSubmit={(e) => void send(e)}>
            <label className="sr-only" htmlFor="q">
              Your question
            </label>
            <textarea
              id="q"
              rows={2}
              maxLength={2000}
              value={question}
              placeholder="e.g. Which wards in Jos North have turnout far above their peers?"
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) void send(e as unknown as FormEvent)
              }}
            />
            <button type="submit" className="primary" disabled={busy || !question.trim()}>
              Ask
            </button>
          </form>
          <p className="small muted">Questions and answers are saved to the project's log. Do not paste personal data.</p>
        </>
      )}
    </section>
  )
}

/** Plain text with paragraphs and "- " bullets. No HTML is ever interpreted. */
function Formatted({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let list: string[] = []
  const flush = () => {
    if (list.length) blocks.push(<ul key={blocks.length}>{list.map((l, i) => <li key={i}>{l}</li>)}</ul>)
    list = []
  }
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    const m = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/)
    if (m) list.push(m[1].replace(/\*\*/g, ''))
    else {
      flush()
      if (line) blocks.push(<p key={blocks.length}>{line.replace(/\*\*/g, '')}</p>)
    }
  }
  flush()
  return <>{blocks}</>
}
