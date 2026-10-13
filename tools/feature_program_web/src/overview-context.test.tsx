import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import peerWire from './fixtures/relate-peer.json?raw';
import { parseSnapshot, record } from './model';
import { CriterionCoverage, ProgramAttention } from './overview-context';
import { LanguageProvider, PREFERENCE_KEY } from './i18n';
import { presentSnapshot } from './presentation';
import { PresentationProvider } from './presentation-ui';
import { presentationDigest } from './presentation-digest';

function fixture() {
  const snapshot = parseSnapshot(JSON.parse(peerWire).snapshot);
  const brief = record(snapshot.brief);
  const ref = { path: 'backlog.json', pointer: '', revision: snapshot.revision };
  const collection = (items: unknown[], omitted = 0) => ({
    items,
    shown: items.length,
    total: items.length + omitted,
    omitted,
    reference: ref,
  });
  return { snapshot, brief, ref, collection };
}

describe('supplied Overview context', () => {
  it.each(['ko', 'en'])('localizes only known producer actions by kind with %s UI', (locale) => {
    localStorage.setItem(PREFERENCE_KEY, locale);
    const { snapshot, brief, ref, collection } = fixture();
    const actions = [
      [
        'objective',
        'Inspect the objective-completion prerequisites',
        '목표 완료 조건을 확인하세요.',
      ],
      ['integrity', 'Inspect original records and artifacts', '원문과 근거를 확인하세요'],
      [
        'acceptance',
        'Inspect the exact submission/review binding',
        '제출 및 검토의 정확한 연결을 확인하세요',
      ],
      [
        'recovery',
        'Inspect recovery-readiness; lock existence does not establish liveness or takeover authority',
        '복구 상태를 확인하세요. 잠금 파일만으로 실행 여부나 인수 권한을 판단할 수 없습니다.',
      ],
      [
        'delivery',
        'Inspect frozen delivery inputs and provider facts',
        '고정된 전달 기록과 제공자의 실제 결과를 확인하세요',
      ],
      [
        'question',
        'Inspect the saved answer, acknowledgement and application separately',
        '저장된 답변, 확인, 적용 결과를 각각 확인하세요',
      ],
      ['transfer', 'Follow the recorded succession workflow', '기록된 인계 절차를 따르세요'],
      [
        'coverage',
        'Inspect exact criterion and current membership/attempt bindings',
        '기준과 현재 작업 구성 및 시도 연결을 확인하세요',
      ],
    ];
    for (const [kind, action, translated] of actions) {
      const raw = parseSnapshot({
        ...snapshot,
        objective: { id: 'goal' },
        brief: {
          ...brief,
          attention: collection([
            {
              kind,
              entity_id: 'goal',
              next_action: action,
              reference: { ...ref, pointer: '/objective' },
              observed_revision: snapshot.revision,
            },
          ]),
        },
      });
      const view = render(
        <LanguageProvider>
          <ProgramAttention snapshot={raw} />
        </LanguageProvider>,
      );
      expect(screen.getByText(locale === 'ko' ? translated! : action!)).toBeTruthy();
      expect(record(record(raw.brief).attention).items).toEqual([
        expect.objectContaining({ next_action: action }),
      ]);
      view.unmount();
    }
  });
  it.each(['objective', 'blocked', 'unknown'])(
    'preserves authored or unknown %s actions even when they equal system copy',
    (kind) => {
      localStorage.setItem(PREFERENCE_KEY, 'ko');
      const { snapshot, brief, ref, collection } = fixture();
      const action = 'Inspect the objective-completion prerequisites';
      render(
        <LanguageProvider>
          <ProgramAttention
            snapshot={parseSnapshot({
              ...snapshot,
              objective: { id: 'goal', next_action: action },
              brief: {
                ...brief,
                attention: collection([
                  {
                    kind,
                    entity_id: 'goal',
                    next_action: action,
                    reference: ref,
                    observed_revision: snapshot.revision,
                  },
                  {
                    kind: 'question',
                    entity_id: 'Q',
                    next_action: 'An unknown authored instruction',
                    reference: ref,
                    observed_revision: snapshot.revision,
                  },
                ]),
              },
            })}
          />
        </LanguageProvider>,
      );
      expect(screen.getByText(action)).toBeTruthy();
      expect(screen.getByText('An unknown authored instruction')).toBeTruthy();
    },
  );
  it.each(['en', 'ko'])(
    'keeps reviewed task actions in the session language with %s interface copy',
    (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const { snapshot, brief, ref, collection } = fixture();
      const original = 'Check the exact original';
      const raw = parseSnapshot({
        ...snapshot,
        communication_language: 'ko',
        tasks: [{ id: 'T1', title: 'Original task', status: 'active', next_action: original }],
        presentation: {
          schema_version: 1,
          revision: snapshot.revision,
          locale: 'ko',
          entries: {
            action: {
              status: 'current',
              mode: 'translated',
              text: '정확한 원문을 확인해요',
              target: { kind: 'task_action', entity_id: 'T1', field: 'next_action' },
              origin: { ...ref, pointer: '/tasks/0/next_action' },
              binding: {
                program_id: snapshot.program_id,
                source_value_sha256: presentationDigest(original),
                context: {},
              },
            },
          },
        },
        brief: {
          ...brief,
          attention: collection([
            {
              kind: 'collision',
              entity_id: 'T1:T2',
              task_ids: ['T1', 'T2'],
              reason: 'Exact supplied conflict',
              next_action: original,
              reference: { ...ref, pointer: '/tasks/0', task_id: 'T1' },
              observed_revision: snapshot.revision,
            },
          ]),
        },
      });
      const view = presentSnapshot(raw);
      render(
        <LanguageProvider>
          <PresentationProvider snapshot={view}>
            <ProgramAttention snapshot={view} />
          </PresentationProvider>
        </LanguageProvider>,
      );
      expect(screen.getByText('정확한 원문을 확인해요')).toBeTruthy();
      expect(screen.queryByText(original)).toBeNull();
      expect(screen.getByRole('link', { name: /Original task/ }).getAttribute('href')).toBe(
        '#task/T1',
      );
      expect(raw.tasks[0]?.next_action).toBe(original);
    },
  );
  it('retains an observed recovery artifact reference without inventing a backlog revision', () => {
    const { snapshot, brief, collection } = fixture();
    const row = {
      kind: 'recovery',
      entity_id: '.work/lock',
      reference: { path: '.work/lock' },
      observed_revision: snapshot.revision,
      reason: 'Observed recovery input',
      next_action: 'Inspect original records and artifacts',
    };
    const view = render(
      <ProgramAttention
        snapshot={parseSnapshot({ ...snapshot, brief: { ...brief, attention: collection([row]) } })}
      />,
    );
    expect(screen.getByText('Observed recovery input')).toBeTruthy();
    view.rerender(
      <ProgramAttention
        snapshot={parseSnapshot({
          ...snapshot,
          brief: { ...brief, attention: collection([{ ...row, observed_revision: 'other' }]) },
        })}
      />,
    );
    expect(screen.queryByText('Observed recovery input')).toBeNull();
  });
  it('keeps goal-only questions and every supplied attention kind, with truthful reduction and exact source access', async () => {
    const { snapshot, brief, ref, collection } = fixture();
    const kinds = [
      'question',
      'integrity',
      'recovery',
      'objective',
      'blocked',
      'collision',
      'acceptance',
      'review',
      'delivery',
      'dependency',
      'revalidation',
      'coverage',
      'transfer',
    ];
    const rows = kinds.map((kind) => ({
      kind,
      entity_id: kind,
      reason: `Reason ${kind}`,
      next_action: `Next ${kind}`,
      reference: ref,
      observed_revision: snapshot.revision,
      task_ids: [],
      criterion_impact: collection([]),
    }));
    render(
      <ProgramAttention
        snapshot={parseSnapshot({
          ...snapshot,
          brief: {
            ...brief,
            attention: collection(rows, 3),
            attention_totals_by_kind: { question: 1, recovery: 1 },
          },
        })}
      />,
    );
    expect(screen.getByText('Showing 5 of 13 supplied items')).toBeTruthy();
    expect(screen.getByText('13 / 16 shown · 3 omitted')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open question' }).getAttribute('href')).toBe(
      '#question/question',
    );
    await userEvent.click(
      screen.getByRole('button', { name: 'Show all supplied attention items' }),
    );
    for (const kind of kinds) expect(screen.getByText(`Reason ${kind}`)).toBeTruthy();
    expect(screen.getByText('13 / 16 shown · 3 omitted')).toBeTruthy();
    expect(screen.queryByText('No current attention items recorded.')).toBeNull();
  });

  it('does not turn omitted, mismatched or absent context into empty healthy attention', () => {
    const { snapshot, brief, collection } = fixture();
    const view = render(
      <ProgramAttention
        snapshot={parseSnapshot({ ...snapshot, brief: { ...brief, attention: collection([], 8) } })}
      />,
    );
    expect(screen.getByText('0 / 8 shown · 8 omitted')).toBeTruthy();
    expect(screen.queryByText('No current attention items recorded.')).toBeNull();
    view.rerender(
      <ProgramAttention
        snapshot={parseSnapshot({ ...snapshot, brief: { ...brief, revision: 'older' } })}
      />,
    );
    expect(
      screen.getByText('Attention summary is unavailable. Recorded work remains inspectable.'),
    ).toBeTruthy();
    view.rerender(<ProgramAttention snapshot={parseSnapshot({ ...snapshot, brief: null })} />);
    expect(
      screen.getByText('Attention summary is unavailable. Recorded work remains inspectable.'),
    ).toBeTruthy();
  });

  it('updates same-revision observed facts and rejects a stale row binding', () => {
    const { snapshot, brief, ref, collection } = fixture();
    const make = (reason: string, revision = snapshot.revision) =>
      parseSnapshot({
        ...snapshot,
        brief: {
          ...brief,
          attention: collection([
            {
              kind: 'integrity',
              entity_id: 'source',
              reason,
              reference: ref,
              observed_revision: revision,
            },
          ]),
        },
      });
    const view = render(<ProgramAttention snapshot={make('Initial source')} />);
    view.rerender(<ProgramAttention snapshot={make('Changed source')} />);
    expect(screen.queryByText('Initial source')).toBeNull();
    expect(screen.getByText('Changed source')).toBeTruthy();
    view.rerender(<ProgramAttention snapshot={make('Old observation', 'old')} />);
    expect(screen.queryByText('Old observation')).toBeNull();
    expect(screen.getByText('Current source binding is unavailable.')).toBeTruthy();
  });

  it('does not display an invalid criterion binding as the current goal text', () => {
    const { snapshot, brief, ref, collection } = fixture();
    render(
      <CriterionCoverage
        snapshot={parseSnapshot({
          ...snapshot,
          brief: {
            ...brief,
            objective: {
              criteria: collection([
                {
                  ordinal: 0,
                  text_sha256: 'old',
                  text: 'Wrong old criterion',
                  reference: { ...ref, revision: 'old' },
                },
              ]),
            },
          },
        })}
      />,
    );
    expect(screen.queryByText('Wrong old criterion')).toBeNull();
    expect(screen.getByText('Criterion context is unavailable.')).toBeTruthy();
  });
  it('presents explicit links and recorded failed checks separately from criterion acceptance and annotation freshness', async () => {
    const { snapshot, brief, ref, collection } = fixture();
    const criterion = { ordinal: 0, text_sha256: 'exact-text-hash' };
    const receipt = { path: 'evidence/S01/attempt-1/checks.json', sha256: 'record-hash' };
    render(
      <CriterionCoverage
        snapshot={parseSnapshot({
          ...snapshot,
          objective: { id: 'goal', criteria: ['Exact criterion'] },
          brief: {
            ...brief,
            objective: {
              criteria: collection([
                {
                  ...criterion,
                  text: 'Exact criterion',
                  reference: ref,
                  linkage: 'linked',
                  knowledge: 'contributors_declared',
                  contributors: collection(['S01']),
                  uncovered: collection([{ reason: 'Remaining explicit gap' }]),
                },
              ]),
            },
          },
          coverage: {
            objective_id: 'goal',
            criteria: [
              {
                criterion,
                links: [
                  {
                    task_id: 'S01',
                    paths: ['tools/test.py'],
                    records: [{ kind: 'receipt', receipt }],
                  },
                ],
                recorded_observations: [
                  {
                    task_id: 'S01',
                    kind: 'receipt',
                    receipt,
                    facts: {
                      checks: [
                        { name: 'Native failure', status: 'failed', execution_kind: 'native' },
                      ],
                    },
                  },
                ],
              },
            ],
            history: [
              { id: 'latest', freshness: 'stale', reasons: ['Exact source drift'], receipt },
            ],
            recorded_objective_acceptance: { status: 'unknown', head_freshness: 'not_assessed' },
            integrated_review: { status: 'not_assessed' },
          },
        })}
      />,
    );
    expect(screen.getByText('Remaining explicit gap')).toBeTruthy();
    expect(screen.getByText('Declared links do not establish criterion acceptance.')).toBeTruthy();
    await userEvent.click(screen.getByText('Evidence and source'));
    expect(screen.getByText('tools/test.py')).toBeTruthy();
    expect(screen.getByText('Native failure')).toBeTruthy();
    expect(screen.getByText('failed')).toBeTruthy();
    await userEvent.click(screen.getByText('Coverage source history'));
    expect(screen.getByText('Exact source drift')).toBeTruthy();
    expect(screen.getByText(/Head freshness: not assessed/i)).toBeTruthy();
  });
});
