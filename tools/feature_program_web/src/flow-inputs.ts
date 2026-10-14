import { items, record, string } from './model';
import type { Json, JsonRecord } from './model';
import { boundedCollection } from './readiness';

const comparisonStatuses = ['unchanged', 'changed', 'not_assessed', 'unavailable'] as const;
const inputGroups = [
  'source',
  'test',
  'config',
  'artifact',
  'environment',
  'method',
  'check',
] as const;
export type InputCounts = Readonly<Record<'known' | 'changed' | 'missing' | 'unknown', number>>;
export type InputGroup = {
  readonly id: string;
  readonly status: string;
  readonly counts: InputCounts;
};
export type InputSummary = {
  readonly status: string;
  readonly applicability: string;
  readonly completeness: string;
  readonly nextAction: string;
  readonly reasons: readonly string[];
  readonly counts: InputCounts;
  readonly groups: readonly InputGroup[];
  readonly omitted: number;
};
function inputCounts(value: JsonRecord): InputCounts | null {
  const { known, changed, missing, unknown } = value;
  const valid = (count: Json | undefined): count is number =>
    typeof count === 'number' && Number.isSafeInteger(count) && count >= 0;
  return valid(known) && valid(changed) && valid(missing) && valid(unknown)
    ? { known, changed, missing, unknown }
    : null;
}
export function inputSummary(value: Json | undefined): InputSummary | null {
  const data = record(value),
    counts = inputCounts(record(data.counts));
  const groups = boundedCollection(data.groups, 7);
  if (
    data.schema_version !== 1 ||
    !comparisonStatuses.some((status) => status === data.status) ||
    !['current', 'stale', 'unavailable'].includes(string(data.applicability)) ||
    !['complete', 'unknown'].includes(string(data.completeness)) ||
    !counts ||
    !groups
  )
    return null;
  const rows: InputGroup[] = [];
  for (const entry of groups.items) {
    const row = record(entry),
      groupCounts = inputCounts(record(row.counts));
    if (
      !inputGroups.some((id) => id === row.id) ||
      !comparisonStatuses.some((status) => status === row.status) ||
      !groupCounts
    )
      return null;
    rows.push({ id: string(row.id), status: string(row.status), counts: groupCounts });
  }
  if (new Set(rows.map((row) => row.id)).size !== rows.length) return null;
  return {
    status: string(data.status),
    applicability: string(data.applicability),
    completeness: string(data.completeness),
    nextAction: string(data.next_action),
    reasons: items(data.reasons).map(string).filter(Boolean),
    counts,
    groups: rows,
    omitted: groups.omitted,
  };
}
export const inputGroupLabels: Readonly<Record<string, string>> = {
  source: 'Source inputs',
  test: 'Test inputs',
  config: 'Configuration inputs',
  artifact: 'Artifact inputs',
  environment: 'Environment identity',
  method: 'Check method',
  check: 'Check declaration',
};
