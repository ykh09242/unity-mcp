import { createContext, useContext, useState, useSyncExternalStore } from 'react';
import type { ReactNode } from 'react';
import type { AnswerInput, AnswerSaver } from './questions';

type DraftPhase = 'editing' | 'saving' | 'saved' | 'conflict' | 'unavailable';
export type QuestionDraft = {
  readonly answer: string;
  readonly version: number;
  readonly phase: DraftPhase;
  readonly submission: AnswerInput | null;
  readonly selection: { readonly start: number; readonly end: number } | null;
};
export const questionDraftKey = (program: string, question: string) =>
  JSON.stringify([program, question]);

// One store per mounted dashboard. No URL, browser storage or module-global answer state.
export function createQuestionDrafts() {
  const drafts = new Map<string, QuestionDraft>();
  const listeners = new Set<() => void>();
  let revision = 0;
  const read = (key: string, version: number): QuestionDraft => {
    let value = drafts.get(key);
    if (!value) {
      value = { answer: '', version, phase: 'editing', submission: null, selection: null };
      drafts.set(key, value);
    }
    return value;
  };
  const update = (key: string, patch: Partial<QuestionDraft>) => {
    const previous = drafts.get(key);
    if (!previous) return;
    drafts.set(key, { ...previous, ...patch });
    revision += 1;
    listeners.forEach((listener) => listener());
  };
  return {
    read,
    update,
    peek: (key: string) => drafts.get(key),
    revision: () => revision,
    forProgram: (program: string) =>
      [...drafts].flatMap(([key, draft]) => {
        const [programId, questionId]: [string, string] = JSON.parse(key);
        return programId === program ? [{ questionId, draft }] : [];
      }),
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    async submit(
      key: string,
      request: { readonly questionId: string; readonly nonce: string; readonly saver: AnswerSaver },
    ) {
      const draft = drafts.get(key);
      if (
        !draft ||
        draft.phase === 'saving' ||
        draft.phase === 'saved' ||
        !draft.answer.trim() ||
        draft.answer.length > 4000
      )
        return false;
      const previous = draft.submission;
      const submission =
        previous && previous.answer === draft.answer && previous.version === draft.version
          ? previous
          : {
              question_id: request.questionId,
              version: draft.version,
              answer: draft.answer,
              submission_id: crypto.randomUUID(),
            };
      update(key, { phase: 'saving', submission });
      try {
        const result = await request.saver(submission, request.nonce);
        update(key, { phase: result.ok ? 'saved' : result.conflict ? 'conflict' : 'unavailable' });
        return result.ok;
      } catch {
        // no-excuse-ok: catch -- transport confirmation boundary; retain immutable retry payload.
        update(key, { phase: 'unavailable' });
        return false;
      }
    },
  };
}
type QuestionDrafts = ReturnType<typeof createQuestionDrafts>;
const DraftContext = createContext<QuestionDrafts | null>(null);
export function QuestionDraftProvider({ children }: { readonly children: ReactNode }) {
  const [store] = useState(createQuestionDrafts);
  return <DraftContext.Provider value={store}>{children}</DraftContext.Provider>;
}
export function useQuestionDrafts(): QuestionDrafts {
  const store = useContext(DraftContext);
  if (!store) throw new Error('QuestionDraftProvider is required');
  return store;
}
export function useQuestionDraft(key: string, version: number) {
  const store = useQuestionDrafts();
  const draft = useSyncExternalStore(store.subscribe, () => store.read(key, version));
  return { draft, store };
}
