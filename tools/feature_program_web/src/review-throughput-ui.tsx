import { recordOperationLabels, closureActionLabels, flowReasonLabels } from './flow-copy';
import { useI18n } from './i18n';
import { usePresentation } from './presentation-ui';
import type { Snapshot } from './model';
import type { WorkContext } from './navigation';
import { defaultContext, emptyFilters, navigationHash } from './navigation';
import {
  evidencePreparation,
  intervalLabels,
  knownLabel,
  recordBatch,
  reviewHandoffs,
  reviewerLoads,
  reviewPhaseLabels,
  reviewTiming,
  taskHandoff,
  taskHandoffUnavailable,
} from './review-throughput';
import type { ObservedAge, TimingInterval } from './review-throughput';

const reasonLabels: Readonly<Record<string, string>> = {
  'Wait for recorded reviewer capacity before preparing dispatch': 'Wait for review capacity',
  'Recorded workload or explicit runtime capacity changes':
    'Review workload or observed capacity changes',
  'Observe actual native contact before preparing the exact handoff action':
    'Confirm reviewer contact and availability',
  'Exact recipient contact, status and evidence are observed':
    'Reviewer contact and availability observed',
  'Exact prior dispatch outcome or process terminal is observed':
    'Earlier delivery or reviewer execution end observed',
  'Reconcile retained dispatch claim with actual native outcome':
    'Confirm the review request status',
  'Dispatch exact packet through actual native transport': 'Send to the reviewer',
  'Resolve explicit reviewer eligibility before dispatch': 'Confirm reviewer eligibility',
  'Wait for actual reviewer acknowledgement/start': 'Wait for acknowledgement or review start',
  'Resolve actual transport failure before explicit retry':
    'Resolve the send failure before retrying',
  'Reconcile actual native delivery before any resend': 'Confirm delivery before resending',
  'Reviewer starts the exact current packet review': 'Reviewer starts the review',
  'Reviewer reports the actual bounded review outcome': 'Reviewer reports the review result',
  'Inspect ordinary review acceptance and director obligations':
    'Inspect the review result and remaining decisions',
  'Revalidate stale or unavailable inputs': 'Recheck changed or unavailable inputs',

  dispatch_pending: 'Review request awaits dispatch',
  reviewer_unassigned: 'Awaiting reviewer assignment',
  ack_pending: 'Awaiting reviewer acknowledgement',
  start_pending: 'Awaiting actual review start',
  terminal_pending: 'Awaiting observed review end',
  dispatch_failed: 'Review request could not be sent',
  dispatch_unknown: 'Review send outcome needs confirmation',
  terminal: 'Review ended',
  unobserved: 'Review activity has no retained observation',
  legacy_history_unavailable: 'Review activity has no retained observation',
  verification_pending: 'Verification is waiting',
  blocked: 'Waiting on the recorded blocker',
  verification_running: 'Verification start observed',
  captured_process: 'Captured process observed',
  missing_endpoint: 'An interval endpoint was not observed',
  missing_observed_at: 'An observation time is missing',
  invalid_timestamp: 'An observation time is invalid',
  naive_timestamp: 'An observation timezone is missing',
  missing_clock: 'Observation clock identity is missing',
  mixed_clocks: 'Observation clocks differ',
  inverted_endpoints: 'Observation times are out of order',
  future_endpoint: 'Observation endpoint exceeds its retained boundary',
  terminal_time_unobserved: 'Review ended; terminal time was not observed',
  wall_clock_conflict: 'Observed process duration conflicts with clock endpoints',
  process_duration_unavailable: 'Process duration is unavailable',
  unobserved_or_incompatible_intervals: 'Some intervals have missing or incompatible observations',

  current_handoff_context_unavailable: 'Current review context is unavailable',
  handoff_subject_changed: 'Submitted review context changed',
  reviewer_assignment_changed: 'Reviewer assignment changed',
  reviewer_eligibility_changed: 'Reviewer eligibility changed; director action required',
  handoff_evidence_unavailable: 'Review packet evidence is unavailable',
  reviewer_unregistered: 'Reviewer is not registered',
  reviewer_retired: 'Reviewer retirement is recorded',
  reviewer_implemented_task: 'Independent reviewer required',
  reviewer_is_director: 'Independent reviewer required',
  reviewer_eligibility_unassessed: 'Reviewer eligibility needs assessment',
  handoff_revalidation_required: 'Revalidate stale or unavailable inputs',
  capture_unavailable: 'Execution capture is unavailable.',
  ambiguous_terminal_association: 'Review end cannot be attributed to this submission',
  missing_decision: 'Supply the responsible review decision',
  missing_materialization: 'Selected evidence has not been retained',
  normal_validation_required: 'Ordinary receipt validation remains required',
  stale_binding: 'Selected evidence bindings changed',
  unavailable_evidence: 'Selected evidence is unavailable',
  invalid_preparation: 'Evidence preparation needs inspection',
};
const batchReasonLabels: Readonly<Record<string, string>> = {
  validated_not_published: 'Validated; publication remains pending',
  exact_commit: 'Exact batch commitment confirmed',
  derived_views_stale: 'Recorded views need recovery',
  batch_identity_conflict: 'Batch identity conflicts with the recorded commitment',
  base_revision_changed: 'The batch base revision changed',
  recorded_marker_only: 'Historical batch record; current preflight not assessed',
  recorded_commit_missing: 'The selected batch commitment was not found',
  recorded_publication: 'Batch publication recorded',
  preparation_failed: 'Batch preparation failed',
  render_required: 'Recorded views need recovery',
  precommit_guard_failed: 'Publication guards rejected the batch before commit',
};
const batchActions: Readonly<Record<string, string>> = {
  prepare_batch: 'Prepare the record batch',
  publish_batch: 'Publish the validated record batch',
  render: 'Recover the recorded views',
  inspect_batch: 'Inspect the record batch',
};
const batchLabels: Readonly<Record<string, string>> = {
  not_started: 'Batch not published',
  not_committed: 'Batch not committed',
  committed: 'Batch recorded',
  already_committed: 'Batch already recorded',
  committed_view_failed: 'Batch recorded; views need recovery',
  conflict: 'Batch publication conflict',
  unknown: 'Batch publication unavailable',
};
export function reviewFlowLabel(value: string): string {
  if (Object.hasOwn(closureActionLabels, value)) return knownLabel(closureActionLabels, value);
  if (Object.hasOwn(flowReasonLabels, value)) return knownLabel(flowReasonLabels, value);
  if (Object.hasOwn(reasonLabels, value)) return knownLabel(reasonLabels, value);
  if (Object.hasOwn(reviewerLoadActions, value)) return knownLabel(reviewerLoadActions, value);
  if (Object.hasOwn(reviewerLoadReasons, value)) return knownLabel(reviewerLoadReasons, value);
  return value;
}
function Duration({ seconds }: { readonly seconds: number }) {
  const i18n = useI18n();
  // Preserve measured zero and subsecond values; do not round an observed positive span to zero.
  const unit = seconds >= 3600 ? 3600 : seconds >= 60 ? 60 : 1;
  const label = unit === 3600 ? '{value} hr' : unit === 60 ? '{value} min' : '{value} sec';
  return (
    <span className="review-timing-unit">
      {i18n.t(label, {
        value: (seconds / unit).toLocaleString(i18n.localeName, { maximumSignificantDigits: 4 }),
      })}
    </span>
  );
}
function LastObserved({ age }: { readonly age: ObservedAge }) {
  const i18n = useI18n();
  return (
    <>
      <Duration seconds={age.seconds} /> ·{' '}
      {i18n.t('Last observed at {time}', { time: i18n.date(age.asOf) })}
    </>
  );
}
function IntervalValue({ interval }: { readonly interval: TimingInterval }) {
  const i18n = useI18n();
  if (interval.state === 'closed' && interval.closedSeconds !== null)
    return (
      <>
        <Duration seconds={interval.closedSeconds} /> · {i18n.t('Completed observation')}
      </>
    );
  if (interval.state === 'canonical_terminal_timing_unknown')
    return <>{i18n.t('Review ended; terminal time was not observed')}</>;
  if (interval.age) return <LastObserved age={interval.age} />;
  return (
    <>{i18n.t(interval.state === 'open' ? 'Elapsed time not observed' : 'Interval not assessed')}</>
  );
}
export function ReviewTimingLine({
  snapshot,
  taskId,
}: {
  readonly snapshot: Snapshot;
  readonly taskId: string;
}) {
  const i18n = useI18n(),
    timing = reviewTiming(snapshot);
  if (!timing)
    return snapshot.timing === undefined ? null : (
      <p className="muted">{i18n.t('Review timing is unavailable.')}</p>
    );
  const current = timing.current.find((row) => row.taskId === taskId);
  const intervals = timing.intervals.filter(
    (row) =>
      row.taskId === taskId &&
      row.kind === 'submission_to_start' &&
      row.applicability === 'current' &&
      (!current || row.handoffId === current.handoffId),
  );
  const measured = intervals.at(-1);
  if (measured?.closedSeconds !== null && measured?.state === 'closed')
    return (
      <p className="muted review-timing-line">
        {i18n.t('Submission observed to review started')}: <IntervalValue interval={measured} />
      </p>
    );
  if (current?.canonicalPhase === 'done' || current?.phase === 'terminal')
    return (
      <p className="muted review-timing-line">
        {i18n.t(
          timing.intervals.some(
            (row) =>
              row.taskId === taskId &&
              row.applicability === 'current' &&
              row.state === 'canonical_terminal_timing_unknown',
          )
            ? 'Review ended; terminal time was not observed'
            : 'Review ended; review wait was not measured',
        )}
      </p>
    );
  if (!current?.waitingSince)
    return current?.cause === 'legacy_history_unavailable' ? (
      <p className="muted review-timing-line">
        {i18n.t('Review activity has no retained observation')}
      </p>
    ) : null;
  return (
    <p className="muted review-timing-line">
      {i18n.t('Waiting since {time}', { time: i18n.date(current.waitingSince.observedAt) })}
      {current.age && (
        <>
          {' '}
          · <LastObserved age={current.age} />
        </>
      )}
      {!current.age && (
        <>
          {' '}
          · <span className="review-timing-unit">{i18n.t('Elapsed time not observed')}</span>
        </>
      )}
    </p>
  );
}
export function HandoffInline({
  snapshot,
  taskId,
}: {
  readonly snapshot: Snapshot;
  readonly taskId: string;
}) {
  const i18n = useI18n(),
    presentation = usePresentation();
  const handoff = taskHandoff(snapshot, taskId);
  if (!handoff)
    return taskHandoffUnavailable(snapshot, taskId) ? (
      <p className="muted">{i18n.t('Current review handoff is unavailable.')}</p>
    ) : null;
  return (
    <div className="review-handoff-inline">
      <p className="review-phase">
        <span>{i18n.t(knownLabel(reviewPhaseLabels, handoff.phase))}</span>
        {handoff.reviewerId && (
          <>
            {' '}
            · {i18n.t('Assigned reviewer')}: {presentation.actor(handoff.reviewerId)}
          </>
        )}
        {handoff.currentness !== 'current' && <> · {i18n.human(handoff.currentness)}</>}
      </p>
      {(handoff.nextActorId || handoff.nextAction) && (
        <p className="flow-next-action">
          {handoff.nextActorId && handoff.nextActorId !== handoff.reviewerId && (
            <>{presentation.actor(handoff.nextActorId)} · </>
          )}
          {i18n.t(reviewFlowLabel(handoff.nextAction))}
        </p>
      )}
      {handoff.nextTrigger && handoff.nextTrigger !== handoff.nextAction && (
        <p className="muted queue-trigger">
          {i18n.t('Next trigger')}: {i18n.t(reviewFlowLabel(handoff.nextTrigger))}
        </p>
      )}
      {handoff.reasons[0] && (
        <p className="lane-warning">{i18n.t(reviewFlowLabel(handoff.reasons[0]))}</p>
      )}
      <ReviewTimingLine snapshot={snapshot} taskId={taskId} />
    </div>
  );
}
export function ReviewFlow({
  snapshot,
  taskId,
}: {
  readonly snapshot: Snapshot;
  readonly taskId: string;
}) {
  const i18n = useI18n(),
    presentation = usePresentation();
  const handoffs = reviewHandoffs(snapshot),
    timing = reviewTiming(snapshot);
  const intervals = timing?.intervals.filter((row) => row.taskId === taskId) || [];
  const history = handoffs?.rows.filter((row) => row.taskId === taskId) || [];
  const current = taskHandoff(snapshot, taskId);
  const currentHandoffId = current?.id;
  if (
    !history.length &&
    !intervals.length &&
    snapshot.review_handoffs === undefined &&
    snapshot.timing === undefined &&
    !taskHandoffUnavailable(snapshot, taskId)
  )
    return null;
  return (
    <div className="review-flow">
      {snapshot.review_handoffs !== undefined && !handoffs && (
        <p className="lane-warning">{i18n.t('Review handoff is unavailable.')}</p>
      )}
      <HandoffInline snapshot={snapshot} taskId={taskId} />
      {!history.length && !current && <ReviewTimingLine snapshot={snapshot} taskId={taskId} />}
      {(history.length > 0 || intervals.length > 0 || (current?.milestones.length || 0) > 0) && (
        <details className="lane-details">
          <summary>{i18n.t('Observed review intervals')}</summary>
          <ol className="flow-details-list">
            {current?.milestones.slice(-50).map((milestone) => (
              <li key={milestone.id} data-milestone-id={milestone.id}>
                <strong>{i18n.t(knownLabel(milestoneLabels, milestone.kind))}</strong> ·{' '}
                {presentation.actor(milestone.actorId)}
                <p className="muted">
                  {milestone.observedAt
                    ? i18n.date(milestone.observedAt)
                    : i18n.t('Actual time not observed')}
                </p>
              </li>
            ))}
            {intervals.map((interval) => (
              <li key={interval.id} data-interval-id={interval.id}>
                <strong>
                  {i18n.t(
                    interval.boundaryLabel === 'recorded_block_to_resolution_history'
                      ? 'Recorded blocker history span'
                      : interval.boundaryLabel === 'recorded_submit_to_verdict_history'
                        ? 'Recorded submission-to-verdict history span'
                        : knownLabel(intervalLabels, interval.kind),
                  )}
                </strong>{' '}
                · <IntervalValue interval={interval} />
                <p className="muted">
                  {interval.attempt !== null && (
                    <>
                      {i18n.t('Attempt')} {i18n.number(interval.attempt)}
                    </>
                  )}
                  {interval.round !== null && (
                    <>
                      {' '}
                      · {i18n.t('Round')} {i18n.number(interval.round)}
                    </>
                  )}
                  {interval.applicability !== 'current' && (
                    <> · {i18n.human(interval.applicability)}</>
                  )}
                  {interval.outcome && interval.outcome !== 'unknown' && (
                    <> · {i18n.human(interval.outcome)}</>
                  )}
                </p>
                {(interval.start || interval.end) && (
                  <p className="muted">
                    {interval.start && (
                      <>
                        {i18n.t('Started')}: {i18n.date(interval.start.observedAt)}
                      </>
                    )}
                    {interval.end && (
                      <>
                        {' '}
                        · {i18n.t('Ended')}: {i18n.date(interval.end.observedAt)}
                      </>
                    )}
                  </p>
                )}
                {interval.reasons.map((reason) => (
                  <p key={reason} className="muted">
                    {i18n.t(reviewFlowLabel(reason))}
                  </p>
                ))}
              </li>
            ))}
            {history
              .filter((row) => row.id !== currentHandoffId)
              .sort((left, right) => left.sequence - right.sequence)
              .map((handoff) => (
                <li key={handoff.id}>
                  <span>{i18n.t(knownLabel(reviewPhaseLabels, handoff.phase))}</span>
                  {handoff.reviewerId && (
                    <>
                      {' '}
                      · {i18n.t('Assigned reviewer')}: {presentation.actor(handoff.reviewerId)}
                    </>
                  )}{' '}
                  · {i18n.t('Attempt')} {handoff.attempt} · {i18n.t('Round')} {handoff.round}
                  {handoff.nextActorId && <> · {presentation.actor(handoff.nextActorId)}</>}
                  {' · '}
                  {i18n.t('Historical handoff')}
                  {handoff.currentness !== 'current' && <> · {i18n.human(handoff.currentness)}</>}
                </li>
              ))}
          </ol>
          {((timing?.intervalOmitted || 0) > 0 ||
            (handoffs?.omitted || 0) > 0 ||
            (current?.milestones.length || 0) > 50) && (
            <p className="muted">
              {i18n.t('Some review observations are omitted; these details are partial.')}
            </p>
          )}
        </details>
      )}
      {!history.length &&
        !intervals.length &&
        ((timing?.intervalOmitted || 0) > 0 || (handoffs?.omitted || 0) > 0) && (
          <p className="muted">
            {i18n.t('Some review observations are omitted; these details are partial.')}
          </p>
        )}
    </div>
  );
}
export function ReviewBottleneck({
  snapshot,
  context = defaultContext,
}: {
  readonly snapshot: Snapshot;
  readonly context?: WorkContext;
}) {
  const i18n = useI18n(),
    timing = reviewTiming(snapshot);
  if (snapshot.timing === undefined) return null;
  if (!timing) return <p className="muted">{i18n.t('Review timing is unavailable.')}</p>;
  const cohort = timing.cohorts.find(
    (row) => row.id === (context.scope === 'current' ? 'current_objective' : 'all_retained'),
  );
  const metric = cohort?.summaries.find(
    (row) => row.kind === 'submission_to_start' && row.outcome === 'all_observed',
  );
  if (!metric || metric.relevant === 0) return null;
  return (
    <section className="review-bottleneck flow-summary" aria-label={i18n.t('Observed review wait')}>
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
        {i18n.t('Observed review wait')}
      </a>
      <p>
        {metric.median === null ? (
          i18n.t('No completed observations')
        ) : (
          <>
            {i18n.t('Median')}: <Duration seconds={metric.median} />
          </>
        )}
        {' · '}
        {i18n.t('{closed} / {total} measured', { closed: metric.closed, total: metric.relevant })}
        {metric.open > 0 && <> · {i18n.t('{count} open intervals', { count: metric.open })}</>}
        {metric.unknown > 0 && (
          <> · {i18n.t('{count} intervals not assessed', { count: metric.unknown })}</>
        )}
      </p>
      <p className="muted">
        {i18n.t('Submission observed to review started')} ·{' '}
        {i18n.t(context.scope === 'current' ? 'Current objective' : 'All retained work')} ·{' '}
        {i18n.t('All observed outcomes')}
      </p>
    </section>
  );
}
export function EvidencePreparationSummary({
  snapshot,
  taskId,
}: {
  readonly snapshot: Snapshot;
  readonly taskId?: string;
}) {
  const i18n = useI18n(),
    presentation = usePresentation();
  if (snapshot.evidence_preparation === undefined) return null;
  const summary = evidencePreparation(snapshot.evidence_preparation);
  if (!summary)
    return <p className="lane-warning">{i18n.t('Evidence preparation is unavailable.')}</p>;
  if (taskId && summary.taskId !== taskId) return null;
  const statusLabels: Readonly<Record<string, string>> = {
    ready: 'Evidence preparation ready',
    incomplete: 'Evidence preparation incomplete',
    stale: 'Evidence preparation is stale',
    unavailable: 'Evidence preparation is unavailable.',
  };
  const retained = summary.kind === 'retained_evidence';
  const title = retained ? 'Retained evidence' : 'Evidence preparation';
  return (
    <section className="flow-summary" aria-label={i18n.t(title)}>
      <div className="lane-heading">
        <strong>{i18n.t(title)}</strong>
        <span>
          {i18n.t(
            retained
              ? summary.status === 'ready'
                ? 'Retained evidence current'
                : 'Retained evidence needs attention'
              : knownLabel(statusLabels, summary.status),
          )}
        </span>
      </div>
      {(summary.nextActorId || summary.nextAction) && (
        <p className="flow-next-action">
          {summary.nextActorId && <>{presentation.actor(summary.nextActorId)} · </>}
          {i18n.t(reviewFlowLabel(summary.nextAction))}
        </p>
      )}
      {summary.reasons[0] &&
        !(
          summary.reasons[0] === 'missing_decision' &&
          summary.nextAction === 'Supply missing decisions'
        ) && <p className="lane-warning">{i18n.t(reviewFlowLabel(summary.reasons[0]))}</p>}
      <p className="muted">
        {i18n.t('Selected evidence: {selected} · Retained attachments: {retained}', {
          retained: summary.counts.retained || 0,
          selected: summary.counts.selected || 0,
        })}
        {summary.counts.unresolved ? (
          <> · {i18n.t('{count} unresolved', { count: summary.counts.unresolved })}</>
        ) : null}
      </p>
      <details className="lane-details">
        <summary>{i18n.t('Preparation details')}</summary>
        <p>
          {i18n.t('{ready} / {planned} attachments ready', {
            ready: summary.counts.attachments_ready || 0,
            planned: summary.counts.attachments_planned || 0,
          })}
        </p>
        {summary.affectedChecks.length > 0 && (
          <p>
            {i18n.t('Affected checks')}: {summary.affectedChecks.join(', ')}
          </p>
        )}
        {summary.reasons
          .slice(
            summary.reasons[0] === 'missing_decision' &&
              summary.nextAction === 'Supply missing decisions'
              ? 0
              : 1,
          )
          .map((reason) => (
            <p key={reason}>{i18n.t(reviewFlowLabel(reason))}</p>
          ))}
      </details>
    </section>
  );
}
export function BatchSummary({
  snapshot,
  taskId,
}: {
  readonly snapshot: Snapshot;
  readonly taskId?: string;
}) {
  const i18n = useI18n(),
    presentation = usePresentation();
  if (snapshot.record_batch === undefined) return null;
  const batch = recordBatch(snapshot.record_batch);
  if (!batch) return <p className="lane-warning">{i18n.t('Record batch is unavailable.')}</p>;
  if (taskId && !batch.operations.some((operation) => operation.taskId === taskId)) return null;
  return (
    <section className="flow-summary record-batch" aria-label={i18n.t('Record batch')}>
      <div className="lane-heading">
        <strong>{i18n.t('Record batch')}</strong>
        <span>{i18n.t(knownLabel(batchLabels, batch.publication))}</span>
        {batch.applicability !== 'current' && (
          <span className="muted">
            {batch.applicability === 'unknown'
              ? i18n.t('Current applicability not assessed')
              : i18n.human(batch.applicability)}
          </span>
        )}
      </div>
      {(batch.nextActorId || batch.nextAction) && (
        <p className="flow-next-action">
          {batch.nextActorId && <>{presentation.actor(batch.nextActorId)} · </>}
          {i18n.t(knownLabel(batchActions, batch.nextAction))}
        </p>
      )}
      {batch.reason &&
        ![
          'validated_not_published',
          'exact_commit',
          'recorded_marker_only',
          'recorded_publication',
        ].includes(batch.reason) && (
          <p className="lane-warning">
            {i18n.t(
              Object.hasOwn(batchReasonLabels, batch.reason)
                ? knownLabel(batchReasonLabels, batch.reason)
                : 'Batch needs inspection',
            )}
          </p>
        )}
      <p className="muted">
        {batch.committed !== null && batch.requested !== null
          ? i18n.t('{committed} / {requested} operations recorded', {
              committed: batch.committed,
              requested: batch.requested,
            })
          : i18n.t('Operation counts unavailable')}
      </p>
      <details className="lane-details">
        <summary>{i18n.t('Batch details')}</summary>
        {batch.recordedPublication && (
          <p>
            {i18n.t('Recorded publication outcome')}:{' '}
            {i18n.t(knownLabel(batchLabels, batch.recordedPublication))}
          </p>
        )}
        <p>
          {i18n.t('Preparation')}: {i18n.human(batch.preparation)} ·{' '}
          {batch.validated === null
            ? i18n.t('Validation count not observed')
            : i18n.t('{count} operations validated', { count: batch.validated })}
        </p>
        {batch.reason && (
          <p>
            {i18n.t(
              Object.hasOwn(batchReasonLabels, batch.reason)
                ? knownLabel(batchReasonLabels, batch.reason)
                : 'Batch needs inspection',
            )}
          </p>
        )}
        <ol className="flow-details-list">
          {batch.operations.map((operation) => (
            <li key={operation.id}>
              {i18n.t(
                operation.operation === 'review-handoff-record'
                  ? 'Record review handoff'
                  : knownLabel(recordOperationLabels, operation.operation),
              )}{' '}
              ·{' '}
              {operation.status === 'not_assessed'
                ? i18n.t('Operation not assessed')
                : i18n.human(operation.status)}
              {operation.taskId && <> · {presentation.taskReference(operation.taskId)}</>}
            </li>
          ))}
        </ol>
        {batch.omitted !== null && batch.omitted > 0 && (
          <p className="muted">{i18n.t('{count} operations omitted.', { count: batch.omitted })}</p>
        )}
        {batch.omitted === null && (
          <p className="muted">{i18n.t('Operation list scope unavailable')}</p>
        )}
      </details>
    </section>
  );
}

const milestoneLabels: Readonly<Record<string, string>> = {
  dispatch_claimed: 'Review dispatch reserved',
  dispatch_sent: 'Review request sent',
  dispatch_failed: 'Review request could not be sent',
  dispatch_unknown: 'Review send outcome needs confirmation',
  reviewer_acknowledged: 'Reviewer acknowledged the request',
  review_started: 'Review start observed',
};
const reviewerLoadActions: Readonly<Record<string, string>> = {
  dispatch_exact: 'Send to the reviewer',
  observe_runtime: 'Confirm reviewer availability',
  choose_reviewer: 'Choose an eligible reviewer',
  reconcile_delivery: 'Confirm delivery before resending',
  await_reviewer: 'Wait for the reviewer',
  await_capacity: 'Wait for review capacity',
  revalidate_subject: 'Recheck the review inputs',
};
const reviewerLoadReasons: Readonly<Record<string, string>> = {
  policy_missing: 'No review limit recorded',
  runtime_capacity_unknown: 'Live capacity not observed',
  runtime_capacity_unavailable: 'Observed review capacity unavailable',
  contact_unverified: 'Reviewer contact needs confirmation',
  wip_limit_reached: 'Recorded review limit reached',
  prior_dispatch_unresolved: 'An earlier send needs confirmation',
  subject_stale: 'Review inputs changed',
  reviewer_ineligible: 'Reviewer eligibility needs attention',
  already_inflight: 'Review work is already in progress',
};
export function ReviewerLoadInline({
  snapshot,
  actorId,
}: {
  readonly snapshot: Snapshot;
  readonly actorId: string;
}) {
  const i18n = useI18n();
  if (snapshot.reviewer_load === undefined) return null;
  const projection = reviewerLoads(snapshot);
  if (!projection)
    return <p className="lane-warning">{i18n.t('Reviewer workload is unavailable.')}</p>;
  const row = projection.rows.find((row) => row.actorId === actorId);
  if (!row)
    return projection.omitted > 0 ? (
      <p className="muted">{i18n.t('Reviewer workload is outside the shown records.')}</p>
    ) : null;
  return (
    <div className="reviewer-load">
      <p>
        {i18n.t('Recorded review work: {count}', { count: row.occupied })}
        {row.policyLimit !== null && (
          <> · {i18n.t('Policy limit: {count}', { count: row.policyLimit })}</>
        )}
      </p>
      {row.nextAction && (
        <p className="flow-next-action">
          {i18n.t(
            Object.hasOwn(reviewerLoadActions, row.nextAction)
              ? knownLabel(reviewerLoadActions, row.nextAction)
              : 'Inspect reviewer workload',
          )}
        </p>
      )}
      <p className="muted">
        {row.availableSlots === null
          ? i18n.t('Live capacity not observed')
          : i18n.t('Observed available review slots: {count}', { count: row.availableSlots })}
        {row.observedAt && <> · {i18n.date(row.observedAt)}</>}
      </p>
      <details className="lane-details">
        <summary>{i18n.t('Review workload details')}</summary>
        <p>
          {i18n.t('Reserved: {reserved} · Sent, not started: {sent} · Started: {started}', {
            reserved: row.pendingDispatch,
            sent: row.sentUnstarted,
            started: row.started,
          })}
        </p>
        <p>
          {i18n.t('Uncertain sends: {count} · Earlier work still open: {earlier}', {
            count: row.ambiguous,
            earlier: row.supersededNonterminal,
          })}
        </p>
        {row.policyHeadroom !== null && (
          <p>
            {i18n.t('Policy headroom: {count}; live availability is separate.', {
              count: row.policyHeadroom,
            })}
          </p>
        )}
        {row.reasons
          .filter((reason) => reason !== 'runtime_capacity_unknown')
          .map((reason) => (
            <p key={reason}>
              {i18n.t(
                Object.hasOwn(reviewerLoadReasons, reason)
                  ? knownLabel(reviewerLoadReasons, reason)
                  : 'Inspect reviewer workload',
              )}
            </p>
          ))}
      </details>
    </div>
  );
}

export function ReviewerLoadSummary({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n(),
    presentation = usePresentation();
  if (snapshot.reviewer_load === undefined) return null;
  const projection = reviewerLoads(snapshot);
  if (!projection)
    return <p className="lane-warning">{i18n.t('Reviewer workload is unavailable.')}</p>;
  const actionable = projection.rows.filter(
    (row) =>
      row.occupied > 0 ||
      row.reasons.some((reason) =>
        ['wip_limit_reached', 'subject_stale', 'reviewer_ineligible'].includes(reason),
      ),
  );
  return (
    <>
      {actionable.length > 0 && (
        <ul className="reviewer-load-list">
          {actionable.slice(0, 5).map((row) => (
            <li key={row.actorId}>
              <strong>{presentation.actor(row.actorId)}</strong>
              <ReviewerLoadInline snapshot={snapshot} actorId={row.actorId} />
            </li>
          ))}
        </ul>
      )}
      {projection.omitted > 0 && (
        <p className="muted">
          {i18n.t('{count} reviewer workload records omitted.', { count: projection.omitted })}
        </p>
      )}
      {actionable.length > 5 && (
        <p className="muted">
          {i18n.t('{shown} / {total} supplied reviewers shown.', {
            shown: 5,
            total: actionable.length,
          })}
        </p>
      )}
    </>
  );
}
