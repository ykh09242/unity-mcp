import { describe, expect, it } from 'vitest';
import { actorLabel, originalValue, presentSnapshot, taskBody } from './presentation';
import { parseSnapshot, records } from './model';
import { attemptFacts, historyComparison } from './history';
import type { JsonRecord } from './model';
import { canonicalJson, presentationDigest } from './presentation-digest';
import { navigationSearch, emptyFilters } from './navigation';
import { selectTasks } from './selectors';
import { questionMatches, defaultQuestions } from './question-state';

const revision = 'captured-revision';
function entry(
  kind: string,
  entity: string,
  field: string,
  pointer: string,
  text: string,
  extra: JsonRecord = {},
): JsonRecord {
  const source =
    kind === 'actor_alias'
      ? entity
      : (
          {
            'objective.title': 'Original goal',
            'task_title.title': 'Original task',
            'question.prompt': 'Original prompt',
            'question.option': 'Original choice',
            'task_content.source_body': 'Full original purpose.\n- Keep this requirement.\n',
          } as Record<string, string>
        )[kind + '.' + field] || '';
  return {
    status: 'current',
    mode: 'translated',
    target: { kind, entity_id: entity, field },
    text,
    origin: { path: 'backlog.json', pointer, revision },
    ...extra,
    binding: {
      program_id: 'example',
      source_value_sha256: presentationDigest(source),
      context: {},
      ...((extra.binding as JsonRecord) || {}),
    },
  };
}
function fixture(entries: JsonRecord) {
  return parseSnapshot({
    snapshot_version: 1,
    revision,
    program_id: 'example',
    communication_language: 'ko',
    objective: { id: 'goal', title: 'Original goal', criteria: ['Keep every requirement'] },
    tasks: [
      {
        id: 'T1',
        title: 'Original task',
        status: 'active',
        owner: 'worker-a',
        source: {
          text: '### T1. Original task\nFull original purpose.\n- Keep this requirement.\n',
        },
        next_action: 'Check the source',
        attempts: [],
      },
    ],
    actors: [
      { id: 'worker-a', role: 'worker' },
      { id: 'worker-b', role: 'worker' },
    ],
    questions: [
      {
        id: 'Q1',
        version: 2,
        status: 'open',
        prompt: 'Original prompt',
        options: ['Original choice'],
        answer: { answer: 'Private saved original' },
      },
    ],
    presentation: { schema_version: 1, revision, locale: 'ko', entries },
  });
}

describe('source-bound presentation', () => {
  it.each([
    {
      originals: ['Original finding', 'Original finding'],
      labels: ['이전 검토 의견', '현재 검토 의견'],
      status: 'same',
    },
    {
      originals: ['Earlier finding', 'Changed finding'],
      labels: ['검토한 의견', '검토한 의견'],
      status: 'different',
    },
  ])(
    'compares canonical findings as $status while displaying both reviewed receipt narratives',
    ({ originals, labels, status }) => {
      const attempts = originals.map((finding, index) => ({
        number: index + 1,
        rounds: [
          {
            review: {
              path: `evidence/T1/attempt-${index + 1}/review.json`,
              sha256: `receipt-${index}`,
            },
            review_record: { unresolved_findings: [finding] },
          },
        ],
      }));
      const entries = Object.fromEntries(
        attempts.map((attempt, index) => [
          String(index),
          entry(
            'receipt_narrative',
            'T1',
            'unresolved_findings',
            '/unresolved_findings/0',
            labels[index]!,
            {
              target: {
                kind: 'receipt_narrative',
                entity_id: 'T1',
                field: 'unresolved_findings',
                record_pointer: '/unresolved_findings/0',
                source_path: attempt.rounds[0]!.review.path,
              },
              origin: { ...attempt.rounds[0]!.review, pointer: '/unresolved_findings/0' },
              binding: { source_value_sha256: presentationDigest(originals[index]!) },
            },
          ),
        ]),
      );
      const raw = fixture(entries);
      const view = presentSnapshot({ ...raw, tasks: [{ ...raw.tasks[0]!, attempts }] });
      const shown = records(view.tasks[0]!.attempts);
      const comparison = historyComparison(attemptFacts(shown[0]!), attemptFacts(shown[1]!), [
        'unresolved_findings',
      ])[0]!;
      expect(comparison.status).toBe(status);
      expect(comparison.before).toEqual([labels[0]]);
      expect(comparison.after).toEqual([labels[1]]);
      expect(originalValue(shown)).toEqual(attempts);
      expect(historyComparison(attemptFacts(shown[0]!), {}, ['unresolved_findings'])[0]).toEqual({
        field: 'unresolved_findings',
        before: [labels[0]],
        after: undefined,
        status: 'unknown',
      });
    },
  );
  it('joins the selected delivery to its exact historical reference rather than the last delivery', () => {
    const first = {
      attempt: 1,
      receipt: { path: 'evidence/T1/attempt-1/delivery.json', sha256: 'first' },
      cleanup: { reason: 'Keep this checkout' },
    };
    const last = {
      attempt: 2,
      receipt: { path: 'evidence/T1/attempt-2/delivery.json', sha256: 'last' },
      cleanup: { reason: 'Keep this checkout' },
    };
    const raw = fixture({
      cleanup: entry(
        'delivery',
        'T1',
        'reason',
        '/tasks/0/deliveries/0/cleanup/reason',
        '이 체크아웃을 유지해요',
        { binding: { source_value_sha256: presentationDigest('Keep this checkout') } },
      ),
    });
    const make = (delivery: JsonRecord) =>
      presentSnapshot({
        ...raw,
        tasks: [
          {
            ...raw.tasks[0]!,
            delivery,
            delivery_history: {
              status: 'recorded',
              items: [first, last].map((record, index) => ({
                record,
                reference: {
                  path: 'backlog.json',
                  revision,
                  pointer: `/tasks/0/deliveries/${index}`,
                },
              })),
            },
          },
        ],
      });
    const selectedFirst = make(first).tasks[0]!;
    expect(JSON.stringify(selectedFirst.delivery)).toContain('이 체크아웃을 유지해요');
    const selectedLast = make(last).tasks[0]!;
    expect(JSON.stringify(selectedLast.delivery)).not.toContain('이 체크아웃을 유지해요');
    expect(JSON.stringify(selectedLast.delivery_history)).toContain('이 체크아웃을 유지해요');
    expect(originalValue(selectedFirst.delivery)).toEqual(first);
  });

  it('keeps reordered options and changed source bodies original when captured value bindings no longer match', () => {
    const raw = fixture({
      option: entry(
        'question',
        'Q1',
        'option',
        '/questions/items/0/issued/options/0',
        '검토한 선택지',
        {
          target: { kind: 'question', entity_id: 'Q1', field: 'option', ordinal: 0 },
          binding: { context: { version: 2 } },
        },
      ),
      body: entry('task_content', 'T1', 'source_body', '/tasks/0/source/text', '검토한 전체 본문', {
        binding: { context: { projection_rule: 'section_after_first_heading_v1' } },
      }),
    });
    const changed = {
      ...raw,
      tasks: [
        {
          ...raw.tasks[0]!,
          source: { text: '### T1. Original task\nChanged source obligation.\n' },
        },
      ],
      questions: [
        { ...(raw.questions as JsonRecord[])[0], options: ['New first option', 'Original choice'] },
      ],
    };
    const view = presentSnapshot(changed);
    expect(taskBody(view.tasks[0]!)).toBe('Changed source obligation.\n');
    expect((view.questions as JsonRecord[])[0]!.options).toEqual([
      'New first option',
      'Original choice',
    ]);
  });
  it('searches original and displayed labels without indexing saved answers or merging duplicate aliases', () => {
    const view = presentSnapshot(
      fixture({
        task: entry('task_title', 'T1', 'title', '/tasks/0/source/title', '검토할 작업'),
        question: entry(
          'question',
          'Q1',
          'prompt',
          '/questions/items/0/issued/prompt',
          '확인할 질문',
        ),
        owner: entry('actor_alias', 'worker-a', 'label', '/actors/0/id', '공동 담당자', {
          mode: 'alias',
        }),
        other: entry('actor_alias', 'worker-b', 'label', '/actors/1/id', '공동 담당자', {
          mode: 'alias',
        }),
      }),
    );
    expect(selectTasks(view, { ...emptyFilters, search: '검토할' }).map((task) => task.id)).toEqual(
      ['T1'],
    );
    expect(
      selectTasks(view, { ...emptyFilters, search: 'Original task' }).map((task) => task.id),
    ).toEqual(['T1']);
    expect(navigationSearch(view, '공동 담당자').results.map((row) => row.owner)).toEqual([
      'worker-a',
      'worker-b',
    ]);
    expect(navigationSearch(view, 'Original prompt').results.map((row) => row.id)).toEqual(['Q1']);
    expect(navigationSearch(view, 'Private saved original').total).toBe(0);
    const question = (view.questions as JsonRecord[])[0]!;
    expect(questionMatches(question, { ...defaultQuestions, search: '확인할' })).toBe(true);
    expect(
      questionMatches(question, { ...defaultQuestions, search: 'Private saved original' }),
    ).toBe(false);
  });

  it('binds receipt paths, hashes and escaped pointers, and restores originals inside derived wrappers', () => {
    const receipt = { checks: { 'a/b': { summary: 'Original check' } } };
    const raw = fixture({
      note: entry('receipt_narrative', 'T1', 'summary', '/checks/a~1b/summary', '검증 설명', {
        target: {
          kind: 'receipt_narrative',
          entity_id: 'T1',
          field: 'summary',
          record_pointer: '/checks/a~1b/summary',
          source_path: 'evidence/T1/attempt-1/receipt.json',
        },
        origin: {
          path: 'evidence/T1/attempt-1/receipt.json',
          pointer: '/checks/a~1b/summary',
          sha256: 'exact',
        },
        binding: {
          program_id: 'example',
          source_value_sha256: presentationDigest('Original check'),
        },
      }),
    });
    const task = {
      ...raw.tasks[0]!,
      attempts: [
        {
          rounds: [
            {
              receipt: { path: 'evidence/T1/attempt-1/receipt.json', sha256: 'exact' },
              receipt_record: receipt,
            },
          ],
        },
      ],
    };
    const view = presentSnapshot({ ...raw, tasks: [task] });
    const actual = (view.tasks[0]!.attempts as JsonRecord[])[0]!;
    expect(JSON.stringify(actual)).toContain('검증 설명');
    expect(originalValue({ nested: [actual] })).toEqual({ nested: task.attempts });
    const wrong = presentSnapshot({
      ...raw,
      tasks: [
        {
          ...task,
          attempts: [
            {
              rounds: [
                {
                  receipt: { path: 'evidence/T1/attempt-1/receipt.json', sha256: 'different' },
                  receipt_record: receipt,
                },
              ],
            },
          ],
        },
      ],
    });
    expect(JSON.stringify(wrong.tasks)).not.toContain('검증 설명');
  });

  it('binds collision attention next action by its supplied task reference, not the compound entity ID', () => {
    const raw = fixture({
      action: entry('task_action', 'T1', 'next_action', '/tasks/0/next_action', '근거를 확인해요', {
        binding: {
          program_id: 'example',
          source_value_sha256: presentationDigest('Check the source'),
        },
      }),
    });
    const row = {
      kind: 'collision',
      entity_id: 'T1:T2',
      task_ids: ['T1', 'T2'],
      next_action: 'Check the source',
      reference: { path: 'backlog.json', pointer: '/tasks/0', revision, task_id: 'T1' },
    };
    const brief = {
      revision,
      program_id: raw.program_id,
      attention: { items: [row, { ...row, reference: { ...row.reference, task_id: 'missing' } }] },
    };
    const view = presentSnapshot({ ...raw, brief });
    expect(view.tasks[0]?.next_action).toBe('근거를 확인해요');
    expect(
      records(
        (view.brief as JsonRecord).attention &&
          ((view.brief as JsonRecord).attention as JsonRecord).items,
      )[0]?.next_action,
    ).toBe('근거를 확인해요');
    expect(
      records(((view.brief as JsonRecord).attention as JsonRecord).items)[1]?.next_action,
    ).toBe('Check the source');
    expect(raw.tasks[0]?.next_action).toBe('Check the source');
  });
  it('projects only the exact current uncovered record and preserves criterion and brief bounds', () => {
    const uncovered = {
      criterion: { ordinal: 0, text_sha256: 'criterion-sha' },
      inventory_digest: 'inventory',
      reason: 'No declared contributor',
    };
    const raw = fixture({
      reason: entry(
        'coverage',
        'annotation',
        'reason',
        '/coverage_annotations/1/annotation/uncovered/3/reason',
        '담당 작업을 연결해야 해요',
        {
          binding: {
            program_id: 'example',
            source_value_sha256: presentationDigest(uncovered.reason),
            context: { record_sha256: presentationDigest(uncovered) },
          },
        },
      ),
      criterion: entry(
        'objective_criterion',
        'goal',
        'text',
        '/objective/criteria/0',
        '합의한 요구 사항을 모두 보존해요',
        {
          target: { kind: 'objective_criterion', entity_id: 'goal', field: 'text', ordinal: 0 },
          binding: {
            program_id: 'example',
            source_value_sha256: presentationDigest('Keep every requirement'),
            context: { text_sha256: 'criterion-sha' },
          },
        },
      ),
    });
    const coverage = {
      history: [{ freshness: 'current' }, { freshness: 'current' }],
      criteria: [
        { criterion: uncovered.criterion, text: 'Keep every requirement', uncovered: [uncovered] },
      ],
    };
    const reference = { path: 'backlog.json', revision, pointer: '/objective' };
    const brief = {
      program_id: 'example',
      revision,
      objective: {
        reference,
        criteria: {
          total: 1,
          shown: 1,
          omitted: 0,
          items: [
            {
              ...uncovered.criterion,
              text: 'Keep every requirement',
              reference: { ...reference, pointer: '/objective/criteria/0' },
              uncovered: { total: 1, shown: 1, omitted: 0, items: [{ reason: uncovered.reason }] },
            },
          ],
        },
      },
    };
    const view = presentSnapshot({ ...raw, coverage, brief });
    expect(JSON.stringify(view.coverage)).toContain('담당 작업을 연결해야 해요');
    expect(JSON.stringify(view.brief)).toContain('합의한 요구 사항을 모두 보존해요');
    expect(JSON.stringify(view.brief)).toContain('담당 작업을 연결해야 해요');
    const stale = presentSnapshot({
      ...raw,
      coverage: { ...coverage, history: [{ freshness: 'current' }, { freshness: 'stale' }] },
    });
    expect(JSON.stringify(stale.coverage)).not.toContain('담당 작업을 연결해야 해요');
    const ambiguous = presentSnapshot({
      ...raw,
      coverage: { ...coverage, criteria: [...coverage.criteria, ...coverage.criteria] },
    });
    expect(JSON.stringify(ambiguous.coverage)).not.toContain('담당 작업을 연결해야 해요');
  });

  it('does not patch mismatched source values or an answer through a narrative-looking pointer', () => {
    const raw = fixture({
      wrong: entry('task_title', 'T1', 'title', '/tasks/0/source/title', 'Wrong', {
        binding: { program_id: 'example', source_value_sha256: presentationDigest('Other title') },
      }),
      answer: entry(
        'question_outcome',
        'Q1',
        'reason',
        '/questions/items/0/answer/reason',
        'Wrong',
        {
          binding: {
            program_id: 'example',
            source_value_sha256: presentationDigest('Private reason'),
          },
        },
      ),
    });
    const questions = [
      {
        ...(raw.questions as JsonRecord[])[0],
        answer: { answer: 'Private saved original', reason: 'Private reason' },
      },
    ];
    const view = presentSnapshot({ ...raw, questions });
    expect(view.tasks[0]!.title).toBe('Original task');
    expect(view.questions).toEqual(questions);
  });
  it('matches SHA-256 and canonical JSON across Unicode, integer keys and multiple blocks', () => {
    const canonical = '{\n  "10": "\\ud83d\\ude00",\n  "2": "\\ud55c\\uae00"\n}\n';
    expect(canonicalJson({ '2': '한글', '10': '😀' })).toBe(canonical);
    const expected = [
      'bd85bcdb8d4e613a79cb62d0903946ad10c83e63dc75f67614c159c0dbf4d184',
      'a4fb58ab9e76dcf8d9b2638c78dc2ba08b72447170604684d3f1adfb9842f950',
      '2bbb6233482330e3df4b11e90be048b0610fab6afff324e721f5e1c79d065900',
      'ba86a4bbe378cd8d97c162a53ee01983417b452544b5faa4e30dd3fd0a342807',
      '389ae517c57da95fdaa9fb98c805c026ea46c15dfea38abd2eedbde724286405',
    ]; // Independently generated by the producer's Python json/hashlib canonical recipe.
    [
      '',
      'abc',
      '한글😀',
      'x'.repeat(100000),
      {
        reason: 'Bound original',
        criterion: { ordinal: 2, text_sha256: 'abc' },
        inventory_digest: 'def',
      },
    ].forEach((value, index) => {
      expect(presentationDigest(value)).toBe(expected[index]);
    });
  });
  it('presents exact typed identities while retaining every original object and technical identity', () => {
    const raw = fixture({
      goal: entry('objective', 'goal', 'title', '/objective/title', '원래 목표를 보여줘요'),
      task: entry('task_title', 'T1', 'title', '/tasks/7/source/title', '작업 제목'),
      question: entry(
        'question',
        'Q1',
        'prompt',
        '/questions/items/5/issued/prompt',
        '어떤 범위를 적용할까요?',
        { binding: { program_id: 'example', context: { version: 2 } } },
      ),
      owner: entry('actor_alias', 'worker-a', 'label', '/actors/0/id', '담당자', { mode: 'alias' }),
      otherOwner: entry('actor_alias', 'worker-b', 'label', '/actors/1/id', '담당자', {
        mode: 'alias',
      }),
    });
    const before = JSON.stringify(raw),
      view = presentSnapshot(raw);
    expect(view.tasks[0]?.title).toBe('작업 제목');
    expect(view.tasks[0]?.id).toBe('T1');
    expect(view.tasks[0]?.owner).toBe('worker-a');
    expect(actorLabel(view, 'worker-a')).toBe(actorLabel(view, 'worker-b'));
    expect((view.questions as JsonRecord[])[0]?.prompt).toBe('어떤 범위를 적용할까요?');
    expect((view.questions as JsonRecord[])[0]?.answer).toEqual({
      answer: 'Private saved original',
    });
    expect(originalValue(view.tasks[0])).toBe(raw.tasks[0]);
    expect(JSON.stringify(originalValue(view))).toBe(before);
    expect(JSON.stringify(raw)).toBe(before);
    expect(presentSnapshot(raw)).toBe(view);
  });

  it('falls back for noncurrent, original, wrong-program and wrong-locale entries without reviving text', () => {
    for (const status of ['stale', 'withdrawn', 'unavailable', 'unknown']) {
      const raw = fixture({
        task: entry('task_title', 'T1', 'title', '/tasks/0/source/title', 'Must not display', {
          status,
        }),
      });
      expect(presentSnapshot(raw).tasks[0]?.title).toBe('Original task');
    }
    const wrongProgram = fixture({
      task: entry('task_title', 'T1', 'title', '/tasks/0/source/title', 'Wrong', {
        binding: { program_id: 'other' },
      }),
    });
    expect(presentSnapshot(wrongProgram).tasks[0]?.title).toBe('Original task');
    const otherLocale = fixture({
      task: entry('task_title', 'T1', 'title', '/tasks/0/source/title', 'Wrong'),
    });
    expect(presentSnapshot({ ...otherLocale, communication_language: 'en' }).tasks[0]?.title).toBe(
      'Original task',
    );
    const original = fixture({
      task: entry('task_title', 'T1', 'title', '/tasks/0/source/title', 'Wrong', {
        mode: 'original',
      }),
    });
    expect(presentSnapshot(original).tasks[0]?.title).toBe('Original task');
  });

  it('preserves the full source-body projection and rejects unknown extraction rules', () => {
    const full = '전체 목적을 확인해요.\n- 이 요구 사항을 보존해요.\n';
    const body = entry('task_content', 'T1', 'source_body', '/tasks/0/source/text', full, {
      binding: {
        program_id: 'example',
        context: { projection_rule: 'section_after_first_heading_v1' },
      },
    });
    const raw = fixture({ body }),
      view = presentSnapshot(raw);
    expect(taskBody(view.tasks[0]!)).toBe(full);
    expect(originalValue(view.tasks[0]!.source)).toEqual(raw.tasks[0]!.source);
    const invalid = fixture({
      body: {
        ...body,
        binding: { program_id: 'example', context: { projection_rule: 'unknown' } },
      },
    });
    expect(taskBody(presentSnapshot(invalid).tasks[0]!)).toContain('Keep this requirement');
  });

  it('never applies an option from a different issued question version or a technical-field target', () => {
    const raw = fixture({
      option: entry(
        'question',
        'Q1',
        'option',
        '/questions/items/0/issued/options/0',
        '잘못된 선택',
        {
          target: { kind: 'question', entity_id: 'Q1', field: 'option', ordinal: 0 },
          binding: { program_id: 'example', context: { version: 1 } },
        },
      ),
      answer: entry(
        'question',
        'Q1',
        'answer',
        '/questions/items/0/answer/answer',
        'Must not change',
      ),
      id: entry('task_action', 'T1', 'id', '/tasks/0/id', 'Must not change'),
    });
    const view = presentSnapshot(raw);
    expect((view.questions as JsonRecord[])[0]?.options).toEqual(['Original choice']);
    expect((view.questions as JsonRecord[])[0]?.answer).toEqual({
      answer: 'Private saved original',
    });
    expect(view.tasks[0]?.id).toBe('T1');
  });
});

describe('task body source-heading compatibility', () => {
  it.each([
    [
      '### T1. Canonical title\n- Goal: Keep first obligation.\n- Method: Keep following obligation.',
      '- Goal: Keep first obligation.\n- Method: Keep following obligation.',
    ],
    [
      '- Goal and expected result: Preserve exact authored context.\n- Source reference: original.md',
      '- Goal and expected result: Preserve exact authored context.\n- Source reference: original.md',
    ],
    [
      'Plain first paragraph.\n\nUnique following method.',
      'Plain first paragraph.\n\nUnique following method.',
    ],
    ['### T2. Different task\nKeep every word.', '### T2. Different task\nKeep every word.'],
    ['### Method notes\nKeep every word.', '### Method notes\nKeep every word.'],
    ['### T1. Canonical title', ''],
  ])(
    'removes only this task canonical heading and otherwise retains the exact source: %s',
    (text, expected) => {
      const task = { id: 'T1', title: 'Visible title', status: 'active', source: { text } };
      const before = JSON.stringify(task);
      expect(taskBody(task)).toBe(expected);
      expect(JSON.stringify(task)).toBe(before);
    },
  );
});
