import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { QuestionForm } from './questions';
import type { AnswerResult, AnswerSaver } from './questions';
import type { JsonRecord } from './model';

const question: JsonRecord = {
  id: 'Q1',
  version: 1,
  actor_id: 'Ada',
  prompt: 'Which scope should be reviewed first?',
  why: 'The decision affects task order.',
  requested_decision: 'Select a scope',
  status: 'open',
  answer: null,
  acknowledgement: null,
  outcome: null,
};
const session = { responsesEnabled: true, nonce: 'synthetic-nonce' };

describe('director question responses', () => {
  it('preserves a typed answer and focus through polling, saves once on double click, then separates acknowledgement and applied outcome', async () => {
    const user = userEvent.setup();
    let settle: ((result: AnswerResult) => void) | undefined;
    const saver = vi.fn(
      () =>
        new Promise<AnswerResult>((resolve) => {
          settle = resolve;
        }),
    );
    const refresh = vi.fn(async () => {});
    const view = render(
      <QuestionForm question={question} session={session} saver={saver} refresh={refresh} />,
    );
    const input = screen.getByLabelText('Your answer');
    await user.type(input, 'Review the parser first');
    view.rerender(
      <QuestionForm
        question={{
          ...question,
          history: [{ action: 'issued', timestamp: '2026-10-13T00:00:00Z' }],
        }}
        session={session}
        saver={saver}
        refresh={refresh}
      />,
    );
    expect(document.activeElement).toBe(input);
    expect(screen.getByDisplayValue('Review the parser first')).toBeDefined();
    await user.dblClick(screen.getByRole('button', { name: 'Save answer' }));
    expect(saver).toHaveBeenCalledTimes(1);
    await act(async () => {
      settle?.({ ok: true, conflict: false });
    });
    await screen.findByText('Answer saved. Waiting for director acknowledgement.');
    expect(screen.queryByText('Needs your answer')).toBeNull();
    expect(screen.getByText('Answer saved')).toBeDefined();
    expect(refresh).toHaveBeenCalledTimes(1);
    view.rerender(
      <QuestionForm
        question={{
          ...question,
          status: 'acknowledged',
          answer: { answer: 'Review the parser first' },
          acknowledgement: { summary: 'The director read this answer' },
        }}
        session={session}
        saver={saver}
        refresh={refresh}
      />,
    );
    expect(screen.queryByText('Answer saved. Waiting for director acknowledgement.')).toBeNull();
    expect(screen.getByText('The director read this answer')).toBeDefined();
    view.rerender(
      <QuestionForm
        question={{
          ...question,
          status: 'resolved',
          answer: { answer: 'Review the parser first' },
          outcome: { kind: 'applied', summary: 'Parser review is scheduled first' },
        }}
        session={session}
        saver={saver}
        refresh={refresh}
      />,
    );
    expect(screen.getByText('Parser review is scheduled first')).toBeDefined();
    expect(screen.getByText('Applied outcome')).toBeDefined();
  });
  it('retains input on a failed save and retries the same immutable submission identity', async () => {
    const user = userEvent.setup();
    const saver = vi
      .fn()
      .mockRejectedValueOnce(new Error('Lost confirmation'))
      .mockResolvedValueOnce({ ok: true, conflict: false });
    render(
      <QuestionForm question={question} session={session} saver={saver} refresh={async () => {}} />,
    );
    await user.type(screen.getByLabelText('Your answer'), 'Use scoped checks');
    await user.click(screen.getByRole('button', { name: 'Save answer' }));
    await screen.findByText(/Retry uses the same submission ID/);
    expect(screen.getByDisplayValue('Use scoped checks')).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Save answer' }));
    await screen.findByText('Answer saved. Waiting for director acknowledgement.');
    expect(saver.mock.calls[0]?.[0]).toEqual(saver.mock.calls[1]?.[0]);
  });
  it('requires review of a revised question before reusing the preserved draft', async () => {
    const user = userEvent.setup();
    const saver = vi.fn<AnswerSaver>(async () => ({ ok: false, conflict: true }));
    const view = render(
      <QuestionForm question={question} session={session} saver={saver} refresh={async () => {}} />,
    );
    await user.type(screen.getByLabelText('Your answer'), 'Review parser');
    view.rerender(
      <QuestionForm
        question={{
          ...question,
          version: 2,
          prompt: 'Which revised scope should be reviewed first?',
        }}
        session={session}
        saver={saver}
        refresh={async () => {}}
      />,
    );
    const button = screen.getByRole('button', { name: 'Save answer' });
    expect(button.hasAttribute('disabled')).toBe(true);
    expect(screen.getByDisplayValue('Review parser')).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Use current question version' }));
    await user.click(button);
    await screen.findByText(/The question changed or closed/);
    expect(saver.mock.calls[0]?.[0].version).toBe(2);
  });
  it('renders a director withdrawal without inventing a user answer or applied decision', () => {
    render(
      <QuestionForm
        question={{
          ...question,
          status: 'resolved',
          outcome: { kind: 'withdrawn', summary: 'Decision no longer needed' },
        }}
        session={session}
        refresh={async () => {}}
      />,
    );
    expect(screen.getByText('Question withdrawn')).toBeDefined();
    expect(screen.getByText('Decision no longer needed')).toBeDefined();
    expect(screen.queryByText('Your saved answer')).toBeNull();
    expect(screen.queryByText('Applied outcome')).toBeNull();
  });
  it('disables answer submission in a read-only session', () => {
    const saver = vi.fn();
    render(
      <QuestionForm
        question={question}
        session={{ responsesEnabled: false, nonce: null }}
        saver={saver}
        refresh={async () => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save answer' }));
    expect(saver).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Your answer').hasAttribute('disabled')).toBe(true);
  });
});
