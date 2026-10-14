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
  const summary = await screen.findByText('Full task context and evidence');
  const disclosure = summary.closest('details');
  const glance = document.querySelector<HTMLElement>('.expected-result-summary');
  if (!disclosure || !glance) throw new Error('Task context missing');
  return { summary, disclosure, glance };
}

describe('task context and refresh continuity', () => {
  it('reveals distinct acceptance and full task content when source context opens', async () => {
    // Given
    start();
    render(<App loader={async () => fixture()} />);
    const { summary, disclosure } = await context();
    // When
    await userEvent.click(summary);
    // Then
    const humanText = [...disclosure.querySelectorAll('.prose, .prose-list')]
      .map((element) => element.textContent)
      .join('\n');
    expect(humanText).toContain('Retain unique acceptance.');
    expect(humanText).toContain('Exact purpose.\n\nUnique method.');
    expect(disclosure.querySelector('details')?.open).toBe(false);
  });
  it('suppresses only the duplicated glance while full source context is expanded', async () => {
    // Given
    start();
    render(<App loader={async () => fixture()} />);
    const { summary, glance } = await context();
    expect(glance.hidden).toBe(false);
    // When
    await userEvent.click(summary);
    // Then
    await waitFor(() => expect(glance.hidden).toBe(true));
  });
  it.each(['unchanged', 'changed'] as const)(
    'retains expanded context and its scroll anchor through a %s same-task refresh',
    async (change) => {
      // Given
      start();
      const snapshot = fixture();
      const loader = vi.fn().mockResolvedValue(new SnapshotResponse(200, 'one', snapshot));
      render(<App loader={loader} />);
      const { summary, disclosure, glance } = await context();
      await userEvent.click(summary);
      await waitFor(() => expect(disclosure.open).toBe(true));
      const main = screen.getByRole('main');
      main.scrollTop = 180;
      summary.focus();
      const hash = window.location.hash;
      loader.mockResolvedValue(
        change === 'unchanged'
          ? new SnapshotResponse(304, 'one')
          : new SnapshotResponse(200, 'two', { ...snapshot, revision: 'two' }),
      );
      // When
      fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
      await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
      await act(async () => {});
      // Then
      expect((await context()).disclosure).toBe(disclosure);
      expect(disclosure.open).toBe(true);
      expect(glance.hidden).toBe(true);
      expect(window.location.hash).toBe(hash);
      expect(document.activeElement).toBe(summary);
      expect(main.scrollTop).toBe(180);
    },
  );
  it('resets source disclosure when the selected task changes', async () => {
    // Given
    start();
    render(<App loader={async () => fixture()} />);
    const first = await context();
    await userEvent.click(first.summary);
    await waitFor(() => expect(first.disclosure.open).toBe(true));
    // When
    await act(async () => {
      window.location.hash = '#task/B?full=1';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    // Then
    await screen.findByRole('heading', { name: 'Other record' });
    const second = await context();
    expect(second.disclosure.open).toBe(false);
    expect(second.glance.hidden).toBe(false);
  });
});
