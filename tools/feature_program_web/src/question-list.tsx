import { useI18n } from './i18n';
import { usePresentation } from './presentation-ui';
import { items, record, records, string } from './model';
import type { JsonRecord, Snapshot } from './model';
import { defaultContext, navigationHash } from './navigation';
import type { WorkContext } from './navigation';
import {
  defaultQuestions,
  questionGoals,
  questionMatches,
  questionStage,
  questionStageLabel,
  questionStages,
} from './question-state';
import { IconButton } from './icons';
import { useSyncExternalStore } from 'react';
import { useQuestionDrafts } from './question-drafts';

function QuestionRow({
  question,
  context,
}: {
  readonly question: JsonRecord;
  readonly context: WorkContext;
}) {
  const i18n = useI18n(),
    id = string(question.id);
  return (
    <a
      className="question-row"
      data-question-id={id}
      aria-current={context.questions?.selected === id ? 'true' : undefined}
      href={navigationHash(
        { view: 'question', questionId: id },
        { ...context, questions: { ...(context.questions || defaultQuestions), selected: id } },
      )}
    >
      <span className="question-row-title">
        <strong>{i18n.display(question.prompt)}</strong>
        <small className="mono">{id}</small>
      </span>
      <span className="badge">{i18n.t(questionStageLabel[questionStage(question)])}</span>
      <span className="question-row-context muted">
        {items(question.affected_task_ids).map(string).join(' · ') ||
          i18n.t('No task link recorded')}
      </span>
    </a>
  );
}
export function QuestionLinks({
  snapshot,
  taskId,
  objectiveId,
  context = defaultContext,
}: {
  readonly snapshot: Snapshot;
  readonly taskId?: string;
  readonly objectiveId?: string;
  readonly context?: WorkContext;
}) {
  const i18n = useI18n();
  const questions = records(snapshot.questions).filter((q) =>
    taskId
      ? items(q.affected_task_ids).includes(taskId)
      : objectiveId
        ? q.objective_id === objectiveId
        : true,
  );
  if (!questions.length) return null;
  const current = questions.filter((q) => q.status !== 'resolved');
  const history = questions.filter((q) => q.status === 'resolved');
  return (
    <section className="question-inbox" aria-label={i18n.t('Related decisions')}>
      <div className="question-inbox-heading">
        <h3>{i18n.t('Related decisions')}</h3>
        <a
          href={navigationHash(
            { view: 'questions' },
            {
              ...context,
              questions: {
                ...defaultQuestions,
                view: 'all',
                task: taskId || '',
                goal: objectiveId || '',
              },
            },
          )}
        >
          {i18n.t('All questions')}
        </a>
      </div>
      {current.slice(0, 3).map((q) => (
        <QuestionRow key={string(q.id)} question={q} context={context} />
      ))}
      {(current.length > 3 || history.length > 0) && (
        <details>
          <summary>
            {i18n.t('More questions and outcomes')} (
            {current.length - Math.min(current.length, 3) + history.length})
          </summary>
          {[...current.slice(3), ...history].map((q) => (
            <QuestionRow key={string(q.id)} question={q} context={context} />
          ))}
        </details>
      )}
    </section>
  );
}
export function QuestionList({
  snapshot,
  context,
  onContext,
  filtersOpen,
  onFiltersOpen,
}: {
  readonly snapshot: Snapshot;
  readonly context: WorkContext;
  readonly onContext: (context: WorkContext) => void;
  readonly filtersOpen: boolean;
  readonly onFiltersOpen: (open: boolean) => void;
}) {
  const i18n = useI18n(),
    q = context.questions || defaultQuestions;
  const presentation = usePresentation();
  const questions = records(snapshot.questions);
  const drafts = useQuestionDrafts();
  useSyncExternalStore(drafts.subscribe, drafts.revision);
  const unavailableDrafts = drafts
    .forProgram(snapshot.program_id)
    .filter(({ questionId, draft }) => {
      const question = questions.find((item) => item.id === questionId);
      const answer = record(question?.answer);
      const confirmed =
        draft.submission &&
        answer.submission_id === draft.submission.submission_id &&
        answer.question_id === draft.submission.question_id &&
        answer.version === draft.submission.version &&
        answer.answer === draft.submission.answer;
      return (
        draft.answer &&
        draft.phase !== 'saved' &&
        !confirmed &&
        (!question || question.status !== 'open')
      );
    });
  const matches = questions.filter((question) => questionMatches(question, q));
  const set = (patch: Partial<typeof q>) =>
    onContext({ ...context, questions: { ...q, ...patch } });
  const currentGoal = string(record(snapshot.objective).id);
  const goals = questionGoals(snapshot);
  const tasks = [
    ...new Set(
      questions.flatMap((item) => items(item.affected_task_ids).map(string)).filter(Boolean),
    ),
  ];
  const filtered = Boolean(q.state || q.search || q.goal || q.task);
  return (
    <section className="questions-page">
      <div className="page-heading">
        <h2>{i18n.t('Questions')}</h2>
        <span className="muted" role="status">
          {i18n.t('{shown} / {total} questions', {
            shown: matches.length,
            total: questions.length,
          })}
        </span>
      </div>
      {unavailableDrafts.length > 0 && (
        <section className="question-inbox" aria-label={i18n.t('Your drafts')}>
          <h3>{i18n.t('Your drafts')}</h3>
          {unavailableDrafts.map(({ questionId, draft }) => (
            <p key={questionId}>
              <a
                data-question-id={questionId}
                href={navigationHash(
                  { view: 'question', questionId },
                  { ...context, questions: { ...q, selected: questionId } },
                )}
              >
                {i18n.t(draft.phase === 'saving' ? 'Saving answer…' : 'Unsent draft')} ·{' '}
                {questionId}
              </a>
            </p>
          ))}
        </section>
      )}
      <div className="question-toolbar">
        <div className="question-views" role="group" aria-label={i18n.t('Question view')}>
          {(['current', 'history', 'all'] as const).map((view) => (
            <button
              key={view}
              aria-pressed={q.view === view}
              onClick={() => set({ view, state: '' })}
            >
              {i18n.t(
                view === 'current'
                  ? 'Current questions'
                  : view === 'history'
                    ? 'Question history'
                    : 'All questions',
              )}
              <span className="muted">
                {' '}
                {
                  questions.filter(
                    (item) =>
                      view === 'all' || (view === 'history') === (item.status === 'resolved'),
                  ).length
                }
              </span>
            </button>
          ))}
        </div>
        <label className="question-search">
          {i18n.t('Search questions')}
          <input
            type="search"
            value={q.search}
            maxLength={300}
            onChange={(event) => set({ search: event.target.value })}
          />
        </label>
        <details
          className="question-filters"
          open={filtersOpen}
          onToggle={(event) => onFiltersOpen(event.currentTarget.open)}
        >
          <summary>
            {i18n.t('Filters')}
            {filtered ? ` · ${i18n.t('Filters applied')}` : ''}
          </summary>
          <label>
            {i18n.t('Question state')}
            <select value={q.state} onChange={(event) => set({ state: event.target.value })}>
              <option value="">{i18n.t('All states')}</option>
              {questionStages.map((stage) => (
                <option key={stage} value={stage}>
                  {i18n.t(questionStageLabel[stage])} (
                  {questions.filter((item) => questionStage(item) === stage).length})
                </option>
              ))}
            </select>
          </label>
          <label>
            {i18n.t('Goal')}
            <select value={q.goal} onChange={(event) => set({ goal: event.target.value })}>
              <option value="">{i18n.t('All goals')}</option>
              {q.goal && !goals.includes(q.goal) && <option value={q.goal}>{q.goal}</option>}
              {goals.map((id) => (
                <option key={id} value={id}>
                  {presentation.goalReference(id)}
                  {id === currentGoal ? ` · ${i18n.t('Current goal')}` : ''}
                </option>
              ))}
            </select>
          </label>
          <label>
            {i18n.t('Affected task')}
            <select value={q.task} onChange={(event) => set({ task: event.target.value })}>
              <option value="">{i18n.t('All tasks')}</option>
              {q.task && !tasks.includes(q.task) && <option value={q.task}>{q.task}</option>}
              {tasks.map((id) => (
                <option key={id} value={id}>
                  {presentation.taskReference(id)}
                </option>
              ))}
            </select>
          </label>
          <IconButton
            icon="clear"
            label={i18n.t('Clear question filters')}
            disabled={!filtered}
            onClick={() => set({ state: '', search: '', goal: '', task: '' })}
          />
        </details>
      </div>
      {filtered && (
        <p className="question-filter-summary muted">
          {[
            q.search,
            q.state
              ? i18n.t(
                  questionStageLabel[
                    questionStages.find((stage) => stage === q.state) || 'unknown'
                  ],
                )
              : '',
            presentation.goal(q.goal),
            presentation.task(q.task),
          ]
            .filter(Boolean)
            .join(' · ')}{' '}
          <button onClick={() => set({ state: '', search: '', goal: '', task: '' })}>
            {i18n.t('Clear filters')}
          </button>
        </p>
      )}
      {!questions.length ? (
        <p className="empty">{i18n.t('No questions have been recorded.')}</p>
      ) : !matches.length ? (
        <p className="empty">
          {i18n.t('No questions match this view. Change the view or clear filters.')}
        </p>
      ) : (
        <div className="question-results">
          {matches.map((item) => (
            <QuestionRow key={string(item.id)} question={item} context={context} />
          ))}
        </div>
      )}
    </section>
  );
}
