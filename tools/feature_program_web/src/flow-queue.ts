import { record, string, items } from './model';
import type { Snapshot, JsonRecord } from './model';
import { currentCollection, currentReference } from './readiness';
import type { TaskFilters } from './selectors';

export const queueReadiness = ['ready', 'blocked', 'not_assessed', 'terminal'] as const;
export const queueFilters = ['open', ...queueReadiness] as const;
export const queueKinds = ['verification_lane', 'task_review', 'required_check_gap'] as const;
const queueActions = [
  'run',
  'wait',
  'resolve_blocker',
  'recheck',
  'assign',
  'review',
  'inspect',
  'none',
] as const;
export type QueueRow = {
  readonly id: string;
  readonly kind: string;
  readonly taskId: string;
  readonly lane: string;
  readonly checkIds: readonly string[];
  readonly phase: string;
  readonly result: string;
  readonly applicability: string;
  readonly readiness: string;
  readonly reasons: readonly string[];
  readonly action: string;
  readonly nextActorId: string;
  readonly nextAction: string;
  readonly nextTrigger: string;
  readonly verifierId: string;
  readonly resolverId: string;
  readonly ownerId: string;
  readonly supervisorId: string;
};
export type ReviewQueue = {
  readonly rows: readonly QueueRow[];
  readonly total: number;
  readonly omitted: number;
  readonly counts: Readonly<Record<string, Readonly<Record<string, number>>>>;
};
function counts(value: JsonRecord): Readonly<Record<string, number>> | null {
  const result: Record<string, number> = {};
  for (const [key, count] of Object.entries(value)) {
    if (typeof count !== 'number' || !Number.isSafeInteger(count) || count < 0) return null;
    result[key] = count;
  }
  return result;
}
const queueCache = new WeakMap<Snapshot, ReviewQueue | null>();
function parseQueue(snapshot: Snapshot): ReviewQueue | null {
  const value = record(snapshot.review_queue);
  if (
    value.schema_version !== 1 ||
    value.queue_version !== 1 ||
    value.status !== 'recorded' ||
    value.observed_revision !== snapshot.revision
  )
    return null;
  const collection = currentCollection(snapshot, value.rows, undefined, 50);
  if (!collection) return null;
  const completeCounts: Record<string, Readonly<Record<string, number>>> = {};
  for (const kind of ['kind', 'action', 'readiness']) {
    const entries = counts(record(record(value.counts)[kind]));
    if (
      !entries ||
      Object.values(entries).reduce((sum, count) => sum + count, 0) !== collection.total
    )
      return null;
    const validKeys =
      kind === 'kind' ? queueKinds : kind === 'action' ? queueActions : queueReadiness;
    if (Object.keys(entries).some((key) => !validKeys.some((value) => value === key))) return null;
    completeCounts[kind] = entries;
  }
  const rows: QueueRow[] = [];
  for (const item of collection.items) {
    const row = record(item),
      ready = record(row.readiness);
    const id = string(row.row_id) || string(row.id),
      taskId = string(row.task_id);
    if (
      !id ||
      !snapshot.tasks.some((task) => task.id === taskId) ||
      !currentReference(snapshot, row.reference, taskId) ||
      !queueKinds.some((kind) => kind === row.kind) ||
      !queueReadiness.some((status) => status === ready.status) ||
      !queueActions.some((action) => action === row.action)
    )
      return null;
    rows.push({
      id,
      kind: string(row.kind),
      taskId,
      lane: string(row.lane),
      checkIds: items(row.check_ids).map(string).filter(Boolean),
      phase: string(row.phase),
      result: string(row.result),
      applicability: string(row.applicability),
      readiness: string(ready.status),
      reasons: items(ready.reasons).map(string).filter(Boolean),
      action: string(row.action),
      nextActorId: string(row.next_actor_id),
      nextAction: string(row.next_action),
      nextTrigger: string(row.next_trigger),
      verifierId: string(row.verifier_id),
      resolverId: string(row.resolver_id),
      ownerId: string(row.owner_id),
      supervisorId: string(row.supervisor_id),
    });
  }
  if (new Set(rows.map((row) => row.id)).size !== rows.length) return null;
  return { rows, total: collection.total, omitted: collection.omitted, counts: completeCounts };
}
export function reviewQueue(snapshot: Snapshot): ReviewQueue | null {
  if (queueCache.has(snapshot)) return queueCache.get(snapshot) || null;
  const parsed = parseQueue(snapshot);
  queueCache.set(snapshot, parsed);
  return parsed;
}
export function queueActorIds(snapshot: Snapshot): readonly string[] {
  return (
    reviewQueue(snapshot)
      ?.rows.flatMap((row) => [
        row.nextActorId,
        row.verifierId,
        row.resolverId,
        row.ownerId,
        row.supervisorId,
      ])
      .filter(Boolean) || []
  );
}
export function queueRowsForTasks(
  snapshot: Snapshot,
  filters: TaskFilters,
  taskIds: ReadonlySet<string>,
) {
  return (
    reviewQueue(snapshot)?.rows.filter(
      (row) =>
        taskIds.has(row.taskId) &&
        (!filters.queue_readiness ||
          (filters.queue_readiness === 'open'
            ? row.readiness !== 'terminal'
            : row.readiness === filters.queue_readiness)) &&
        (!filters.owner ||
          (filters.owner === '__unassigned'
            ? !row.nextActorId
            : [
                row.nextActorId,
                row.verifierId,
                row.resolverId,
                row.ownerId,
                row.supervisorId,
              ].includes(filters.owner))),
    ) || []
  );
}
export const queueKindLabels: Readonly<Record<string, string>> = {
  verification_lane: 'Verification request',
  task_review: 'Submitted review',
  required_check_gap: 'Check coverage gap',
};
export const queueActionLabels: Readonly<Record<string, string>> = {
  run: 'Run the assigned check',
  wait: 'Wait for the recorded result',
  resolve_blocker: 'Resolve the blocker',
  recheck: 'Recheck current inputs',
  assign: 'Choose a reviewer',
  review: 'Review the submission',
  inspect: 'Inspect the missing facts',
  none: 'No next action recorded',
};
