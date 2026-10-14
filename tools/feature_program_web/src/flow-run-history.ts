import { boundedCollection } from './readiness';
import { record, string, items } from './model';
import type { Json } from './model';
import { capturedExecution } from './flow-execution';
import type { CapturedExecution } from './flow-execution';

export type RunHistoryRow = {
  readonly id: string;
  readonly execution: CapturedExecution | null;
  readonly nextAction: string;
  readonly reasons: readonly string[];
};
export function runHistory(value: Json | undefined) {
  const collection = boundedCollection(value, 50);
  if (!collection) return null;
  const rows: RunHistoryRow[] = [];
  for (const [index, item] of collection.items.entries()) {
    const data = record(item),
      execution = capturedExecution(item);
    if (!execution && !(data.schema_version === 1 && data.status === 'unavailable')) return null;
    rows.push({
      id: string(record(data.publication).observation_id) || string(data.run_id) + ':' + index,
      execution,
      nextAction: string(data.next_action),
      reasons: items(data.reasons).map(string).filter(Boolean),
    });
  }
  if (new Set(rows.map((row) => row.id)).size !== rows.length) return null;
  return { rows, total: collection.total, omitted: collection.omitted };
}
