import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { CheckInputs } from './flow-inputs-ui';
import { LocalExecution } from './flow-execution-ui';
import { flowFixture, flowInput, flowExecution, flowCollection } from './flow.fixture';
import { inputSummary } from './flow-inputs';
import { capturedExecution } from './flow-execution';
import { record } from './model';
import type { JsonRecord, Snapshot } from './model';
import { SnapshotResponse } from './useSnapshot';

describe('flow verification summaries', () => {
  it('shows incomplete precise inputs and changed groups without disclosing source payloads', () => {
    const view = render(
      <CheckInputs
        value={{
          ...flowInput,
          raw_paths: ['hidden/source.ts'],
          environment_facts: { secret: 'hidden-value' },
        }}
      />,
    );
    expect(screen.getByText('Input coverage unknown')).toBeTruthy();
    expect(screen.getByText('An input changed')).toBeTruthy();
    expect(screen.getByText('Test inputs')).toBeTruthy();
    expect(view.container.textContent).not.toContain('hidden/source.ts');
    expect(view.container.textContent).not.toContain('hidden-value');
    expect(view.container.querySelector('details details')).toBeNull();
  });
  it('preserves unknown/unavailable input coverage and rejects malformed counts', () => {
    expect(
      inputSummary({ ...flowInput, status: 'unavailable', applicability: 'unavailable' })
        ?.completeness,
    ).toBe('unknown');
    expect(
      inputSummary({ ...flowInput, counts: { known: -1, changed: 0, missing: 0, unknown: 0 } }),
    ).toBeNull();
    expect(
      inputSummary({ ...flowInput, groups: { total: 4, shown: 0, omitted: 0, items: [] } }),
    ).toBeNull();
  });
  it('keeps process completion, reported outcome, artifact gaps and publication separate', () => {
    const view = render(
      <LocalExecution
        value={{
          ...flowExecution,
          argv: ['hidden-command'],
          stdout: 'hidden-log',
          environment: { token: 'hidden-token' },
        }}
      />,
    );
    expect(screen.getByText('Process exited')).toBeTruthy();
    expect(screen.getByText('Evidence publication pending')).toBeTruthy();
    expect(screen.getByText('Evidence: 1 / 2 artifacts captured', { exact: false })).toBeTruthy();
    expect(screen.getByText('1 missing artifacts', { exact: false })).toBeTruthy();
    expect(screen.getAllByText('Output truncated', { exact: false }).length).toBeGreaterThan(0);
    for (const hidden of ['hidden-command', 'hidden-log', 'hidden-token'])
      expect(view.container.textContent).not.toContain(hidden);
    expect(view.container.querySelector('details details')).toBeNull();
  });
  it.each(['spawn_failed', 'timed_out', 'canceled', 'interrupted', 'outcome_unknown'])(
    'retains adverse actual process result %s rather than implying passing execution',
    (outcome) => {
      const capture = capturedExecution({
        ...flowExecution,
        process_result: outcome,
        phase: outcome === 'outcome_unknown' ? 'interrupted' : 'finished',
        reported_check_result: outcome === 'outcome_unknown' ? 'unknown' : 'failed',
      });
      expect(capture?.processResult).toBe(outcome);
      expect(capture?.reportedResult).not.toBe('passed');
    },
  );
  it('keeps older failure and reused success as history when current observation has no capture', async () => {
    window.history.replaceState(null, '', '#task/A');
    const snapshot = flowFixture(),
      task = snapshot.tasks[0];
    if (!task) throw new Error('Fixture requires task A');
    const verification = record(task.verification),
      lanes = record(verification.lanes);
    const lane = record(Array.isArray(lanes.items) ? lanes.items[0] : undefined);
    const { execution_summary: _current, ...withoutCurrent } = lane;
    const failed = {
      ...flowExecution,
      run_id: 'failed-run',
      reported_check_result: 'failed',
      publication: { status: 'recorded', observation_id: 'failure-observation' },
    };
    const reused = {
      ...flowExecution,
      run_id: 'passed-run',
      reported_check_result: 'passed',
      reused: true,
      publication: { status: 'recorded', observation_id: 'reuse-observation' },
    };
    const changed = {
      ...task,
      verification: {
        ...verification,
        lanes: {
          ...lanes,
          items: [
            {
              ...withoutCurrent,
              result: 'unknown',
              execution_history: flowCollection([failed, reused]),
            },
          ],
        },
      },
    };
    const view = render(
      <App loader={async () => ({ ...snapshot, tasks: [changed, ...snapshot.tasks.slice(1)] })} />,
    );
    await screen.findByText('No current execution capture recorded; earlier runs remain history.');
    expect(view.container.querySelector('.execution-summary')).toBeNull();
    const history = view.container.querySelector<HTMLDetailsElement>('.local-run-history');
    expect(history?.open).toBe(false);
    expect(history?.querySelector('summary')?.textContent).toContain('1 failed results');
    expect(history?.querySelector('summary')?.textContent).toContain('1 passed results');
    expect(screen.getByText('Reused execution evidence')).toBeTruthy();
  });
  it('retains unavailable runs alongside known history and stable open disclosures through refresh', async () => {
    window.history.replaceState(null, '', '#task/A');
    const initial = flowFixture(),
      task = initial.tasks[0];
    if (!task) throw new Error('Fixture requires task A');
    const verification = record(task.verification),
      lanes = record(verification.lanes);
    const lane = record(Array.isArray(lanes.items) ? lanes.items[0] : undefined);
    const unavailable: JsonRecord = {
      schema_version: 1,
      status: 'unavailable',
      next_action: 'Inspect unavailable capture',
      reasons: ['unavailable_capture'],
    };
    let next: Snapshot = {
      ...initial,
      tasks: [
        {
          ...task,
          verification: {
            ...verification,
            lanes: {
              ...lanes,
              items: [{ ...lane, execution_history: flowCollection([flowExecution, unavailable]) }],
            },
          },
        },
        ...initial.tasks.slice(1),
      ],
    };
    const loader = vi.fn(async () => new SnapshotResponse(200, null, next));
    const view = render(<App loader={loader} />);
    await screen.findByRole('heading', { name: 'Verification lanes' });
    const history = view.container.querySelector<HTMLDetailsElement>('.local-run-history');
    if (!history) throw new Error('History is required');
    history.open = true;
    next = { ...next, generated_at: '2026-10-14T10:03:00Z' };
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Refresh' })));
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
    expect(history.isConnected).toBe(true);
    expect(history.open).toBe(true);
    expect(history.textContent).toContain('Execution capture is unavailable.');
  });
});
