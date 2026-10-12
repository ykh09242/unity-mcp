import { useI18n } from './i18n';
import { useEffect, useRef, useState } from 'react';
import { items, record, string } from './model';
import type { JsonRecord } from './model';

export type Session = { readonly responsesEnabled: boolean; readonly nonce: string | null };
export type AnswerInput = {
  readonly question_id: string;
  readonly version: number;
  readonly answer: string;
  readonly submission_id: string;
};
export type AnswerResult = { readonly ok: boolean; readonly conflict: boolean };
export type AnswerSaver = (input: AnswerInput, nonce: string) => Promise<AnswerResult>;
export type SessionLoader = () => Promise<Session>;
export const loadSession: SessionLoader = async () => {
  const response = await fetch('/api/session', { cache: 'no-store', credentials: 'same-origin' });
  if (!response.ok) throw new Error('Session unavailable');
  const input: unknown = await response.json();
  if (!input || typeof input !== 'object' || !('responses_enabled' in input) || !('nonce' in input))
    throw new Error('Invalid session');
  return {
    responsesEnabled: input.responses_enabled === true,
    nonce: typeof input.nonce === 'string' ? input.nonce : null,
  };
};
export const saveAnswer: AnswerSaver = async (input, nonce) => {
  const response = await fetch('/api/questions/answer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Work-Program-Nonce': nonce },
    credentials: 'same-origin',
    body: JSON.stringify(input),
    signal: AbortSignal.timeout(12000),
  });
  return { ok: response.ok, conflict: response.status === 409 };
};
export function useSession(loader: SessionLoader = loadSession) {
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => {
    let active = true;
    const read = () =>
      loader()
        .then((value) => {
          if (active)
            setSession((previous) =>
              previous?.nonce === value.nonce &&
              previous.responsesEnabled === value.responsesEnabled
                ? previous
                : value,
            );
        })
        .catch(() => {
          if (active)
            setSession((previous) => previous || { responsesEnabled: false, nonce: null });
        });
    void read();
    const interval = setInterval(() => {
      if (!document.hidden) void read();
    }, 5000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [loader]);
  return session;
}
export function QuestionForm({
  question,
  session,
  saver = saveAnswer,
  refresh,
}: {
  readonly question: JsonRecord;
  readonly session: Session | null;
  readonly saver?: AnswerSaver;
  readonly refresh: () => Promise<void>;
}) {
  const i18n = useI18n();
  const [answer, setAnswer] = useState('');
  const [version, setVersion] = useState(Number(question.version));
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [failure, setFailure] = useState<'conflict' | 'unavailable' | null>(null);
  const submission = useRef<{
    readonly id: string;
    readonly answer: string;
    readonly version: number;
  } | null>(null);
  const pending = useRef(false);
  const status = string(question.status);
  const recordedAnswer = record(question.answer);
  const acknowledgement = record(question.acknowledgement);
  const outcome = record(question.outcome);
  const withdrawn = outcome.kind === 'withdrawn';
  const waiting = status === 'answered' || (saved && status === 'open');
  const changed = version !== Number(question.version);
  const canAnswer = session?.responsesEnabled && session.nonce && status === 'open' && !saved;
  const submit = async () => {
    if (
      !canAnswer ||
      !session?.nonce ||
      pending.current ||
      !answer.trim() ||
      answer.length > 4000 ||
      changed
    )
      return;
    pending.current = true;
    setBusy(true);
    setFailure(null);
    if (
      !submission.current ||
      submission.current.answer !== answer ||
      submission.current.version !== version
    )
      submission.current = { id: crypto.randomUUID(), answer, version };
    try {
      const result = await saver(
        { question_id: string(question.id), version, answer, submission_id: submission.current.id },
        session.nonce,
      );
      if (result.ok) {
        setSaved(true);
        await refresh();
      } else setFailure(result.conflict ? 'conflict' : 'unavailable');
    } catch {
      setFailure('unavailable');
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  return (
    <section className="question-form" aria-label={i18n.t('Director question')}>
      <div className="question-heading">
        <h2>{i18n.display(question.prompt)}</h2>
        <span className="badge">
          {withdrawn
            ? i18n.t('Withdrawn')
            : waiting
              ? i18n.t('Answer saved')
              : status === 'open'
                ? i18n.t('Needs your answer')
                : status === 'acknowledged'
                  ? i18n.t('Director acknowledged')
                  : i18n.t('Applied')}
        </span>
      </div>
      <p className="question-why">{i18n.display(question.why, 'No explanation recorded')}</p>
      <dl className="fields">
        <div>
          <dt>{i18n.t('Decision requested')}</dt>
          <dd>{i18n.display(question.requested_decision)}</dd>
        </div>
        <div>
          <dt>{i18n.t('Asked by')}</dt>
          <dd>{i18n.display(question.actor_id)}</dd>
        </div>
        <div>
          <dt>{i18n.t('Recorded')}</dt>
          <dd>{i18n.date(question.issued_at)}</dd>
        </div>
        <div>
          <dt>{i18n.t('Affected tasks')}</dt>
          <dd>
            {items(question.affected_task_ids)
              .map((item) => i18n.display(item))
              .join(', ') || i18n.t('None recorded')}
          </dd>
        </div>
      </dl>
      {status === 'open' && !saved ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          {items(question.options).length > 0 && (
            <fieldset className="question-options">
              <legend>{i18n.t('Suggested choices')}</legend>
              <div>
                {items(question.options).map((option, index) => (
                  <button
                    key={index}
                    type="button"
                    disabled={busy || !session?.responsesEnabled}
                    onClick={() => setAnswer(i18n.display(option))}
                  >
                    {i18n.display(option)}
                  </button>
                ))}
              </div>
            </fieldset>
          )}
          <label>
            {i18n.t('Your answer')}
            <textarea
              value={answer}
              maxLength={4000}
              rows={5}
              onChange={(event) => setAnswer(event.target.value)}
              placeholder={i18n.t("Respond to the director's question…")}
              disabled={busy || !session?.responsesEnabled}
            />
          </label>
          <p className="muted">
            {answer.length}
            {i18n.t('/4000 characters. Responses are stored in the local program.')}
          </p>
          {changed && (
            <div className="question-error">
              <p>
                {i18n.t(
                  'The director revised this question. Your draft is preserved. Review the current prompt before continuing.',
                )}
              </p>
              <button
                type="button"
                onClick={() => {
                  setVersion(Number(question.version));
                  submission.current = null;
                  setFailure(null);
                }}
              >
                {i18n.t('Use current question version')}
              </button>
            </div>
          )}
          {!session?.responsesEnabled && (
            <p className="muted">
              {i18n.t('Responses are disabled for this server. Task records remain readable.')}
            </p>
          )}
          <button type="submit" disabled={!canAnswer || busy || !answer.trim() || changed}>
            {busy ? i18n.t('Saving answer…') : i18n.t('Save answer')}
          </button>
        </form>
      ) : (
        <div className="response-record">
          {!withdrawn && (
            <>
              <h3>{i18n.t('Your saved answer')}</h3>
              <p className="prose">
                {i18n.display(recordedAnswer.answer, saved ? answer : i18n.t('Not recorded'))}
              </p>
              <p className="muted">
                {i18n.t('Saved')} {i18n.date(recordedAnswer.saved_at)}{' '}
                {i18n.t('· local response, not authenticated identity')}
              </p>
            </>
          )}
          {waiting && (
            <p role="status">{i18n.t('Answer saved. Waiting for director acknowledgement.')}</p>
          )}
          {(status === 'acknowledged' || (status === 'resolved' && question.acknowledgement)) && (
            <>
              <h3>{i18n.t('Director acknowledgement')}</h3>
              <p className="prose">{i18n.display(acknowledgement.summary)}</p>
            </>
          )}
          {status === 'resolved' && (
            <>
              <h3>{withdrawn ? i18n.t('Question withdrawn') : i18n.t('Applied outcome')}</h3>
              <p className="prose">{i18n.display(outcome.summary)}</p>
            </>
          )}
        </div>
      )}
      {failure && (
        <p className="question-error" role="alert">
          {failure === 'conflict'
            ? i18n.t(
                'The question changed or closed before this answer was saved. Refresh, then review its current state. Your draft is preserved.',
              )
            : i18n.t(
                'The answer could not be confirmed as saved. Your draft is preserved. Retry uses the same submission ID.',
              )}
        </p>
      )}
      <p className="response-help">
        {i18n.t(
          'Saving an answer does not acknowledge or apply a decision. The director checks responses at work points; a stopped agent is not automatically woken.',
        )}
      </p>
      <details>
        <summary>{i18n.t('Question scope and response history')}</summary>
        <pre className="source">{i18n.display(question)}</pre>
      </details>
    </section>
  );
}
