import type { ReactNode } from 'react';
import { checkLabels } from './throughput';
import { knownLabel } from './review-throughput';
import { closureActionLabels, flowReasonLabels } from './flow-copy';
import { useI18n } from './i18n';
import { usePresentation } from './presentation-ui';
import { PlanSummary } from './flow-plan-ui';
import { selectedPlan } from './flow-plan';
import { record } from './model';
import {
  closureContext,
  integratedReview,
  integratedReviewProvided,
  integratedReviewVisible,
} from './flow-closure';
import type { Snapshot } from './model';
import { navigationHash, emptyFilters } from './navigation';
import type { WorkContext } from './navigation';

const requirementLabels: Readonly<Record<string, string>> = {
  task_review: 'Task review',
  current_verification: 'Current verification',
  question: 'Questions',
  handoff: 'Handoff',
  integrity: 'Data integrity',
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
      <IntegratedReviewFacts snapshot={snapshot} taskId={taskId} />
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
        <IntegratedObligations snapshot={snapshot} taskId={taskId} />
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

const integratedLabels: Readonly<Record<string, string>> = {
  accepted: 'Integrated acceptance applies',
  not_accepted: 'Integrated acceptance needs attention',
  not_assessed: 'Integrated acceptance not assessed',
  unavailable: 'Integrated acceptance unavailable',
};
export function IntegratedReviewFacts({
  snapshot,
  taskId,
}: {
  readonly snapshot: Snapshot;
  readonly taskId?: string;
}) {
  const i18n = useI18n();
  if (!integratedReviewVisible(snapshot)) return null;
  const review = integratedReview(snapshot);
  if (!review)
    return <p className="lane-warning">{i18n.t('Integrated acceptance is unavailable.')}</p>;
  if (taskId && !review.memberIds.includes(taskId)) return null;
  const acceptedMember =
    taskId && review.currentness === 'current' && review.acceptedMemberIds.includes(taskId);
  const unmapped = taskId
    ? review.obligations.filter((row) => row.taskId === taskId && row.status === 'unmapped').length
    : review.counts.unmapped;
  return (
    <div className="integrated-review-facts">
      <p>
        {i18n.t(
          acceptedMember
            ? 'Member completion accepted by integrated review'
            : review.status === 'accepted' && review.currentness !== 'current'
              ? 'Recorded integrated acceptance needs reassessment'
              : knownLabel(integratedLabels, review.status),
        )}
        {review.currentness !== 'current' && <> · {i18n.human(review.currentness)}</>}
      </p>
      {!taskId && (
        <p className="muted">
          {i18n.t('{accepted} / {total} members accepted', {
            accepted: review.counts.accepted_members,
            total: review.counts.members,
          })}
        </p>
      )}
      {unmapped > 0 && (
        <p className="muted">
          {i18n.t('{count} check mappings not assessed; no new execution is implied.', {
            count: unmapped,
          })}
        </p>
      )}
    </div>
  );
}
export function IntegratedReviewNextAction({
  snapshot,
  fallback,
}: {
  readonly snapshot: Snapshot;
  readonly fallback: ReactNode;
}) {
  const i18n = useI18n(),
    presentation = usePresentation();
  const review = integratedReviewVisible(snapshot) ? integratedReview(snapshot) : null;
  if (!review || !review.nextAction) return <>{fallback}</>;
  return (
    <>
      {review.nextActorId && <>{presentation.actor(review.nextActorId)} · </>}
      {i18n.t(knownLabel(closureActionLabels, review.nextAction))}
      {review.nextTrigger && review.nextTrigger !== review.nextAction && (
        <span className="integrated-trigger">
          {i18n.t('Next trigger')}: {i18n.t(knownLabel(closureActionLabels, review.nextTrigger))}
        </span>
      )}
    </>
  );
}
export function IntegratedObligations({
  snapshot,
  taskId,
}: {
  readonly snapshot: Snapshot;
  readonly taskId?: string;
}) {
  const i18n = useI18n(),
    presentation = usePresentation();
  if (!integratedReviewProvided(snapshot)) return null;
  const review = integratedReview(snapshot);
  if (!review)
    return <p className="lane-warning">{i18n.t('Integrated acceptance is unavailable.')}</p>;
  const rows = review.obligations.filter((row) => !taskId || row.taskId === taskId);
  return (
    <>
      <ul className="flow-details-list integrated-obligations">
        {rows.slice(0, 50).map((row, index) => (
          <li key={row.taskId + ':' + row.checkId + ':' + index}>
            {!taskId && <>{presentation.taskReference(row.taskId)} · </>}
            {row.checkId && <>{i18n.t(knownLabel(checkLabels, row.checkId))} · </>}
            {i18n.t(knownLabel(obligationLabels, row.status))}
            {row.actionable && row.nextAction && (
              <p>
                {row.nextActorId && <>{presentation.actor(row.nextActorId)} · </>}
                {i18n.t(knownLabel(closureActionLabels, row.nextAction))}
              </p>
            )}
            {row.reasons
              .filter((reason) => !(row.status === 'unmapped' && reason === 'mapping_absent'))
              .map((reason) => (
                <p key={reason} className="muted">
                  {i18n.t(
                    Object.hasOwn(flowReasonLabels, reason)
                      ? knownLabel(flowReasonLabels, reason)
                      : 'Inspect current review obligations',
                  )}
                </p>
              ))}
          </li>
        ))}
      </ul>
      {rows.length > 50 && (
        <p className="muted">
          {i18n.t('{count} integrated review details omitted.', { count: rows.length - 50 })}
        </p>
      )}
    </>
  );
}
const obligationLabels: Readonly<Record<string, string>> = {
  fulfilled: 'Named obligation fulfilled',
  historical_only: 'Historical execution',
  unmapped: 'Check mapping not assessed',
  requires_recheck: 'Reassessment required',
  pending: 'Verification is waiting',
  adverse: 'Verification needs attention',
  unavailable: 'Verification is unavailable',
};
export function IntegratedMemberContext({
  snapshot,
  taskId,
}: {
  readonly snapshot: Snapshot;
  readonly taskId: string;
}) {
  const i18n = useI18n(),
    review = integratedReview(snapshot);
  return review?.currentness === 'current' && review.acceptedMemberIds.includes(taskId) ? (
    <p className="muted">
      {i18n.t(
        'Member completion is accepted separately; executions below retain their own freshness.',
      )}
    </p>
  ) : null;
}
