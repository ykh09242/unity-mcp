import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { recordedCriterionResult } from './criterion-results';
import { LanguageProvider, PREFERENCE_KEY } from './i18n';
import { parseSnapshot, record } from './model';
import type { Json, JsonRecord, Snapshot } from './model';
import { CriterionCoverage, ProgramAttention } from './overview-context';
import { GoalCoordination } from './readiness-ui';
import { originalValue, presentSnapshot } from './presentation';
import { presentationDigest, textDigest } from './presentation-digest';

const criterionSha = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad';
const identity = { ordinal: 0, text_sha256: criterionSha };
const receipt = { path: 'evidence/completion.json', sha256: 'a'.repeat(64) };
const results = [
  { criterion: 'abc', status: 'passed', evidence: ['evidence/check.json', 'evidence/review.json'] },
];
function fixture(): Snapshot {
  const reference = { path: 'backlog.json', pointer: '', revision: 'revision' };
  const collection = (items: readonly Json[]) => ({
    items,
    total: items.length,
    shown: items.length,
    omitted: 0,
    reference,
  });
  return parseSnapshot({
    snapshot_version: 1,
    revision: 'revision',
    program_id: 'example',
    communication_language: 'ko',
    objective: {
      id: 'goal',
      criteria: ['abc'],
      task_ids: ['T1'],
      evidence: [receipt],
      completion: { objective_id: 'goal', criteria_results: results },
    },
    tasks: [{ id: 'T1', title: 'Completed task', status: 'done' }],
    coverage: {
      objective_id: 'goal',
      legacy_absence: true,
      history: [],
      criteria: [{ criterion: identity, text: 'abc', links: [], recorded_observations: [] }],
      recorded_objective_acceptance: {
        status: 'recorded',
        receipt,
        criteria_results: results,
        source_freshness: 'stale',
        head_freshness: 'not_assessed',
      },
    },
    brief: {
      brief_version: 1,
      revision: 'revision',
      program_id: 'example',
      reference,
      objective: {
        reference: { ...reference, pointer: '/objective' },
        criteria: collection([
          {
            ...identity,
            text: 'abc',
            reference: { ...reference, pointer: '/objective/criteria/0' },
            linkage: 'unknown',
            knowledge: 'unknown',
            contributors: collection([]),
            uncovered: collection([]),
          },
        ]),
      },
      attention: collection([
        {
          kind: 'question',
          entity_id: 'Q1',
          reason: 'Keep this authored warning',
          next_action: 'Inspect the decision',
          responsible_actor_id: 'director',
          task_ids: ['T1'],
          reference,
          observed_revision: 'revision',
          criterion_impact: collection([]),
        },
      ]),
    },
  });
}
function withCoverage(snapshot: Snapshot, patch: JsonRecord) {
  return parseSnapshot({ ...snapshot, coverage: { ...record(snapshot.coverage), ...patch } });
}
function withAcceptance(snapshot: Snapshot, patch: JsonRecord) {
  return withCoverage(snapshot, {
    recorded_objective_acceptance: {
      ...record(record(snapshot.coverage).recorded_objective_acceptance),
      ...patch,
    },
  });
}
function withObjective(snapshot: Snapshot, patch: JsonRecord) {
  return parseSnapshot({ ...snapshot, objective: { ...record(snapshot.objective), ...patch } });
}

describe('source-bound recorded criterion results', () => {
  it('uses exact UTF-8 text identity and retains supplied historical freshness and evidence', () => {
    expect(textDigest('abc')).toBe(criterionSha);
    const snapshot = fixture();
    expect(recordedCriterionResult(snapshot, identity)).toEqual({
      result: results[0],
      receipt,
      sourceFreshness: 'stale',
      headFreshness: 'not_assessed',
    });
    expect(
      recordedCriterionResult(snapshot, { ...identity, text_sha256: textDigest('ABC') }),
    ).toBeNull();
    expect(recordedCriterionResult(snapshot, { ...identity, ordinal: -1 })).toBeNull();
  });
  it.each([
    ['missing completion', (s: Snapshot) => withObjective(s, { completion: null })],
    ['another goal', (s: Snapshot) => withObjective(s, { id: 'new-goal' })],
    [
      'another completion goal',
      (s: Snapshot) =>
        withObjective(s, { completion: { objective_id: 'old-goal', criteria_results: results } }),
    ],
    ['missing receipt', (s: Snapshot) => withObjective(s, { evidence: [] })],
    [
      'duplicate source receipt',
      (s: Snapshot) => withObjective(s, { evidence: [receipt, receipt] }),
    ],
    [
      'duplicate original criterion',
      (s: Snapshot) => withObjective(s, { criteria: ['abc', 'abc'] }),
    ],
    [
      'mismatched receipt path',
      (s: Snapshot) => withAcceptance(s, { receipt: { ...receipt, path: 'evidence/other.json' } }),
    ],
    [
      'mismatched receipt hash',
      (s: Snapshot) => withAcceptance(s, { receipt: { ...receipt, sha256: 'b'.repeat(64) } }),
    ],
    ['unrecorded acceptance', (s: Snapshot) => withAcceptance(s, { status: 'unknown' })],
    [
      'divergent projected verdict',
      (s: Snapshot) =>
        withAcceptance(s, { criteria_results: [{ ...results[0], status: 'failed' }] }),
    ],
    ['changed original criterion', (s: Snapshot) => withObjective(s, { criteria: ['abc '] })],
  ])('rejects %s even when its tasks are done', (_name, mutate) => {
    expect(recordedCriterionResult(mutate(fixture()), identity)).toBeNull();
  });
  it('rejects duplicate or mismatched exact result rows', () => {
    for (const rows of [[...results, ...results], [{ ...results[0], criterion: 'ABC' }]]) {
      const snapshot = withAcceptance(
        withObjective(fixture(), {
          completion: { objective_id: 'goal', criteria_results: rows },
        }),
        { criteria_results: rows },
      );
      expect(recordedCriterionResult(snapshot, identity)).toBeNull();
    }
  });
  it('matches originals even when a current translation changes the visible criterion', () => {
    const raw = parseSnapshot({
      ...fixture(),
      presentation: {
        schema_version: 1,
        revision: 'revision',
        locale: 'ko',
        entries: {
          criterion: {
            status: 'current',
            mode: 'translated',
            text: '번역된 완료 기준',
            target: { kind: 'objective_criterion', entity_id: 'goal', field: 'text', ordinal: 0 },
            origin: {
              path: 'backlog.json',
              pointer: '/objective/criteria/0',
              revision: 'revision',
            },
            binding: {
              program_id: 'example',
              source_value_sha256: presentationDigest('abc'),
              context: { text_sha256: criterionSha },
            },
          },
        },
      },
    });
    const shown = presentSnapshot(raw);
    expect(record(shown.objective).criteria).toEqual(['번역된 완료 기준']);
    expect(recordedCriterionResult(shown, identity)?.result.status).toBe('passed');
    expect(record(originalValue(shown.objective)).criteria).toEqual(['abc']);
    expect(record(raw.objective).criteria).toEqual(['abc']);
  });
  it.each(['ko', 'en'])(
    'foregrounds the actual historical result and keeps annotations and evidence in %s details',
    async (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      render(
        <LanguageProvider>
          <CriterionCoverage snapshot={fixture()} />
        </LanguageProvider>,
      );
      const result = screen.getByText(
        locale === 'ko' ? '기록된 완료 판정: 통과' : 'Recorded completion result: passed',
      );
      expect(result.closest('details')).toBeNull();
      const summary = screen.getByText(locale === 'ko' ? '검토 근거' : 'Review evidence');
      const details = summary.closest('details');
      expect(details).not.toBeNull();
      if (!details) throw new Error('Review evidence is not a disclosure');
      expect(
        within(details).getByText(
          locale === 'ko' ? '기준 연결 기록이 없어요.' : 'No coverage annotation recorded.',
        ),
      ).toBeTruthy();
      expect(screen.queryByText(/^(Linkage|연결 상태):/)).toBeNull();
      await userEvent.click(summary);
      expect(details.open).toBe(true);
      expect(
        within(details)
          .getByRole('link', { name: 'evidence/completion.json' })
          .getAttribute('href'),
      ).toBe('#evidence/evidence%2Fcompletion.json');
      expect(within(details).getByRole('link', { name: 'evidence/review.json' })).toBeTruthy();
      expect(
        within(details).getByText(
          locale === 'ko'
            ? '과거 완료 기록이며 현재 승인 상태를 새로 판정하지 않아요.'
            : 'Historical completion record; current acceptance is not inferred.',
        ),
      ).toBeTruthy();
    },
  );
  it('keeps absent annotations explicit without inventing completion from done tasks', () => {
    localStorage.setItem(PREFERENCE_KEY, 'en');
    render(
      <LanguageProvider>
        <CriterionCoverage snapshot={withObjective(fixture(), { completion: null })} />
      </LanguageProvider>,
    );
    expect(
      screen.getByText('Explicit task links: No coverage annotation recorded.').closest('details'),
    ).toBeNull();
    expect(screen.queryByText(/Recorded completion result: passed/)).toBeNull();
  });
  it.each(['ko', 'en'])(
    'removes duplicate summary-original panels while retaining authored facts and actual evidence access in %s',
    (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const snapshot = fixture();
      const before = JSON.stringify(snapshot);
      const view = render(
        <LanguageProvider>
          <ProgramAttention snapshot={snapshot} />
          <GoalCoordination snapshot={snapshot} />
        </LanguageProvider>,
      );
      expect(screen.getAllByText('Keep this authored warning')).toHaveLength(2);
      expect(screen.getAllByText('Inspect the decision')).toHaveLength(2);
      expect(view.container.querySelector('a[href="#question/Q1"]')).toBeTruthy();
      const labels = Array.from(
        view.container.querySelectorAll('summary'),
        (element) => element.textContent,
      );
      for (const label of [
        'Summary scope and source',
        'Original program summary',
        'Original coordination context',
        'Original coordination summary',
        '요약 범위와 원본',
        '프로그램 요약 원본',
        '협업 맥락 원본',
        '협업 요약 원본',
      ])
        expect(labels).not.toContain(label);
      // The deliberate context disclosure keeps its diagnostic record, rather than deleting all originals.
      expect(view.container.querySelector('.original-record')).toBeTruthy();
      expect(JSON.stringify(snapshot)).toBe(before);
    },
  );
});
