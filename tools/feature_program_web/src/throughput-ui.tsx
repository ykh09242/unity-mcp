import { CheckInputs } from './flow-inputs-ui';
import { RunHistory } from './flow-run-history-ui';
import { LocalExecution } from './flow-execution-ui';
import { QueueResponsibilities } from './flow-queue-ui';
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
  checkLabels,
  executionPriority,
  findingsText,
  laneNeedsAction,
  matchesCoordination,
  observedWait,
  providerContext,
  providerStageKey,
  providerStageIdentity,
  hasProviderRecord,
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
      {coordinationViews
        .filter((view) => view !== 'review_queue')
        .map((view) => {
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
  if (!string(raw.started_at) && raw.status !== 'observed') return null;
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
function Lane({
  lane,
  snapshot,
  task,
  represented,
}: {
  readonly lane: JsonRecord;
  readonly snapshot: Snapshot;
  readonly task: Task;
  readonly represented: ReadonlySet<string>;
}) {
  const i18n = useI18n();
  const blocker = record(lane.blocker);
  const recheck = record(lane.recheck);
  const showResult =
    lane.phase === 'finished' || !['unknown', 'pending', ''].includes(string(lane.result));
  const showRecheck = string(recheck.status) && recheck.status !== 'not_assessed';
  const hasWait =
    string(record(lane.waiting).started_at) || record(lane.waiting).status === 'observed';
  const details = [...findingsText(lane.reasons), ...findingsText(recheck.reasons)];
  return (
    <li className="verification-lane">
      <div className="lane-heading">
        <strong>{i18n.t(laneLabels[string(lane.lane)] || string(lane.lane))}</strong>
        <span className="badge">{i18n.human(lane.phase)}</span>
        {showResult && <span className="badge">{i18n.human(lane.result)}</span>}
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
                .map((id) =>
                  checkLabels[string(id)] ? i18n.t(checkLabels[string(id)]!) : i18n.display(id),
                )
                .join(', ')}
            </>
          )}
          {items(lane.criteria).length > 0 && (
            <> · {i18n.t('{count} explicit criteria', { count: items(lane.criteria).length })}</>
          )}
        </p>
      )}
      {(hasWait || showRecheck) && (
        <div className="lane-foot">
          <Waiting value={lane.waiting} />
          {showRecheck && (
            <span>
              {i18n.t('Recheck')}: {i18n.human(recheck.status)}
            </span>
          )}
        </div>
      )}
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
      <CheckInputs value={lane.input_summary} />
      {lane.execution_summary === undefined && lane.execution_history !== undefined && (
        <p className="muted">
          {i18n.t('No current execution capture recorded; earlier runs remain history.')}
        </p>
      )}
      <LocalExecution value={lane.execution_summary} />
      <RunHistory value={lane.execution_history} />
      {lane.provider_execution && (
        <ProviderStages
          snapshot={snapshot}
          task={task}
          value={lane.provider_execution}
          represented={
            lane.applicability === 'current' && lane.phase !== 'canceled' ? represented : undefined
          }
        />
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
  const projected = record(task.verification);
  const hasVerification =
    Number(record(projected.lanes).total) > 0 ||
    items(record(projected.lanes).items).length > 0 ||
    Number(record(projected.findings).total) > 0 ||
    items(record(projected.findings).items).length > 0;
  const hasProvider = hasProviderRecord(task.provider_execution);
  const hasDelivery =
    task.status === 'done' &&
    Boolean(string(record(task.delivery).status)) &&
    record(task.delivery).status !== 'unrecorded';
  if (!hasVerification && !hasProvider && !hasDelivery) return null;
  const provider = record(task.provider_execution);
  const represented = new Set(
    (provider.applicability === 'current' || (task.status === 'done' && !provider.applicability)
      ? providerContext(snapshot, task)
          ?.stages.map(providerStageIdentity)
          .filter((identity): identity is string => identity !== null)
      : []) || [],
  );
  return (
    <section className="verification-context" aria-label={i18n.t('Verification lanes')}>
      {hasVerification && <h3>{i18n.t('Verification lanes')}</h3>}
      {hasVerification &&
        (!context ? (
          <p className="muted">
            {i18n.t('No current verification record. Task and review records remain available.')}
          </p>
        ) : (
          <>
            {context.lanes.total ? (
              <ul className="verification-lanes">
                {context.lanes.items.map((lane) => (
                  <Lane
                    key={`${string(lane.request_id)}:${String(lane.request_version)}`}
                    lane={lane}
                    snapshot={snapshot}
                    task={task}
                    represented={represented}
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
        ))}
      <ProviderStages snapshot={snapshot} task={task} />
      {hasDelivery && <CurrentDelivery task={task} />}
    </section>
  );
}

function ProviderStages({
  snapshot,
  task,
  value = task.provider_execution,
  represented,
}: {
  readonly snapshot: Snapshot;
  readonly task: Task;
  readonly value?: Json;
  readonly represented?: ReadonlySet<string>;
}) {
  const i18n = useI18n();
  const presentation = usePresentation();
  const provider = record(value);
  if (!hasProviderRecord(value)) return null;
  const context = providerContext(snapshot, { ...task, provider_execution: value || null });
  const stages =
    context?.stages.filter((stage) => {
      const identity = providerStageIdentity(stage);
      return provider.applicability !== 'current' || !identity || !represented?.has(identity);
    }) || [];
  if (
    context &&
    represented &&
    context.stages.length > 0 &&
    !stages.length &&
    !Number(provider.omitted) &&
    !Number(provider.omitted_lanes)
  )
    return null;
  return (
    <section className="provider-stages">
      <h4>{i18n.t('PR and beta execution')}</h4>
      {provider.applicability && provider.applicability !== 'current' && (
        <p className="lane-warning">{i18n.human(provider.applicability)}</p>
      )}
      {!context ? (
        <p className="muted">{i18n.t('Provider execution context is unavailable.')}</p>
      ) : (
        <>
          <ul className="verification-lanes">
            {stages.map((stage) => (
              <li className="verification-lane" key={providerStageKey(stage)}>
                <div className="lane-heading">
                  <strong>{i18n.t(laneLabels[string(stage.kind)] || string(stage.kind))}</strong>
                  <span className="muted">{i18n.display(stage.id)}</span>
                  {string(stage.verifier_id) && (
                    <span className="muted">{presentation.actor(stage.verifier_id)}</span>
                  )}
                  <span className="badge">{i18n.human(stage.status)}</span>
                  {stage.current === false && (
                    <span className="lane-warning">{i18n.t('Earlier context')}</span>
                  )}
                  {stage.reported_outcome &&
                    stage.reported_outcome !== stage.status &&
                    !(stage.status === 'passed' && stage.reported_outcome === 'success') && (
                      <span>
                        {i18n.t('Reported outcome')}: {i18n.human(stage.reported_outcome)}
                      </span>
                    )}
                </div>
                <ProviderChecks stage={stage} />
                {Number(stage.checks_omitted) > 0 && (
                  <p className="muted">
                    {i18n.t('{count} checks omitted', { count: Number(stage.checks_omitted) })}
                  </p>
                )}
                <ProviderTime value={stage.timing} />
                {records(stage.history).length > 0 && (
                  <details className="lane-details">
                    <summary>
                      {i18n.t('Execution history')} ·{' '}
                      <OutcomeCounts observations={records(stage.history)} />
                    </summary>
                    <ul>
                      {records(stage.history).map((run, runIndex) => (
                        <li key={`${String(run.run_id)}:${String(run.run_attempt)}:${runIndex}`}>
                          {i18n.t('Run {id}', { id: i18n.display(run.run_id) })} ·{' '}
                          {i18n.t('Attempt')} {i18n.display(run.run_attempt)} ·{' '}
                          {i18n.human(run.status || 'unknown')} ·{' '}
                          <span className="badge">
                            {i18n.human(run.reported_outcome || 'unknown')}
                          </span>
                          {' · '}
                          {i18n.date(run.captured_at)}
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
          {!context.stages.length && provider.adopted === true && (
            <p className="muted">{i18n.t('Awaiting a current provider observation')}</p>
          )}
          {Number(provider.omitted) > 0 && (
            <p className="muted">
              {i18n.t('{count} stages omitted', { count: Number(provider.omitted) })}
            </p>
          )}
          {Number(provider.omitted_lanes) > 0 && (
            <p className="lane-warning">
              {i18n.t('{count} provider lanes omitted; execution assessment is incomplete.', {
                count: Number(provider.omitted_lanes),
              })}
            </p>
          )}
        </>
      )}
    </section>
  );
}
function CheckLabels({ check }: { readonly check: JsonRecord }) {
  const i18n = useI18n();
  const status = i18n.human(check.status);
  const mode = check.mode ? i18n.human(check.mode) : null;
  const applicability =
    check.applicability && check.applicability !== 'current'
      ? i18n.human(check.applicability)
      : null;
  return (
    <>
      <span className="badge">{status}</span>{' '}
      {mode &&
        mode !== status &&
        !(check.status === 'policy_skipped' && check.mode === 'policy_skip') && (
          <span className="muted">{mode}</span>
        )}{' '}
      {applicability && applicability !== status && applicability !== mode && (
        <span className="lane-warning">{applicability}</span>
      )}{' '}
      {check.reported_outcome === 'failure' && check.status !== 'failed' && (
        <span className="lane-warning">{i18n.human(check.reported_outcome)}</span>
      )}
    </>
  );
}
function ProviderChecks({ stage }: { readonly stage: JsonRecord }) {
  const i18n = useI18n();
  const checks = records(stage.checks);
  if (!checks.length) return null;
  const groups = new Map<string, { check: JsonRecord; count: number }>();
  for (const check of checks) {
    const key = JSON.stringify([
      check.status,
      check.mode,
      check.applicability,
      check.reported_outcome,
    ]);
    const group = groups.get(key);
    if (group) group.count++;
    else groups.set(key, { check, count: 1 });
  }
  return (
    <>
      <p className="lane-foot" aria-label={i18n.t('Check summary')}>
        {[...groups.entries()].map(([key, { check, count }]) => (
          <span key={key}>
            {i18n.t('{count} checks', { count })} · <CheckLabels check={check} />
          </span>
        ))}
      </p>
      <details className="lane-details">
        <summary>{i18n.t('Check details ({count})', { count: checks.length })}</summary>
        <ul className="provider-checks">
          {checks.map((check, index) => (
            <li key={string(check.id) || index}>
              <span>{i18n.display(check.id)}</span> <CheckLabels check={check} />
            </li>
          ))}
        </ul>
      </details>
    </>
  );
}
function OutcomeCounts({ observations }: { readonly observations: readonly JsonRecord[] }) {
  const i18n = useI18n();
  const counts = new Map<string, number>();
  for (const run of observations) {
    const outcome = string(run.reported_outcome) || 'unknown';
    counts.set(outcome, (counts.get(outcome) || 0) + 1);
  }
  return [...counts]
    .map(([outcome, count]) => `${i18n.number(count)} ${i18n.human(outcome)}`)
    .join(' · ');
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
  if (snapshot.review_queue !== undefined)
    return <QueueResponsibilities snapshot={snapshot} context={context} />;
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
