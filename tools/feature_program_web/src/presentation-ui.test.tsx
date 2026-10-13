import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { parseSnapshot, record } from './model';
import { presentationDigest } from './presentation-digest';
import { LanguageProvider, PREFERENCE_KEY } from './i18n';
import { ContentLanguage } from './presentation-ui';
import type { AnswerSaver } from './questions';

function fixture(option = '검토한 선택지', state = 'current') {
  const entry = (field: string, original: string, translated: string, ordinal?: number) => ({
    target: {
      kind: 'question',
      entity_id: 'Q1',
      field,
      ...(ordinal === undefined ? {} : { ordinal }),
    },
    origin: {
      path: 'backlog.json',
      pointer:
        field === 'option'
          ? '/questions/items/0/issued/options/0'
          : '/questions/items/0/issued/prompt',
      revision: 'one',
    },
    binding: {
      program_id: 'language-test',
      source_value_sha256: presentationDigest(original),
      context: { version: 1 },
    },
    status: state,
    mode: 'translated',
    text: translated,
  });
  return parseSnapshot({
    snapshot_version: 1,
    revision: 'one',
    program_id: 'language-test',
    communication_language: 'ko',
    tasks: [],
    questions: [
      {
        id: 'Q1',
        version: 1,
        status: 'open',
        prompt: 'Original question',
        options: ['Original option'],
        affected_task_ids: [],
      },
    ],
    presentation: {
      schema_version: 1,
      revision: 'one',
      locale: 'ko',
      entries: {
        prompt: entry('prompt', 'Original question', '확인할 범위를 골라주세요'),
        option: entry('option', 'Original option', option, 0),
      },
    },
  });
}
const sessionLoader = async () => ({ responsesEnabled: true, nonce: 'local-test-nonce' });
describe('content review coverage', () => {
  it.each(['en', 'ko'] as const)(
    'distinguishes supplied fallback and original modes in %s',
    (locale) => {
      localStorage.setItem(PREFERENCE_KEY, locale);
      const raw = fixture();
      const snapshot = parseSnapshot({
        ...raw,
        presentation: {
          ...record(raw.presentation),
          totals: {
            total: 36,
            current_translated: 1,
            current_original: 2,
            original_only: 3,
            missing: 4,
            stale: 5,
            withdrawn: 6,
            unavailable: 15,
          },
          periods: { current: { total: 10 }, historical: { total: 26 } },
        },
      });
      const { container } = render(
        <LanguageProvider>
          <ContentLanguage snapshot={snapshot} />
        </LanguageProvider>,
      );
      fireEvent.click(
        screen.getByText(locale === 'ko' ? '내용 언어: 한국어' : 'Content language: Korean'),
      );
      const expected =
        locale === 'ko'
          ? [
              '번역·별칭',
              '원문 유지',
              '원문 전용',
              '검토 누락',
              '검토 갱신 필요',
              '검토 철회',
              '원문 확인 불가',
              '현재 기록',
              '보존된 이력',
            ]
          : [
              'Translated or aliased',
              'Original retained',
              'Original only',
              'Missing review',
              'Stale review',
              'Withdrawn review',
              'Source unavailable',
              'Current records',
              'Retained history',
            ];
      expected.forEach((label, index) => {
        const term = screen.getByText(label);
        expect(term.tagName).toBe('DT');
        expect(term.nextElementSibling?.textContent).toBe(
          String([1, 2, 3, 4, 5, 6, 15, 10, 26][index]),
        );
      });
      expect(container.querySelector('details')?.open).toBe(true);
    },
  );

  it.each(['revision', 'locale', 'missing totals'] as const)(
    'does not report compatible coverage for %s',
    (mismatch) => {
      localStorage.setItem(PREFERENCE_KEY, 'en');
      const raw = fixture();
      const snapshot = parseSnapshot({
        ...raw,
        presentation: {
          ...record(raw.presentation),
          ...(mismatch === 'revision'
            ? { revision: 'older' }
            : mismatch === 'locale'
              ? { locale: 'en' }
              : {}),
          ...(mismatch === 'missing totals'
            ? {}
            : { totals: { total: 9, current_translated: 0, current_original: 0, withdrawn: 9 } }),
          periods: { historical: { total: 9 } },
        },
      });
      render(
        <LanguageProvider>
          <ContentLanguage snapshot={snapshot} />
        </LanguageProvider>,
      );
      expect(screen.getByText('Content coverage is unavailable.')).toBeTruthy();
      expect(screen.queryByText('Withdrawn review')).toBeNull();
      expect(screen.queryByText('Retained history')).toBeNull();
    },
  );
});
function start() {
  localStorage.setItem(PREFERENCE_KEY, 'en');
  window.history.replaceState(null, '', '#question/Q1?qview=all&qselected=Q1');
}

describe('reviewed content and answer state', () => {
  it('opens the complete reviewed task body without automatically opening the duplicate technical source', async () => {
    start();
    window.history.replaceState(null, '', '#task/T1?scope=all&page=1&size=50&full=1');
    const raw = fixture();
    const body = 'Full original purpose.\n\nAcceptance: keep every item.\n';
    const reviewed = '전체 목적을 확인해요.\n\n완료 조건: 모든 항목을 보존해요.\n';
    const snapshot = parseSnapshot({
      ...raw,
      tasks: [
        { id: 'T1', title: 'Task', status: 'active', source: { text: '### T1. Task\n' + body } },
      ],
      presentation: {
        schema_version: 1,
        revision: 'one',
        locale: 'ko',
        entries: {
          body: {
            status: 'current',
            mode: 'translated',
            text: reviewed,
            target: { kind: 'task_content', entity_id: 'T1', field: 'source_body' },
            origin: { path: 'backlog.json', pointer: '/tasks/0/source/text', revision: 'one' },
            binding: {
              program_id: 'language-test',
              source_value_sha256: presentationDigest(body),
              context: { projection_rule: 'section_after_first_heading_v1' },
            },
          },
        },
      },
    });
    render(<App loader={async () => snapshot} sessionLoader={sessionLoader} />);
    const summary = await screen.findByText('Full task context and evidence');
    await userEvent.setup().click(summary);
    const human = summary.closest('details')!;
    expect(human.open).toBe(true);
    expect(human.textContent).toContain('완료 조건: 모든 항목을 보존해요.');
    const technical = human.querySelector('details')!;
    expect(technical.open).toBe(false);
    await userEvent.setup().click(technical.querySelector('summary')!);
    expect(technical.open).toBe(true);
    expect(JSON.parse(technical.querySelector('pre')!.textContent!)).toEqual({
      text: '### T1. Task\n' + body,
    });
  });
  it('keeps two equal actor aliases as separate workload rows with canonical destinations', async () => {
    start();
    window.history.replaceState(null, '', '#team?scope=all');
    const raw = fixture();
    const aliases = Object.fromEntries(
      ['worker-a', 'worker-b'].map((id) => [
        id,
        {
          status: 'current',
          mode: 'alias',
          text: '검토 담당자',
          target: { kind: 'actor_alias', entity_id: id, field: 'label' },
          origin: { path: 'backlog.json', pointer: '/actors', revision: 'one' },
          binding: { program_id: 'language-test', source_value_sha256: presentationDigest(id) },
        },
      ]),
    );
    const snapshot = parseSnapshot({
      ...raw,
      actors: [
        { id: 'worker-a', role: 'worker' },
        { id: 'worker-b', role: 'worker' },
      ],
      tasks: [
        { id: 'A', title: 'First work', status: 'active', owner: 'worker-a' },
        { id: 'B', title: 'Second work', status: 'active', owner: 'worker-b' },
      ],
      presentation: { schema_version: 1, revision: 'one', locale: 'ko', entries: aliases },
    });
    render(<App loader={async () => snapshot} sessionLoader={sessionLoader} />);
    const links = await screen.findAllByRole('link', { name: 'All 1 records for 검토 담당자' });
    expect(new Set(links.map((link) => link.getAttribute('href')))).toEqual(
      new Set([
        '#work?scope=all&owner=worker-a&page=1&size=50',
        '#work?scope=all&owner=worker-b&page=1&size=50',
      ]),
    );
  });
  it('keeps content language independent, scopes originals and preserves a focused draft across presentation-only reads', async () => {
    start();
    const user = userEvent.setup();
    let snapshot = fixture();
    render(<App loader={async () => snapshot} sessionLoader={sessionLoader} />);
    await screen.findByRole('heading', { name: '확인할 범위를 골라주세요' });
    expect(screen.getByRole('button', { name: '검토한 선택지' })).toBeDefined();
    expect(screen.queryByText('Original option')).toBeNull();
    const input = screen.getByLabelText<HTMLTextAreaElement>('Your answer');
    await user.type(input, 'Private unsent answer');
    input.setSelectionRange(3, 9);
    fireEvent.select(input);
    snapshot = fixture('새로 검토한 선택지');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(await screen.findByRole('button', { name: '새로 검토한 선택지' })).toBeDefined();
    expect(input.value).toBe('Private unsent answer');
    expect(document.activeElement).toBe(input);
    expect([input.selectionStart, input.selectionEnd]).toEqual([3, 9]);
    await user.selectOptions(screen.getByLabelText('Language'), 'ko');
    expect(screen.getByRole('heading', { name: '확인할 범위를 골라주세요' })).toBeDefined();
    expect(screen.getByLabelText<HTMLTextAreaElement>('내 답변').value).toBe(
      'Private unsent answer',
    );
    expect(window.location.hash).not.toContain('Private');
    expect(JSON.stringify(localStorage)).not.toContain('Private');
    expect(sessionStorage.length).toBe(0);
    const originals = document.querySelector<HTMLDetailsElement>(
      '.question-form .content-originals',
    );
    expect(originals).not.toBeNull();
    await user.click(originals!.querySelector('summary')!);
    const optionSource = [...originals!.querySelectorAll('details')].find((row) =>
      row.textContent?.includes('/options/0'),
    )!;
    await user.click(optionSource.querySelector('summary')!);
    expect(screen.getByText('Original option')).toBeDefined();
    expect(document.querySelector('.rail .content-originals')).toBeNull();
  });

  it('saves only the explicitly clicked visible option and preserves the exact retry tuple when presentation changes', async () => {
    start();
    const user = userEvent.setup();
    let snapshot = fixture();
    const saver = vi.fn<AnswerSaver>().mockResolvedValue({ ok: false, conflict: false });
    render(<App loader={async () => snapshot} sessionLoader={sessionLoader} answerSaver={saver} />);
    await user.click(await screen.findByRole('button', { name: '검토한 선택지' }));
    expect(screen.getByLabelText<HTMLTextAreaElement>('Your answer').value).toBe('검토한 선택지');
    await user.click(screen.getByRole('button', { name: 'Save answer' }));
    await waitFor(() => expect(saver).toHaveBeenCalledTimes(1));
    const first = saver.mock.calls[0]![0];
    expect(Object.keys(first).sort()).toEqual([
      'answer',
      'question_id',
      'submission_id',
      'version',
    ]);
    expect(first).toMatchObject({ answer: '검토한 선택지', question_id: 'Q1', version: 1 });
    snapshot = fixture('다른 검토 문구');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await screen.findByRole('button', { name: '다른 검토 문구' });
    expect(screen.getByLabelText<HTMLTextAreaElement>('Your answer').value).toBe('검토한 선택지');
    await user.click(screen.getByRole('button', { name: 'Save answer' }));
    await waitFor(() => expect(saver).toHaveBeenCalledTimes(2));
    expect(saver.mock.calls[1]![0]).toEqual(first);
    expect(JSON.stringify(localStorage)).not.toContain('검토한 선택지');
  });
});
