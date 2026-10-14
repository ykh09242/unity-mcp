import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { App } from './App';
import { PREFERENCE_KEY } from './i18n';
import { parseSnapshot } from './model';
import { defaultContext, navigationHash, parseNavigation } from './navigation';

const originalWidth = window.innerWidth;
afterEach(() =>
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth }),
);
const fixture = () =>
  parseSnapshot({
    snapshot_version: 1,
    revision: 'sections-one',
    program_id: 'task-sections',
    tasks: [
      {
        id: 'T1',
        title: 'Primary task',
        status: 'review',
        owner: 'worker',
        next_action: 'Current next action',
        expected_result: 'Expected useful result',
        source: { text: '- Authored purpose with `inline code`\n- Authored method' },
        required_checks: ['Required test'],
        depends_on: ['T2'],
        related_to: ['missing'],
        notes: [{ actor: 'peer', reason: 'Coordination reason', next_action: 'Coordinate next' }],
        attempts: [
          {
            number: 1,
            owner: 'worker',
            status: 'review_complete',
            assignment: { actor_id: 'worker', model_id: 'recorded-model' },
            rounds: [
              {
                round: 1,
                receipt_record: { checks: [{ id: 'Recorded executed test', status: 'passed' }] },
                review_record: {
                  verdict: 'accepted',
                  reviewer: 'reviewer',
                  next_action: 'Current review action',
                },
              },
            ],
          },
        ],
        delivery: {
          status: 'ci_recorded_success',
          metadata: {
            ci: [
              {
                name: 'Provider test',
                conclusion: 'success',
                url: 'https://github.com/example/project/actions/runs/42',
              },
            ],
          },
        },
      },
      { id: 'T2', title: 'Upstream task', status: 'active' },
      { id: 'T3', title: 'Empty task', status: 'planned' },
    ],
    questions: [
      {
        id: 'Q1',
        status: 'open',
        version: 1,
        prompt: 'Related decision?',
        affected_task_ids: ['T1'],
      },
    ],
    actors: [{ id: 'worker', model_id: 'recorded-model', history: [] }],
  });
async function hash(value: string) {
  await act(async () => {
    window.history.pushState(null, '', value);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
}

describe('task sections without stacked context', () => {
  it.each(['en', 'ko'])(
    'defaults to core information and separates related work and records in %s',
    async (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      await hash('#task/T1?full=1');
      const user = userEvent.setup();
      render(<App loader={async () => fixture()} />);
      await screen.findByRole('heading', { name: 'Primary task' });
      const labels =
        locale === 'ko'
          ? ['작업 정보', '관련 작업', '이력']
          : ['Task information', 'Related work', 'History'];
      const information = screen.getByRole('tabpanel', { name: labels[0] });
      expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
      expect(within(information).getAllByText('Expected useful result').length).toBeGreaterThan(0);
      expect(within(information).getByText('Current review action')).toBeDefined();
      expect(information.querySelector('.readiness-context')).not.toBeNull();
      expect(
        within(information).getAllByText(locale === 'ko' ? '필수 확인' : 'Required checks').length,
      ).toBeGreaterThan(0);
      expect(screen.queryByRole('link', { name: /Upstream task/ })).toBeNull();
      expect(
        screen.queryByRole('heading', {
          name: locale === 'ko' ? '기록된 배정' : 'Recorded assignment',
        }),
      ).toBeNull();
      await user.click(screen.getByRole('tab', { name: labels[1] }));
      const related = screen.getByRole('tabpanel', { name: labels[1] });
      expect(within(related).getByRole('link', { name: /Upstream task/ })).toBeDefined();
      expect(within(related).getByText('Related decision?')).toBeDefined();
      expect(within(related).getByText('Coordination reason')).toBeDefined();
      expect(related.textContent).toContain('missing');
      expect(
        screen.queryByRole('heading', { name: locale === 'ko' ? '검토' : 'Review' }),
      ).toBeNull();
      await user.click(screen.getByRole('tab', { name: labels[2] }));
      const history = screen.getByRole('tabpanel', { name: labels[2] });
      expect(within(history).getByText('Recorded executed test')).toBeDefined();
      expect(within(history).getByRole('link', { name: 'Provider test' })).toBeDefined();
      expect(history.querySelector('.history-attempt details')).toBeNull();
      expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
    },
  );

  it('supports roving keyboard navigation and keeps inactive panels out of tab traversal', async () => {
    await hash('#task/T1?full=1');
    render(<App loader={async () => fixture()} />);
    await screen.findByRole('heading', { name: 'Primary task' });
    const user = userEvent.setup();
    const info = screen.getByRole('tab', { name: 'Task information' });
    const related = screen.getByRole('tab', { name: 'Related work' });
    const history = screen.getByRole('tab', { name: 'History' });
    info.focus();
    await user.keyboard('{ArrowRight}');
    expect(document.activeElement).toBe(related);
    expect(related.getAttribute('aria-selected')).toBe('true');
    expect(info.tabIndex).toBe(-1);
    expect(history.tabIndex).toBe(-1);
    expect(parseNavigation(window.location.hash).route.section).toBe('related');
    await user.keyboard('{End}');
    expect(document.activeElement).toBe(history);
    await user.keyboard('{ArrowRight}');
    expect(document.activeElement).toBe(info);
    expect(new URLSearchParams(window.location.hash.split('?')[1]).has('section')).toBe(false);
    await user.keyboard('{End}{Home}{ArrowLeft}');
    expect(document.activeElement).toBe(history);
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole('tabpanel', { name: 'History' }));
  });

  it('preserves expanded records through section switches and a changed refresh, without stealing tab focus', async () => {
    await hash('#task/T1?full=1&section=history');
    let snapshot = fixture();
    render(<App loader={async () => snapshot} />);
    await screen.findByRole('heading', { name: 'Primary task' });
    const user = userEvent.setup();
    const disclosure = screen.getByText('Attempt and review history').closest('details')!;
    await user.click(screen.getByText('Attempt and review history'));
    expect(disclosure.open).toBe(true);
    await user.click(screen.getByRole('tab', { name: 'Task information' }));
    await user.click(screen.getByRole('tab', { name: 'History' }));
    expect(screen.getByText('Attempt and review history').closest('details')).toBe(disclosure);
    expect(disclosure.open).toBe(true);
    snapshot = {
      ...snapshot,
      revision: 'sections-two',
      tasks: snapshot.tasks.map((task) =>
        task.id === 'T1' ? { ...task, next_action: 'Updated next action' } : task,
      ),
    };
    await user.click(screen.getByRole('button', { name: 'Refresh' }));
    const historyTab = screen.getByRole('tab', { name: 'History' });
    historyTab.focus();
    await screen.findByText('Updated next action');
    expect(historyTab.getAttribute('aria-selected')).toBe('true');
    expect(disclosure.open).toBe(true);
    expect(document.activeElement).toBe(historyTab);
  });

  it('retains URL selection and list context across inline/full/responsive detail, then resets for another task', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 });
    await hash('#task/T1?scope=all&status=review&owner=worker&page=1&size=50&task=T1');
    render(<App loader={async () => fixture()} />);
    await screen.findByRole('heading', { name: 'Primary task' });
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'History' }));
    const route = parseNavigation(window.location.hash);
    expect(route.filters.owner).toBe('worker');
    expect(route.filters.status).toBe('review');
    expect(route.paging.size).toBe(50);
    expect(route.selected).toBe('T1');
    expect(route.route.section).toBe('history');
    const full = screen.getByRole('link', { name: 'Open full detail' });
    expect(parseNavigation(full.getAttribute('href') || '').route.section).toBe('history');
    await user.click(full);
    await waitFor(() => expect(parseNavigation(window.location.hash).route.full).toBe(true));
    expect(screen.getByRole('tab', { name: 'History' }).getAttribute('aria-selected')).toBe('true');
    await act(async () => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
      window.dispatchEvent(new Event('resize'));
    });
    expect(screen.getByRole('tab', { name: 'History' }).getAttribute('aria-selected')).toBe('true');
    await hash('#task/T3?full=1');
    expect(
      screen.getByRole('tab', { name: 'Task information' }).getAttribute('aria-selected'),
    ).toBe('true');
    await user.click(screen.getByRole('tab', { name: 'Related work' }));
    const empty = screen.getByRole('tabpanel', { name: 'Related work' });
    expect(empty.textContent).toBe('No related work recorded.');
    expect(empty.querySelector('details')).toBeNull();
  });

  it('validates task-only section URLs and preserves unrelated navigation parameters', () => {
    const context = {
      ...defaultContext,
      filters: { ...defaultContext.filters, owner: 'worker' },
      selected: 'T1',
    };
    for (const section of ['related', 'history'] as const) {
      const url = navigationHash({ view: 'task', taskId: 'T1', full: true, section }, context);
      const parsed = parseNavigation(url);
      expect(parsed.route).toEqual({ view: 'task', taskId: 'T1', full: true, section });
      expect(parsed.filters.owner).toBe('worker');
      expect(parsed.selected).toBe('T1');
    }
    for (const url of [
      '#task/T1?section=invalid',
      '#task/T1?section=history&section=related',
      '#overview?section=history',
    ]) {
      const parsed = parseNavigation(url);
      expect(parsed.route.section).toBeUndefined();
      expect(parsed.issues).toContain('section');
    }
    expect(navigationHash({ view: 'task', taskId: 'T1' }, context)).not.toContain('section=');
    expect(navigationHash({ view: 'work', section: 'history' }, context)).not.toContain('section=');
  });
});
