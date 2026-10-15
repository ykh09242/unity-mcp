import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LanguageProvider, PREFERENCE_KEY } from './i18n';
import {
  flowCollection,
  flowFixture as baseFlowFixture,
  flowReference,
  flowQueue,
  flowQueueRow,
} from './flow.fixture';
import { record, semanticSnapshotEqual, parseSnapshot } from './model';
import type { JsonRecord } from './model';
import {
  evidencePreparation,
  knownLabel,
  recordBatch,
  reviewHandoffs,
  reviewTiming,
  taskHandoff,
} from './review-throughput';
import {
  BatchSummary,
  EvidencePreparationSummary,
  HandoffInline,
  ReviewBottleneck,
  ReviewFlow,
} from './review-throughput-ui';
import { ReviewSummary } from './history-ui';
import { QueueResponsibilities } from './flow-queue-ui';
import { defaultContext } from './navigation';

// Synthetic host projection uses only a complete input set. Bounded-history cases supply
// the authoritative task field explicitly, independently of the displayed history slice.
function flowFixture(extra: JsonRecord = {}) {
  const snapshot = baseFlowFixture(extra),
    envelope = record(extra.review_handoffs),
    rows = record(envelope.rows);
  if (envelope.schema_version !== 1 || rows.total !== rows.shown || !Array.isArray(rows.items))
    return snapshot;
  const completeRows = rows.items.map(record);
  return parseSnapshot({
    ...snapshot,
    tasks: snapshot.tasks.map((task) => {
      if (Object.hasOwn(task, 'review_handoff')) return task;
      const full = completeRows.filter((row) => row.task_id === task.id);
      const latest = full.reduce<JsonRecord | null>(
        (prior, row) =>
          !prior || Number(row.recorded_sequence) > Number(prior.recorded_sequence) ? row : prior,
        null,
      );
      return { ...task, review_handoff: latest };
    }),
  });
}
const endpoint = (time: string, clock = 'retained-clock') => ({
  observed_at: time,
  clock_id: clock,
  timestamp_source: 'live_postcondition_observation',
  boundary: 'canonical_submission_observed',
  reference: { path: 'retained.json', sha256: 'c'.repeat(64) },
});
const start = endpoint('2026-10-15T01:00:00Z');
const end = endpoint('2026-10-15T01:00:00.250Z');
const interval = (extra: JsonRecord = {}): JsonRecord => ({
  interval_id: 'stable-interval',
  kind: 'submission_to_start',
  boundary_label: 'Observed review wait',
  task_id: 'A',
  attempt: 1,
  round: 1,
  handoff_id: 'handoff-A',
  request_id: null,
  capture_id: null,
  cohort_ids: ['all_retained'],
  applicability: 'current',
  canonical_phase: 'review',
  observation_phase: 'started',
  start,
  end,
  state: 'closed',
  closed_seconds: 0.25,
  age: null,
  outcome: 'unknown',
  reasons: [],
  cause: null,
  resolver_id: null,
  next_trigger: null,
  references: [],
  ...extra,
});
const metric = (extra: JsonRecord = {}): JsonRecord => ({
  kind: 'submission_to_start',
  boundary_label: 'Observed review wait',
  outcome: 'all_observed',
  relevant_total: 1,
  closed_valid: 1,
  open: 0,
  unknown: 0,
  excluded: 0,
  min_seconds: 0.25,
  max_seconds: 0.25,
  median_seconds: 0.25,
  ...extra,
});
const timing = (extra: JsonRecord = {}): JsonRecord => ({
  schema_version: 1,
  source_binding: { program_id: 'flow-test', input_digest: 'a'.repeat(64) },
  cohorts: [
    {
      id: 'all_retained',
      objective_id: null,
      membership_revision: null,
      task_total: 2,
      interval_total: 1,
      summaries: [metric()],
    },
  ],
  current: flowCollection([]),
  intervals: flowCollection([interval()]),
  coverage: {
    status: 'complete',
    relevant_total: 1,
    represented_total: 1,
    unknown_total: 0,
    open_total: 0,
    excluded_total: 0,
    reasons: [],
  },
  ...extra,
});
const handoff = (extra: JsonRecord = {}): JsonRecord => {
  const taskId = typeof extra.task_id === 'string' ? extra.task_id : 'A';
  const sequence = typeof extra.recorded_sequence === 'number' ? extra.recorded_sequence : 1;
  return {
    id: 'handoff-A:1',
    handoff_id: 'handoff-A',
    assignment_generation: 1,
    recorded_sequence: sequence,
    task_id: taskId,
    attempt: 1,
    submission_round: 1,
    currentness: 'current',
    canonical_terminal: false,
    phase: 'sent',
    next_actor_id: 'reviewer',
    next_action: 'Wait for reviewer acknowledgement',
    next_trigger: 'Reviewer acknowledgement',
    reasons: [],
    observations: [],
    subject: {
      program_id: 'flow-test',
      task_id: taskId,
      attempt: 1,
      submission_round: 1,
      receipt_sha256: 'b'.repeat(64),
      source_digest: 'd'.repeat(64),
    },
    reference: {
      ...flowReference(taskId),
      pointer: '/review_handoffs/records/' + sequence,
      record_sha256: 'c'.repeat(64),
    },
    ...extra,
  };
};
const handoffs = (rows = [handoff()]): JsonRecord => ({
  schema_version: 1,
  status: 'recorded',
  rows: flowCollection(rows),
  counts: {},
  source_binding: { revision: 'flow-one' },
});
const preparation = (extra: JsonRecord = {}): JsonRecord => ({
  schema_version: 1,
  task_id: 'A',
  operation: 'complete',
  status: 'incomplete',
  counts: { selected: 3, retained: 2, attachments_planned: 2, attachments_ready: 1, unresolved: 1 },
  affected_checks: ['acceptance'],
  next_actor_id: 'reviewer',
  next_action: 'Supply missing decisions',
  reasons: ['missing_decision'],
  ...extra,
});
const batch = (extra: JsonRecord = {}): JsonRecord => ({
  schema_version: 1,
  id: 'batch-A',
  status: 'committed_view_failed',
  preparation: { status: 'validated' },
  publication: { status: 'committed_view_failed', view_recovery_required: true, reason_code: '' },
  applicability: 'current',
  requested_operation_count: 2,
  validated_operation_count: 2,
  committed_operation_count: 2,
  next_actor_id: 'reviewer',
  next_action: 'render',
  accepted: false,
  execution_replayed: false,
  remote_verified_by_tool: false,
  operations: flowCollection([
    { id: 'submit-A', operation: 'submit', task_id: 'A', status: 'committed' },
    { id: 'note-A', operation: 'note', task_id: 'A', status: 'committed' },
  ]),
  ...extra,
});
describe('review throughput boundary and existing-surface summaries', () => {
  it('keeps no-adoption legacy quiet and distinguishes explicit malformed adoption', () => {
    const legacy = flowFixture();
    const view = render(
      <>
        <ReviewFlow snapshot={legacy} taskId="A" />
        <EvidencePreparationSummary snapshot={legacy} />
        <BatchSummary snapshot={legacy} />
      </>,
    );
    expect(view.container.textContent).toBe('');
    view.rerender(
      <ReviewFlow snapshot={flowFixture({ review_handoffs: { schema_version: 2 } })} taskId="A" />,
    );
    expect(screen.getByText('Review handoff is unavailable.')).toBeTruthy();
  });
  const incompatibleEndpoints: readonly JsonRecord[] = [
    { end: endpoint('2026-10-15T01:00:00.250Z', 'other-clock') },
    { end: endpoint('2026-10-15T00:59:59Z') },
    { closed_seconds: -1 },
    { start: null },
    { end: null },
  ];
  it.each(incompatibleEndpoints)(
    'rejects unmeasured or incompatible completed interval %j',
    (change) => {
      const snapshot = flowFixture({
        timing: timing({ intervals: flowCollection([interval(change)]) }),
      });
      expect(reviewTiming(snapshot)).toBeNull();
    },
  );
  it('retains observed zero and positive subsecond intervals without browser-time substitution', () => {
    const snapshot = flowFixture({ timing: timing() });
    expect(reviewTiming(snapshot)?.intervals[0]?.closedSeconds).toBe(0.25);
    const zero = flowFixture({
      timing: timing({ intervals: flowCollection([interval({ end: start, closed_seconds: 0 })]) }),
    });
    expect(reviewTiming(zero)?.intervals[0]?.closedSeconds).toBe(0);
    render(<ReviewFlow snapshot={snapshot} taskId="A" />);
    expect(screen.getAllByText(/0.25 sec/).length).toBeGreaterThan(0);
  });
  it('accepts compatible aware offset endpoints and refuses invented open age', () => {
    const compatible = interval({ end: endpoint('2026-10-15T10:00:00.250+09:00') });
    expect(
      reviewTiming(flowFixture({ timing: timing({ intervals: flowCollection([compatible]) }) })),
    ).not.toBeNull();
    const open = interval({
      end: null,
      state: 'open',
      closed_seconds: null,
      age: {
        as_of: '2026-10-15T01:00:05Z',
        clock_id: 'retained-clock',
        seconds: 5,
        reference: { id: 'retained-observation' },
      },
    });
    const snapshot = flowFixture({ timing: timing({ intervals: flowCollection([open]) }) });
    expect(reviewTiming(snapshot)?.intervals[0]?.age?.seconds).toBe(5);
    expect(
      reviewTiming(
        flowFixture({
          timing: timing({
            intervals: flowCollection([{ ...open, age: { ...record(open.age), reference: null } }]),
          }),
        }),
      ),
    ).toBeNull();
  });
  it('uses the full qualified cohort rather than the clipped visible interval rows', () => {
    const fullMetric = metric({
      relevant_total: 75,
      closed_valid: 60,
      open: 10,
      unknown: 5,
      min_seconds: 0.25,
      max_seconds: 80,
      median_seconds: 12,
    });
    const snapshot = flowFixture({
      timing: timing({
        intervals: flowCollection([interval()], 75),
        cohorts: [
          { id: 'all_retained', task_total: 2, interval_total: 75, summaries: [fullMetric] },
        ],
      }),
    });
    render(<ReviewBottleneck snapshot={snapshot} context={defaultContext} />);
    expect(screen.getByText(/60 \/ 75 measured/)).toBeTruthy();
    expect(screen.getByText(/12 sec/)).toBeTruthy();
    expect(reviewTiming(snapshot)?.intervalOmitted).toBe(74);
  });
  it('does not replace missing completed samples with a zero median', () => {
    const snapshot = flowFixture({
      timing: timing({
        cohorts: [
          {
            id: 'all_retained',
            task_total: 2,
            interval_total: 1,
            summaries: [
              metric({
                closed_valid: 0,
                unknown: 1,
                min_seconds: null,
                max_seconds: null,
                median_seconds: null,
              }),
            ],
          },
        ],
      }),
    });
    render(<ReviewBottleneck snapshot={snapshot} />);
    expect(screen.getByText(/No completed observations/)).toBeTruthy();
    expect(screen.queryByText(/0 sec/)).toBeNull();
  });
  it('retains a stable interval DOM node and open disclosure when the observed end arrives', () => {
    const open = flowFixture({
      timing: timing({
        intervals: flowCollection([interval({ state: 'open', end: null, closed_seconds: null })]),
      }),
    });
    const view = render(<ReviewFlow snapshot={open} taskId="A" />);
    const disclosure = view.container.querySelector('details');
    if (!disclosure) throw new Error('Missing review interval disclosure');
    disclosure.open = true;
    const row = view.container.querySelector('[data-interval-id="stable-interval"]');
    act(() =>
      view.rerender(<ReviewFlow snapshot={flowFixture({ timing: timing() })} taskId="A" />),
    );
    expect(view.container.querySelector('[data-interval-id="stable-interval"]')).toBe(row);
    expect(disclosure.open).toBe(true);
  });
  it.each(['en', 'ko'])('separates actual send from reviewer start in %s', (locale) => {
    localStorage.setItem(PREFERENCE_KEY, locale);
    const snapshot = flowFixture({ review_handoffs: handoffs() });
    const view = render(
      <LanguageProvider>
        <HandoffInline snapshot={snapshot} taskId="A" />
      </LanguageProvider>,
    );
    expect(
      screen.getByText(locale === 'ko' ? '검토 요청 보냄' : 'Review request sent'),
    ).toBeTruthy();
    expect(screen.queryByText(locale === 'ko' ? '검토 시작' : 'Review started')).toBeNull();
    expect(view.container.querySelectorAll('button')).toHaveLength(0);
    expect(view.container.textContent).toContain('reviewer');
  });
  it('rejects foreign or malformed projections without hiding ordinary work', () => {
    expect(
      reviewHandoffs(
        flowFixture({ review_handoffs: { ...handoffs(), source_binding: { revision: 'old' } } }),
      ),
    ).toBeNull();
    expect(
      reviewTiming(
        flowFixture({
          timing: timing({ source_binding: { program_id: 'other', input_digest: 'a'.repeat(64) } }),
        }),
      ),
    ).toBeNull();
    expect(
      reviewTiming(
        flowFixture({ timing: timing({ intervals: flowCollection([interval(), interval()]) }) }),
      ),
    ).toBeNull();
  });
  it('does not revive an older sent handoff after a newer uncertain send', () => {
    const snapshot = flowFixture({
      review_handoffs: handoffs([
        handoff(),
        handoff({
          id: 'handoff-A:2',
          phase: 'delivery_unknown',
          assignment_generation: 2,
          recorded_sequence: 2,
        }),
      ]),
    });
    render(<HandoffInline snapshot={snapshot} taskId="A" />);
    expect(screen.getByText('Review send outcome needs confirmation')).toBeTruthy();
    expect(screen.queryByText('Review request sent')).toBeNull();
  });
  it('keeps missing evidence decisions separate from original outcomes and new execution', () => {
    const snapshot = flowFixture({ evidence_preparation: preparation() });
    render(<EvidencePreparationSummary snapshot={snapshot} taskId="A" />);
    expect(screen.getByText('Supply the responsible review decision')).toBeTruthy();
    expect(screen.getByText(/Selected evidence: 3 · Retained attachments: 2/)).toBeTruthy();
    expect(screen.queryByText('passed')).toBeNull();
    expect(
      evidencePreparation(
        preparation({
          counts: {
            selected: 1,
            retained: 2,
            attachments_planned: 0,
            attachments_ready: 0,
            unresolved: 0,
          },
        }),
      ),
    ).toBeNull();
  });
  it('renders one committed batch recovery outcome without implying acceptance or replay', () => {
    render(<BatchSummary snapshot={flowFixture({ record_batch: batch() })} />);
    expect(screen.getByText('Batch recorded; views need recovery')).toBeTruthy();
    expect(screen.getByText(/Recover the recorded views/)).toBeTruthy();
    expect(screen.getByText('2 / 2 operations recorded')).toBeTruthy();
    fireEvent.click(screen.getByText('Batch details'));
    expect(screen.queryByText('Accepted')).toBeNull();
    expect(recordBatch(batch({ execution_replayed: true }))).toBeNull();
  });
  it('preserves authored/prototype-name fallback strings and maps only owned finite vocabulary', () => {
    for (const reason of ['constructor', '__proto__', 'toString', 'Author supplied explanation'])
      expect(knownLabel({ missing_decision: 'Supply decision' }, reason)).toBe(reason);
    expect(knownLabel({ missing_decision: 'Supply decision' }, 'missing_decision')).toBe(
      'Supply decision',
    );
  });
  it('keeps retained timing semantic identity across read time and changes only for new facts', () => {
    const before = flowFixture({ timing: timing() });
    expect(semanticSnapshotEqual(before, { ...before, generated_at: 'later-read' })).toBe(true);
    const changed = flowFixture({
      timing: timing({
        intervals: flowCollection([
          interval({ end: endpoint('2026-10-15T01:00:02Z'), closed_seconds: 2 }),
        ]),
      }),
    });
    expect(semanticSnapshotEqual(before, changed)).toBe(false);
  });

  it('preserves a positive microsecond observation rather than displaying zero', () => {
    const snapshot = flowFixture({
      timing: timing({
        intervals: flowCollection([
          interval({
            end: endpoint('2026-10-15T01:00:00.000001Z'),
            closed_seconds: 0.000001,
          }),
        ]),
      }),
    });
    render(<ReviewFlow snapshot={snapshot} taskId="A" />);
    expect(screen.getAllByText(/0.000001 sec/).length).toBeGreaterThan(0);
  });
  it('selects the latest recorded handoff subject rather than lexical hash order', () => {
    const snapshot = flowFixture({
      review_handoffs: handoffs([
        handoff({ id: 'hash-z:1', handoff_id: 'hash-z', recorded_sequence: 1, phase: 'started' }),
        handoff({
          id: 'hash-a:1',
          handoff_id: 'hash-a',
          recorded_sequence: 2,
          phase: 'delivery_failed',
        }),
      ]),
    });
    render(<HandoffInline snapshot={snapshot} taskId="A" />);
    expect(screen.getByText('Review request could not be sent')).toBeTruthy();
    expect(screen.queryByText('Review started')).toBeNull();
  });

  it('groups Team check-only gaps once per task without implying a submitted review', () => {
    const snapshot = flowFixture({
      review_queue: flowQueue(
        ['acceptance', 'format_lint', 'scoped_tests'].map((check) =>
          flowQueueRow('gap-' + check, 'A', { kind: 'required_check_gap', check_ids: [check] }),
        ),
      ),
    });
    const view = render(<QueueResponsibilities snapshot={snapshot} context={defaultContext} />);
    const summary = screen.getByText(/Required checks not yet recorded/);
    expect(summary.closest('details')?.open).toBe(false);
    expect(view.container.querySelectorAll('.team-gap-task')).toHaveLength(1);
    expect(screen.getByText('No review requests.')).toBeTruthy();
    expect(view.container.querySelectorAll('details details')).toHaveLength(0);
    expect(summary.textContent).toContain('3');
    expect(view.container.textContent).not.toContain('0 omitted');
  });
  it('keeps verification/capture intervals unsubmitted when attempt or round is absent', () => {
    const snapshot = flowFixture({
      timing: timing({
        intervals: flowCollection([
          interval({
            kind: 'command_process',
            attempt: null,
            round: null,
            handoff_id: null,
          }),
        ]),
      }),
    });
    expect(reviewTiming(snapshot)?.intervals[0]?.round).toBeNull();
    const view = render(<ReviewFlow snapshot={snapshot} taskId="A" />);
    expect(view.container.textContent).not.toContain('Round 0');
    expect(view.container.textContent).not.toContain('Round 1');
  });
  it('keeps recorded history wording distinct from observed blocked time', () => {
    const snapshot = flowFixture({
      timing: timing({
        intervals: flowCollection([
          interval({
            kind: 'recorded_history_span',
            boundary_label: 'recorded_block_to_resolution_history',
          }),
        ]),
      }),
    });
    render(<ReviewFlow snapshot={snapshot} taskId="A" />);
    expect(screen.getByText('Recorded blocker history span')).toBeTruthy();
    expect(screen.queryByText('Observed blocked span')).toBeNull();
  });

  it('uses authoritative sealed monotonic process duration rather than rounded wall timestamps', () => {
    const snapshot = flowFixture({
      timing: timing({
        intervals: flowCollection([
          interval({
            kind: 'command_process',
            boundary_label: 'sealed_process_monotonic_span',
            round: null,
            end: endpoint('2026-10-15T01:00:04Z'),
            closed_seconds: 4.25,
          }),
        ]),
      }),
    });
    expect(reviewTiming(snapshot)?.intervals[0]?.closedSeconds).toBe(4.25);
    render(<ReviewFlow snapshot={snapshot} taskId="A" />);
    expect(screen.getAllByText(/4.25 sec/).length).toBeGreaterThan(0);
  });
});

it('renders a failed-before-plan batch with no invented batch identity', () => {
  const failed = batch({
    id: null,
    preparation: { status: 'failed' },
    publication: {
      status: 'not_committed',
      view_recovery_required: false,
      reason_code: 'preparation_failed',
    },
    applicability: 'unknown',
    requested_operation_count: 0,
    validated_operation_count: 0,
    committed_operation_count: 0,
    operations: flowCollection([]),
    next_actor_id: null,
    next_action: 'prepare_batch',
  });
  render(<BatchSummary snapshot={flowFixture({ record_batch: failed })} />);
  expect(screen.getByText('Batch not committed')).toBeTruthy();
  expect(screen.getByText('Prepare the record batch')).toBeTruthy();
  expect(screen.getAllByText('Batch preparation failed').length).toBeGreaterThan(0);
  expect(screen.queryByText('Record batch is unavailable.')).toBeNull();
  expect(recordBatch(batch({ id: null }))).toBeNull();
});
it.each(['en', 'ko'])(
  'keeps withdrawn reviewer eligibility actionable without reviving ready in %s',
  (locale) => {
    localStorage.setItem(PREFERENCE_KEY, locale);
    const snapshot = flowFixture({
      review_handoffs: handoffs([
        handoff({
          phase: 'unassigned',
          currentness: 'stale',
          next_actor_id: 'director',
          reasons: ['reviewer_eligibility_changed'],
          next_action: 'Revalidate stale or unavailable inputs',
        }),
      ]),
    });
    const view = render(
      <LanguageProvider>
        <HandoffInline snapshot={snapshot} taskId="A" />
      </LanguageProvider>,
    );
    expect(
      screen.getByText(
        locale === 'ko'
          ? '검토자 자격 변경 · 책임자 확인 필요'
          : 'Reviewer eligibility changed; director action required',
      ),
    ).toBeTruthy();
    expect(view.container.textContent).toContain('director');
    expect(screen.queryByText('Review handoff prepared')).toBeNull();
  },
);

it.each(['en', 'ko'])(
  'distinguishes a submitted pending review from its assigned handoff reviewer in %s',
  (locale) => {
    localStorage.setItem(PREFERENCE_KEY, locale);
    const snapshot = flowFixture({
      review_handoffs: handoffs([
        handoff({
          phase: 'ready',
          reviewer_id: 'reviewer',
          next_actor_id: 'director',
          next_action: 'Dispatch exact packet through actual native transport',
          next_trigger: 'Dispatch exact packet through actual native transport',
        }),
      ]),
      tasks: [
        {
          id: 'A',
          title: 'Pending review',
          status: 'review',
          readiness: { submission: { status: 'observed' } },
          attempts: [
            {
              number: 1,
              owner: 'worker',
              rounds: [{ round: 1, receipt_record: {}, review_record: null }],
            },
          ],
        },
      ],
    });
    const task = snapshot.tasks[0];
    if (!task) throw new Error('Missing pending review task');
    const view = render(
      <LanguageProvider>
        <ReviewSummary snapshot={snapshot} task={task} />
      </LanguageProvider>,
    );
    expect(screen.getByText(locale === 'ko' ? '제출 확인됨' : 'Submission observed')).toBeTruthy();
    expect(view.container.querySelector('.history-meta')?.textContent).not.toContain(
      locale === 'ko' ? '검토자 미기록' : 'Reviewer not recorded',
    );
    expect(view.container.querySelector('.review-phase')?.textContent).toContain(
      locale === 'ko' ? '검토 담당' : 'Assigned reviewer',
    );
    expect(
      screen.getByText(
        new RegExp(locale === 'ko' ? '검토 담당자에게 전달하세요' : 'Send to the reviewer'),
      ),
    ).toBeTruthy();
  },
);

it('preserves actual unknown validation counts and unassessed operations in failed preparation', () => {
  const failed = batch({
    id: null,
    preparation: { status: 'failed' },
    publication: {
      status: 'not_committed',
      view_recovery_required: false,
      reason_code: 'preparation_failed',
    },
    applicability: 'unknown',
    requested_operation_count: 2,
    validated_operation_count: null,
    committed_operation_count: 0,
    next_action: 'prepare_batch',
    operations: flowCollection([
      { id: 'one', operation: 'note', task_id: 'A', status: 'not_assessed' },
      { id: 'two', operation: 'note', task_id: 'A', status: 'not_assessed' },
    ]),
  });
  expect(recordBatch(failed)?.validated).toBeNull();
  render(<BatchSummary snapshot={flowFixture({ record_batch: failed })} />);
  expect(screen.getByText('0 / 2 operations recorded')).toBeTruthy();
  expect(screen.getByText(/Validation count not observed/)).toBeTruthy();
  expect(screen.queryByText('0 operations validated')).toBeNull();
});
it('does not manufacture batch scope or commitment after unavailable request history', () => {
  const unknown = batch({
    id: null,
    preparation: { status: 'failed' },
    publication: {
      status: 'unknown',
      view_recovery_required: false,
      reason_code: 'preparation_failed',
    },
    applicability: 'unknown',
    requested_operation_count: null,
    validated_operation_count: null,
    committed_operation_count: null,
    operations: { items: [], total: null, shown: 0, omitted: null },
  });
  expect(recordBatch(unknown)?.requested).toBeNull();
  render(<BatchSummary snapshot={flowFixture({ record_batch: unknown })} />);
  expect(screen.getByText('Operation counts unavailable')).toBeTruthy();
  expect(screen.getByText('Operation list scope unavailable')).toBeTruthy();
  expect(screen.queryByText('0 / 0 operations recorded')).toBeNull();
});
it('keeps an earlier committed-view failure as history after current recovery', () => {
  render(
    <BatchSummary
      snapshot={flowFixture({
        record_batch: batch({
          recorded_publication: { status: 'committed_view_failed' },
          publication: {
            status: 'already_committed',
            view_recovery_required: false,
            reason_code: 'exact_commit',
          },
        }),
      })}
    />,
  );
  expect(screen.getByText('Batch already recorded')).toBeTruthy();
  expect(screen.getByText(/Recorded publication outcome/).textContent).toContain(
    'Batch recorded; views need recovery',
  );
});
function suppliedSnapshot(): string {
  const runtime: unknown = Reflect.get(globalThis, 'process');
  if (typeof runtime !== 'object' || runtime === null || !('env' in runtime)) return '';
  const environment = runtime.env;
  if (
    typeof environment !== 'object' ||
    environment === null ||
    !('WEB_PRODUCER_SNAPSHOT' in environment)
  )
    return '';
  return typeof environment.WEB_PRODUCER_SNAPSHOT === 'string'
    ? environment.WEB_PRODUCER_SNAPSHOT
    : '';
}
const suppliedProducerSnapshot = suppliedSnapshot();
if (suppliedProducerSnapshot) {
  it('consumes a supplied producer snapshot without guessed projection fields', () => {
    // Optional transport compatibility check. The caller supplies actual producer output;
    // the ordinary task envelope is a test host, not native execution/acceptance proof.
    const supplied: unknown = JSON.parse(suppliedProducerSnapshot);
    const snapshot = parseSnapshot(supplied);
    if (snapshot.review_handoffs !== undefined) expect(reviewHandoffs(snapshot)).not.toBeNull();
    for (const task of snapshot.tasks)
      if (task.review_handoff !== undefined && task.review_handoff !== null)
        expect(taskHandoff(snapshot, task.id)).not.toBeNull();
    if (snapshot.timing !== undefined) expect(reviewTiming(snapshot)).not.toBeNull();
    if (snapshot.evidence_preparation !== undefined)
      expect(evidencePreparation(snapshot.evidence_preparation)).not.toBeNull();
    if (snapshot.record_batch !== undefined)
      expect(recordBatch(snapshot.record_batch)).not.toBeNull();
    render(
      <>
        <ReviewFlow snapshot={snapshot} taskId={snapshot.tasks[0]?.id || ''} />
        <ReviewBottleneck snapshot={snapshot} />
        <EvidencePreparationSummary snapshot={snapshot} />
        <BatchSummary snapshot={snapshot} />
      </>,
    );
  });
}

it('keeps positive Team omissions explicit without repeating zero or same-total counts', () => {
  const snapshot = flowFixture({
    review_queue: flowQueue(
      [flowQueueRow('gap-one', 'A', { kind: 'required_check_gap', check_ids: ['acceptance'] })],
      8,
    ),
  });
  const view = render(<QueueResponsibilities snapshot={snapshot} context={defaultContext} />);
  expect(screen.getByText('7 obligations omitted; this view is partial.')).toBeTruthy();
  expect(view.container.textContent).not.toContain('0 omitted');
  expect(view.container.textContent).not.toContain('0 requests shown');
});

it('does not claim an observed terminal timestamp is missing when the start endpoint is absent', () => {
  const snapshot = flowFixture({
    timing: timing({
      current: flowCollection([
        {
          id: 'current-A',
          queue_row_id: 'queue-A',
          task_id: 'A',
          attempt: 1,
          round: 1,
          handoff_id: 'handoff-A',
          canonical_phase: 'done',
          observation_phase: 'terminal',
          applicability: 'current',
          cause: 'terminal',
          resolver_id: null,
          next_trigger: null,
          waiting_since: null,
          age: null,
          interval_ids: ['stable-interval'],
          reasons: [],
        },
      ]),
      intervals: flowCollection([
        interval({
          kind: 'submission_to_terminal',
          state: 'unknown',
          start: null,
          closed_seconds: null,
          reasons: ['missing_endpoint'],
        }),
      ]),
    }),
  });
  render(<ReviewFlow snapshot={snapshot} taskId="A" />);
  expect(screen.getByText('Review ended; review wait was not measured')).toBeTruthy();
  expect(screen.queryByText('Review ended; terminal time was not observed')).toBeNull();
});

it('does not repeat the assigned reviewer as the adjacent next actor', () => {
  const snapshot = flowFixture({
    review_handoffs: handoffs([
      handoff({
        reviewer_id: 'reviewer-identity',
        next_actor_id: 'reviewer-identity',
        phase: 'acknowledged',
        next_action: 'Reviewer starts the exact current packet review',
      }),
    ]),
  });
  const view = render(<HandoffInline snapshot={snapshot} taskId="A" />);
  expect(view.container.querySelector('.review-phase')?.textContent).toContain('reviewer-identity');
  expect(view.container.querySelector('.flow-next-action')?.textContent).not.toContain(
    'reviewer-identity',
  );
  expect(view.container.querySelector('.flow-next-action')?.textContent).toContain(
    'Reviewer starts the review',
  );
});
it('keeps the short elapsed label and value in a bounded wrapping unit', () => {
  localStorage.setItem(PREFERENCE_KEY, 'ko');
  const snapshot = flowFixture({
    timing: timing({
      current: flowCollection([
        {
          id: 'current-A',
          queue_row_id: 'queue-A',
          task_id: 'A',
          attempt: 1,
          round: 1,
          handoff_id: 'handoff-A',
          canonical_phase: 'review',
          observation_phase: 'ready',
          applicability: 'current',
          cause: 'dispatch_pending',
          resolver_id: 'director',
          next_trigger: null,
          waiting_since: start,
          age: null,
          interval_ids: ['stable-interval'],
          reasons: [],
        },
      ]),
      intervals: flowCollection([interval({ state: 'open', end: null, closed_seconds: null })]),
    }),
  });
  const view = render(
    <LanguageProvider>
      <ReviewFlow snapshot={snapshot} taskId="A" />
    </LanguageProvider>,
  );
  expect(view.container.querySelector('.review-timing-line .review-timing-unit')?.textContent).toBe(
    '경과 시간 미관찰',
  );
});

it('uses the authoritative task handoff when bounded history omits the latest subject', () => {
  const older = Array.from({ length: 50 }, (_, sequence) =>
    handoff({
      id: 'older-' + sequence + ':1',
      handoff_id: 'older-' + sequence,
      recorded_sequence: sequence,
      phase: 'sent',
    }),
  );
  const latest = handoff({
    id: 'latest:2',
    handoff_id: 'latest',
    assignment_generation: 2,
    recorded_sequence: 70,
    phase: 'delivery_unknown',
    next_actor_id: 'director',
  });
  const snapshot = flowFixture({
    tasks: [{ id: 'A', title: 'Bounded handoff task', status: 'review', review_handoff: latest }],
    review_handoffs: { ...handoffs(older), rows: flowCollection(older, 51) },
  });
  expect(taskHandoff(snapshot, 'A')?.id).toBe('latest:2');
  render(<HandoffInline snapshot={snapshot} taskId="A" />);
  expect(screen.getByText('Review send outcome needs confirmation')).toBeTruthy();
  expect(screen.queryByText('Review request sent')).toBeNull();
});

const malformedTaskAuthorities: readonly JsonRecord[] = [
  { phase: 'invented' },
  { task_id: 'B' },
  { recorded_sequence: -1 },
  {
    reference: {
      ...flowReference('A'),
      pointer: '/review_handoffs/records/1',
      record_sha256: 'c'.repeat(64),
      revision: 'old-revision',
    },
  },
  { reference: { ...flowReference('A'), pointer: '/tasks/A', record_sha256: 'c'.repeat(64) } },
  {
    subject: {
      program_id: 'foreign-program',
      task_id: 'A',
      attempt: 1,
      submission_round: 1,
      receipt_sha256: 'b'.repeat(64),
      source_digest: 'd'.repeat(64),
    },
  },
];
it.each(malformedTaskAuthorities)(
  'does not fall back to shown history when task authority is malformed %j',
  (change) => {
    const snapshot = flowFixture({
      tasks: [
        {
          id: 'A',
          title: 'Malformed authority',
          status: 'review',
          review_handoff: handoff(change),
        },
      ],
      review_handoffs: handoffs([handoff()]),
    });
    expect(taskHandoff(snapshot, 'A')).toBeNull();
    render(<HandoffInline snapshot={snapshot} taskId="A" />);
    expect(screen.getByText('Current review handoff is unavailable.')).toBeTruthy();
    expect(screen.queryByText('Review request sent')).toBeNull();
  },
);
it('does not infer current authority from even a complete history when the task field is absent', () => {
  const snapshot = baseFlowFixture({ review_handoffs: handoffs([handoff()]) });
  expect(taskHandoff(snapshot, 'A')).toBeNull();
  render(<HandoffInline snapshot={snapshot} taskId="A" />);
  expect(screen.getByText('Current review handoff is unavailable.')).toBeTruthy();
  expect(screen.queryByText('Review request sent')).toBeNull();
});
it('respects explicit no-current-handoff without reviving retained history', () => {
  const snapshot = flowFixture({
    tasks: [{ id: 'A', title: 'No current handoff', status: 'review', review_handoff: null }],
    review_handoffs: handoffs([handoff()]),
  });
  expect(taskHandoff(snapshot, 'A')).toBeNull();
  const view = render(<HandoffInline snapshot={snapshot} taskId="A" />);
  expect(view.container.textContent).toBe('');
});

it.each(['en', 'ko'])(
  'retains actual-shaped unknown dispatch endpoints beside measured review spans in %s',
  (locale) => {
    localStorage.setItem(PREFERENCE_KEY, locale);
    const submitted = endpoint('2026-10-15T02:13:42.721903+00:00', 'local_system_utc');
    const acknowledged = endpoint('2026-10-15T02:14:06+00:00', 'local_system_utc');
    const started = endpoint('2026-10-15T02:14:15+00:00', 'local_system_utc');
    const dispatch = {
      ...submitted,
      observed_at: null,
      clock_id: null,
      boundary: 'dispatch_sent',
      timestamp_source: 'retained_observation',
    };
    const missing = ['missing_observed_at', 'missing_clock'];
    // Actual normal producer retains unknown endpoint metadata; it does not fabricate a time.
    const rows = [
      interval({ interval_id: 'wait', start: submitted, end: started, closed_seconds: 32.278097 }),
      interval({
        interval_id: 'ack',
        kind: 'ack_to_start',
        start: acknowledged,
        end: started,
        closed_seconds: 9,
      }),
      interval({
        interval_id: 'dispatch-ack',
        kind: 'dispatch_to_ack',
        start: dispatch,
        end: acknowledged,
        state: 'unknown',
        closed_seconds: null,
        reasons: missing,
      }),
      interval({
        interval_id: 'submit-dispatch',
        kind: 'submission_to_dispatch',
        start: submitted,
        end: dispatch,
        state: 'unknown',
        closed_seconds: null,
        reasons: missing,
      }),
      interval({
        interval_id: 'verification',
        kind: 'verification_wait',
        round: null,
        start: { ...dispatch, boundary: 'request' },
        end: acknowledged,
        state: 'unknown',
        closed_seconds: null,
        reasons: missing,
      }),
      interval({
        interval_id: 'running',
        kind: 'start_to_terminal',
        start: started,
        end: null,
        state: 'open',
        closed_seconds: null,
      }),
    ];
    const snapshot = flowFixture({
      timing: timing({
        intervals: flowCollection(rows),
        cohorts: [
          {
            id: 'all_retained',
            task_total: 2,
            interval_total: 6,
            summaries: [
              metric({ min_seconds: 32.278097, max_seconds: 32.278097, median_seconds: 32.278097 }),
            ],
          },
        ],
        coverage: {
          status: 'complete',
          relevant_total: 6,
          represented_total: 6,
          unknown_total: 3,
          open_total: 1,
          excluded_total: 0,
          reasons: ['unobserved_or_incompatible_intervals'],
        },
      }),
    });
    const parsed = reviewTiming(snapshot);
    expect(parsed).not.toBeNull();
    expect(parsed?.intervals.map((row) => row.closedSeconds)).toEqual([
      32.278097,
      9,
      null,
      null,
      null,
      null,
    ]);
    expect(parsed?.intervals[2]?.start).toBeNull();
    expect(parsed?.intervals[2]?.reasons).toEqual(missing);
    expect(parsed?.intervals[5]?.state).toBe('open');
    expect(parsed?.intervals[5]?.age).toBeNull();
    render(
      <LanguageProvider>
        <ReviewBottleneck snapshot={snapshot} context={defaultContext} />
      </LanguageProvider>,
    );
    expect(screen.queryByText(/32[.,]28/)).not.toBeNull();
    expect(screen.queryByText('Review timing is unavailable.')).toBeNull();
    expect(screen.queryByText('검토 시간 기록을 확인할 수 없어요.')).toBeNull();
  },
);

const retainedMissingEndpoint = { ...start, observed_at: null, clock_id: null };
it.each(['start', 'end'])(
  'retains an unknown %s endpoint when the opposite endpoint is absent without missing_clock',
  (side) => {
    // Given the producer's missing-opposite shape beside measured review spans.
    const rows = [
      interval({
        interval_id: 'measured-wait',
        end: endpoint('2026-10-15T01:00:32.278097Z'),
        closed_seconds: 32.278097,
      }),
      interval({
        interval_id: 'measured-start',
        kind: 'ack_to_start',
        end: endpoint('2026-10-15T01:00:09Z'),
        closed_seconds: 9,
      }),
      interval({
        interval_id: 'unmeasured-terminal',
        kind: 'start_to_terminal',
        start: side === 'start' ? retainedMissingEndpoint : null,
        end: side === 'end' ? retainedMissingEndpoint : null,
        state: 'unknown',
        closed_seconds: null,
        reasons: ['missing_endpoint', 'missing_observed_at'],
      }),
    ];
    // When decoding the coherent timing projection.
    const parsed = reviewTiming(
      flowFixture({ timing: timing({ intervals: flowCollection(rows) }) }),
    );
    // Then measured spans survive and absent measurements remain unknown.
    expect(parsed?.intervals.map((row) => row.closedSeconds)).toEqual([32.278097, 9, null]);
    expect(parsed?.intervals[2]?.start).toBeNull();
    expect(parsed?.intervals[2]?.end).toBeNull();
    expect(parsed?.intervals[2]?.state).toBe('unknown');
  },
);
const invalidMissingOppositeIntervals: readonly JsonRecord[] = [
  { start },
  { start: {} },
  { reasons: ['missing_observed_at'] },
  { reasons: ['missing_endpoint'] },
  { end: { ...retainedMissingEndpoint, observed_at: 'invalid-time' } },
  { end: { ...retainedMissingEndpoint, clock_id: 7 } },
  { end: { ...retainedMissingEndpoint, reference: {} } },
  { end: { ...retainedMissingEndpoint, timestamp_source: '' } },
  { end: { ...retainedMissingEndpoint, boundary: '' } },
  { state: 'closed' },
  { state: 'open' },
  { closed_seconds: 0 },
  { age: { as_of: '2026-10-15T01:00:05Z', clock_id: 'retained-clock', seconds: 5 } },
];
it.each(invalidMissingOppositeIntervals)(
  'rejects malformed missing-opposite interval %j',
  (change) => {
    // Given an unknown interval whose missing clock has no separate reason.
    const candidate = interval({
      start: null,
      end: retainedMissingEndpoint,
      state: 'unknown',
      closed_seconds: null,
      reasons: ['missing_endpoint', 'missing_observed_at'],
      ...change,
    });
    // When decoding supplied malformed metadata or an incompatible state.
    const parsed = reviewTiming(
      flowFixture({ timing: timing({ intervals: flowCollection([candidate]) }) }),
    );
    // Then the projection remains unavailable.
    expect(parsed).toBeNull();
  },
);
it('rejects an omitted opposite endpoint rather than interpreting it as explicit null', () => {
  // Given an omitted opposite endpoint with the producer reasons.
  const { start: _start, ...candidate } = interval({
    end: retainedMissingEndpoint,
    state: 'unknown',
    closed_seconds: null,
    reasons: ['missing_endpoint', 'missing_observed_at'],
  });
  // When decoding the incomplete shape.
  const parsed = reviewTiming(
    flowFixture({ timing: timing({ intervals: flowCollection([candidate]) }) }),
  );
  // Then missing properties do not acquire null semantics.
  expect(parsed).toBeNull();
});
it.each(['en', 'ko'])('separates selected origins from retained copies in %s', (locale) => {
  // Given one origin retained as two copies, with stale applicability preserved.
  localStorage.setItem(PREFERENCE_KEY, locale);
  const evidence = preparation({
    kind: 'retained_evidence',
    status: 'stale',
    counts: {
      selected: 1,
      retained: 2,
      attachments_planned: 2,
      attachments_ready: 2,
      unresolved: 0,
    },
  });
  // When rendering the evidence summary.
  const view = render(
    <LanguageProvider>
      <EvidencePreparationSummary snapshot={flowFixture({ evidence_preparation: evidence })} />
    </LanguageProvider>,
  );
  // Then both units are independent and currentness is not promoted.
  expect(evidencePreparation(evidence)?.status).toBe('stale');
  expect(
    screen.getByText(
      locale === 'ko'
        ? '선택한 근거 1개 · 보관한 파일 2개'
        : 'Selected evidence: 1 · Retained attachments: 2',
    ),
  ).toBeTruthy();
  expect(view.container.textContent).not.toContain('2 / 1');
  expect(
    screen.getByText(
      locale === 'ko' ? '보존된 근거 확인 필요' : 'Retained evidence needs attention',
    ),
  ).toBeTruthy();
});
const invalidEvidenceCounts: readonly JsonRecord[] = [
  { retained: 3 },
  { attachments_ready: 3 },
  ...['selected', 'retained', 'attachments_planned', 'attachments_ready', 'unresolved'].flatMap(
    (key) =>
      [-1, 0.5, Infinity, NaN, Number.MAX_SAFE_INTEGER + 1].map((value) => ({ [key]: value })),
  ),
];
it.each(invalidEvidenceCounts)('rejects invalid evidence copy counts %j', (change) => {
  // Given counts outside finite nonnegative integers or the planned copy bound.
  const evidence = preparation({
    counts: {
      selected: 1,
      retained: 2,
      attachments_planned: 2,
      attachments_ready: 2,
      unresolved: 0,
      ...change,
    },
  });
  // When decoding evidence preparation.
  const parsed = evidencePreparation(evidence);
  // Then invalid counts remain unavailable.
  expect(parsed).toBeNull();
});
const invalidUnmeasuredIntervals: readonly JsonRecord[] = [
  { start: { ...retainedMissingEndpoint, observed_at: 'not-a-time' } },
  { start: { ...retainedMissingEndpoint, clock_id: 7 } },
  { start: { ...retainedMissingEndpoint, reference: {} } },
  { start: { ...retainedMissingEndpoint, timestamp_source: '' } },
  { start: { ...retainedMissingEndpoint, boundary: '' } },
  { reasons: ['missing_observed_at'] },
  { reasons: ['missing_clock'] },
  { state: 'closed', closed_seconds: 0.25 },
  { state: 'open' },
  { closed_seconds: 0 },
];
it.each(invalidUnmeasuredIntervals)(
  'rejects malformed or measured incomplete endpoint %j',
  (change) => {
    const snapshot = flowFixture({
      timing: timing({
        intervals: flowCollection([
          interval({
            state: 'unknown',
            start: retainedMissingEndpoint,
            closed_seconds: null,
            reasons: ['missing_observed_at', 'missing_clock'],
            ...change,
          }),
        ]),
      }),
    });
    expect(reviewTiming(snapshot)).toBeNull();
  },
);
const partiallyObservedEndpoints: readonly JsonRecord[] = [
  { start: { ...start, observed_at: null }, reasons: ['missing_observed_at'] },
  { start: { ...start, clock_id: null }, reasons: ['missing_clock'] },
];
it.each(partiallyObservedEndpoints)(
  'retains only explicitly unknown partial endpoint %j',
  (change) => {
    const snapshot = flowFixture({
      timing: timing({
        intervals: flowCollection([
          interval({
            state: 'unknown',
            closed_seconds: null,
            ...change,
          }),
        ]),
      }),
    });
    const parsed = reviewTiming(snapshot);
    expect(parsed?.intervals[0]?.state).toBe('unknown');
    expect(parsed?.intervals[0]?.start).toBeNull();
    expect(parsed?.intervals[0]?.closedSeconds).toBeNull();
  },
);
