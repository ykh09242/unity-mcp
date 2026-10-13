import { LanguageProvider, language, useI18n } from './i18n';
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { categoryTasks, items, ownerOf, record, records, safeUrl, string } from './model';
import type { Json, JsonRecord, Snapshot } from './model';
import { snapshotIndex } from './selectors';
import { useSnapshot } from './useSnapshot';
import type { SnapshotLoader } from './useSnapshot';
import { QuestionForm, useSession } from './questions';
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
import type { Navigation, Route, ViewPreferences, WorkContext } from './navigation';
import type { TaskFilters } from './selectors';
import { Breadcrumbs, NavigationSearch, RecordedAttention, ViewControls } from './navigation-ui';

type Filters = TaskFilters;
const statuses = ['planned', 'active', 'blocked', 'review', 'done'] as const;
export function App(props: {
  readonly loader?: SnapshotLoader;
  readonly sessionLoader?: SessionLoader;
  readonly answerSaver?: AnswerSaver;
}) {
  return (
    <LanguageProvider>
      <Dashboard {...props} />
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
  return (
    <dl className="fields">
      {values.map(([key, value]) => (
        <div key={key}>
          <dt>{i18n.t(key)}</dt>
          <dd>{i18n.display(value)}</dd>
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
          <li key={index}>{i18n.display(item)}</li>
        ))}
      </ul>
    ) : (
      <p className="muted">{i18n.t(fallback)}</p>
    );
  return <p className="prose">{i18n.display(value, fallback)}</p>;
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
function Link({ url, children }: { readonly url?: Json; readonly children: ReactNode }) {
  const i18n = useI18n();
  const href = safeUrl(url);
  return href ? (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ) : url ? (
    <span className="muted">
      {children}: {i18n.display(url)} ({i18n.t('link unavailable')})
    </span>
  ) : null;
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
  const done = snapshot.tasks.filter((task) => task.status === 'done').length;
  const delivery = snapshot.tasks.filter(
    (task) =>
      string(record(task.delivery).status) !== 'unrecorded' && string(record(task.delivery).status),
  ).length;
  return (
    <div className="metrics">
      <div className="metric">
        <p className="muted">{i18n.t('Reviewed implementation')}</p>
        <span className="metric-number">
          {done} <span className="metric-denominator">/ {snapshot.tasks.length}</span>
        </span>
        <progress
          max={Math.max(snapshot.tasks.length, 1)}
          value={done}
          aria-label={i18n.t('{done} of {total} leaf tasks reviewed complete', {
            done,
            total: snapshot.tasks.length,
          })}
        />
        <p className="muted">{i18n.t('Leaf tasks accepted after recorded review.')}</p>
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
        <Prose value={objective.criteria} fallback="No success criteria recorded" />
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
function Overview({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n();
  const index = snapshotIndex(snapshot);
  const categories = index.categoryChildren.get('') || [];
  const blockers = index.tasksByStatus.get('blocked') || [];
  const active = [
    ...(index.tasksByStatus.get('active') || []),
    ...(index.tasksByStatus.get('review') || []),
  ];
  const checkpoint = record(snapshot.checkpoint);
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{i18n.t('Program overview')}</p>
          <h2>{i18n.t('Where the work stands')}</h2>
        </div>
        <a className="button-link" href="#work">
          {i18n.t('Browse work')}
        </a>
      </div>
      <p className="intro">
        {i18n.t(
          'Start here for the goal and progress. Open Work for tasks, or Team for responsibility and handoffs.',
        )}
      </p>
      <Objective snapshot={snapshot} />
      <Progress snapshot={snapshot} />
      <div className="status-strip">
        {statuses.map((status) => (
          <div key={status}>
            <strong>{snapshot.tasks.filter((task) => task.status === status).length}</strong>
            <span>{i18n.statusLabel(status)}</span>
          </div>
        ))}
      </div>
      <div className="overview-grid">
        <Section title={i18n.t('Areas and responsibility')}>
          {categories.length ? (
            categories.map((category) => {
              const ids = categoryTasks(snapshot, string(category.id));
              const tasks = snapshot.tasks.filter((task) => ids.has(task.id));
              const done = tasks.filter((task) => task.status === 'done').length;
              const owners = [...new Set(tasks.map(ownerOf).filter(Boolean))];
              return (
                <div className="area-row" key={string(category.id)}>
                  <div>
                    <strong>{i18n.display(category.title)}</strong>
                    <p className="muted">
                      {i18n.t('Recorded owners:')}{' '}
                      {owners.join(', ') || i18n.t('No assignment yet')}
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
        <Section title={i18n.t('Needs attention')}>
          {blockers.length ? (
            blockers.map((task) => (
              <a
                className="attention-row"
                key={task.id}
                href={`#task/${encodeURIComponent(task.id)}`}
              >
                <Status value="blocked" />
                <strong>{task.title}</strong>
                <span>{i18n.display(task.next_action, 'No next action recorded')}</span>
              </a>
            ))
          ) : (
            <p className="muted">{i18n.t('No blocked tasks recorded.')}</p>
          )}
          <p className="summary-line">
            {active.length}{' '}
            {i18n.t(
              'tasks in progress or review. Saved statuses do not establish live agent activity.',
            )}
          </p>
        </Section>
      </div>
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
  const selectedRef = useRef<HTMLAnchorElement>(null);
  const sizeRef = useRef<HTMLSelectElement>(null);
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
    ['First page', 0],
    ['Previous page', page.index - 1],
    ['Next page', page.index + 1],
    ['Last page', page.count - 1],
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
              ...owners.map((owner) => [owner, owner] as const),
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
        {Object.entries(filters)
          .filter(([key, value]) => key !== 'sort' && Boolean(value))
          .map(([key, value]) => (
            <button type="button" key={key} onClick={() => onChange({ ...filters, [key]: '' })}>
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
              : {key === 'status' ? i18n.statusLabel(value) : value} ×
            </button>
          ))}
      </div>
      <div className="ledger-summary">
        <div className="list-heading">
          <button type="button" onClick={() => onChange(emptyFilters)}>
            {i18n.t('Clear filters')}
          </button>
        </div>
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
            {controls.map(([label, target]) => (
              <button
                key={label}
                type="button"
                aria-disabled={
                  !page.count || target < 0 || target >= page.count || target === page.index
                }
                onClick={() => {
                  if (page.count && target >= 0 && target < page.count && target !== page.index)
                    onPage({ ...paging, index: target });
                }}
              >
                {i18n.t(String(label))}
              </button>
            ))}
          </div>
          <p className="paging-range" role="status" aria-live="polite" aria-atomic="true">
            {i18n.t('Showing {start}–{end} of {matching} matching tasks · Page {page} of {pages}', {
              start: page.start,
              end: page.end,
              matching: page.matching,
              page: page.count ? page.index + 1 : 0,
              pages: page.count,
            })}
          </p>
        </nav>
        {selected && (
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
                      <> · {i18n.display(task.workstream)}</>
                    )}
                  </span>
                  <span className="task-next-action">
                    <span className="sr-only">{i18n.t('Next action')} · </span>
                    {i18n.display(task.next_action, 'No next action recorded')}
                  </span>
                </span>
                <Status value={task.status} />
                {preferences.columns.includes('owner') && (
                  <span className="task-owner">{ownerOf(task) || i18n.t('Unassigned')}</span>
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
function Delivery({ value }: { readonly value?: Json }) {
  const i18n = useI18n();
  const delivery = record(value);
  if (!delivery.status || delivery.status === 'unrecorded')
    return <p className="muted">{i18n.t('No delivery recorded.')}</p>;
  const metadata = record(delivery.metadata);
  return (
    <>
      <p className="muted">
        {i18n.t('Locally recorded results; this dashboard does not authenticate remote CI.')}
      </p>
      <Fields
        values={[
          ['Status', i18n.human(delivery.status)],
          ['Tested commit', metadata.tested_commit],
          ['Merge', i18n.human(metadata.merge_status)],
        ]}
      />
      <Link url={metadata.pr_url}>{i18n.t('Open recorded pull request')}</Link>
      {records(metadata.ci).map((check, index) => (
        <div className="history" key={index}>
          <p>
            {i18n.display(check.name)} · {i18n.human(check.conclusion)}
          </p>
          <Link url={check.url}>{i18n.t('Open recorded CI check')}</Link>
        </div>
      ))}
    </>
  );
}
function Handoff({ handoff }: { readonly handoff: JsonRecord }) {
  const i18n = useI18n();
  const outgoing = record(handoff.outgoing);
  const acknowledgement = record(handoff.acknowledgement);
  return (
    <article className="handoff">
      <div className="handoff-heading">
        <strong>
          {i18n.display(handoff.from_actor)} <span className="muted">{i18n.t('to')}</span>{' '}
          {i18n.display(handoff.to_actor)}
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
        <summary>{i18n.t('Constraints, findings and evidence')}</summary>
        <Prose value={outgoing.constraints} />
        <Prose value={outgoing.unresolved_findings} fallback="No unresolved findings recorded" />
        <pre className="source">{i18n.display(handoff)}</pre>
      </details>
      <p className="muted">
        {i18n.t('A pending handoff does not assign or activate the successor.')}
      </p>
    </article>
  );
}
function TaskRelations({
  snapshot,
  references,
  title,
  explanation,
}: {
  readonly snapshot: Snapshot;
  readonly references: Json | undefined;
  readonly title: string;
  readonly explanation: string;
}) {
  const i18n = useI18n();
  const ids = [...new Set(items(references).map(string).filter(Boolean))];
  return (
    <div className="task-relations">
      <h4>{i18n.t(title)}</h4>
      <p className="muted">{i18n.t(explanation)}</p>
      {ids.length ? (
        <ul>
          {ids.map((id) => {
            const peer = snapshotIndex(snapshot).tasks.get(id);
            const owner = peer && ownerOf(peer);
            return (
              <li key={id}>
                {peer ? (
                  <a href={`#task/${encodeURIComponent(id)}`}>
                    {id} · {peer.title}
                  </a>
                ) : (
                  <span>
                    {id} · {i18n.t('Task unavailable')}
                  </span>
                )}
                <p className="muted">
                  {owner
                    ? i18n.t('Current recorded owner / contact: {owner}', { owner })
                    : i18n.t('No owner or contact recorded')}
                </p>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted">{i18n.t('None recorded')}</p>
      )}
    </div>
  );
}
function TaskNotes({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  const notes = Array.isArray(value) ? value : value === null || value === undefined ? [] : [value];
  if (!notes.length) return <p className="muted">{i18n.t('No notes recorded')}</p>;
  return (
    <>
      <h4>{i18n.t('Task notes')}</h4>
      <ul className="task-notes" aria-label={i18n.t('Task notes')}>
        {notes.map((note, index) => {
          if (typeof note === 'string')
            return (
              <li key={index}>
                <Prose value={note} />
              </li>
            );
          const entry = record(note);
          const readable = ['actor', 'timestamp', 'reason', 'next_action'].some((key) =>
            string(entry[key]),
          );
          return (
            <li key={index}>
              {readable ? (
                <>
                  <p className="muted">
                    {i18n.date(entry.timestamp)} ·{' '}
                    {i18n.t('Recorded actor: {actor}', {
                      actor: i18n.display(string(entry.actor), 'Unknown'),
                    })}
                  </p>
                  <Prose value={string(entry.reason)} fallback="No readable reason recorded" />
                  <Fields
                    values={[['Next action', string(entry.next_action) || i18n.t('Not recorded')]]}
                  />
                </>
              ) : (
                <p className="muted">
                  {i18n.t('Unrecognized note format. Inspect the original record.')}
                </p>
              )}
              <details>
                <summary>{i18n.t('Original note record')}</summary>
                <pre className="source">{JSON.stringify(note, null, 2)}</pre>
              </details>
            </li>
          );
        })}
      </ul>
    </>
  );
}
function TaskDetail({
  snapshot,
  taskId,
  returnHref = '#work',
  fullHref,
}: {
  readonly snapshot: Snapshot;
  readonly taskId?: string;
  readonly returnHref?: string;
  readonly fullHref?: string;
}) {
  const i18n = useI18n();
  const task = snapshotIndex(snapshot).tasks.get(taskId || '');
  if (!task)
    return (
      <>
        <a href={returnHref}>{i18n.t('Back to work')}</a>
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
  const actor = snapshotIndex(snapshot).actors.get(ownerOf(task)) || {};
  const attempts = records(task.attempts);
  const attempt = attempts.at(-1) || {};
  const assignment = record(attempt.assignment);
  const source = record(task.source);
  const expectedResult =
    /^- Goal and expected result: (.+)$/m.exec(string(source.text))?.[1] || task.planning_note;
  const handoffs = records(snapshot.handoffs).filter(
    (item) => !item.task_id || item.task_id === task.id,
  );
  return (
    <>
      <nav className="detail-actions" aria-label={i18n.t('Task navigation')}>
        <a className="back-link" href={returnHref}>
          {i18n.t('Back to work')}
        </a>
        {fullHref && (
          <a className="full-detail-link" href={fullHref} aria-label={i18n.t('Open full detail')}>
            {i18n.t('Full screen')}
          </a>
        )}
        {fullHref && (
          <a className="close-detail" href={returnHref} aria-label={i18n.t('Close detail')}>
            <span aria-hidden="true">×</span>
          </a>
        )}
      </nav>
      <div className="page-heading detail-heading">
        <div>
          <p className="eyebrow mono">{task.id}</p>
          <h2 tabIndex={-1}>{task.title}</h2>
        </div>
        <Status value={task.status} />
      </div>
      <div className="task-detail-grid">
        <div>
          <Section title={i18n.t('Next action')}>
            <Fields
              values={[
                ['Owner', ownerOf(task) || i18n.t('Unassigned')],
                ['Next action', task.next_action],
              ]}
            />
            <div className="expected-result-summary">
              <h3>{i18n.t('Expected result')}</h3>
              <Prose
                value={expectedResult}
                fallback="Read the full source context below for task purpose and method."
              />
            </div>
            <details className="expected-result">
              <summary>{i18n.t('Planning and metadata')}</summary>
              <Prose
                value={task.planning_note}
                fallback="Read the full source context below for task purpose and method."
              />
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
          <Section title={i18n.t('Requirements and source context')}>
            <details>
              <summary>{i18n.t('Full original source and evidence')}</summary>
              <Prose value={task.requirements || task.acceptance || source.text} />
              <pre className="source">{i18n.display(source)}</pre>
            </details>
          </Section>
          <Section title={i18n.t('Dependencies and verification')}>
            <details>
              <summary>{i18n.t('Dependencies and verification')}</summary>
              <TaskRelations
                snapshot={snapshot}
                references={task.depends_on}
                title="Hard dependencies"
                explanation="Required before this task can proceed."
              />
              <TaskRelations
                snapshot={snapshot}
                references={task.related_to}
                title="Related tasks · advisory"
                explanation="Coordinate with these owners; this relation does not block the task."
              />
              <Prose value={task.required_checks} fallback="No required checks recorded" />
            </details>
          </Section>
          <Section title={i18n.t('Attempt and review history')}>
            <details>
              <summary>{i18n.t('Attempt and review history')}</summary>
              {attempts.length ? (
                attempts.map((item, index) => (
                  <details key={i18n.display(item.number, String(index))}>
                    <summary>
                      {i18n.t('Attempt')} {i18n.display(item.number)} · {i18n.display(item.owner)} ·{' '}
                      {i18n.human(item.status || item.outcome)}
                    </summary>
                    <pre className="source">{i18n.display(item)}</pre>
                  </details>
                ))
              ) : (
                <p className="muted">{i18n.t('No attempts recorded.')}</p>
              )}
              <TaskNotes value={task.notes} />
            </details>
          </Section>
        </div>
        <div className="task-context">
          <Section title={i18n.t('Recorded assignment')}>
            <details>
              <summary>{i18n.t('Recorded assignment')}</summary>
              <Fields
                values={[
                  ['Owner', ownerOf(task) || i18n.t('Unassigned')],
                  ['Role', i18n.human(assignment.role)],
                  ['Model', assignment.model_id],
                  ['Reasoning', i18n.human(assignment.reasoning_effort)],
                  ['Provenance', i18n.human(assignment.provenance)],
                  ['Staffing', i18n.human(actor.staffing_status)],
                  [
                    'Last observed',
                    `${i18n.human(actor.observed_status)} · ${i18n.date(actor.observed_at)}`,
                  ],
                ]}
              />
              <p className="muted">
                {i18n.t(
                  'Saved model settings are not authenticated runtime settings. Owner names do not prove agent liveness.',
                )}
              </p>
            </details>
          </Section>
          <Section title={i18n.t('Recorded PR / CI')}>
            <details>
              <summary>{i18n.t('Recorded PR / CI')}</summary>
              <Delivery value={task.delivery} />
            </details>
          </Section>
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
        </div>
      </div>
    </>
  );
}
function Team({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n();
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
              <h3>{id}</h3>
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
                ['Model', actor.model_id],
                ['Reasoning', i18n.human(actor.reasoning_effort)],
                ['Provenance', `${i18n.human(actor.provenance)} · ${i18n.t('saved')}`],
                [
                  'Last observed',
                  `${i18n.human(actor.observed_status)} · ${i18n.date(actor.observed_at)}`,
                ],
              ]}
            />
            <p className="muted">
              {i18n.t('Current recorded assignments:')}{' '}
              {assignments.map((task) => task.id).join(', ') || i18n.t('None')}
            </p>
            <details>
              <summary>{i18n.t('Staffing and assignment history')}</summary>
              <pre className="source">
                {i18n.display({
                  history: actor.history || [],
                  assignments: actor.assignments || [],
                })}
              </pre>
            </details>
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
          <p className="eyebrow">{i18n.t('Responsibility and continuity')}</p>
          <h2>{i18n.t('Team')}</h2>
        </div>
        <span className="muted">
          {actors.length} {i18n.t('saved personnel records')}
        </span>
      </div>
      <p className="intro">
        {i18n.t(
          'Management relationships and assignments are recorded by the director. This page does not poll live agent sessions.',
        )}
      </p>
      <p className="muted">
        {i18n.t('Program communication language')}:{' '}
        {snapshot.communication_language === 'ko'
          ? '한국어'
          : snapshot.communication_language === 'en'
            ? 'English'
            : i18n.t('Not recorded')}
        {' · '}
        {i18n.t(
          'Program communication language applies to director-authored questions; original content is not translated.',
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
          <pre className="source">{i18n.display(director)}</pre>
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
    </>
  );
}

function Questions({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n();
  const questions = [...snapshotIndex(snapshot).questions.values()];
  if (!questions.length) return null;
  const pending = questions.filter((question) => question.status === 'open');
  const current = questions.filter((question) => question.status !== 'resolved');
  const past = questions.filter((question) => question.status === 'resolved');
  const row = (question: JsonRecord) => (
    <a
      className="question-row"
      key={string(question.id)}
      href={`#question/${encodeURIComponent(string(question.id))}`}
    >
      <strong>{i18n.display(question.prompt)}</strong>
      <span className="muted">
        {question.status === 'open'
          ? i18n.t('Needs your answer')
          : question.status === 'answered'
            ? i18n.t('Saved · awaiting acknowledgement')
            : question.status === 'acknowledged'
              ? i18n.t('Acknowledged · awaiting application')
              : record(question.outcome).kind === 'withdrawn'
                ? i18n.t('Withdrawn')
                : i18n.t('Applied')}
      </span>
    </a>
  );
  return (
    <section className="question-inbox">
      <div className="question-inbox-heading">
        <h3>{i18n.t('Director questions')}</h3>
        <span className="badge">
          {pending.length} {i18n.t('awaiting your answer')}
        </span>
      </div>
      {current.slice(0, 3).map(row)}
      {current.length > 3 && (
        <details>
          <summary>
            {i18n.t('More current questions')} ({i18n.number(current.length - 3)})
          </summary>
          {current.slice(3).map(row)}
        </details>
      )}
      {past.length > 0 && (
        <details>
          <summary>
            {i18n.t('Question history')} ({i18n.number(past.length)})
          </summary>
          {past.map(row)}
        </details>
      )}
    </section>
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
  const { snapshot, observed, error, busy, refresh } = useSnapshot(loader);
  const programLanguage = string(snapshot?.communication_language);
  useEffect(() => {
    i18n.setProgramLanguage(language(programLanguage));
  }, [programLanguage, i18n.setProgramLanguage]);
  const session = useSession(sessionLoader);
  const [navigation, setNavigation] = useState<Navigation>(() =>
    parseNavigation(window.location.hash),
  );
  const { route, filters, selected, paging, scope } = navigation;
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
  const previousProgram = useRef<string | null>(null);
  const main = useRef<HTMLElement>(null);
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
      if (next.route.view === 'work' && previous.route.view === 'task') {
        const origin = listOrigin.current;
        const taskId = origin?.taskId || next.selected || previous.route.taskId || null;
        if (snapshot && taskId && origin) {
          const rank = taskPageIndex(
            selectWorkTasks(snapshot, origin.context),
            taskId,
            origin.context.paging.size,
          );
          next = {
            ...next,
            ...origin.context,
            route: next.route,
            selected: taskId,
            paging: { ...origin.context.paging, index: rank ?? origin.context.paging.index },
          };
        }
        setFocusRequest({ token: ++focusToken.current, taskId });
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
      commit({ ...defaultContext, route: { view: 'overview' }, issues: [], explicit: false }, true);
      setFocusRequest(null);
      listOrigin.current = null;
    } else {
      const explicit = parseNavigation(window.location.hash, snapshot);
      const preset = saved.preferences.presets.find(
        (value) => value.name === saved.preferences.selectedPreset,
      );
      const context =
        !explicit.explicit && preset
          ? {
              ...parseNavigation(preset.hash, snapshot),
              route: explicit.route,
              selected: explicit.route.taskId || explicit.selected,
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
          : main.current;
      target?.focus({ preventScroll: true });
      if (route.view === 'task') {
        const pane = main.current?.querySelector('.detail-pane');
        if (pane) pane.scrollTop = 0;
        else main.current?.scrollIntoView?.({ block: 'start' });
      }
    }
    initialized.current = true;
  }, [route.view, route.taskId, route.questionId, selected, Boolean(snapshot)]);
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
    setFocusRequest((previous) => (previous?.token === token ? null : previous));
  }, []);
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
  useEffect(() => {
    if (!focusRequest || route.view !== 'work') return;
    const origin = listOrigin.current;
    if (!origin) return;
    const frame = requestAnimationFrame(() => {
      const ledger = main.current?.querySelector('.task-list');
      if (ledger) ledger.scrollTop = origin.ledgerScroll;
      if (main.current) main.current.scrollTop = origin.mainScroll;
      if (window.scrollY !== origin.documentScroll) window.scrollTo({ top: origin.documentScroll });
      listOrigin.current = null;
    });
    return () => cancelAnimationFrame(frame);
  }, [route.view, focusRequest]);
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
      content = (
        <>
          <Questions snapshot={snapshot} />
          <RecordedAttention snapshot={snapshot} />
          <Overview snapshot={snapshot} />
        </>
      );
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
                  snapshot={snapshot}
                  taskId={route.taskId}
                  returnHref={navigationHash(
                    { view: 'work' },
                    { ...navigation, selected: listOrigin.current?.taskId || selected },
                  )}
                  fullHref={navigationHash({ ...route, full: true }, navigation)}
                />
              )
            }
          />
        </>
      );
    else if (route.view === 'team') content = <Team snapshot={snapshot} />;
    else if (route.view === 'task')
      content = (
        <TaskDetail
          snapshot={snapshot}
          taskId={route.taskId}
          returnHref={navigationHash(
            { view: 'work' },
            { ...navigation, selected: listOrigin.current?.taskId || selected },
          )}
        />
      );
    else if (route.view === 'questions') content = <Questions snapshot={snapshot} />;
    else if (route.view === 'evidence') {
      const references = snapshotIndex(snapshot).evidenceByPath.get(route.evidencePath || '');
      content = (
        <>
          <a href={navigationHash({ view: 'work' }, navigation)}>{i18n.t('Back to work')}</a>
          <h2>{i18n.t('Evidence record')}</h2>
          <p className="mono">{route.evidencePath}</p>
          {references?.length ? (
            <>
              <p>
                {i18n.t(
                  'Supplied evidence references. Files are not opened or verified by this view.',
                )}
              </p>
              {references.map((reference, index) => (
                <pre className="source" key={index}>
                  {i18n.display(reference)}
                </pre>
              ))}
            </>
          ) : (
            <p className="empty">{i18n.t('Record unavailable in this snapshot')}</p>
          )}
        </>
      );
    } else {
      const question = snapshotIndex(snapshot).questions.get(route.questionId || '');
      content = (
        <>
          <a className="back-link" href="#overview">
            {i18n.t('Back to overview')}
          </a>
          {question ? (
            <QuestionForm
              key={string(question.id)}
              question={question}
              session={session}
              saver={answerSaver}
              refresh={refresh}
            />
          ) : (
            <>
              <h2>{i18n.t('Question not available')}</h2>
              <p className="empty">
                {i18n.t('Return to Overview to inspect current director questions.')}
              </p>
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
    openTask,
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
    <>
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
          <span role="status">{freshness}</span>
          <button type="button" disabled={busy} onClick={() => void refresh()}>
            {i18n.t('Refresh')}
          </button>
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
              <div className="rail-foot">
                <p>{i18n.t('Task views are read only')}</p>
                <p>{i18n.t('Refresh every 5 seconds')}</p>
                <p>
                  {i18n.t('Snapshot:')} {i18n.date(snapshot?.generated_at)}
                </p>
                <p>{i18n.t('Fetch time and personnel observation time are separate.')}</p>
              </div>
              <p>
                {i18n.t(
                  'Recorded local work. Task changes are managed through the program tool. Question answers can be saved here when enabled.',
                )}
              </p>
              <p>
                {i18n.t(
                  'Start with the goal, choose a work scope, then inspect tasks and original evidence. Recorded ownership is not live agent presence.',
                )}
              </p>
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
    </>
  );
}
