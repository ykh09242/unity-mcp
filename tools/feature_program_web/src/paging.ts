import type { Task } from './model';

export const pageSizes = [25, 50, 100] as const;
export type PageSize = (typeof pageSizes)[number];
export type WorkPage = { readonly index: number; readonly size: PageSize };
export const firstWorkPage: WorkPage = { index: 0, size: 50 };
export type WorkFocusRequest = { readonly token: number; readonly taskId: string | null };
export type TaskPage = {
  readonly index: number;
  readonly count: number;
  readonly matching: number;
  readonly start: number;
  readonly end: number;
  readonly tasks: readonly Task[];
};

export function pageSize(value: string): PageSize {
  return value === '25' ? 25 : value === '100' ? 100 : 50;
}

// Selection and deterministic ordering must run over the complete snapshot before this slice.
export function taskPage(tasks: readonly Task[], requested: WorkPage): TaskPage {
  const count = Math.ceil(tasks.length / requested.size);
  const index = Math.max(0, Math.min(Math.trunc(requested.index), Math.max(0, count - 1)));
  const offset = index * requested.size;
  return {
    index,
    count,
    matching: tasks.length,
    start: tasks.length ? offset + 1 : 0,
    end: Math.min(offset + requested.size, tasks.length),
    tasks: tasks.slice(offset, offset + requested.size),
  };
}

export function taskPageIndex(tasks: readonly Task[], id: string, size: PageSize): number | null {
  const position = tasks.findIndex((task) => task.id === id);
  return position < 0 ? null : Math.floor(position / size);
}
