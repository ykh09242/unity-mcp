import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { PREFERENCE_KEY } from './i18n';
import { parseSnapshot } from './model';
import { SnapshotResponse } from './useSnapshot';

function fixture() {
  return parseSnapshot({
    snapshot_version: 1,
    revision: 'one',
    program_id: 'context-continuity',
    integrity: { status: 'fresh', warnings: [] },
    tasks: [
      {
        id: 'A',
        title: 'Complete context',
        status: 'active',
        expected_result: 'Exact purpose.',
        acceptance_criteria: ['Retain unique acceptance.'],
        source: { text: '### A. Complete context\nExact purpose.\n\nUnique method.' },
      },
      {
        id: 'B',
        title: 'Other record',
        status: 'planned',
        expected_result: 'Different purpose.',
        source: { text: '### B. Other record\nDifferent purpose.' },
      },
    ],
  });
}
function start() {
  localStorage.setItem(PREFERENCE_KEY, 'en');
  window.history.replaceState(null, '', '#task/A?full=1');
}
async function context() {
  await screen.findByRole('heading', { name: 'Complete context' });
  const summary = screen.getByText('Acceptance criteria', { selector: 'summary' });
  const disclosure = summary.closest('details');
  const description = document.querySelector<HTMLElement>('.task-description-content');
  if (!disclosure || !description) throw new Error('Task context missing');
  return { summary, disclosure, description };
}

describe('single task description and refresh continuity', () => {
  it('shows complete description once and keeps distinct acceptance initially collapsed', async () => {
    start();
    render(<App loader={async () => fixture()} />);
    const { summary, disclosure, description } = await context();
    expect(disclosure.open).toBe(false);
    expect(screen.getAllByText('Acceptance criteria')).toHaveLength(1);
    expect(
      [...description.querySelectorAll('.task-description p')].map((node) => node.textContent),
    ).toEqual(['Exact purpose.', 'Unique method.']);
    expect(screen.getAllByText('Exact purpose.')).toHaveLength(1);
    expect(screen.queryByText('Full task description')).toBeNull();
    expect(document.querySelector('.task-source-context')).toBeNull();
    await userEvent.click(summary);
    expect(disclosure.open).toBe(true);
    expect(disclosure.textContent).toContain('Retain unique acceptance.');
    expect(disclosure.querySelector('details')).toBeNull();
    expect(description.hidden).toBe(false);
    expect(screen.getAllByText('Exact purpose.')).toHaveLength(1);
  });
  it('retains a distinct expected result and combines exact duplicated criteria only once', async () => {
    start();
    const original = fixture();
    const body =
      'Exact purpose.\n\nUnique method.\n\nAcceptance criteria:\n\n1. Keep wording.\n2. Keep behavior.';
    const snapshot = {
      ...original,
      tasks: original.tasks.map((task) =>
        task.id === 'A'
          ? {
              ...task,
              expected_result: 'Distinct delivered result.',
              acceptance_criteria: ['Keep wording.', 'Keep behavior.'],
              source: { text: '### A. Complete context\n' + body },
            }
          : task,
      ),
    };
    const before = JSON.stringify(snapshot);
    render(<App loader={async () => snapshot} />);
    const { disclosure } = await context();
    expect(screen.getAllByText('Distinct delivered result.')).toHaveLength(1);
    expect(screen.getAllByText('Keep wording.')).toHaveLength(1);
    expect(screen.getAllByText('Keep behavior.')).toHaveLength(1);
    expect(disclosure.querySelectorAll('ol li')).toHaveLength(2);
    expect(disclosure.open).toBe(false);
    expect(JSON.stringify(snapshot)).toBe(before);
  });
  it.each(['unchanged', 'changed'] as const)(
    'retains criteria disclosure, single description, focus and scroll through a %s same-task refresh',
    async (change) => {
      start();
      const snapshot = fixture();
      const loader = vi.fn().mockResolvedValue(new SnapshotResponse(200, 'one', snapshot));
      render(<App loader={loader} />);
      const { summary, disclosure, description } = await context();
      await userEvent.click(summary);
      const main = screen.getByRole('main');
      main.scrollTop = 180;
      summary.focus();
      const hash = window.location.hash;
      loader.mockResolvedValue(
        change === 'unchanged'
          ? new SnapshotResponse(304, 'one')
          : new SnapshotResponse(200, 'two', { ...snapshot, revision: 'two' }),
      );
      fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
      await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
      await act(async () => {});
      expect((await context()).disclosure).toBe(disclosure);
      expect(disclosure.open).toBe(true);
      expect(description.hidden).toBe(false);
      expect(screen.getAllByText('Exact purpose.')).toHaveLength(1);
      expect(window.location.hash).toBe(hash);
      expect(document.activeElement).toBe(summary);
      expect(main.scrollTop).toBe(180);
    },
  );
  it('does not carry the previous task criteria disclosure into another task', async () => {
    start();
    render(<App loader={async () => fixture()} />);
    const first = await context();
    await userEvent.click(first.summary);
    await act(async () => {
      window.location.hash = '#task/B?full=1';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    await screen.findByRole('heading', { name: 'Other record' });
    expect(document.querySelector('.task-acceptance-criteria')).toBeNull();
    expect(screen.getAllByText('Different purpose.')).toHaveLength(1);
    expect(document.querySelector<HTMLElement>('.task-description-content')?.hidden).toBe(false);
  });
});
