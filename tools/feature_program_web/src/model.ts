export type Json = null | boolean | number | string | readonly Json[] | JsonRecord;
export type JsonRecord = { readonly [key: string]: Json };
export type Task = JsonRecord & {
  readonly id: string;
  readonly title: string;
  readonly status: string;
};
export type Snapshot = JsonRecord & {
  readonly snapshot_version: 1;
  readonly revision: string;
  readonly program_id: string;
  readonly tasks: readonly Task[];
};

function isRecord(value: Json | undefined): value is JsonRecord {
  return (
    value !== null && value !== undefined && typeof value === 'object' && !Array.isArray(value)
  );
}
export function record(value: Json | undefined): JsonRecord {
  return isRecord(value) ? value : {};
}
export function items(value: Json | undefined): readonly Json[] {
  return Array.isArray(value) ? value : [];
}
export function records(value: Json | undefined): readonly JsonRecord[] {
  return items(value).map(record);
}
export function display(value: Json | undefined, fallback = 'Unknown'): string {
  if (value === null || value === undefined || value === '') return fallback;
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
}
export function string(value: Json | undefined): string {
  return typeof value === 'string' ? value : '';
}
export function ownerOf(task: Task): string {
  return string(task.owner);
}
export function human(value: Json | undefined): string {
  return display(value).replaceAll('_', ' ');
}
export function statusLabel(value: Json | undefined): string {
  const labels: Readonly<Record<string, string>> = {
    done: 'Reviewed complete',
    active: 'In progress',
    planned: 'Planned',
    blocked: 'Blocked',
    review: 'In review',
  };
  return labels[string(value)] || human(value);
}
export function date(value: Json | undefined): string {
  const input = string(value);
  if (!input) return 'Not recorded';
  const parsed = new Date(input);
  return Number.isNaN(parsed.valueOf())
    ? 'Invalid recorded time'
    : parsed.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}
function isJson(value: unknown): value is Json {
  if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) return true;
  if (Array.isArray(value)) return value.every(isJson);
  return typeof value === 'object' && Object.values(value).every(isJson);
}
export function parseSnapshot(value: unknown): Snapshot {
  if (!isJson(value)) throw new Error('Invalid JSON snapshot');
  const data = record(value);
  if (
    data.snapshot_version !== 1 ||
    !string(data.revision) ||
    !string(data.program_id) ||
    !Array.isArray(data.tasks) ||
    data.tasks.length > 2000
  )
    throw new Error('Unsupported snapshot');
  const tasks = data.tasks.map((item) => {
    const task = record(item);
    const id = string(task.id);
    const title = string(task.title);
    const status = string(task.status);
    if (!id || !title || !['planned', 'active', 'blocked', 'review', 'done'].includes(status))
      throw new Error('Invalid task');
    return { ...task, id, title, status };
  });
  if (new Set(tasks.map((task) => task.id)).size !== tasks.length)
    throw new Error('Duplicate tasks');
  return {
    ...data,
    snapshot_version: 1,
    revision: string(data.revision),
    program_id: string(data.program_id),
    tasks,
  };
}
export function categoryTasks(snapshot: Snapshot, categoryId: string): ReadonlySet<string> {
  const categories = records(snapshot.categories);
  const visited = new Set<string>();
  const result = new Set<string>();
  const visit = (id: string): void => {
    if (visited.has(id)) return;
    visited.add(id);
    const category = categories.find((item) => item.id === id);
    items(category?.task_ids).forEach((taskId) => {
      if (typeof taskId === 'string') result.add(taskId);
    });
    snapshot.tasks.filter((task) => task.category_id === id).forEach((task) => result.add(task.id));
    categories.filter((item) => item.parent_id === id).forEach((item) => visit(string(item.id)));
  };
  visit(categoryId);
  return result;
}
export function safeUrl(value: Json | undefined): string | null {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' &&
      ['github.com', 'gitlab.com', 'dev.azure.com', 'bitbucket.org'].includes(url.hostname) &&
      !url.username &&
      !url.password &&
      !url.port
      ? url.href
      : null;
  } catch {
    return null;
  }
}
