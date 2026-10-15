import { record, string } from './model';
import type { Json, JsonRecord, Snapshot } from './model';
import { boundedCollection } from './readiness';

// New projections are consumed only from one coherent snapshot, never merged independently.
export function finiteCount(value: Json | undefined): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}
function seconds(value: Json | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}
function strings(value: Json | undefined): readonly string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === 'string') ? value : null;
}
function nullableId(value: Json | undefined) {
  return value === null || typeof value === 'string';
}
function awareTime(value: Json | undefined): value is string {
  return (
    typeof value === 'string' &&
    /(Z|[+-]\d{2}:\d{2})$/iu.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}
export type ObservedEndpoint = {
  readonly observedAt: string;
  readonly clockId: string;
  readonly boundary: string;
};
export type ObservedAge = {
  readonly asOf: string;
  readonly seconds: number;
};
function endpoint(input: Json | undefined): ObservedEndpoint | null {
  const value = record(input);
  return awareTime(value.observed_at) &&
    string(value.clock_id) &&
    string(value.timestamp_source) &&
    string(value.boundary) &&
    Object.keys(record(value.reference)).length
    ? {
        observedAt: value.observed_at,
        clockId: string(value.clock_id),
        boundary: string(value.boundary),
      }
    : null;
}
// The producer retains an unknown interval's endpoint reference even when no time was observed.
// Only explicitly missing metadata is unmeasured; malformed supplied metadata stays unavailable.
function retainedUnmeasuredEndpoint(
  input: Json | undefined,
  reasons: readonly string[],
  oppositeEndpointMissing: boolean,
): boolean {
  const value = record(input);
  const missingTime = value.observed_at === null && reasons.includes('missing_observed_at');
  const missingClock =
    value.clock_id === null &&
    (reasons.includes('missing_clock') ||
      (oppositeEndpointMissing && reasons.includes('missing_endpoint')));
  return Boolean(
    (missingTime || missingClock) &&
    (awareTime(value.observed_at) || missingTime) &&
    (string(value.clock_id) || missingClock) &&
    string(value.timestamp_source) &&
    string(value.boundary) &&
    Object.keys(record(value.reference)).length,
  );
}
function observedAge(input: Json | undefined, start: ObservedEndpoint | null): ObservedAge | null {
  const value = record(input);
  if (
    !start ||
    !awareTime(value.as_of) ||
    value.clock_id !== start.clockId ||
    !seconds(value.seconds) ||
    !Object.keys(record(value.reference)).length
  )
    return null;
  const delta = (Date.parse(value.as_of) - Date.parse(start.observedAt)) / 1000;
  return delta >= 0 && Math.abs(delta - value.seconds) < 0.001
    ? { asOf: value.as_of, seconds: value.seconds }
    : null;
}
export const timingKinds = [
  'submission_to_start',
  'submission_to_dispatch',
  'dispatch_to_ack',
  'ack_to_start',
  'start_to_terminal',
  'submission_to_terminal',
  'blocked_span',
  'verification_wait',
  'verification_observed_span',
  'command_process',
  'recorded_history_span',
] as const;
export type TimingInterval = {
  readonly id: string;
  readonly kind: string;
  readonly boundaryLabel: string;
  readonly taskId: string;
  readonly attempt: number | null;
  readonly round: number | null;
  readonly handoffId: string;
  readonly applicability: string;
  readonly state: string;
  readonly start: ObservedEndpoint | null;
  readonly end: ObservedEndpoint | null;
  readonly closedSeconds: number | null;
  readonly age: ObservedAge | null;
  readonly outcome: string;
  readonly reasons: readonly string[];
};
export type CurrentReviewTiming = {
  readonly id: string;
  readonly queueRowId: string;
  readonly taskId: string;
  readonly attempt: number | null;
  readonly round: number | null;
  readonly handoffId: string;
  readonly canonicalPhase: string;
  readonly phase: string;
  readonly applicability: string;
  readonly cause: string;
  readonly resolverId: string;
  readonly nextTrigger: string;
  readonly waitingSince: ObservedEndpoint | null;
  readonly age: ObservedAge | null;
  readonly intervalIds: readonly string[];
  readonly reasons: readonly string[];
};
export type TimingMetric = {
  readonly kind: string;
  readonly outcome: string;
  readonly relevant: number;
  readonly closed: number;
  readonly open: number;
  readonly unknown: number;
  readonly excluded: number;
  readonly min: number | null;
  readonly max: number | null;
  readonly median: number | null;
};
export type TimingCohort = {
  readonly id: string;
  readonly taskTotal: number;
  readonly intervalTotal: number;
  readonly summaries: readonly TimingMetric[];
};
export type ReviewTiming = {
  readonly current: readonly CurrentReviewTiming[];
  readonly currentTotal: number;
  readonly currentOmitted: number;
  readonly intervals: readonly TimingInterval[];
  readonly intervalTotal: number;
  readonly intervalOmitted: number;
  readonly cohorts: readonly TimingCohort[];
  readonly coverage: JsonRecord;
};
function applicability(value: Json | undefined) {
  return ['current', 'stale', 'unavailable', 'historical', 'superseded'].includes(string(value));
}
function subject(snapshot: Snapshot, value: JsonRecord) {
  return (
    snapshot.tasks.some((task) => task.id === value.task_id) &&
    (value.attempt === null || (finiteCount(value.attempt) && value.attempt > 0)) &&
    (value.round === null || (finiteCount(value.round) && value.round > 0)) &&
    applicability(value.applicability)
  );
}
function interval(snapshot: Snapshot, input: Json): TimingInterval | null {
  const value = record(input),
    start = endpoint(value.start),
    end = endpoint(value.end);
  const reasons = strings(value.reasons);
  const unknown = value.state === 'unknown' && value.closed_seconds === null;
  if (
    !string(value.interval_id) ||
    !timingKinds.some((kind) => kind === value.kind) ||
    !subject(snapshot, value) ||
    !reasons ||
    !['closed', 'open', 'unknown', 'canonical_terminal_timing_unknown'].includes(
      string(value.state),
    ) ||
    (value.start !== null &&
      !start &&
      !(unknown && retainedUnmeasuredEndpoint(value.start, reasons, value.end === null))) ||
    (value.end !== null &&
      !end &&
      !(unknown && retainedUnmeasuredEndpoint(value.end, reasons, value.start === null)))
  )
    return null;
  if (value.state === 'closed') {
    if (!start || !end || !seconds(value.closed_seconds) || start.clockId !== end.clockId)
      return null;
    const delta = (Date.parse(end.observedAt) - Date.parse(start.observedAt)) / 1000;
    if (
      delta < 0 ||
      (!(
        value.kind === 'command_process' && value.boundary_label === 'sealed_process_monotonic_span'
      ) &&
        Math.abs(delta - value.closed_seconds) >= 0.001)
    )
      return null;
  } else if (value.closed_seconds !== null) return null;
  const age = observedAge(value.age, start);
  if (value.age !== null && !age) return null;
  return {
    id: string(value.interval_id),
    kind: string(value.kind),
    boundaryLabel: string(value.boundary_label),
    taskId: string(value.task_id),
    attempt: typeof value.attempt === 'number' ? value.attempt : null,
    round: typeof value.round === 'number' ? value.round : null,
    handoffId: string(value.handoff_id),
    applicability: string(value.applicability),
    state: string(value.state),
    start,
    end,
    closedSeconds: typeof value.closed_seconds === 'number' ? value.closed_seconds : null,
    age,
    outcome: string(value.outcome),
    reasons,
  };
}
function currentRow(snapshot: Snapshot, input: Json): CurrentReviewTiming | null {
  const value = record(input),
    waitingSince = endpoint(value.waiting_since),
    reasons = strings(value.reasons);
  const intervalIds = strings(value.interval_ids);
  if (
    !string(value.id) ||
    !subject(snapshot, value) ||
    !reasons ||
    !intervalIds ||
    !['active', 'blocked', 'review', 'done', 'planned'].includes(string(value.canonical_phase)) ||
    ![
      'ready',
      'unassigned',
      'sent',
      'delivery_unknown',
      'delivery_failed',
      'acknowledged',
      'started',
      'terminal',
      'unknown',
    ].includes(string(value.observation_phase)) ||
    (value.waiting_since !== null && !waitingSince)
  )
    return null;
  const age = observedAge(value.age, waitingSince);
  if (value.age !== null && !age) return null;
  return {
    id: string(value.id),
    queueRowId: string(value.queue_row_id),
    taskId: string(value.task_id),
    attempt: typeof value.attempt === 'number' ? value.attempt : null,
    round: typeof value.round === 'number' ? value.round : null,
    handoffId: string(value.handoff_id),
    canonicalPhase: string(value.canonical_phase),
    phase: string(value.observation_phase),
    applicability: string(value.applicability),
    cause: string(value.cause),
    resolverId: string(value.resolver_id),
    nextTrigger: string(value.next_trigger),
    waitingSince,
    age,
    intervalIds,
    reasons,
  };
}
function metric(input: Json): TimingMetric | null {
  const value = record(input);
  if (
    !timingKinds.some((kind) => kind === value.kind) ||
    !['relevant_total', 'closed_valid', 'open', 'unknown', 'excluded'].every((key) =>
      finiteCount(value[key]),
    )
  )
    return null;
  const closed = Number(value.closed_valid);
  const stats = [value.min_seconds, value.max_seconds, value.median_seconds];
  if (closed ? !stats.every(seconds) : stats.some((stat) => stat !== null)) return null;
  if (
    closed &&
    !(
      Number(value.min_seconds) <= Number(value.median_seconds) &&
      Number(value.median_seconds) <= Number(value.max_seconds)
    )
  )
    return null;
  if (
    closed + Number(value.open) + Number(value.unknown) + Number(value.excluded) !==
    value.relevant_total
  )
    return null;
  return {
    kind: string(value.kind),
    outcome: string(value.outcome),
    relevant: Number(value.relevant_total),
    closed,
    open: Number(value.open),
    unknown: Number(value.unknown),
    excluded: Number(value.excluded),
    min: typeof value.min_seconds === 'number' ? value.min_seconds : null,
    max: typeof value.max_seconds === 'number' ? value.max_seconds : null,
    median: typeof value.median_seconds === 'number' ? value.median_seconds : null,
  };
}
function parseTiming(snapshot: Snapshot): ReviewTiming | null {
  const value = record(snapshot.timing),
    binding = record(value.source_binding);
  const current = boundedCollection(value.current, 50),
    intervals = boundedCollection(value.intervals, 50);
  const coverage = record(value.coverage);
  if (
    value.schema_version !== 1 ||
    binding.program_id !== snapshot.program_id ||
    !/^[a-f0-9]{64}$/iu.test(string(binding.input_digest)) ||
    !current ||
    !intervals ||
    !Array.isArray(value.cohorts) ||
    value.cohorts.length > 2 ||
    !['complete', 'not_assessed'].includes(string(coverage.status)) ||
    !['relevant_total', 'represented_total', 'unknown_total', 'open_total', 'excluded_total'].every(
      (key) => finiteCount(coverage[key]),
    ) ||
    !strings(coverage.reasons)
  )
    return null;
  const objective = record(snapshot.objective);
  if (
    binding.objective_id !== undefined &&
    binding.objective_id !== null &&
    binding.objective_id !== objective.id
  )
    return null;
  if (
    binding.membership_revision !== undefined &&
    binding.membership_revision !== null &&
    binding.membership_revision !== objective.membership_revision
  )
    return null;
  const currentRows: CurrentReviewTiming[] = [],
    intervalRows: TimingInterval[] = [],
    cohorts: TimingCohort[] = [];
  for (const raw of current.items) {
    const row = currentRow(snapshot, raw);
    if (!row) return null;
    currentRows.push(row);
  }
  for (const raw of intervals.items) {
    const row = interval(snapshot, raw);
    if (!row) return null;
    intervalRows.push(row);
  }
  for (const raw of value.cohorts) {
    const row = record(raw);
    if (
      !['all_retained', 'current_objective'].includes(string(row.id)) ||
      !finiteCount(row.task_total) ||
      !finiteCount(row.interval_total) ||
      !Array.isArray(row.summaries)
    )
      return null;
    const summaries: TimingMetric[] = [];
    for (const rawMetric of row.summaries) {
      const parsed = metric(rawMetric);
      if (!parsed) return null;
      summaries.push(parsed);
    }
    cohorts.push({
      id: string(row.id),
      taskTotal: row.task_total,
      intervalTotal: row.interval_total,
      summaries,
    });
  }
  if (
    new Set(currentRows.map((row) => row.id)).size !== currentRows.length ||
    new Set(intervalRows.map((row) => row.id)).size !== intervalRows.length ||
    new Set(cohorts.map((row) => row.id)).size !== cohorts.length
  )
    return null;
  return {
    current: currentRows,
    currentTotal: current.total,
    currentOmitted: current.omitted,
    intervals: intervalRows,
    intervalTotal: intervals.total,
    intervalOmitted: intervals.omitted,
    cohorts,
    coverage,
  };
}
const timingCache = new WeakMap<Snapshot, ReviewTiming | null>();
export function reviewTiming(snapshot: Snapshot): ReviewTiming | null {
  if (timingCache.has(snapshot)) return timingCache.get(snapshot) || null;
  const parsed = parseTiming(snapshot);
  timingCache.set(snapshot, parsed);
  return parsed;
}
export function knownLabel(labels: Readonly<Record<string, string>>, value: string): string {
  return Object.hasOwn(labels, value) ? labels[value] || value : value;
}

export const handoffPhases = [
  'ready',
  'unassigned',
  'sent',
  'delivery_unknown',
  'delivery_failed',
  'acknowledged',
  'started',
  'terminal',
] as const;
export type ReviewHandoff = {
  readonly id: string;
  readonly taskId: string;
  readonly attempt: number;
  readonly round: number;
  readonly handoffId: string;
  readonly currentness: string;
  readonly generation: number;
  readonly sequence: number;
  readonly reviewerId: string;
  readonly canonicalTerminal: boolean;
  readonly phase: string;
  readonly nextActorId: string;
  readonly nextAction: string;
  readonly nextTrigger: string;
  readonly reasons: readonly string[];
};
export type ReviewHandoffs = {
  readonly rows: readonly ReviewHandoff[];
  readonly total: number;
  readonly omitted: number;
};
function currentHandoffEnvelope(snapshot: Snapshot): boolean {
  const value = record(snapshot.review_handoffs);
  return (
    value.schema_version === 1 &&
    ['recorded', 'absent'].includes(string(value.status)) &&
    record(value.source_binding).revision === snapshot.revision
  );
}
function parseHandoffRow(snapshot: Snapshot, input: Json | undefined): ReviewHandoff | null {
  const row = record(input),
    reasons = strings(row.reasons);
  if (
    !string(row.id) ||
    !string(row.handoff_id) ||
    !snapshot.tasks.some((task) => task.id === row.task_id) ||
    !finiteCount(row.attempt) ||
    row.attempt < 1 ||
    !finiteCount(row.submission_round) ||
    row.submission_round < 1 ||
    !finiteCount(row.assignment_generation) ||
    row.assignment_generation < 1 ||
    !finiteCount(row.recorded_sequence) ||
    !['current', 'stale', 'unavailable'].includes(string(row.currentness)) ||
    !handoffPhases.some((phase) => phase === row.phase) ||
    typeof row.canonical_terminal !== 'boolean' ||
    !reasons
  )
    return null;
  return {
    id: string(row.id),
    handoffId: string(row.handoff_id),
    taskId: string(row.task_id),
    attempt: row.attempt,
    round: row.submission_round,
    generation: row.assignment_generation,
    sequence: row.recorded_sequence,
    currentness: string(row.currentness),
    reviewerId: string(row.reviewer_id),
    canonicalTerminal: row.canonical_terminal,
    phase: string(row.phase),
    nextActorId: string(row.next_actor_id),
    nextAction: string(row.next_action),
    nextTrigger: string(row.next_trigger),
    reasons,
  };
}
function parseHandoffs(snapshot: Snapshot): ReviewHandoffs | null {
  const rows = boundedCollection(record(snapshot.review_handoffs).rows, 50);
  if (!currentHandoffEnvelope(snapshot) || !rows) return null;
  const parsed: ReviewHandoff[] = [];
  for (const input of rows.items) {
    const row = parseHandoffRow(snapshot, input);
    if (!row) return null;
    parsed.push(row);
  }
  if (new Set(parsed.map((row) => row.id)).size !== parsed.length) return null;
  return { rows: parsed, total: rows.total, omitted: rows.omitted };
}
const handoffCache = new WeakMap<Snapshot, ReviewHandoffs | null>();
export function reviewHandoffs(snapshot: Snapshot): ReviewHandoffs | null {
  if (handoffCache.has(snapshot)) return handoffCache.get(snapshot) || null;
  const parsed = parseHandoffs(snapshot);
  handoffCache.set(snapshot, parsed);
  return parsed;
}
function parseTaskHandoff(snapshot: Snapshot, taskId: string): ReviewHandoff | null {
  const task = snapshot.tasks.find((task) => task.id === taskId);
  if (
    !task ||
    !currentHandoffEnvelope(snapshot) ||
    record(snapshot.review_handoffs).status !== 'recorded'
  )
    return null;
  // The producer selects this exact row from the complete assessed set before bounding history.
  // Missing, null or malformed authority never falls back to a displayed historical row.
  const raw = record(task.review_handoff),
    row = parseHandoffRow(snapshot, task.review_handoff);
  const reference = record(raw.reference),
    subject = record(raw.subject);
  if (
    !row ||
    row.taskId !== taskId ||
    row.id !== row.handoffId + ':' + row.generation ||
    reference.path !== 'backlog.json' ||
    reference.revision !== snapshot.revision ||
    reference.task_id !== taskId ||
    reference.pointer !== '/review_handoffs/records/' + row.sequence ||
    !/^[a-f0-9]{64}$/iu.test(string(reference.record_sha256)) ||
    subject.program_id !== snapshot.program_id ||
    subject.task_id !== taskId ||
    subject.attempt !== row.attempt ||
    subject.submission_round !== row.round ||
    !/^[a-f0-9]{64}$/iu.test(string(subject.receipt_sha256)) ||
    !/^[a-f0-9]{64}$/iu.test(string(subject.source_digest))
  )
    return null;
  return row;
}
const taskHandoffCache = new WeakMap<Snapshot, Map<string, ReviewHandoff | null>>();
export function taskHandoff(snapshot: Snapshot, taskId: string): ReviewHandoff | null {
  let tasks = taskHandoffCache.get(snapshot);
  if (!tasks) {
    tasks = new Map();
    taskHandoffCache.set(snapshot, tasks);
  }
  if (tasks.has(taskId)) return tasks.get(taskId) ?? null;
  const parsed = parseTaskHandoff(snapshot, taskId);
  tasks.set(taskId, parsed);
  return parsed;
}
export function taskHandoffUnavailable(snapshot: Snapshot, taskId: string): boolean {
  const task = snapshot.tasks.find((task) => task.id === taskId);
  if (!task || task.review_handoff === null) return false;
  return (
    (task.review_handoff !== undefined || record(snapshot.review_handoffs).status === 'recorded') &&
    taskHandoff(snapshot, taskId) === null
  );
}
export const reviewPhaseLabels: Readonly<Record<string, string>> = {
  ready: 'Review handoff prepared',
  unassigned: 'Awaiting reviewer assignment',
  sent: 'Review request sent',
  delivery_unknown: 'Review send outcome needs confirmation',
  delivery_failed: 'Review request could not be sent',
  acknowledged: 'Reviewer acknowledged',
  started: 'Review started',
  terminal: 'Review ended',
};
export const intervalLabels: Readonly<Record<string, string>> = {
  submission_to_start: 'Submission observed to review started',
  submission_to_dispatch: 'Submission observed to request sent',
  dispatch_to_ack: 'Request sent to acknowledgement',
  ack_to_start: 'Acknowledgement to review started',
  start_to_terminal: 'Observed review span',
  submission_to_terminal: 'Submission observed to review ended',
  blocked_span: 'Observed blocked span',
  verification_wait: 'Observed verification wait',
  verification_observed_span: 'Observed verification span',
  recorded_history_span: 'Recorded history span',
  command_process: 'Observed process span',
};

export type EvidencePreparation = {
  readonly kind: string;
  readonly taskId: string;
  readonly operation: string;
  readonly status: string;
  readonly counts: Readonly<Record<string, number>>;
  readonly affectedChecks: readonly string[];
  readonly nextActorId: string;
  readonly nextAction: string;
  readonly reasons: readonly string[];
};
export function evidencePreparation(input: Json | undefined): EvidencePreparation | null {
  const value = record(input),
    counts = record(value.counts);
  const reasons = strings(value.reasons),
    checks = strings(value.affected_checks);
  if (
    value.schema_version !== 1 ||
    !['ready', 'incomplete', 'stale', 'unavailable'].includes(string(value.status)) ||
    !nullableId(value.task_id) ||
    !nullableId(value.operation) ||
    !reasons ||
    !checks ||
    !['selected', 'retained', 'attachments_planned', 'attachments_ready', 'unresolved'].every(
      (key) => finiteCount(counts[key]),
    ) ||
    Number(counts.retained) > Number(counts.attachments_planned) ||
    Number(counts.attachments_ready) > Number(counts.attachments_planned)
  )
    return null;
  const parsedCounts: Record<string, number> = {};
  for (const [key, number] of Object.entries(counts))
    if (finiteCount(number)) parsedCounts[key] = number;
  return {
    kind: string(value.kind),
    taskId: string(value.task_id),
    operation: string(value.operation),
    status: string(value.status),
    counts: parsedCounts,
    affectedChecks: checks,
    nextActorId: string(value.next_actor_id),
    nextAction: string(value.next_action),
    reasons,
  };
}
export type BatchOperation = {
  readonly id: string;
  readonly operation: string;
  readonly taskId: string;
  readonly status: string;
};
export type RecordBatch = {
  readonly id: string;
  readonly preparation: string;
  readonly recordedPublication: string;
  readonly publication: string;
  readonly applicability: string;
  readonly viewRecovery: boolean;
  readonly reason: string;
  readonly requested: number | null;
  readonly validated: number | null;
  readonly committed: number | null;
  readonly nextActorId: string;
  readonly nextAction: string;
  readonly operations: readonly BatchOperation[];
  readonly omitted: number | null;
};
export function recordBatch(input: Json | undefined): RecordBatch | null {
  const value = record(input),
    preparation = record(value.preparation),
    publication = record(value.publication);
  const statuses = [
    'not_started',
    'not_committed',
    'already_committed',
    'committed',
    'committed_view_failed',
    'conflict',
    'unknown',
  ];
  const failure =
    preparation.status === 'failed' &&
    ['not_committed', 'unknown'].includes(string(publication.status));
  const rawOperations = record(value.operations);
  const knownOperations = boundedCollection(value.operations, 50);
  const unknownOperations =
    rawOperations.total === null &&
    rawOperations.omitted === null &&
    rawOperations.shown === 0 &&
    Array.isArray(rawOperations.items) &&
    rawOperations.items.length === 0;
  const operations =
    knownOperations ||
    (failure && unknownOperations ? { items: [], total: null, omitted: null } : null);
  const requested = value.requested_operation_count,
    validated = value.validated_operation_count,
    committed = value.committed_operation_count;
  if (
    value.schema_version !== 1 ||
    !(string(value.id) || (value.id === null && failure)) ||
    !['not_prepared', 'validated', 'failed', 'recorded'].includes(string(preparation.status)) ||
    !statuses.includes(string(publication.status)) ||
    (value.recorded_publication !== undefined &&
      !statuses.includes(string(record(value.recorded_publication).status))) ||
    !['current', 'historical', 'stale', 'unavailable', 'unknown'].includes(
      string(value.applicability),
    ) ||
    typeof publication.view_recovery_required !== 'boolean' ||
    ![requested, validated, committed].every(
      (count) => finiteCount(count) || (failure && count === null),
    ) ||
    (typeof requested === 'number' && typeof validated === 'number' && validated > requested) ||
    (typeof validated === 'number' && typeof committed === 'number' && committed > validated) ||
    (failure && publication.status === 'not_committed' && committed !== 0) ||
    (failure && publication.status === 'unknown' && committed !== null) ||
    value.accepted !== false ||
    value.execution_replayed !== false ||
    value.remote_verified_by_tool !== false ||
    !operations ||
    operations.total !== requested
  )
    return null;
  if (
    ['committed', 'already_committed', 'committed_view_failed'].includes(
      string(publication.status),
    ) &&
    (committed !== requested || validated !== requested)
  )
    return null;
  const parsed: BatchOperation[] = [];
  for (const input of operations.items) {
    const row = record(input);
    if (
      !string(row.id) ||
      !string(row.operation) ||
      !nullableId(row.task_id) ||
      !['validated', 'committed', 'conflict', 'not_assessed'].includes(string(row.status))
    )
      return null;
    parsed.push({
      id: string(row.id),
      operation: string(row.operation),
      taskId: string(row.task_id),
      status: string(row.status),
    });
  }
  if (new Set(parsed.map((row) => row.id)).size !== parsed.length) return null;
  return {
    id: string(value.id),
    preparation: string(preparation.status),
    recordedPublication: string(record(value.recorded_publication).status),
    publication: string(publication.status),
    applicability: string(value.applicability),
    viewRecovery: publication.view_recovery_required,
    reason: string(publication.reason_code),
    requested: typeof requested === 'number' ? requested : null,
    validated: typeof validated === 'number' ? validated : null,
    committed: typeof committed === 'number' ? committed : null,
    nextActorId: string(value.next_actor_id),
    nextAction: string(value.next_action),
    operations: parsed,
    omitted: operations.omitted,
  };
}
