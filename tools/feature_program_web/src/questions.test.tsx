import { act, fireEvent, render as rtlRender, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QuestionDraftProvider } from './question-drafts';
import type { ReactElement } from 'react';
const render = (ui: ReactElement) => rtlRender(ui, { wrapper: QuestionDraftProvider });
import { QuestionForm, useSession } from './questions';
import type { AnswerResult, AnswerSaver, Session, SessionLoader } from './questions';
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

describe('session read lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  });
  afterEach(() => vi.useRealTimers());

  it('keeps one pending read, aborts at the deadline and ignores a late response', async () => {
    let finish: ((value: Session) => void) | undefined;
    let signal: AbortSignal | undefined;
    const loader = vi
      .fn<SessionLoader>()
      .mockImplementationOnce((input) => {
        signal = input;
        return new Promise((resolve) => {
          finish = resolve;
        });
      })
      .mockResolvedValue(session);
    const hook = renderHook(() => useSession(loader));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(loader).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(signal?.aborted).toBe(true);
    expect(hook.result.current).toEqual({ responsesEnabled: false, nonce: null });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(loader).toHaveBeenCalledTimes(2);
    expect(hook.result.current).toEqual(session);
    await act(async () => {
      finish?.({ responsesEnabled: true, nonce: 'late old nonce' });
    });
    expect(hook.result.current).toEqual(session);
  });

  it('aborts the replaced loader and retains the new nonce after old settlement', async () => {
    let finish: ((value: Session) => void) | undefined;
    let signal: AbortSignal | undefined;
    const oldLoader = vi.fn<SessionLoader>((input) => {
      signal = input;
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    const nextLoader = vi.fn<SessionLoader>().mockResolvedValue(session);
    const hook = renderHook(({ loader }) => useSession(loader), {
      initialProps: { loader: oldLoader },
    });
    hook.rerender({ loader: nextLoader });
    await act(async () => {
      finish?.({ responsesEnabled: true, nonce: 'old nonce' });
    });
    expect(signal?.aborted).toBe(true);
    expect(hook.result.current).toEqual(session);
    hook.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(nextLoader).toHaveBeenCalledTimes(1);
  });
});

describe('director question responses', () => {
  it.each(['{Enter}', ' '])(
    'continues keyboard focus to the saved answer after %s submission',
    async (key) => {
      const user = userEvent.setup();
      let settle: ((result: AnswerResult) => void) | undefined;
      const saver = vi.fn<AnswerSaver>(
        () =>
          new Promise((resolve) => {
            settle = resolve;
          }),
      );
      render(
        <QuestionForm
          question={question}
          session={session}
          saver={saver}
          refresh={async () => {}}
        />,
      );
      await user.type(screen.getByLabelText('Your answer'), 'Keyboard answer');
      await user.tab();
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Save answer' }));
      await user.keyboard(key);
      expect(saver).toHaveBeenCalledTimes(1);
      await act(async () => {
        settle?.({ ok: true, conflict: false });
      });
      expect(document.activeElement).toBe(
        screen.getByRole('heading', { name: 'Your saved answer' }),
      );
      expect(screen.getByRole('status').textContent).toContain('Answer saved.');
    },
  );
  it.each(['focus', 'pointer'])(
    'does not reclaim focus after deliberate %s movement during a save',
    async (movement) => {
      const user = userEvent.setup();
      let settle: ((result: AnswerResult) => void) | undefined;
      const saver = vi.fn<AnswerSaver>(
        () =>
          new Promise((resolve) => {
            settle = resolve;
          }),
      );
      render(
        <>
          <button>Other control</button>
          <QuestionForm
            question={question}
            session={session}
            saver={saver}
            refresh={async () => {}}
          />
        </>,
      );
      await user.type(screen.getByLabelText('Your answer'), 'Do not steal focus');
      await user.click(screen.getByRole('button', { name: 'Save answer' }));
      if (movement === 'focus') {
        const other = screen.getByRole('button', { name: 'Other control' });
        other.focus();
        other.blur();
      } else {
        fireEvent.pointerDown(document.body);
        (document.activeElement as HTMLElement).blur();
      }
      if (movement === 'focus') expect(document.activeElement).toBe(document.body);
      await act(async () => {
        settle?.({ ok: true, conflict: false });
      });
      expect(document.activeElement).toBe(document.body);
    },
  );
  it('does not focus a saved record supplied by polling without a local save', async () => {
    const view = render(
      <>
        <button>Keep focus</button>
        <QuestionForm question={question} session={session} refresh={async () => {}} />
      </>,
    );
    const control = screen.getByRole('button', { name: 'Keep focus' });
    control.focus();
    view.rerender(
      <>
        <button>Keep focus</button>
        <QuestionForm
          question={{ ...question, status: 'answered', answer: { answer: 'Saved elsewhere' } }}
          session={session}
          refresh={async () => {}}
        />
      </>,
    );
    expect(screen.getByRole('heading', { name: 'Your saved answer' })).toBeDefined();
    expect(document.activeElement).toBe(control);
  });
  it('retains keyboard retry focus and the exact draft after a failed save', async () => {
    const user = userEvent.setup();
    const saver = vi.fn<AnswerSaver>(async () => ({ ok: false, conflict: false }));
    render(
      <QuestionForm question={question} session={session} saver={saver} refresh={async () => {}} />,
    );
    await user.type(screen.getByLabelText('Your answer'), 'Retry this exact draft');
    await user.tab();
    await user.keyboard('{Enter}');
    await screen.findByRole('alert');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Save answer' }));
    expect(screen.getByDisplayValue('Retry this exact draft')).toBeDefined();
    await user.keyboard('{Enter}');
    expect(saver.mock.calls[1]?.[0]).toEqual(saver.mock.calls[0]?.[0]);
  });
  it('focuses the actionable error when the initiating retry control becomes disabled', async () => {
    const user = userEvent.setup();
    let settle: ((result: AnswerResult) => void) | undefined;
    const saver = vi.fn<AnswerSaver>(
      () =>
        new Promise((resolve) => {
          settle = resolve;
        }),
    );
    const view = render(
      <QuestionForm question={question} session={session} saver={saver} refresh={async () => {}} />,
    );
    await user.type(screen.getByLabelText('Your answer'), 'Retain after session changes');
    await user.tab();
    await user.keyboard('{Enter}');
    view.rerender(
      <QuestionForm
        question={question}
        session={{ responsesEnabled: false, nonce: null }}
        saver={saver}
        refresh={async () => {}}
      />,
    );
    await act(async () => {
      settle?.({ ok: false, conflict: false });
    });
    expect(document.activeElement).toBe(screen.getByRole('alert'));
    expect(screen.getByDisplayValue('Retain after session changes')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Save answer' }).hasAttribute('disabled')).toBe(true);
  });
  it('retains an authored no-action explanation and canonical outcome responsibility without inventing another lifecycle', async () => {
    const user = userEvent.setup();
    render(
      <QuestionForm
        question={{
          ...question,
          status: 'resolved',
          answer: { answer: 'Keep current scope' },
          acknowledgement: { actor_id: 'reader', summary: 'Read by director' },
          outcome: {
            kind: 'applied',
            actor_id: 'decision-director',
            summary: 'No task action was taken; the current scope already meets the request.',
            scope: ['src/exact.ts'],
            source_head: 'exact-revision',
          },
        }}
        session={session}
        refresh={async () => {}}
      />,
    );
    expect(screen.getByText('Applied outcome')).toBeDefined();
    expect(
      screen.getByText('No task action was taken; the current scope already meets the request.'),
    ).toBeDefined();
    expect(screen.getByText('Keep current scope')).toBeDefined();
    await user.click(screen.getByText('Decision scope and actors'));
    expect(screen.getByText('decision-director')).toBeDefined();
    expect(screen.getByText('src/exact.ts')).toBeDefined();
    expect(screen.queryByText('exact-revision')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save answer' })).toBeNull();
    expect(document.querySelector('pre')).toBeNull();
  });
  it('cannot adopt a revised version while an earlier save is pending', async () => {
    const user = userEvent.setup();
    let settle: ((result: AnswerResult) => void) | undefined;
    const saver = vi.fn<AnswerSaver>(
      () =>
        new Promise((resolve) => {
          settle = resolve;
        }),
    );
    const view = render(
      <QuestionForm question={question} session={session} saver={saver} refresh={async () => {}} />,
    );
    await user.type(screen.getByLabelText('Your answer'), 'Frozen version one');
    await user.click(screen.getByRole('button', { name: 'Save answer' }));
    view.rerender(
      <QuestionForm
        question={{ ...question, version: 2 }}
        session={session}
        saver={saver}
        refresh={async () => {}}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Use current question version' }).hasAttribute('disabled'),
    ).toBe(true);
    expect(saver).toHaveBeenCalledTimes(1);
    await act(async () => {
      settle?.({ ok: false, conflict: true });
    });
    expect(
      screen.getByRole('button', { name: 'Use current question version' }).hasAttribute('disabled'),
    ).toBe(false);
  });
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
    expect(refresh).toHaveBeenCalledWith(false, true);
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
