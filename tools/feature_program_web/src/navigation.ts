import { defaultQuestions, questionStages } from './question-state';
import type { QuestionContext } from './question-state';
import { items, record, records, string } from './model';
import type { Snapshot, Task } from './model';
import { firstWorkPage, pageSize } from './paging';
import type { WorkPage } from './paging';
import { selectTasks, snapshotIndex } from './selectors';
import type { TaskFilters } from './selectors';

export type View = 'overview' | 'work' | 'team' | 'task' | 'question' | 'questions' | 'evidence';
export type Route = {
  readonly view: View;
  readonly taskId?: string;
  readonly questionId?: string;
  readonly evidencePath?: string;
  readonly full?: boolean;
};
export type WorkContext = {
  readonly scope: 'all' | 'current';
  readonly filters: TaskFilters;
  readonly paging: WorkPage;
  readonly selected: string | null;
  readonly questions?: QuestionContext;
};
export type Navigation = WorkContext & {
  readonly route: Route;
  readonly issues: readonly string[];
  readonly explicit: boolean;
};
export const emptyFilters: TaskFilters = {
  search: '',
  status: '',
  category: '',
  owner: '',
  workstream: '',
  sort: 'id',
};
export const defaultContext: WorkContext = {
  scope: 'all',
  filters: emptyFilters,
  paging: firstWorkPage,
  selected: null,
};
const statuses = ['planned', 'active', 'blocked', 'review', 'done'] as const;
const sorts = ['id', 'title', 'status', 'owner', 'phase'] as const;
const questionKeys = ['qview', 'qstate', 'qsearch', 'qgoal', 'qtask', 'qselected'] as const;
const keys = [
  'scope',
  'status',
  'owner',
  'workstream',
  'category',
  'q',
  'sort',
  'page',
  'size',
  'task',
  'full',
] as const;
export function parseNavigation(
  hash: string,
  snapshot?: Snapshot | null,
  fallback: WorkContext = defaultContext,
): Navigation {
  const issues: string[] = [];
  const source = hash.replace(/^#/, '');
  const split = source.indexOf('?');
  const path = split < 0 ? source : source.slice(0, split);
  const query = split < 0 ? '' : source.slice(split + 1);
  const parameters = new URLSearchParams(query);
  try {
    decodeURIComponent(query.replaceAll('+', ' '));
  } catch (error) {
    if (error instanceof URIError) issues.push('encoding');
    else throw error;
  }
  for (const key of parameters.keys())
    if (
      ![...keys, ...questionKeys].some((value) => value === key) ||
      parameters.getAll(key).length > 1
    )
      issues.push(key);
  if (/%(?![0-9a-f]{2})/i.test(source)) issues.push('encoding');
  let route: Route = { view: 'overview' };
  if (['work', 'team', 'overview', 'questions'].includes(path)) {
    if (path === 'work') route = { view: 'work' };
    if (path === 'team') route = { view: 'team' };
    if (path === 'questions') route = { view: 'questions' };
  } else {
    try {
      if (path.startsWith('task/'))
        route = {
          view: 'task',
          taskId: decodeURIComponent(path.slice(5)),
          full: parameters.get('full') === '1',
        };
      else if (path.startsWith('question/'))
        route = { view: 'question', questionId: decodeURIComponent(path.slice(9)) };
      else if (path.startsWith('evidence/'))
        route = { view: 'evidence', evidencePath: decodeURIComponent(path.slice(9)) };
      else if (path) issues.push('route');
    } catch (error) {
      if (error instanceof URIError) issues.push('encoding');
      else throw error;
    }
  }
  const explicit = [...parameters.keys()].some((key) => keys.some((value) => value === key));
  const base = explicit ? defaultContext : fallback;
  const value = (key: string, previous: string) =>
    parameters.getAll(key).length > 1 || issues.includes('encoding')
      ? previous
      : parameters.has(key)
        ? parameters.get(key) || ''
        : previous;
  const index = snapshot ? snapshotIndex(snapshot) : null;
  const validate = (
    key: string,
    candidate: string,
    allowed: (value: string) => boolean,
    fallbackValue = '',
  ) => {
    if (!candidate || allowed(candidate)) return candidate;
    issues.push(key);
    return fallbackValue;
  };
  const owner = validate(
    'owner',
    value('owner', base.filters.owner),
    (candidate) =>
      candidate === '__unassigned' ||
      !index ||
      index.tasksByOwner.has(candidate) ||
      index.actors.has(candidate),
  );
  const workstreams = new Set([
    ...(snapshot?.tasks.map((task) => string(task.workstream)) || []),
    ...records(snapshot?.workstreams).map((stream) => string(stream.id)),
  ]);
  const filters: TaskFilters = {
    search: value('q', base.filters.search).slice(0, 300),
    status: validate('status', value('status', base.filters.status), (candidate) =>
      statuses.some((status) => status === candidate),
    ),
    owner,
    workstream: validate(
      'workstream',
      value('workstream', base.filters.workstream),
      (candidate) => !snapshot || workstreams.has(candidate),
    ),
    category: validate(
      'category',
      value('category', base.filters.category),
      (candidate) => !index || index.categories.has(candidate),
    ),
    sort:
      validate(
        'sort',
        value('sort', base.filters.sort),
        (candidate) => sorts.some((sort) => sort === candidate),
        'id',
      ) || 'id',
  };
  const requestedScope = value('scope', base.scope);
  const scope = requestedScope === 'current' ? 'current' : 'all';
  if (!['all', 'current'].includes(requestedScope)) issues.push('scope');
  const requestedPage = value('page', String(base.paging.index + 1));
  const page = /^\d+$/.test(requestedPage) ? Number(requestedPage) : NaN;
  const pageValid = Number.isSafeInteger(page) && page >= 1 && page <= 2000;
  if (!pageValid) issues.push('page');
  const requestedSize = value('size', String(base.paging.size));
  if (!['25', '50', '100'].includes(requestedSize)) issues.push('size');
  const hasQuestions = questionKeys.some((key) => parameters.has(key));
  const previousQuestions = hasQuestions ? defaultQuestions : fallback.questions;
  const questionValue = (key: string, previous = '') => value(key, previous);
  const requestedQuestionView = questionValue('qview', previousQuestions?.view || 'current');
  const questionView = validate(
    'qview',
    requestedQuestionView,
    (candidate) => ['current', 'history', 'all'].includes(candidate),
    'current',
  );
  const questionState = validate(
    'qstate',
    questionValue('qstate', previousQuestions?.state),
    (candidate) => questionStages.some((stage) => stage === candidate),
  );
  const questions =
    hasQuestions || previousQuestions
      ? {
          view:
            questionView === 'history'
              ? ('history' as const)
              : questionView === 'all'
                ? ('all' as const)
                : ('current' as const),
          state: questionState,
          search: questionValue('qsearch', previousQuestions?.search).slice(0, 300),
          goal: questionValue('qgoal', previousQuestions?.goal),
          task: questionValue('qtask', previousQuestions?.task),
          selected: route.questionId || questionValue('qselected', previousQuestions?.selected),
        }
      : undefined;
  return {
    ...(questions ? { questions } : {}),
    route,
    scope,
    filters,
    paging: { index: pageValid ? page - 1 : 0, size: pageSize(requestedSize) },
    selected: route.taskId || parameters.get('task') || base.selected,
    issues: [...new Set(issues)],
    explicit,
  };
}
export function navigationHash(route: Route, context: WorkContext): string {
  let path: string = route.view;
  if (route.view === 'task') path += '/' + encodeURIComponent(route.taskId || '');
  if (route.view === 'question') path += '/' + encodeURIComponent(route.questionId || '');
  if (route.view === 'evidence') path += '/' + encodeURIComponent(route.evidencePath || '');
  const params = new URLSearchParams();
  params.set('scope', context.scope);
  for (const [key, value] of Object.entries(context.filters))
    if (value && !(key === 'sort' && value === 'id'))
      params.set(key === 'search' ? 'q' : key, value);
  params.set('page', String(context.paging.index + 1));
  params.set('size', String(context.paging.size));
  if (context.selected) params.set('task', context.selected);
  if (route.full) params.set('full', '1');
  if (context.questions) {
    const q = context.questions;
    params.set('qview', q.view);
    for (const [key, value] of Object.entries({
      qstate: q.state,
      qsearch: q.search,
      qgoal: q.goal,
      qtask: q.task,
      qselected: route.questionId || q.selected,
    }))
      if (value) params.set(key, value);
  }
  return '#' + path + '?' + params.toString();
}
export function goalTaskIds(snapshot: Snapshot): ReadonlySet<string> | null {
  const objective = record(snapshot.objective);
  return Array.isArray(objective.task_ids)
    ? new Set(items(objective.task_ids).map(string).filter(Boolean))
    : null;
}
export function scopedTasks(snapshot: Snapshot, scope: WorkContext['scope']): readonly Task[] {
  if (scope === 'all') return snapshot.tasks;
  const ids = goalTaskIds(snapshot);
  return ids ? snapshot.tasks.filter((task) => ids.has(task.id)) : [];
}
export function selectWorkTasks(
  snapshot: Snapshot,
  context: Pick<WorkContext, 'scope' | 'filters'>,
): readonly Task[] {
  const results = selectTasks(snapshot, context.filters);
  if (context.scope === 'all') return results;
  const ids = goalTaskIds(snapshot);
  return ids ? results.filter((task) => ids.has(task.id)) : [];
}
export function categoryBreadcrumbs(
  snapshot: Snapshot,
  id: string,
): readonly { readonly id: string; readonly title: string }[] {
  const index = snapshotIndex(snapshot),
    visited = new Set<string>(),
    result: { id: string; title: string }[] = [];
  while (id && !visited.has(id)) {
    visited.add(id);
    const category = index.categories.get(id);
    if (!category) break;
    result.unshift({ id, title: string(category.title) || id });
    id = string(category.parent_id);
  }
  return result;
}
export type SearchResult = {
  readonly kind: 'task' | 'actor' | 'question' | 'evidence';
  readonly id: string;
  readonly label: string;
  readonly route: Route;
  readonly owner?: string;
};
const navigationSearchCache = new WeakMap<Snapshot, readonly SearchResult[]>();
export function navigationSearch(
  snapshot: Snapshot,
  query: string,
  limit = 12,
): { readonly results: readonly SearchResult[]; readonly total: number } {
  let all = navigationSearchCache.get(snapshot);
  if (!all) {
    const index = snapshotIndex(snapshot);
    all = [
      ...snapshot.tasks.map((task) => ({
        kind: 'task' as const,
        id: task.id,
        label: task.title,
        route: { view: 'task' as const, taskId: task.id },
      })),
      ...[...index.actors].map(([id, actor]) => ({
        kind: 'actor' as const,
        id,
        label: string(actor.name || actor.role) || id,
        route: { view: 'team' as const },
        owner: id,
      })),
      ...[...index.questions].map(([id, question]) => ({
        kind: 'question' as const,
        id,
        label: string(question.prompt || question.title) || id,
        route: { view: 'question' as const, questionId: id },
      })),
      ...[...index.evidenceLabels].map(([id, label]) => ({
        kind: 'evidence' as const,
        id,
        label,
        route: { view: 'evidence' as const, evidencePath: id },
      })),
    ];
    navigationSearchCache.set(snapshot, all);
  }
  const text = query.trim().toLocaleLowerCase();
  const matches = text
    ? all.filter((item) => `${item.id} ${item.label}`.toLocaleLowerCase().includes(text))
    : [];
  return { results: matches.slice(0, limit), total: matches.length };
}
export const columns = ['owner', 'category', 'workstream'] as const;
export type Column = (typeof columns)[number];
export type Preset = { readonly name: string; readonly hash: string };
export type ViewPreferences = {
  readonly density: 'compact' | 'comfortable';
  readonly columns: readonly Column[];
  readonly presets: readonly Preset[];
  readonly selectedPreset: string;
};
export const defaultPreferences: ViewPreferences = {
  density: 'comfortable',
  columns: ['owner'],
  presets: [],
  selectedPreset: '',
};
export const viewPreferenceKey = (program: string) =>
  'work-program-view:' + encodeURIComponent(program);
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
export function parsePreferences(value: unknown): ViewPreferences {
  if (!object(value)) return defaultPreferences;
  const requestedColumns = value.columns;
  const selectedColumns = Array.isArray(requestedColumns)
    ? columns.filter((column) => requestedColumns.includes(column))
    : defaultPreferences.columns;
  const presets: Preset[] = [];
  if (Array.isArray(value.presets))
    for (const entry of value.presets.slice(0, 20))
      if (
        object(entry) &&
        typeof entry.name === 'string' &&
        entry.name.trim() &&
        entry.name.length <= 80 &&
        typeof entry.hash === 'string' &&
        entry.hash.length <= 4096
      ) {
        const parsed = parseNavigation(entry.hash);
        presets.push({
          name: entry.name.trim(),
          hash: navigationHash({ view: 'work' }, { ...parsed, selected: null }),
        });
      }
  return {
    density: value.density === 'compact' ? 'compact' : 'comfortable',
    columns: selectedColumns,
    presets,
    selectedPreset:
      typeof value.selectedPreset === 'string' &&
      presets.some((preset) => preset.name === value.selectedPreset)
        ? value.selectedPreset
        : '',
  };
}
export function readPreferences(program: string): {
  readonly preferences: ViewPreferences;
  readonly unavailable: boolean;
} {
  try {
    return {
      preferences: parsePreferences(
        JSON.parse(localStorage.getItem(viewPreferenceKey(program)) || 'null'),
      ),
      unavailable: false,
    };
  } catch (error) {
    if (error instanceof Error || error instanceof DOMException)
      return { preferences: defaultPreferences, unavailable: true };
    throw error;
  }
}
export function writePreferences(program: string, preferences: ViewPreferences): boolean {
  try {
    localStorage.setItem(viewPreferenceKey(program), JSON.stringify(preferences));
    return true;
  } catch (error) {
    if (error instanceof Error || error instanceof DOMException) return false;
    throw error;
  }
}
