import { record, string, items } from './model';
import type { Json, Snapshot } from './model';
import { boundedCollection } from './readiness';

export type PlanStage = {
  readonly id: string;
  readonly operation: string;
  readonly taskId: string;
  readonly status: string;
  readonly nextActorId: string;
  readonly nextAction: string;
  readonly viewRecovery: boolean;
};
export type ClosurePlan = {
  readonly digest: string;
  readonly preparation: string;
  readonly preflight: string;
  readonly preflightCurrent: boolean;
  readonly publication: string;
  readonly nextActorId: string;
  readonly nextAction: string;
  readonly reasons: readonly string[];
  readonly stages: readonly PlanStage[];
  readonly total: number;
  readonly omitted: number;
};
export function selectedPlan(snapshot: Snapshot, input: Json | undefined): ClosurePlan | null {
  const value = record(input),
    preparation = record(value.preparation),
    preflight = record(value.preflight);
  const publication = record(value.publication),
    stages = boundedCollection(publication.stages, 50);
  const reference = record(value.reference);
  if (
    value.schema_version !== 1 ||
    value.observed_revision !== snapshot.revision ||
    (value.plan_digest !== undefined && !/^[a-f0-9]{64}$/iu.test(string(value.plan_digest))) ||
    !string(reference.path) ||
    !/^[a-f0-9]{64}$/iu.test(string(reference.sha256)) ||
    !['prepared', 'stale', 'failed'].includes(string(preparation.status)) ||
    !['validated', 'not_assessed', 'failed'].includes(string(preflight.status)) ||
    typeof preflight.current !== 'boolean' ||
    !['not_started', 'partial', 'published', 'view_recovery_required', 'blocked'].includes(
      string(publication.status),
    ) ||
    value.accepted !== false ||
    value.remote_verified_by_tool !== false ||
    !stages
  )
    return null;
  const rows: PlanStage[] = [];
  for (const entry of stages.items) {
    const row = record(entry);
    if (
      !string(row.id) ||
      !string(row.operation) ||
      !string(row.status) ||
      typeof row.view_recovery_required !== 'boolean'
    )
      return null;
    rows.push({
      id: string(row.id),
      operation: string(row.operation),
      taskId: string(row.task_id),
      status: string(row.status),
      nextActorId: string(row.next_actor_id),
      nextAction: string(row.next_action),
      viewRecovery: row.view_recovery_required,
    });
  }
  if (new Set(rows.map((row) => row.id)).size !== rows.length) return null;
  return {
    digest: string(value.plan_digest) || string(reference.sha256),
    preparation: string(preparation.status),
    preflight: string(preflight.status),
    preflightCurrent: preflight.current,
    publication: string(publication.status),
    nextActorId: string(value.next_actor_id),
    nextAction: string(value.next_action),
    reasons: items(value.reasons).map(string).filter(Boolean),
    stages: rows,
    total: stages.total,
    omitted: stages.omitted,
  };
}
