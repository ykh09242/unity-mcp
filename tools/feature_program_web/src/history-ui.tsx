import { useState } from 'react';
import { useI18n } from './i18n';
import { items, record, records, safeUrl, string } from './model';
import type { Json, JsonRecord, Snapshot, Task } from './model';
import { OriginalRecord } from './readiness-ui';
import {
  attemptFacts,
  deliveryHistory,
  historyComparison,
  latestReview,
  priorParticipants,
  revisionMismatch,
  taskCoverage,
} from './history';

const fieldLabels: Readonly<Record<string, string>> = {
  scope: 'Scope',
  claimed_head: 'Claimed revision',
  submitted_head: 'Submitted revision',
  source_head: 'Head revision',
  source_digest: 'Source identity',
  checks: 'Checks',
  unresolved_findings: 'Findings',
  review: 'Review',
  model_id: 'Model',
  reasoning_effort: 'Reasoning',
  provenance: 'Provenance',
  role: 'Role',
  questions: 'Questions',
  dependencies: 'Dependencies',
  objective_digest: 'Objective identity',
  planning_digest: 'Planning identity',
  task_definition_digest: 'Task definition identity',
  task_ids: 'Tasks',
  candidate_paths: 'Candidate paths',
  acceptance: 'Acceptance criteria',
  status: 'Status',
  id: 'ID',
  name: 'Name',
  reason: 'Reason',
  command: 'Command',
  execution_kind: 'Execution',
  evidence: 'Evidence',
  path: 'Path',
  sha256: 'SHA-256',
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
  'Provenance',
]);

function Text({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  return Array.isArray(value) ? (
    value.length ? (
      <ul className="history-prose">
        {value.map((item, index) => (
          <li key={index}>
            <Text value={item} />
          </li>
        ))}
      </ul>
    ) : (
      <span className="muted">{i18n.t('None')}</span>
    )
  ) : value !== null && typeof value === 'object' ? (
    <dl className="history-object">
      {Object.entries(value).map(([key, item]) => (
        <div key={key}>
          <dt>{i18n.t(fieldLabels[key] || key)}</dt>
          <dd>
            <Text value={item} />
          </dd>
        </div>
      ))}
    </dl>
  ) : (
    <span className="history-text">{i18n.display(value)}</span>
  );
}

function Facts({ values }: { readonly values: readonly (readonly [string, Json | undefined])[] }) {
  const i18n = useI18n();
  return (
    <dl className="history-facts">
      {values.map(([label, value]) => (
        <div key={label}>
          <dt>{i18n.t(label)}</dt>
          <dd>{enumFields.has(label) ? i18n.human(value) : <Text value={value} />}</dd>
        </div>
      ))}
    </dl>
  );
}

export function EvidenceReferences({
  references,
  path,
}: {
  readonly references: readonly JsonRecord[];
  readonly path: string;
}) {
  const i18n = useI18n();
  const labels: Readonly<Record<string, string>> = {
    sha256: 'SHA-256',
    revision: 'Revision',
    pointer: 'Record location',
    scope: 'Scope',
    source_digest: 'Source identity',
    source_head: 'Head revision',
    task_id: 'Task',
    attempt: 'Attempt',
    status: 'Status',
    origin: 'Origin',
    recorded_at: 'Recorded at',
    reviewed_source_digest: 'Reviewed source',
  };
  return (
    <div className="evidence-records">
      <details>
        <summary>{i18n.t('About these records')}</summary>
        <p className="muted">
          {i18n.t('Supplied evidence references. Files are not opened or verified by this view.')}
        </p>
      </details>
      {references.map((reference, index) => (
        <article className="history-round" key={index}>
          {reference.label && reference.label !== path && <h3>{i18n.display(reference.label)}</h3>}
          {references.length > 1 && (
            <p className="history-meta">{i18n.t('Reference {number}', { number: index + 1 })}</p>
          )}
          <Facts
            values={Object.entries(reference)
              .filter(([key]) => key !== 'path' && key !== 'label')
              .map(([key, value]) => [labels[key] || fieldLabels[key] || key, value] as const)}
          />
          <OriginalRecord value={reference} label="Original evidence reference" />
        </article>
      ))}
    </div>
  );
}

export function HistoryReferences({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  const [expanded, setExpanded] = useState(false);
  const references = Array.isArray(value) ? value : value ? [value] : [];
  return references.length ? (
    <>
      <ul className="history-references">
        {(expanded ? references : references.slice(0, 5)).map((original, index) => {
          const ref = record(original);
          const path = typeof original === 'string' ? original : string(ref.path);
          return (
            <li key={index}>
              {path.startsWith('evidence/') ? (
                <a href={'#evidence/' + encodeURIComponent(path)}>{string(ref.label) || path}</a>
              ) : (
                <span>{path || i18n.t('Unknown')}</span>
              )}
              {ref.sha256 && <code>{string(ref.sha256)}</code>}
              {ref.pointer && <code>{string(ref.pointer)}</code>}
              {ref.revision && <code>{string(ref.revision)}</code>}
              {typeof original === 'object' && (
                <OriginalRecord value={original} label="Reference details" />
              )}
            </li>
          );
        })}
      </ul>
      {references.length > 5 && (
        <button
          type="button"
          className="secondary"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {i18n.t(expanded ? 'Show fewer references' : 'Show all {count} references', {
            count: references.length,
          })}
        </button>
      )}
    </>
  ) : (
    <p className="muted">{i18n.t('No evidence recorded')}</p>
  );
}

function Compare({
  before,
  after,
  fields,
  labels = ['Previous', 'Current'],
}: {
  readonly before: JsonRecord;
  readonly after: JsonRecord;
  readonly fields: readonly string[];
  readonly labels?: readonly [string, string];
}) {
  const i18n = useI18n();
  return (
    <div className="history-comparison">
      {historyComparison(before, after, fields).map((row) => (
        <section key={row.field}>
          <h5>
            {i18n.t(fieldLabels[row.field] || row.field)}{' '}
            <span className="muted">
              {i18n.t(
                row.status === 'same'
                  ? 'Unchanged'
                  : row.status === 'different'
                    ? 'Different'
                    : 'Unknown',
              )}
            </span>
          </h5>
          <div>
            {(
              [
                ['before', row.before],
                ['after', row.after],
              ] as const
            ).map(([key, value], index) => (
              <div key={key}>
                <h6>{i18n.t(labels[index] || '')}</h6>
                <Text value={value} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
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
            <details>
              <summary>{i18n.t('Check details')}</summary>
              <Facts
                values={[
                  ['Execution', check.execution_kind],
                  ['Command', check.command],
                  ['Result', check.result],
                  ['Reason', check.reason],
                ]}
              />
              <HistoryReferences value={check.evidence} />
              <OriginalRecord value={original} />
            </details>
          </li>
        );
      })}
    </ul>
  ) : (
    <p className="muted">{i18n.t('No check results recorded')}</p>
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
        <h4>
          {i18n.t('Round')} {i18n.display(round.round)}
        </h4>
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
      <details>
        <summary>{i18n.t('Submitted revision and scope')}</summary>
        <Facts
          values={[
            ['Submitted revision', round.submitted_head],
            ['Source identity', round.source_digest],
            ['Scope', attempt.scope],
            ['Changed paths', receipt.changed_paths],
            ['Reviewed source', review.source_digest],
            ['Reviewed receipt', review.receipt_sha256],
          ]}
        />
      </details>
      <details>
        <summary>{i18n.t('Checks')}</summary>
        <CheckRows value={receipt.checks} />
      </details>
      <details>
        <summary>{i18n.t('Findings and acceptance')}</summary>
        <Facts
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
                <Text value={criterion.criterion} />
                <p className="muted">{i18n.human(criterion.status)}</p>
                <HistoryReferences value={criterion.evidence} />
                <OriginalRecord value={original} />
              </li>
            );
          })}
        </ul>
      </details>
      <details>
        <summary>{i18n.t('Evidence')}</summary>
        <HistoryReferences
          value={[round.receipt ?? null, round.review ?? null].filter((value) => value !== null)}
        />
        <HistoryReferences value={round.evidence} />
      </details>
      <OriginalRecord value={round} label="Original round" />
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
        {i18n.display(round.round)} · {i18n.display(review.reviewer, 'Reviewer not recorded')}
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
            ['Submitted revision', round.submitted_head],
            ['Scope', attempt.scope],
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
        <OriginalRecord value={integrated} label="Original integrated review" />
      </details>
    </section>
  );
}

export function TaskHistory({
  snapshot,
  task,
}: {
  readonly snapshot: Snapshot;
  readonly task: Task;
}) {
  const i18n = useI18n();
  const attempts = records(task.attempts);
  const [previous, setPrevious] = useState('');
  const current = attempts.at(-1) || {};
  const previousAttempt =
    attempts.find((attempt) => String(attempt.number) === previous) || attempts.at(-2) || {};
  const coverage = taskCoverage(snapshot, task.id);
  return (
    <section className="history-section">
      <details>
        <summary>
          {i18n.t('Attempt and review history')}{' '}
          <span className="muted">{i18n.number(attempts.length)}</span>
        </summary>
        {attempts.length > 1 && (
          <details>
            <summary>{i18n.t('Compare attempts')}</summary>
            <label>
              {i18n.t('Previous attempt')}
              <select
                value={String(previousAttempt.number)}
                onChange={(event) => setPrevious(event.target.value)}
              >
                {attempts.slice(0, -1).map((attempt, index) => (
                  <option key={index} value={String(attempt.number)}>
                    {i18n.t('Attempt')} {i18n.display(attempt.number)}
                  </option>
                ))}
              </select>
            </label>
            <Compare
              before={attemptFacts(previousAttempt)}
              after={attemptFacts(current)}
              fields={[
                'scope',
                'claimed_head',
                'submitted_head',
                'source_digest',
                'checks',
                'unresolved_findings',
                'review',
              ]}
            />
          </details>
        )}
        {attempts.length ? (
          <ol className="history-events">
            {attempts.map((attempt, index) => (
              <li key={string(attempt.number) || index}>
                <details open={index === attempts.length - 1 ? true : undefined}>
                  <summary>
                    {i18n.t('Attempt')} {i18n.display(attempt.number)} ·{' '}
                    {i18n.display(attempt.owner)} · {i18n.human(attempt.status || attempt.outcome)}
                  </summary>
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
                  <OriginalRecord value={attempt} label="Original attempt" />
                </details>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">{i18n.t('No attempts recorded.')}</p>
        )}
      </details>
      <details>
        <summary>{i18n.t('Criterion links')}</summary>
        <details>
          <summary>{i18n.t('Coverage source history')}</summary>
          {records(record(snapshot.coverage).history).map((entry, index) => (
            <article key={index}>
              <Facts
                values={[
                  ['ID', entry.id],
                  ['Freshness', entry.freshness],
                  ['Recorded at', entry.recorded_at],
                  ['Reasons', entry.reasons],
                ]}
              />
              <HistoryReferences value={entry.receipt} />
              <OriginalRecord value={entry} />
            </article>
          ))}
          <OriginalRecord value={snapshot.coverage} label="Original coverage" />
        </details>
        {coverage.length ? (
          coverage.map(({ criterion, links }, index) => (
            <article key={index}>
              <Text value={criterion.text} />
              <Facts
                values={[
                  ['Linkage', criterion.linkage],
                  ['Knowledge', criterion.knowledge],
                ]}
              />
              {links.map((link, linkIndex) => (
                <div key={linkIndex}>
                  <Facts
                    values={[
                      ['Task', link.task_id],
                      ['Paths', link.paths],
                      ['Scope kind', link.scope_kind],
                      ['Criterion', link.criterion],
                      ['Coverage observation', record(snapshot.coverage).status],
                    ]}
                  />
                  <HistoryReferences value={link.evidence} />
                  <OriginalRecord value={link} />
                </div>
              ))}
            </article>
          ))
        ) : (
          <p className="muted">{i18n.t('No explicit criterion links recorded')}</p>
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
  const delivery = record(value);
  const meta = record(delivery.metadata);
  const cleanup = record(meta.cleanup);
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
          ['PR', meta.pr_url],
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
      <details>
        <summary>{i18n.t('Revisions')}</summary>
        <Facts
          values={[
            ['Attempt', meta.attempt],
            ['Reviewed source', meta.reviewed_source_digest],
            ['Tested commit', meta.tested_commit],
            ['Head revision', meta.head_sha],
            ['Base revision', meta.base_sha],
            ['Test merge revision', meta.test_merge_sha],
            ['Merge commit', meta.merge_commit],
            ['Branch', meta.branch],
            ['Base', meta.base],
          ]}
        />
      </details>
      <details>
        <summary>
          {i18n.t('CI checks')} <span className="muted">{i18n.number(items(meta.ci).length)}</span>
        </summary>
        <Facts values={[['Required CI', meta.required_ci]]} />
        {missingChecks.length > 0 && (
          <Facts values={[['Required checks without observations', missingChecks]]} />
        )}
        {items(meta.ci).length ? (
          <ul className="history-events">
            {items(meta.ci).map((original, index) => {
              const check = record(original);
              return (
                <li key={index}>
                  <div className="history-event-heading">
                    <External value={check.url} label={i18n.display(check.name)} />
                    <span className="badge">{i18n.human(check.conclusion)}</span>
                  </div>
                  {revisionMismatch(check.commit, meta.tested_commit) && (
                    <p className="error">{i18n.t('Check revision differs from tested commit')}</p>
                  )}
                  <details>
                    <summary>{i18n.t('Check details')}</summary>
                    <Facts
                      values={[
                        ['Commit', check.commit],
                        ['Run', check.run_id],
                        ['Job', check.job_id],
                        ['Head revision', check.head_sha],
                        ['Base revision', check.base_sha],
                        ['Test merge revision', check.test_merge_sha],
                        ['Observed at', check.observed_at],
                        ['Started', check.started_at],
                        ['Completed', check.completed_at],
                      ]}
                    />
                    <OriginalRecord value={original} />
                  </details>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="muted">{i18n.t('No CI observations recorded')}</p>
        )}
      </details>
      <details>
        <summary>{i18n.t('Cleanup details')}</summary>
        <Facts
          values={[
            ['Status', cleanup.status],
            ['Reason', cleanup.reason],
            ['Merge identity', cleanup.merge_identity],
            ['Actor', cleanup.actor_id],
            ['Recorded at', cleanup.recorded_at],
          ]}
        />
        <HistoryReferences value={cleanup.evidence} />
      </details>
      <HistoryReferences value={delivery.receipt} />
      <OriginalRecord value={value} label="Original delivery" />
    </div>
  );
}

export function DeliveryHistory({ task }: { readonly task: Task }) {
  const i18n = useI18n();
  const history = deliveryHistory(task);
  return (
    <section className="history-section">
      <h3>{i18n.t('Recorded PR / CI')}</h3>
      <details>
        <summary>{i18n.t('About these records')}</summary>
        <p className="muted">
          {i18n.t(
            'These are saved observations, not live provider checks. A success conclusion does not establish that every test executed.',
          )}
        </p>
      </details>
      <details>
        <summary>
          {i18n.t('Current delivery')} · {i18n.human(record(task.delivery).status)}
        </summary>
        <DeliveryRecord value={task.delivery} task={task} />
      </details>
      <details>
        <summary>
          {i18n.t('Delivery history')}{' '}
          <span className="muted">
            {history.supplied ? i18n.number(history.entries.length) : i18n.t('Unavailable')}
          </span>
        </summary>
        {history.entries.length ? (
          <ol className="history-events">
            {history.entries.map((entry) => (
              <li key={entry.index}>
                <details>
                  <summary>
                    {i18n.number(entry.index + 1)} · {i18n.human(record(entry.record).status)} ·{' '}
                    {entry.time_status === 'recorded'
                      ? i18n.date(entry.recorded_at)
                      : i18n.t('Time unknown')}
                  </summary>
                  <p className="muted">{i18n.t('Local record time')}</p>
                  <DeliveryRecord value={entry.record} task={task} />
                  <HistoryReferences value={entry.reference} />
                  <HistoryReferences value={entry.event_reference} />
                  <OriginalRecord value={entry.original} label="Original history entry" />
                </details>
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
      </details>
    </section>
  );
}

export function StaffingHistory({
  actor,
  assignment,
}: {
  readonly actor: JsonRecord;
  readonly assignment?: JsonRecord;
}) {
  const i18n = useI18n();
  return (
    <details className="history-section">
      <summary>{i18n.t('Staffing and assignment history')}</summary>
      {assignment && (
        <Compare
          before={assignment}
          after={actor}
          labels={['At claim', 'Current settings']}
          fields={['model_id', 'reasoning_effort', 'provenance', 'role']}
        />
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
                  ['Model', record(event.metadata).model_id],
                  ['Reasoning', record(event.metadata).reasoning_effort],
                  ['Provenance', record(event.metadata).provenance],
                  ['Previous actor', event.predecessor_id],
                  ['Next actor', event.successor_id],
                ]}
              />
              <OriginalRecord value={original} />
            </li>
          );
        })}
      </ol>
      {!items(actor.history).length && (
        <p className="muted">{i18n.t('No staffing history recorded')}</p>
      )}
      {!assignment &&
        records(actor.assignments).map((entry, index) => (
          <details key={index}>
            <summary>
              {i18n.display(entry.task_id)} · {i18n.t('Attempt')} {i18n.display(entry.attempt)} ·{' '}
              {i18n.statusLabel(entry.status)}
            </summary>
            <Compare
              before={record(entry.metadata)}
              after={actor}
              labels={['At claim', 'Current settings']}
              fields={['model_id', 'reasoning_effort', 'provenance', 'role']}
            />
            <OriginalRecord value={entry} />
          </details>
        ))}
      <OriginalRecord
        value={{ history: actor.history ?? [], assignments: actor.assignments ?? [] }}
      />
    </details>
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
        {stages.map(([label, value, reference, time], index) => {
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
                  <HistoryReferences value={reference} />
                  <OriginalRecord value={value} />
                </>
              )}
            </li>
          );
        })}
      </ol>
      <details>
        <summary>{i18n.t('Compare transfer records')}</summary>
        <Compare
          before={record(handoff.outgoing)}
          after={record(handoff.acceptance || handoff.acknowledgement)}
          fields={['scope', 'source_head', 'source_digest', 'checks', 'questions', 'dependencies']}
        />
      </details>
      <OriginalRecord value={handoff} label="Original transfer" />
    </details>
  );
}

function TaskLinks({ value }: { readonly value: Json | undefined }) {
  return (
    <ul className="history-task-links">
      {items(value).map((id, index) => (
        <li key={index}>
          <a href={'#task/' + encodeURIComponent(string(id))}>{string(id)}</a>
        </li>
      ))}
    </ul>
  );
}
function BindingChanges({ entry }: { readonly entry: JsonRecord }) {
  const i18n = useI18n();
  return (
    <details>
      <summary>{i18n.t('Recorded binding changes')}</summary>
      <Compare
        before={record(entry.before_binding)}
        after={record(entry.after_binding)}
        fields={['objective_digest', 'planning_digest', 'task_definition_digest', 'task_ids']}
      />
      <OriginalRecord
        value={{ before: entry.before_binding ?? null, after: entry.after_binding ?? null }}
      />
    </details>
  );
}

export function MembershipHistory({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n();
  const objective = record(snapshot.objective);
  return (
    <details className="history-section membership-history">
      <summary>{i18n.t('Objective and source history')}</summary>
      <p className="muted">
        {i18n.t('Membership revision')} {i18n.display(objective.membership_revision)}
      </p>
      <ol className="history-events">
        {items(objective.membership_history).map((original, index) => {
          const entry = record(original);
          return (
            <li key={index}>
              <div className="history-event-heading">
                <strong>
                  {i18n.t('Revision')} {i18n.display(entry.revision)}
                </strong>
                <span>{i18n.date(entry.recorded_at)}</span>
              </div>
              <Facts values={[['Reason', entry.reason]]} />
              {Array.isArray(entry.added) && (
                <>
                  <h4>{i18n.t('Added tasks')}</h4>
                  <TaskLinks value={entry.added} />
                </>
              )}
              {Array.isArray(entry.removed) && (
                <>
                  <h4>{i18n.t('Removed tasks')}</h4>
                  <TaskLinks value={entry.removed} />
                </>
              )}
              {records(entry.changes).map((change, changeIndex) => (
                <details key={changeIndex}>
                  <summary>
                    <a href={'#task/' + encodeURIComponent(string(change.id))}>
                      {i18n.display(change.id)}
                    </a>{' '}
                    · {i18n.t('Changes')}
                  </summary>
                  <Compare
                    before={record(change.before)}
                    after={record(change.after)}
                    fields={[
                      ...new Set([
                        ...Object.keys(record(change.before)),
                        ...Object.keys(record(change.after)),
                      ]),
                    ]}
                  />
                  <OriginalRecord value={change} />
                </details>
              ))}
              <BindingChanges entry={entry} />
              <HistoryReferences value={entry.receipt} />
              <HistoryReferences value={entry.evidence} />
              <OriginalRecord value={original} />
            </li>
          );
        })}
      </ol>
      <details>
        <summary>{i18n.t('Source changes')}</summary>
        {items(snapshot.amendments).map((original, index) => {
          const entry = record(original);
          return (
            <article className="history-round" key={index}>
              <h4>{i18n.display(entry.id)}</h4>
              <Facts
                values={[
                  ['Operation', entry.operation],
                  ['Actor', entry.actor_id],
                  ['Reason', entry.reason],
                  ['Affected criteria', entry.affected_criteria],
                  ['Dependencies', entry.dependencies],
                ]}
              />
              {Array.isArray(entry.added) && (
                <>
                  <h4>{i18n.t('Added tasks')}</h4>
                  <TaskLinks value={entry.added} />
                </>
              )}
              {Array.isArray(entry.removed) && (
                <>
                  <h4>{i18n.t('Removed tasks')}</h4>
                  <TaskLinks value={entry.removed} />
                </>
              )}
              {records(entry.changes).map((change, changeIndex) => (
                <details key={changeIndex}>
                  <summary>
                    <a href={'#task/' + encodeURIComponent(string(change.id))}>
                      {i18n.display(change.id)}
                    </a>{' '}
                    · {i18n.t('Changes')}
                  </summary>
                  <Compare
                    before={record(change.before)}
                    after={record(change.after)}
                    fields={[
                      ...new Set([
                        ...Object.keys(record(change.before)),
                        ...Object.keys(record(change.after)),
                      ]),
                    ]}
                  />
                  <OriginalRecord value={change} />
                </details>
              ))}
              <BindingChanges entry={entry} />
              <HistoryReferences value={entry.receipt} />
              <HistoryReferences value={entry.evidence} />
              <OriginalRecord value={original} />
            </article>
          );
        })}
        {records(snapshot.source_intakes).map((intake, index) => (
          <details key={index}>
            <summary>
              {i18n.display(intake.id)} · {i18n.date(intake.recorded_at)}
            </summary>
            <Facts
              values={[
                ['Actor', intake.recorded_by],
                ['Objective', intake.objective_id],
                ['Review baseline', record(intake.source).review_baseline],
              ]}
            />
            <TaskLinks value={record(intake.source).task_ids} />
            <HistoryReferences value={[intake.source ?? null, intake.planning ?? null]} />
            <OriginalRecord value={intake} />
          </details>
        ))}
        <OriginalRecord value={snapshot.source_intakes} label="Original source intakes" />
      </details>
      <OriginalRecord value={objective} label="Original objective" />
    </details>
  );
}
