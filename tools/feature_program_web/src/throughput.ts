import { items, record, string } from './model';
import type { Json, JsonRecord, Snapshot, Task } from './model';
import { currentCollection, currentReference, recordedReadiness } from './readiness';

export const coordinationViews = [
  'available',
  'blocked',
  'verification',
  'waiting',
  'unknown',
] as const;
export const coordinationLabels: Readonly<Record<string, string>> = {
  available: 'No recorded blockers',
  blocked: 'Recorded blockers',
  verification: 'Verification needs',
  waiting: 'Observed waiting',
  unknown: 'Coordination unavailable',
};
export function readinessFactors(snapshot: Snapshot, task: Task) {
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
const phases = new Set(['requested', 'running', 'blocked', 'finished', 'canceled']);
const results = new Set(['passed', 'failed', 'pending', 'not_run', 'unknown', 'not_applicable']);
const applicability = new Set(['current', 'stale', 'unavailable', 'unknown']);
export function verificationContext(snapshot: Snapshot, task: Task) {
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
export function laneNeedsAction(lane: JsonRecord) {
  return (
    lane.applicability !== 'current' ||
    lane.result === 'failed' ||
    lane.phase === 'blocked' ||
    ['requested', 'running'].includes(string(lane.phase)) ||
    (lane.phase === 'finished' && ['unknown', 'pending', 'not_run'].includes(string(lane.result)))
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
  const factors = readinessFactors(snapshot, task);
  const verification = verificationContext(snapshot, task);
  if (filter === 'available') return factors.available;
  if (filter === 'blocked')
    return (
      task.status === 'blocked' ||
      factors.stale ||
      Number(factors.blockers) > 0 ||
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
    filter === 'unknown' && (!factors.known || !verification || verification.lanes.omitted > 0)
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
    !['passed', 'failed', 'pending', 'not_assessed'].includes(string(provider.status)) ||
    !Array.isArray(provider.stages) ||
    !validCounts(provider, '', provider.stages)
  )
    return null;
  const stages = provider.stages.map(record);
  if (
    new Set(stages.map((stage) => stage.id)).size !== stages.length ||
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
