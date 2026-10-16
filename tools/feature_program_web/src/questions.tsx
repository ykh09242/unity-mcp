import { usePresentation } from './presentation-ui';
import { useI18n } from './i18n';
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useQuestionDraft, questionDraftKey, useQuestionDrafts } from './question-drafts';
import { questionStage, questionStageLabel } from './question-state';
import { navigationHash, defaultContext } from './navigation';
import type { WorkContext } from './navigation';
import { items, record, string } from './model';
import type { JsonRecord } from './model';
import type { SnapshotWritePhase } from './useSnapshot';
import type { DiagnosticSpan, Diagnostics } from './diagnostics';

export type Session = { readonly responsesEnabled: boolean; readonly nonce: string | null };
export type AnswerInput = {
  readonly question_id: string;
  readonly version: number;
  readonly answer: string;
  readonly submission_id: string;
};
export type AnswerReceipt = AnswerInput & {
  readonly saved_at: string;
  readonly channel: 'local_web';
  readonly provenance: 'unverified_local_human';
  readonly status: 'answered';
};
export type AnswerResult = {
  readonly ok: boolean;
  readonly conflict: boolean;
  readonly receipt?: AnswerReceipt;
};
export type AnswerSaver = (
  input: AnswerInput,
  nonce: string,
  received?: (receipt: AnswerReceipt) => void,
  diagnostic?: DiagnosticSpan,
) => Promise<AnswerResult>;
function validSavedAt(value: unknown): value is string {
  if (typeof value !== 'string' || value.length > 64) return false;
  const parts =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/u.exec(value);
  if (!parts) return false;
  const year = Number(parts[1]),
    month = Number(parts[2]),
    day = Number(parts[3]);
  return (
    year >= 1 &&
    month >= 1 &&
    month <= 12 &&
    day >= 1 &&
    day <= new Date(Date.UTC(year, month, 0)).getUTCDate() &&
    Number(parts[4]) < 24 &&
    Number(parts[5]) < 60 &&
    Number(parts[6]) < 60 &&
    !Number.isNaN(new Date(value).valueOf())
  );
}
export function parseAnswerReceipt(value: unknown, input: AnswerInput): AnswerReceipt | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const receipt = value as Record<string, unknown>;
  const fields = [
    'question_id',
    'version',
    'answer',
    'submission_id',
    'saved_at',
    'channel',
    'provenance',
    'status',
  ];
  if (
    Object.keys(receipt).length !== fields.length ||
    !fields.every((key) => Object.hasOwn(receipt, key)) ||
    receipt.question_id !== input.question_id ||
    receipt.version !== input.version ||
    receipt.answer !== input.answer ||
    receipt.submission_id !== input.submission_id ||
    !Number.isSafeInteger(receipt.version) ||
    Number(receipt.version) < 1 ||
    !input.question_id.trim() ||
    input.question_id.length > 128 ||
    !input.submission_id.trim() ||
    input.submission_id.length > 128 ||
    !input.answer.trim() ||
    input.answer.length > 4000 ||
    !validSavedAt(receipt.saved_at) ||
    receipt.channel !== 'local_web' ||
    receipt.provenance !== 'unverified_local_human' ||
    receipt.status !== 'answered'
  )
    return null;
  return receipt as AnswerReceipt;
}
export type SessionLoader = (signal?: AbortSignal) => Promise<Session>;
export const loadSession: SessionLoader = async (signal) => {
  const response = await fetch('/api/session', {
    cache: 'no-store',
    credentials: 'same-origin',
    signal,
  });
  if (!response.ok) throw new Error('Session unavailable');
  const input: unknown = await response.json();
  if (!input || typeof input !== 'object' || !('responses_enabled' in input) || !('nonce' in input))
    throw new Error('Invalid session');
  return {
    responsesEnabled: input.responses_enabled === true,
    nonce: typeof input.nonce === 'string' ? input.nonce : null,
  };
};
export const saveAnswer: AnswerSaver = async (input, nonce, _received, diagnostic) => {
  diagnostic?.mark('post_started');
  const response = await fetch('/api/questions/answer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Work-Program-Nonce': nonce },
    credentials: 'same-origin',
    body: JSON.stringify(input),
    signal: AbortSignal.timeout(12000),
  });
  diagnostic?.mark('post_response', {
    httpStatus: response.status,
    serverTraceId: response.headers.get('X-Work-Program-Trace'),
  });
  if (response.status !== 200) return { ok: false, conflict: response.status === 409 };
  // Normal 200 follows the locked durable save, final model closure and synchronous render.
  // Validate its actual receipt; never accept a transport acknowledgement as saved evidence.
  const body = await response.text();
  if (body.length > 64 * 1024) return { ok: false, conflict: false };
  const receipt = parseAnswerReceipt(JSON.parse(body), input);
  if (receipt) diagnostic?.mark('receipt_validated', { httpStatus: 200 });
  return receipt ? { ok: true, conflict: false, receipt } : { ok: false, conflict: false };
};
export function useSession(loader: SessionLoader = loadSession) {
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => {
    let active = true;
    let pending: AbortController | null = null;
    const read = async () => {
      if (pending) return;
      const controller = new AbortController();
      pending = controller;
      const timeout = setTimeout(() => controller.abort(), 12000);
      let aborted: (() => void) | undefined;
      const cancellation = new Promise<never>((_, reject) => {
        aborted = () => reject(new Error('Session read aborted'));
        controller.signal.addEventListener('abort', aborted, { once: true });
      });
      try {
        const value = await Promise.race([loader(controller.signal), cancellation]);
        if (active && !controller.signal.aborted && pending === controller)
          setSession((previous) =>
            previous?.nonce === value.nonce && previous.responsesEnabled === value.responsesEnabled
              ? previous
              : value,
          );
      } catch {
        // no-excuse-ok: catch -- this hook is the session read boundary.
        if (active && pending === controller)
          setSession((previous) => previous || { responsesEnabled: false, nonce: null });
      } finally {
        clearTimeout(timeout);
        if (aborted) controller.signal.removeEventListener('abort', aborted);
        if (pending === controller) pending = null;
      }
    };
    void read();
    const interval = setInterval(() => {
      if (!document.hidden) void read();
    }, 5000);
    return () => {
      active = false;
      pending?.abort();
      pending = null;
      clearInterval(interval);
    };
  }, [loader]);
  return session;
}
export function QuestionForm({
  question,
  session,
  saver = saveAnswer,
  coordinatedSaver,
  writePhase,
  holdAutomaticReads,
  diagnostics,
  refresh,
  programId = 'standalone-question',
  context = defaultContext,
}: {
  readonly programId?: string;
  readonly context?: WorkContext;
  readonly question: JsonRecord;
  readonly session: Session | null;
  readonly saver?: AnswerSaver;
  readonly coordinatedSaver?: AnswerSaver;
  readonly writePhase?: SnapshotWritePhase | null;
  readonly holdAutomaticReads?: () => () => void;
  readonly diagnostics?: Diagnostics;
  readonly refresh: (manual?: boolean, afterWrite?: boolean) => Promise<void>;
}) {
  const i18n = useI18n();
  const presentation = usePresentation();
  const formRoot = useRef<HTMLElement>(null);
  const savedHeading = useRef<HTMLHeadingElement>(null);
  const failureMessage = useRef<HTMLParagraphElement>(null);
  const submitFocus = useRef<HTMLElement | null>(null);
  const answerTarget = useRef<HTMLLabelElement>(null);
  const options = items(question.options);
  const longChoices = options.length > 4 || options.map(string).join('').length > 600;
  const key = questionDraftKey(programId, string(question.id));
  const { draft, store } = useQuestionDraft(key, Number(question.version));
  const { answer, version } = draft;
  const busy = draft.phase === 'saving';
  const saveBlocked = busy || Boolean(writePhase);
  const setAnswer = (value: string) => store.update(key, { answer: value, phase: 'editing' });
  const status = string(question.status);
  const recordedAnswer = record(question.answer);
  const confirmed = Boolean(
    draft.submission &&
    Number(question.version) === draft.submission.version &&
    recordedAnswer.submission_id === draft.submission.submission_id &&
    recordedAnswer.question_id === draft.submission.question_id &&
    recordedAnswer.version === draft.submission.version &&
    recordedAnswer.answer === draft.submission.answer,
  );
  const localReceipt =
    draft.receipt &&
    draft.submission &&
    Number(question.version) === draft.receipt.version &&
    parseAnswerReceipt(draft.receipt, draft.submission) &&
    draft.answer === draft.receipt.answer &&
    draft.version === draft.receipt.version
      ? draft.receipt
      : null;
  const saved =
    confirmed ||
    Boolean(localReceipt) ||
    (!coordinatedSaver && draft.phase === 'saved' && draft.version === Number(question.version));
  useLayoutEffect(() => {
    if (localReceipt && formRoot.current?.isConnected)
      draft.diagnostic?.mark('receipt_dom_committed');
  }, [localReceipt, draft.diagnostic]);
  const shownAnswer = localReceipt || recordedAnswer;
  const failure =
    !saved && (draft.phase === 'conflict' || draft.phase === 'unavailable') ? draft.phase : null;
  const acknowledgement = record(question.acknowledgement);
  const outcome = record(question.outcome);
  const withdrawn = outcome.kind === 'withdrawn';
  const waiting = status === 'answered' || (saved && status === 'open');
  const validVersion =
    Number.isSafeInteger(Number(question.version)) && Number(question.version) > 0;
  const changed = validVersion && version !== Number(question.version);
  const canAnswer =
    session?.responsesEnabled && session.nonce && status === 'open' && !saved && validVersion;
  const editing = Boolean(canAnswer && !changed && draft.phase === 'editing' && answer.trim());
  useEffect(() => {
    if (editing) return holdAutomaticReads?.();
  }, [editing, holdAutomaticReads, key]);
  useEffect(() => {
    // A result belongs to this explicit interaction, never to a later focus or route.
    const moved = (event: Event) => {
      if (!submitFocus.current || event.target === submitFocus.current) return;
      if (
        event.type === 'focusin' &&
        (event.target === document.body || event.target === document.documentElement)
      )
        return;
      submitFocus.current = null;
    };
    document.addEventListener('focusin', moved);
    document.addEventListener('pointerdown', moved, true);
    return () => {
      submitFocus.current = null;
      document.removeEventListener('focusin', moved);
      document.removeEventListener('pointerdown', moved, true);
    };
  }, [key]);
  useEffect(() => {
    const origin = submitFocus.current;
    if (!origin || (!saved && !failure)) return;
    submitFocus.current = null;
    if (document.activeElement !== document.body && document.activeElement !== origin) return;
    if (saved) savedHeading.current?.focus();
    else if (origin.isConnected && !origin.matches(':disabled'))
      origin.focus({ preventScroll: true });
    else failureMessage.current?.focus();
  }, [saved, failure, draft]);
  const save = async () => {
    if (!session?.responsesEnabled || !session.nonce || saveBlocked) return;
    const active = document.activeElement;
    submitFocus.current =
      active instanceof HTMLElement && formRoot.current?.contains(active) ? active : null;
    if (
      await store.submit(key, {
        questionId: string(question.id),
        nonce: session.nonce,
        saver: coordinatedSaver || saver,
        diagnostic: diagnostics?.start(),
      })
    ) {
      if (!coordinatedSaver) await refresh(false, true);
    }
  };
  const submit = async () => {
    if (!canAnswer || changed) return;
    await save();
  };
  return (
    <section ref={formRoot} className="question-form" aria-label={i18n.t('Director question')}>
      <div className="question-heading">
        <h2 tabIndex={-1}>{i18n.display(question.prompt)}</h2>
        <span className="badge">
          {i18n.t(waiting ? 'Answer saved' : questionStageLabel[questionStage(question)])}
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
          <dd>{i18n.display(presentation.actor(question.actor_id))}</dd>
        </div>
        <div>
          <dt>{i18n.t('Recorded')}</dt>
          <dd>{i18n.date(question.issued_at)}</dd>
        </div>
        <div>
          <dt>{i18n.t('Affected tasks')}</dt>
          <dd>
            {items(question.affected_task_ids).map((item) => (
              <a
                key={string(item)}
                href={navigationHash(
                  { view: 'task', taskId: string(item) },
                  { ...context, selected: string(item) },
                )}
              >
                {presentation.taskReference(item)}{' '}
              </a>
            ))}
            {!items(question.affected_task_ids).length && i18n.t('None recorded')}
          </dd>
        </div>
        <div>
          <dt>{i18n.t('Goal')}</dt>
          <dd>
            {string(question.objective_id) ? (
              <a
                href={navigationHash(
                  { view: 'questions' },
                  {
                    ...context,
                    questions: {
                      view: 'all',
                      search: '',
                      state: '',
                      task: '',
                      selected: string(question.id),
                      goal: string(question.objective_id),
                    },
                  },
                )}
              >
                {presentation.goalReference(question.objective_id)}
              </a>
            ) : (
              i18n.t('Not recorded')
            )}
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
          {!session?.responsesEnabled && (
            <p className="muted">
              {i18n.t(
                session === null
                  ? 'Checking answer availability…'
                  : 'Responses are disabled for this server. Task records remain readable.',
              )}
            </p>
          )}
          {longChoices && (
            <button
              type="button"
              className="answer-jump"
              onClick={() => {
                const target = answerTarget.current;
                target?.scrollIntoView?.({ block: 'start' });
                const input = target?.querySelector('textarea');
                if (input && !input.disabled) input.focus({ preventScroll: true });
                else target?.focus({ preventScroll: true });
              }}
            >
              {i18n.t('Write answer')}
            </button>
          )}
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
          <label ref={answerTarget} tabIndex={-1}>
            {i18n.t('Your answer')}
            <textarea
              data-question-answer={string(question.id)}
              value={answer}
              onSelect={(event) =>
                store.update(key, {
                  selection: {
                    start: event.currentTarget.selectionStart,
                    end: event.currentTarget.selectionEnd,
                  },
                })
              }
              maxLength={4000}
              rows={5}
              onChange={(event) => setAnswer(event.target.value)}
              placeholder={i18n.t("Respond to the director's question…")}
              disabled={busy || !session?.responsesEnabled}
            />
          </label>
          <p className="muted">{i18n.t('{count} / 4000 characters', { count: answer.length })}</p>
          {!validVersion && (
            <p role="alert">
              {i18n.t('The question version is unavailable. Your draft cannot be saved yet.')}
            </p>
          )}
          {changed && (
            <div className="question-error">
              <p>
                {i18n.t(
                  'The director revised this question. Your draft is preserved. Review the current prompt before continuing.',
                )}
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  store.update(key, {
                    version: Number(question.version),
                    submission: null,
                    phase: 'editing',
                  });
                }}
              >
                {i18n.t('Use current question version')}
              </button>
            </div>
          )}
          <button type="submit" disabled={!canAnswer || saveBlocked || !answer.trim() || changed}>
            {busy && writePhase === 'waiting-read'
              ? i18n.t('Waiting for the current refresh before saving…')
              : busy
                ? i18n.t('Saving answer…')
                : i18n.t('Save answer')}
          </button>
        </form>
      ) : (
        <div className="response-record">
          {(Object.keys(recordedAnswer).length > 0 ||
            saved ||
            status === 'answered' ||
            status === 'acknowledged') && (
            <>
              <h3 ref={savedHeading} tabIndex={-1}>
                {i18n.t('Your saved answer')}
              </h3>
              <p className="prose">
                {i18n.display(shownAnswer.answer, saved ? answer : i18n.t('Not recorded'))}
              </p>
              <p className="muted">
                {Object.keys(shownAnswer).length > 0 && (
                  <>
                    {i18n.t('Saved')} {i18n.date(shownAnswer.saved_at)}{' '}
                  </>
                )}
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
              <h3>
                {i18n.t(
                  withdrawn
                    ? 'Question withdrawn'
                    : outcome.kind === 'applied'
                      ? 'Applied outcome'
                      : 'Recorded outcome',
                )}
              </h3>
              <p className="prose">{i18n.display(outcome.summary)}</p>
            </>
          )}
        </div>
      )}
      {status !== 'open' && items(question.options).length > 0 && (
        <details className="recorded-options">
          <summary>
            {i18n.t('Suggested choices')} ({items(question.options).length})
          </summary>
          <ol>
            {items(question.options).map((option, index) => (
              <li key={index}>{string(option)}</li>
            ))}
          </ol>
        </details>
      )}
      {status !== 'open' &&
        !saved &&
        failure === 'unavailable' &&
        draft.submission &&
        draft.answer === draft.submission.answer &&
        draft.version === draft.submission.version && (
          <button
            type="button"
            disabled={busy || !session?.responsesEnabled || !session.nonce}
            onClick={() => void save()}
          >
            {i18n.t('Check save confirmation')}
          </button>
        )}
      {failure && (
        <p ref={failureMessage} tabIndex={-1} className="question-error" role="alert">
          {failure === 'conflict'
            ? i18n.t(
                'The question changed or closed before this answer was saved. Refresh, then review its current state. Your draft is preserved.',
              )
            : i18n.t(
                'The answer could not be confirmed as saved. Your draft is preserved. Retry uses the same submission ID.',
              )}
        </p>
      )}
      {status !== 'open' && answer && !saved && (
        <details open className="unsent-answer">
          <summary>{i18n.t('Unsent draft')}</summary>
          <label>
            {i18n.t('Your draft')}
            <textarea readOnly value={answer} rows={4} />
          </label>
        </details>
      )}
      {(Object.keys(acknowledgement).length > 0 || Object.keys(outcome).length > 0) && (
        <details className="decision-context">
          <summary>{i18n.t('Decision scope and actors')}</summary>
          {[
            ['Director acknowledgement', acknowledgement],
            ['Recorded outcome', outcome],
          ].map(([label, value]) => {
            const entry = record(value);
            if (!Object.keys(entry).length) return null;
            return (
              <div key={string(label)}>
                <h3>{i18n.t(string(label))}</h3>
                <dl className="history-facts">
                  <div>
                    <dt>{i18n.t('Recorded by')}</dt>
                    <dd>{i18n.display(presentation.actor(entry.actor_id))}</dd>
                  </div>
                  <div>
                    <dt>{i18n.t('Recorded')}</dt>
                    <dd>{i18n.date(entry.recorded_at)}</dd>
                  </div>
                  <div>
                    <dt>{i18n.t('Scope')}</dt>
                    <dd>{items(entry.scope).map(string).join(', ') || i18n.t('Not recorded')}</dd>
                  </div>
                </dl>
              </div>
            );
          })}
        </details>
      )}
      <details className="response-help">
        <summary>{i18n.t('About answers and drafts')}</summary>
        <p>
          {i18n.t(
            'Drafts stay in this tab while you navigate or refresh data. Reloading or closing the page clears unsaved drafts.',
          )}
        </p>
        <p>
          {i18n.t(
            'Saving an answer does not acknowledge or apply a decision. The director checks responses at work points; a stopped agent is not automatically woken.',
          )}
        </p>
      </details>
    </section>
  );
}

export function MissingQuestionDraft({
  programId,
  questionId,
}: {
  readonly programId: string;
  readonly questionId: string;
}) {
  const i18n = useI18n(),
    store = useQuestionDrafts();
  const key = questionDraftKey(programId, questionId);
  const draft = useSyncExternalStore(store.subscribe, () => store.peek(key));
  if (!draft?.answer) return null;
  return (
    <section className="question-form unsent-answer" aria-label={i18n.t('Unsent draft')}>
      <h3>{i18n.t('Unsent draft')}</h3>
      <p>
        {i18n.t(
          'This question is not in the current snapshot. Your draft is still available in this tab.',
        )}
      </p>
      <label>
        {i18n.t('Your draft')}
        <textarea readOnly value={draft.answer} rows={5} />
      </label>
    </section>
  );
}
