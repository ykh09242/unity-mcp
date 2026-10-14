import { useI18n } from './i18n';
import { usePresentation } from './presentation-ui';
import { items, record, records, safeUrl, string } from './model';
import type { Json, JsonRecord, Snapshot, Task } from './model';
import { deliveryHistory, latestReview, priorParticipants, revisionMismatch } from './history';

const fieldLabels: Readonly<Record<string, string>> = {
  scope: 'Scope',
  checks: 'Checks',
  unresolved_findings: 'Findings',
  review: 'Review',
  model_id: 'Model',
  reasoning_effort: 'Reasoning',
  role: 'Role',
  questions: 'Questions',
  dependencies: 'Dependencies',
  task_ids: 'Tasks',
  acceptance: 'Acceptance criteria',
  status: 'Status',
  id: 'ID',
  name: 'Name',
  reason: 'Reason',
  command: 'Command',
  execution_kind: 'Execution',
  severity: 'Severity',
  message: 'Message',
  criterion: 'Criterion',
  result: 'Result',
};
const enumFields = new Set([
  'Status',
  'Delivery',
  'Merge',
  'Cleanup',
  'Reviewer role',
  'Role',
  'Execution',
]);

const semanticFields = new Set([
  'criterion',
  'description',
  'purpose',
  'method',
  'goal',
  'title',
  'note',
  'constraints',
  'decisions',
  'details',
  'expected',
  'actual',
  'status',
  'result',
  'reason',
  'summary',
  'message',
  'name',
  'severity',
  'conclusion',
  'expected_result',
  'acceptance_criteria',
  'findings',
  'unresolved_findings',
  'unresolved_acceptance',
  'unresolved_blockers',
  'limitations',
  'next_action',
  'text',
  'checks',
]);

export function ContentValue({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  const fields =
    value !== null && typeof value === 'object' && !Array.isArray(value)
      ? Object.entries(value).filter(([key]) => semanticFields.has(key))
      : [];
  return Array.isArray(value) ? (
    value.length ? (
      <ul className="history-prose">
        {value.map((item, index) => (
          <li key={index}>
            <ContentValue value={item} />
          </li>
        ))}
      </ul>
    ) : (
      <span className="muted">{i18n.t('None')}</span>
    )
  ) : value !== null && typeof value === 'object' ? (
    fields.length ? (
      <dl className="history-object">
        {fields.map(([key, item]) => (
          <div key={key}>
            <dt>{i18n.t(fieldLabels[key] || key)}</dt>
            <dd>
              <ContentValue value={item} />
            </dd>
          </div>
        ))}
      </dl>
    ) : (
      <span className="muted">{i18n.t('This content cannot be displayed.')}</span>
    )
  ) : (
    <span className="history-text">{i18n.display(value)}</span>
  );
}

function hasValue(value: Json | undefined): boolean {
  if (value === undefined || value === null) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return true;
}

function Facts({
  values,
  optional = false,
}: {
  readonly values: readonly (readonly [string, Json | undefined])[];
  readonly optional?: boolean;
}) {
  const i18n = useI18n(),
    presentation = usePresentation();
  const rows = optional ? values.filter(([, value]) => hasValue(value)) : values;
  return rows.length ? (
    <dl className="history-facts">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{i18n.t(label)}</dt>
          <dd>
            {enumFields.has(label) ? (
              i18n.human(value)
            ) : (
              <ContentValue value={presentation.field(label, value)} />
            )}
          </dd>
        </div>
      ))}
    </dl>
  ) : null;
}

function CheckRows({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  const checks = items(value);
  return checks.length ? (
    <ul className="history-events">
      {checks.map((original, index) => {
        const check = record(original);
        return (
          <li key={index}>
            <div className="history-event-heading">
              <strong>{i18n.display(check.id || check.name)}</strong>
              <span className="badge">{i18n.human(check.status)}</span>
            </div>
            <Facts
              optional
              values={[
                ['Execution', check.execution_kind],
                ['Command', check.command],
                ['Result', check.result],
                ['Reason', check.reason],
              ]}
            />
          </li>
        );
      })}
    </ul>
  ) : (
    <p className="muted">{i18n.t('No check results recorded')}</p>
  );
}

export function CoverageObservations({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  return (
    <div>
      {records(value).map((entry, index) => {
        const facts = record(entry.facts);
        const values: readonly (readonly [string, Json | undefined])[] = [
          ['Task', entry.task_id],
          ['Verdict', facts.verdict],
          ['Status', facts.status],
          ['Findings', facts.findings],
          ['Unresolved findings', facts.unresolved_findings],
          ['Acceptance criteria', facts.criteria_results || facts.acceptance_evidence],
          ['Unresolved acceptance', facts.unresolved_acceptance],
          ['Unresolved blockers', facts.unresolved_blockers],
          ['Acceptance complete', facts.acceptance_complete],
          ['Limitations', facts.limitations],
        ];
        return (
          <article key={index}>
            <Facts values={values.filter(([, field]) => field !== undefined)} />
            {facts.checks !== undefined && (
              <>
                <h5>{i18n.t('Checks')}</h5>
                <CheckRows value={facts.checks} />
              </>
            )}
          </article>
        );
      })}
    </div>
  );
}

function ReviewRound({
  round,
  attempt,
}: {
  readonly round: JsonRecord;
  readonly attempt: JsonRecord;
}) {
  const i18n = useI18n();
  const receipt = record(round.receipt_record);
  const review = record(round.review_record);
  return (
    <article className="history-round">
      <div className="history-event-heading">
        <h5>
          {i18n.t('Round')} {i18n.display(round.round)}
        </h5>
        <span className="badge">{i18n.human(review.verdict)}</span>
      </div>
      <Facts
        values={[
          ['Implementer', attempt.owner],
          ['Reviewer', review.reviewer],
          ['Reviewer role', review.reviewer_role],
          ['Next action', review.next_action || receipt.next_action],
        ]}
      />

      <section className="history-block">
        <h5>{i18n.t('Checks')}</h5>
        <CheckRows value={receipt.checks} />
      </section>
      {(hasValue(review.unresolved_findings) ||
        hasValue(review.reason) ||
        hasValue(receipt.unresolved_acceptance) ||
        hasValue(receipt.limitations) ||
        items(receipt.acceptance_evidence).length > 0) && (
        <section className="history-block">
          <h5>{i18n.t('Findings and acceptance')}</h5>
          <Facts
            optional
            values={[
              ['Findings', review.unresolved_findings],
              ['Reason', review.reason],
              ['Unresolved acceptance', receipt.unresolved_acceptance],
              ['Limitations', receipt.limitations],
            ]}
          />
          <ul className="history-events">
            {items(receipt.acceptance_evidence).map((original, index) => {
              const criterion = record(original);
              return (
                <li key={index}>
                  <ContentValue value={criterion.criterion} />
                  <p className="muted">{i18n.human(criterion.status)}</p>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </article>
  );
}

export function ReviewSummary({
  snapshot,
  task,
}: {
  readonly snapshot: Snapshot;
  readonly task: Task;
}) {
  const i18n = useI18n();
  const presentation = usePresentation();
  const { attempt, round, review, receipt } = latestReview(task);
  const integrated = record(record(snapshot.coverage).integrated_review);
  const submission = record(record(task.readiness).submission);
  if (!Object.keys(attempt).length && task.status !== 'review') return null;
  return (
    <section className="history-section current-review">
      <div className="history-event-heading">
        <h3>{i18n.t('Review')}</h3>
        <span className="badge">{i18n.human(review.verdict || submission.status)}</span>
      </div>
      <p className="history-meta">
        {i18n.t('Attempt')} {i18n.display(attempt.number)} · {i18n.t('Round')}{' '}
        {i18n.display(round.round)} ·{' '}
        {i18n.display(presentation.actor(review.reviewer), 'Reviewer not recorded')}
      </p>
      {(review.next_action || receipt.next_action) && (
        <p className="history-text">{i18n.display(review.next_action || receipt.next_action)}</p>
      )}
      <details>
        <summary>{i18n.t('Review context')}</summary>
        <Facts
          values={[
            ['Task', task.id],
            ['Implementer', attempt.owner],
            ['Reviewer', review.reviewer],
            ['Reviewer role', review.reviewer_role],
            ['Director', record(snapshot.director).actor_id],
            ['Recorded participants', priorParticipants(task)],
            ['Findings', review.unresolved_findings],
          ]}
        />
        <h4>{i18n.t('Integrated review')}</h4>
        <Facts
          values={[
            ['Status', integrated.status],
            ['Prerequisites', integrated.requires],
            ['Limitations', integrated.limitations],
          ]}
        />
      </details>
    </section>
  );
}

export function TaskHistory({ task }: { readonly snapshot: Snapshot; readonly task: Task }) {
  const i18n = useI18n();
  const presentation = usePresentation();
  const attempts = records(task.attempts);
  return (
    <section className="history-section">
      <details>
        <summary>
          {i18n.t('Attempt and review history')}{' '}
          <span className="muted">{i18n.number(attempts.length)}</span>
        </summary>

        {attempts.length ? (
          <ol className="history-events">
            {attempts.map((attempt, index) => (
              <li key={string(attempt.number) || index}>
                <article className="history-attempt">
                  <div className="history-event-heading">
                    <h4>
                      {i18n.t('Attempt')} {i18n.display(attempt.number)} ·{' '}
                      {i18n.display(presentation.actor(attempt.owner))}
                    </h4>
                    <span className="badge">{i18n.human(attempt.status || attempt.outcome)}</span>
                  </div>
                  {records(attempt.rounds).map((round, roundIndex) => (
                    <ReviewRound
                      key={string(round.round) || roundIndex}
                      round={round}
                      attempt={attempt}
                    />
                  ))}
                  {!records(attempt.rounds).length && (
                    <p className="muted">{i18n.t('No review rounds recorded')}</p>
                  )}
                </article>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">{i18n.t('No attempts recorded.')}</p>
        )}
      </details>
    </section>
  );
}

function External({ value, label }: { readonly value: Json | undefined; readonly label: string }) {
  const url = safeUrl(value);
  return url ? (
    <a href={url} target="_blank" rel="noreferrer">
      {label}
    </a>
  ) : (
    <span>{label}</span>
  );
}

function DeliveryRecord({
  value,
  task,
}: {
  readonly value: Json | undefined;
  readonly task: Task;
}) {
  const i18n = useI18n();
  const delivery = record(value),
    meta = record(delivery.metadata),
    cleanup = record(meta.cleanup);
  const { attempt, round } = latestReview(task);
  const ci = records(meta.ci);
  const missingChecks = items(meta.required_ci).filter(
    (name) => !ci.some((check) => check.name === name),
  );
  return (
    <div className="delivery-record">
      <Facts
        values={[
          ['Delivery', delivery.status],
          ['Merge', meta.merge_status],
          ['Cleanup', cleanup.status],
        ]}
      />
      {meta.pr_url && <External value={meta.pr_url} label={i18n.t('Open PR')} />}
      {(revisionMismatch(meta.reviewed_source_digest, round.source_digest) ||
        (typeof meta.attempt === 'number' &&
          typeof attempt.number === 'number' &&
          meta.attempt !== attempt.number)) && (
        <p className="muted">
          {i18n.t('This delivery belongs to a different attempt or reviewed source')}
        </p>
      )}
      <section className="history-block">
        <h5>
          {i18n.t('CI checks')} <span className="muted">{i18n.number(ci.length)}</span>
        </h5>
        <Facts optional values={[['Required CI', meta.required_ci]]} />
        {missingChecks.length > 0 && (
          <Facts values={[['Required checks without observations', missingChecks]]} />
        )}
        {ci.length ? (
          <ul className="history-events">
            {ci.map((check, index) => (
              <li key={index}>
                <div className="history-event-heading">
                  <External value={check.url} label={i18n.display(check.name)} />
                  <span className="badge">{i18n.human(check.conclusion)}</span>
                </div>
                {revisionMismatch(check.commit, meta.tested_commit) && (
                  <p className="error">{i18n.t('Check revision differs from tested commit')}</p>
                )}
                <Facts
                  optional
                  values={[
                    ['Run', check.run_id],
                    ['Job', check.job_id],
                    ['Observed at', check.observed_at],
                    ['Started', check.started_at],
                    ['Completed', check.completed_at],
                  ]}
                />
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{i18n.t('No CI observations recorded')}</p>
        )}
      </section>
      {Object.keys(cleanup).length > 0 && (
        <section className="history-block">
          <h5>{i18n.t('Cleanup details')}</h5>
          <Facts values={[['Status', cleanup.status]]} />
          <Facts
            optional
            values={[
              ['Reason', cleanup.reason],
              ['Actor', cleanup.actor_id],
              ['Recorded at', cleanup.recorded_at],
            ]}
          />
        </section>
      )}
    </div>
  );
}

export function DeliveryHistory({ task }: { readonly task: Task }) {
  const i18n = useI18n(),
    history = deliveryHistory(task);
  return (
    <section className="history-section">
      <details>
        <summary>{i18n.t('Recorded PR / CI')}</summary>
        <p className="muted">
          {i18n.t(
            'These are saved observations, not live provider checks. A success conclusion does not establish that every test executed.',
          )}
        </p>
        <section className="history-block">
          <h4>
            {i18n.t('Current delivery')} · {i18n.human(record(task.delivery).status)}
          </h4>
          <DeliveryRecord value={task.delivery} task={task} />
        </section>
        <section className="history-block">
          <h4>
            {i18n.t('Delivery history')}{' '}
            <span className="muted">
              {history.supplied ? i18n.number(history.entries.length) : i18n.t('Unavailable')}
            </span>
          </h4>
          {history.entries.length ? (
            <ol className="history-events">
              {history.entries.map((entry) => (
                <li key={entry.index}>
                  <article>
                    <div className="history-event-heading">
                      <h5>
                        {i18n.number(entry.index + 1)} · {i18n.human(record(entry.record).status)}
                      </h5>
                      <span className="muted">
                        {entry.time_status === 'recorded'
                          ? i18n.date(entry.recorded_at)
                          : i18n.t('Time unknown')}
                      </span>
                    </div>
                    <DeliveryRecord value={entry.record} task={task} />
                  </article>
                </li>
              ))}
            </ol>
          ) : (
            <p className="muted">
              {i18n.t(
                history.supplied && history.status === 'unrecorded'
                  ? 'No delivery history recorded'
                  : 'Delivery history unavailable',
              )}
            </p>
          )}
        </section>
      </details>
    </section>
  );
}

export function StaffingHistory({
  actor,
  assignment,
  disclosure = true,
}: {
  readonly actor: JsonRecord;
  readonly assignment?: JsonRecord;
  readonly disclosure?: boolean;
}) {
  const i18n = useI18n();
  const presentation = usePresentation();
  const Container = disclosure ? 'details' : 'section';
  return (
    <Container className="history-section">
      {disclosure ? (
        <summary>{i18n.t('Staffing and assignment history')}</summary>
      ) : (
        <h4>{i18n.t('Staffing and assignment history')}</h4>
      )}

      <ol className="history-events">
        {items(actor.history).map((original, index) => {
          const event = record(original);
          return (
            <li key={index}>
              <div className="history-event-heading">
                <strong>{i18n.human(event.decision || event.action)}</strong>
                <span>{i18n.date(event.recorded_at || event.timestamp)}</span>
              </div>
              <Facts
                values={[
                  ['Reason', event.reason],
                  ['Model', i18n.personnel('model_id', record(event.metadata).model_id)],
                  [
                    'Reasoning',
                    i18n.personnel('reasoning_effort', record(event.metadata).reasoning_effort),
                  ],
                  ['Previous actor', event.predecessor_id],
                  ['Next actor', event.successor_id],
                ]}
              />
            </li>
          );
        })}
      </ol>
      {!items(actor.history).length && (
        <p className="muted">{i18n.t('No staffing history recorded')}</p>
      )}
      {!assignment &&
        records(actor.assignments).map((entry, index) => (
          <section className="history-block" key={index}>
            <h4>
              {i18n.display(presentation.taskReference(entry.task_id))} · {i18n.t('Attempt')}{' '}
              {i18n.display(entry.attempt)} · {i18n.statusLabel(entry.status)}
            </h4>
            <Facts
              values={[
                ['Model', i18n.personnel('model_id', record(entry.metadata).model_id)],
                [
                  'Reasoning',
                  i18n.personnel('reasoning_effort', record(entry.metadata).reasoning_effort),
                ],
                ['Role', record(entry.metadata).role],
              ]}
            />
          </section>
        ))}
    </Container>
  );
}

export function TransferHistory({ handoff }: { readonly handoff: JsonRecord }) {
  const i18n = useI18n();
  const stages = [
    ['Outgoing', handoff.outgoing, handoff.outgoing_ref, handoff.created_at],
    ...(records(handoff.acknowledgements).length
      ? records(handoff.acknowledgements).map(
          (ack) => ['Acknowledged', ack.receipt, ack.ref, ack.timestamp] as const,
        )
      : [
          [
            'Acknowledged',
            handoff.acknowledgement,
            handoff.ack_ref,
            handoff.acknowledged_at,
          ] as const,
        ]),
    ['Accepted', handoff.acceptance, handoff.accept_ref, handoff.accepted_at],
  ] as const;
  return (
    <details className="history-section">
      <summary>{i18n.t('Transfer history')}</summary>
      <p className="muted">
        {i18n.t('A pending handoff does not assign or activate the successor.')}
      </p>
      <Facts values={[['Successor', handoff.to_actor ?? record(handoff.outgoing).to_actor]]} />
      <ol className="history-events">
        {stages.map(([label, value, _reference, time], index) => {
          const event = record(value);
          return (
            <li key={index}>
              <div className="history-event-heading">
                <strong>{i18n.t(label)}</strong>
                <span>{value ? i18n.date(time) : i18n.t('Not recorded')}</span>
              </div>
              {value && (
                <>
                  <Facts
                    values={[
                      [
                        'Actor',
                        label === 'Outgoing'
                          ? (event.from_actor ?? handoff.from_actor)
                          : event.actor_id,
                      ],
                      ['Next action', event.next_action],
                      ['Summary', event.summary || event.goal],
                    ]}
                  />
                </>
              )}
            </li>
          );
        })}
      </ol>
    </details>
  );
}
