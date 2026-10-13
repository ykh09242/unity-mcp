import { items, record, records, semanticValueEqual, string } from './model';
import type { Json, JsonRecord, Snapshot, Task } from './model';

export function latestReview(task: Task) {
  const attempts = records(task.attempts);
  const attempt = attempts.at(-1) || {};
  const rounds = records(attempt.rounds);
  const round = rounds.at(-1) || {};
  return {
    attempt,
    round,
    receipt: record(round.receipt_record),
    review: record(round.review_record),
  };
}

export function historyComparison(
  before: JsonRecord,
  after: JsonRecord,
  fields: readonly string[],
) {
  return fields.map((field) => ({
    field,
    before: before[field],
    after: after[field],
    status:
      before[field] === undefined ||
      after[field] === undefined ||
      before[field] === null ||
      after[field] === null
        ? 'unknown'
        : semanticValueEqual(before[field], after[field])
          ? 'same'
          : 'different',
  }));
}

export function attemptFacts(attempt: JsonRecord): JsonRecord {
  const round = records(attempt.rounds).at(-1) || {};
  return {
    scope: attempt.scope ?? null,
    claimed_head: attempt.claimed_head ?? null,
    source_digest: round.source_digest ?? null,
    submitted_head: round.submitted_head ?? null,
    checks: record(round.receipt_record).checks ?? null,
    unresolved_findings: record(round.review_record).unresolved_findings ?? null,
    review: round.review ?? null,
  };
}

export function deliveryHistory(task: Task) {
  const history = record(task.delivery_history);
  return {
    supplied: Object.hasOwn(task, 'delivery_history'),
    status: string(history.status) || 'unknown',
    // Do not deduplicate repeated receipts or silently remove malformed optional rows.
    entries: items(history.items).map((original, index) => {
      const entry = record(original);
      return {
        original,
        index,
        record: entry.record,
        reference: entry.reference,
        recorded_at: entry.recorded_at,
        time_status: entry.time_status,
        event_reference: entry.event_reference,
      };
    }),
  };
}

export function revisionMismatch(left: Json | undefined, right: Json | undefined) {
  return (
    typeof left === 'string' &&
    left.length > 0 &&
    typeof right === 'string' &&
    right.length > 0 &&
    left !== right
  );
}

export function priorParticipants(task: Task): readonly string[] {
  return [
    ...new Set(
      records(task.attempts)
        .flatMap((attempt) => [
          string(attempt.owner),
          ...records(attempt.rounds).map((round) => string(record(round.review_record).reviewer)),
        ])
        .filter(Boolean),
    ),
  ];
}

export function taskCoverage(snapshot: Snapshot, taskId: string) {
  return records(record(snapshot.coverage).criteria).flatMap((criterion) => {
    const links = records(criterion.links).filter((link) => link.task_id === taskId);
    return links.length ? [{ criterion, links }] : [];
  });
}
