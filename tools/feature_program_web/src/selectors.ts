import { reviewQueue } from './flow-queue';
import { display, items, ownerOf, record, records, string } from './model';
import type { Json, JsonRecord, Snapshot, Task } from './model';
import { actorLabel, originalValue } from './presentation';
import { executionPriority, matchesCoordination, verificationContext } from './throughput';

export type SnapshotIndex = {
  readonly tasks: ReadonlyMap<string, Task>;
  readonly actors: ReadonlyMap<string, JsonRecord>;
  readonly categories: ReadonlyMap<string, JsonRecord>;
  readonly questions: ReadonlyMap<string, JsonRecord>;
  readonly categoryChildren: ReadonlyMap<string, readonly JsonRecord[]>;
  readonly actorChildren: ReadonlyMap<string, readonly JsonRecord[]>;
  readonly tasksByOwner: ReadonlyMap<string, readonly Task[]>;
  readonly tasksByStatus: ReadonlyMap<string, readonly Task[]>;
  readonly reverseDependencies: ReadonlyMap<string, readonly Task[]>;
  readonly questionsByTask: ReadonlyMap<string, readonly JsonRecord[]>;
  readonly questionsByStatus: ReadonlyMap<string, readonly JsonRecord[]>;
  readonly evidenceLabels: ReadonlyMap<string, string>;
  readonly evidenceByPath: ReadonlyMap<string, readonly JsonRecord[]>;
  readonly searchText: ReadonlyMap<string, string>;
  readonly categoryTasks: (id: string) => ReadonlySet<string>;
};

// Derived caches belong to a single validated snapshot and disappear with it.
const cache = new WeakMap<Snapshot, SnapshotIndex>();
function append<T>(map: Map<string, T[]>, key: string, value: T): void {
  const group = map.get(key);
  if (group) group.push(value);
  else map.set(key, [value]);
}
function byId(values: readonly JsonRecord[]): Map<string, JsonRecord> {
  const result = new Map<string, JsonRecord>();
  for (const value of values) {
    const id = string(value.id);
    if (id && !result.has(id)) result.set(id, value);
  }
  return result;
}
export function snapshotIndex(snapshot: Snapshot): SnapshotIndex {
  const previous = cache.get(snapshot);
  if (previous) return previous;
  const tasks = new Map(snapshot.tasks.map((task) => [task.id, task]));
  const actors = byId(records(snapshot.actors));
  const categories = byId(records(snapshot.categories));
  const questions = byId(records(snapshot.questions));
  const categoryChildren = new Map<string, JsonRecord[]>();
  const actorChildren = new Map<string, JsonRecord[]>();
  const directCategories = new Map<string, Set<string>>();
  const tasksByOwner = new Map<string, Task[]>();
  const tasksByStatus = new Map<string, Task[]>();
  const reverseDependencies = new Map<string, Task[]>();
  const questionsByTask = new Map<string, JsonRecord[]>();
  const questionsByStatus = new Map<string, JsonRecord[]>();
  const evidenceLabels = new Map<string, string>();
  const evidenceByPath = new Map<string, JsonRecord[]>();
  const searchText = new Map<string, string>();
  const addCategory = (category: string, taskId: string) => {
    if (!category || !tasks.has(taskId)) return;
    const group = directCategories.get(category) || new Set<string>();
    group.add(taskId);
    directCategories.set(category, group);
  };
  for (const category of categories.values()) {
    append(categoryChildren, string(category.parent_id), category);
    for (const id of items(category.task_ids)) addCategory(string(category.id), string(id));
  }
  for (const actor of actors.values()) append(actorChildren, string(actor.parent_id), actor);
  for (const task of snapshot.tasks) {
    addCategory(string(task.category_id), task.id);
    append(tasksByOwner, ownerOf(task), task);
    append(tasksByStatus, task.status, task);
    for (const id of new Set(items(task.depends_on).map(string).filter(Boolean)))
      append(reverseDependencies, id, task);
    searchText.set(
      task.id,
      [
        task.id,
        task.title,
        task.source,
        task.notes,
        task.planning_note,
        task.next_action,
        originalValue(task).title,
        originalValue(task).source,
        originalValue(task).notes,
        originalValue(task).planning_note,
        ownerOf(task),
        actorLabel(snapshot, ownerOf(task)),
      ]
        .map((value) => display(value, ''))
        .join(' ')
        .toLowerCase(),
    );
  }
  for (const question of questions.values()) {
    append(questionsByStatus, string(question.status), question);
    for (const id of new Set(items(question.affected_task_ids).map(string).filter(Boolean)))
      append(questionsByTask, id, question);
  }
  // Index authored frozen references, preserving their records rather than inferring acceptance.
  const pending: Json[] = [snapshot];
  while (pending.length) {
    const value = pending.pop();
    if (Array.isArray(value)) pending.push(...value);
    else if (value && typeof value === 'object') {
      const entry = record(value);
      const path = string(entry.path);
      if (path && string(entry.sha256)) {
        append(evidenceByPath, path, entry);
        if (!evidenceLabels.has(path)) evidenceLabels.set(path, string(entry.label) || path);
      }
      pending.push(...Object.values(entry));
    }
  }
  const descendants = new Map<string, ReadonlySet<string>>();
  const categoryTasks = (id: string): ReadonlySet<string> => {
    const cached = descendants.get(id);
    if (cached) return cached;
    const result = new Set<string>();
    const visited = new Set<string>();
    const pendingIds = [id];
    while (pendingIds.length) {
      const current = pendingIds.pop();
      if (current === undefined || visited.has(current)) continue;
      visited.add(current);
      for (const taskId of directCategories.get(current) || []) result.add(taskId);
      for (const child of categoryChildren.get(current) || []) pendingIds.push(string(child.id));
    }
    descendants.set(id, result);
    return result;
  };
  const result: SnapshotIndex = {
    tasks,
    actors,
    categories,
    questions,
    categoryChildren,
    actorChildren,
    tasksByOwner,
    tasksByStatus,
    reverseDependencies,
    questionsByTask,
    questionsByStatus,
    evidenceLabels,
    evidenceByPath,
    searchText,
    categoryTasks,
  };
  cache.set(snapshot, result);
  return result;
}

export type TaskFilters = {
  readonly search: string;
  readonly status: string;
  readonly category: string;
  readonly owner: string;
  readonly workstream: string;
  readonly sort: string;
  readonly coordination?: string;
  readonly queue_readiness?: string;
};
export function selectTasks(snapshot: Snapshot, filters: TaskFilters): readonly Task[] {
  const index = snapshotIndex(snapshot);
  const members = filters.category ? index.categoryTasks(filters.category) : null;
  const query = filters.search.toLowerCase();
  const filtered = snapshot.tasks.filter(
    (task) =>
      (!filters.status || task.status === filters.status) &&
      (!members || members.has(task.id)) &&
      (!filters.workstream || task.workstream === filters.workstream) &&
      (!filters.owner ||
        (filters.owner === '__unassigned'
          ? filters.coordination === 'review_queue'
            ? Boolean(
                reviewQueue(snapshot)?.rows.some(
                  (row) => row.taskId === task.id && !row.nextActorId,
                ),
              )
            : !ownerOf(task)
          : ownerOf(task) === filters.owner ||
            (filters.coordination === 'review_queue' &&
              reviewQueue(snapshot)?.rows.some(
                (row) =>
                  row.taskId === task.id &&
                  [
                    row.nextActorId,
                    row.verifierId,
                    row.resolverId,
                    row.ownerId,
                    row.supervisorId,
                  ].includes(filters.owner),
              )) ||
            (['verification', 'waiting'].includes(filters.coordination || '') &&
              verificationContext(snapshot, task)?.lanes.items.some(
                (lane) =>
                  lane.verifier_id === filters.owner ||
                  lane.resolver_id === filters.owner ||
                  record(lane.blocker).resolver_id === filters.owner,
              )))) &&
      matchesCoordination(snapshot, task, filters.coordination) &&
      (!query || index.searchText.get(task.id)?.includes(query)),
  );
  if (filters.sort === 'priority') {
    const positions = new Map(snapshot.tasks.map((task, index) => [task.id, index]));
    const recorded =
      snapshot.execution_priorities !== undefined && snapshot.execution_priorities !== null;
    return filtered.sort(
      (left, right) =>
        (recorded ? executionPriority(snapshot, left) - executionPriority(snapshot, right) : 0) ||
        Number(left.phase || 0) - Number(right.phase || 0) ||
        (recorded
          ? left.id < right.id
            ? -1
            : left.id > right.id
              ? 1
              : 0
          : positions.get(left.id)! - positions.get(right.id)!),
    );
  }
  return filtered.sort(
    (left, right) =>
      display(
        filters.sort === 'owner' ? actorLabel(snapshot, ownerOf(left)) : left[filters.sort],
      ).localeCompare(
        display(
          filters.sort === 'owner' ? actorLabel(snapshot, ownerOf(right)) : right[filters.sort],
        ),
        string(snapshot.communication_language) === 'ko' ? 'ko' : 'en',
        { numeric: true },
      ) || left.id.localeCompare(right.id),
  );
}
