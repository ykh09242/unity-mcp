import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { flowFixture, flowReference, flowCollection } from './flow.fixture';
import type { JsonRecord } from './model';
import { selectedPlan } from './flow-plan';
import { SnapshotResponse } from './useSnapshot';
import { LanguageProvider, PREFERENCE_KEY, translationTools } from './i18n';
import { ClosureSummary } from './flow-closure-ui';
import { PlanSummary } from './flow-plan-ui';

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
  it.each(['en', 'ko'])(
    'localizes fixed closure readiness and plan diagnostics while preserving authored limitations in %s',
    (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const snapshot = flowFixture({
        closure: closure({
          requirements: flowCollection([
            {
              kind: 'task_review',
              task_id: 'A',
              status: 'required',
              reason: 'Current required task is not reviewed complete',
              next_action: 'task_review',
            },
            {
              kind: 'integrated_review',
              status: 'required',
              reason:
                'Fresh independent aggregate review and explicit director decision remain required',
              next_action: 'integrated_review',
            },
            {
              kind: 'current_verification',
              task_id: 'A',
              status: 'required',
              reason: 'Authored verification limitation',
              next_action: 'current_verification',
            },
            {
              kind: 'handoff',
              status: 'required',
              reason: 'Authored handoff limitation',
              next_action: 'handoff',
            },
          ]),
          plan: plan({
            preparation: { status: 'stale' },
            preflight: { status: 'not_assessed', current: false },
            publication: { status: 'blocked', stages: flowCollection([]) },
            reasons: [
              'Closure source changed',
              'Canonical closure stage conflicts with host assessment',
              'Authored closure limitation',
            ],
          }),
        }),
      });
      render(
        <LanguageProvider>
          <ClosureSummary snapshot={snapshot} taskId="A" />
        </LanguageProvider>,
      );
      for (const label of locale === 'ko'
        ? [
            '필수 작업의 검토 완료가 아직 기록되지 않았어요',
            '최신 독립 통합 검토와 디렉터의 완료 결정이 필요해요',
            '마무리 계획의 소스가 변경됐어요',
            '현재 마무리 기록이 도구의 확인 결과와 달라요',
            '현재 검증',
            '인계',
          ]
        : [
            'Current required task is not reviewed complete',
            'Fresh independent aggregate review and explicit director decision remain required',
            'Closure source changed',
            'Canonical closure stage conflicts with host assessment',
            'Current verification',
            'Handoff',
          ])
        expect(screen.getByText(label)).toBeTruthy();
      for (const label of [
        'Authored verification limitation',
        'Authored handoff limitation',
        'Authored closure limitation',
      ])
        expect(screen.getByText(label)).toBeTruthy();
    },
  );
  it('has localized exact diagnostic labels for the bounded selected-plan inspection vocabulary', () => {
    const messages = [
      'Unsupported closure plan schema',
      'Closure plan changed',
      'Closure producer identity changed',
      'Closure program identity differs',
      'Closure source changed',
      'Closure predecessor chain changed',
      'Invalid stage event position',
      'Stage event identity changed',
      'Closure commit order differs',
      'Canonical closure stage conflicts with host assessment',
      'Plan preflight provenance incomplete',
      'Invalid recorded preparation failure',
      'Failure producer identity changed',
      'Unsupported closure operation',
    ];
    const i18n = translationTools('ko');
    for (const message of messages) {
      expect(i18n.warning(message)).not.toBe(message);
      expect(i18n.warning(message)).toMatch(/[가-힣]/u);
    }
    expect(i18n.warning('Unfamiliar authored diagnostic')).toBe('Unfamiliar authored diagnostic');
  });
  const operationLabels: readonly (readonly [string, string, string])[] = [
    ['verification-record', 'Record verification result', '검증 결과 기록'],
    ['submit', 'Submit task', '작업 제출'],
    ['complete', 'Record task acceptance', '작업 완료 승인 기록'],
    ['rework', 'Record rework', '재작업 기록'],
    ['delivery', 'Record delivery observation', '전달 결과 기록'],
    ['objective-complete', 'Record objective acceptance', '목표 완료 승인 기록'],
    ['note', 'Record progress', '진행 기록'],
    ['checkpoint', 'Record checkpoint', '진행 상황 기록'],
    ['resume', 'Record resume', '작업 재개 기록'],
  ];
  const operationStages = (operations: readonly string[]) =>
    flowCollection(
      operations.map((operation, index) => ({
        id: 'stage-' + index,
        operation,
        task_id: index === 3 ? 'B' : 'A',
        status: index < 2 ? 'committed' : 'not_started',
        next_actor_id: 'worker',
        next_action: index < 2 ? 'inspect_postconditions' : 'publish_remaining_records',
        view_recovery_required: false,
      })),
    );
  it.each(['en', 'ko'])(
    'localizes the actual submit/complete/delivery/note partial plan in %s',
    (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const view = render(
        <LanguageProvider>
          <PlanSummary
            snapshot={flowFixture({
              closure: closure({
                plan: plan({
                  publication: {
                    status: 'partial',
                    stages: operationStages(['submit', 'complete', 'delivery', 'note']),
                  },
                }),
              }),
            })}
          />
        </LanguageProvider>,
      );
      const labels = Array.from(
        view.container.querySelectorAll('.flow-details-list li > strong'),
      ).map((node) => node.textContent);
      expect(labels).toEqual(
        locale === 'ko'
          ? ['작업 제출', '작업 완료 승인 기록', '전달 결과 기록', '진행 기록']
          : [
              'Submit task',
              'Record task acceptance',
              'Record delivery observation',
              'Record progress',
            ],
      );
      expect(view.container.querySelectorAll('.flow-details-list li')).toHaveLength(4);
    },
  );
  it.each(['en', 'ko'])(
    'labels the bounded host-approved operation set and preserves unfamiliar codes in %s',
    (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const view = render(
        <LanguageProvider>
          <PlanSummary
            snapshot={flowFixture({
              closure: closure({
                plan: plan({
                  publication: {
                    status: 'partial',
                    stages: operationStages([
                      ...operationLabels.map(([operation]) => operation),
                      'future-operation',
                    ]),
                  },
                }),
              }),
            })}
          />
        </LanguageProvider>,
      );
      const labels = Array.from(
        view.container.querySelectorAll('.flow-details-list li > strong'),
      ).map((node) => node.textContent);
      expect(labels).toEqual([
        ...operationLabels.map(([, english, korean]) => (locale === 'ko' ? korean : english)),
        'future-operation',
      ]);
    },
  );
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
