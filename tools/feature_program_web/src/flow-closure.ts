import { record, string, items } from './model';
import type { Snapshot } from './model';
import { boundedCollection, currentReference } from './readiness';

export type ClosureRequirement = {
  readonly kind: string;
  readonly taskId: string;
  readonly status: string;
  readonly reason: string;
  readonly nextActorId: string;
  readonly nextAction: string;
};
export type Closure = {
  readonly status: string;
  readonly accepted: boolean;
  readonly objectiveId: string;
  readonly taskIds: readonly string[];
  readonly nextActorId: string;
  readonly nextAction: string;
  readonly requirements: readonly ClosureRequirement[];
  readonly total: number;
  readonly omitted: number;
};
export function closureContext(snapshot: Snapshot): Closure | null {
  const value = record(snapshot.closure),
    requirements = boundedCollection(value.requirements, 50);
  if (
    value.schema_version !== 1 ||
    !['incomplete', 'ready', 'accepted', 'unavailable'].includes(string(value.status)) ||
    !currentReference(snapshot, value.reference) ||
    value.objective_id !== record(snapshot.objective).id ||
    typeof value.accepted !== 'boolean' ||
    (value.status === 'accepted') !== value.accepted ||
    value.remote_verified_by_tool !== false ||
    !requirements ||
    !Array.isArray(value.task_ids)
  )
    return null;
  const rows: ClosureRequirement[] = [];
  for (const entry of requirements.items) {
    const row = record(entry);
    if (!string(row.kind) || !string(row.status)) return null;
    rows.push({
      kind: string(row.kind),
      taskId: string(row.task_id),
      status: string(row.status),
      reason: string(row.reason),
      nextActorId: string(row.next_actor_id),
      nextAction: string(row.next_action),
    });
  }
  return {
    status: string(value.status),
    accepted: value.accepted,
    objectiveId: string(value.objective_id),
    taskIds: items(value.task_ids).map(string).filter(Boolean),
    nextActorId: string(value.next_actor_id),
    nextAction: string(value.next_action),
    requirements: rows,
    total: requirements.total,
    omitted: requirements.omitted,
  };
}
