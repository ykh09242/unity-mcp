import { items, record, records, semanticValueEqual, string } from './model';
import type { JsonRecord, Snapshot } from './model';
import { originalValue } from './presentation';
import { textDigest } from './presentation-digest';

export function recordedCriterionResult(snapshot: Snapshot, identity: JsonRecord) {
  const coverage = record(snapshot.coverage);
  const objective = record(originalValue(snapshot.objective));
  const acceptance = record(originalValue(coverage.recorded_objective_acceptance));
  const completion = record(objective.completion);
  const receipt = record(acceptance.receipt);
  const expectedReceipt = records(objective.evidence)[0];
  const ordinal = identity.ordinal;
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
    !semanticValueEqual(completion.criteria_results, acceptance.criteria_results) ||
    typeof ordinal !== 'number' ||
    !Number.isSafeInteger(ordinal) ||
    ordinal < 0
  )
    return null;
  const originalText = items(objective.criteria)[ordinal];
  if (
    typeof originalText !== 'string' ||
    textDigest(originalText) !== identity.text_sha256 ||
    items(objective.criteria).filter((text) => text === originalText).length !== 1
  )
    return null;
  const matches = records(acceptance.criteria_results).filter(
    (result) =>
      result.criterion === originalText &&
      textDigest(string(result.criterion)) === identity.text_sha256,
  );
  const result = matches.length === 1 ? matches[0] : undefined;
  return result && string(result.status)
    ? {
        result,
        receipt,
        sourceFreshness: acceptance.source_freshness,
        headFreshness: acceptance.head_freshness,
      }
    : null;
}
