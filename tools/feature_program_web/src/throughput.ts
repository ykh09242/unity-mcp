import { items, record, records, string } from './model';
import { reviewQueue } from './flow-queue';
import type { Json, JsonRecord, Snapshot, Task } from './model';
import { currentCollection, currentReference, recordedReadiness } from './readiness';

export const coordinationViews = [
  'available',
  'blocked',
  'verification',
  'waiting',
  'unknown',
  'review_queue',
] as const;
export const coordinationLabels: Readonly<Record<string, string>> = {
  available: 'No recorded blockers',
  blocked: 'Recorded blockers',
  verification: 'Verification needs',
  waiting: 'Observed waiting',
  unknown: 'Coordination unavailable',
  review_queue: 'Review queue',
};
export const checkLabels: Readonly<Record<string, string>> = {
  acceptance: 'Acceptance criteria',
  scoped_tests: 'Scoped tests',
  format_lint: 'Formatting and lint',
};
function parseReadinessFactors(snapshot: Snapshot, task: Task) {
  const readiness = recordedReadiness(snapshot, task);
  const dependencies = currentCollection(snapshot, readiness?.unmet_dependencies, task.id);
  const collisions = currentCollection(snapshot, readiness?.collisions, task.id);
  const stale = record(readiness?.submission).status === 'stale';
  const known = Boolean(readiness && dependencies && collisions);
  const blockers = known ? dependencies!.total + collisions!.total : null;
  return {
    known,
    blockers,
    stale,
    dependencies,
    collisions,
    available:
      known &&
      task.status === 'planned' &&
      readiness?.canonical_candidate === true &&
      blockers === 0 &&
      !stale,
  };
}
const readinessCache = new WeakMap<
  Snapshot,
  WeakMap<Task, ReturnType<typeof parseReadinessFactors>>
>();
export function readinessFactors(snapshot: Snapshot, task: Task) {
  let tasks = readinessCache.get(snapshot);
  if (!tasks) {
    tasks = new WeakMap();
    readinessCache.set(snapshot, tasks);
  }
  const cached = tasks.get(task);
  if (cached) return cached;
  const parsed = parseReadinessFactors(snapshot, task);
  tasks.set(task, parsed);
  return parsed;
}
const phases = new Set(['requested', 'running', 'blocked', 'finished', 'canceled']);
const results = new Set(['passed', 'failed', 'pending', 'not_run', 'unknown', 'not_applicable']);
const applicability = new Set(['current', 'stale', 'unavailable', 'unknown']);
function parseVerificationContext(snapshot: Snapshot, task: Task) {
  const envelope = record(snapshot.verification);
  if (envelope.schema_version !== 1 || envelope.status !== 'recorded') return null;
  const value = record(task.verification);
  if (value.task_id !== task.id || !currentReference(snapshot, value.reference, task.id))
    return null;
  const lanes = currentCollection(snapshot, value.lanes, task.id, 50);
  const findings = currentCollection(snapshot, value.findings, task.id, 50);
  if (!lanes || !findings) return null;
  const rows = lanes.items.map(record);
  if (
    rows.some(
      (lane) =>
        !string(lane.request_id) ||
        !string(lane.lane) ||
        !Number.isSafeInteger(lane.request_version) ||
        Number(lane.request_version) < 1 ||
        !phases.has(string(lane.phase)) ||
        !results.has(string(lane.result)) ||
        !applicability.has(string(lane.applicability)),
    )
  )
    return null;
  return { value, lanes: { ...lanes, items: rows }, findings };
}
const verificationCache = new WeakMap<
  Snapshot,
  WeakMap<Task, ReturnType<typeof parseVerificationContext>>
>();
export function verificationContext(snapshot: Snapshot, task: Task) {
  let tasks = verificationCache.get(snapshot);
  if (!tasks) {
    tasks = new WeakMap();
    verificationCache.set(snapshot, tasks);
  }
  if (tasks.has(task)) return tasks.get(task) ?? null;
  const parsed = parseVerificationContext(snapshot, task);
  tasks.set(task, parsed);
  return parsed;
}
export function laneNeedsAction(lane: JsonRecord) {
  const provider = record(lane.provider_execution);
  return (
    lane.applicability !== 'current' ||
    lane.result === 'failed' ||
    lane.phase === 'blocked' ||
    ['requested', 'running'].includes(string(lane.phase)) ||
    (lane.phase === 'finished' &&
      ['unknown', 'pending', 'not_run'].includes(string(lane.result))) ||
    (hasProviderRecord(lane.provider_execution) &&
      (provider.status !== 'passed' || provider.applicability !== 'current'))
  );
}
export function observedWait(value: Json | undefined) {
  const waiting = record(value);
  const start = string(waiting.started_at);
  const end = string(waiting.ended_at) || string(waiting.as_of);
  const seconds = waiting.seconds;
  if (
    waiting.status !== 'observed' ||
    !string(waiting.clock_id) ||
    !start ||
    !end ||
    !Number.isFinite(Date.parse(start)) ||
    !Number.isFinite(Date.parse(end)) ||
    Date.parse(end) < Date.parse(start) ||
    typeof seconds !== 'number' ||
    !Number.isFinite(seconds) ||
    seconds < 0
  )
    return null;
  // Require consistent supplied endpoints; never substitute the browser/publication clock.
  if (Math.abs((Date.parse(end) - Date.parse(start)) / 1000 - seconds) > 1) return null;
  return { seconds, start, end, open: !string(waiting.ended_at), clock: string(waiting.clock_id) };
}
export function matchesCoordination(snapshot: Snapshot, task: Task, filter = '') {
  if (!filter) return true;
  if (filter === 'review_queue')
    return Boolean(reviewQueue(snapshot)?.rows.some((row) => row.taskId === task.id));
  if (filter === 'available') return readinessFactors(snapshot, task).available;
  const verification = verificationContext(snapshot, task);
  const factors =
    filter === 'blocked' || filter === 'unknown' ? readinessFactors(snapshot, task) : null;
  if (filter === 'blocked')
    return (
      task.status === 'blocked' ||
      factors?.stale ||
      Number(factors?.blockers) > 0 ||
      Boolean(verification?.lanes.items.some((lane) => lane.phase === 'blocked'))
    );
  if (filter === 'verification')
    return Boolean(
      verification &&
      (verification.lanes.omitted > 0 || verification.lanes.items.some(laneNeedsAction)),
    );
  if (filter === 'waiting')
    return Boolean(verification?.lanes.items.some((lane) => observedWait(lane.waiting)?.open));
  return (
    filter === 'unknown' && (!factors?.known || !verification || verification.lanes.omitted > 0)
  );
}
export function executionPriority(snapshot: Snapshot, task: Task) {
  const priorities = record(snapshot.execution_priorities);
  const value = priorities[task.id];
  return typeof value === 'number' && Number.isSafeInteger(value) ? value : 0;
}
export function verificationAssignments(snapshot: Snapshot, tasks: readonly Task[]) {
  return tasks.flatMap((task) => {
    const context = verificationContext(snapshot, task);
    return context
      ? context.lanes.items.filter(laneNeedsAction).map((lane) => ({ task, lane }))
      : [];
  });
}
export function findingsText(value: Json | undefined) {
  return items(value)
    .map((item) => (typeof item === 'string' ? item : string(record(item).reason)))
    .filter(Boolean);
}

function validCounts(value: JsonRecord, prefix: string, rows: readonly Json[]) {
  const total = value[prefix + 'total'],
    shown = value[prefix + 'shown'],
    omitted = value[prefix + 'omitted'];
  return (
    typeof total === 'number' &&
    typeof shown === 'number' &&
    typeof omitted === 'number' &&
    [total, shown, omitted].every((number) => Number.isSafeInteger(number) && number >= 0) &&
    shown === rows.length &&
    total === shown + omitted
  );
}
export function providerContext(snapshot: Snapshot, task: Task) {
  const provider = record(task.provider_execution);
  if (
    provider.task_id !== task.id ||
    !currentReference(snapshot, provider.reference, task.id) ||
    !['passed', 'failed', 'pending', 'not_assessed', 'unavailable'].includes(
      string(provider.status),
    ) ||
    !Array.isArray(provider.stages) ||
    !validCounts(provider, '', provider.stages)
  )
    return null;
  const stages = provider.stages.map(record);
  if (
    new Set(stages.map(providerStageKey)).size !== stages.length ||
    stages.some(
      (stage) =>
        !string(stage.id) ||
        !['pr_ci', 'beta_ci'].includes(string(stage.kind)) ||
        !['passed', 'failed', 'pending', 'not_assessed'].includes(string(stage.status)) ||
        typeof stage.current !== 'boolean' ||
        !Array.isArray(stage.checks) ||
        !Array.isArray(stage.history) ||
        !validCounts(stage, 'checks_', stage.checks) ||
        !validCounts(stage, 'history_', stage.history),
    )
  )
    return null;
  return { provider, stages };
}
export function providerStageKey(stage: JsonRecord) {
  return JSON.stringify([string(stage.request_id), string(stage.id)]);
}
// Attachment metadata can differ when the same captured execution is adopted at delivery.
// Missing execution identity is deliberately not eligible for display deduplication.
export function providerStageIdentity(stage: JsonRecord): string | null {
  const source = record(stage.source);
  const history = records(stage.history);
  const knownId = (value: Json | undefined) =>
    typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
  const knownSha = (value: Json | undefined) =>
    /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/iu.test(string(value));
  if (
    stage.current !== true ||
    !['head_sha', 'base_sha', 'tested_sha'].every((key) => knownSha(source[key])) ||
    !records(stage.checks).length ||
    stage.checks_omitted !== 0 ||
    stage.history_omitted !== 0 ||
    !history.length ||
    history.some(
      (run) =>
        !knownId(run.run_id) ||
        !knownId(run.run_attempt) ||
        !Number.isFinite(Date.parse(string(run.captured_at))) ||
        !knownSha(run.head_sha) ||
        !knownSha(run.checkout_sha) ||
        run.jobs_omitted !== 0 ||
        run.jobs_total !== items(run.job_ids).length ||
        run.jobs_shown !== items(run.job_ids).length ||
        !items(run.job_ids).length ||
        !items(run.job_ids).every(knownId),
    ) ||
    records(stage.checks).some(
      (check) => !knownId(check.run_id) || !knownId(check.run_attempt) || !knownId(check.job_id),
    )
  )
    return null;
  const ordered = (value: Json): Json =>
    Array.isArray(value)
      ? value.map(ordered)
      : value !== null && typeof value === 'object'
        ? Object.fromEntries(
            Object.entries(value)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([key, child]) => [key, ordered(child)]),
          )
        : value;
  const attachmentKeys = new Set([
    'request_id',
    'lane',
    'verifier_id',
    'reference',
    'provider_reference',
  ]);
  return JSON.stringify(
    ordered(Object.fromEntries(Object.entries(stage).filter(([key]) => !attachmentKeys.has(key)))),
  );
}
export function hasProviderRecord(value: Json | undefined) {
  const provider = record(value);
  return (
    provider.adopted === true ||
    Number(provider.total) > 0 ||
    items(provider.stages).length > 0 ||
    Object.keys(record(provider.provider_reference)).length > 0
  );
}
