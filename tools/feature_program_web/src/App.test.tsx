import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { parseSnapshot, safeUrl } from './model';
import type { Snapshot } from './model';
import { PREFERENCE_KEY } from './i18n';
import { SnapshotResponse } from './useSnapshot';

const fixture = (): Snapshot =>
  parseSnapshot({
    snapshot_version: 1,
    revision: 'one',
    program_id: 'Synthetic Atlas',
    integrity: { status: 'fresh', warnings: [] },
    categories: [
      { id: 'platform', title: 'Platform', parent_id: null, task_ids: [] },
      { id: 'parser', title: 'Parser', parent_id: 'platform', task_ids: ['A1', 'A2'] },
    ],
    actors: [
      {
        id: 'Ada',
        role: 'director',
        parent_id: null,
        model_id: null,
        reasoning_effort: null,
        provenance: 'unknown',
        observed_status: 'unknown',
        observed_at: null,
        history: [],
      },
    ],
    tasks: [
      {
        id: 'A1',
        title: 'Preserve parser context',
        status: 'active',
        workstream: 'platform',
        category_id: 'parser',
        owner: 'Ada',
        source: { text: '<img src=x onerror=alert(1)>' },
        required_checks: ['Inspect source'],
        attempts: [],
        delivery: { status: 'unrecorded' },
      },
      {
        id: 'A2',
        title: 'Review evidence',
        status: 'planned',
        workstream: 'platform',
        category_id: 'parser',
        owner: null,
        source: { text: 'Acceptance prose' },
        attempts: [],
        delivery: { status: 'unrecorded' },
      },
    ],
    handoffs: [
      {
        id: 'swap',
        role: 'worker',
        from_actor: 'Old',
        to_actor: 'Next',
        task_id: 'A1',
        outgoing: { goal: 'Inspect source', next_action: 'Revalidate first' },
        acknowledgement: null,
      },
    ],
    objective: {
      title: 'Ship a workflow',
      criteria: ['Keep data local'],
      status: 'active',
      decision_owner: 'director',
      next_action_owner: 'director',
    },
  });
async function navigate(hash: string) {
  await act(async () => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
}

describe('work program user outcomes', () => {
  it('preserves the question draft, focus and disclosure through refreshing304 and failed reads', async () => {
    // Given
    const user = userEvent.setup();
    const snapshot = {
      ...fixture(),
      questions: [
        {
          id: 'Q',
          prompt: 'Choose scope',
          why: 'Keep context',
          requested_decision: 'Choose',
          version: 1,
          status: 'open',
        },
      ],
    };
    const loader = vi.fn().mockResolvedValue(new SnapshotResponse(200, 'W/"one"', snapshot));
    render(
      <App
        loader={loader}
        sessionLoader={async () => ({ responsesEnabled: true, nonce: 'synthetic' })}
      />,
    );
    await screen.findByText('Where the work stands');
    await navigate('#question/Q');
    const input = screen.getByLabelText('Your answer');
    await user.type(input, 'Keep the draft');
    const disclosure = screen.getByText('Question scope and response history').closest('details');
    if (disclosure) disclosure.open = true;
    let resolve: ((value: unknown) => void) | undefined;
    loader.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    // When
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await screen.findByText('Refreshing…');
    await act(async () => {
      resolve?.(new SnapshotResponse(304, 'W/"one"'));
    });
    // Then
    expect(screen.getByDisplayValue('Keep the draft')).toBe(input);
    expect(document.activeElement).toBe(input);
    expect(disclosure?.open).toBe(true);
    loader.mockRejectedValue(new Error('outage'));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await screen.findByText(/Showing last observed records/);
    expect(screen.getByDisplayValue('Keep the draft')).toBe(input);
    expect(document.activeElement).toBe(input);
    expect(disclosure?.open).toBe(true);
  });
  it('locates tasks by category and status, preserves filters and selection when returning from a task page', async () => {
    const user = userEvent.setup();
    render(<App loader={async () => fixture()} />);
    await screen.findByText('Where the work stands');
    await navigate('#work');
    await user.click(screen.getByRole('button', { name: 'Parser, 0 of 2 reviewed' }));
    await user.selectOptions(screen.getByLabelText('Status'), 'active');
    expect(screen.getByText('1 of 2 tasks')).toBeDefined();
    await user.click(screen.getByRole('link', { name: /A1 Preserve parser context/ }));
    await navigate('#task/A1');
    expect(screen.getByRole('heading', { name: 'Preserve parser context' })).toBeDefined();
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeDefined();
    expect(document.querySelector('img')).toBeNull();
    await user.click(screen.getByRole('link', { name: 'Back to work' }));
    await navigate('#work');
    expect(screen.getByLabelText('Status').getAttribute('value')).toBeNull();
    expect(screen.getByText('1 of 2 tasks')).toBeDefined();
    expect(document.activeElement?.textContent).toContain('Preserve parser context');
  });
  it('keeps input focus and selected detail through changed snapshots and unavailable reads', async () => {
    const user = userEvent.setup();
    const loader = vi.fn().mockResolvedValue(fixture());
    render(<App loader={loader} />);
    await screen.findByText('Where the work stands');
    await navigate('#work');
    await user.type(screen.getByLabelText('Search tasks'), 'parser');
    const search = screen.getByLabelText('Search tasks');
    loader.mockResolvedValue({
      ...fixture(),
      revision: 'two',
      integrity: { status: 'stale', warnings: ['Frozen source drifted'] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await screen.findByText(/Snapshot integrity is stale/);
    const warningDisclosure = screen.getByText('Integrity warnings: 1').closest('details');
    expect(warningDisclosure?.hasAttribute('open')).toBe(false);
    expect(document.activeElement).toBe(search);
    expect(screen.getByText('1 of 2 tasks')).toBeDefined();
    await navigate('#task/A1');
    loader.mockRejectedValue(new Error('Network unavailable'));
    await user.click(screen.getByRole('button', { name: 'Refresh' }));
    await screen.findByText(/Showing last observed records/);
    expect(screen.getByRole('heading', { name: 'Preserve parser context' })).toBeDefined();
  });
  it('shows pending handoff without activating its successor and unknown model metadata', async () => {
    render(<App loader={async () => fixture()} />);
    await screen.findByText('Where the work stands');
    await navigate('#task/A1');
    expect(screen.getByText('Pending acknowledgement')).toBeDefined();
    expect(
      screen.getByText('A pending handoff does not assign or activate the successor.'),
    ).toBeDefined();
    const assignment = screen.getByRole('heading', { name: 'Recorded assignment' }).parentElement;
    expect(assignment).not.toBeNull();
    if (assignment) expect(within(assignment).getAllByText('Unknown').length).toBeGreaterThan(1);
  });
  it('shows the user as next action owner after goal completion without selecting a new goal', async () => {
    render(
      <App
        loader={async () => ({
          ...fixture(),
          objective: {
            title: 'Ship a workflow',
            status: 'awaiting_next_goal',
            decision_owner: 'director',
            next_action_owner: 'user',
            criteria: ['Keep data local'],
          },
        })}
      />,
    );
    await screen.findByText(
      'Awaiting your next objective. The completed goal does not authorize another one.',
    );
    expect(screen.getByText('User supplies the next objective.')).toBeDefined();
    expect(screen.getByText('user')).toBeDefined();
  });
  it('recovers after an unavailable initial snapshot and labels empty filtered results', async () => {
    const user = userEvent.setup();
    const loader = vi.fn().mockRejectedValue(new Error('Unavailable'));
    render(<App loader={loader} />);
    await screen.findByText('Program unavailable');
    loader.mockResolvedValue(fixture());
    await user.click(screen.getByRole('button', { name: 'Refresh' }));
    await screen.findByText('Where the work stands');
    await navigate('#work');
    await user.type(screen.getByLabelText('Search tasks'), 'missing-task');
    await screen.findByText('No tasks match these filters. Clear filters to see all work.');
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    await waitFor(() => expect(screen.getByText('2 of 2 tasks')).toBeDefined());
  });
  it('allows provider HTTPS links and rejects executable, credential-bearing and private destinations', () => {
    expect(safeUrl('https://github.com/example/repo/pull/1')).toBe(
      'https://github.com/example/repo/pull/1',
    );
    for (const url of [
      'javascript:alert(1)',
      'http://github.com/example',
      'https://user:secret@github.com/example',
      'https://127.0.0.1/evidence',
      'https://github.com.attacker.example/path',
    ])
      expect(safeUrl(url)).toBeNull();
  });
  it('shows frozen assignment settings even after the actor is reused with different settings', async () => {
    const original = fixture();
    const changed = {
      ...original,
      actors: [
        {
          id: 'Ada',
          role: 'director',
          model_id: 'model-v2',
          reasoning_effort: 'medium',
          provenance: 'verified_launch',
        },
      ],
      tasks: original.tasks.map((task) =>
        task.id === 'A1'
          ? {
              ...task,
              attempts: [
                {
                  number: 1,
                  owner: 'Ada',
                  assignment: {
                    actor_id: 'Ada',
                    role: 'worker',
                    model_id: 'model-v1',
                    reasoning_effort: 'high',
                    provenance: 'verified_launch',
                  },
                },
              ],
            }
          : task,
      ),
    };
    render(<App loader={async () => changed} />);
    await screen.findByText('Where the work stands');
    await navigate('#task/A1');
    const panel = screen.getByRole('heading', { name: 'Recorded assignment' }).parentElement;
    if (!panel) throw new Error('Assignment panel missing');
    expect(within(panel).getByText('model-v1')).toBeDefined();
    expect(within(panel).getByText('high')).toBeDefined();
    expect(within(panel).queryByText('model-v2')).toBeNull();
    await navigate('#team');
    expect(screen.getByText('model-v2')).toBeDefined();
  });
  it('links dependency and advisory peers with their current recorded contact and honest unknowns', async () => {
    const original = fixture();
    render(
      <App
        loader={async () => ({
          ...original,
          tasks: original.tasks.map((task) =>
            task.id === 'A1'
              ? { ...task, depends_on: ['A2'], related_to: ['A2', 'missing'] }
              : { ...task, owner: 'peer_worker' },
          ),
        })}
      />,
    );
    await screen.findByText('Where the work stands');
    await navigate('#task/A1');
    expect(screen.getByText('Hard dependencies')).toBeDefined();
    expect(screen.getByText('Related tasks · advisory')).toBeDefined();
    expect(screen.getAllByText('Current recorded owner / contact: peer_worker')).toHaveLength(2);
    expect(screen.getByText('No owner or contact recorded')).toBeDefined();
    expect(screen.getByText('missing · Task unavailable')).toBeDefined();
    const peerLinks = screen.getAllByRole('link', { name: 'A2 · Review evidence' });
    expect(peerLinks[0]?.getAttribute('href')).toBe('#task/A2');
    await userEvent.setup().click(peerLinks[0]!);
    await navigate('#task/A2');
    expect(screen.getByRole('heading', { name: 'Review evidence' })).toBeDefined();
  });
  it.each(['en', 'ko'] as const)(
    'shows readable task notes and preserves malformed legacy records in %s',
    async (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const original = fixture();
      render(
        <App
          loader={async () => ({
            ...original,
            tasks: original.tasks.map((task) =>
              task.id === 'A1'
                ? {
                    ...task,
                    notes: [
                      {
                        actor: 'peer_worker$&',
                        timestamp: '2026-10-13T00:00:00Z',
                        reason: 'Unknown',
                        next_action: 'Accepted',
                        extra: 'Keep additional fields',
                      },
                      'Keep this legacy note unchanged.',
                      { legacy_payload: { reason: 'Preserve the nested record' } },
                      { actor: ['malformed'], reason: 17, next_action: false, timestamp: null },
                    ],
                  }
                : task,
            ),
          })}
        />,
      );
      await screen.findByRole('heading', {
        name: locale === 'ko' ? '현재 작업 상태' : 'Where the work stands',
      });
      await navigate('#task/A1');
      const notes = screen.getByRole('list', {
        name: locale === 'ko' ? '작업 메모' : 'Task notes',
      });
      expect(within(notes).getByText(/peer_worker\$&/, { selector: 'p' })).toBeDefined();
      expect(notes.textContent).toContain('2026');
      expect(within(notes).getByText('Unknown', { exact: true })).toBeDefined();
      expect(within(notes).getByText('Accepted', { exact: true })).toBeDefined();
      expect(within(notes).getByText(locale === 'ko' ? '다음 할 일' : 'Next action')).toBeDefined();
      expect(within(notes).getByText('Keep this legacy note unchanged.')).toBeDefined();
      expect(
        within(notes).getAllByText(
          locale === 'ko'
            ? '인식할 수 없는 메모 형식입니다. 원래 기록을 확인하세요.'
            : 'Unrecognized note format. Inspect the original record.',
        ),
      ).toHaveLength(2);
      const originalRecords = notes.querySelectorAll('details');
      expect(originalRecords).toHaveLength(3);
      expect([...originalRecords].every((detail) => !detail.hasAttribute('open'))).toBe(true);
      expect(originalRecords[0]?.textContent).toContain('Keep additional fields');
      expect(originalRecords[1]?.textContent).toContain('Preserve the nested record');
      expect(originalRecords[2]?.textContent).toContain('"reason": 17');
    },
  );
});
