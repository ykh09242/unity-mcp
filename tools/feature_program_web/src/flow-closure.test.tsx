import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { flowFixture, flowReference, flowCollection } from './flow.fixture';
import type { JsonRecord } from './model';
import { selectedPlan } from './flow-plan';
import { SnapshotResponse } from './useSnapshot';

const closure = (extra: JsonRecord = {}): JsonRecord => ({
  schema_version: 1,
  status: 'incomplete',
  objective_id: 'goal-flow',
  task_ids: ['A', 'B'],
  reference: flowReference(),
  requirements: flowCollection([
    {
      kind: 'task_review',
      task_id: 'A',
      status: 'required',
      reason: 'Current work is unfinished',
      next_actor_id: 'worker',
      next_action: 'task_review',
    },
  ]),
  tasks: [],
  accepted: false,
  remote_verified_by_tool: false,
  next_actor_id: 'worker',
  next_action: 'task_review',
  ...extra,
});
const plan = (extra: JsonRecord = {}): JsonRecord => ({
  schema_version: 1,
  plan_digest: 'a'.repeat(64),
  observed_revision: 'flow-one',
  reference: { path: 'evidence/_coordination/attempt-1/plan.json', sha256: 'b'.repeat(64) },
  preparation: { status: 'prepared' },
  preflight: { status: 'validated', current: true, historical_registry_revision: 'initial' },
  publication: {
    status: 'partial',
    stages: flowCollection([
      {
        id: 'record-first',
        operation: 'verification-record',
        task_id: 'A',
        status: 'committed',
        next_actor_id: 'reviewer',
        next_action: 'inspect_postconditions',
        view_recovery_required: false,
      },
      {
        id: 'record-second',
        operation: 'checkpoint',
        task_id: null,
        status: 'not_started',
        next_actor_id: 'worker',
        next_action: 'publish_remaining_records',
        view_recovery_required: false,
      },
    ]),
  },
  next_actor_id: 'worker',
  next_action: 'publish_remaining_records',
  reasons: [],
  accepted: false,
  remote_verified_by_tool: false,
  ...extra,
});
describe('flow closure and overview', () => {
  it('keeps ordinary unfinished goal completion reminders out of prominent alerts while preserving failures', async () => {
    window.history.replaceState(null, '', '#overview');
    const snapshot = flowFixture();
    const attention: readonly JsonRecord[] = [
      {
        kind: 'objective',
        entity_id: 'goal-flow',
        attention_kind: 'completion_reminder',
        actionability: 'routine',
        reason: 'Ordinary completion reminder',
        next_action: 'Inspect the objective-completion prerequisites',
        task_ids: ['A', 'B'],
        reference: flowReference(),
        observed_revision: 'flow-one',
      },
      {
        kind: 'integrity',
        entity_id: 'problem',
        actionability: 'actionable',
        reason: 'Actual source failure',
        next_action: 'Inspect source failure',
        task_ids: ['A'],
        reference: flowReference(),
        observed_revision: 'flow-one',
      },
    ];
    render(
      <App
        loader={async () => ({
          ...snapshot,
          closure: closure(),
          brief: {
            brief_version: 1,
            revision: 'flow-one',
            program_id: snapshot.program_id,
            reference: flowReference(),
            attention: flowCollection(attention),
          },
        })}
      />,
    );
    await screen.findByRole('heading', { name: /^Needs attention/ });
    expect(screen.queryByText('Ordinary completion reminder')).toBeNull();
    expect(screen.getByText('Actual source failure')).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Closure next action' })).toBeNull();
  });
  it('shows actual selected partial publication and next responsibility without objective acceptance', async () => {
    window.history.replaceState(null, '', '#task/A');
    const view = render(
      <App loader={async () => flowFixture({ closure: closure({ plan: plan() }) })} />,
    );
    await screen.findByText('Selected closure plan');
    expect(screen.getByText('Preflight validated')).toBeTruthy();
    expect(view.container.querySelector('.plan-summary .flow-next-action')?.textContent).toContain(
      'Publish remaining records',
    );
    expect(view.container.querySelector('.plan-summary')?.textContent).toContain('partial');
    expect(screen.queryByText('Objective acceptance recorded')).toBeNull();
    expect(view.container.querySelectorAll('.plan-summary details details')).toHaveLength(0);
  });
  it('renders actual failed preparation with no fake prepared or validated state', async () => {
    window.history.replaceState(null, '', '#task/A');
    const { plan_digest: _digest, ...withoutDigest } = plan();
    render(
      <App
        loader={async () =>
          flowFixture({
            closure: closure({
              plan: {
                ...withoutDigest,
                preparation: { status: 'failed' },
                preflight: { status: 'failed', current: false },
                publication: { status: 'blocked', stages: flowCollection([]) },
                next_action: 'correct_preparation_input',
                reasons: ['Actual later-stage preparation failure'],
              },
            }),
          })
        }
      />,
    );
    await screen.findByText('Preflight failed');
    expect(screen.queryByText('Preflight validated')).toBeNull();
    expect(screen.getByText('Actual later-stage preparation failure')).toBeTruthy();
  });
  it('rejects mismatched selected plan refs/revisions and preserves historical preflight as a separate fact', () => {
    const snapshot = flowFixture();
    expect(selectedPlan(snapshot, plan())?.publication).toBe('partial');
    expect(selectedPlan(snapshot, plan({ observed_revision: 'old' }))).toBeNull();
    expect(selectedPlan(snapshot, plan({ reference: { path: 'plan', sha256: 'bad' } }))).toBeNull();
    expect(
      selectedPlan(snapshot, plan({ preflight: { status: 'validated', current: false } }))
        ?.preflightCurrent,
    ).toBe(false);
  });
  it('preserves opened selected-plan stages through record-only refresh', async () => {
    window.history.replaceState(null, '', '#task/A');
    let next = flowFixture({ closure: closure({ plan: plan() }) });
    const loader = vi.fn(async () => new SnapshotResponse(200, null, next));
    const view = render(<App loader={loader} />);
    await screen.findByText('Selected closure plan');
    const details = view.container.querySelector<HTMLDetailsElement>('.plan-summary details');
    if (!details) throw new Error('Stages are required');
    details.open = true;
    next = flowFixture({
      closure: closure({
        plan: plan({
          publication: {
            status: 'published',
            stages: flowCollection([
              {
                id: 'record-first',
                operation: 'verification-record',
                task_id: 'A',
                status: 'committed',
                next_actor_id: 'reviewer',
                next_action: 'inspect_postconditions',
                view_recovery_required: false,
              },
              {
                id: 'record-second',
                operation: 'checkpoint',
                task_id: null,
                status: 'committed',
                next_actor_id: 'worker',
                next_action: 'inspect_postconditions',
                view_recovery_required: false,
              },
            ]),
          },
        }),
      }),
    });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Refresh' })));
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
    expect(details.isConnected).toBe(true);
    expect(details.open).toBe(true);
    expect(view.container.querySelector('.plan-summary')?.textContent).toContain('published');
    expect(view.container.querySelector('.closure-summary')?.textContent).toContain(
      'Closure work remains',
    );
  });
});
