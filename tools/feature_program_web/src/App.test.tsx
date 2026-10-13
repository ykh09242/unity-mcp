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
  it('keeps a pending manual refresh in keyboard traversal without allowing another request or stealing moved focus', async () => {
    localStorage.setItem(PREFERENCE_KEY, 'en');
    let release: ((response: SnapshotResponse) => void) | undefined;
    const loader = vi
      .fn()
      .mockResolvedValueOnce(new SnapshotResponse(200, 'one', fixture()))
      .mockImplementation(
        () =>
          new Promise<SnapshotResponse>((resolve) => {
            release = resolve;
          }),
      );
    const user = userEvent.setup();
    render(<App loader={loader} />);
    await screen.findByRole('heading', { name: 'Overview' });
    const refresh = screen.getByRole('button', { name: 'Refresh' });
    const language = within(screen.getByRole('banner')).getByRole('combobox');
    await waitFor(() =>
      expect(within(screen.getByRole('banner')).getByRole('status').textContent).toMatch(
        /^Fetched /,
      ),
    );
    refresh.focus();
    expect(document.activeElement).toBe(refresh);
    await user.keyboard('{Enter}');
    expect(loader).toHaveBeenCalledTimes(2);
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(language);
    await user.tab();
    expect(document.activeElement).toBe(refresh);
    await user.keyboard('{Enter}');
    expect(loader).toHaveBeenCalledTimes(2);
    language.focus();
    await act(async () => {
      release?.(new SnapshotResponse(304, 'one'));
    });
    expect(document.activeElement).toBe(language);
  });
  it('leads Overview with its goal and progress while related decision narratives are secondary', async () => {
    const base = fixture();
    render(
      <App
        loader={async () => ({
          ...base,
          objective: { ...(base.objective as object), id: 'goal', task_ids: ['A1', 'A2'] },
          questions: [
            {
              id: 'Q1',
              version: 1,
              status: 'open',
              objective_id: 'goal',
              prompt: 'Long current question narrative',
            },
            {
              id: 'Q2',
              version: 1,
              status: 'resolved',
              objective_id: 'goal',
              prompt: 'Past decision narrative',
            },
          ],
        })}
      />,
    );
    const heading = await screen.findByRole('heading', { name: 'Overview' });
    const main = screen.getByRole('main');
    expect(within(main).getAllByRole('heading')[0]).toBe(heading);
    expect(screen.queryByRole('link', { name: /Long current question narrative/ })).toBeNull();
    const disclosure = screen.getByText('Related decisions').closest('details');
    expect(disclosure?.open).toBe(false);
    await userEvent.click(screen.getByText('Related decisions'));
    expect(
      screen.getByRole('link', { name: /Long current question narrative/ }).getAttribute('href'),
    ).toContain('#question/Q1');
    expect(main.querySelector('progress')!.compareDocumentPosition(disclosure!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });
  it('keeps current objective progress separate from retained historical task records', async () => {
    const base = fixture();
    render(
      <App
        loader={async () => ({
          ...base,
          objective: { id: 'current', title: 'Current objective', task_ids: ['A1'] },
          tasks: base.tasks.map((task) =>
            task.id === 'A1'
              ? { ...task, status: 'done' }
              : { ...task, status: 'planned', membership: { disposition: 'retired' } },
          ),
        })}
      />,
    );
    const progress = await screen.findByRole('progressbar', {
      name: '1 of 1 tasks reviewed complete',
    });
    expect(progress.getAttribute('max')).toBe('1');
    expect(screen.getByRole('link', { name: 'All work (2)' }).getAttribute('href')).toBe(
      '#work?scope=all',
    );
  });
  it('announces an explicit refresh but keeps unchanged background polls quiet', async () => {
    localStorage.setItem(PREFERENCE_KEY, 'en');
    const user = userEvent.setup();
    const loader = vi.fn(async () => fixture());
    render(<App loader={loader} />);
    const status = within(screen.getByRole('banner')).getByRole('status');
    await waitFor(() => expect(status.textContent).toMatch(/^Fetched /));
    expect(status.getAttribute('aria-live')).toBe('polite');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(status.getAttribute('aria-live')).toBe('off');
    await user.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(status.textContent).toMatch(/^Fetched /));
    expect(status.getAttribute('aria-live')).toBe('polite');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(status.getAttribute('aria-live')).toBe('off');
  });
  it.each([
    ['en', 'Refresh', 'Loading program…', 'Refreshing…', 'Unavailable'],
    ['ko', '새로고침', '프로그램 불러오는 중…', '갱신 중…', '사용 불가'],
  ])(
    'keeps header controls and localized status available across %s read states',
    async (locale, refreshLabel, loading, refreshing, unavailable) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      let release: ((response: SnapshotResponse) => void) | undefined;
      const loader = vi.fn(
        () =>
          new Promise<SnapshotResponse>((resolve) => {
            release = resolve;
          }),
      );
      render(<App loader={loader} />);
      const header = within(screen.getByRole('banner'));
      const language = header.getByRole('combobox') as HTMLSelectElement;
      const refresh = header.getByRole('button', { name: refreshLabel }) as HTMLButtonElement;
      const status = header.getByRole('status');
      expect(status.textContent).toBe(loading);
      expect(refresh.disabled).toBe(true);
      expect(language.value).toBe(locale);
      await act(async () => {
        release?.(new SnapshotResponse(200, 'one', fixture()));
      });
      const overview = screen.getByRole('heading', { name: locale === 'ko' ? '개요' : 'Overview' });
      expect(overview.closest('.page-heading')?.querySelectorAll('h2').length).toBe(1);
      expect(overview.closest('.page-heading')?.querySelector('.eyebrow')).toBeNull();
      expect(overview.closest('main')?.querySelector('.intro')).toBeNull();
      expect(overview.closest('.page-heading')?.querySelector('a')?.getAttribute('href')).toBe(
        '#work',
      );
      expect(refresh.disabled).toBe(false);
      const fetched = status.textContent;
      fireEvent.click(refresh);
      await waitFor(() => expect(status.textContent).toBe(refreshing));
      expect(refresh.disabled).toBe(false);
      expect(refresh.getAttribute('aria-disabled')).toBe('true');
      expect(header.getByRole('combobox')).toBe(language);
      expect(language.value).toBe(locale);
      await act(async () => {
        release?.(new SnapshotResponse(304, 'one'));
      });
      expect(refresh.disabled).toBe(false);
      expect(status.textContent).not.toBe(refreshing);
      expect(status.textContent?.split(' ')[0]).toBe(fetched?.split(' ')[0]);
      loader.mockRejectedValueOnce(new Error('Read unavailable'));
      fireEvent.click(refresh);
      await waitFor(() => expect(status.textContent).toBe(unavailable));
      expect(header.getByRole('button', { name: refreshLabel })).toBe(refresh);
      expect(header.getByRole('combobox')).toBe(language);
      expect(refresh.disabled).toBe(false);
      expect(language.value).toBe(locale);
    },
  );
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
    await screen.findByRole('heading', { name: 'Overview' });
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
    await screen.findByRole('heading', { name: 'Overview' });
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
    await screen.findByRole('heading', { name: 'Overview' });
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
    await screen.findByRole('heading', { name: 'Overview' });
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
    await screen.findByRole('heading', { name: 'Overview' });
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
    await screen.findByRole('heading', { name: 'Overview' });
    await navigate('#task/A1');
    const panel = screen.getByRole('heading', { name: 'Recorded assignment' }).parentElement;
    if (!panel) throw new Error('Assignment panel missing');
    expect(within(panel).getAllByText('model-v1').length).toBeGreaterThan(0);
    expect(within(panel).getAllByText('high').length).toBeGreaterThan(0);
    const modelComparison = panel.querySelector<HTMLElement>('.history-comparison section');
    if (!modelComparison) throw new Error('Settings comparison missing');
    expect(within(modelComparison).getByText('At claim').parentElement?.textContent).toContain(
      'model-v1',
    );
    expect(
      within(modelComparison).getByText('Current settings').parentElement?.textContent,
    ).toContain('model-v2');
    await navigate('#team');
    expect(screen.getAllByText('model-v2').length).toBeGreaterThan(0);
  });
  it.each([
    ['done', 'done', 'recorded-worker'],
    ['planned', 'released', 'recorded-worker'],
    ['done', 'done', null],
  ] as const)(
    'retains historical staffing for owner-null %s/%s with assignment identity %s',
    async (status, attemptStatus, assignedId) => {
      const original = fixture();
      const snapshot = parseSnapshot({
        ...original,
        actors: [
          {
            id: 'recorded-worker',
            role: 'worker',
            model_id: 'current-saved-model',
            reasoning_effort: 'high',
            provenance: 'verified_launch',
            history: [
              {
                decision: 'reuse',
                reason: 'Recorded staffing rationale',
                metadata: { model_id: 'claim-model' },
              },
            ],
          },
        ],
        tasks: original.tasks.map((task) =>
          task.id === 'A1'
            ? {
                ...task,
                owner: null,
                status,
                attempts: [
                  {
                    number: 1,
                    owner: 'recorded-worker',
                    status: attemptStatus,
                    assignment: {
                      actor_id: assignedId,
                      model_id: 'claim-model',
                      reasoning_effort: 'medium',
                      provenance: 'inherited',
                      role: 'worker',
                    },
                  },
                ],
              }
            : task,
        ),
      });
      render(<App loader={async () => snapshot} />);
      await screen.findByRole('heading', { name: 'Overview' });
      await navigate('#task/A1');
      const panel = screen.getByRole('heading', { name: 'Recorded assignment' }).parentElement;
      if (!panel) throw new Error('Assignment panel missing');
      const comparison = panel.querySelector<HTMLElement>('.history-comparison section');
      if (!comparison) throw new Error('Settings comparison missing');
      expect(within(comparison).getByText('Current settings').parentElement?.textContent).toContain(
        'current-saved-model',
      );
      expect(within(comparison).getByText('At claim').parentElement?.textContent).toContain(
        'claim-model',
      );
      expect(within(panel).getByText('Recorded staffing rationale')).toBeDefined();
      expect(within(panel).getByText('Assigned actor').nextElementSibling?.textContent).toBe(
        'recorded-worker',
      );
      expect(within(panel).getByText('Current owner').nextElementSibling?.textContent).toBe(
        'Unassigned',
      );
    },
  );
  it('keeps a missing historical actor unknown instead of borrowing the current owner metadata', async () => {
    const original = fixture();
    const snapshot = parseSnapshot({
      ...original,
      actors: [
        {
          id: 'Ada',
          role: 'worker',
          model_id: 'unrelated-current-model',
          history: [{ decision: 'new', reason: 'Unrelated history' }],
        },
      ],
      tasks: original.tasks.map((task) =>
        task.id === 'A1'
          ? {
              ...task,
              owner: 'Ada',
              attempts: [
                {
                  number: 1,
                  owner: 'missing-historical-worker',
                  status: 'released',
                  assignment: {
                    actor_id: 'missing-historical-worker',
                    model_id: 'frozen-missing-model',
                    role: 'worker',
                  },
                },
              ],
            }
          : task,
      ),
    });
    render(<App loader={async () => snapshot} />);
    await screen.findByRole('heading', { name: 'Overview' });
    await navigate('#task/A1');
    const panel = screen.getByRole('heading', { name: 'Recorded assignment' }).parentElement;
    if (!panel) throw new Error('Assignment panel missing');
    const comparison = panel.querySelector<HTMLElement>('.history-comparison section');
    if (!comparison) throw new Error('Settings comparison missing');
    expect(within(comparison).getByText('Current settings').parentElement?.textContent).toContain(
      'Unknown',
    );
    expect(within(panel).queryByText('unrelated-current-model')).toBeNull();
    expect(within(panel).queryByText('Unrelated history')).toBeNull();
    expect(within(panel).getByText('No personnel record')).toBeDefined();
    expect(within(panel).getByText('Assigned actor').nextElementSibling?.textContent).toBe(
      'missing-historical-worker',
    );
    expect(within(panel).getByText('Current owner').nextElementSibling?.textContent).toBe('Ada');
  });
  it('links dependency and advisory peers with current ownership and honest unknowns', async () => {
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
    await screen.findByRole('heading', { name: 'Overview' });
    await navigate('#task/A1');
    expect(screen.getByText('Depends on')).toBeDefined();
    expect(screen.getByText('Related tasks · advisory')).toBeDefined();
    await userEvent.click(screen.getByText('Related tasks · advisory'));
    expect(screen.getAllByRole('link', { name: 'Assignments for peer_worker' })).toHaveLength(2);
    expect(screen.getByText('missing · Task unavailable')).toBeDefined();
    const peerLinks = screen.getAllByRole('link', { name: 'A2 Review evidence' });
    expect(peerLinks[0]?.getAttribute('href')).toBe('#task/A2');
    await userEvent.setup().click(peerLinks[0]!);
    await navigate('#task/A2');
    expect(screen.getByRole('heading', { name: 'Review evidence' })).toBeDefined();
  });
  it('keeps bespoke completed next actions in the ledger and exact generic completion text in detail', async () => {
    const standard = 'Reviewed complete; delivery/CI is tracked separately.';
    const custom = 'Archive the verified checkout tomorrow.';
    render(
      <App
        loader={async () => ({
          ...fixture(),
          tasks: fixture().tasks.map((task, index) => ({
            ...task,
            status: 'done',
            owner: null,
            next_action: index ? custom : standard,
          })),
        })}
      />,
    );
    await screen.findByRole('heading', { name: 'Overview' });
    await navigate('#work');
    const ledger = document.querySelector('.task-list')! as HTMLElement;
    expect(within(ledger).queryByText(standard)).toBeNull();
    expect(within(ledger).getByText(custom)).toBeDefined();
    expect(within(ledger).getAllByLabelText('No current owner')).toHaveLength(2);
    await navigate('#task/A1');
    expect(screen.getByText(standard)).toBeDefined();
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
        name: locale === 'ko' ? '개요' : 'Overview',
      });
      await navigate('#task/A1');
      await userEvent.click(
        screen.getByText(locale === 'ko' ? '협업 메모' : 'Authored coordination notes'),
      );
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
      expect(originalRecords).toHaveLength(4);
      expect([...originalRecords].every((detail) => !detail.hasAttribute('open'))).toBe(true);
      for (const detail of originalRecords) await userEvent.click(detail.querySelector('summary')!);
      expect(notes.textContent).toContain('Keep additional fields');
      expect(notes.textContent).toContain('Preserve the nested record');
      expect(notes.textContent).toContain('"reason": 17');
    },
  );
});
