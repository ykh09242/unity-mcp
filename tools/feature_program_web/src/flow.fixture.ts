import { record, parseSnapshot } from './model';
import type { JsonRecord, Snapshot } from './model';

export const flowReference = (taskId?: string) => ({
  path: 'backlog.json',
  revision: 'flow-one',
  pointer: taskId ? '/tasks/' + taskId : '/tasks',
  ...(taskId ? { task_id: taskId } : {}),
});
export const flowCollection = (rows: readonly JsonRecord[], total = rows.length) => ({
  total,
  shown: rows.length,
  omitted: total - rows.length,
  items: rows,
  reference: flowReference(),
});
export const flowInput: JsonRecord = {
  schema_version: 1,
  status: 'changed',
  applicability: 'stale',
  completeness: 'unknown',
  next_action: 'Recheck current inputs',
  reasons: ['An input changed'],
  counts: { known: 2, changed: 1, missing: 1, unknown: 1 },
  groups: {
    total: 2,
    shown: 2,
    omitted: 0,
    items: [
      { id: 'test', status: 'changed', counts: { known: 2, changed: 1, missing: 0, unknown: 0 } },
      {
        id: 'environment',
        status: 'not_assessed',
        counts: { known: 0, changed: 0, missing: 1, unknown: 1 },
      },
    ],
  },
};
export const flowExecution: JsonRecord = {
  schema_version: 1,
  run_id: 'actual-run-1',
  phase: 'finished',
  process_result: 'exited',
  reported_check_result: 'passed',
  capture_status: 'complete',
  status: 'captured',
  reused: false,
  artifacts: { expected: 2, captured: 1, missing: 1 },
  publication: { status: 'pending' },
  output_counts: {
    stdout: { bytes_seen: 10, bytes_retained: 10, truncated: false },
    stderr: { bytes_seen: 20, bytes_retained: 5, truncated: true },
  },
  next_action: 'Inspect the missing facts',
  reasons: ['A requested artifact is missing'],
  started_at: '2026-10-14T10:00:00Z',
  ended_at: '2026-10-14T10:00:03Z',
};
export const flowQueueRow = (id: string, taskId = 'A', changes: JsonRecord = {}): JsonRecord => ({
  id,
  row_id: id,
  kind: 'verification_lane',
  task_id: taskId,
  attempt: 1,
  request_id: id,
  request_version: 1,
  lane: 'qa',
  check_ids: ['acceptance'],
  phase: 'requested',
  result: 'pending',
  applicability: 'current',
  readiness: { status: 'ready', reasons: [] },
  action: 'run',
  next_actor_id: 'reviewer',
  next_action: 'Run the assigned check',
  next_trigger: 'Wait for the recorded result',
  verifier_id: 'reviewer',
  resolver_id: null,
  owner_id: 'worker',
  supervisor_id: null,
  reference: flowReference(taskId),
  ...changes,
});
export function flowQueue(rows: readonly JsonRecord[], total = rows.length): JsonRecord {
  const kind: Record<string, number> = {},
    action: Record<string, number> = {},
    readiness: Record<string, number> = {};
  for (const row of rows) {
    const rowKind = String(row.kind),
      rowAction = String(row.action);
    const rowReadiness = String(record(row.readiness).status || 'not_assessed');
    kind[rowKind] = (kind[rowKind] || 0) + 1;
    action[rowAction] = (action[rowAction] || 0) + 1;
    readiness[rowReadiness] = (readiness[rowReadiness] || 0) + 1;
  }
  if (total > rows.length) {
    kind.required_check_gap = (kind.required_check_gap || 0) + total - rows.length;
    action.assign = (action.assign || 0) + total - rows.length;
    readiness.not_assessed = (readiness.not_assessed || 0) + total - rows.length;
  }
  return {
    schema_version: 1,
    queue_version: 1,
    status: 'recorded',
    observed_revision: 'flow-one',
    rows: flowCollection(rows, total),
    counts: { kind, action, readiness },
  };
}
export function flowFixture(extra: JsonRecord = {}): Snapshot {
  return parseSnapshot({
    snapshot_version: 1,
    program_id: 'flow-test',
    revision: 'flow-one',
    generated_at: '2026-10-14T10:01:00Z',
    integrity: { status: 'fresh', warnings: [] },
    objective: { id: 'goal-flow', title: 'Verify the flow', task_ids: ['A', 'B'] },
    actors: [
      { id: 'reviewer', role: 'reviewer' },
      { id: 'resolver', role: 'worker' },
    ],
    brief: { brief_version: 1 },
    verification: { schema_version: 1, status: 'recorded' },
    tasks: [
      {
        id: 'A',
        title: 'First flow task',
        status: 'review',
        owner: 'worker',
        source: { text: 'One task description.' },
        next_action: 'Inspect current work',
        verification: {
          task_id: 'A',
          reference: flowReference('A'),
          lanes: {
            ...flowCollection([
              {
                request_id: 'request-A',
                request_version: 1,
                lane: 'qa',
                phase: 'finished',
                result: 'passed',
                applicability: 'current',
                verifier_id: 'reviewer',
                next_trigger: 'Inspect current work',
                check_ids: ['acceptance'],
                input_summary: flowInput,
                execution_summary: flowExecution,
              },
            ]),
            reference: flowReference('A'),
          },
          findings: { ...flowCollection([]), reference: flowReference('A') },
        },
      },
      {
        id: 'B',
        title: 'Second flow task',
        status: 'active',
        owner: 'other',
        source: { text: 'Second task description.' },
      },
    ],
    review_queue: flowQueue([flowQueueRow('queue-A')]),
    ...extra,
  });
}
