import { flowReasonLabels } from './flow-copy';
import { checkLabels } from './throughput';
import { useEffect, useRef } from 'react';
import { useI18n } from './i18n';
import { usePresentation } from './presentation-ui';
import { reviewQueue, queueRowsForTasks, queueKindLabels, queueActionLabels } from './flow-queue';
import type { QueueRow } from './flow-queue';
import type { Snapshot, Task } from './model';
import type { TaskFilters } from './selectors';
import type { WorkFocusRequest, WorkPage } from './paging';
import { pageSizes, pageSize } from './paging';
import { IconButton } from './icons';
import { navigationHash, emptyFilters, scopedTasks } from './navigation';
import type { WorkContext } from './navigation';

const laneLabels: Readonly<Record<string, string>> = {
  code_review: 'Code review',
  review: 'Code review',
  qa: 'QA',
  visual_qa: 'Visual QA',
  visual: 'Visual QA',
  ci: 'CI',
  pr_ci: 'PR CI',
  beta_ci: 'Beta CI',
};
function ActorLink({ id, context }: { readonly id: string; readonly context: WorkContext }) {
  const i18n = useI18n(),
    presentation = usePresentation();
  return id ? (
    <a
      href={navigationHash(
        { view: 'work' },
        {
          ...context,
          selected: null,
          paging: { ...context.paging, index: 0 },
          filters: {
            ...emptyFilters,
            coordination: 'review_queue',
            queue_readiness: 'open',
            owner: id,
          },
        },
      )}
    >
      {presentation.actor(id)}
    </a>
  ) : (
    <span className="muted">{i18n.t('Unassigned')}</span>
  );
}
function unassignedCheckGap(row: QueueRow): boolean {
  return (
    row.kind === 'required_check_gap' &&
    row.readiness === 'not_assessed' &&
    !row.verifierId &&
    row.reasons.length === 1 &&
    row.reasons[0] === 'assignment_unrecorded'
  );
}
function ReviewLabel({ row }: { readonly row: QueueRow }) {
  const i18n = useI18n();
  return (
    <>
      {i18n.t(queueKindLabels[row.kind] || row.kind)}
      {row.lane && <> · {i18n.t(laneLabels[row.lane] || row.lane)}</>}
      {row.kind === 'required_check_gap' && row.checkIds.length > 0 && (
        <> · {row.checkIds.map((id) => i18n.t(checkLabels[id] || id)).join(', ')}</>
      )}
    </>
  );
}
function QueueFacts({ row, context }: { readonly row: QueueRow; readonly context: WorkContext }) {
  const i18n = useI18n(),
    unassignedGap = unassignedCheckGap(row);
  return (
    <>
      <div className="queue-action">
        <ActorLink id={row.nextActorId} context={context} />
        <span>{i18n.t(row.nextAction || queueActionLabels[row.action] || 'Not recorded')}</span>
      </div>
      {row.nextTrigger && row.nextTrigger !== row.nextAction && (
        <p className="muted queue-trigger">
          {i18n.t('Next trigger')}: {i18n.t(row.nextTrigger)}
        </p>
      )}
      {row.reasons[0] && !unassignedGap && (
        <p className="lane-warning queue-reason">
          {i18n.warning(flowReasonLabels[row.reasons[0]] || row.reasons[0])}
        </p>
      )}
      <details className="lane-details queue-details">
        <summary>{i18n.t('Review details')}</summary>
        <dl className="flow-facts">
          {[
            ['Verifier', row.verifierId],
            ['Resolver', row.resolverId],
            ['Owner', row.ownerId],
          ]
            .filter(([, id]) => Boolean(id))
            .map(([label, id]) => (
              <div key={label}>
                <dt>{i18n.t(label || '')}</dt>
                <dd>
                  <ActorLink id={id || ''} context={context} />
                </dd>
              </div>
            ))}
          {row.phase && (
            <div>
              <dt>{i18n.t('Phase')}</dt>
              <dd>{i18n.human(row.phase)}</dd>
            </div>
          )}
          {row.result && (
            <div>
              <dt>{i18n.t('Reported outcome')}</dt>
              <dd>
                {unassignedGap && row.result === 'unknown'
                  ? i18n.t('No verification result recorded')
                  : i18n.human(row.result)}
              </dd>
            </div>
          )}
          {row.applicability && (
            <div>
              <dt>{i18n.t('Applicability')}</dt>
              <dd>
                {unassignedGap && row.applicability === 'unknown'
                  ? i18n.t('Verification inputs not assessed')
                  : i18n.human(row.applicability)}
              </dd>
            </div>
          )}
        </dl>
        {row.reasons.length > (unassignedGap ? 0 : 1) && (
          <ul>
            {row.reasons.slice(unassignedGap ? 0 : 1).map((reason, index) => (
              <li key={index}>{i18n.warning(flowReasonLabels[reason] || reason)}</li>
            ))}
          </ul>
        )}
      </details>
    </>
  );
}
export function ReviewQueueList({
  snapshot,
  tasks,
  filters,
  navigation,
}: {
  readonly snapshot: Snapshot;
  readonly tasks: readonly Task[];
  readonly filters: TaskFilters;
  readonly navigation: {
    readonly context: WorkContext;
    readonly paging: WorkPage;
    readonly selected: string | null;
    readonly focusRequest: WorkFocusRequest | null;
    readonly onPage: (page: WorkPage) => void;
    readonly onFocusHandled: (token: number) => void;
    readonly onOpenTask: (id: string) => void;
    readonly taskHref?: (id: string) => string;
  };
}) {
  const i18n = useI18n(),
    presentation = usePresentation();
  const queue = reviewQueue(snapshot);
  const rows = queueRowsForTasks(snapshot, filters, new Set(tasks.map((task) => task.id)));
  const count = Math.ceil(rows.length / navigation.paging.size);
  const pageIndex = Math.max(0, Math.min(navigation.paging.index, Math.max(0, count - 1)));
  const offset = pageIndex * navigation.paging.size;
  const shown = rows.slice(offset, offset + navigation.paging.size);
  const regionRef = useRef<HTMLDivElement>(null),
    sizeRef = useRef<HTMLSelectElement>(null);
  const focusedNode = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const request = navigation.focusRequest;
    const position = request?.taskId ? rows.findIndex((row) => row.taskId === request.taskId) : -1;
    const targetPage = position >= 0 ? Math.floor(position / navigation.paging.size) : pageIndex;
    if (targetPage !== navigation.paging.index) {
      navigation.onPage({ ...navigation.paging, index: targetPage });
      return;
    }
    if (!request) {
      if (
        focusedNode.current &&
        !focusedNode.current.isConnected &&
        document.activeElement === document.body
      ) {
        focusedNode.current = null;
        regionRef.current?.focus({ preventScroll: true });
      }
      return;
    }
    const target = Array.from(
      regionRef.current?.querySelectorAll<HTMLAnchorElement>('[data-task-id]') || [],
    ).find((element) => element.dataset.taskId === request.taskId);
    (target || sizeRef.current)?.focus({ preventScroll: true });
    navigation.onFocusHandled(request.token);
  }, [rows, navigation, pageIndex]);
  if (!queue)
    return (
      <p className="empty">
        {i18n.t(
          snapshot.review_queue === undefined
            ? 'No review queue recorded.'
            : 'Review queue is unavailable; current work remains inspectable.',
        )}
      </p>
    );
  const controls = [
    ['First page', 0, 'first'],
    ['Previous page', pageIndex - 1, 'previous'],
    ['Next page', pageIndex + 1, 'next'],
    ['Last page', count - 1, 'last'],
  ] as const;
  return (
    <section className="review-queue" aria-label={i18n.t('Review queue')}>
      <div className="ledger-summary">
        <nav className="paging" aria-label={i18n.t('Review queue pages')}>
          <label>
            {i18n.t('Rows per page')}
            <select
              ref={sizeRef}
              value={navigation.paging.size}
              onChange={(event) =>
                navigation.onPage({ index: 0, size: pageSize(event.target.value) })
              }
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
                aria-disabled={!count || target < 0 || target >= count || target === pageIndex}
                onClick={() => {
                  if (target >= 0 && target < count && target !== pageIndex)
                    navigation.onPage({ ...navigation.paging, index: target });
                }}
              />
            ))}
          </div>
          <p className="paging-range">
            {i18n.t('{start}–{end} / {matching} · {page}/{pages}', {
              start: rows.length ? offset + 1 : 0,
              end: Math.min(offset + shown.length, rows.length),
              matching: rows.length,
              page: count ? pageIndex + 1 : 0,
              pages: count,
            })}
          </p>
        </nav>
        <p className="muted">
          {i18n.t('{count} recorded obligations', { count: queue.total })}
          {queue.omitted > 0 && (
            <>
              {' '}
              · {i18n.t('{count} omitted; filtered results are partial.', { count: queue.omitted })}
            </>
          )}
        </p>
      </div>
      <div className="queue-labels" aria-hidden="true">
        <span>{i18n.t('Task / review')}</span>
        <span>{i18n.t('Readiness')}</span>
        <span>{i18n.t('Next responsibility')}</span>
      </div>
      <div
        className="queue-rows"
        ref={regionRef}
        onFocusCapture={(event) => {
          if (event.target instanceof HTMLElement) focusedNode.current = event.target;
        }}
        role="region"
        aria-label={i18n.t('Recorded review obligations')}
        tabIndex={0}
      >
        {shown.map((row) => (
          <article
            className={'queue-row' + (navigation.selected === row.taskId ? ' selected' : '')}
            key={row.id}
          >
            <div className="queue-identity">
              <a
                className="task-button queue-task"
                data-task-id={row.taskId}
                href={
                  navigation.taskHref?.(row.taskId) ||
                  navigationHash({ view: 'task', taskId: row.taskId }, navigation.context)
                }
                onClick={(event) => {
                  if (!event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
                    event.preventDefault();
                    navigation.onOpenTask(row.taskId);
                  }
                }}
              >
                <span className="mono">{row.taskId}</span> {presentation.task(row.taskId)}
              </a>
              <p className="muted">
                <ReviewLabel row={row} />
              </p>
            </div>
            <div className="queue-state">
              <span className="badge">
                {unassignedCheckGap(row)
                  ? i18n.t('Awaiting verifier assignment')
                  : i18n.human(row.readiness)}
              </span>
              {row.applicability &&
                row.applicability !== 'current' &&
                !(unassignedCheckGap(row) && row.applicability === 'unknown') && (
                  <span className="lane-warning">{i18n.human(row.applicability)}</span>
                )}
            </div>
            <div className="queue-responsibility">
              <QueueFacts row={row} context={navigation.context} />
            </div>
          </article>
        ))}
        {!shown.length && (
          <p className="empty">
            {i18n.t(
              queue.omitted
                ? 'No supplied obligations match; omitted work remains unknown.'
                : queue.total
                  ? 'No review obligations match these filters.'
                  : 'No review obligations recorded.',
            )}
          </p>
        )}
      </div>
    </section>
  );
}
export function QueueResponsibilities({
  snapshot,
  context,
}: {
  readonly snapshot: Snapshot;
  readonly context: WorkContext;
}) {
  const i18n = useI18n(),
    presentation = usePresentation(),
    queue = reviewQueue(snapshot);
  if (snapshot.review_queue === undefined) return null;
  if (!queue)
    return (
      <p className="muted">
        {i18n.t('Review queue is unavailable; current work remains inspectable.')}
      </p>
    );
  const taskIds = new Set(scopedTasks(snapshot, context.scope).map((task) => task.id));
  const rows = queue.rows.filter((row) => taskIds.has(row.taskId) && row.readiness !== 'terminal');
  return (
    <section className="verification-responsibilities">
      <h3>{i18n.t('Next review responsibility')}</h3>
      <ul>
        {rows.slice(0, 5).map((row) => (
          <li key={row.id}>
            <a href={navigationHash({ view: 'task', taskId: row.taskId }, context)}>
              {presentation.taskReference(row.taskId)}
            </a>
            <div className="queue-action">
              <span>
                <ReviewLabel row={row} />
              </span>
              <ActorLink id={row.nextActorId} context={context} />
              <span>
                {i18n.t(row.nextAction || queueActionLabels[row.action] || 'Not recorded')}
              </span>
            </div>
            {row.reasons[0] && (
              <span className="lane-warning">
                {i18n.warning(flowReasonLabels[row.reasons[0]] || row.reasons[0])}
              </span>
            )}
          </li>
        ))}
      </ul>
      {!rows.length && (
        <p className="muted">
          {i18n.t(
            queue.omitted
              ? 'No supplied obligations match; omitted work remains unknown.'
              : 'No open review obligations recorded.',
          )}
        </p>
      )}
      {(rows.length > 5 || queue.omitted > 0) && (
        <p className="muted">
          {i18n.t(
            'Showing {shown} of {supplied} supplied obligations · {omitted} omitted from snapshot.',
            { shown: Math.min(rows.length, 5), supplied: rows.length, omitted: queue.omitted },
          )}
        </p>
      )}
      <a
        href={navigationHash(
          { view: 'work' },
          {
            ...context,
            selected: null,
            filters: { ...emptyFilters, coordination: 'review_queue', queue_readiness: 'open' },
            paging: { ...context.paging, index: 0 },
          },
        )}
      >
        {i18n.t('Open review queue')}
      </a>
    </section>
  );
}
export function QueueAttention({
  snapshot,
  context,
}: {
  readonly snapshot: Snapshot;
  readonly context: WorkContext;
}) {
  const i18n = useI18n(),
    queue = reviewQueue(snapshot);
  if (snapshot.review_queue === undefined) return null;
  return (
    <nav className="coordination-summary" aria-label={i18n.t('Review queue')}>
      <a
        href={navigationHash(
          { view: 'work' },
          {
            ...context,
            selected: null,
            filters: { ...emptyFilters, coordination: 'review_queue', queue_readiness: 'open' },
            paging: { ...context.paging, index: 0 },
          },
        )}
      >
        {i18n.t('Review queue')} ·{' '}
        {queue
          ? i18n.t('{count} open review items', {
              count: queue.total - (queue.counts.readiness?.terminal || 0),
            })
          : i18n.t('Unavailable')}
      </a>
    </nav>
  );
}
