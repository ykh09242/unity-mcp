import { TaskDescription, taskDescriptionParts } from './task-description';
import { CriterionCoverage, ProgramAttention } from './overview-context';
import { LanguageProvider, language, useI18n } from './i18n';
import { presentSnapshot, taskBody } from './presentation';
import { PresentationProvider, usePresentation } from './presentation-ui';
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  categoryTasks,
  items,
  ownerOf,
  record,
  records,
  semanticValueEqual,
  string,
} from './model';
import type { Json, JsonRecord, Snapshot } from './model';
import { snapshotIndex } from './selectors';
import { IconButton, IconLink } from './icons';
import { DependencyNeighborhood, Workload } from './relate-ui';
import { hasActionGlance, taskNeighborhood, workloadHref } from './relate';
import { CoordinationNotes, GoalCoordination, ReadinessContext } from './readiness-ui';
import {
  ContentValue,
  DeliveryHistory,
  ReviewSummary,
  StaffingHistory,
  TaskHistory,
  TransferHistory,
} from './history-ui';
import { useSnapshot } from './useSnapshot';
import type { SnapshotLoader } from './useSnapshot';
import { QuestionForm, MissingQuestionDraft, useSession } from './questions';
import { QuestionDraftProvider, questionDraftKey, useQuestionDrafts } from './question-drafts';
import { QuestionList, QuestionLinks } from './question-list';
import { questionListIdentity } from './question-state';
import type { AnswerSaver, SessionLoader } from './questions';
import { pageSize, pageSizes, taskPage, taskPageIndex } from './paging';
import type { WorkFocusRequest, WorkPage } from './paging';
import {
  categoryBreadcrumbs,
  defaultContext,
  defaultPreferences,
  emptyFilters,
  goalTaskIds,
  navigationHash,
  parseNavigation,
  readPreferences,
  scopedTasks,
  selectWorkTasks,
  writePreferences,
} from './navigation';
import type { Navigation, Route, TaskSection, ViewPreferences, WorkContext } from './navigation';
import type { TaskFilters } from './selectors';
import { Breadcrumbs, NavigationSearch, ViewControls } from './navigation-ui';

type Filters = TaskFilters;
const statuses = ['planned', 'active', 'blocked', 'review', 'done'] as const;
export function App(props: {
  readonly loader?: SnapshotLoader;
  readonly sessionLoader?: SessionLoader;
  readonly answerSaver?: AnswerSaver;
}) {
  return (
    <LanguageProvider>
      <QuestionDraftProvider>
        <Dashboard {...props} />
      </QuestionDraftProvider>
    </LanguageProvider>
  );
}
function Section({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  const i18n = useI18n();
  return (
    <section className="content-section">
      <h3>{i18n.t(title)}</h3>
      {children}
    </section>
  );
}
function Fields({ values }: { readonly values: readonly (readonly [string, Json | undefined])[] }) {
  const i18n = useI18n();
  const presentation = usePresentation();
  return (
    <dl className="fields">
      {values.map(([key, value]) => (
        <div key={key}>
          <dt>{i18n.t(key)}</dt>
          <dd>
            <ContentValue value={presentation.field(key, value)} />
          </dd>
        </div>
      ))}
    </dl>
  );
}
function Prose({
  value,
  fallback = 'Not recorded',
}: {
  readonly value?: Json;
  readonly fallback?: string;
}) {
  const i18n = useI18n();
  if (Array.isArray(value))
    return value.length ? (
      <ul className="prose-list">
        {value.map((item, index) => (
          <li key={index}>
            <ContentValue value={item} />
          </li>
        ))}
      </ul>
    ) : (
      <p className="muted">{i18n.t(fallback)}</p>
    );
  return value !== null && typeof value === 'object' ? (
    <div className="prose">
      <ContentValue value={value} />
    </div>
  ) : (
    <p className="prose">{i18n.display(value, fallback)}</p>
  );
}
function Status({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  const name = string(value);
  return (
    <span className={`badge ${statuses.some((status) => status === name) ? name : ''}`}>
      {i18n.statusLabel(value)}
    </span>
  );
}
function CategoryTreeView({
  snapshot,
  selected,
  onSelect,
  context = defaultContext,
}: {
  readonly snapshot: Snapshot;
  readonly selected: string;
  readonly onSelect: (id: string) => void;
  readonly context?: WorkContext;
}) {
  const i18n = useI18n();
  const index = snapshotIndex(snapshot);
  const scopeIds = new Set(scopedTasks(snapshot, context.scope).map((task) => task.id));
  const selectedPath = new Set(categoryBreadcrumbs(snapshot, selected).map((item) => item.id));
  const matching = new Set(
    selectWorkTasks(snapshot, { ...context, filters: { ...context.filters, category: '' } }).map(
      (task) => task.id,
    ),
  );
  const filtered = Object.entries(context.filters).some(
    ([key, value]) => key !== 'category' && key !== 'sort' && Boolean(value),
  );
  const walk = (parent: Json | undefined, visited: ReadonlySet<string>): ReactNode =>
    (index.categoryChildren.get(string(parent)) || []).map((category) => {
      const id = string(category.id);
      if (visited.has(id)) return null;
      const path = new Set([...visited, id]);
      const taskIds = new Set(
        [...categoryTasks(snapshot, id)].filter((taskId) => scopeIds.has(taskId)),
      );
      const matches = [...taskIds].filter((taskId) => matching.has(taskId)).length;
      if (filtered && !matches && !selectedPath.has(id)) return null;
      const complete = [...taskIds].filter(
        (taskId) => index.tasks.get(taskId)?.status === 'done',
      ).length;
      return (
        <div key={id} className="category-node">
          <button
            type="button"
            className="category-button"
            aria-label={i18n.t('{title}, {done} of {total} reviewed', {
              title: i18n.display(category.title),
              done: complete,
              total: taskIds.size,
            })}
            aria-pressed={selected === id}
            onClick={() => onSelect(id)}
          >
            <span>{i18n.display(category.title)}</span>
            <span className="category-count">
              {complete}/{taskIds.size}
              {filtered && <small>{i18n.t('{count} matching', { count: matches })}</small>}
            </span>
          </button>
          <div className="category-children">{walk(id, path)}</div>
        </div>
      );
    });
  return (
    <nav className="category-tree" aria-label={i18n.t('Categories')}>
      <p className="eyebrow">{i18n.t('Categories · reviewed / total')}</p>
      <button
        type="button"
        className="category-button"
        aria-pressed={!selected}
        onClick={() => onSelect('')}
      >
        <span>{i18n.t('All work')}</span>
        <span className="category-count">
          {scopedTasks(snapshot, context.scope).filter((task) => task.status === 'done').length}/
          {scopeIds.size}
        </span>
      </button>
      {walk(null, new Set())}
      {!index.categories.size && <p className="muted">{i18n.t('No categories recorded.')}</p>}
      {filtered && !matching.size && (
        <p className="muted">{i18n.t('No category members match these filters.')}</p>
      )}
    </nav>
  );
}
const CategoryTree = memo(CategoryTreeView);

function Progress({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n();
  const currentGoal = goalTaskIds(snapshot);
  const tasks = scopedTasks(snapshot, currentGoal ? 'current' : 'all');
  const done = tasks.filter((task) => task.status === 'done').length;
  const delivery = tasks.filter(
    (task) =>
      string(record(task.delivery).status) !== 'unrecorded' && string(record(task.delivery).status),
  ).length;
  return (
    <>
      <p className="progress-scope muted">
        {i18n.t(currentGoal ? 'Current goal' : 'All recorded tasks')} ·{' '}
        <a href="#work?scope=all">
          {i18n.t('All work')} ({i18n.number(snapshot.tasks.length)})
        </a>
      </p>
      <div className="metrics">
        <div className="metric">
          <p className="muted">{i18n.t('Reviewed implementation')}</p>
          <span className="metric-number">
            {done} <span className="metric-denominator">/ {tasks.length}</span>
          </span>
          <progress
            max={Math.max(tasks.length, 1)}
            value={done}
            aria-label={i18n.t('{done} of {total} tasks reviewed complete', {
              done,
              total: tasks.length,
            })}
          />
          <p className="muted">{i18n.t('Tasks accepted after recorded review.')}</p>
        </div>
        <div className="metric">
          <p className="muted">{i18n.t('Recorded PR / CI')}</p>
          <span className="metric-number">
            {delivery} <span className="metric-denominator">{i18n.t('tasks')}</span>
          </span>
          <p className="muted">
            {i18n.t('Saved delivery records. Open the provider to verify current results.')}
          </p>
        </div>
      </div>
    </>
  );
}
function Objective({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n();
  const objective = record(snapshot.objective);
  const checkpoint = record(snapshot.checkpoint);
  const awaiting = objective.status === 'awaiting_next_goal';
  return (
    <section className="objective">
      <p className="eyebrow">{i18n.t('Current objective')}</p>
      <h3 className="objective-title">
        {i18n.display(objective.title || objective.goal, 'No objective recorded')}
      </h3>
      {awaiting && (
        <p className="awaiting">
          {i18n.t(
            'Awaiting your next objective. The completed goal does not authorize another one.',
          )}
        </p>
      )}
      <Fields
        values={[
          ['Lifecycle', i18n.human(objective.status)],
          [
            'Decision owner',
            ['director', 'user'].includes(string(objective.decision_owner))
              ? i18n.human(objective.decision_owner)
              : objective.decision_owner,
          ],
          [
            'Next action owner',
            ['director', 'user'].includes(string(objective.next_action_owner))
              ? i18n.human(objective.next_action_owner)
              : objective.next_action_owner,
          ],
        ]}
      />
      <details>
        <summary>{i18n.t('Success criteria')}</summary>
        <CriterionCoverage snapshot={snapshot} />
      </details>
      <p className="next-action">
        <span className="muted">{i18n.t('Next action')}</span>
        <br />
        {i18n.display(
          objective.next_action || checkpoint.next_action,
          awaiting
            ? i18n.t('User supplies the next objective.')
            : i18n.t('Director records the next action.'),
        )}
      </p>
    </section>
  );
}
function Overview({
  snapshot,
  context,
}: {
  readonly snapshot: Snapshot;
  readonly context: WorkContext;
}) {
  const i18n = useI18n();
  const [decisionsOpen, setDecisionsOpen] = useState(false);
  const presentation = usePresentation();
  const index = snapshotIndex(snapshot);
  const tasksInScope = scopedTasks(snapshot, goalTaskIds(snapshot) ? 'current' : 'all');
  const categories = index.categoryChildren.get('') || [];
  const checkpoint = record(snapshot.checkpoint);
  return (
    <>
      <div className="page-heading overview-heading">
        <h2>{i18n.t('Overview')}</h2>
        <a className="button-link" href="#work">
          {i18n.t('Browse work')}
        </a>
      </div>
      <Objective snapshot={snapshot} />
      <ProgramAttention snapshot={snapshot} />
      <Progress snapshot={snapshot} />
      <div className="status-strip">
        {statuses.map((status) => (
          <div key={status}>
            <strong>{tasksInScope.filter((task) => task.status === status).length}</strong>
            <span>{i18n.statusLabel(status)}</span>
          </div>
        ))}
      </div>
      <Section title={i18n.t('Areas and responsibility')}>
        {categories.length ? (
          categories.map((category) => {
            const ids = categoryTasks(snapshot, string(category.id));
            const tasks = tasksInScope.filter((task) => ids.has(task.id));
            const done = tasks.filter((task) => task.status === 'done').length;
            const owners = [...new Set(tasks.map(ownerOf).filter(Boolean))];
            return (
              <div className="area-row" key={string(category.id)}>
                <div>
                  <strong>{i18n.display(category.title)}</strong>
                  <p className="muted">
                    {i18n.t('Recorded owners:')}{' '}
                    {owners.map(presentation.actor).join(', ') || i18n.t('No assignment yet')}
                  </p>
                </div>
                <span className="mono">
                  {done}/{tasks.length}
                </span>
              </div>
            );
          })
        ) : (
          <p className="muted">{i18n.t('No category hierarchy recorded.')}</p>
        )}
      </Section>
      {records(snapshot.questions).some(
        (question) =>
          !string(record(snapshot.objective).id) ||
          question.objective_id === record(snapshot.objective).id,
      ) && (
        <details
          className="overview-decisions"
          onToggle={(event) => setDecisionsOpen(event.currentTarget.open)}
        >
          <summary>{i18n.t('Related decisions')}</summary>
          {decisionsOpen && (
            <QuestionLinks
              snapshot={snapshot}
              objectiveId={string(record(snapshot.objective).id)}
              context={context}
              heading={false}
            />
          )}
        </details>
      )}
      <Section title={i18n.t('Director checkpoint')}>
        <Fields
          values={[
            ['Recorded', i18n.date(checkpoint.timestamp)],
            ['Final director', record(snapshot.director).actor_id],
          ]}
        />
        <Prose value={checkpoint.note} fallback="No checkpoint recorded yet." />
      </Section>
    </>
  );
}
function TaskList({
  snapshot,
  tasks: sorted,
  filters,
  onChange,
  selected,
  paging,
  onPage,
  onOpenTask,
  focusRequest,
  onFocusHandled,
  onRevealTask,
  preferences = defaultPreferences,
  taskHref,
  detail,
}: {
  readonly snapshot: Snapshot;
  readonly tasks: Snapshot['tasks'];
  readonly filters: Filters;
  readonly onChange: (filters: Filters) => void;
  readonly selected: string | null;
  readonly paging: WorkPage;
  readonly onPage: (page: WorkPage) => void;
  readonly onOpenTask: (id: string) => void;
  readonly focusRequest: WorkFocusRequest | null;
  readonly onFocusHandled: (token: number) => void;
  readonly onRevealTask: (id: string) => void;
  readonly preferences?: ViewPreferences;
  readonly taskHref?: (id: string) => string;
  readonly detail?: ReactNode;
}) {
  const i18n = useI18n();
  const index = snapshotIndex(snapshot);
  const page = useMemo(() => taskPage(sorted, paging), [sorted, paging]);
  const presentation = usePresentation();
  const selectedRef = useRef<HTMLAnchorElement>(null);
  const sizeRef = useRef<HTMLSelectElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const focusedRow = useRef<string | null>(null);
  useEffect(() => {
    if (page.index !== paging.index) onPage({ ...paging, index: page.index });
    if (focusRequest) {
      const target = focusRequest.taskId ? selectedRef.current : null;
      (target || sizeRef.current)?.focus({ preventScroll: true });
      target?.scrollIntoView?.({ block: 'nearest' });
      onFocusHandled(focusRequest.token);
    } else if (
      focusedRow.current &&
      !page.tasks.some((task) => task.id === focusedRow.current) &&
      document.activeElement === document.body
    ) {
      sizeRef.current?.focus({ preventScroll: true });
    }
  }, [page, paging, onPage, focusRequest, onFocusHandled]);
  const selectedPage = selected ? taskPageIndex(sorted, selected, paging.size) : null;
  const controls = [
    ['First page', 0, 'first'],
    ['Previous page', page.index - 1, 'previous'],
    ['Next page', page.index + 1, 'next'],
    ['Last page', page.count - 1, 'last'],
  ] as const;
  const select = (
    key: keyof Filters,
    title: string,
    options: readonly (readonly [string, string])[],
  ) => (
    <label>
      {i18n.t(title)}
      <select
        value={filters[key]}
        onChange={(event) => onChange({ ...filters, [key]: event.target.value })}
      >
        <option value="">
          {i18n.t(
            title === 'Status'
              ? 'All statuses'
              : title === 'Owner'
                ? 'All owners'
                : 'All workstreams',
          )}
        </option>
        {options.map(([value, name]) => (
          <option value={value} key={value}>
            {value === '__unassigned' ? i18n.t('Unassigned') : name}
          </option>
        ))}
      </select>
    </label>
  );
  const owners = [...index.tasksByOwner.keys()].filter(Boolean).sort();
  const workstreams = records(snapshot.workstreams);
  return (
    <div
      className={`work-ledger density-${preferences.density} ${preferences.columns.includes('owner') ? 'with-owner' : 'without-owner'}`}
      onFocusCapture={(event) => {
        const target = event.target;
        focusedRow.current =
          target instanceof HTMLElement
            ? target.closest<HTMLAnchorElement>('.task-button')?.dataset.taskId || null
            : null;
      }}
    >
      <form className="filters" role="search" onSubmit={(event) => event.preventDefault()}>
        <label className="search">
          {i18n.t('Search tasks')}
          <input
            ref={searchRef}
            type="search"
            value={filters.search}
            placeholder={i18n.t('Title, ID, requirements…')}
            onChange={(event) => onChange({ ...filters, search: event.target.value })}
          />
        </label>
        <details className="filter-options">
          <summary>{i18n.t('Filters')}</summary>
          <div className="filter-fields">
            {select(
              'status',
              'Status',
              statuses.map((status) => [status, i18n.statusLabel(status)]),
            )}
            {select('owner', 'Owner', [
              ...owners.map((owner) => [owner, presentation.actor(owner)] as const),
              ['__unassigned', 'Unassigned'],
            ])}
            {select(
              'workstream',
              'Workstream',
              workstreams.length
                ? workstreams.map((stream) => [
                    string(stream.id),
                    i18n.display(stream.name || stream.title),
                  ])
                : [...new Set(snapshot.tasks.map((task) => string(task.workstream)))].map(
                    (stream) => [stream, stream],
                  ),
            )}
            <label>
              {i18n.t('Sort')}
              <select
                value={filters.sort}
                onChange={(event) => onChange({ ...filters, sort: event.target.value })}
              >
                {['id', 'title', 'status', 'owner', 'phase'].map((value) => (
                  <option key={value} value={value}>
                    {value === 'id' ? i18n.t('Task ID') : i18n.human(value)}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </details>
      </form>
      <div className="active-filters" aria-label={i18n.t('Active filters')}>
        {Object.entries(filters).some(([key, value]) => key !== 'sort' && Boolean(value)) && (
          <IconButton
            icon="clear"
            label={i18n.t('Clear filters')}
            onClick={() => {
              searchRef.current?.focus({ preventScroll: true });
              onChange(emptyFilters);
            }}
          />
        )}
        {Object.entries(filters)
          .filter(([key, value]) => key !== 'sort' && Boolean(value))
          .map(([key, value]) => (
            <button
              type="button"
              key={key}
              onClick={() => {
                searchRef.current?.focus({ preventScroll: true });
                onChange({ ...filters, [key]: '' });
              }}
            >
              {i18n.t(
                key === 'search'
                  ? 'Search'
                  : key === 'owner'
                    ? 'Owner'
                    : key === 'category'
                      ? 'Category'
                      : key === 'status'
                        ? 'Status'
                        : 'Workstream',
              )}
              :{' '}
              {key === 'status'
                ? i18n.statusLabel(value)
                : key === 'owner' && value === '__unassigned'
                  ? i18n.t('Unassigned')
                  : key === 'owner'
                    ? presentation.actor(value)
                    : key === 'category'
                      ? string(index.categories.get(value)?.title) || value
                      : key === 'workstream'
                        ? presentation.workstream(value)
                        : value}{' '}
              ×
            </button>
          ))}
      </div>
      <div className="ledger-summary">
        <nav className="paging" aria-label={i18n.t('Work pages')}>
          <label>
            {i18n.t('Tasks per page')}
            <select
              ref={sizeRef}
              value={paging.size}
              onChange={(event) => onPage({ index: 0, size: pageSize(event.target.value) })}
            >
              {pageSizes.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
          <div className="paging-actions">
            {controls.map(([label, target, icon]) => (
              <IconButton
                key={label}
                icon={icon}
                label={i18n.t(label)}
                aria-disabled={
                  !page.count || target < 0 || target >= page.count || target === page.index
                }
                onClick={() => {
                  if (page.count && target >= 0 && target < page.count && target !== page.index)
                    onPage({ ...paging, index: target });
                }}
              />
            ))}
          </div>
          <p className="paging-range" role="status" aria-live="polite" aria-atomic="true">
            <span aria-hidden="true">
              {i18n.t('{start}–{end} / {matching} · {page}/{pages}', {
                start: page.start,
                end: page.end,
                matching: page.matching,
                page: page.count ? page.index + 1 : 0,
                pages: page.count,
              })}
            </span>
            <span className="sr-only">
              {i18n.t(
                'Showing {start}–{end} of {matching} matching tasks · Page {page} of {pages}',
                {
                  start: page.start,
                  end: page.end,
                  matching: page.matching,
                  page: page.count ? page.index + 1 : 0,
                  pages: page.count,
                },
              )}
            </span>
          </p>
        </nav>
        {selected && selectedPage !== page.index && (
          <p className="selection-context">
            {i18n.t(
              !index.tasks.has(selected)
                ? 'Selected task {id} is no longer available.'
                : selectedPage === null
                  ? 'Selected task {id} does not match these filters.'
                  : selectedPage !== page.index
                    ? 'Selected task {id} is on another page.'
                    : 'Selected task: {id}',
              { id: selected },
            )}
            {selectedPage !== null && (
              <button type="button" onClick={() => onRevealTask(selected)}>
                {i18n.t('Show selected task')}
              </button>
            )}
          </p>
        )}
      </div>
      <div className={`work-stage ${detail ? 'has-preview' : ''}`}>
        <section className="ledger-pane" aria-label={i18n.t('Task ledger')}>
          <div className="row-labels" aria-hidden="true">
            <span>
              {i18n.t(preferences.columns.includes('category') ? 'Task / category' : 'Task')}
            </span>
            <span>{i18n.t('Status')}</span>
            {preferences.columns.includes('owner') && (
              <span className="owner-label">{i18n.t('Owner')}</span>
            )}
          </div>
          <div className="task-list" role="region" aria-label={i18n.t('Work')} tabIndex={0}>
            {page.tasks.map((task) => (
              <a
                key={task.id}
                href={taskHref?.(task.id) || `#task/${encodeURIComponent(task.id)}`}
                className={`task-button ${selected === task.id ? 'selected' : ''}`}
                ref={task.id === selected ? selectedRef : undefined}
                data-task-id={task.id}
                aria-current={selected === task.id ? 'true' : undefined}
                onClick={(event) => {
                  if (!event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
                    if (taskHref) event.preventDefault();
                    onOpenTask(task.id);
                  }
                }}
              >
                <span>
                  <span className="task-identity">
                    <span className="mono task-id">{task.id}</span>{' '}
                    <span className="task-title">{task.title}</span>
                  </span>
                  <span className="task-meta">
                    {preferences.columns.includes('category') && (
                      <>
                        {' '}
                        ·{' '}
                        {i18n.display(
                          index.categories.get(string(task.category_id))?.title || task.workstream,
                          'Uncategorized',
                        )}
                      </>
                    )}
                    {preferences.columns.includes('workstream') && (
                      <> · {i18n.display(presentation.workstream(task.workstream))}</>
                    )}
                  </span>
                  {hasActionGlance(task) && (
                    <span className="task-next-action">
                      <span className="sr-only">{i18n.t('Next action')} · </span>
                      {i18n.display(task.next_action, 'No next action recorded')}
                    </span>
                  )}
                </span>
                <Status value={task.status} />
                {preferences.columns.includes('owner') && (
                  <span className="task-owner">
                    {task.status === 'done' && !ownerOf(task) ? (
                      <span aria-label={i18n.t('No current owner')}>—</span>
                    ) : (
                      presentation.actor(ownerOf(task)) || i18n.t('Unassigned')
                    )}
                  </span>
                )}
              </a>
            ))}
            {!sorted.length && (
              <p className="empty">
                {snapshot.tasks.length
                  ? i18n.t('No tasks match these filters. Clear filters to see all work.')
                  : i18n.t('This program has no tasks.')}
              </p>
            )}
          </div>
        </section>
        {detail && (
          <section className="detail-pane" aria-label={i18n.t('Task inspection')}>
            {detail}
          </section>
        )}
      </div>
    </div>
  );
}
function Handoff({ handoff }: { readonly handoff: JsonRecord }) {
  const i18n = useI18n();
  const presentation = usePresentation();
  const outgoing = record(handoff.outgoing);
  const acknowledgement = record(handoff.acknowledgement);
  return (
    <article className="handoff">
      <div className="handoff-heading">
        <strong>
          {i18n.display(presentation.actor(handoff.from_actor))}{' '}
          <span className="muted">{i18n.t('to')}</span>{' '}
          {i18n.display(presentation.actor(handoff.to_actor))}
        </strong>
        <span className="badge">
          {handoff.acceptance
            ? i18n.t('Accepted')
            : handoff.acknowledgement
              ? i18n.t('Acknowledged')
              : i18n.t('Pending acknowledgement')}
        </span>
      </div>
      <p className="muted">
        {i18n.human(handoff.role)} · {i18n.display(handoff.task_id, 'Program succession')}
      </p>
      <p className="prose">{i18n.display(outgoing.goal)}</p>
      <Fields
        values={[
          ['Next action', outgoing.next_action],
          ['Acknowledgement', acknowledgement.summary || i18n.t('Not recorded')],
        ]}
      />
      <details>
        <summary>{i18n.t('Constraints and findings')}</summary>
        <Prose value={outgoing.constraints} />
        <Prose value={outgoing.unresolved_findings} fallback="No unresolved findings recorded" />
      </details>
      <TransferHistory handoff={handoff} />
    </article>
  );
}
function TaskDetail({
  context = defaultContext,
  snapshot,
  taskId,
  returnHref = '#work',
  fullHref,
  onReturn,
  section = 'information',
  onSection,
}: {
  readonly context?: WorkContext;
  readonly snapshot: Snapshot;
  readonly taskId?: string;
  readonly returnHref?: string;
  readonly fullHref?: string;
  readonly onReturn?: () => void;
  readonly section?: TaskSection;
  readonly onSection: (section: TaskSection) => void;
}) {
  const i18n = useI18n();
  const presentation = usePresentation();
  const returnClick = (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (onReturn && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
      event.preventDefault();
      onReturn();
    }
  };
  const task = snapshotIndex(snapshot).tasks.get(taskId || '');
  if (!task)
    return (
      <>
        <a href={returnHref} onClick={returnClick}>
          {i18n.t('Back to work')}
        </a>
        <h2 className="not-found" tabIndex={-1}>
          {i18n.t('Task not available')}
        </h2>
        <p className="muted">
          {i18n.t(
            'The task may have been removed from the latest snapshot. Your list filters are preserved.',
          )}
        </p>
      </>
    );
  const attempts = records(task.attempts);
  const attempt = attempts.at(-1) || {};
  const assignment = record(attempt.assignment);
  const assignedActorId = string(assignment.actor_id) || string(attempt.owner);
  const actor = snapshotIndex(snapshot).actors.get(assignedActorId) || {};
  const description = taskDescriptionParts(taskBody(task));
  const expectedResult = task.expected_result;
  const expectedRepeated =
    typeof expectedResult === 'string' &&
    description.description
      .trim()
      .split(/\n\s*\n/u)
      .some((paragraph) => paragraph.trim() === expectedResult.trim());
  const criteria = task.acceptance_criteria;
  const distinctCriteria =
    criteria !== undefined &&
    criteria !== null &&
    criteria !== '' &&
    (!Array.isArray(criteria) || criteria.length > 0) &&
    !semanticValueEqual(criteria, description.criteria?.trim()) &&
    !semanticValueEqual(criteria, description.criteriaItems) &&
    !semanticValueEqual(criteria, description.description.trim());
  const handoffs = records(snapshot.handoffs).filter(
    (item) => !item.task_id || item.task_id === task.id,
  );
  const neighborhood = taskNeighborhood(snapshot, task);
  const hasRelationships = Object.values(neighborhood).some((group) => group.length > 0);
  const hasQuestions = records(snapshot.questions).some((question) =>
    items(question.affected_task_ids).includes(task.id),
  );
  const hasNotes = task.notes != null && (Array.isArray(task.notes) ? task.notes.length > 0 : true);
  const taskSections = [
    ['information', 'Task information'],
    ['related', 'Related work'],
    ['history', 'History'],
  ] as const;
  const sectionId = (value: TaskSection) => `task-${encodeURIComponent(task.id)}-${value}`;
  return (
    <>
      <nav className="detail-actions" aria-label={i18n.t('Task navigation')}>
        <IconLink
          icon="back"
          label={i18n.t('Back to work')}
          className="back-link"
          href={returnHref}
          onClick={returnClick}
        />
        {fullHref && (
          <IconLink
            icon="expand"
            label={i18n.t('Open full detail')}
            className="full-detail-link"
            href={fullHref}
          />
        )}
        {fullHref && (
          <IconLink
            icon="close"
            label={i18n.t('Close detail')}
            className="close-detail"
            href={returnHref}
            onClick={returnClick}
          />
        )}
      </nav>
      <div className="page-heading detail-heading">
        <div>
          <p className="eyebrow mono">{task.id}</p>
          <h2 tabIndex={-1}>{task.title}</h2>
        </div>
        <Status value={task.status} />
      </div>
      <div className="task-detail-tabs" role="tablist" aria-label={i18n.t('Task sections')}>
        {taskSections.map(([value, label], index) => (
          <button
            key={value}
            type="button"
            role="tab"
            id={`${sectionId(value)}-tab`}
            aria-controls={sectionId(value)}
            aria-selected={section === value}
            tabIndex={section === value ? 0 : -1}
            data-section={value}
            onClick={() => onSection(value)}
            onKeyDown={(event) => {
              const nextIndex =
                event.key === 'ArrowRight'
                  ? (index + 1) % taskSections.length
                  : event.key === 'ArrowLeft'
                    ? (index + taskSections.length - 1) % taskSections.length
                    : event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? taskSections.length - 1
                        : null;
              if (nextIndex === null) return;
              const next = taskSections[nextIndex];
              if (!next) return;
              event.preventDefault();
              onSection(next[0]);
              event.currentTarget.parentElement
                ?.querySelector<HTMLButtonElement>(`[data-section="${next[0]}"]`)
                ?.focus();
            }}
          >
            {i18n.t(label)}
          </button>
        ))}
      </div>
      <section
        className="task-detail-panel"
        role="tabpanel"
        id={sectionId('information')}
        aria-labelledby={`${sectionId('information')}-tab`}
        hidden={section !== 'information'}
        tabIndex={0}
      >
        <div className="task-detail-grid">
          <div>
            <Section title={i18n.t('Next action')}>
              <dl className="fields">
                <div>
                  <dt>{i18n.t('Owner')}</dt>
                  <dd>
                    <a
                      href={workloadHref('all', ownerOf(task))}
                      aria-label={i18n.t('Assignments for {owner}', {
                        owner: presentation.actor(ownerOf(task)) || i18n.t('Unassigned'),
                      })}
                    >
                      {presentation.actor(ownerOf(task)) || i18n.t('Unassigned')}
                    </a>
                  </dd>
                </div>
                <div>
                  <dt>{i18n.t('Next action')}</dt>
                  <dd>{i18n.display(task.next_action)}</dd>
                </div>
              </dl>
              <div className="task-description-content">
                <h3>{i18n.t('Task description')}</h3>
                {description.description.trim() ? (
                  <TaskDescription text={description.description} />
                ) : (
                  <Prose />
                )}
              </div>
              {expectedResult !== undefined &&
                expectedResult !== null &&
                expectedResult !== '' &&
                !expectedRepeated && (
                  <div className="expected-result-summary">
                    <h3>{i18n.t('Expected result')}</h3>
                    <Prose value={expectedResult} />
                  </div>
                )}
              <details className="expected-result">
                <summary>{i18n.t('Planning and metadata')}</summary>
                <Prose value={task.planning_note} />
                <Fields
                  values={[
                    [
                      'Blocker',
                      task.blocker ||
                        attempt.blocker ||
                        (task.status === 'blocked' ? task.next_action : i18n.t('None recorded')),
                    ],
                    ['Workstream', task.workstream],
                    ['Phase', task.phase],
                    [
                      'Category',
                      snapshotIndex(snapshot).categories.get(string(task.category_id))?.title,
                    ],
                  ]}
                />
              </details>
            </Section>
            <ReviewSummary snapshot={snapshot} task={task} />
            {(description.criteria || distinctCriteria) && (
              <div className="content-section">
                <details className="task-acceptance-criteria">
                  <summary>{i18n.t('Acceptance criteria')}</summary>
                  {description.criteria && <TaskDescription text={description.criteria} />}
                  {distinctCriteria && <Prose value={criteria} />}
                </details>
              </div>
            )}
          </div>
          <div className="task-context">
            <ReadinessContext snapshot={snapshot} task={task} />
            <Section title={i18n.t('Required checks')}>
              <details>
                <summary>{i18n.t('Required checks')}</summary>
                <Prose value={task.required_checks} fallback="No required checks recorded" />
              </details>
            </Section>
          </div>
        </div>
      </section>
      <section
        className="task-detail-panel"
        role="tabpanel"
        id={sectionId('related')}
        aria-labelledby={`${sectionId('related')}-tab`}
        hidden={section !== 'related'}
        tabIndex={0}
      >
        {hasRelationships || hasQuestions || hasNotes ? (
          <>
            {hasRelationships && <DependencyNeighborhood snapshot={snapshot} task={task} />}
            <QuestionLinks snapshot={snapshot} taskId={taskId} context={context} />
            {hasNotes && <CoordinationNotes task={task} />}
          </>
        ) : (
          <p className="muted">{i18n.t('No related work recorded.')}</p>
        )}
      </section>
      <section
        className="task-detail-panel"
        role="tabpanel"
        id={sectionId('history')}
        aria-labelledby={`${sectionId('history')}-tab`}
        hidden={section !== 'history'}
        tabIndex={0}
      >
        <TaskHistory snapshot={snapshot} task={task} />
        <Section title={i18n.t('Recorded assignment')}>
          <details>
            <summary>{i18n.t('Recorded assignment')}</summary>
            <Fields
              values={[
                ['Current owner', presentation.actor(ownerOf(task)) || i18n.t('Unassigned')],
                ['Assigned actor', assignedActorId],
                ['Role', i18n.human(assignment.role)],
                ['Model', i18n.personnel('model_id', assignment.model_id)],
                ['Reasoning', i18n.personnel('reasoning_effort', assignment.reasoning_effort)],
                ['Staffing', i18n.human(actor.staffing_status)],
                [
                  'Last observed',
                  `${i18n.personnel('observed_status', actor.observed_status)} · ${i18n.date(actor.observed_at)}`,
                ],
              ]}
            />
            {assignedActorId && !snapshotIndex(snapshot).actors.has(assignedActorId) && (
              <p className="muted">{i18n.t('No personnel record')}</p>
            )}
            <StaffingHistory actor={actor} assignment={assignment} disclosure={false} />
          </details>
        </Section>
        <DeliveryHistory task={task} />
        <Section title={i18n.t('Personnel handoffs')}>
          <details>
            <summary>{i18n.t('Personnel handoffs')}</summary>
            {handoffs.length ? (
              handoffs.map((handoff) => <Handoff key={string(handoff.id)} handoff={handoff} />)
            ) : (
              <p className="muted">{i18n.t('No handoff recorded for this task.')}</p>
            )}
          </details>
        </Section>
      </section>
    </>
  );
}
function Team({
  snapshot,
  context,
}: {
  readonly snapshot: Snapshot;
  readonly context: WorkContext;
}) {
  const i18n = useI18n();
  const presentation = usePresentation();
  const index = snapshotIndex(snapshot);
  const actors = [...index.actors.values()];
  const responsibilities = record(snapshot.responsibilities);
  const walk = (parent: Json | undefined, visited: ReadonlySet<string>): ReactNode =>
    (index.actorChildren.get(string(parent)) || []).map((actor) => {
      const id = string(actor.id);
      if (visited.has(id)) return null;
      const assignments = snapshotIndex(snapshot).tasksByOwner.get(id) || [];
      return (
        <div className="actor-branch" key={id}>
          <article className="actor-record">
            <div className="actor-heading">
              <h3>{presentation.actor(id)}</h3>
              <span className="badge">
                {i18n.human(actor.role)} · {i18n.human(actor.staffing_status)}
              </span>
            </div>
            <p className="role-purpose">
              {i18n.t(
                i18n.display(
                  items(responsibilities[string(actor.role)])[0],
                  'No role responsibility recorded',
                ),
              )}
            </p>
            <Fields
              values={[
                ['Model', i18n.personnel('model_id', actor.model_id)],
                ['Reasoning', i18n.personnel('reasoning_effort', actor.reasoning_effort)],
                [
                  'Last observed',
                  `${i18n.personnel('observed_status', actor.observed_status)} · ${i18n.date(actor.observed_at)}`,
                ],
              ]}
            />
            <p className="muted">
              {i18n.t('Current recorded assignments:')}{' '}
              {assignments.map((task) => task.id).join(', ') || i18n.t('None')}
            </p>
            <StaffingHistory actor={actor} />
          </article>
          <div className="actor-child">{walk(id, new Set([...visited, id]))}</div>
        </div>
      );
    });
  const director = record(snapshot.director);
  const handoffs = records(snapshot.handoffs);
  return (
    <>
      <div className="page-heading">
        <div>
          <h2>{i18n.t('Team')}</h2>
        </div>
        <span className="muted">
          {i18n.t('{count} personnel records', { count: actors.length })}
        </span>
      </div>
      <Workload snapshot={snapshot} context={context} />
      <GoalCoordination snapshot={snapshot} />
      <details className="team-context">
        <summary>{i18n.t('Team records and responsibilities')}</summary>
        <p className="intro">
          {i18n.t(
            'Management relationships and assignments are recorded by the director. This page does not poll live agent sessions.',
          )}
        </p>
        <Section title={i18n.t('Final director')}>
          <Fields
            values={[
              ['Appointed actor', director.actor_id],
              ['Acknowledged', i18n.date(director.acknowledged_at)],
              [
                'Merge target',
                record(director.authority).merge_target || i18n.t('No merge authority recorded'),
              ],
            ]}
          />
          <details>
            <summary>{i18n.t('Authority and responsibility')}</summary>
            <Prose value={items(responsibilities.director).map((item) => i18n.t(string(item)))} />
          </details>
        </Section>
        <Section title={i18n.t('Management hierarchy')}>
          {actors.length ? (
            walk(null, new Set())
          ) : (
            <p className="empty">
              {i18n.t(
                'No personnel metadata recorded. A task owner string alone does not establish a live agent.',
              )}
            </p>
          )}
        </Section>
        <Section title={i18n.t('Personnel handoffs')}>
          {handoffs.length ? (
            handoffs.map((handoff) => <Handoff key={string(handoff.id)} handoff={handoff} />)
          ) : (
            <p className="muted">{i18n.t('No personnel handoffs recorded.')}</p>
          )}
        </Section>
      </details>
    </>
  );
}

function Dashboard({
  loader,
  sessionLoader,
  answerSaver,
}: {
  readonly loader?: SnapshotLoader;
  readonly sessionLoader?: SessionLoader;
  readonly answerSaver?: AnswerSaver;
}) {
  const i18n = useI18n();
  const { snapshot: rawSnapshot, observed, error, busy, announce, refresh } = useSnapshot(loader);
  const snapshot = useMemo(
    () => (rawSnapshot ? presentSnapshot(rawSnapshot) : null),
    [rawSnapshot],
  );
  const programLanguage = string(snapshot?.communication_language);
  useEffect(() => {
    i18n.setProgramLanguage(language(programLanguage));
  }, [programLanguage, i18n.setProgramLanguage]);
  const session = useSession(sessionLoader);
  const drafts = useQuestionDrafts();
  const [questionFiltersOpen, setQuestionFiltersOpen] = useState(false);
  const questionOrigin = useRef<{
    id: string;
    list: string;
    mainScroll: number;
    documentScroll: number;
  } | null>(null);
  const [navigation, setNavigation] = useState<Navigation>(() =>
    parseNavigation(window.location.hash),
  );
  const { route, filters, selected, paging, scope } = navigation;
  const initialNavigation = useRef(navigation);
  const currentNavigation = useRef(navigation);
  currentNavigation.current = navigation;
  const [preferences, setPreferences] = useState<ViewPreferences>(defaultPreferences);
  const [storageUnavailable, setStorageUnavailable] = useState(false);
  const [wide, setWide] = useState(() => window.innerWidth >= 1100);
  const [focusRequest, setFocusRequest] = useState<WorkFocusRequest | null>(null);
  const focusToken = useRef(0);
  const listOrigin = useRef<{
    readonly taskId: string;
    readonly context: WorkContext;
    readonly ledgerScroll: number;
    readonly mainScroll: number;
    readonly documentScroll: number;
  } | null>(null);
  const returnScroll = useRef<{
    readonly token: number;
    readonly origin: NonNullable<typeof listOrigin.current>;
    frame: number | null;
  } | null>(null);
  const previousProgram = useRef<string | null>(null);
  const main = useRef<HTMLElement>(null);
  const removedQuestionFocus = useRef<{ element: HTMLElement; questionId: string } | null>(null);
  useEffect(() => {
    const remember = (event: FocusEvent) => {
      const element = event.target;
      removedQuestionFocus.current =
        element instanceof HTMLElement && element.closest('.question-form')
          ? { element, questionId: currentNavigation.current.route.questionId || '' }
          : null;
    };
    const abandon = () => {
      removedQuestionFocus.current = null;
    };
    const pointer = (event: PointerEvent) => {
      if (event.target !== removedQuestionFocus.current?.element) abandon();
    };
    document.addEventListener('focusin', remember);
    document.addEventListener('pointerdown', pointer, true);
    window.addEventListener('blur', abandon);
    return () => {
      document.removeEventListener('focusin', remember);
      document.removeEventListener('pointerdown', pointer, true);
      window.removeEventListener('blur', abandon);
    };
  }, []);
  useLayoutEffect(() => {
    const previous = removedQuestionFocus.current;
    if (!previous || route.view !== 'question' || previous.questionId !== route.questionId) return;
    if (previous.element.isConnected && !previous.element.matches(':disabled')) return;
    removedQuestionFocus.current = null;
    if (document.activeElement !== document.body) return;
    main.current
      ?.querySelector<HTMLElement>('.question-heading h2,.not-found')
      ?.focus({ preventScroll: true });
  }, [snapshot]);
  const initialized = useRef(false);
  const commit = useCallback((next: Navigation, replace = false) => {
    currentNavigation.current = next;
    setNavigation(next);
    const hash = navigationHash(next.route, next);
    if (window.location.hash !== hash)
      window.history[replace ? 'replaceState' : 'pushState'](null, '', hash);
  }, []);
  const navigate = useCallback(
    (nextRoute: Route, context?: WorkContext) => {
      const current = currentNavigation.current;
      const next = {
        ...current,
        ...context,
        route: nextRoute,
        selected: nextRoute.taskId || context?.selected || current.selected,
        issues: [],
      };
      commit(next);
    },
    [commit],
  );
  useEffect(() => {
    const resize = () => setWide(window.innerWidth >= 1100);
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  useEffect(() => {
    const changed = () => {
      const previous = currentNavigation.current;
      let next = parseNavigation(window.location.hash, snapshot, previous);
      if (previous.route.view === 'questions' && next.route.view === 'question')
        questionOrigin.current = {
          id: next.route.questionId || '',
          list: questionListIdentity(previous.questions),
          mainScroll: main.current?.scrollTop || 0,
          documentScroll: window.scrollY,
        };
      if (next.route.view === 'work' && previous.route.view === 'task') {
        const origin = listOrigin.current;
        const sameList =
          origin &&
          (!next.selected || next.selected === origin.taskId) &&
          navigationHash({ view: 'work' }, { ...next, selected: null }) ===
            navigationHash({ view: 'work' }, { ...origin.context, selected: null });
        const taskId = sameList ? origin.taskId : next.selected;
        if (snapshot && taskId && sameList) {
          const rank = taskPageIndex(selectWorkTasks(snapshot, next), taskId, next.paging.size);
          next = {
            ...next,
            selected: taskId,
            paging: { ...next.paging, index: rank ?? next.paging.index },
          };
        }
        const token = ++focusToken.current;
        returnScroll.current = sameList ? { token, origin, frame: null } : null;
        if (!sameList) listOrigin.current = null;
        setFocusRequest(taskId ? { token, taskId } : null);
        commit(next, true);
        return;
      }
      currentNavigation.current = next;
      setNavigation(next);
    };
    window.addEventListener('hashchange', changed);
    window.addEventListener('popstate', changed);
    return () => {
      window.removeEventListener('hashchange', changed);
      window.removeEventListener('popstate', changed);
    };
  }, [snapshot, commit]);
  useEffect(() => {
    if (!snapshot) return;
    if (previousProgram.current === snapshot.program_id) {
      const parsed = parseNavigation(
        navigationHash(currentNavigation.current.route, currentNavigation.current),
        snapshot,
      );
      if (
        parsed.issues.length &&
        JSON.stringify(parsed.filters) !== JSON.stringify(currentNavigation.current.filters)
      )
        commit(parsed, true);
      return;
    }
    const saved = readPreferences(snapshot.program_id);
    setPreferences(saved.preferences);
    setStorageUnavailable(saved.unavailable);
    if (previousProgram.current && previousProgram.current !== snapshot.program_id) {
      setQuestionFiltersOpen(false);
      commit({ ...defaultContext, route: { view: 'overview' }, issues: [], explicit: false }, true);
      setFocusRequest(null);
      listOrigin.current = null;
    } else {
      // Navigation can occur after the first DOM commit but before this effect.
      // Validate that established context instead of replacing it with URL defaults.
      const explicit = parseNavigation(
        currentNavigation.current !== initialNavigation.current
          ? navigationHash(currentNavigation.current.route, currentNavigation.current)
          : window.location.hash,
        snapshot,
      );
      const preset = saved.preferences.presets.find(
        (value) => value.name === saved.preferences.selectedPreset,
      );
      const context =
        !explicit.explicit && preset
          ? {
              ...parseNavigation(preset.hash, snapshot),
              route: explicit.route,
              selected: explicit.route.taskId || explicit.selected,
              questions: explicit.questions,
            }
          : explicit;
      setNavigation(
        !context.explicit && !preset && goalTaskIds(snapshot)
          ? { ...context, scope: 'current' }
          : context,
      );
    }
    previousProgram.current = snapshot.program_id;
  }, [snapshot, commit]);
  useEffect(() => {
    if (initialized.current && !(route.view === 'work' && selected)) {
      const target =
        route.view === 'task'
          ? main.current?.querySelector<HTMLElement>('.detail-heading h2,.not-found') ||
            main.current
          : route.view === 'question'
            ? main.current?.querySelector<HTMLElement>('.question-heading h2,.not-found') ||
              main.current
            : main.current;
      target?.focus({ preventScroll: true });
      if (route.view === 'task') {
        const pane = main.current?.querySelector('.detail-pane');
        if (pane) pane.scrollTop = 0;
        else main.current?.scrollIntoView?.({ block: 'start' });
      }
    }
    initialized.current = true;
    if (route.view === 'question' && snapshot) {
      const entry = drafts.peek(questionDraftKey(snapshot.program_id, route.questionId || ''));
      const input = main.current?.querySelector<HTMLTextAreaElement>('[data-question-answer]');
      if (input && entry?.selection) {
        input.focus();
        input.setSelectionRange(entry.selection.start, entry.selection.end);
      } else {
        main.current
          ?.querySelector('.question-heading,.not-found')
          ?.scrollIntoView?.({ block: 'start' });
      }
    }
    if (route.view === 'questions' && questionOrigin.current) {
      const origin = questionOrigin.current;
      questionOrigin.current = null;
      if (origin.list !== questionListIdentity(navigation.questions)) return;
      const row = [
        ...(main.current?.querySelectorAll<HTMLElement>('[data-question-id]') || []),
      ].find((item) => item.dataset.questionId === origin.id);
      row?.focus({ preventScroll: true });
      const frame = requestAnimationFrame(() => {
        if (main.current) main.current.scrollTop = origin.mainScroll;
        if (window.scrollY !== origin.documentScroll) window.scrollTo(0, origin.documentScroll);
      });
      return () => cancelAnimationFrame(frame);
    }
  }, [route.view, route.taskId, route.full, route.questionId, selected, Boolean(snapshot)]);
  const integrity = record(snapshot?.integrity);
  const stale = integrity.status === 'stale';
  const changeContext = useCallback(
    (context: WorkContext, replace = false) =>
      commit({ ...currentNavigation.current, ...context, issues: [] }, replace),
    [commit],
  );
  const selectCategory = useCallback(
    (category: string) => {
      const current = currentNavigation.current;
      changeContext({
        ...current,
        filters: { ...current.filters, category },
        paging: { ...current.paging, index: 0 },
      });
    },
    [changeContext],
  );
  const changeFilters = useCallback(
    (next: Filters) => {
      const current = currentNavigation.current;
      changeContext(
        { ...current, filters: next, paging: { ...current.paging, index: 0 } },
        next.search !== current.filters.search,
      );
    },
    [changeContext],
  );
  const setPaging = useCallback(
    (page: WorkPage) => changeContext({ ...currentNavigation.current, paging: page }),
    [changeContext],
  );
  const openTask = useCallback(
    (taskId: string) => {
      const current = currentNavigation.current;
      listOrigin.current = {
        taskId,
        context: current,
        ledgerScroll: main.current?.querySelector('.task-list')?.scrollTop || 0,
        mainScroll: main.current?.scrollTop || 0,
        documentScroll: window.scrollY,
      };
      navigate({ view: 'task', taskId }, { ...current, selected: taskId });
    },
    [navigate],
  );
  const focusHandled = useCallback((token: number) => {
    const pending = returnScroll.current;
    if (pending?.token === token) {
      if (pending.frame !== null) return;
      pending.frame = requestAnimationFrame(() => {
        if (
          returnScroll.current !== pending ||
          !main.current ||
          currentNavigation.current.route.view !== 'work'
        )
          return;
        const { origin } = pending;
        const ledger = main.current.querySelector('.task-list');
        if (ledger) ledger.scrollTop = origin.ledgerScroll;
        main.current.scrollTop = origin.mainScroll;
        if (window.scrollY !== origin.documentScroll)
          window.scrollTo({ top: origin.documentScroll });
        returnScroll.current = null;
        listOrigin.current = null;
        setFocusRequest((previous) => (previous?.token === token ? null : previous));
      });
      return;
    }
    setFocusRequest((previous) => (previous?.token === token ? null : previous));
  }, []);
  const returnToWork = useCallback(() => {
    const origin = listOrigin.current;
    const context = origin?.context || currentNavigation.current;
    const taskId = origin?.taskId || currentNavigation.current.selected;
    const rank =
      origin && snapshot && taskId
        ? taskPageIndex(selectWorkTasks(snapshot, context), taskId, context.paging.size)
        : null;
    const token = ++focusToken.current;
    returnScroll.current = origin ? { token, origin, frame: null } : null;
    setFocusRequest({ token, taskId });
    commit({
      ...currentNavigation.current,
      ...context,
      route: { view: 'work' },
      selected: taskId,
      paging: { ...context.paging, index: rank ?? context.paging.index },
    });
  }, [snapshot, commit]);
  const revealTask = useCallback(
    (taskId: string) => {
      if (!snapshot) return;
      const index = taskPageIndex(selectWorkTasks(snapshot, navigation), taskId, paging.size);
      if (index === null) return;
      setPaging({ ...paging, index });
      setFocusRequest({ token: ++focusToken.current, taskId });
    },
    [snapshot, navigation, paging, setPaging],
  );
  const applyPreset = useCallback(
    (hash: string) => {
      listOrigin.current = null;
      returnScroll.current = null;
      setFocusRequest(null);
      commit(parseNavigation(hash, snapshot));
    },
    [snapshot, commit],
  );
  const changePreferences = useCallback(
    (next: ViewPreferences) => {
      setPreferences(next);
      if (snapshot && !writePreferences(snapshot.program_id, next)) setStorageUnavailable(true);
    },
    [snapshot?.program_id],
  );
  const freshness = error
    ? i18n.t('Unavailable')
    : busy && snapshot
      ? i18n.t('Refreshing…')
      : observed
        ? i18n.t('Fetched {time}', { time: observed.toLocaleTimeString(i18n.localeName) })
        : i18n.t('Loading program…');
  const showsLedger = route.view === 'work' || (route.view === 'task' && wide && !route.full);
  const matchingTasks = useMemo(
    () => (snapshot && showsLedger ? selectWorkTasks(snapshot, { scope, filters }) : []),
    [snapshot, scope, filters, showsLedger],
  );
  const content = useMemo(() => {
    let content: ReactNode;
    const matchingTaskCount = matchingTasks.length;
    const scopedTaskCount = snapshot ? scopedTasks(snapshot, scope).length : 0;
    if (!snapshot)
      content = (
        <>
          <h2>{error ? i18n.t('Program unavailable') : i18n.t('Loading program')}</h2>
          <p className="empty">
            {error
              ? i18n.t('The program could not be read. Check the server configuration.')
              : i18n.t('Reading the local snapshot…')}
          </p>
        </>
      );
    else if (route.view === 'overview')
      content = <Overview snapshot={snapshot} context={navigation} />;
    else if (route.view === 'work' || (route.view === 'task' && wide && !route.full))
      content = (
        <>
          <div className="page-heading work-heading">
            <div>
              <h2>{i18n.t(scope === 'current' ? 'Current goal' : 'All work')}</h2>
            </div>
            <span className="muted">
              <span className="sr-only">
                {i18n.t('{shown} of {total} tasks', {
                  shown: matchingTaskCount,
                  total: scopedTaskCount,
                })}
              </span>
              <span aria-hidden="true">
                {matchingTaskCount === scopedTaskCount
                  ? i18n.t('{count} tasks', { count: matchingTaskCount })
                  : i18n.t('{shown} / {total} tasks', {
                      shown: matchingTaskCount,
                      total: scopedTaskCount,
                    })}
              </span>
            </span>
          </div>
          {scope === 'current' && !goalTaskIds(snapshot) && (
            <p className="empty">
              {i18n.t(
                'Current goal membership is not recorded. Choose All work to inspect the preserved backlog.',
              )}
            </p>
          )}
          <ViewControls
            context={navigation}
            preferences={preferences}
            onContext={changeContext}
            onPreferences={changePreferences}
            onPreset={applyPreset}
          />
          <Breadcrumbs snapshot={snapshot} category={filters.category} onSelect={selectCategory} />
          <TaskList
            snapshot={snapshot}
            tasks={matchingTasks}
            filters={filters}
            onChange={changeFilters}
            selected={selected}
            paging={paging}
            onPage={setPaging}
            onOpenTask={openTask}
            focusRequest={focusRequest}
            onFocusHandled={focusHandled}
            onRevealTask={revealTask}
            preferences={preferences}
            taskHref={(id) =>
              navigationHash({ view: 'task', taskId: id }, { ...navigation, selected: id })
            }
            detail={
              route.view === 'task' && (
                <TaskDetail
                  key={route.taskId}
                  snapshot={snapshot}
                  context={navigation}
                  taskId={route.taskId}
                  section={route.section}
                  onSection={(section) =>
                    navigate({ ...route, section: section === 'information' ? undefined : section })
                  }
                  onReturn={returnToWork}
                  returnHref={navigationHash(
                    { view: 'work' },
                    {
                      ...(listOrigin.current?.context || navigation),
                      selected: listOrigin.current?.taskId || selected,
                    },
                  )}
                  fullHref={navigationHash({ ...route, full: true }, navigation)}
                />
              )
            }
          />
        </>
      );
    else if (route.view === 'team') content = <Team snapshot={snapshot} context={navigation} />;
    else if (route.view === 'task')
      content = (
        <TaskDetail
          key={route.taskId}
          snapshot={snapshot}
          context={navigation}
          taskId={route.taskId}
          section={route.section}
          onSection={(section) =>
            navigate({ ...route, section: section === 'information' ? undefined : section })
          }
          onReturn={returnToWork}
          returnHref={navigationHash(
            { view: 'work' },
            {
              ...(listOrigin.current?.context || navigation),
              selected: listOrigin.current?.taskId || selected,
            },
          )}
        />
      );
    else if (route.view === 'questions')
      content = (
        <QuestionList
          snapshot={snapshot}
          context={navigation}
          onContext={changeContext}
          filtersOpen={questionFiltersOpen}
          onFiltersOpen={setQuestionFiltersOpen}
        />
      );
    else if (route.view === 'evidence') {
      content = (
        <>
          <IconLink
            icon="back"
            label={i18n.t(
              selected && snapshotIndex(snapshot).tasks.has(selected)
                ? 'Back to task'
                : 'Back to work',
            )}
            href={navigationHash(
              selected && snapshotIndex(snapshot).tasks.has(selected)
                ? { view: 'task', taskId: selected }
                : { view: 'work' },
              navigation,
            )}
          />
          <h2>{i18n.t('Record unavailable')}</h2>
          <p className="empty">{i18n.t('Record details are not shown in this view.')}</p>
        </>
      );
    } else {
      const question = snapshotIndex(snapshot).questions.get(route.questionId || '');
      content = (
        <>
          <IconLink
            icon="back"
            label={i18n.t('Back to questions')}
            href={navigationHash({ view: 'questions' }, navigation)}
          />
          {question ? (
            <QuestionForm
              key={string(question.id)}
              question={question}
              programId={snapshot.program_id}
              context={navigation}
              session={session}
              saver={answerSaver}
              refresh={refresh}
            />
          ) : (
            <>
              <h2 className="not-found" tabIndex={-1}>
                {i18n.t('Question not available')}
              </h2>
              <p className="empty">
                {i18n.t('Return to Questions to inspect the current records.')}
              </p>
              <MissingQuestionDraft
                programId={snapshot.program_id}
                questionId={route.questionId || ''}
              />
            </>
          )}
        </>
      );
    }
    return content;
  }, [
    snapshot,
    matchingTasks,
    error,
    route,
    filters,
    selected,
    paging,
    focusRequest,
    changeFilters,
    applyPreset,
    returnToWork,
    openTask,
    navigate,
    focusHandled,
    revealTask,
    session,
    answerSaver,
    refresh,
    i18n,
    navigation,
    scope,
    preferences,
    wide,
    changeContext,
    changePreferences,
    selectCategory,
  ]);
  return (
    <PresentationProvider snapshot={snapshot}>
      <a
        className="skip"
        href="#"
        onClick={(event) => {
          event.preventDefault();
          main.current?.focus();
        }}
      >
        {i18n.t('Skip to content')}
      </a>
      <header className="topbar">
        <div className="brand">
          <span className="brand-title">{i18n.t('WORK PROGRAM')}</span>{' '}
          <span className="pill">
            {i18n.t('Local workspace ·')}{' '}
            {session?.responsesEnabled ? i18n.t('Responses enabled') : i18n.t('Read only')}
          </span>
        </div>
        <div className="refresh">
          <span className="refresh-status" role="status" aria-live={announce ? 'polite' : 'off'}>
            {freshness}
          </span>
          <label className="language-control">
            {i18n.t('Language')}
            <select
              value={i18n.locale}
              onChange={(event) => {
                const selectedLanguage = language(event.target.value);
                if (selectedLanguage) i18n.setPreference(selectedLanguage);
              }}
            >
              <option value="en">English</option>
              <option value="ko">한국어</option>
            </select>
          </label>
          <IconButton
            icon="refresh"
            label={i18n.t('Refresh')}
            disabled={busy && !snapshot}
            aria-disabled={busy}
            onClick={() => {
              if (!busy) void refresh(true);
            }}
          />
        </div>
      </header>
      {(error || stale) && (
        <div className="notice" role="status">
          {error
            ? i18n.t('Snapshot unavailable. {message}', {
                message: observed
                  ? i18n.t('Showing last observed records from {time}.', {
                      time: observed.toLocaleString(i18n.localeName),
                    })
                  : i18n.t('Check the configured program and refresh.'),
              })
            : i18n.t('Snapshot integrity is stale.')}
          {!error && items(integrity.warnings).length > 0 && (
            <details className="integrity-details">
              <summary>
                {i18n.t('Integrity warnings: {count}', { count: items(integrity.warnings).length })}
              </summary>
              <ul>
                {items(integrity.warnings).map((warning, index) => (
                  <li key={index}>{i18n.warning(warning)}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      <div className="shell">
        <aside className="rail" aria-label={i18n.t('Program navigation')}>
          <details className="rail-sections" open={wide}>
            <summary>{i18n.t('Program navigation')}</summary>
            <div className="rail-head">
              <p className="eyebrow">{i18n.t('Program')}</p>
              <h1>{snapshot?.program_id || i18n.t('Work program')}</h1>
            </div>
            <nav className="primary-nav" aria-label={i18n.t('Views')}>
              {(['overview', 'work', 'team', 'questions'] as const).map((view) => (
                <a
                  href={navigationHash({ view }, navigation)}
                  key={view}
                  aria-current={
                    route.view === view ||
                    (view === 'work' && route.view === 'task') ||
                    (view === 'questions' && route.view === 'question')
                      ? 'page'
                      : undefined
                  }
                >
                  {view === 'overview'
                    ? i18n.t('Overview')
                    : view === 'work'
                      ? i18n.t('Work')
                      : view === 'team'
                        ? i18n.t('Team')
                        : i18n.t('Questions') +
                          ' ' +
                          records(snapshot?.questions).filter(
                            (question) => question.status === 'open',
                          ).length}
                </a>
              ))}
            </nav>
            {snapshot &&
              (route.view === 'work' || (route.view === 'task' && wide && !route.full)) && (
                <details className="category-disclosure" open={wide}>
                  <summary>{i18n.t('Categories in this scope')}</summary>
                  <CategoryTree
                    snapshot={snapshot}
                    selected={filters.category}
                    onSelect={selectCategory}
                    context={navigation}
                  />
                </details>
              )}
            <details className="local-help">
              <summary>{i18n.t('About this local view')}</summary>
              <p>
                {i18n.t(
                  'Tasks are read only; use the program tool to change them. Enabled question answers can be saved here.',
                )}
              </p>
              <div className="rail-foot">
                <p>{i18n.t('Refresh every 5 seconds')}</p>
                <p>
                  {i18n.t('Snapshot:')} {i18n.date(snapshot?.generated_at)}
                </p>
                <p>
                  {i18n.t(
                    'Snapshot, fetch and personnel observation times are separate. Recorded ownership is not live presence.',
                  )}
                </p>
              </div>
            </details>
          </details>
          {snapshot && (
            <NavigationSearch snapshot={snapshot} context={navigation} navigate={navigate} />
          )}
        </aside>
        <main
          ref={main}
          className={`workspace ${route.view === 'work' || (route.view === 'task' && wide && !route.full) ? 'work-workspace' : ''}`}
          tabIndex={-1}
          aria-label={i18n.t('Program content')}
        >
          {navigation.issues.length > 0 && (
            <p className="notice" role="status">
              {i18n.t(
                'Some view settings were ignored because they are invalid or no longer recorded.',
              )}
            </p>
          )}
          {storageUnavailable && (
            <p className="notice" role="status">
              {i18n.t(
                'Browser storage is unavailable. View preferences are kept in memory for this visit.',
              )}
            </p>
          )}
          {content}
        </main>
      </div>
    </PresentationProvider>
  );
}
