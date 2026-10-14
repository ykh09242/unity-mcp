import { useI18n } from './i18n';
import { items, record, records, safeUrl, string } from './model';
import type { Json, JsonRecord, Snapshot, Task } from './model';
import { usePresentation } from './presentation-ui';
import { defaultContext, emptyFilters, navigationHash, scopedTasks } from './navigation';
import type { WorkContext } from './navigation';
import { snapshotIndex } from './selectors';
import {
  coordinationLabels,
  coordinationViews,
  executionPriority,
  findingsText,
  laneNeedsAction,
  matchesCoordination,
  observedWait,
  providerContext,
  readinessFactors,
  verificationAssignments,
  verificationContext,
} from './throughput';

const laneLabels: Readonly<Record<string, string>> = {
  code_review: 'Code review',
  review: 'Code review',
  qa: 'QA',
  visual: 'Visual QA',
  visual_qa: 'Visual QA',
  ci: 'CI',
  pr_ci: 'PR CI',
  beta_ci: 'Beta CI',
};
export function CoordinationGlimpse({
  snapshot,
  task,
}: {
  readonly snapshot: Snapshot;
  readonly task: Task;
}) {
  const i18n = useI18n();
  const presentation = usePresentation();
  const factors = readinessFactors(snapshot, task);
  const verification = verificationContext(snapshot, task);
  const needs = verification?.lanes.items.filter(laneNeedsAction).length || 0;
  const nextLane = verification?.lanes.items.find(laneNeedsAction);
  const dependents = (snapshotIndex(snapshot).reverseDependencies.get(task.id) || []).filter(
    (peer) => peer.status !== 'done',
  ).length;
  const text = factors.stale
    ? i18n.t('Revalidation required')
    : Number(factors.blockers) > 0
      ? i18n.t('{count} recorded blockers', { count: factors.blockers! })
      : factors.available
        ? i18n.t('No recorded blockers')
        : '';
  if (!text && !needs && !(task.status === 'blocked' && dependents)) return null;
  return (
    <span className="task-coordination">
      {text}
      {text && needs > 0 && ' · '}
      {needs > 0 && i18n.t('{count} verification needs', { count: needs })}
      {task.status === 'blocked' && dependents > 0 && (
        <> · {i18n.t('{count} open dependents', { count: dependents })}</>
      )}
      {factors.available && (
        <>
          {' '}
          · {i18n.t('Revalidation required')}
          {Object.hasOwn(record(snapshot.execution_priorities), task.id) && (
            <> · {i18n.t('Priority {value}', { value: executionPriority(snapshot, task) })}</>
          )}
        </>
      )}
      {nextLane && (
        <span className="task-verification-action">
          {i18n.t(laneLabels[string(nextLane.lane)] || string(nextLane.lane))} ·{' '}
          {presentation.actor(
            record(nextLane.blocker).resolver_id || nextLane.resolver_id || nextLane.verifier_id,
          ) || i18n.t('Not recorded')}{' '}
          ·{' '}
          {i18n.display(
            record(nextLane.blocker).next_trigger || nextLane.next_trigger,
            'Not recorded',
          )}
        </span>
      )}
    </span>
  );
}

export function CoordinationSummary({
  snapshot,
  context,
}: {
  readonly snapshot: Snapshot;
  readonly context: WorkContext;
}) {
  const i18n = useI18n();
  const tasks = scopedTasks(snapshot, context.scope);
  const incomplete = tasks.some((task) => {
    const value = verificationContext(snapshot, task);
    return !value || value.lanes.omitted > 0;
  });
  return (
    <nav className="coordination-summary" aria-label={i18n.t('Work coordination')}>
      {coordinationViews.map((view) => {
        const count = tasks.filter((task) => matchesCoordination(snapshot, task, view)).length;
        const partial = incomplete && ['verification', 'waiting'].includes(view);
        return (
          <a
            key={view}
            href={navigationHash(
              { view: 'work' },
              {
                ...context,
                filters: { ...emptyFilters, coordination: view },
                selected: null,
                paging: { ...context.paging, index: 0 },
              },
            )}
          >
            <strong>
              {partial
                ? count
                  ? i18n.t('At least {count}', { count })
                  : i18n.t('Not recorded')
                : i18n.number(count)}
            </strong>{' '}
            {i18n.t(coordinationLabels[view]!)}
          </a>
        );
      })}
    </nav>
  );
}

function Actor({ id }: { readonly id: Json | undefined }) {
  const i18n = useI18n();
  const presentation = usePresentation();
  return string(id) ? (
    <a
      href={navigationHash(
        { view: 'work' },
        {
          ...defaultContext,
          filters: { ...emptyFilters, coordination: 'verification', owner: string(id) },
        },
      )}
    >
      {presentation.actor(id)}
    </a>
  ) : (
    <span className="muted">{i18n.t('Not recorded')}</span>
  );
}
function Waiting({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  const wait = observedWait(value);
  const raw = record(value);
  if (!wait)
    return (
      <span className="muted">
        {i18n.t(
          string(raw.started_at) ? 'Wait start recorded; duration unknown' : 'No observed interval',
        )}
      </span>
    );
  const minutes = Math.floor(wait.seconds / 60);
  return (
    <span className="observed-wait">
      <strong>
        {wait.seconds < 60
          ? i18n.t('{value} sec', { value: Math.floor(wait.seconds) })
          : i18n.t('{value} min', { value: minutes })}
      </strong>
      <span className="muted">
        {i18n.t(wait.open ? 'Open wait as of {time}' : 'Completed wait at {time}', {
          time: i18n.date(wait.end),
        })}
      </span>
    </span>
  );
}
function Lane({ lane }: { readonly lane: JsonRecord }) {
  const i18n = useI18n();
  const blocker = record(lane.blocker);
  const recheck = record(lane.recheck);
  const details = [...findingsText(lane.reasons), ...findingsText(recheck.reasons)];
  return (
    <li className="verification-lane">
      <div className="lane-heading">
        <strong>{i18n.t(laneLabels[string(lane.lane)] || string(lane.lane))}</strong>
        <span className="badge">{i18n.human(lane.phase)}</span>
        <span className="badge">{i18n.human(lane.result)}</span>
        {lane.applicability !== 'current' && (
          <span className="badge lane-warning">{i18n.human(lane.applicability)}</span>
        )}
      </div>
      <div className="lane-action">
        <span>
          {i18n.t('Verifier')}: <Actor id={lane.verifier_id} />
        </span>
        {(blocker.resolver_id || lane.resolver_id) && (
          <span>
            {i18n.t('Resolver')}: <Actor id={blocker.resolver_id || lane.resolver_id} />
          </span>
        )}
        <span>
          {i18n.t('Next trigger')}:{' '}
          {i18n.display(blocker.next_trigger || lane.next_trigger, 'Not recorded')}
        </span>
      </div>
      {string(blocker.reason) && <p className="lane-blocker">{i18n.display(blocker.reason)}</p>}
      {(items(lane.check_ids).length > 0 || items(lane.criteria).length > 0) && (
        <p className="muted lane-obligations">
          {items(lane.check_ids).length > 0 && (
            <>
              {i18n.t('{count} assigned checks', { count: items(lane.check_ids).length })}:{' '}
              {items(lane.check_ids)
                .map((id) => i18n.display(id))
                .join(', ')}
            </>
          )}
          {items(lane.criteria).length > 0 && (
            <> · {i18n.t('{count} explicit criteria', { count: items(lane.criteria).length })}</>
          )}
        </p>
      )}
      <div className="lane-foot">
        <Waiting value={lane.waiting} />
        {string(recheck.status) && (
          <span>
            {i18n.t('Recheck')}: {i18n.human(recheck.status)}
          </span>
        )}
      </div>
      {details.length > 0 && (
        <details className="lane-details">
          <summary>{i18n.t('Assessment details')}</summary>
          <ul>
            {[...new Set(details)].map((text, index) => (
              <li key={index}>{i18n.warning(text)}</li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

export function VerificationLanes({
  snapshot,
  task,
}: {
  readonly snapshot: Snapshot;
  readonly task: Task;
}) {
  const i18n = useI18n();
  const context = verificationContext(snapshot, task);
  return (
    <section className="verification-context" aria-label={i18n.t('Verification lanes')}>
      <h3>{i18n.t('Verification lanes')}</h3>
      {!context ? (
        <p className="muted">
          {i18n.t('No current verification record. Task and review records remain available.')}
        </p>
      ) : (
        <>
          <p className="muted lane-explanation">
            {i18n.t('Recorded progress and outcomes; applicability is assessed separately.')}
          </p>
          {context.lanes.total ? (
            <ul className="verification-lanes">
              {context.lanes.items.map((lane) => (
                <Lane
                  key={`${string(lane.request_id)}:${String(lane.request_version)}`}
                  lane={lane}
                />
              ))}
            </ul>
          ) : (
            <p className="muted">{i18n.t('No verification requests recorded')}</p>
          )}
          {context.lanes.omitted > 0 && (
            <p className="muted">
              {i18n.t('{shown} / {total} shown · {omitted} omitted', {
                shown: context.lanes.shown,
                total: context.lanes.total,
                omitted: context.lanes.omitted,
              })}
            </p>
          )}
          {context.findings.total > 0 && (
            <section className="current-findings">
              <h4>{i18n.t('Recorded findings')}</h4>
              <ul>
                {context.findings.items.map((value, index) => {
                  const finding = record(value);
                  return (
                    <li key={string(finding.id) || index}>
                      <p>{i18n.display(finding.statement)}</p>
                      {items(finding.resolutions).length > 0 && (
                        <span className="muted">
                          {i18n.t('Resolution recorded; acceptance remains separate.')}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
              {context.findings.omitted > 0 && (
                <p className="muted">
                  {i18n.t('{shown} / {total} shown · {omitted} omitted', {
                    shown: context.findings.shown,
                    total: context.findings.total,
                    omitted: context.findings.omitted,
                  })}
                </p>
              )}
            </section>
          )}
        </>
      )}
      <ProviderStages snapshot={snapshot} task={task} />
      {task.status === 'done' && <CurrentDelivery task={task} />}
    </section>
  );
}

function ProviderStages({ snapshot, task }: { readonly snapshot: Snapshot; readonly task: Task }) {
  const i18n = useI18n();
  const provider = record(task.provider_execution);
  if (!Object.hasOwn(task, 'provider_execution')) return null;
  const context = providerContext(snapshot, task);
  return (
    <section className="provider-stages">
      <h4>{i18n.t('PR and beta execution')}</h4>
      {!context ? (
        <p className="muted">{i18n.t('Provider execution context is unavailable.')}</p>
      ) : (
        <>
          <ul className="verification-lanes">
            {context.stages.map((stage, index) => (
              <li className="verification-lane" key={string(stage.id) || index}>
                <div className="lane-heading">
                  <strong>{i18n.t(laneLabels[string(stage.kind)] || string(stage.kind))}</strong>
                  <span className="badge">{i18n.human(stage.status)}</span>
                  {stage.current === false && (
                    <span className="lane-warning">{i18n.t('Earlier context')}</span>
                  )}
                  {stage.reported_outcome && stage.reported_outcome !== stage.status && (
                    <span>
                      {i18n.t('Reported outcome')}: {i18n.human(stage.reported_outcome)}
                    </span>
                  )}
                </div>
                {records(stage.checks).length > 0 && (
                  <ul className="provider-checks">
                    {records(stage.checks).map((check, checkIndex) => (
                      <li key={string(check.id) || checkIndex}>
                        <span>{i18n.display(check.id)}</span>{' '}
                        <span className="badge">{i18n.human(check.status)}</span>
                        {check.mode && <span className="muted">{i18n.human(check.mode)}</span>}
                        {check.applicability && check.applicability !== 'current' && (
                          <span className="lane-warning">{i18n.human(check.applicability)}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {Number(stage.checks_omitted) > 0 && (
                  <p className="muted">
                    {i18n.t('{count} checks omitted', { count: Number(stage.checks_omitted) })}
                  </p>
                )}
                <ProviderTime value={stage.timing} />
                {records(stage.history).length > 0 && (
                  <details className="lane-details">
                    <summary>{i18n.t('Previous execution observations')}</summary>
                    <ul>
                      {records(stage.history).map((run, runIndex) => (
                        <li key={`${String(run.run_id)}:${String(run.run_attempt)}:${runIndex}`}>
                          {i18n.t('Attempt')} {i18n.display(run.run_attempt)} ·{' '}
                          {i18n.human(
                            run.status || run.conclusion || run.reported_outcome || 'unknown',
                          )}
                        </li>
                      ))}
                    </ul>
                    {Number(stage.history_omitted) > 0 && (
                      <p className="muted">
                        {i18n.t('{count} execution observations omitted', {
                          count: Number(stage.history_omitted),
                        })}
                      </p>
                    )}
                  </details>
                )}
              </li>
            ))}
          </ul>
          {Number(provider.omitted) > 0 && (
            <p className="muted">
              {i18n.t('{count} stages omitted', { count: Number(provider.omitted) })}
            </p>
          )}
        </>
      )}
    </section>
  );
}
function ProviderTime({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  const timing = record(value);
  const seconds = timing.duration_seconds;
  return (
    <p className="muted">
      {typeof seconds === 'number' &&
      Number.isFinite(seconds) &&
      seconds >= 0 &&
      Number.isFinite(Date.parse(string(timing.started_at))) &&
      Number.isFinite(Date.parse(string(timing.completed_at))) &&
      Date.parse(string(timing.completed_at)) >= Date.parse(string(timing.started_at)) &&
      Math.abs(
        (Date.parse(string(timing.completed_at)) - Date.parse(string(timing.started_at))) / 1000 -
          seconds,
      ) <= 1
        ? i18n.t('Recorded execution: {value} sec', { value: Math.floor(seconds) })
        : i18n.t('Execution duration unknown')}
    </p>
  );
}

function CurrentDelivery({ task }: { readonly task: Task }) {
  const i18n = useI18n();
  const delivery = record(task.delivery);
  const metadata = record(delivery.metadata);
  const url = safeUrl(metadata.pr_url);
  return (
    <section className="current-delivery-glance">
      <h4>{i18n.t('Current delivery')}</h4>
      <p>
        {i18n.t('Accepted work; delivery is separate')} ·{' '}
        {i18n.human(delivery.status || 'unrecorded')}
      </p>
      {url && (
        <a href={url} target="_blank" rel="noreferrer">
          {i18n.t('Open PR')}
        </a>
      )}
      {metadata.merge_status && (
        <p className="muted">
          {i18n.t('Merge')}: {i18n.human(metadata.merge_status)}
        </p>
      )}
    </section>
  );
}

export function VerificationResponsibilities({
  snapshot,
  context,
}: {
  readonly snapshot: Snapshot;
  readonly context: WorkContext;
}) {
  const i18n = useI18n();
  const presentation = usePresentation();
  const assignments = verificationAssignments(snapshot, scopedTasks(snapshot, context.scope));
  const omitted = scopedTasks(snapshot, context.scope).reduce(
    (total, task) => total + (verificationContext(snapshot, task)?.lanes.omitted || 0),
    0,
  );
  if (!assignments.length && !omitted) return null;
  return (
    <section className="verification-responsibilities">
      <h3>{i18n.t('Verification responsibility')}</h3>
      <ul>
        {assignments.map(({ task, lane }) => (
          <li key={`${task.id}:${string(lane.request_id)}:${String(lane.request_version)}`}>
            <a
              href={navigationHash(
                { view: 'task', taskId: task.id },
                { ...defaultContext, scope: context.scope },
              )}
            >
              {presentation.taskReference(task.id)}
            </a>
            <span>
              {i18n.t(laneLabels[string(lane.lane)] || string(lane.lane))} ·{' '}
              <Actor id={lane.verifier_id} /> · {i18n.human(lane.phase)}
            </span>
            <span>
              {i18n.t('Next trigger')}:{' '}
              {i18n.display(record(lane.blocker).next_trigger || lane.next_trigger, 'Not recorded')}
            </span>
          </li>
        ))}
      </ul>
      {omitted > 0 && (
        <p className="muted">
          {i18n.t('{count} verification requests omitted; responsibility may be incomplete.', {
            count: omitted,
          })}
        </p>
      )}
    </section>
  );
}
