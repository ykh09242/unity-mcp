import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { CheckInputs } from './flow-inputs-ui';
import { LocalExecution } from './flow-execution-ui';
import { RunHistory } from './flow-run-history-ui';
import { flowFixture, flowInput, flowExecution, flowCollection } from './flow.fixture';
import { inputSummary } from './flow-inputs';
import { capturedExecution } from './flow-execution';
import { record } from './model';
import type { JsonRecord, Snapshot } from './model';
import { SnapshotResponse } from './useSnapshot';
import { LanguageProvider, PREFERENCE_KEY } from './i18n';

describe('flow verification summaries', () => {
  it.each(['constructor', '__proto__', 'toString', 'Ordinary authored input limitation'])(
    'preserves unfamiliar reason %s in the primary and detail positions in both languages',
    (reason) => {
      const content = <CheckInputs value={{ ...flowInput, reasons: [reason, reason] }} />;
      const view = render(<LanguageProvider>{content}</LanguageProvider>);
      expect(screen.getAllByText(reason)).toHaveLength(2);
      view.unmount();
      localStorage.setItem(PREFERENCE_KEY, 'ko');
      render(<LanguageProvider>{content}</LanguageProvider>);
      expect(screen.getAllByText(reason)).toHaveLength(2);
    },
  );
  it.each(['en', 'ko'])(
    'uses actual publication facts for the recording instruction in both capture surfaces in %s',
    (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const nextAction = 'Inspect captured execution facts before recording verification';
      const failure = {
        ...flowExecution,
        process_result: 'spawn_failed',
        reported_check_result: 'failed',
        capture_status: 'incomplete',
        next_action: nextAction,
      };
      const renderCapture = (publication: string) => (
        <LanguageProvider>
          <LocalExecution
            value={{
              ...failure,
              publication: { status: publication, observation_id: 'recorded-failure' },
            }}
          />
          <RunHistory
            value={flowCollection([
              {
                ...failure,
                publication: { status: publication, observation_id: 'recorded-failure' },
              },
            ])}
          />
        </LanguageProvider>
      );
      const view = render(renderCapture('pending'));
      const instruction =
        locale === 'ko' ? '수집된 실행 근거를 확인한 뒤 검증 결과를 기록하세요' : nextAction;
      expect(screen.getAllByText(instruction)).toHaveLength(2);
      view.rerender(renderCapture('recorded'));
      expect(screen.queryByText(instruction)).toBeNull();
      expect(
        screen.getAllByText(locale === 'ko' ? '근거 기록됨' : 'Evidence recorded'),
      ).toHaveLength(2);
      expect(
        screen.getAllByText(locale === 'ko' ? '근거 수집 미완료' : 'Capture incomplete'),
      ).toHaveLength(2);
      expect(view.container.querySelectorAll('.badge')[0]?.textContent).toBe(
        locale === 'ko' ? '실패' : 'failed',
      );
    },
  );
  it('retains a different recorded next action and unavailable-history action', () => {
    render(
      <>
        <LocalExecution
          value={{
            ...flowExecution,
            publication: { status: 'recorded' },
            next_action: 'Resolve the remaining blocker',
          }}
        />
        <RunHistory
          value={flowCollection([
            {
              schema_version: 1,
              status: 'unavailable',
              next_action: 'Inspect unavailable capture',
              reasons: [],
            },
          ])}
        />
      </>,
    );
    expect(screen.getByText('Resolve the remaining blocker')).toBeTruthy();
    expect(screen.getByText('Inspect unavailable capture')).toBeTruthy();
  });
  it.each(['en', 'ko'])(
    'maps deterministic input actions and reasons while retaining unfamiliar input text in %s',
    (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const actions: readonly (readonly [string, string])[] = [
        ['Inspect unknown inputs', '확인하지 못한 입력을 살펴보세요'],
        ['Recheck affected checks', '영향받은 검사를 다시 확인하세요'],
        ['Inspect current task obligations', '현재 작업의 확인 항목을 살펴보세요'],
        ['Inspect unavailable inputs', '확인할 수 없는 입력을 살펴보세요'],
      ];
      const view = render(
        <LanguageProvider>
          <CheckInputs
            value={{
              ...flowInput,
              next_action: actions[0]?.[0] || '',
              reasons: [
                'changed_inputs',
                'unknown_inputs',
                'unavailable_inputs',
                'Authored input limitation',
              ],
            }}
          />
        </LanguageProvider>,
      );
      for (const [action, translated] of actions) {
        view.rerender(
          <LanguageProvider>
            <CheckInputs
              value={{
                ...flowInput,
                next_action: action,
                reasons: [
                  'changed_inputs',
                  'unknown_inputs',
                  'unavailable_inputs',
                  'Authored input limitation',
                ],
              }}
            />
          </LanguageProvider>,
        );
        expect(screen.getByText(locale === 'ko' ? translated : action)).toBeTruthy();
      }
      for (const label of locale === 'ko'
        ? [
            '검증 입력이 변경됐어요',
            '전체 검증 입력을 확인할 수 없어요',
            '검증 입력을 확인할 수 없어요',
          ]
        : [
            'Verification inputs changed',
            'Verification input coverage unknown',
            'Verification inputs unavailable',
          ])
        expect(screen.getByText(label)).toBeTruthy();
      expect(screen.getByText('Authored input limitation')).toBeTruthy();
    },
  );
  it.each(['en', 'ko'])(
    'localizes fixed stale execution diagnostics without rewriting unfamiliar text in %s',
    (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      render(
        <LanguageProvider>
          <RunHistory
            value={flowCollection([
              {
                ...flowExecution,
                reasons: [
                  'Captured verification inputs or task context are no longer current',
                  'A newer recorded verification event supersedes this execution',
                  'Current observation authority or request binding is unavailable',
                  'Authored capture limitation',
                ],
              },
            ])}
          />
        </LanguageProvider>,
      );
      for (const label of locale === 'ko'
        ? [
            '수집된 검증 입력이나 작업 정보가 현재 상태와 달라요',
            '더 최근의 검증 기록이 이 실행을 대체했어요',
            '현재 결과 기록 권한이나 요청 연결을 확인할 수 없어요',
          ]
        : [
            'Captured verification inputs or task context are no longer current',
            'A newer recorded verification event supersedes this execution',
            'Current observation authority or request binding is unavailable',
          ])
        expect(screen.getByText(label)).toBeTruthy();
      expect(screen.getByText('Authored capture limitation')).toBeTruthy();
    },
  );
  it.each([
    ['en', 'Recording preparation needs attention'],
    ['ko', '기록 준비에 확인이 필요해요'],
  ])('shows unavailable recording preparation safely in %s', (locale, warning) => {
    localStorage.setItem(PREFERENCE_KEY, locale);
    const view = render(
      <LanguageProvider>
        <LocalExecution
          value={{
            ...flowExecution,
            observation_draft: { status: 'unavailable', reasons: ['hidden/private-draft-error'] },
          }}
        />
      </LanguageProvider>,
    );
    expect(screen.getByText(warning)).toBeTruthy();
    expect(view.container.textContent).not.toContain('hidden/private-draft-error');
    expect(view.container.querySelector('.execution-summary')).toBeTruthy();
  });
  it.each([undefined, { status: 'prepared', reasons: [] }])(
    'keeps ordinary pending publication for absent or prepared draft %j',
    (draft) => {
      const value =
        draft === undefined ? flowExecution : { ...flowExecution, observation_draft: draft };
      render(<LocalExecution value={value} />);
      expect(screen.getByText('Evidence publication pending')).toBeTruthy();
      expect(screen.queryByText('Recording preparation needs attention')).toBeNull();
    },
  );
  it.each([
    { status: 'ready', reasons: [] },
    { status: 'prepared', reasons: 'hidden' },
  ])('rejects malformed optional draft diagnostics %j', (draft) => {
    expect(capturedExecution({ ...flowExecution, observation_draft: draft })).toBeNull();
  });
  it.each([
    { expected: 0, captured: 1, missing: 0 },
    { expected: 2, captured: 1, missing: 0 },
    { expected: 1, captured: 1, missing: 1 },
  ])('rejects inconsistent artifact counts %j without displaying captured success', (artifacts) => {
    const value = { ...flowExecution, artifacts };
    expect(capturedExecution(value)).toBeNull();
    const view = render(<LocalExecution value={value} />);
    expect(screen.getByText('Execution capture is unavailable.')).toBeTruthy();
    expect(view.container.querySelector('.execution-summary')).toBeNull();
  });
  it('retains valid incomplete artifact capture and its reported outcome separately', () => {
    const capture = capturedExecution({
      ...flowExecution,
      capture_status: 'incomplete',
      reported_check_result: 'failed',
      artifacts: { expected: 2, captured: 1, missing: 1 },
    });
    expect(capture?.captureStatus).toBe('incomplete');
    expect(capture?.reportedResult).toBe('failed');
    expect(capture?.artifacts).toEqual({ expected: 2, captured: 1, missing: 1 });
  });
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
