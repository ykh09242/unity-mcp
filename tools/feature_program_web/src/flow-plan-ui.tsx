import { closureActionLabels, recordOperationLabels } from './flow-copy';
import { useI18n } from './i18n';
import { usePresentation } from './presentation-ui';
import { selectedPlan } from './flow-plan';
import type { Snapshot } from './model';
import { record } from './model';

export function PlanSummary({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n(),
    presentation = usePresentation();
  const raw = record(snapshot.closure).plan;
  if (raw === undefined) return null;
  const plan = selectedPlan(snapshot, raw);
  if (!plan)
    return <p className="lane-warning">{i18n.t('Selected closure plan is unavailable.')}</p>;
  return (
    <section className="flow-summary plan-summary" aria-label={i18n.t('Selected closure plan')}>
      <div className="lane-heading">
        <strong>{i18n.t('Selected closure plan')}</strong>
        <span className={plan.preparation === 'prepared' ? 'muted' : 'lane-warning'}>
          {i18n.human(plan.preparation)}
        </span>
        <span className={plan.preflight === 'validated' ? 'muted' : 'lane-warning'}>
          {i18n.t(
            plan.preflight === 'validated'
              ? plan.preflightCurrent
                ? 'Preflight validated'
                : 'Historical preflight'
              : plan.preflight === 'failed'
                ? 'Preflight failed'
                : 'Preflight not assessed',
          )}
        </span>
        <span className="badge">{i18n.human(plan.publication)}</span>
      </div>
      <p className="flow-next-action">
        {plan.nextActorId && <>{presentation.actor(plan.nextActorId)} · </>}
        {i18n.t(closureActionLabels[plan.nextAction] || plan.nextAction || 'Not recorded')}
      </p>
      {plan.reasons[0] && <p className="lane-warning">{i18n.warning(plan.reasons[0])}</p>}
      <details className="lane-details">
        <summary>
          {i18n.t('Publication stages')} · {i18n.number(plan.total)}
        </summary>
        <ol className="flow-details-list">
          {plan.stages.map((stage) => (
            <li key={stage.id}>
              <strong>{i18n.t(recordOperationLabels[stage.operation] || stage.operation)}</strong> ·{' '}
              <span className="badge">{i18n.human(stage.status)}</span>
              {stage.taskId && <> · {presentation.taskReference(stage.taskId)}</>}
              {stage.viewRecovery && (
                <span className="lane-warning"> · {i18n.t('Views need recovery')}</span>
              )}
              {(stage.nextActorId || stage.nextAction) && (
                <p className="muted">
                  {stage.nextActorId && <>{presentation.actor(stage.nextActorId)} · </>}
                  {i18n.t(
                    closureActionLabels[stage.nextAction] || stage.nextAction || 'Not recorded',
                  )}
                </p>
              )}
            </li>
          ))}
        </ol>
        {plan.omitted > 0 && (
          <p className="muted">
            {i18n.t('{count} publication stages omitted.', { count: plan.omitted })}
          </p>
        )}
        {plan.reasons.length > 1 && (
          <ul>
            {plan.reasons.slice(1).map((reason, index) => (
              <li key={index}>{i18n.warning(reason)}</li>
            ))}
          </ul>
        )}
      </details>
    </section>
  );
}
