import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { App } from './App';
import { reviewQueue } from './flow-queue';
import { flowFixture, flowQueue, flowQueueRow, flowReference } from './flow.fixture';
import { record } from './model';
import type { JsonRecord } from './model';
import { defaultContext, navigationHash, parseNavigation } from './navigation';
import { PREFERENCE_KEY } from './i18n';
import { SnapshotResponse } from './useSnapshot';

describe('flow review queue', () => {
  it('uses producer order and shows distinct task reviews and required-check gaps without invented requests', async () => {
    window.history.replaceState(null, '', '#work?coordination=review_queue');
    const rows = [
      flowQueueRow('gap-B', 'B', {
        kind: 'required_check_gap',
        request_id: null,
        request_version: null,
        lane: null,
        check_ids: ['scoped_tests'],
        phase: 'planned',
        action: 'assign',
        readiness: { status: 'not_assessed', reasons: ['Assignment is not recorded'] },
        next_actor_id: null,
      }),
      flowQueueRow('review-A', 'A', {
        kind: 'task_review',
        request_id: null,
        request_version: null,
        lane: null,
        action: 'review',
        next_action: 'Review the submission',
      }),
    ];
    const view = render(
      <App loader={async () => flowFixture({ review_queue: flowQueue(rows) })} />,
    );
    await screen.findByRole('region', { name: 'Recorded review obligations' });
    expect(
      Array.from(view.container.querySelectorAll('.queue-task')).map((row) =>
        row.textContent?.trim(),
      ),
    ).toEqual(['B Second flow task', 'A First flow task']);
    expect(screen.getByText('Check coverage gap', { exact: false })).toBeTruthy();
    expect(screen.getByText('Submitted review')).toBeTruthy();
    expect(screen.getByText('Assignment is not recorded')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Run the assigned check' })).toBeNull();
  });
  it('retains honest partial counts and unknown empty results after filtering bounded rows', async () => {
    window.history.replaceState(null, '', '#work?coordination=review_queue&owner=resolver');
    const snapshot = flowFixture({ review_queue: flowQueue([], 73) });
    render(<App loader={async () => snapshot} />);
    await screen.findByRole('region', { name: 'Recorded review obligations' });
    expect(screen.getByText('73 recorded obligations', { exact: false })).toBeTruthy();
    expect(
      screen.getByText('73 omitted; filtered results are partial.', { exact: false }),
    ).toBeTruthy();
    expect(
      screen.getByText('No supplied obligations match; omitted work remains unknown.'),
    ).toBeTruthy();
    expect(screen.queryByText('No review obligations recorded.')).toBeNull();
  });
  it('filters next responsibility and readiness while ordinary ownership remains separate', async () => {
    window.history.replaceState(
      null,
      '',
      '#work?coordination=review_queue&owner=resolver&queue_readiness=blocked',
    );
    const snapshot = flowFixture({
      review_queue: flowQueue([
        flowQueueRow('blocked-A', 'A', {
          next_actor_id: 'resolver',
          resolver_id: 'resolver',
          action: 'resolve_blocker',
          readiness: { status: 'blocked', reasons: ['Repair the actual blocker'] },
        }),
        flowQueueRow('ready-B', 'B'),
      ]),
    });
    const view = render(<App loader={async () => snapshot} />);
    await screen.findByRole('region', { name: 'Recorded review obligations' });
    expect(view.container.querySelectorAll('.queue-row')).toHaveLength(1);
    expect(view.container.querySelector('.queue-row')?.textContent).toContain(
      'Repair the actual blocker',
    );
    const context = {
      ...defaultContext,
      filters: {
        ...defaultContext.filters,
        coordination: 'review_queue',
        owner: 'resolver',
        queue_readiness: 'blocked',
      },
    };
    expect(parseNavigation(navigationHash({ view: 'work' }, context), snapshot).filters).toEqual(
      context.filters,
    );
  });
  it('rejects stale or inconsistent queue projections instead of showing a successful empty state', () => {
    const snapshot = flowFixture(),
      queue = record(snapshot.review_queue);
    expect(reviewQueue(snapshot)?.total).toBe(1);
    const changes: readonly JsonRecord[] = [
      { observed_revision: 'old' },
      { counts: {} },
      { rows: { ...record(queue.rows), total: 4 } },
      { rows: { ...record(queue.rows), reference: { ...flowReference(), revision: 'old' } } },
      {
        rows: {
          ...record(queue.rows),
          items: [flowQueueRow('unknown', 'A', { action: 'launch_agent' })],
        },
      },
    ];
    for (const change of changes)
      expect(reviewQueue(flowFixture({ review_queue: { ...queue, ...change } }))).toBeNull();
  });
  it('shows resolver responsibility on Team without changing code ownership', async () => {
    window.history.replaceState(null, '', '#team');
    render(
      <App
        loader={async () =>
          flowFixture({
            review_queue: flowQueue([
              flowQueueRow('blocked-A', 'A', {
                next_actor_id: 'resolver',
                resolver_id: 'resolver',
                action: 'resolve_blocker',
                next_action: 'Resolve the blocker',
                readiness: { status: 'blocked', reasons: [] },
              }),
            ]),
          })
        }
      />,
    );
    await screen.findByRole('heading', { name: 'Next review responsibility' });
    expect(screen.getByRole('link', { name: 'Open review queue' })).toBeTruthy();
    expect(screen.getByText('Resolve the blocker')).toBeTruthy();
    expect(
      screen
        .getAllByRole('link', { name: 'resolver' })
        .some((link) => link.getAttribute('href')?.includes('coordination=review_queue')),
    ).toBe(true);
  });
  it('preserves focused row and sibling disclosure through changed refresh', async () => {
    window.history.replaceState(null, '', '#work?coordination=review_queue');
    let next = flowFixture();
    const loader = vi.fn(async () => new SnapshotResponse(200, null, next));
    const view = render(<App loader={loader} />);
    await screen.findByRole('region', { name: 'Recorded review obligations' });
    const link = view.container.querySelector<HTMLAnchorElement>('.queue-task');
    const details = view.container.querySelector<HTMLDetailsElement>('.queue-details');
    expect(link && details).toBeTruthy();
    link?.focus();
    if (details) details.open = true;
    next = flowFixture({
      generated_at: '2026-10-14T10:02:00Z',
      checkpoint: { note: 'New unrelated observation' },
    });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Refresh' })));
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
    expect(link?.isConnected).toBe(true);
    expect(document.activeElement).toBe(link);
    expect(details?.open).toBe(true);
  });
  it('defaults open queue filtering to nonterminal work while retaining completed records', async () => {
    window.history.replaceState(null, '', '#work?coordination=review_queue&queue_readiness=open');
    const view = render(
      <App
        loader={async () =>
          flowFixture({
            review_queue: flowQueue([
              flowQueueRow('open-A'),
              flowQueueRow('terminal-B', 'B', {
                action: 'none',
                phase: 'finished',
                result: 'passed',
                readiness: { status: 'terminal', reasons: [] },
              }),
            ]),
          })
        }
      />,
    );
    await screen.findByRole('region', { name: 'Recorded review obligations' });
    expect(view.container.querySelectorAll('.queue-row')).toHaveLength(1);
    expect(view.container.querySelector('.queue-row')?.textContent).toContain('First flow task');
    fireEvent.change(screen.getByLabelText('Readiness'), { target: { value: 'terminal' } });
    await waitFor(() =>
      expect(view.container.querySelector('.queue-row')?.textContent).toContain('Second flow task'),
    );
  });
  it('continues focus at the queue when completion removes the focused open item', async () => {
    window.history.replaceState(null, '', '#work?coordination=review_queue&queue_readiness=open');
    let next = flowFixture();
    const loader = vi.fn(async () => new SnapshotResponse(200, null, next));
    const view = render(<App loader={loader} />);
    const region = await screen.findByRole('region', { name: 'Recorded review obligations' });
    const link = view.container.querySelector<HTMLAnchorElement>('.queue-task');
    if (!link) throw new Error('Open review link is required');
    link.focus();
    next = flowFixture({
      review_queue: flowQueue([
        flowQueueRow('queue-A', 'A', {
          phase: 'finished',
          result: 'passed',
          action: 'none',
          readiness: { status: 'terminal', reasons: [] },
        }),
      ]),
    });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Refresh' })));
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
    expect(link.isConnected).toBe(false);
    expect(document.activeElement).toBe(region);
  });
  it('localizes queue system copy while preserving task identity', async () => {
    localStorage.setItem(PREFERENCE_KEY, 'ko');
    window.history.replaceState(null, '', '#work?coordination=review_queue');
    render(<App loader={async () => flowFixture()} />);
    await screen.findByRole('region', { name: '기록된 검토 항목' });
    expect(screen.getByLabelText('검토 준비')).toBeTruthy();
    expect(screen.getAllByText('준비됨').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '검토 대기열' })).toBeTruthy();
    expect(screen.getByRole('link', { name: /A First flow task/ })).toBeTruthy();
  });
});
