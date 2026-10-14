import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { parseSnapshot } from './model';
import { defaultContext, parseNavigation, selectWorkTasks } from './navigation';
import { hasActionGlance, workload, workloadHref, taskNeighborhood } from './relate';
import { DependencyNeighborhood, Workload } from './relate-ui';
import { IconButton, IconLink } from './icons';

const fixture = () =>
  parseSnapshot({
    snapshot_version: 1,
    revision: 'one',
    program_id: 'relations',
    objective: { task_ids: ['A', 'B', 'missing', 'A'] },
    actors: [{ id: 'worker', assignments: [{ task_id: 'C', status: 'active' }] }, { id: 'idle' }],
    tasks: [
      {
        id: 'A',
        title: 'Alpha',
        status: 'active',
        owner: 'worker',
        depends_on: ['B', 'B', 'missing'],
        related_to: ['C'],
      },
      { id: 'B', title: 'Beta', status: 'blocked', owner: null },
      { id: 'C', title: 'Gamma', status: 'review', owner: 'external', depends_on: ['B'] },
      { id: 'D', title: 'Delta', status: 'planned', owner: 'worker', related_to: ['B'] },
    ],
  });

describe('recorded workload and dependency context', () => {
  it('separates completed cleared owners from planned unassigned work and preserves bespoke next actions', async () => {
    const standard = 'Reviewed complete; delivery/CI is tracked separately.';
    const snapshot = parseSnapshot({
      ...fixture(),
      tasks: [
        { id: 'D', title: 'Done', status: 'done', owner: null, next_action: standard },
        { id: 'P', title: 'Planned', status: 'planned', owner: null, next_action: standard },
        {
          id: 'C',
          title: 'Custom',
          status: 'done',
          owner: null,
          next_action: 'Archive the verified checkout tomorrow.',
        },
      ],
    });
    expect(snapshot.tasks.map(hasActionGlance)).toEqual([false, true, true]);
    expect(snapshot.tasks[0]?.next_action).toBe(standard);
    expect(workload(snapshot, 'all').find(({ owner }) => owner === '')).toMatchObject({
      total: 3,
      planned: 1,
      done: 2,
      active: 0,
    });
    render(<Workload snapshot={snapshot} context={defaultContext} />);
    expect(
      within(screen.getByRole('table'))
        .getByRole('link', { name: 'Unassigned · Planned: 1' })
        .getAttribute('href'),
    ).toContain('status=planned');
    await userEvent.click(screen.getByText('Completed records: 2'));
    expect(
      screen.getByRole('link', { name: 'No current owner · 2 complete' }).getAttribute('href'),
    ).toContain('status=done');
  });
  it('counts current ownership by explicit scope and preserves unknown and unassigned owners', () => {
    const snapshot = fixture();
    expect(workload(snapshot, 'current')).toMatchObject([
      { owner: 'worker', total: 1, active: 1, blocked: 0, review: 0 },
      { owner: 'idle', total: 0, active: 0, blocked: 0, review: 0 },
      { owner: '', total: 1, active: 0, blocked: 1, review: 0 },
    ]);
    expect(workload(snapshot, 'all')).toContainEqual({
      owner: 'external',
      total: 1,
      planned: 0,
      done: 0,
      active: 0,
      blocked: 0,
      review: 1,
    });
    for (const scope of ['current', 'all'] as const) {
      for (const row of workload(snapshot, scope)) {
        for (const status of ['planned', 'active', 'blocked', 'review', 'done', 'total'] as const) {
          const destination = parseNavigation(
            workloadHref(scope, row.owner, status === 'total' ? '' : status),
            snapshot,
          );
          expect(selectWorkTasks(snapshot, destination)).toHaveLength(row[status]);
          expect(destination.selected).toBeNull();
          expect(destination.paging.index).toBe(0);
        }
      }
    }
  });
  it('derives unique reverse dependencies only from depends_on and updates after owner/status changes', () => {
    const before = fixture();
    const b = before.tasks[1]!;
    expect(taskNeighborhood(before, b).downstream.map(({ id }) => id)).toEqual(['A', 'C']);
    expect(
      taskNeighborhood(before, before.tasks[0]!).upstream.map(({ id, task }) => [id, !!task]),
    ).toEqual([
      ['B', true],
      ['missing', false],
    ]);
    const after = parseSnapshot({
      ...before,
      revision: 'two',
      tasks: before.tasks.map((task) =>
        task.id === 'A' ? { ...task, owner: 'external', status: 'review', depends_on: [] } : task,
      ),
    });
    expect(taskNeighborhood(after, after.tasks[1]!).downstream.map(({ id }) => id)).toEqual(['C']);
    expect(workload(after, 'all').find(({ owner }) => owner === 'external')).toMatchObject({
      total: 2,
      review: 2,
    });
  });
  it('keeps absent goal membership unknown and does not populate current scope with all tasks', () => {
    const snapshot = parseSnapshot({ ...fixture(), objective: {} });
    render(<Workload snapshot={snapshot} context={{ ...defaultContext, scope: 'current' }} />);
    expect(screen.getByText(/Current goal membership is not recorded/)).toBeDefined();
    expect(screen.queryByRole('table')).toBeNull();
    expect(screen.queryByText(/Personnel without open assignments/)).toBeNull();
  });
  it('puts assigned and unassigned work ahead of zero-assignment personnel without implying availability', async () => {
    render(<Workload snapshot={fixture()} context={defaultContext} />);
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(4);
    expect(within(table).queryByRole('link', { name: 'idle' })).toBeNull();
    await userEvent.click(screen.getByText('Personnel without open assignments: 1'));
    expect(screen.getByRole('link', { name: 'idle' }).getAttribute('href')).toContain('owner=idle');
    expect(screen.queryByText(/\b(?:available|live|capacity)\b/i)).toBeNull();
  });
  it('provides keyboard links to exact downstream work and owner workload without runtime contact claims', async () => {
    const snapshot = fixture();
    render(<DependencyNeighborhood snapshot={snapshot} task={snapshot.tasks[1]!} />);
    const downstream = screen.getByText('Required by').closest('details')!;
    expect(within(downstream).getAllByRole('link')).toHaveLength(4);
    const task = within(downstream).getByRole('link', { name: 'A Alpha' });
    task.focus();
    expect(document.activeElement).toBe(task);
    await userEvent.tab();
    expect(document.activeElement).toBe(
      within(downstream).getByRole('link', { name: 'Assignments for worker' }),
    );
    expect(task.getAttribute('href')).toBe('#task/A');
    expect(screen.getByText('In review')).toBeDefined();
    expect(screen.queryByText(/contact:/)).toBeNull();
  });
  it('offers locally named icon controls without changing link destinations or guarded disabled semantics', async () => {
    render(
      <>
        <IconButton icon="previous" label="Previous page" aria-disabled="true" />
        <IconLink icon="back" label="Back to work" href="#work?scope=current" />
      </>,
    );
    const previous = screen.getByRole('button', { name: 'Previous page' });
    expect(previous.getAttribute('aria-disabled')).toBe('true');
    expect(previous.getAttribute('data-tooltip')).toBe('Previous page');
    expect(previous.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
    await userEvent.tab();
    expect(document.activeElement).toBe(previous);
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByRole('link', { name: 'Back to work' }));
    expect(document.activeElement?.getAttribute('href')).toBe('#work?scope=current');
  });
});
