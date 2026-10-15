import { record, string, items, semanticValueEqual } from './model';
import type { Json, Snapshot } from './model';
import { originalValue } from './presentation';
import { textDigest } from './presentation-digest';
import { finiteCount } from './review-throughput';
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
function parseClosure(snapshot: Snapshot): Closure | null {
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

const closureCache = new WeakMap<Snapshot, Closure | null>();
export function closureContext(snapshot: Snapshot): Closure | null {
  if (!closureCache.has(snapshot)) closureCache.set(snapshot, parseClosure(snapshot));
  return closureCache.get(snapshot) ?? null;
}

export type IntegratedObligation = {
  readonly taskId: string;
  readonly checkId: string;
  readonly status: string;
  readonly actionable: boolean;
  readonly basis: string;
  readonly reasons: readonly string[];
  readonly nextActorId: string;
  readonly nextAction: string;
  readonly nextTrigger: string;
};
export type IntegratedReview = {
  readonly status: string;
  readonly currentness: string;
  readonly identity: string;
  readonly reasons: readonly string[];
  readonly memberIds: readonly string[];
  readonly acceptedMemberIds: readonly string[];
  readonly obligations: readonly IntegratedObligation[];
  readonly counts: Readonly<
    Record<
      | 'members'
      | 'accepted_members'
      | 'obligations'
      | 'fulfilled'
      | 'unmapped'
      | 'historical'
      | 'actionable',
      number
    >
  >;
  readonly nextActorId: string;
  readonly nextAction: string;
  readonly nextTrigger: string;
};
function textList(value: Json | undefined): value is readonly string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}
function nullableText(value: Json | undefined) {
  return value === null || typeof value === 'string';
}
function frozenRef(value: Json | undefined) {
  const ref = record(value);
  return Boolean(string(ref.path)) && /^[a-f0-9]{64}$/iu.test(string(ref.sha256));
}
export function integratedReviewProvided(snapshot: Snapshot): boolean {
  const raw = record(snapshot.coverage).integrated_review;
  const value = record(raw);
  return raw !== undefined && !(Object.keys(value).length === 1 && value.status === 'not_assessed');
}
function parseIntegratedReview(snapshot: Snapshot): IntegratedReview | null {
  const coverage = record(snapshot.coverage),
    value = record(coverage.integrated_review);
  const counts = record(value.counts),
    basis = record(value.basis);
  const objective = record(originalValue(snapshot.objective));
  const countKeys = [
    'members',
    'accepted_members',
    'obligations',
    'fulfilled',
    'unmapped',
    'historical',
    'actionable',
  ] as const;
  if (
    value.schema_version !== 1 ||
    string(coverage.objective_id) !== string(objective.id) ||
    !['accepted', 'not_accepted', 'not_assessed', 'unavailable'].includes(string(value.status)) ||
    !['current', 'stale', 'unknown', 'unavailable'].includes(string(value.currentness)) ||
    value.remote_verified_by_tool !== false ||
    !textList(value.reason_codes) ||
    !countKeys.every((key) => finiteCount(counts[key])) ||
    !Array.isArray(value.members) ||
    !Array.isArray(value.obligations) ||
    counts.members !== value.members.length ||
    counts.obligations !== value.obligations.length ||
    Number(counts.accepted_members) > Number(counts.members) ||
    ['fulfilled', 'unmapped', 'historical', 'actionable'].some(
      (key) => Number(counts[key]) > Number(counts.obligations),
    ) ||
    ![value.next_actor_id, value.next_action, value.next_trigger].every(nullableText)
  )
    return null;
  if (value.basis !== null) {
    const binding = record(basis.objective_binding);
    const members = items(objective.task_ids).map(string).sort();
    if (
      !string(objective.id) ||
      binding.program_id !== snapshot.program_id ||
      binding.objective_id !== objective.id ||
      !semanticValueEqual(binding.criteria, objective.criteria) ||
      !semanticValueEqual(binding.member_ids, members) ||
      binding.membership_revision !== (objective.membership_revision ?? null) ||
      !/^[a-f0-9]{64}$/iu.test(string(binding.criteria_sha256)) ||
      !/^[a-f0-9]{64}$/iu.test(string(basis.identity)) ||
      !/^[a-f0-9]{64}$/iu.test(string(basis.source_digest)) ||
      !string(basis.source_head) ||
      !frozenRef(basis.completion_ref) ||
      !frozenRef(basis.integrated_review_ref)
    )
      return null;
  } else if (value.status === 'accepted') return null;
  const memberIds: string[] = [];
  const acceptedMemberIds: string[] = [];
  for (const input of value.members) {
    const member = record(input),
      id = string(member.task_id);
    if (
      !snapshot.tasks.some((task) => task.id === id) ||
      !['accepted', 'requires_recheck', 'not_assessed'].includes(string(member.status)) ||
      !(member.attempt === null || (finiteCount(member.attempt) && member.attempt > 0)) ||
      !Array.isArray(member.obligations)
    )
      return null;
    memberIds.push(id);
    if (member.status === 'accepted') acceptedMemberIds.push(id);
  }
  if (
    new Set(memberIds).size !== memberIds.length ||
    counts.accepted_members !== acceptedMemberIds.length
  )
    return null;
  const obligations: IntegratedObligation[] = [];
  for (const input of value.obligations) {
    const row = record(input),
      criterion = record(row.criterion);
    if (
      !memberIds.includes(string(row.task_id)) ||
      !nullableText(row.check_id) ||
      !(row.attempt === null || (finiteCount(row.attempt) && row.attempt > 0)) ||
      ![
        'fulfilled',
        'historical_only',
        'unmapped',
        'requires_recheck',
        'pending',
        'adverse',
        'unavailable',
      ].includes(string(row.status)) ||
      !['current_execution', 'integrated_mapping', 'integrated_member', 'none'].includes(
        string(row.basis),
      ) ||
      typeof row.actionable !== 'boolean' ||
      !textList(row.reason_codes) ||
      ![
        row.next_actor_id,
        row.next_action,
        row.next_trigger,
        row.request_id,
        row.observation_id,
      ].every(nullableText)
    )
      return null;
    if (
      row.criterion !== null &&
      (!finiteCount(criterion.ordinal) ||
        typeof items(objective.criteria)[criterion.ordinal] !== 'string' ||
        textDigest(string(items(objective.criteria)[criterion.ordinal])) !== criterion.text_sha256)
    )
      return null;
    obligations.push({
      taskId: string(row.task_id),
      checkId: string(row.check_id),
      status: string(row.status),
      actionable: row.actionable,
      basis: string(row.basis),
      reasons: row.reason_codes,
      nextActorId: string(row.next_actor_id),
      nextAction: string(row.next_action),
      nextTrigger: string(row.next_trigger),
    });
  }
  if (
    obligations.some((row) => row.actionable && acceptedMemberIds.includes(row.taskId)) ||
    (value.status === 'accepted' && obligations.some((row) => row.actionable))
  )
    return null;
  return {
    status: string(value.status),
    currentness: string(value.currentness),
    identity: string(basis.identity),
    reasons: value.reason_codes,
    memberIds,
    acceptedMemberIds,
    obligations,
    counts: {
      members: Number(counts.members),
      accepted_members: Number(counts.accepted_members),
      obligations: Number(counts.obligations),
      fulfilled: Number(counts.fulfilled),
      unmapped: Number(counts.unmapped),
      historical: Number(counts.historical),
      actionable: Number(counts.actionable),
    },
    nextActorId: string(value.next_actor_id),
    nextAction: string(value.next_action),
    nextTrigger: string(value.next_trigger),
  };
}
const integratedCache = new WeakMap<Snapshot, IntegratedReview | null>();
export function integratedReview(snapshot: Snapshot): IntegratedReview | null {
  if (!integratedCache.has(snapshot))
    integratedCache.set(snapshot, parseIntegratedReview(snapshot));
  return integratedCache.get(snapshot) ?? null;
}

export function integratedReviewVisible(snapshot: Snapshot): boolean {
  if (!integratedReviewProvided(snapshot)) return false;
  const review = integratedReview(snapshot);
  return (
    !review ||
    Boolean(review.identity) ||
    record(snapshot.objective).completion != null ||
    review.reasons.some((reason) => !['completion_absent', 'objective_absent'].includes(reason))
  );
}
