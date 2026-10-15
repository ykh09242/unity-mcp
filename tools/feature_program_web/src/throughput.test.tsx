import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { parseSnapshot, record } from './model';
import type { JsonRecord, Snapshot } from './model';
import { defaultContext, navigationHash, parseNavigation, selectWorkTasks } from './navigation';
import { observedWait, readinessFactors, verificationContext } from './throughput';
import { providerContext, providerStageIdentity } from './throughput';
import { deliveredExecution, requestedExecution } from './provider-delivery.fixture';
import { presentSnapshot } from './presentation';
import { presentationDigest } from './presentation-digest';
import { SnapshotResponse } from './useSnapshot';
import { PREFERENCE_KEY } from './i18n';

const reference = (taskId = 'A') => ({
  path: 'backlog.json',
  revision: 'one',
  pointer: '/verification',
  task_id: taskId,
});
const collection = (items: readonly JsonRecord[] = [], total = items.length) => ({
  total,
  shown: items.length,
  omitted: total - items.length,
  items,
  reference: reference(),
});
const waiting = {
  status: 'observed',
  started_at: '2026-10-14T09:00:00Z',
  ended_at: null,
  as_of: '2026-10-14T09:02:00Z',
  clock_id: 'local-observation',
  seconds: 120,
};
const lane = (id: string, kind: string, changes: JsonRecord = {}) => ({
  request_id: id,
  request_version: 1,
  lane: kind,
  verifier_id: 'reviewer',
  resolver_id: null,
  next_trigger: 'Wait for the rendered QA result',
  phase: 'running',
  result: 'pending',
  applicability: 'current',
  reasons: [],
  context: { attempt: 1, submission: { round: 1 }, source_digest: 'source-one' },
  waiting,
  recheck: { status: 'not_assessed', reasons: [] },
  blocker: null,
  ...changes,
});
function fixture(): Snapshot {
  return parseSnapshot({
    snapshot_version: 1,
    program_id: 'throughput-test',
    revision: 'one',
    generated_at: '2026-10-14T09:02:00Z',
    integrity: { status: 'fresh', warnings: [] },
    objective: { task_ids: ['A', 'B'] },
    actors: [{ id: 'reviewer', role: 'reviewer' }],
    brief: { brief_version: 1 },
    verification: { schema_version: 1, status: 'recorded' },
    execution_priorities: { A: 5, B: 1 },
    tasks: [
      {
        id: 'A',
        title: 'Render verification states',
        status: 'review',
        owner: 'implementer',
        phase: 1,
        next_action: 'Inspect the concurrent results',
        source: { text: 'Keep each independent result visible.' },
        readiness: {
          reference: reference(),
          canonical_candidate: false,
          unmet_dependencies: collection(),
          collisions: collection(),
          submission: { status: 'observed' },
        },
        verification: {
          task_id: 'A',
          reference: reference(),
          lanes: collection([lane('review-1', 'code_review'), lane('qa-1', 'qa')]),
          findings: collection(),
        },
      },
      {
        id: 'B',
        title: 'Available next work',
        status: 'planned',
        phase: 1,
        readiness: {
          reference: reference('B'),
          canonical_candidate: true,
          unmet_dependencies: { ...collection(), reference: reference('B') },
          collisions: { ...collection(), reference: reference('B') },
        },
      },
    ],
  });
}

describe('recorded throughput context', () => {
  it('rechecks projection envelopes and readiness on a same-revision replacement snapshot', () => {
    const valid = fixture();
    const task = valid.tasks[0]!;
    expect(verificationContext(valid, task)?.lanes.items).toHaveLength(2);
    const unavailable = parseSnapshot({
      ...valid,
      verification: { schema_version: 1, status: 'unavailable' },
    });
    expect(unavailable.revision).toBe(valid.revision);
    expect(verificationContext(unavailable, task)).toBeNull();
    expect(verificationContext(valid, task)?.lanes.items).toHaveLength(2);
    const availableTask = valid.tasks[1]!;
    expect(readinessFactors(valid, availableTask).available).toBe(true);
    const changedTask = {
      ...availableTask,
      readiness: {
        ...record(availableTask.readiness),
        reference: { ...reference('B'), revision: 'stale' },
      },
    };
    const changed = parseSnapshot({ ...valid, tasks: [task, changedTask] });
    expect(readinessFactors(changed, changed.tasks[1]!).available).toBe(false);
    expect(readinessFactors(valid, availableTask).available).toBe(true);
  });
  it('maps only exact source-bound verification narratives into Korean session content', () => {
    const snapshot = fixture(),
      task = snapshot.tasks[0]!;
    const trigger = 'Wait for the rendered QA result';
    const projectedLane = {
      ...lane('review-1', 'code_review'),
      request_reference: { ...reference(), pointer: '/verification/records/0' },
    };
    const translated = {
      ...snapshot,
      communication_language: 'ko',
      tasks: [
        {
          ...task,
          verification: { ...record(task.verification), lanes: collection([projectedLane]) },
        },
        snapshot.tasks[1]!,
      ],
      presentation: {
        schema_version: 1,
        revision: 'one',
        locale: 'ko',
        entries: {
          trigger: {
            status: 'current',
            mode: 'translated',
            text: '화면 검증 결과를 기다려요',
            target: {
              kind: 'verification',
              entity_id: 'review-1',
              field: 'next_trigger',
              record_pointer: '/verification/records/0/input/next_trigger',
            },
            origin: {
              path: 'backlog.json',
              revision: 'one',
              pointer: '/verification/records/0/input/next_trigger',
            },
            binding: {
              program_id: snapshot.program_id,
              source_value_sha256: presentationDigest(trigger),
              context: { task_id: 'A' },
            },
          },
        },
      },
    };
    const view = presentSnapshot(translated);
    expect(verificationContext(view, view.tasks[0]!)?.lanes.items[0]?.next_trigger).toBe(
      '화면 검증 결과를 기다려요',
    );
    const wrong = {
      ...translated,
      presentation: {
        ...translated.presentation,
        entries: {
          trigger: {
            ...translated.presentation.entries.trigger,
            target: {
              ...translated.presentation.entries.trigger.target,
              entity_id: 'another-request',
            },
          },
        },
      },
    };
    const wrongView = presentSnapshot(wrong);
    expect(verificationContext(wrongView, wrongView.tasks[0]!)?.lanes.items[0]?.next_trigger).toBe(
      trigger,
    );
  });
  it('validates bound provider stages and exposes optional skips separately from PR and beta results', async () => {
    const snapshot = fixture(),
      task = snapshot.tasks[0]!;
    const stage = {
      id: 'pr-web',
      kind: 'pr_ci',
      status: 'passed',
      current: true,
      reported_outcome: 'success',
      source: { head_sha: 'head', base_sha: 'base', tested_sha: 'merge' },
      checks: [
        { id: 'Web', status: 'passed', mode: 'required', applicability: 'current' },
        {
          id: 'Optional native',
          status: 'policy_skipped',
          mode: 'policy_skip',
          applicability: 'current',
        },
      ],
      checks_total: 2,
      checks_shown: 2,
      checks_omitted: 0,
      history: [
        {
          run_id: 7,
          run_attempt: 1,
          status: 'completed',
          reported_outcome: 'failure',
          head_sha: 'head',
          checkout_sha: 'merge',
          captured_at: '2026-10-14T09:00:00Z',
          job_ids: [101],
          jobs_total: 1,
          jobs_shown: 1,
          jobs_omitted: 0,
        },
        {
          run_id: 7,
          run_attempt: 2,
          status: 'completed',
          reported_outcome: 'success',
          head_sha: 'head',
          checkout_sha: 'merge',
          captured_at: '2026-10-14T09:01:00Z',
          job_ids: [102],
          jobs_total: 1,
          jobs_shown: 1,
          jobs_omitted: 0,
        },
      ],
      history_total: 2,
      history_shown: 2,
      history_omitted: 0,
      timing: {
        started_at: '2026-10-14T09:00:00Z',
        completed_at: '2026-10-14T09:01:00Z',
        duration_seconds: 60,
      },
    };
    const provider = {
      task_id: 'A',
      reference: reference(),
      status: 'pending',
      remote_verified_by_tool: false,
      stages: [
        stage,
        {
          ...stage,
          id: 'beta-web',
          kind: 'beta_ci',
          status: 'pending',
          reported_outcome: 'in_progress',
        },
      ],
      total: 2,
      shown: 2,
      omitted: 0,
    };
    const enriched = { ...task, provider_execution: provider };
    expect(providerContext(snapshot, enriched)?.stages).toHaveLength(2);
    expect(
      providerContext(snapshot, {
        ...enriched,
        provider_execution: { ...provider, reference: { ...reference(), revision: 'old' } },
      }),
    ).toBeNull();
    expect(
      providerContext(snapshot, { ...enriched, provider_execution: { ...provider, total: 3 } }),
    ).toBeNull();
    window.history.replaceState(null, '', '#task/A');
    render(<App loader={async () => ({ ...snapshot, tasks: [enriched, snapshot.tasks[1]!] })} />);
    await screen.findByRole('heading', { name: 'PR and beta execution' });
    expect(screen.getByText('PR CI')).toBeTruthy();
    expect(screen.getByText('Beta CI')).toBeTruthy();
    expect(screen.getAllByText('policy skipped')).toHaveLength(4);
    expect(screen.getAllByText('Recorded execution: 60 sec')).toHaveLength(2);
    expect(screen.getAllByText('failure')).toHaveLength(2);
    expect(screen.getAllByText('success')).toHaveLength(2);
  });
  it('omits never-recorded optional sections but keeps adopted incomplete diagnostics', async () => {
    const snapshot = fixture(),
      task = snapshot.tasks[1]!;
    window.history.replaceState(null, '', '#task/B');
    const empty = {
      ...task,
      provider_execution: {
        task_id: 'B',
        reference: reference('B'),
        status: 'not_assessed',
        adopted: false,
        stages: [],
        total: 0,
        shown: 0,
        omitted: 0,
      },
    };
    const view = render(
      <App loader={async () => ({ ...snapshot, tasks: [snapshot.tasks[0]!, empty] })} />,
    );
    await screen.findByRole('heading', { name: 'Available next work' });
    expect(screen.queryByRole('heading', { name: 'Verification lanes' })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'PR and beta execution' })).toBeNull();
    view.unmount();
    render(
      <App
        loader={async () => ({
          ...snapshot,
          tasks: [
            snapshot.tasks[0]!,
            { ...empty, provider_execution: { ...empty.provider_execution, adopted: true } },
          ],
        })}
      />,
    );
    await screen.findByRole('heading', { name: 'PR and beta execution' });
    expect(screen.getByText('Awaiting a current provider observation')).toBeTruthy();
  });
  it.each(['en', 'ko'])(
    'deduplicates repeated provider check meaning in %s while retaining distinct semantics',
    async (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      window.history.replaceState(null, '', '#task/A');
      const snapshot = fixture(),
        task = snapshot.tasks[0]!;
      const checks = [
        {
          id: 'optional-docs',
          status: 'policy_skipped',
          mode: 'policy_skip',
          applicability: 'policy_skipped',
        },
        { id: 'required-check', status: 'pending', mode: 'required', applicability: 'stale' },
        { id: 'advisory-check', status: 'failed', mode: 'advisory', applicability: 'unavailable' },
        {
          id: 'inapplicable-check',
          status: 'not_applicable',
          mode: 'not_applicable',
          applicability: 'not_applicable',
        },
      ];
      const provider = {
        task_id: 'A',
        reference: reference(),
        status: 'pending',
        stages: [
          {
            id: 'pr-web',
            kind: 'pr_ci',
            status: 'pending',
            current: true,
            checks,
            checks_total: checks.length,
            checks_shown: checks.length,
            checks_omitted: 0,
            history: [],
            history_total: 0,
            history_shown: 0,
            history_omitted: 0,
          },
        ],
        total: 1,
        shown: 1,
        omitted: 0,
      };
      const view = render(
        <App
          loader={async () => ({
            ...snapshot,
            tasks: [{ ...task, provider_execution: provider }, snapshot.tasks[1]!],
          })}
        />,
      );
      await screen.findByRole('heading', {
        name: locale === 'ko' ? 'PR 및 베타 실행' : 'PR and beta execution',
      });
      const rows = Array.from(view.container.querySelectorAll('.provider-checks > li'));
      expect(rows).toHaveLength(4);
      const labels = rows.map((row) =>
        Array.from(row.querySelectorAll('span'))
          .slice(1)
          .map((span) => span.textContent),
      );
      expect(labels).toEqual(
        locale === 'ko'
          ? [
              ['정책에 따른 생략'],
              ['대기 중', '필수', '현재 적용 대상 변경'],
              ['실패', '참고 항목', '사용 불가'],
              ['해당 없음'],
            ]
          : [
              ['policy skipped'],
              ['pending', 'required', 'stale'],
              ['failed', 'advisory', 'unavailable'],
              ['not applicable'],
            ],
      );
      const summary = screen.getByLabelText(locale === 'ko' ? '검사 요약' : 'Check summary');
      const expectedVisible =
        locale === 'ko'
          ? [
              '정책에 따른 생략',
              '대기 중',
              '필수',
              '현재 적용 대상 변경',
              '실패',
              '참고 항목',
              '사용 불가',
              '해당 없음',
            ]
          : [
              'policy skipped',
              'pending',
              'required',
              'stale',
              'failed',
              'advisory',
              'unavailable',
              'not applicable',
            ];
      for (const label of expectedVisible) expect(summary.textContent).toContain(label);
      expect(summary.closest('details')).toBeNull();
    },
  );
  it('uses natural localized view and readiness copy without translating authored fixture titles', async () => {
    localStorage.setItem(PREFERENCE_KEY, 'ko');
    window.history.replaceState(null, '', '#work?coordination=available');
    render(<App loader={async () => fixture()} />);
    await screen.findByRole('link', { name: /Available next work/ });
    expect(screen.getByLabelText('보기')).toBeTruthy();
    expect(screen.getByRole('button', { name: /보기:/ })).toBeTruthy();
    expect(screen.getAllByText(/재검증 필요/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Revalidation required/)).toBeNull();
  });
  it('keeps blocked action facts while hiding unfinished unknown outcome and unassessed recheck boilerplate', async () => {
    localStorage.setItem(PREFERENCE_KEY, 'ko');
    window.history.replaceState(null, '', '#task/A');
    const snapshot = fixture(),
      task = snapshot.tasks[0]!;
    const blocked = lane('blocked-qa', 'qa', {
      phase: 'blocked',
      result: 'unknown',
      waiting: { status: 'unknown' },
      check_ids: ['acceptance', 'scoped_tests', 'format_lint', 'custom_check'],
      blocker: {
        reason: 'Actual blocker',
        resolver_id: 'reviewer',
        next_trigger: 'Actual trigger',
      },
    });
    const view = render(
      <App
        loader={async () => ({
          ...snapshot,
          tasks: [
            {
              ...task,
              verification: { ...record(task.verification), lanes: collection([blocked]) },
            },
            snapshot.tasks[1]!,
          ],
        })}
      />,
    );
    await screen.findByRole('heading', { name: '병렬 검증' });
    const laneRow = view.container.querySelector('.verification-lane');
    expect(laneRow?.textContent).toContain('차단됨');
    expect(laneRow?.textContent).toContain('Actual blocker');
    expect(laneRow?.textContent).toContain('Actual trigger');
    expect(laneRow?.textContent).toContain(
      '완료 기준, 범위별 테스트, 서식 및 정적 검사, custom_check',
    );
    expect(laneRow?.textContent).not.toContain('알 수 없음');
    expect(laneRow?.textContent).not.toContain('평가 안 됨');
    expect(view.container.querySelector('.lane-explanation')).toBeNull();
  });
  it.each(['current', null, 'stale'])(
    'renders independent provider streams once with aggregate applicability %s',
    async (applicability) => {
      const snapshot = fixture(),
        task = snapshot.tasks[0]!;
      const stage = {
        id: 'web',
        kind: 'pr_ci',
        status: 'passed',
        current: true,
        reported_outcome: 'success',
        source: { head_sha: '1'.repeat(40), base_sha: '2'.repeat(40), tested_sha: '3'.repeat(40) },
        checks: [{ id: 'Web', run_id: 1, run_attempt: 1, job_id: 1, status: 'passed' }],
        checks_total: 1,
        checks_shown: 1,
        checks_omitted: 0,
        history: [
          {
            run_id: 1,
            run_attempt: 1,
            captured_at: '2026-01-01T09:00:00Z',
            head_sha: '1'.repeat(40),
            checkout_sha: '3'.repeat(40),
            job_ids: [1],
            jobs_total: 1,
            jobs_shown: 1,
            jobs_omitted: 0,
          },
        ],
        history_total: 1,
        history_shown: 1,
        history_omitted: 0,
        timing: { started_at: null, completed_at: null, duration_seconds: null },
      };
      const captured = (request_id: string) => ({
        task_id: 'A',
        reference: reference(),
        adopted: true,
        request_id,
        status: 'passed',
        applicability: 'current',
        stages: [{ ...stage, request_id }],
        total: 1,
        shown: 1,
        omitted: 0,
      });
      const first = captured('review-1'),
        second = captured('qa-1');
      const aggregate = {
        ...first,
        applicability,
        stages: [...first.stages, ...second.stages],
        total: 2,
        shown: 2,
      };
      const enriched = {
        ...task,
        provider_execution: aggregate,
        verification: {
          ...record(task.verification),
          lanes: collection([
            lane('review-1', 'code_review', { provider_execution: first }),
            lane('qa-1', 'qa', { provider_execution: second }),
          ]),
        },
      };
      expect(providerContext(snapshot, enriched)?.stages).toHaveLength(2);
      window.history.replaceState(null, '', '#task/A');
      render(<App loader={async () => ({ ...snapshot, tasks: [enriched, snapshot.tasks[1]!] })} />);
      await screen.findByRole('heading', { name: 'PR and beta execution' });
      expect(screen.getAllByText('PR CI')).toHaveLength(2);
      expect(screen.getAllByRole('heading', { name: 'PR and beta execution' })).toHaveLength(1);
    },
  );
  it.each(['en', 'ko'])(
    'summarizes delivered matrix volume once with one-level details in %s',
    async (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      window.history.replaceState(null, '', '#task/A');
      const snapshot = fixture(),
        task = snapshot.tasks[0]!;
      const delivered = { ...deliveredExecution, reference: reference() };
      const requested = { ...requestedExecution, reference: reference() };
      const view = render(
        <App
          loader={async () => ({
            ...snapshot,
            tasks: [
              {
                ...task,
                status: 'done',
                provider_execution: delivered,
                verification: {
                  ...record(task.verification),
                  lanes: collection([
                    lane('pr-request', 'ci', {
                      phase: 'finished',
                      result: 'passed',
                      next_trigger: 'Inspect the beta result',
                      provider_execution: requested,
                    }),
                  ]),
                },
              },
              snapshot.tasks[1]!,
            ],
          })}
        />,
      );
      await screen.findByRole('heading', {
        name: locale === 'ko' ? 'PR 및 베타 실행' : 'PR and beta execution',
      });
      expect(view.container.querySelectorAll('.provider-stages')).toHaveLength(1);
      expect(view.container.querySelectorAll('.provider-stages .verification-lane')).toHaveLength(
        4,
      );
      expect(view.container.querySelectorAll('.provider-stages details details')).toHaveLength(0);
      expect(view.container.querySelectorAll('.provider-stages .lane-heading .muted')).toHaveLength(
        4,
      );
      expect(view.container.querySelector('.lane-action')?.textContent).toContain(
        'Inspect the beta result',
      );
      const summaries = Array.from(view.container.querySelectorAll('.provider-stages summary'));
      const checks = summaries.filter((summary) =>
        summary.textContent?.startsWith(locale === 'ko' ? '검사 상세' : 'Check details'),
      );
      expect(checks).toHaveLength(4);
      expect(checks.every((summary) => !summary.closest('details')?.open)).toBe(true);
      expect(
        view.container.querySelectorAll(
          '[aria-label="' + (locale === 'ko' ? '검사 요약' : 'Check summary') + '"]',
        ),
      ).toHaveLength(4);
      fireEvent.click(checks[0]!);
      expect(checks[0]!.closest('details')?.open).toBe(true);
      expect(checks[0]!.closest('details')?.textContent).toContain('python-13');
    },
  );
  it('requires exact source, execution, capture and facts before deduplicating a provider stage', () => {
    const original = requestedExecution.stages[0]!;
    const expected = providerStageIdentity(deliveredExecution.stages[0]!);
    expect(providerStageIdentity(original)).toBe(expected);
    expect(expected).not.toBeNull();
    const changes: JsonRecord[] = [
      { source: { ...original.source, tested_sha: 'other-source' } },
      { current: false },
      {
        checks: original.checks.map((check, index) => (index ? check : { ...check, job_id: 999 })),
      },
      {
        checks: original.checks.map((check, index) =>
          index ? check : { ...check, run_attempt: 2 },
        ),
      },
      {
        checks: original.checks.map((check, index) => (index ? check : { ...check, run_id: null })),
      },
      { history: original.history.map((run) => ({ ...run, captured_at: '2026-01-01T09:00:00Z' })) },
      { history: [] },
      { checks_omitted: 1 },
      { status: 'failed' },
    ];
    for (const change of changes)
      expect(providerStageIdentity({ ...original, ...change })).not.toBe(expected);
    for (const length of [41, 48, 63]) {
      expect(
        providerStageIdentity({
          ...original,
          source: { ...original.source, head_sha: 'a'.repeat(length) },
        }),
      ).toBeNull();
      expect(
        providerStageIdentity({
          ...original,
          history: original.history.map((run) => ({ ...run, checkout_sha: 'a'.repeat(length) })),
        }),
      ).toBeNull();
    }
  });
  it('preserves omitted-lane diagnostics without treating represented observations as absent', async () => {
    window.history.replaceState(null, '', '#task/A');
    const snapshot = fixture(),
      task = snapshot.tasks[0]!;
    const view = render(
      <App
        loader={async () => ({
          ...snapshot,
          tasks: [
            {
              ...task,
              status: 'done',
              provider_execution: { ...deliveredExecution, reference: reference() },
              verification: {
                ...record(task.verification),
                lanes: collection([
                  lane('pr-request', 'ci', {
                    phase: 'finished',
                    result: 'passed',
                    provider_execution: {
                      ...requestedExecution,
                      reference: reference(),
                      omitted_lanes: 1,
                    },
                  }),
                ]),
              },
            },
            snapshot.tasks[1]!,
          ],
        })}
      />,
    );
    await screen.findAllByRole('heading', { name: 'PR and beta execution' });
    expect(view.container.querySelectorAll('.provider-stages .verification-lane')).toHaveLength(4);
    expect(
      screen.getByText('1 provider lanes omitted; execution assessment is incomplete.'),
    ).toBeTruthy();
    expect(screen.queryByText('Awaiting a current provider observation')).toBeNull();
  });
  it('retains stale lane captures and exposes older failures without opening history', async () => {
    window.history.replaceState(null, '', '#task/A');
    const snapshot = fixture(),
      task = snapshot.tasks[0]!;
    const stage = requestedExecution.stages[0]!;
    const failedEarlier = { ...stage.history[0]!, run_attempt: 2, reported_outcome: 'failure' };
    const provider = {
      ...deliveredExecution,
      reference: reference(),
      stages: deliveredExecution.stages.map((item, index) =>
        index
          ? item
          : {
              ...item,
              history: [failedEarlier, ...item.history],
              history_total: 2,
              history_shown: 2,
            },
      ),
    };
    const view = render(
      <App
        loader={async () => ({
          ...snapshot,
          tasks: [
            {
              ...task,
              status: 'done',
              provider_execution: provider,
              verification: {
                ...record(task.verification),
                lanes: collection([
                  lane('pr-request', 'ci', {
                    applicability: 'stale',
                    provider_execution: {
                      ...requestedExecution,
                      reference: reference(),
                      applicability: 'stale',
                    },
                  }),
                ]),
              },
            },
            snapshot.tasks[1]!,
          ],
        })}
      />,
    );
    await screen.findAllByRole('heading', { name: 'PR and beta execution' });
    expect(view.container.querySelectorAll('.provider-stages .verification-lane')).toHaveLength(6);
    const history = Array.from(view.container.querySelectorAll('.provider-stages summary')).find(
      (summary) => summary.textContent?.includes('1 failure'),
    );
    expect(history).toBeTruthy();
    expect(history!.closest('details')?.open).toBe(false);
  });
  it('requires current candidate factors and preserved zero totals, never authorization', () => {
    const snapshot = fixture();
    const task = snapshot.tasks[1]!;
    expect(readinessFactors(snapshot, task).available).toBe(true);
    const blocked = {
      ...task,
      readiness: {
        ...record(task.readiness),
        collisions: { ...collection([], 2), reference: reference('B') },
      },
    };
    expect(readinessFactors(snapshot, blocked).available).toBe(false);
    expect(readinessFactors(snapshot, blocked).blockers).toBe(2);
    expect(
      readinessFactors(snapshot, { ...task, readiness: undefined } as unknown as typeof task).known,
    ).toBe(false);
    expect(
      readinessFactors(snapshot, {
        ...task,
        readiness: { ...record(task.readiness), reference: { ...reference('B'), revision: 'old' } },
      }).available,
    ).toBe(false);
  });
  it('retains stale reported successes and rejects mismatched optional references', () => {
    const snapshot = fixture(),
      task = snapshot.tasks[0]!;
    const stale = {
      ...task,
      verification: {
        ...record(task.verification),
        lanes: collection([
          lane('review-1', 'code_review', {
            result: 'passed',
            phase: 'finished',
            applicability: 'stale',
          }),
        ]),
      },
    };
    expect(verificationContext(snapshot, stale)?.lanes.items[0]?.result).toBe('passed');
    expect(
      selectWorkTasks(
        { ...snapshot, tasks: [stale] },
        { ...defaultContext, filters: { ...defaultContext.filters, coordination: 'verification' } },
      ),
    ).toHaveLength(1);
    expect(
      verificationContext(snapshot, {
        ...task,
        verification: { ...record(task.verification), task_id: 'another' },
      }),
    ).toBeNull();
    expect(verificationContext({ ...snapshot, verification: null }, task)).toBeNull();
  });
  it('only displays explicit consistent observed waiting endpoints, including real zero', () => {
    expect(observedWait(waiting)?.seconds).toBe(120);
    expect(observedWait({ ...waiting, as_of: null })).toBeNull();
    expect(observedWait({ ...waiting, as_of: '2026-10-14T08:59:00Z' })).toBeNull();
    expect(observedWait({ ...waiting, clock_id: null })).toBeNull();
    expect(observedWait({ ...waiting, seconds: 1 })).toBeNull();
    expect(observedWait({ ...waiting, as_of: waiting.started_at, seconds: 0 })?.seconds).toBe(0);
  });
  it('roundtrips coordination filters and recorded-priority sorting with legacy presets', () => {
    const snapshot = fixture();
    const context = {
      ...defaultContext,
      filters: { ...defaultContext.filters, coordination: 'available', sort: 'priority' },
    };
    const decoded = parseNavigation(navigationHash({ view: 'work' }, context), snapshot);
    expect(decoded.filters).toEqual(context.filters);
    expect(selectWorkTasks(snapshot, decoded).map((task) => task.id)).toEqual(['B']);
    expect(parseNavigation('#work?sort=id', snapshot).filters.coordination).toBeUndefined();
    expect(parseNavigation('#work?coordination=claim-authorized', snapshot).issues).toContain(
      'coordination',
    );
    expect(
      selectWorkTasks(snapshot, {
        ...context,
        filters: { ...context.filters, coordination: '', sort: 'priority' },
      }).map((task) => task.id),
    ).toEqual(['B', 'A']);
  });
  it('filters reviewer responsibility separately from implementation ownership', () => {
    const snapshot = fixture();
    expect(
      selectWorkTasks(snapshot, {
        ...defaultContext,
        filters: { ...defaultContext.filters, owner: 'reviewer', coordination: 'verification' },
      }).map((task) => task.id),
    ).toEqual(['A']);
    expect(
      selectWorkTasks(snapshot, {
        ...defaultContext,
        filters: { ...defaultContext.filters, owner: 'reviewer' },
      }),
    ).toHaveLength(0);
  });
  it('renders concurrent states, triggers and observations without audit panels', async () => {
    window.history.replaceState(null, '', '#task/A');
    render(<App loader={async () => fixture()} />);
    await screen.findByRole('heading', { name: 'Verification lanes' });
    expect(screen.getByText('Code review')).toBeTruthy();
    expect(screen.getByText('QA')).toBeTruthy();
    expect(screen.getAllByText('Wait for the rendered QA result', { exact: false })).toHaveLength(
      2,
    );
    expect(screen.getAllByText('2 min')).toHaveLength(2);
    expect(screen.queryByText('source-one')).toBeNull();
    expect(screen.queryByText(/Original verification/)).toBeNull();
  });
  it('localizes system controls while preserving authored Korean content', async () => {
    localStorage.setItem(PREFERENCE_KEY, 'ko');
    window.history.replaceState(null, '', '#task/A');
    const raw = fixture(),
      task = raw.tasks[0]!;
    const snapshot = {
      ...raw,
      tasks: [
        {
          ...task,
          verification: {
            ...record(task.verification),
            lanes: collection([lane('qa-1', 'qa', { next_trigger: '화면 검증 결과를 기다려요' })]),
          },
        },
        raw.tasks[1]!,
      ],
    };
    render(<App loader={async () => snapshot} />);
    await screen.findByRole('heading', { name: '병렬 검증' });
    expect(screen.getByText('화면 검증 결과를 기다려요', { exact: false })).toBeTruthy();
    expect(screen.getByText('품질 검증')).toBeTruthy();
    expect(screen.getByText('2분')).toBeTruthy();
  });
  it('keeps a focused verifier link and open details connected through changed refresh', async () => {
    window.history.replaceState(null, '', '#task/A');
    let next = fixture();
    const loader = vi.fn(async () => new SnapshotResponse(200, null, next));
    render(<App loader={loader} />);
    await screen.findByRole('heading', { name: 'Verification lanes' });
    const verifier = screen.getAllByRole('link', { name: 'reviewer' })[0]!;
    verifier.focus();
    const before = verifier;
    next = {
      ...next,
      generated_at: '2026-10-14T09:03:00Z',
      checkpoint: { note: 'Changed unrelated recorded context' },
    };
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Refresh' })));
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
    expect(before.isConnected).toBe(true);
    // Explicit refresh focus does not steal focus from a connected lane link.
    expect(document.activeElement).toBe(before);
  });
});
