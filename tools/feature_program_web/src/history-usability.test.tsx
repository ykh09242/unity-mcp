import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LanguageProvider, PREFERENCE_KEY } from './i18n';
import { DeliveryHistory, StaffingHistory, TaskHistory } from './history-ui';
import { parseSnapshot } from './model';
import type { JsonRecord, Task } from './model';

const taskFixture = (extra: JsonRecord = {}): Task => ({
  id: 'QUERY-1',
  title: 'Recorded task',
  status: 'done',
  ...extra,
});
const snapshotFixture = (task: Task) =>
  parseSnapshot({
    snapshot_version: 1,
    revision: 'history-usability',
    program_id: 'history-usability',
    tasks: [task],
  });
const minimalDelivery = (conclusion: string | null = 'success', fields: JsonRecord = {}) => ({
  status: 'ci_recorded_success',
  metadata: {
    pr_url: 'https://github.com/example/project/pull/30',
    tested_commit: 'tested',
    merge_status: 'merged',
    cleanup: { status: 'completed' },
    ci: [
      {
        name: 'beta build',
        url: 'https://github.com/example/project/actions/runs/42',
        commit: 'tested',
        conclusion,
        ...fields,
      },
    ],
  },
});

function onlyDisclosure(container: HTMLElement) {
  const disclosures = container.querySelectorAll('details');
  expect(disclosures).toHaveLength(1);
  const disclosure = disclosures[0];
  if (!disclosure) throw new Error('Missing history disclosure');
  expect(disclosure.querySelector('details')).toBeNull();
  return disclosure;
}

describe('one-step operational history', () => {
  it.each(['en', 'ko'])(
    'shows every attempt, round and check after one %s disclosure',
    async (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const task = taskFixture({
        attempts: [1, 2].map((number) => ({
          number,
          owner: `worker-${number}`,
          status: number === 1 ? 'released' : 'review_complete',
          rounds: [
            {
              round: 1,
              receipt_record: {
                checks: [
                  {
                    id: `unit-${number}`,
                    status: number === 1 ? 'failed' : 'passed',
                    command: `test attempt ${number}`,
                  },
                ],
                acceptance_evidence: [{ criterion: `criterion-${number}`, status: 'passed' }],
              },
              review_record: {
                verdict: number === 1 ? 'rejected' : 'accepted',
                reviewer: 'independent-reviewer',
                unresolved_findings: number === 1 ? ['Keep historical finding'] : [],
              },
            },
          ],
        })),
      });
      const { container } = render(
        <LanguageProvider>
          <TaskHistory task={task} snapshot={snapshotFixture(task)} />
        </LanguageProvider>,
      );
      const disclosure = onlyDisclosure(container);
      await userEvent.click(
        within(disclosure).getByText(
          locale === 'ko' ? '시도 및 검토 이력' : 'Attempt and review history',
        ),
      );
      expect(disclosure.open).toBe(true);
      expect(
        [...disclosure.querySelectorAll('.history-attempt h4')].map(
          (heading) => heading.textContent,
        ),
      ).toEqual([
        `${locale === 'ko' ? '시도' : 'Attempt'} 1 · worker-1`,
        `${locale === 'ko' ? '시도' : 'Attempt'} 2 · worker-2`,
      ]);
      for (const number of [1, 2]) {
        expect(screen.getByText(`unit-${number}`)).toBeDefined();
        expect(screen.getByText(`test attempt ${number}`)).toBeDefined();
        expect(screen.getByText(`criterion-${number}`)).toBeDefined();
      }
      expect(screen.getByText('Keep historical finding')).toBeDefined();
      expect(screen.getByText(locale === 'ko' ? '승인됨' : 'accepted')).toBeDefined();
    },
  );

  it.each(['en', 'ko'])(
    'keeps sparse %s CI result and provider links without empty metadata',
    async (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const { container } = render(
        <LanguageProvider>
          <DeliveryHistory
            task={taskFixture({
              delivery: minimalDelivery(),
              delivery_history: { status: 'unrecorded', items: [] },
            })}
          />
        </LanguageProvider>,
      );
      const disclosure = onlyDisclosure(container);
      await userEvent.click(
        within(disclosure).getByText(locale === 'ko' ? '기록된 PR / CI' : 'Recorded PR / CI'),
      );
      expect(disclosure.open).toBe(true);
      const check = screen.getByRole('link', { name: 'beta build' });
      expect(check.getAttribute('href')).toBe('https://github.com/example/project/actions/runs/42');
      expect(check.closest('li')?.querySelector('dl')).toBeNull();
      expect(
        within(check.closest('li')!).getByText(locale === 'ko' ? '성공' : 'success'),
      ).toBeDefined();
      expect(
        screen
          .getByRole('link', { name: locale === 'ko' ? 'PR 보기' : 'Open PR' })
          .getAttribute('href'),
      ).toBe('https://github.com/example/project/pull/30');
      expect(screen.queryByText(locale === 'ko' ? '검사 상세' : 'Check details')).toBeNull();
      expect(check.closest('li')?.textContent).not.toContain(
        locale === 'ko' ? '알 수 없음' : 'Unknown',
      );
    },
  );

  it('shows populated CI metadata, retains zero IDs and revision warnings without more disclosures', () => {
    const populated = minimalDelivery('failure', {
      run_id: 0,
      job_id: 17,
      observed_at: 'saved observation',
      started_at: 'saved start',
      completed_at: 'saved completion',
      commit: 'different',
    });
    const { container } = render(
      <DeliveryHistory
        task={taskFixture({
          delivery: populated,
          delivery_history: {
            status: 'recorded',
            items: [{ record: minimalDelivery('skipped'), time_status: 'unknown' }],
          },
        })}
      />,
    );
    onlyDisclosure(container);
    expect(screen.getByText('Run').nextElementSibling?.textContent).toBe('0');
    expect(screen.getByText('Job').nextElementSibling?.textContent).toBe('17');
    for (const value of [
      'saved observation',
      'saved start',
      'saved completion',
      'failure',
      'skipped',
    ])
      expect(screen.getByText(value)).toBeDefined();
    expect(screen.getByText('Check revision differs from tested commit')).toBeDefined();
    expect(screen.getByText('Time unknown')).toBeDefined();
  });

  it('retains a genuinely unrecorded CI conclusion and required missing-check warning', () => {
    const sparse = minimalDelivery(null, { run_id: null, job_id: '', observed_at: null });
    const { container } = render(
      <DeliveryHistory
        task={taskFixture({
          delivery: {
            ...sparse,
            metadata: { ...sparse.metadata, required_ci: ['beta build', 'missing job'] },
          },
        })}
      />,
    );
    onlyDisclosure(container);
    const row = screen.getByRole('link', { name: 'beta build' }).closest('li')!;
    expect(within(row).getByText('Unknown')).toBeDefined();
    expect(within(row).queryByText('success')).toBeNull();
    expect(row.querySelector('dl')).toBeNull();
    expect(
      screen.getByText('Required checks without observations').nextElementSibling?.textContent,
    ).toContain('missing job');
  });

  it('reads Team assignments in one disclosure and can share the existing task disclosure', () => {
    const actor = {
      history: [
        {
          decision: 'reuse',
          reason: 'Preserve domain context',
          metadata: { model_id: 'recorded-model' },
        },
      ],
      assignments: [1, 2].map((attempt) => ({
        task_id: `T-${attempt}`,
        attempt,
        status: 'done',
        metadata: { model_id: `model-${attempt}`, reasoning_effort: 'high', role: 'implementer' },
      })),
    };
    const view = render(<StaffingHistory actor={actor} />);
    onlyDisclosure(view.container);
    expect(screen.getByText('Preserve domain context')).toBeDefined();
    for (const number of [1, 2]) expect(screen.getByText(`model-${number}`)).toBeDefined();
    view.rerender(
      <details open>
        <summary>Recorded assignment</summary>
        <StaffingHistory actor={actor} assignment={{}} disclosure={false} />
      </details>,
    );
    onlyDisclosure(view.container);
    expect(screen.getByRole('heading', { name: 'Staffing and assignment history' })).toBeDefined();
    expect(screen.getByText('Preserve domain context')).toBeDefined();
  });
});
