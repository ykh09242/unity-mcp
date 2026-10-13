import { record, string } from './model';
import type { Json, JsonRecord, Snapshot, Task } from './model';

export function currentReference(
  snapshot: Snapshot,
  value: Json | undefined,
  taskId?: string,
): boolean {
  const ref = record(value);
  return (
    ref.path === 'backlog.json' &&
    ref.revision === snapshot.revision &&
    typeof ref.pointer === 'string' &&
    (!taskId || ref.task_id === taskId)
  );
}

export function recordedReadiness(snapshot: Snapshot, task: Task): JsonRecord | null {
  const value = record(task.readiness);
  return record(snapshot.brief).brief_version === 1 &&
    currentReference(snapshot, value.reference, task.id)
    ? value
    : null;
}

export function boundedCollection(value: Json | undefined, limit = 20) {
  const data = record(value);
  const { total, shown, omitted } = data;
  if (
    typeof total !== 'number' ||
    typeof shown !== 'number' ||
    typeof omitted !== 'number' ||
    ![total, shown, omitted].every(Number.isSafeInteger) ||
    total < 0 ||
    shown < 0 ||
    omitted < 0 ||
    shown + omitted !== total ||
    !Array.isArray(data.items) ||
    data.items.length !== shown
  )
    return null;
  const rows = data.items.slice(0, limit);
  return {
    items: rows,
    total,
    shown: rows.length,
    omitted: total - rows.length,
    reference: data.reference,
  };
}

export function authoredText(value: Json | undefined, limit = 512) {
  const original = string(value);
  const points = Array.from(original);
  return { text: points.slice(0, limit).join(''), truncated: points.length > limit };
}

export function currentCollection(
  snapshot: Snapshot,
  value: Json | undefined,
  taskId?: string,
  limit = 20,
) {
  const collection = boundedCollection(value, limit);
  return collection && currentReference(snapshot, collection.reference, taskId) ? collection : null;
}
