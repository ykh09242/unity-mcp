import { items, ownerOf, string } from './model';
import type { Snapshot, Task } from './model';
import { defaultContext, emptyFilters, navigationHash, scopedTasks } from './navigation';
import type { WorkContext } from './navigation';
import { snapshotIndex } from './selectors';
import { originalValue } from './presentation';

export function workload(snapshot: Snapshot, scope: WorkContext['scope']) {
  const tasks = scopedTasks(snapshot, scope);
  const groups = new Map<string, Task[]>();
  for (const actor of snapshotIndex(snapshot).actors.keys()) groups.set(actor, []);
  groups.set('', []);
  for (const task of tasks) {
    const owner = ownerOf(task);
    const group = groups.get(owner) || [];
    group.push(task);
    groups.set(owner, group);
  }
  return [...groups].map(([owner, assignments]) => ({
    owner,
    total: assignments.length,
    planned: assignments.filter((task) => task.status === 'planned').length,
    active: assignments.filter((task) => task.status === 'active').length,
    blocked: assignments.filter((task) => task.status === 'blocked').length,
    review: assignments.filter((task) => task.status === 'review').length,
    done: assignments.filter((task) => task.status === 'done').length,
  }));
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
