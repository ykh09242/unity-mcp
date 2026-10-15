import { items, record, records, semanticValueEqual, string } from './model';
import type { JsonRecord, Snapshot } from './model';
import { originalValue } from './presentation';
import { textDigest } from './presentation-digest';

function completionBinding(snapshot: Snapshot) {
  const coverage = record(snapshot.coverage);
  const objective = record(originalValue(snapshot.objective));
  const acceptance = record(originalValue(coverage.recorded_objective_acceptance));
  const completion = record(objective.completion);
  const receipt = record(acceptance.receipt);
  const expectedReceipt = records(objective.evidence)[0];
  if (
    !string(objective.id) ||
    coverage.objective_id !== objective.id ||
    completion.objective_id !== objective.id ||
    acceptance.status !== 'recorded' ||
    !expectedReceipt ||
    !string(receipt.path) ||
    !/^[a-f0-9]{64}$/u.test(string(receipt.sha256)) ||
    receipt.path !== expectedReceipt.path ||
    receipt.sha256 !== expectedReceipt.sha256 ||
    records(objective.evidence).filter(
      (ref) => ref.path === receipt.path && ref.sha256 === receipt.sha256,
    ).length !== 1 ||
    !Array.isArray(completion.criteria_results) ||
    !semanticValueEqual(completion.criteria_results, acceptance.criteria_results)
  )
    return null;
  const criteria = items(objective.criteria);
  const textCounts = new Map<string, number>();
  for (const criterion of criteria)
    if (typeof criterion === 'string')
      textCounts.set(criterion, (textCounts.get(criterion) || 0) + 1);
  const results = new Map<string, JsonRecord[]>();
  for (const result of records(acceptance.criteria_results)) {
    if (typeof result.criterion !== 'string') continue;
    const group = results.get(result.criterion) || [];
    group.push(result);
    results.set(result.criterion, group);
  }
  return {
    criteria,
    textCounts,
    results,
    receipt,
    sourceFreshness: acceptance.source_freshness,
    headFreshness: acceptance.head_freshness,
  };
}

const bindings = new WeakMap<Snapshot, ReturnType<typeof completionBinding>>();
export function recordedCriterionResult(snapshot: Snapshot, identity: JsonRecord) {
  if (!bindings.has(snapshot)) bindings.set(snapshot, completionBinding(snapshot));
  const binding = bindings.get(snapshot);
  const ordinal = identity.ordinal;
  if (!binding || typeof ordinal !== 'number' || !Number.isSafeInteger(ordinal) || ordinal < 0)
    return null;
  const originalText = binding.criteria[ordinal];
  if (
    typeof originalText !== 'string' ||
    textDigest(originalText) !== identity.text_sha256 ||
    binding.textCounts.get(originalText) !== 1
  )
    return null;
  const matches = binding.results.get(originalText) || [];
  const result = matches.length === 1 ? matches[0] : undefined;
  return result && string(result.status)
    ? {
        result,
        receipt: binding.receipt,
        sourceFreshness: binding.sourceFreshness,
        headFreshness: binding.headFreshness,
      }
    : null;
}
