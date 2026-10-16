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
  it.each([
    ['en', 'Refresh', 'Unavailable', /^Fetched /],
    ['ko', '새로고침', '사용 불가', /^조회 /],
  ])(
    'keeps the %s refresh failure in the reserved header without losing work context',
    async (locale, refreshLabel, unavailable, fetched) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const user = userEvent.setup();
      const loader = vi.fn().mockResolvedValue(new SnapshotResponse(200, 'one', fixture()));
      const view = render(<App loader={loader} />);
      await waitFor(() =>
        expect(within(screen.getByRole('banner')).getByRole('status').textContent).toMatch(fetched),
      );
      await navigate('#work');
      const main = screen.getByRole('main');
      await waitFor(() => expect(document.activeElement).toBe(main));
      const header = screen.getByRole('banner');
      const status = within(header).getByRole('status');
      const language = within(header).getByRole('combobox');
      const refresh = within(header).getByRole('button', { name: refreshLabel });
      const shell = view.container.querySelector('.shell');
      const rail = view.container.querySelector('.rail');
      if (!rail || !shell) throw new Error('Workspace missing');
      main.scrollTop = 120;
      rail.scrollTop = 24;
      language.focus();
      const route = window.location.hash;
      loader.mockRejectedValueOnce(new Error('publication in progress'));
      fireEvent.click(refresh);
      await waitFor(() => expect(status.textContent).toBe(unavailable));
      const disclosure = status.closest('details');
      const summary = disclosure?.querySelector('summary');
      if (!disclosure || !summary) throw new Error('Refresh explanation missing');
      expect(disclosure.closest('header')).toBe(header);
      expect(header.nextElementSibling).toBe(shell);
      expect(screen.getByRole('main')).toBe(main);
      expect(disclosure.open).toBe(false);
      expect(status.getAttribute('aria-live')).toBe('polite');
      expect(summary.getAttribute('aria-describedby')).toBe('snapshot-unavailable-explanation');
      expect(document.getElementById('snapshot-unavailable-explanation')?.textContent).toMatch(
        locale === 'ko' ? /마지막/ : /last observed records/,
      );
      expect(document.activeElement).toBe(language);
      expect([main.scrollTop, rail.scrollTop, window.location.hash]).toEqual([120, 24, route]);
      summary.focus();
      await user.click(summary);
      expect(disclosure.open).toBe(true);
      await user.keyboard('{Escape}');
      expect(disclosure.open).toBe(false);
      expect(document.activeElement).toBe(summary);
      await user.click(summary);
      loader.mockResolvedValueOnce(new SnapshotResponse(304, 'one'));
      fireEvent.click(refresh);
      await waitFor(() => expect(status.textContent).toMatch(fetched));
      expect(status.closest('details')).toBe(disclosure);
      expect(disclosure.open).toBe(false);
      expect(document.activeElement).toBe(summary);
      expect(header.nextElementSibling).toBe(shell);
      expect([main.scrollTop, rail.scrollTop, window.location.hash]).toEqual([120, 24, route]);
      expect(document.getElementById('snapshot-unavailable-explanation')).toBeNull();
    },
  );
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
  it('places actionable attention directly after the goal before progress and separate category responsibility', async () => {
    const base = fixture();
    const reference = { path: 'backlog.json', revision: base.revision, pointer: '/objective' };
    const attention = {
      items: [
        {
          kind: 'blocked',
          entity_id: 'A1',
          reason: 'Attention ordering reason',
          next_action: 'Keep useful action',
          reference,
          observed_revision: base.revision,
          task_ids: ['A1'],
        },
      ],
      shown: 1,
      total: 1,
      omitted: 0,
      reference,
    };
    const view = render(
      <App
        loader={async () =>
          parseSnapshot({
            ...base,
            brief: {
              brief_version: 1,
              revision: base.revision,
              program_id: base.program_id,
              reference,
              attention,
            },
          })
        }
      />,
    );
    await screen.findByText('Attention ordering reason');
    const goal = view.container.querySelector('.objective');
    const panel = view.container.querySelector('.program-attention');
    const progress = view.container.querySelector('.metrics');
    if (!goal || !panel || !progress) throw new Error('Overview landmark missing');
    expect(goal.nextElementSibling).toBe(panel);
    expect(panel.compareDocumentPosition(progress)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(panel.closest('.overview-grid')).toBeNull();
    expect(screen.getByText('Keep useful action')).toBeTruthy();
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
    const disclosure = screen.getByText('About answers and drafts').closest('details');
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
    await userEvent.click(screen.getByRole('tab', { name: 'History' }));
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
    await userEvent.click(screen.getByRole('tab', { name: 'History' }));
    const panel = screen.getByRole('heading', { name: 'Recorded assignment' }).parentElement;
    if (!panel) throw new Error('Assignment panel missing');
    expect(within(panel).getAllByText('model-v1').length).toBeGreaterThan(0);
    expect(within(panel).getAllByText('high').length).toBeGreaterThan(0);
    expect(panel.querySelector('.history-comparison')).toBeNull();
    expect(within(panel).queryByText('model-v2')).toBeNull();
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
      await userEvent.click(screen.getByRole('tab', { name: 'History' }));
      const panel = screen.getByRole('heading', { name: 'Recorded assignment' }).parentElement;
      if (!panel) throw new Error('Assignment panel missing');
      expect(panel.querySelector('.history-comparison')).toBeNull();
      expect(
        within(panel).getAllByText('Model', { selector: 'dt' })[0]?.nextElementSibling?.textContent,
      ).toBe('claim-model');
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
    await userEvent.click(screen.getByRole('tab', { name: 'History' }));
    const panel = screen.getByRole('heading', { name: 'Recorded assignment' }).parentElement;
    if (!panel) throw new Error('Assignment panel missing');
    expect(panel.querySelector('.history-comparison')).toBeNull();
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
    await userEvent.click(screen.getByRole('tab', { name: 'Related work' }));
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
        screen.getByRole('tab', { name: locale === 'ko' ? '관련 작업' : 'Related work' }),
      );
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
          locale === 'ko' ? '이 메모를 표시할 수 없어요.' : 'This note cannot be displayed.',
        ),
      ).toHaveLength(2);
      expect(notes.querySelectorAll('details, pre')).toHaveLength(0);
      expect(notes.textContent).not.toContain('\"reason\": 17');
    },
  );
});

describe('opt-in actual receipt and fresh DOM diagnostics', () => {
  it.each(['success', 'failure'] as const)(
    'keeps a durable receipt separate from %s mandatory reconciliation',
    async (outcome) => {
      const meta = document.createElement('meta');
      meta.name = 'work-program-diagnostics';
      meta.content = '1';
      document.head.append(meta);
      const lines: string[] = [];
      vi.spyOn(console, 'info').mockImplementation((line) => lines.push(String(line)));
      const initial = {
        ...fixture(),
        questions: [
          {
            id: 'Q-observer',
            version: 1,
            status: 'open',
            prompt: 'Choose the actual scope',
            why: 'A focused diagnostic case',
            requested_decision: 'Choose',
            options: [],
            answer: null,
            acknowledgement: null,
            outcome: null,
          },
        ],
      };
      let finishGet: ((value: Response) => void) | undefined;
      let saved: Record<string, unknown> | undefined;
      let getCalls = 0,
        postCalls = 0;
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          if (url === '/api/snapshot') {
            getCalls += 1;
            if (getCalls === 1) return new Response(JSON.stringify(initial), { status: 200 });
            return new Promise<Response>((resolve) => {
              finishGet = resolve;
            });
          }
          if (url === '/api/questions/answer') {
            postCalls += 1;
            saved = {
              ...JSON.parse(String(init?.body)),
              saved_at: '2026-10-16T00:00:00Z',
              channel: 'local_web',
              provenance: 'unverified_local_human',
              status: 'answered',
            };
            return new Response(JSON.stringify(saved), {
              status: 200,
              headers: { 'X-Work-Program-Trace': 'a'.repeat(16) },
            });
          }
          throw new Error('Unexpected route');
        }),
      );
      window.location.hash = '#question/Q-observer';
      const view = render(
        <App sessionLoader={async () => ({ responsesEnabled: true, nonce: 'private-nonce' })} />,
      );
      const events = () =>
        lines.flatMap((line) => JSON.parse(line.slice('WORK_PROGRAM_TIMING_V1 '.length)).events);
      try {
        const user = userEvent.setup();
        const input = await screen.findByRole('textbox', { name: 'Your answer' });
        await user.type(input, 'private answer text');
        await user.click(screen.getByRole('button', { name: 'Save answer' }));
        await screen.findByRole('heading', { name: 'Your saved answer' });
        await waitFor(() =>
          expect(events().some((event) => event.phase === 'receipt_dom_committed')).toBe(true),
        );
        expect(
          events().some((event) => event.phase === 'post_response' && event.http_status === 200),
        ).toBe(true);
        expect(events().some((event) => event.phase === 'fresh_dom_committed')).toBe(false);
        expect(getCalls).toBe(2);
        expect(postCalls).toBe(1);
        await act(async () => {
          finishGet?.(
            outcome === 'failure'
              ? new Response('{}', { status: 503 })
              : new Response(
                  JSON.stringify({
                    ...initial,
                    revision: 'two',
                    questions: [{ ...initial.questions[0], status: 'answered', answer: saved }],
                  }),
                  { status: 200, headers: { 'X-Work-Program-Trace': 'b'.repeat(16) } },
                ),
          );
        });
        await waitFor(() =>
          expect(events().some((event) => event.phase === 'mandatory_get_settled')).toBe(true),
        );
        if (outcome === 'success')
          await waitFor(() =>
            expect(events().some((event) => event.phase === 'fresh_dom_committed')).toBe(true),
          );
        else {
          expect(events().some((event) => event.phase === 'fresh_dom_committed')).toBe(false);
          expect(screen.getByText('Unavailable')).toBeDefined();
          expect(screen.getByRole('heading', { name: 'Your saved answer' })).toBeDefined();
        }
        expect(postCalls).toBe(1);
        expect(lines.join()).not.toMatch(
          /private answer text|private-nonce|Q-observer|submission_id/u,
        );
        expect(events().map((event) => event.t_ms)).toEqual(
          [...events().map((event) => event.t_ms)].sort((a, b) => a - b),
        );
      } finally {
        view.unmount();
        meta.remove();
        vi.unstubAllGlobals();
      }
    },
  );
});
