import { useI18n } from './i18n';
import { ownerOf } from './model';
import type { Snapshot, Task } from './model';
import { emptyFilters, goalTaskIds, navigationHash, scopedTasks } from './navigation';
import type { WorkContext } from './navigation';
import { taskNeighborhood, workload, workloadHref } from './relate';
import { snapshotIndex } from './selectors';

export function Workload({
  snapshot,
  context,
}: {
  readonly snapshot: Snapshot;
  readonly context: WorkContext;
}) {
  const i18n = useI18n();
  const rows = workload(snapshot, context.scope);
  const actionableCount = (row: (typeof rows)[number]) =>
    row.planned + row.active + row.blocked + row.review;
  const assigned = rows.filter((row) => actionableCount(row) > 0 || !row.owner);
  const withoutAssignments = rows.filter((row) => actionableCount(row) === 0 && row.owner);
  const completed = rows.filter((row) => row.done > 0);
  const index = snapshotIndex(snapshot);
  return (
    <section className="allocation" aria-labelledby="allocation-title">
      <div className="allocation-heading">
        <h3 id="allocation-title">{i18n.t('Assignments')}</h3>
        <nav className="scope-switch" aria-label={i18n.t('Assignment scope')}>
          {(['current', 'all'] as const).map((scope) => (
            <a
              key={scope}
              aria-current={scope === context.scope ? 'page' : undefined}
              href={navigationHash(
                { view: 'team' },
                {
                  ...context,
                  scope,
                  filters: emptyFilters,
                  selected: null,
                  paging: { ...context.paging, index: 0 },
                },
              )}
            >
              {i18n.t(scope === 'current' ? 'Current goal' : 'All work')}
            </a>
          ))}
        </nav>
      </div>
      <p className="muted">
        {i18n.t('{count} tasks', { count: scopedTasks(snapshot, context.scope).length })} ·{' '}
        {i18n.t('Current assignments from saved task records')}
      </p>
      {context.scope === 'current' && goalTaskIds(snapshot) === null ? (
        <p className="empty">
          {i18n.t(
            'Current goal membership is not recorded. Choose All work to inspect the preserved backlog.',
          )}
        </p>
      ) : (
        <div
          className="allocation-table"
          role="region"
          aria-label={i18n.t('Assignments')}
          tabIndex={0}
        >
          <table>
            <thead>
              <tr>
                <th scope="col">{i18n.t('Owner')}</th>
                <th scope="col">{i18n.t('Planned')}</th>
                <th scope="col">{i18n.t('In progress')}</th>
                <th scope="col">{i18n.t('Blocked')}</th>
                <th scope="col">{i18n.t('In review')}</th>
              </tr>
            </thead>
            <tbody>
              {assigned.map((row) => (
                <tr key={row.owner}>
                  <th scope="row">
                    {row.owner ? (
                      <a
                        href={workloadHref(context.scope, row.owner)}
                        aria-label={i18n.t('All {count} records for {owner}', {
                          count: row.total,
                          owner: row.owner || i18n.t('Unassigned'),
                        })}
                      >
                        {row.owner}
                      </a>
                    ) : (
                      i18n.t('Unassigned')
                    )}
                    {row.owner && !index.actors.has(row.owner) && (
                      <span className="muted allocation-note">{i18n.t('No personnel record')}</span>
                    )}
                  </th>
                  {(['planned', 'active', 'blocked', 'review'] as const).map((status) => (
                    <td key={status}>
                      <a
                        href={workloadHref(context.scope, row.owner, status)}
                        aria-label={i18n.t('{owner} · {status}: {count}', {
                          owner: row.owner || i18n.t('Unassigned'),
                          status: i18n.statusLabel(status),
                          count: row[status],
                        })}
                      >
                        {i18n.number(row[status])}
                      </a>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {completed.length > 0 && (
        <details className="completed-assignments">
          <summary>
            {i18n.t('Completed records: {count}', {
              count: completed.reduce((sum, row) => sum + row.done, 0),
            })}
          </summary>
          <ul>
            {completed.map((row) => (
              <li key={row.owner}>
                <a href={workloadHref(context.scope, row.owner, 'done')}>
                  {i18n.t('{owner} · {count} complete', {
                    owner: row.owner || i18n.t('No current owner'),
                    count: row.done,
                  })}
                </a>
              </li>
            ))}
          </ul>
        </details>
      )}
      {(context.scope === 'all' || goalTaskIds(snapshot) !== null) &&
        withoutAssignments.length > 0 && (
          <details className="unassigned-personnel">
            <summary>
              {i18n.t('Personnel without open assignments: {count}', {
                count: withoutAssignments.length,
              })}
            </summary>
            <ul>
              {withoutAssignments.map((row) => (
                <li key={row.owner}>
                  <a href={workloadHref(context.scope, row.owner)}>{row.owner}</a>
                </li>
              ))}
            </ul>
          </details>
        )}
    </section>
  );
}

export function DependencyNeighborhood({
  snapshot,
  task,
}: {
  readonly snapshot: Snapshot;
  readonly task: Task;
}) {
  const i18n = useI18n();
  const neighborhood = taskNeighborhood(snapshot, task);
  const groups = [
    ['upstream', 'Depends on'],
    ['downstream', 'Required by'],
    ['related', 'Related tasks · advisory'],
  ] as const;
  return (
    <section className="dependency-neighborhood" aria-label={i18n.t('Task relationships')}>
      <h3>{i18n.t('Task relationships')}</h3>
      {groups.map(([key, label]) => (
        <details key={key} open={key !== 'related' || undefined}>
          <summary>
            {i18n.t(label)} <span className="muted">{neighborhood[key].length}</span>
          </summary>
          {neighborhood[key].length ? (
            <ul>
              {neighborhood[key].map(({ id, task: peer }) => (
                <li key={id}>
                  {peer ? (
                    <>
                      <a className="neighbor-task" href={`#task/${encodeURIComponent(id)}`}>
                        <span className="mono">{id}</span> {peer.title}
                      </a>
                      <div className="neighbor-meta">
                        <span className={`badge status-${peer.status}`}>
                          {i18n.statusLabel(peer.status)}
                        </span>
                        <a
                          href={workloadHref('all', ownerOf(peer))}
                          aria-label={i18n.t('Assignments for {owner}', {
                            owner: ownerOf(peer) || i18n.t('Unassigned'),
                          })}
                        >
                          {ownerOf(peer) || i18n.t('Unassigned')}
                        </a>
                      </div>
                    </>
                  ) : (
                    <span>
                      {id} · {i18n.t('Task unavailable')}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{i18n.t('None recorded')}</p>
          )}
        </details>
      ))}
      <details className="relationship-help">
        <summary>{i18n.t('About these relationships')}</summary>
        <p className="muted">
          {i18n.t(
            'Dependencies describe required work, not claim permission. Related tasks are advisory. Owners reflect saved assignments, not live availability.',
          )}
        </p>
      </details>
    </section>
  );
}
