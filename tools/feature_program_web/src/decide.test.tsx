import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { parseSnapshot, records } from './model';
import { createQuestionDrafts, questionDraftKey } from './question-drafts';
import { defaultQuestions, questionMatches, questionStage } from './question-state';
import {
  defaultContext,
  defaultPreferences,
  navigationHash,
  parseNavigation,
  writePreferences,
} from './navigation';
import type { AnswerResult, AnswerSaver } from './questions';
import { saveAnswer } from './questions';
import { SnapshotResponse } from './useSnapshot';

const snapshot = () =>
  parseSnapshot({
    snapshot_version: 1,
    revision: 'one',
    program_id: 'decision-test',
    objective: { id: 'goal-one', title: 'First goal', task_ids: ['A'] },
    tasks: [{ id: 'A', title: 'Affected work', status: 'active' }],
    questions: [
      {
        id: 'Q1',
        version: 1,
        status: 'open',
        prompt: 'Choose the first scope',
        why: 'Authored reason',
        requested_decision: 'Choose scope',
        objective_id: 'goal-one',
        affected_task_ids: ['A'],
        options: Array.from({ length: 10 }, (_, index) => `Authored option ${index + 1}`),
      },
      {
        id: 'Q2',
        version: 1,
        status: 'open',
        prompt: 'Choose the second scope',
        objective_id: 'goal-two',
        affected_task_ids: [],
      },
      {
        id: 'Q3',
        version: 1,
        status: 'resolved',
        prompt: 'Previous decision',
        objective_id: 'goal-one',
        affected_task_ids: ['A'],
        outcome: { kind: 'withdrawn', summary: 'No work was applied.' },
      },
    ],
  });
const sessionLoader = async () => ({ responsesEnabled: true, nonce: 'test-private-nonce' });
const initial = () =>
  window.history.replaceState(
    null,
    '',
    '#questions?scope=all&status=active&page=1&size=25&qview=all',
  );

describe('complete question navigation', () => {
  it('preserves the open question Filters disclosure across a detail round trip', async () => {
    initial();
    const user = userEvent.setup();
    render(<App loader={async () => snapshot()} sessionLoader={sessionLoader} />);
    await screen.findByRole('heading', { name: 'Questions' });
    const filters = document.querySelector<HTMLDetailsElement>('.question-filters');
    if (!filters) throw new Error('Missing filters');
    await user.click(filters.querySelector('summary')!);
    expect(filters.open).toBe(true);
    await user.click(screen.getByRole('link', { name: /Choose the first scope/ }));
    await user.click(screen.getByRole('link', { name: 'Back to questions' }));
    expect(document.querySelector<HTMLDetailsElement>('.question-filters')?.open).toBe(true);
    expect(document.activeElement).toBe(
      screen.getByRole('link', { name: /Choose the first scope/ }),
    );
  });
  it('does not move focus when a departed question finishes saving', async () => {
    initial();
    const user = userEvent.setup();
    let settle: ((result: AnswerResult) => void) | undefined;
    const saver = vi.fn<AnswerSaver>(
      () =>
        new Promise((resolve) => {
          settle = resolve;
        }),
    );
    render(
      <App loader={async () => snapshot()} sessionLoader={sessionLoader} answerSaver={saver} />,
    );
    await user.click(await screen.findByRole('link', { name: /Choose the first scope/ }));
    await user.type(screen.getByLabelText('Your answer'), 'Answer first');
    await user.click(screen.getByRole('button', { name: 'Save answer' }));
    await user.click(screen.getByRole('link', { name: 'Back to questions' }));
    await user.click(screen.getByRole('link', { name: /Choose the second scope/ }));
    const input = screen.getByLabelText<HTMLTextAreaElement>('Your answer');
    await user.type(input, 'Still writing second');
    input.setSelectionRange(3, 8);
    fireEvent.select(input);
    await act(async () => {
      settle?.({ ok: true, conflict: false });
    });
    expect(document.activeElement).toBe(input);
    expect([input.selectionStart, input.selectionEnd]).toEqual([3, 8]);
    expect(input.value).toBe('Still writing second');
    expect(screen.queryByText('Answer first')).toBeNull();
  });
  it('retains draft and work filters through affected-task reverse links and goal relevance', async () => {
    initial();
    const user = userEvent.setup();
    render(<App loader={async () => snapshot()} sessionLoader={sessionLoader} />);
    await user.click(await screen.findByRole('link', { name: /Choose the first scope/ }));
    await user.type(screen.getByLabelText('Your answer'), 'Keep across work context');
    await user.click(screen.getByRole('link', { name: 'A' }));
    await screen.findByRole('heading', { name: 'Affected work' });
    expect(parseNavigation(window.location.hash).filters.status).toBe('active');
    expect(screen.queryByRole('link', { name: /Choose the second scope/ })).toBeNull();
    await user.click(screen.getByRole('link', { name: /Choose the first scope/ }));
    expect(screen.getByDisplayValue('Keep across work context')).toBeDefined();
    await user.click(screen.getByRole('link', { name: 'goal-one' }));
    expect(screen.getByLabelText<HTMLSelectElement>('Goal').value).toBe('goal-one');
    expect(screen.queryByRole('link', { name: /Choose the second scope/ })).toBeNull();
    expect(screen.getByRole('link', { name: /Previous decision/ })).toBeDefined();
  });
  it('shows a genuine empty question list and a truthful unknown detail without a write action', async () => {
    initial();
    const user = userEvent.setup();
    let model = parseSnapshot({ ...snapshot(), questions: [] });
    render(<App loader={async () => model} sessionLoader={sessionLoader} />);
    await screen.findByText('No questions have been recorded.');
    model = parseSnapshot({
      ...snapshot(),
      questions: [{ id: 'future', prompt: 'Unrecognized lifecycle', status: 'future', version: 1 }],
    });
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await user.click(screen.getByRole('link', { name: /Unrecognized lifecycle/ }));
    expect(screen.getByText('Unknown question state')).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Save answer' })).toBeNull();
    expect(screen.queryByText('Applied outcome')).toBeNull();
  });
  it('keeps explicit question filters on startup even when work has a saved preset', async () => {
    writePreferences('decision-test', {
      ...defaultPreferences,
      selectedPreset: 'Active work',
      presets: [{ name: 'Active work', hash: '#work?scope=all&status=active&page=1&size=25' }],
    });
    window.history.replaceState(null, '', '#questions?qview=history&qgoal=goal-one');
    render(<App loader={async () => snapshot()} sessionLoader={sessionLoader} />);
    await screen.findByRole('heading', { name: 'Questions' });
    expect(
      screen.getByRole('button', { name: /Question history/ }).getAttribute('aria-pressed'),
    ).toBe('true');
    expect(screen.getByLabelText<HTMLSelectElement>('Goal').value).toBe('goal-one');
    expect(screen.getByRole('link', { name: /Previous decision/ })).toBeDefined();
    expect(screen.queryByRole('link', { name: /Choose the first scope/ })).toBeNull();
    await userEvent.click(screen.getByRole('link', { name: 'Work' }));
    expect(parseNavigation(window.location.hash).filters.status).toBe('active');
    await userEvent.click(screen.getByRole('link', { name: 'Questions 2' }));
    expect(
      screen.getByRole('button', { name: /Question history/ }).getAttribute('aria-pressed'),
    ).toBe('true');
    expect(screen.getByLabelText<HTMLSelectElement>('Goal').value).toBe('goal-one');
  });
  it('keeps a removed question draft discoverable from the list without inventing a current question', async () => {
    initial();
    const user = userEvent.setup();
    let model = snapshot();
    render(<App loader={async () => model} sessionLoader={sessionLoader} />);
    await user.click(await screen.findByRole('link', { name: /Choose the first scope/ }));
    await user.type(screen.getByLabelText('Your answer'), 'Keep my removed draft');
    await user.click(screen.getByRole('link', { name: 'Back to questions' }));
    model = parseSnapshot({
      ...model,
      questions: records(model.questions).filter((q) => q.id !== 'Q1'),
    });
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await user.click(screen.getByRole('link', { name: 'Unsent draft · Q1' }));
    expect(screen.getByRole('heading', { name: 'Question not available' })).toBeDefined();
    expect(screen.getByLabelText<HTMLTextAreaElement>('Your draft').value).toBe(
      'Keep my removed draft',
    );
    expect(screen.queryByRole('button', { name: 'Save answer' })).toBeNull();
  });
  it('requires explicit version review after route unmount and clears memory for a fresh document', async () => {
    initial();
    const user = userEvent.setup();
    let model = snapshot();
    const saver = vi.fn<AnswerSaver>(async () => ({ ok: false, conflict: true }));
    const view = render(
      <App loader={async () => model} sessionLoader={sessionLoader} answerSaver={saver} />,
    );
    await user.click(await screen.findByRole('link', { name: /Choose the first scope/ }));
    await user.type(screen.getByLabelText('Your answer'), 'Version bound draft');
    await user.click(screen.getByRole('button', { name: 'Save answer' }));
    await screen.findByRole('alert');
    await user.click(screen.getByRole('link', { name: 'Back to questions' }));
    // Synthetic drift: canonical wording revisions use a new identity; defensive same-ID version handling is explicit.
    model = parseSnapshot({
      ...model,
      questions: records(model.questions).map((q) =>
        q.id === 'Q1' ? { ...q, version: 2, prompt: 'Revised scope' } : q,
      ),
    });
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await user.click(screen.getByRole('link', { name: /Revised scope/ }));
    expect(screen.getByDisplayValue('Version bound draft')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Save answer' }).hasAttribute('disabled')).toBe(true);
    expect(saver).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Use current question version' }));
    await user.click(screen.getByRole('button', { name: 'Save answer' }));
    expect(saver.mock.calls[1]?.[0]).toMatchObject({ version: 2, answer: 'Version bound draft' });
    expect(saver.mock.calls[1]?.[0].submission_id).not.toBe(saver.mock.calls[0]?.[0].submission_id);
    view.unmount();
    render(<App loader={async () => model} sessionLoader={sessionLoader} />);
    expect((await screen.findByLabelText<HTMLTextAreaElement>('Your answer')).value).toBe('');
    expect(JSON.stringify(localStorage)).not.toContain('Version bound');
    expect(sessionStorage.length).toBe(0);
  });
  it('retains the focused draft and disclosures through conditional, changed and failed reads, then exposes a removed draft', async () => {
    initial();
    const user = userEvent.setup();
    let response: unknown = snapshot();
    let failure = false;
    const loader = vi.fn(async () => {
      if (failure) throw new Error('Temporary read failure');
      return response;
    });
    render(<App loader={loader} sessionLoader={sessionLoader} />);
    await user.click(await screen.findByRole('link', { name: /Choose the first scope/ }));
    const input = screen.getByLabelText<HTMLTextAreaElement>('Your answer');
    const help = screen.getByText('About answers and drafts').closest('details');
    if (!help) throw new Error('Missing draft help');
    help.open = true;
    await user.type(input, 'Private draft during outage');
    input.setSelectionRange(4, 10);
    fireEvent.select(input);
    for (const next of [
      new SnapshotResponse(304, 'one'),
      snapshot(),
      { ...snapshot(), revision: 'two' },
      { ...snapshot(), tasks: [{ id: 'A', title: 'Changed same revision', status: 'active' }] },
    ]) {
      response = next;
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'));
      });
      expect(document.activeElement).toBe(input);
      expect(input.value).toBe('Private draft during outage');
      expect([input.selectionStart, input.selectionEnd]).toEqual([4, 10]);
      expect(help.open).toBe(true);
    }
    failure = true;
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe('Private draft during outage');
    failure = false;
    response = snapshot();
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(document.activeElement).toBe(input);
    response = { ...snapshot(), questions: [] };
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(screen.getByRole('heading', { name: 'Question not available' })).toBeDefined();
    expect(screen.getByLabelText<HTMLTextAreaElement>('Your draft').value).toBe(
      'Private draft during outage',
    );
    expect(screen.queryByRole('button', { name: 'Save answer' })).toBeNull();
  });
  it('does not downgrade a resolved snapshot when confirming an uncertain historical answer receipt', async () => {
    initial();
    const user = userEvent.setup();
    let model = snapshot();
    const saver = vi
      .fn<AnswerSaver>()
      .mockRejectedValueOnce(new Error('Lost reply'))
      .mockResolvedValue({ ok: true, conflict: false });
    render(<App loader={async () => model} sessionLoader={sessionLoader} answerSaver={saver} />);
    await user.click(await screen.findByRole('link', { name: /Choose the first scope/ }));
    await user.type(screen.getByLabelText('Your answer'), 'Keep exact payload');
    await user.click(screen.getByRole('button', { name: 'Save answer' }));
    await screen.findByRole('alert');
    model = parseSnapshot({
      ...model,
      questions: records(model.questions).map((q) =>
        q.id === 'Q1'
          ? {
              ...q,
              status: 'resolved',
              acknowledgement: { summary: 'Read by director' },
              outcome: { kind: 'applied', summary: 'Recorded action' },
            }
          : q,
      ),
    });
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(screen.getByDisplayValue('Keep exact payload')).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Check save confirmation' }));
    expect(saver.mock.calls[1]?.[0]).toEqual(saver.mock.calls[0]?.[0]);
    expect(screen.getByText('Applied outcome')).toBeDefined();
    expect(screen.queryByText('Answer saved. Waiting for director acknowledgement.')).toBeNull();
  });
  it('keeps independent drafts, caret and question/work context across list, other question and back-forward', async () => {
    initial();
    const user = userEvent.setup();
    render(<App loader={async () => snapshot()} sessionLoader={sessionLoader} />);
    await user.click(await screen.findByRole('link', { name: /Choose the first scope/ }));
    const input = screen.getByLabelText<HTMLTextAreaElement>('Your answer');
    await user.type(input, 'Private first answer');
    input.setSelectionRange(3, 8);
    fireEvent.select(input);
    const first = window.location.hash;
    await user.click(screen.getByRole('link', { name: 'Back to questions' }));
    const list = window.location.hash;
    expect(parseNavigation(list).filters.status).toBe('active');
    expect(parseNavigation(list).paging.size).toBe(25);
    await user.click(screen.getByRole('link', { name: /Choose the second scope/ }));
    await user.type(screen.getByLabelText('Your answer'), 'Private second answer');
    await user.click(screen.getByRole('link', { name: 'Back to questions' }));
    await user.click(screen.getByRole('link', { name: /Choose the first scope/ }));
    const restored = screen.getByLabelText<HTMLTextAreaElement>('Your answer');
    expect(restored.value).toBe('Private first answer');
    expect(document.activeElement).toBe(restored);
    expect([restored.selectionStart, restored.selectionEnd]).toEqual([3, 8]);
    await act(async () => {
      window.history.replaceState(null, '', list);
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await screen.findByRole('heading', { name: 'Questions' });
    await act(async () => {
      window.history.replaceState(null, '', first);
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(screen.getByDisplayValue('Private first answer')).toBeDefined();
    expect(window.location.hash).not.toContain('Private');
    expect(JSON.stringify(localStorage)).not.toContain('Private');
    expect(sessionStorage.length).toBe(0);
  });
  it('retains a pending save and exact uncertain retry across route unmounts', async () => {
    initial();
    const user = userEvent.setup();
    let settle: ((value: AnswerResult) => void) | undefined;
    const saver = vi.fn<AnswerSaver>(
      () =>
        new Promise((resolve) => {
          settle = resolve;
        }),
    );
    render(
      <App loader={async () => snapshot()} sessionLoader={sessionLoader} answerSaver={saver} />,
    );
    await user.click(await screen.findByRole('link', { name: /Choose the first scope/ }));
    await user.type(screen.getByLabelText('Your answer'), 'A frozen retry');
    await user.click(screen.getByRole('button', { name: 'Save answer' }));
    await user.click(screen.getByRole('link', { name: 'Back to questions' }));
    await user.click(screen.getByRole('link', { name: /Choose the first scope/ }));
    expect(screen.getByRole('button', { name: 'Saving answer…' }).hasAttribute('disabled')).toBe(
      true,
    );
    expect(saver).toHaveBeenCalledTimes(1);
    await act(async () => {
      settle?.({ ok: false, conflict: false });
    });
    await user.click(screen.getByRole('link', { name: 'Back to questions' }));
    await user.click(screen.getByRole('link', { name: /Choose the first scope/ }));
    await user.click(screen.getByRole('button', { name: 'Save answer' }));
    expect(saver.mock.calls[1]?.[0]).toEqual(saver.mock.calls[0]?.[0]);
    await act(async () => {
      settle?.({ ok: true, conflict: false });
    });
    expect(screen.getByText('Answer saved')).toBeDefined();
    expect(screen.queryByText('Applied outcome')).toBeNull();
  });
  it('retains ten authored options, goal/task filters, history and empty search recovery', async () => {
    initial();
    const user = userEvent.setup();
    render(<App loader={async () => snapshot()} sessionLoader={sessionLoader} />);
    await user.click(await screen.findByRole('link', { name: /Choose the first scope/ }));
    expect(screen.getAllByRole('button', { name: /Authored option/ })).toHaveLength(10);
    const questionHash = window.location.hash;
    await user.click(screen.getByRole('button', { name: 'Write answer' }));
    expect(document.activeElement).toBe(screen.getByLabelText('Your answer'));
    expect(window.location.hash).toBe(questionHash);
    await user.click(screen.getByRole('button', { name: 'Authored option 10' }));
    expect(screen.getByDisplayValue('Authored option 10')).toBeDefined();
    await user.click(screen.getByRole('link', { name: 'Back to questions' }));
    await user.selectOptions(screen.getByLabelText('Goal'), 'goal-one');
    await user.selectOptions(screen.getByLabelText('Affected task'), 'A');
    expect(screen.queryByRole('link', { name: /Choose the second scope/ })).toBeNull();
    expect(screen.getByRole('link', { name: /Previous decision/ })).toBeDefined();
    await user.type(screen.getByRole('searchbox', { name: 'Search questions' }), 'not-present');
    expect(screen.getByText(/No questions match/)).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Clear question filters' }));
    await waitFor(() =>
      expect(screen.getByRole('link', { name: /Choose the second scope/ })).toBeDefined(),
    );
  });
});

describe('draft identity and truthful state boundaries', () => {
  it('validates question query values independently while retaining explicit missing filters and exact work context', () => {
    const context = {
      ...defaultContext,
      filters: { ...defaultContext.filters, status: 'active' },
      questions: {
        ...defaultQuestions,
        view: 'history' as const,
        task: 'missing-task',
        goal: 'historical-goal',
      },
    };
    const parsed = parseNavigation(navigationHash({ view: 'questions' }, context), snapshot());
    expect(parsed.questions).toEqual(context.questions);
    expect(parsed.filters.status).toBe('active');
    const invalid = parseNavigation(
      '#questions?qview=invalid&qstate=invalid&qsearch=one&qsearch=two',
      snapshot(),
      context,
    );
    expect(invalid.issues).toEqual(expect.arrayContaining(['qview', 'qstate', 'qsearch']));
    expect(invalid.questions).toEqual(defaultQuestions);
    expect(invalid.filters.status).toBe('active');
    const malformed = parseNavigation('#questions?qgoal=%GG&qtask=A', snapshot(), context);
    expect(malformed.issues).toContain('encoding');
    expect(malformed.questions?.goal).toBe('');
    expect(malformed.questions?.task).toBe('');
  });
  it('isolates programs, changes identity only with payload, and never infers application', async () => {
    const store = createQuestionDrafts(),
      first = questionDraftKey('one', 'Q'),
      second = questionDraftKey('two', 'Q');
    store.read(first, 1);
    store.read(second, 1);
    store.update(first, { answer: 'First' });
    expect(store.read(second, 1).answer).toBe('');
    const saver = vi.fn<AnswerSaver>(async () => ({ ok: false, conflict: false }));
    const request = { questionId: 'Q', nonce: 'nonce', saver };
    await store.submit(first, request);
    const renewedRequest = { ...request, nonce: 'renewed-nonce' };
    await store.submit(first, renewedRequest);
    expect(saver.mock.calls[1]?.[0]).toEqual(saver.mock.calls[0]?.[0]);
    expect(saver.mock.calls[1]?.[1]).toBe('renewed-nonce');
    store.update(first, { answer: 'Changed' });
    await store.submit(first, request);
    expect(saver.mock.calls[2]?.[0].submission_id).not.toBe(saver.mock.calls[0]?.[0].submission_id);
    expect(questionStage({ status: 'future' })).toBe('unknown');
    expect(questionStage({ status: 'resolved', outcome: { summary: 'Done' } })).toBe('unknown');
    expect(
      questionMatches(
        { status: 'resolved', outcome: { kind: 'withdrawn' } },
        { ...defaultQuestions, view: 'history' },
      ),
    ).toBe(true);
  });
  it('sends only the existing answer payload and nonce header to the same-origin write endpoint', async () => {
    const fetcher = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 200 }));
    const input = {
      question_id: 'Q',
      version: 1,
      answer: 'Exact private answer',
      submission_id: 'stable-id',
    };
    expect(await saveAnswer(input, 'private-nonce')).toEqual({ ok: true, conflict: false });
    expect(fetcher).toHaveBeenCalledWith(
      '/api/questions/answer',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify(input),
        headers: { 'Content-Type': 'application/json', 'X-Work-Program-Nonce': 'private-nonce' },
      }),
    );
  });
});
