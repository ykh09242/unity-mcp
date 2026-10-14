import { describe, expect, it } from 'vitest';
import {
  attemptFacts,
  deliveryHistory,
  historyComparison,
  latestReview,
  revisionMismatch,
} from './history';
import { parseSnapshot, record } from './model';
import type { JsonRecord, Task } from './model';
import { render, screen, within } from '@testing-library/react';
import { LanguageProvider, PREFERENCE_KEY } from './i18n';
import {
  DeliveryHistory,
  ReviewSummary,
  StaffingHistory,
  TaskHistory,
  TransferHistory,
} from './history-ui';

const taskFixture = (extra: JsonRecord = {}): Task => ({
  id: 'A1',
  title: 'Authored task',
  status: 'review',
  ...extra,
});
const snapshotFixture = (task: Task, extra: JsonRecord = {}) =>
  parseSnapshot({
    snapshot_version: 1,
    revision: 'snapshot-one',
    program_id: 'history-fixture',
    tasks: [task],
    ...extra,
  });
const ref = { path: 'evidence/A1/attempt-1/result.json', sha256: 'exact-hash' };
const delivery = (conclusion: string, extra: JsonRecord = {}) => ({
  status: 'ci_recorded_success',
  receipt: ref,
  metadata: {
    attempt: 1,
    reviewed_source_digest: 'old-source',
    tested_commit: 'tested',
    required_ci: ['unit', 'not-observed'],
    ci: [{ name: 'unit', conclusion, commit: 'different', run_id: 10, job_id: 11 }],
    merge_status: 'open',
    ...extra,
  },
});

describe('readable history surfaces', () => {
  it('distinguishes missing projection from explicitly empty delivery history', () => {
    const view = render(<DeliveryHistory task={taskFixture()} />);
    expect(screen.getByText('Delivery history unavailable')).toBeDefined();
    expect(screen.queryByText('No delivery history recorded')).toBeNull();
    view.rerender(
      <DeliveryHistory
        task={taskFixture({ delivery_history: { status: 'unrecorded', items: [] } })}
      />,
    );
    expect(screen.getByText('No delivery history recorded')).toBeDefined();
  });
  it.each(['skipped', 'neutral', 'failure', 'unavailable'])(
    'retains %s CI and separates missing checks, source mismatch and cleanup',
    (conclusion) => {
      render(
        <DeliveryHistory
          task={taskFixture({
            attempts: [{ number: 2, rounds: [{ source_digest: 'current-source' }] }],
            delivery: delivery(conclusion),
          })}
        />,
      );
      expect(screen.getByText(conclusion)).toBeDefined();
      expect(screen.getByText('Check revision differs from tested commit')).toBeDefined();
      expect(
        screen.getByText('This delivery belongs to a different attempt or reviewed source'),
      ).toBeDefined();
      expect(
        screen.getByText('Required checks without observations').nextElementSibling?.textContent,
      ).toContain('not-observed');
      expect(screen.getByText('Cleanup').nextElementSibling?.textContent).toBe('Unknown');
      expect(screen.getByText('Merge').nextElementSibling?.textContent).toBe('open');
    },
  );
  it('preserves historical receipt identity, ambiguous time and an explicit deferred cleanup reason', () => {
    render(
      <DeliveryHistory
        task={taskFixture({
          delivery: { status: 'unrecorded' },
          delivery_history: {
            status: 'recorded',
            items: [
              {
                record: delivery('success', {
                  cleanup: {
                    status: 'deferred',
                    reason: 'Keep checkout for follow-up',
                    merge_identity: 'merge-exact',
                  },
                }),
                time_status: 'unknown',
                recorded_at: null,
                reference: {
                  path: 'backlog.json',
                  pointer: '/tasks/0/deliveries/0',
                  revision: 'snapshot-one',
                },
              },
            ],
          },
        })}
      />,
    );
    expect(screen.getByText(/Time unknown/)).toBeDefined();
    expect(screen.getByText('Keep checkout for follow-up')).toBeDefined();
    expect(screen.queryByText('merge-exact')).toBeNull();
    expect(screen.queryByText('/tasks/0/deliveries/0')).toBeNull();
    expect(screen.queryByRole('link', { name: ref.path })).toBeNull();
  });
  it('keeps current review, independent readiness limits, old findings and explicit criterion paths separate', () => {
    const task = taskFixture({
      attempts: [
        {
          number: 1,
          owner: 'old-worker',
          status: 'released',
          scope: ['old.ts'],
          rounds: [
            {
              round: 1,
              source_digest: 'old',
              receipt_record: { checks: [{ id: 'unit', status: 'failed' }] },
              review_record: {
                verdict: 'rejected',
                unresolved_findings: ['Original finding remains'],
              },
            },
          ],
        },
        {
          number: 2,
          owner: 'worker',
          scope: ['new.ts'],
          rounds: [
            {
              round: 1,
              source_digest: 'new',
              receipt_record: {
                checks: [{ id: 'unit', status: 'passed', execution_kind: 'unit' }],
              },
              review_record: {
                verdict: 'accepted',
                reviewer: 'reviewer',
                reviewer_role: 'leader',
                unresolved_findings: [],
                next_action: 'Authored next step',
              },
            },
          ],
        },
      ],
    });
    const snapshot = snapshotFixture(task, {
      director: { actor_id: 'director' },
      coverage: {
        integrated_review: {
          status: 'not_assessed',
          requires: ['Independent complete review'],
          limitations: ['No live source check'],
        },
        criteria: [
          {
            text: 'Exact criterion',
            linkage: 'linked',
            links: [
              {
                task_id: 'A1',
                paths: ['src/exact.ts'],
                scope_kind: 'submitted',
                criterion: { ordinal: 0, text_sha256: 'criterion-hash' },
                evidence: [ref],
              },
            ],
          },
        ],
      },
    });
    render(
      <>
        <ReviewSummary task={task} snapshot={snapshot} />
        <TaskHistory task={task} snapshot={snapshot} />
      </>,
    );
    expect(screen.getAllByText('Authored next step').length).toBeGreaterThan(0);
    expect(screen.getByText('Independent complete review')).toBeDefined();
    expect(screen.getByText('No live source check')).toBeDefined();
    expect(screen.getAllByText('Original finding remains').length).toBeGreaterThan(0);
    expect(screen.queryByText('src/exact.ts')).toBeNull();
    expect(screen.queryByText('criterion-hash')).toBeNull();
    expect(screen.getAllByText('failed').length).toBeGreaterThan(0);
    expect(screen.getAllByText('passed').length).toBeGreaterThan(0);
  });
  it('retains each transfer acknowledgement and unknown comparisons without implying successor activation', () => {
    render(
      <TransferHistory
        handoff={{
          to_actor: 'successor',
          outgoing: { from_actor: 'old', next_action: 'Inspect original', scope: ['one'] },
          acknowledgements: [
            { receipt: { actor_id: 'successor', summary: 'First ACK' }, ref },
            { receipt: { actor_id: 'successor', summary: 'Revised ACK' }, ref },
          ],
          acknowledgement: { summary: 'Revised ACK' },
        }}
      />,
    );
    expect(screen.getByText('First ACK')).toBeDefined();
    expect(screen.getByText('Revised ACK')).toBeDefined();
    expect(
      screen.getByText('A pending handoff does not assign or activate the successor.'),
    ).toBeDefined();
    expect(screen.getAllByText('Unknown').length).toBeGreaterThan(0);
  });
  it.each(['en', 'ko'] as const)(
    'uses canonical actors for every transfer stage in %s without inventing missing next actions',
    (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const handoff: JsonRecord = {
        id: 'transfer-one',
        from_actor: 'predecessor',
        to_actor: 'successor',
        role: 'worker',
        outgoing: {
          from_actor: 'predecessor',
          to_actor: 'successor',
          goal: 'Exact outgoing goal',
          next_action: 'Exact outgoing action',
          scope: ['src/a.ts'],
        },
        acknowledgements: [
          {
            receipt: { actor_id: 'successor', summary: 'First exact ACK', source_digest: 'one' },
            ref,
          },
          {
            receipt: {
              actor_id: 'successor',
              summary: 'Second exact ACK',
              source_digest: 'two',
              next_action: 'Explicit ACK action',
            },
            ref,
          },
        ],
        acknowledgement: {
          actor_id: 'successor',
          summary: 'Second exact ACK',
          source_digest: 'two',
        },
        acceptance: {
          actor_id: 'accepting-director',
          summary: 'Exact acceptance',
          source_digest: 'two',
        },
      };
      const view = render(
        <LanguageProvider>
          <TransferHistory handoff={handoff} />
        </LanguageProvider>,
      );
      const actorLabel = locale === 'ko' ? '담당자' : 'Actor';
      const nextLabel = locale === 'ko' ? '다음 할 일' : 'Next action';
      const stage = (text: string) => {
        const row = screen.getByText(text).closest('li');
        if (!row) throw new Error('Stage missing');
        return within(row);
      };
      expect(
        stage('Exact outgoing goal').getByText(actorLabel).nextElementSibling?.textContent,
      ).toBe('predecessor');
      expect(stage('First exact ACK').getByText(actorLabel).nextElementSibling?.textContent).toBe(
        'successor',
      );
      expect(stage('Second exact ACK').getByText(actorLabel).nextElementSibling?.textContent).toBe(
        'successor',
      );
      expect(stage('Exact acceptance').getByText(actorLabel).nextElementSibling?.textContent).toBe(
        'accepting-director',
      );
      expect(
        screen.getByText(locale === 'ko' ? '후임 담당자' : 'Successor').nextElementSibling
          ?.textContent,
      ).toBe('successor');
      expect(
        stage('Exact outgoing goal').getByText(nextLabel).nextElementSibling?.textContent,
      ).toBe('Exact outgoing action');
      expect(stage('Second exact ACK').getByText(nextLabel).nextElementSibling?.textContent).toBe(
        'Explicit ACK action',
      );
      expect(stage('First exact ACK').getByText(nextLabel).nextElementSibling?.textContent).toBe(
        locale === 'ko' ? '알 수 없음' : 'Unknown',
      );
      expect(stage('Exact acceptance').getByText(nextLabel).nextElementSibling?.textContent).toBe(
        locale === 'ko' ? '알 수 없음' : 'Unknown',
      );
      view.rerender(
        <LanguageProvider>
          <TransferHistory
            handoff={{
              outgoing: { goal: 'Missing identities' },
              acknowledgement: { summary: 'Missing ACK identity' },
            }}
          />
        </LanguageProvider>,
      );
      expect(
        stage('Missing identities').getByText(actorLabel).nextElementSibling?.textContent,
      ).toBe(locale === 'ko' ? '알 수 없음' : 'Unknown');
      expect(
        stage('Missing ACK identity').getByText(actorLabel).nextElementSibling?.textContent,
      ).toBe(locale === 'ko' ? '알 수 없음' : 'Unknown');
    },
  );
  it('shows saved staffing decision rationale and claim/current configuration without runtime claims', () => {
    render(
      <StaffingHistory
        actor={{
          model_id: 'new-model',
          reasoning_effort: 'high',
          provenance: 'inherited',
          history: [
            {
              decision: 'reuse',
              reason: 'Preserve domain context',
              predecessor_id: 'earlier-worker',
              metadata: { model_id: 'old-model' },
            },
          ],
        }}
        assignment={{ model_id: 'old-model', reasoning_effort: 'medium', provenance: 'unknown' }}
      />,
    );
    expect(screen.getByText('Preserve domain context')).toBeDefined();
    expect(screen.getByText('earlier-worker')).toBeDefined();
    expect(screen.getByText('old-model')).toBeDefined();
    expect(document.querySelector('.history-comparison, .original-record')).toBeNull();
  });
  it('localizes known review and attempt states while retaining authored Korean and unknown values', () => {
    localStorage.setItem(PREFERENCE_KEY, 'ko');
    const task = taskFixture({
      attempts: [
        {
          number: 1,
          owner: 'worker',
          status: 'released',
          scope: ['src/a.ts'],
          rounds: [
            {
              round: 1,
              review_record: {
                verdict: 'accepted',
                reviewer_role: 'leader',
                next_action: 'Original 한글 action',
              },
            },
          ],
        },
      ],
    });
    render(
      <LanguageProvider>
        <TaskHistory task={task} snapshot={snapshotFixture(task)} />
      </LanguageProvider>,
    );
    expect(screen.getByText('승인됨')).toBeDefined();
    expect(screen.getByText(/담당 해제/)).toBeDefined();
    expect(screen.getByText('디렉터')).toBeDefined();
    expect(screen.queryByText('작업 범위')).toBeNull();
    expect(screen.getByText('Original 한글 action')).toBeDefined();
  });
  it('keeps expanded operational history through a changed snapshot without comparison audits', () => {
    const task = taskFixture({
      attempts: [1, 2, 3].map((number) => ({
        number,
        scope: [`attempt-${number}.ts`],
        rounds: [],
      })),
    });
    const view = render(<TaskHistory task={task} snapshot={snapshotFixture(task)} />);
    const disclosure = screen.getByText('Attempt and review history').closest('details');
    if (!disclosure) throw new Error('Missing history disclosure');
    disclosure.open = true;
    const updated = { ...task, next_action: 'New authored action after polling' };
    view.rerender(
      <TaskHistory
        task={updated}
        snapshot={snapshotFixture(updated, { revision: 'snapshot-two' })}
      />,
    );
    expect(screen.getByText('Attempt and review history').closest('details')).toBe(disclosure);
    expect(disclosure.open).toBe(true);
    expect(screen.queryByLabelText('Previous attempt')).toBeNull();
    expect(screen.queryByText('attempt-1.ts')).toBeNull();
  });
});

describe('recorded history meaning', () => {
  it('keeps delivery append order, repeated receipts and complete extensions separate from current delivery', () => {
    const receipt = { path: 'evidence/T/attempt-1/delivery.json', sha256: 'frozen' };
    const task = parseSnapshot({
      snapshot_version: 1,
      revision: 'synthetic-history',
      program_id: 'synthetic-history',
      tasks: [
        {
          id: 'T',
          title: 'History',
          status: 'active',
          delivery: { status: 'unrecorded' },
          delivery_history: {
            status: 'recorded',
            items: [
              {
                record: { status: 'ci_pending', receipt, extension: { exact: true } },
                time_status: 'unknown',
              },
              { record: { status: 'ci_recorded_failure', receipt }, time_status: 'unknown' },
            ],
          },
        },
      ],
    }).tasks[0];
    if (!task) throw new Error('fixture missing');
    expect(deliveryHistory(task).entries.map((row) => record(row.record).status)).toEqual([
      'ci_pending',
      'ci_recorded_failure',
    ]);
    expect(record(deliveryHistory(task).entries[0]?.record).extension).toEqual({ exact: true });
    expect(record(task.delivery).status).toBe('unrecorded');
  });
  it('distinguishes absent comparison values from same and different authored values', () => {
    expect(
      historyComparison({ scope: ['a'], checks: [] }, { scope: ['b'], checks: [] }, [
        'scope',
        'checks',
        'source_digest',
      ]).map((row) => row.status),
    ).toEqual(['different', 'same', 'unknown']);
    expect(revisionMismatch('head-one', 'head-two')).toBe(true);
    expect(revisionMismatch('head-one', undefined)).toBe(false);
  });
  it('retains rejected findings and exact reviewed identity when a later attempt passes', () => {
    const previous = {
      number: 1,
      rounds: [
        {
          source_digest: 'one',
          review_record: { verdict: 'rejected', unresolved_findings: ['Still recorded'] },
        },
      ],
    };
    const current = {
      number: 2,
      rounds: [
        { source_digest: 'two', review_record: { verdict: 'accepted', unresolved_findings: [] } },
      ],
    };
    const task = { id: 'T', title: 'Task', status: 'done', attempts: [previous, current] };
    expect(latestReview(task).review.verdict).toBe('accepted');
    expect(attemptFacts(previous).unresolved_findings).toEqual(['Still recorded']);
    expect(
      historyComparison(attemptFacts(previous), attemptFacts(current), ['unresolved_findings'])[0]
        ?.status,
    ).toBe('different');
  });
});
