import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { flowFixture, flowReference, flowCollection } from './flow.fixture';
import type { JsonRecord } from './model';
import { selectedPlan } from './flow-plan';
import { SnapshotResponse } from './useSnapshot';
import { LanguageProvider, PREFERENCE_KEY, translationTools } from './i18n';
import {
  ClosureSummary,
  IntegratedReviewFacts,
  IntegratedReviewNextAction,
  IntegratedObligations,
} from './flow-closure-ui';
import { closureContext, integratedReview } from './flow-closure';
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
  it('revalidates closure and plan references in independent same-revision snapshots', () => {
    const valid = flowFixture({ closure: closure() });
    const invalid = flowFixture({
      closure: closure({ reference: { ...flowReference(), revision: 'stale' } }),
    });
    expect(valid.revision).toBe(invalid.revision);
    expect(closureContext(valid)?.status).toBe('incomplete');
    expect(closureContext(invalid)).toBeNull();
    expect(closureContext(valid)?.status).toBe('incomplete');
    const selected = plan();
    expect(selectedPlan(valid, selected)?.publication).toBe('partial');
    expect(selectedPlan(valid, plan({ observed_revision: 'stale' }))).toBeNull();
    expect(selectedPlan(invalid, selected)?.publication).toBe('partial');
  });
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

const integratedObligation = (extra: JsonRecord = {}): JsonRecord => ({
  task_id: 'A',
  attempt: 1,
  check_id: 'scoped_tests',
  criterion: null,
  status: 'historical_only',
  actionable: false,
  basis: 'integrated_member',
  reason_codes: ['historical_execution'],
  next_actor_id: null,
  next_action: null,
  next_trigger: null,
  request_id: null,
  observation_id: null,
  ...extra,
});
function integratedFixture(extra: JsonRecord = {}) {
  const obligations = [
    integratedObligation(),
    integratedObligation({
      task_id: 'B',
      status: 'unmapped',
      reason_codes: ['mapping_absent'],
      basis: 'none',
    }),
  ];
  return flowFixture({
    objective: {
      id: 'goal-flow',
      title: '검토 근거 확인',
      criteria: ['원래 기준'],
      task_ids: ['A', 'B'],
      completion: {},
    },
    coverage: {
      objective_id: 'goal-flow',
      integrated_review: {
        schema_version: 1,
        status: 'accepted',
        currentness: 'current',
        reason_codes: [],
        reasons: [],
        basis: {
          identity: 'a'.repeat(64),
          source_head: 'b'.repeat(40),
          source_digest: 'c'.repeat(64),
          completion_ref: { path: 'completion.json', sha256: 'd'.repeat(64) },
          integrated_review_ref: { path: 'review.json', sha256: 'e'.repeat(64) },
          objective_binding: {
            program_id: 'flow-test',
            objective_id: 'goal-flow',
            criteria: ['원래 기준'],
            criteria_sha256: 'f'.repeat(64),
            membership_revision: null,
            member_ids: ['A', 'B'],
          },
        },
        counts: {
          members: 2,
          accepted_members: 2,
          obligations: 2,
          fulfilled: 0,
          unmapped: 1,
          historical: 1,
          actionable: 0,
        },
        members: ['A', 'B'].map((task_id) => ({
          task_id,
          attempt: 1,
          status: 'accepted',
          obligations: obligations.filter((row) => row.task_id === task_id),
        })),
        obligations,
        next_actor_id: 'user',
        next_action: 'set_next_goal',
        next_trigger: 'next_user_goal',
        remote_verified_by_tool: false,
        ...extra,
      },
    },
  });
}
it.each(['en', 'ko'])(
  'separates current integrated member acceptance from historical check execution in %s',
  (locale) => {
    localStorage.setItem(PREFERENCE_KEY, locale);
    const snapshot = integratedFixture();
    expect(integratedReview(snapshot)?.acceptedMemberIds).toEqual(['A', 'B']);
    const view = render(
      <LanguageProvider>
        <IntegratedReviewFacts snapshot={snapshot} taskId="B" />
        <IntegratedReviewNextAction snapshot={snapshot} fallback="fallback" />
      </LanguageProvider>,
    );
    expect(view.container.textContent).toContain(
      locale === 'ko'
        ? '통합 검토로 작업 완료 수락됨'
        : 'Member completion accepted by integrated review',
    );
    expect(view.container.textContent).toContain(
      locale === 'ko' ? '새 실행을 뜻하지 않습니다' : 'no new execution is implied',
    );
    expect(view.container.textContent).not.toContain('fallback');
  },
);
it('retains unaffected member acceptance when another member has a new adverse obligation', () => {
  const affected = integratedObligation({
    status: 'adverse',
    actionable: true,
    basis: 'none',
    reason_codes: ['verification_nonpassing'],
    next_actor_id: 'resolver',
    next_action: 'resolve_verification',
    next_trigger: 'current_verification_evidence',
  });
  const snapshot = integratedFixture({
    status: 'not_accepted',
    reason_codes: ['current_obligations_actionable'],
    counts: {
      members: 2,
      accepted_members: 1,
      obligations: 1,
      fulfilled: 0,
      unmapped: 0,
      historical: 0,
      actionable: 1,
    },
    members: [
      { task_id: 'A', attempt: 1, status: 'requires_recheck', obligations: [affected] },
      { task_id: 'B', attempt: 1, status: 'accepted', obligations: [] },
    ],
    obligations: [affected],
    next_actor_id: 'resolver',
    next_action: 'resolve_verification',
  });
  expect(integratedReview(snapshot)?.acceptedMemberIds).toEqual(['B']);
  const view = render(<IntegratedReviewFacts snapshot={snapshot} taskId="B" />);
  expect(view.container.textContent).toContain('Member completion accepted by integrated review');
  view.rerender(<IntegratedReviewFacts snapshot={snapshot} taskId="A" />);
  expect(view.container.textContent).toContain('Integrated acceptance needs attention');
});
const invalidIntegrated: readonly JsonRecord[] = [
  { status: 'accepted', basis: null },
  {
    counts: {
      members: 2,
      accepted_members: true,
      obligations: 2,
      fulfilled: 0,
      unmapped: 1,
      historical: 1,
      actionable: 0,
    },
  },
  { basis: { objective_binding: { program_id: 'foreign' } } },
];
it.each(invalidIntegrated)(
  'rejects malformed or foreign integrated acceptance without promoting old results %#',
  (change) => {
    expect(integratedReview(integratedFixture(change))).toBeNull();
  },
);

it('rejects contradictory member acceptance when the same member has an actionable adverse obligation', () => {
  const row = integratedObligation({
    status: 'adverse',
    actionable: true,
    basis: 'none',
    next_actor_id: 'resolver',
    next_action: 'resolve_verification',
  });
  const snapshot = integratedFixture({
    counts: {
      members: 2,
      accepted_members: 2,
      obligations: 1,
      fulfilled: 0,
      unmapped: 0,
      historical: 0,
      actionable: 1,
    },
    obligations: [row],
  });
  expect(integratedReview(snapshot)).toBeNull();
});

it.each(['en', 'ko'])(
  'localizes generated closure gates and avoids the redundant unmapped reason in %s',
  (locale) => {
    localStorage.setItem(PREFERENCE_KEY, locale);
    const snapshot = flowFixture({
      ...integratedFixture(),
      closure: closure({
        requirements: flowCollection([
          {
            kind: 'task_review',
            task_id: 'B',
            status: 'required',
            reason: 'Keep this authored detail exactly.',
            next_actor_id: 'worker',
            next_action: 'task_review',
          },
        ]),
      }),
    });
    render(
      <LanguageProvider>
        <ClosureSummary snapshot={snapshot} taskId="B" />
      </LanguageProvider>,
    );
    expect(screen.getByText(locale === 'ko' ? '작업 검토' : 'Task review')).toBeTruthy();
    expect(
      screen.getByText(locale === 'ko' ? /검사 연결 미평가$/ : /Check mapping not assessed$/),
    ).toBeTruthy();
    expect(
      screen.queryByText(
        locale === 'ko' ? '검사별 명시적인 연결 미평가' : 'Explicit check mapping not assessed',
      ),
    ).toBeNull();
    expect(screen.getByText('Keep this authored detail exactly.')).toBeTruthy();
  },
);
it.each(['en', 'ko'])(
  'keeps pending external execution distinct from accepted completion in %s',
  (locale) => {
    localStorage.setItem(PREFERENCE_KEY, locale);
    const row = integratedObligation({
      status: 'pending',
      actionable: true,
      basis: 'none',
      reason_codes: ['provider_execution_pending'],
      next_actor_id: 'resolver',
      next_action: 'resolve_verification',
      next_trigger: 'current_verification_evidence',
    });
    const snapshot = integratedFixture({
      status: 'not_accepted',
      counts: {
        members: 2,
        accepted_members: 1,
        obligations: 1,
        fulfilled: 0,
        unmapped: 0,
        historical: 0,
        actionable: 1,
      },
      members: [
        { task_id: 'A', attempt: 1, status: 'requires_recheck', obligations: [row] },
        { task_id: 'B', attempt: 1, status: 'accepted', obligations: [] },
      ],
      obligations: [row],
    });
    render(
      <LanguageProvider>
        <IntegratedObligations snapshot={snapshot} taskId="A" />
      </LanguageProvider>,
    );
    expect(
      screen.getByText(
        locale === 'ko' ? '외부 실행 결과 확인이 필요해요' : 'Provider execution is pending',
      ),
    ).toBeTruthy();
  },
);
