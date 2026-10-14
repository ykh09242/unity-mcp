import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from './App';
import { LanguageProvider, PREFERENCE_KEY } from './i18n';
import { CoverageObservations } from './history-ui';
import { parseSnapshot, record } from './model';
import { presentationDigest, textDigest } from './presentation-digest';

const audit = 'INTERNAL-AUDIT-ONLY';
const ref = {
  path: 'evidence/' + audit + '.json',
  sha256: 'a'.repeat(64),
  revision: audit,
  pointer: '/audit/' + audit,
};
function fixture(status = 'open') {
  const reference = { path: 'backlog.json', pointer: '', revision: 'revision' };
  const collection = (items: readonly unknown[]) => ({
    items,
    total: items.length,
    shown: items.length,
    omitted: 0,
    reference,
  });
  const results = [{ criterion: 'Keep useful behavior', status: 'passed', evidence: [ref] }];
  return parseSnapshot({
    snapshot_version: 1,
    revision: 'revision',
    program_id: 'provenance-fixture',
    communication_language: 'ko',
    integrity: { status: 'fresh', warnings: ['Operational warning retained'] },
    objective: {
      id: 'goal',
      title: 'Useful goal',
      criteria: ['Keep useful behavior'],
      task_ids: ['T1'],
      status: 'awaiting_next_goal',
      evidence: [ref],
      completion: { objective_id: 'goal', criteria_results: results },
      membership_history: [{ reason: audit, receipt: ref }],
    },
    tasks: [
      {
        id: 'T1',
        title: 'Useful task',
        status: 'review',
        owner: 'worker',
        next_action: 'Review the meaningful result',
        expected_result: 'Expected behavior retained',
        acceptance_criteria: ['Criterion wording retained'],
        source: {
          text: '### T1. Useful task\nPurpose paragraph.\n\n## Method\n- Preserve `src/retain.ts`\n- Keep original wording and sha256=authored',
        },
        notes: [
          {
            actor: 'worker',
            reason: 'Authored decision retained',
            next_action: 'Do the next useful step',
            evidence: [ref],
          },
        ],
        attempts: [
          {
            number: 1,
            owner: 'worker',
            assignment: {
              actor_id: 'worker',
              model_id: 'model-visible',
              reasoning_effort: 'high',
              provenance: audit,
            },
            scope: [audit],
            rounds: [
              {
                round: 1,
                submitted_head: audit,
                source_digest: audit,
                receipt: ref,
                review: ref,
                receipt_record: {
                  checks: [
                    {
                      name: 'Useful check',
                      status: 'passed',
                      result: 'Useful result',
                      evidence: [ref],
                    },
                  ],
                  acceptance_evidence: results,
                },
                review_record: {
                  reviewer: 'reviewer',
                  verdict: 'accepted',
                  unresolved_findings: ['Useful finding retained'],
                  reason: 'Recorded review reason',
                },
              },
            ],
          },
        ],
        delivery: {
          status: 'ci_recorded_success',
          receipt: ref,
          metadata: {
            pr_url: 'https://github.com/example/project/pull/1',
            merge_status: 'open',
            head_sha: audit,
            ci: [
              {
                name: 'Useful CI',
                conclusion: 'success',
                url: 'https://github.com/example/project/actions/runs/42',
                head_sha: audit,
                run_id: 42,
              },
            ],
            cleanup: {
              status: 'deferred',
              reason: 'Keep checkout for follow-up',
              merge_identity: audit,
              evidence: [ref],
            },
          },
        },
      },
    ],
    actors: [
      {
        id: 'worker',
        role: 'worker',
        model_id: 'model-visible',
        reasoning_effort: 'high',
        provenance: audit,
        observed_status: 'recorded',
        history: [
          {
            decision: 'reuse',
            reason: 'Useful staffing reason',
            metadata: { model_id: 'model-visible', provenance: audit },
            evidence: [ref],
          },
        ],
      },
    ],
    director: { actor_id: 'director', source_digest: audit, authority: { merge_target: 'main' } },
    responsibilities: { worker: ['Useful responsibility'], director: ['Retain useful authority'] },
    handoffs: [
      {
        id: 'handoff',
        from_actor: 'worker',
        to_actor: 'successor',
        outgoing: {
          goal: 'Handoff goal retained',
          constraints: ['Useful constraint'],
          unresolved_findings: ['Handoff finding retained'],
          next_action: 'Handoff next action',
          source_digest: audit,
        },
        outgoing_ref: ref,
      },
    ],
    questions: [
      {
        id: 'Q1',
        version: 1,
        status,
        prompt: 'Choose useful behavior',
        options: ['Useful choice'],
        why: 'Decision reason retained',
        requested_decision: 'Requested decision retained',
        affected_task_ids: ['T1'],
        goal_id: 'goal',
        source_head: audit,
        answer:
          status === 'open'
            ? null
            : { answer: 'Saved answer retained', saved_at: '2026-10-14T00:00:00Z' },
        acknowledgement:
          status === 'acknowledged' || status === 'resolved'
            ? {
                summary: 'Acknowledgement retained',
                actor_id: 'director',
                scope: ['T1'],
                source_head: audit,
              }
            : null,
        outcome:
          status === 'resolved'
            ? {
                kind: 'applied',
                summary: 'Outcome retained',
                actor_id: 'director',
                scope: ['T1'],
                source_head: audit,
              }
            : null,
      },
    ],
    coverage: {
      objective_id: 'goal',
      legacy_absence: true,
      recorded_objective_acceptance: {
        status: 'recorded',
        receipt: ref,
        criteria_results: results,
        source_freshness: 'current',
        head_freshness: 'not_assessed',
      },
      history: [{ receipt: ref, reasons: [audit] }],
    },
    brief: {
      brief_version: 1,
      revision: 'revision',
      program_id: 'provenance-fixture',
      reference,
      attention: collection([
        {
          kind: 'question',
          entity_id: 'Q1',
          reason: 'Attention reason retained',
          next_action: 'Attention action retained',
          responsible_actor_id: 'director',
          observed_revision: 'revision',
          reference,
          task_ids: ['T1'],
        },
      ]),
      objective: {
        criteria: collection([
          {
            ordinal: 0,
            text_sha256: textDigest('Keep useful behavior'),
            text: 'Keep useful behavior',
            reference,
            linkage: 'unknown',
            knowledge: 'unknown',
            contributors: collection([]),
            uncovered: collection([]),
          },
        ]),
      },
    },
    presentation: {
      schema_version: 1,
      revision: 'revision',
      locale: 'ko',
      entries: {
        title: {
          status: 'current',
          mode: 'translated',
          text: '읽기 쉬운 작업',
          target: { kind: 'task_title', entity_id: 'T1', field: 'title' },
          origin: { path: 'backlog.json', pointer: '/tasks/0/title', revision: 'revision' },
          binding: {
            program_id: 'provenance-fixture',
            source_value_sha256: presentationDigest('Useful task'),
            context: {},
          },
        },
      },
    },
  });
}
const routes = [
  '#overview',
  '#work?scope=all',
  '#task/T1?scope=all&full=1',
  '#team',
  '#questions?qview=all',
  '#question/Q1',
  '#evidence/evidence%2FINTERNAL-AUDIT-ONLY.json',
];
function noAudit(container: HTMLElement) {
  expect(container.textContent).not.toContain(audit);
  expect(
    container.querySelector(
      'pre, .original-record, .content-originals, .content-language, .history-comparison, .membership-history, .source-locator, a[href^="#evidence/"]',
    ),
  ).toBeNull();
  expect(
    Array.from(container.querySelectorAll('summary'), (node) => node.textContent).join(' '),
  ).not.toMatch(
    /Original text and sources|Context and source|Coverage source history|Submitted revision and scope|원문과 출처|맥락과 원본|목표 및 원문 이력|내용 언어/,
  );
}
describe('operational UI without provenance presentation', () => {
  it.each(['en', 'ko'])(
    'removes nested audit areas across every primary and detail route while retaining semantic content with %s UI',
    async (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const snapshot = fixture();
      const before = JSON.stringify(snapshot);
      for (const route of routes) {
        window.history.replaceState(null, '', route);
        const view = render(
          <App
            loader={async () => snapshot}
            sessionLoader={async () => ({ responsesEnabled: true, nonce: 'test' })}
          />,
        );
        await waitFor(() => expect(view.container.querySelector('.workspace')).not.toBeNull());
        await waitFor(() =>
          expect(view.container.textContent).toContain(
            route.startsWith('#evidence')
              ? locale === 'ko'
                ? '기록을 표시할 수 없어요'
                : 'Record unavailable'
              : route.startsWith('#task') || route.startsWith('#work')
                ? '읽기 쉬운 작업'
                : route.startsWith('#question')
                  ? 'Choose useful behavior'
                  : route.startsWith('#team')
                    ? 'Useful staffing reason'
                    : 'Useful goal',
          ),
        );
        // Closed nested sections are inspected too; remove the actual presentation, not just visible labels.
        noAudit(view.container);
        if (route.startsWith('#task')) {
          expect(view.container.textContent).toContain('Useful finding retained');
          expect(
            view.container.querySelector('a[href="https://github.com/example/project/pull/1"]'),
          ).toBeTruthy();
          expect(view.container.querySelector('.task-description code')?.textContent).toBe(
            'src/retain.ts',
          );
        }
        if (route === '#question/Q1') expect(screen.getByRole('textbox').tagName).toBe('TEXTAREA');
        view.unmount();
      }
      expect(JSON.stringify(snapshot)).toBe(before);
      expect(record(snapshot.presentation).entries).toBeTruthy();
    },
  );
  it.each(['answered', 'acknowledged', 'resolved'])(
    'retains %s answers and useful decisions while removing source response-history presentation',
    async (status) => {
      localStorage.setItem(PREFERENCE_KEY, 'en');
      window.history.replaceState(null, '', '#question/Q1');
      const snapshot = fixture(status);
      const view = render(<App loader={async () => snapshot} />);
      await screen.findByText('Saved answer retained');
      noAudit(view.container);
      if (status === 'resolved') expect(screen.getByText('Outcome retained')).toBeTruthy();
    },
  );
  it('preserves semantic nested criterion/check findings while excluding their internal evidence references', () => {
    const value = [
      {
        task_id: 'T1',
        receipt: ref,
        facts: {
          criteria_results: [
            {
              criterion: 'Nested criterion retained',
              status: 'passed',
              evidence: [ref],
              sha256: audit,
            },
          ],
          checks: [
            {
              name: 'Nested check retained',
              status: 'failed',
              reason: 'Failure reason retained',
              evidence: [ref],
            },
          ],
        },
      },
    ];
    const before = JSON.stringify(value);
    const view = render(
      <LanguageProvider>
        <CoverageObservations value={value} />
      </LanguageProvider>,
    );
    expect(screen.getByText('Nested criterion retained')).toBeTruthy();
    expect(screen.getByText('Nested check retained')).toBeTruthy();
    expect(screen.getByText('Failure reason retained')).toBeTruthy();
    noAudit(view.container);
    expect(JSON.stringify(value)).toBe(before);
  });
});
