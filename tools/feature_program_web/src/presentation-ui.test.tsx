import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { parseSnapshot } from './model';
import { presentationDigest } from './presentation-digest';
import { PREFERENCE_KEY } from './i18n';
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
    const summary = await screen.findByText('Full task description');
    await userEvent.setup().click(summary);
    const human = summary.closest('details')!;
    expect(human.open).toBe(true);
    expect(human.textContent).toContain('완료 조건: 모든 항목을 보존해요.');
    expect(human.querySelector('details')).toBeNull();
    expect(human.querySelector('pre')).toBeNull();
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
    expect(
      document.querySelector('.content-originals, .content-language, .original-record'),
    ).toBeNull();
    expect(screen.queryByText('Original option')).toBeNull();
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
