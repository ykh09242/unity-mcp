import { items, ownerOf, string } from './model';
import type { Snapshot, Task } from './model';
import { defaultContext, emptyFilters, navigationHash, scopedTasks } from './navigation';
import type { WorkContext } from './navigation';
import { snapshotIndex } from './selectors';
import { originalValue } from './presentation';

export function workload(snapshot: Snapshot, scope: WorkContext['scope']) {
  const tasks = scopedTasks(snapshot, scope);
  const empty = (owner: string) => ({
    owner,
    total: 0,
    planned: 0,
    active: 0,
    blocked: 0,
    review: 0,
    done: 0,
  });
  const groups = new Map<string, ReturnType<typeof empty>>();
  for (const actor of snapshotIndex(snapshot).actors.keys()) groups.set(actor, empty(actor));
  groups.set('', empty(''));
  for (const task of tasks) {
    const owner = ownerOf(task);
    const group = groups.get(owner) || empty(owner);
    group.total++;
    if (
      task.status === 'planned' ||
      task.status === 'active' ||
      task.status === 'blocked' ||
      task.status === 'review' ||
      task.status === 'done'
    )
      group[task.status]++;
    groups.set(owner, group);
  }
  return [...groups.values()];
}

export function hasActionGlance(task: Task): boolean {
  return !(
    task.status === 'done' &&
    string(originalValue(task).next_action).trim() ===
      'Reviewed complete; delivery/CI is tracked separately.'
  );
}

export function workloadHref(scope: WorkContext['scope'], owner: string, status = '') {
  return navigationHash(
    { view: 'work' },
    {
      ...defaultContext,
      scope,
      filters: { ...emptyFilters, owner: owner || '__unassigned', status },
    },
  );
}

export function taskNeighborhood(snapshot: Snapshot, task: Task) {
  const index = snapshotIndex(snapshot);
  const references = (value: Task[string] | undefined) =>
    [...new Set(items(value).map(string).filter(Boolean))].map((id) => ({
      id,
      task: index.tasks.get(id),
    }));
  return {
    upstream: references(task.depends_on),
    downstream: (index.reverseDependencies.get(task.id) || []).map((peer) => ({
      id: peer.id,
      task: peer,
    })),
    related: references(task.related_to),
  };
}
