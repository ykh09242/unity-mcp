import { closureActionLabels } from './flow-copy';
import { useI18n } from './i18n';
import { usePresentation } from './presentation-ui';
import { PlanSummary } from './flow-plan-ui';
import { selectedPlan } from './flow-plan';
import { record } from './model';
import { closureContext } from './flow-closure';
import type { Snapshot } from './model';
import { navigationHash, emptyFilters } from './navigation';
import type { WorkContext } from './navigation';

const requirementLabels: Readonly<Record<string, string>> = {
  task_review: 'Task review',
  required_checks: 'Required checks',
  delivery: 'Delivery',
  cleanup: 'Cleanup',
  integrated_review: 'Integrated review',
  objective: 'Objective acceptance',
};
const closureLabels: Readonly<Record<string, string>> = {
  incomplete: 'Closure work remains',
  ready: 'Closure prerequisites recorded',
  accepted: 'Objective acceptance recorded',
  unavailable: 'Closure facts unavailable',
};
export function ClosureSummary({
  snapshot,
  taskId,
}: {
  readonly snapshot: Snapshot;
  readonly taskId: string;
}) {
  const i18n = useI18n(),
    presentation = usePresentation();
  if (snapshot.closure === undefined) return null;
  const closure = closureContext(snapshot);
  if (!closure) return <p className="lane-warning">{i18n.t('Closure summary is unavailable.')}</p>;
  if (!closure.taskIds.includes(taskId)) return null;
  const rows = closure.requirements.filter((row) => !row.taskId || row.taskId === taskId);
  const first = rows.find((row) =>
    ['failed', 'blocked', 'stale', 'unavailable', 'unknown'].includes(row.status),
  );
  return (
    <section className="flow-summary closure-summary" aria-label={i18n.t('Objective closure')}>
      <h3>{i18n.t('Objective closure')}</h3>
      <div className="lane-heading">
        <span>{i18n.t(closureLabels[closure.status] || closure.status)}</span>
      </div>
      {(closure.nextActorId || closure.nextAction) && (
        <p className="flow-next-action">
          {closure.nextActorId && (
            <>
              <span className="muted">{i18n.t('Next actor')}: </span>
              {presentation.actor(closure.nextActorId)} ·{' '}
            </>
          )}
          {i18n.t(closureActionLabels[closure.nextAction] || closure.nextAction || 'Not recorded')}
        </p>
      )}
      {first?.reason && <p className="lane-warning">{i18n.warning(first.reason)}</p>}
      <details className="lane-details">
        <summary>{i18n.t('Closure details')}</summary>
        <ul className="flow-details-list">
          {rows.map((row, index) => (
            <li key={row.kind + ':' + row.taskId + ':' + index}>
              <strong>{i18n.t(requirementLabels[row.kind] || row.kind)}</strong> ·{' '}
              {i18n.human(row.status)}
              {row.reason && <p>{i18n.warning(row.reason)}</p>}
              {(row.nextActorId || row.nextAction) && (
                <p className="muted">
                  {row.nextActorId && <>{presentation.actor(row.nextActorId)} · </>}
                  {i18n.t(closureActionLabels[row.nextAction] || row.nextAction || 'Not recorded')}
                </p>
              )}
            </li>
          ))}
        </ul>
        {closure.omitted > 0 && (
          <p className="muted">
            {i18n.t('{count} closure requirements omitted.', { count: closure.omitted })}
          </p>
        )}
      </details>
      <PlanSummary snapshot={snapshot} />
    </section>
  );
}
export function ClosureAttention({
  snapshot,
  context,
}: {
  readonly snapshot: Snapshot;
  readonly context: WorkContext;
}) {
  const i18n = useI18n(),
    presentation = usePresentation();
  if (snapshot.closure === undefined) return null;
  const closure = closureContext(snapshot);
  if (!closure) return <p className="lane-warning">{i18n.t('Closure summary is unavailable.')}</p>;
  const unfinished = snapshot.tasks.some(
    (task) => closure.taskIds.includes(task.id) && task.status !== 'done',
  );
  const blocker = closure.requirements.find(
    (row) =>
      ['failed', 'blocked', 'stale'].includes(row.status) ||
      ['current_verification', 'delivery', 'integrity'].includes(row.kind),
  );
  const plan = selectedPlan(snapshot, record(snapshot.closure).plan);
  const planProvided = record(snapshot.closure).plan !== undefined;
  const activePublication =
    planProvided &&
    (!plan ||
      !['not_started', 'published'].includes(plan.publication) ||
      plan.preparation !== 'prepared');
  if (unfinished && !blocker && !activePublication) return null;
  if (closure.status === 'accepted') return null;
  const taskId = blocker?.taskId || closure.requirements.find((row) => row.taskId)?.taskId;
  return (
    <section className="closure-attention" aria-label={i18n.t('Closure next action')}>
      <h3>{i18n.t('Closure next action')}</h3>
      <p>
        {(plan?.nextActorId || closure.nextActorId) && (
          <>{presentation.actor(plan?.nextActorId || closure.nextActorId)} · </>
        )}
        {i18n.t(
          closureActionLabels[plan?.nextAction || closure.nextAction] ||
            plan?.nextAction ||
            closure.nextAction ||
            'Not recorded',
        )}
      </p>
      {blocker?.reason && <p className="lane-warning">{i18n.warning(blocker.reason)}</p>}
      <a
        href={navigationHash(taskId ? { view: 'task', taskId } : { view: 'work' }, {
          ...context,
          selected: taskId || null,
          filters: emptyFilters,
          paging: { ...context.paging, index: 0 },
        })}
      >
        {i18n.t(taskId ? 'Inspect closure work' : 'Browse work')}
      </a>
    </section>
  );
}
