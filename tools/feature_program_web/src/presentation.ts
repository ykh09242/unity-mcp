import type { Json, JsonRecord, Snapshot, Task } from './model';
import { presentationDigest } from './presentation-digest';

type MutableRecord = { [key: string]: Json };
type Origin = { readonly path: string; readonly pointer: string; readonly sha256?: string };
export type PresentedField = {
  readonly target: JsonRecord;
  readonly origin: JsonRecord;
  readonly original: string;
  readonly text: string;
};
type PresentationView = {
  readonly snapshot: Snapshot;
  readonly aliases: ReadonlyMap<string, string>;
  readonly fields: readonly PresentedField[];
};
const originals = new WeakMap<object, Json>();
const cache = new WeakMap<Snapshot, PresentationView>();
const views = new WeakMap<Snapshot, PresentationView>();
const object = (value: Json | undefined): JsonRecord =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as JsonRecord) : {};
const text = (value: Json | undefined) => (typeof value === 'string' ? value : '');
const rows = (value: Json | undefined): readonly JsonRecord[] =>
  Array.isArray(value) ? value.map(object) : [];
const narrative = new Set([
  'reason',
  'summary',
  'note',
  'next_action',
  'goal',
  'constraints',
  'decisions',
  'findings',
  'unresolved_findings',
  'unresolved_acceptance',
  'unresolved_blockers',
  'limitations',
  'description',
  'basis',
  'focus',
  'team_state',
  'criterion',
  'planning_note',
]);
const recordKinds = new Set([
  'objective_record',
  'task_note',
  'task_history',
  'receipt_narrative',
  'question_outcome',
  'staffing',
  'handoff',
  'delivery',
  'planning',
  'coverage',
  'checkpoint',
]);

/** Original API records remain separate from the display view, including nested raw disclosures. */
export function originalValue<T extends Json | undefined>(value: T): T {
  if (!value || typeof value !== 'object') return value;
  const original = originals.get(value);
  if (original !== undefined) return original as T;
  // Derived disclosure wrappers may contain mapped records at any depth.
  return (
    Array.isArray(value)
      ? value.map(originalValue)
      : Object.fromEntries(Object.entries(value).map(([key, item]) => [key, originalValue(item)]))
  ) as T;
}

function clone(value: Json): Json {
  if (value === null || typeof value !== 'object') return value;
  const copy: Json = Array.isArray(value)
    ? value.map(clone)
    : Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clone(item)]));
  originals.set(copy, value);
  return copy;
}

function pointer(value: string): string[] | null {
  if (value === '') return [];
  if (!value.startsWith('/') || /~(?![01])/u.test(value)) return null;
  return value
    .slice(1)
    .split('/')
    .map((part) => part.replaceAll('~1', '/').replaceAll('~0', '~'));
}

function at(value: Json, path: readonly string[]): Json | undefined {
  let current: Json | undefined = value;
  for (const key of path) {
    if (!current || typeof current !== 'object' || !Object.hasOwn(current, key)) return undefined;
    current = (current as JsonRecord)[key];
  }
  return current;
}

function put(root: Json, path: readonly string[], value: string): boolean {
  if (!path.length) return false;
  const parent = at(root, path.slice(0, -1));
  const key = path.at(-1)!;
  if (!parent || typeof parent !== 'object' || typeof at(root, path) !== 'string') return false;
  (parent as MutableRecord)[key] = value;
  return true;
}

function byId(value: Json | undefined, id: string): JsonRecord | undefined {
  return rows(value).find((row) => row.id === id);
}

function permitted(target: JsonRecord): boolean {
  const kind = text(target.kind),
    field = text(target.field);
  if (recordKinds.has(kind))
    return narrative.has(field) || (kind === 'checkpoint' && field === 'title');
  const fields: Readonly<Record<string, readonly string[]>> = {
    objective: ['title', 'goal'],
    objective_criterion: ['text'],
    workstream: ['name', 'title', 'focus'],
    category: ['title', 'reason'],
    task_title: ['title'],
    task_content: ['source_body', 'expected_result', 'acceptance_criteria'],
    task_action: ['planning_note', 'next_action', 'blocker', 'reason'],
    question: ['prompt', 'why', 'requested_decision', 'option'],
    actor_alias: ['label'],
  };
  return fields[kind]?.includes(field) ?? false;
}

function matchesField(target: JsonRecord, path: readonly string[]): boolean {
  const field = text(target.field),
    kind = text(target.kind);
  if (kind === 'actor_alias') return true;
  if (
    ['answer', 'saved_answer', 'submission_id', 'nonce', 'source_snapshot', 'presentation'].some(
      (key) => path.includes(key),
    )
  )
    return false;
  const root = path[0],
    taskPart = path[2],
    questionPart = path[3] === 'issued' ? path[4] : path[3];
  const correctContainer =
    kind === 'receipt_narrative' ||
    (['objective', 'objective_criterion', 'objective_record'].includes(kind) &&
      root === 'objective') ||
    (kind === 'workstream' && root === 'workstreams') ||
    (kind === 'category' && root === 'categories') ||
    (['task_title', 'task_content', 'task_action'].includes(kind) && root === 'tasks') ||
    (kind === 'task_note' && root === 'tasks' && taskPart === 'notes') ||
    (kind === 'task_history' &&
      ((root === 'tasks' && taskPart === 'attempts') || root === 'events')) ||
    (kind === 'question' &&
      root === 'questions' &&
      !['outcome', 'acknowledgement'].includes(questionPart || '')) ||
    (kind === 'question_outcome' &&
      root === 'questions' &&
      ['outcome', 'acknowledgement'].includes(questionPart || '')) ||
    (kind === 'staffing' && ['actors', 'director'].includes(root || '')) ||
    (kind === 'handoff' && root === 'handoffs') ||
    (kind === 'delivery' && root === 'tasks' && taskPart === 'deliveries') ||
    (kind === 'planning' &&
      ['amendments', 'phases', 'source_intakes', 'publication'].includes(root || '')) ||
    (kind === 'coverage' && root === 'coverage_annotations') ||
    (kind === 'checkpoint' && ['checkpoint', 'title'].includes(root || ''));
  if (
    !correctContainer ||
    (target.record_pointer !== undefined &&
      target.record_pointer !==
        '/' + path.map((part) => part.replaceAll('~', '~0').replaceAll('/', '~1')).join('/'))
  )
    return false;
  if (kind === 'task_action' && path.length !== 3) return false;
  if (kind === 'task_content' && field === 'source_body')
    return path.slice(-2).join('/') === 'source/text';
  if (kind === 'objective_criterion')
    return path.at(-2) === 'criteria' && path.at(-1) === String(target.ordinal);
  if (kind === 'question' && field === 'option')
    return path.at(-2) === 'options' && path.at(-1) === String(target.ordinal);
  const position = path.lastIndexOf(field);
  return position >= 0 && path.slice(position + 1).every((part) => /^\d+$/u.test(part));
}

function receiptRoots(snapshot: Snapshot): readonly { origin: Origin; value: JsonRecord }[] {
  const result: { origin: Origin; value: JsonRecord }[] = [];
  for (const task of snapshot.tasks)
    for (const attempt of rows(task.attempts))
      for (const round of rows(attempt.rounds))
        for (const kind of ['receipt', 'review']) {
          const ref = object(round[kind]),
            value = object(round[kind + '_record']);
          if (text(ref.path) && text(ref.sha256) && Object.keys(value).length)
            result.push({
              origin: { path: text(ref.path), pointer: '', sha256: text(ref.sha256) },
              value,
            });
        }
  for (const criterion of rows(object(snapshot.coverage).criteria))
    for (const observation of rows(criterion.recorded_observations)) {
      const ref = object(observation.receipt);
      if (text(ref.path) && text(ref.sha256))
        result.push({
          origin: { path: text(ref.path), pointer: '', sha256: text(ref.sha256) },
          value: object(observation.facts),
        });
    }
  return result;
}

function locations(
  snapshot: Snapshot,
  entry: JsonRecord,
  receipts: ReturnType<typeof receiptRoots>,
): readonly [Json, readonly string[]][] {
  const target = object(entry.target),
    origin = object(entry.origin);
  const id = text(target.entity_id),
    kind = text(target.kind),
    field = text(target.field);
  const path = pointer(text(origin.pointer));
  if (!path || !matchesField(target, path)) return [];
  if (origin.path !== 'backlog.json')
    return receipts
      .filter(
        (root) =>
          root.origin.path === origin.path &&
          root.origin.sha256 === origin.sha256 &&
          (!target.source_path || target.source_path === origin.path),
      )
      .map((root) => [root.value, path]);
  if (origin.revision !== snapshot.revision) return [];
  if (path[0] === 'tasks') {
    const task = snapshot.tasks.find((item) => item.id === id);
    if (!task) return [];
    if (kind === 'task_title')
      return [
        [task, ['title']],
        [object(task.source), ['title']],
      ];
    if (kind === 'task_content' && field === 'source_body')
      return [[object(task.source), ['text']]];
    if (path[2] === 'deliveries') {
      const result: [Json, readonly string[]][] = rows(object(task.delivery_history).items)
        .filter((item) => {
          const ref = object(item.reference);
          return (
            ref.path === origin.path &&
            ref.revision === origin.revision &&
            text(origin.pointer).startsWith(text(ref.pointer) + '/')
          );
        })
        .map((item) => [object(item.record), path.slice(4)]);
      const current = originalValue(task.delivery);
      if (
        current &&
        result.some(
          ([value]) => presentationDigest(originalValue(value)) === presentationDigest(current),
        )
      )
        result.push([object(task.delivery), path.slice(4)]);
      return result;
    }
    return [[task, path.slice(2)]];
  }
  if (path[0] === 'questions' && path[1] === 'items') {
    const question = byId(snapshot.questions, id);
    const context = object(object(entry.binding).context);
    if (!question || (context.version !== undefined && question.version !== context.version))
      return [];
    return [[question, path.slice(path[3] === 'issued' ? 4 : 3)]];
  }
  if (['categories', 'workstreams', 'actors', 'handoffs'].includes(path[0] || '')) {
    const row = byId(snapshot[path[0]!], id);
    const result: [Json, readonly string[]][] = row ? [[row, path.slice(2)]] : [];
    if (path[0] === 'workstreams' && ['title', 'name'].includes(field)) {
      const category = byId(snapshot.categories, id);
      // Only derived roots inherit workstream labels; explicit categories have their own targets.
      const explicit = Object.values(object(object(snapshot.presentation).entries)).some(
        (value) => {
          const target = object(object(value).target);
          return target.kind === 'category' && target.entity_id === id && target.field === 'title';
        },
      );
      if (category && !explicit && (!row?.title || field === 'title'))
        result.push([category, ['title']]);
    }
    return result;
  }
  if (path[0] === 'coverage_annotations' && path[2] === 'annotation' && path[3] === 'uncovered') {
    const coverage = object(snapshot.coverage),
      history = rows(coverage.history);
    if (Number(path[1]) !== history.length - 1 || history.at(-1)?.freshness !== 'current')
      return [];
    const digest = object(object(entry.binding).context).record_sha256;
    const matches = rows(coverage.criteria)
      .flatMap((criterion) => rows(criterion.uncovered))
      .filter((value) => presentationDigest(originalValue(value)) === digest);
    return matches.length === 1 ? [[matches[0]!, path.slice(5)]] : [];
  }
  // These supplied collections preserve canonical order; task/question/category adapters above do not.
  if (
    ['objective', 'director', 'amendments', 'source_intakes', 'checkpoint', 'title'].includes(
      path[0] || '',
    )
  )
    return [[snapshot, path]];
  return [];
}

function updateDerivedCopies(snapshot: Snapshot): void {
  const brief = object(snapshot.brief),
    objective = object(snapshot.objective);
  const referenceMatches = (value: JsonRecord, path: string) => {
    const ref = object(value.reference);
    return (
      ref.path === 'backlog.json' && ref.revision === snapshot.revision && ref.pointer === path
    );
  };
  const copy = (
    target: JsonRecord,
    source: JsonRecord,
    fields: readonly string[],
    bounded = false,
  ) => {
    for (const field of fields) {
      const value = source[field];
      if (
        typeof value !== 'string' ||
        value === originalValue(source)[field] ||
        typeof target[field] !== 'string'
      )
        continue;
      (target as MutableRecord)[field] = bounded ? Array.from(value).slice(0, 512).join('') : value;
      if (bounded && Array.from(value).length > 512) {
        const truncated = Array.isArray(target.truncated_fields) ? target.truncated_fields : [];
        (target as MutableRecord).truncated_fields = [...new Set([...truncated, field])];
      }
    }
  };
  // Repeated criterion text has its own ordinal/hash identity, not a prose match.
  const criteria = rows(object(snapshot.coverage).criteria);
  const completion = object(objective.completion),
    aggregate = object(object(snapshot.coverage).recorded_objective_acceptance);
  const completionRef = rows(objective.evidence)[0],
    aggregateRef = object(aggregate.receipt);
  if (
    completionRef &&
    aggregate.status === 'recorded' &&
    completionRef.path === aggregateRef.path &&
    completionRef.sha256 === aggregateRef.sha256 &&
    completion.criteria_results &&
    aggregate.criteria_results &&
    presentationDigest(originalValue(completion.criteria_results)) ===
      presentationDigest(originalValue(aggregate.criteria_results))
  )
    (aggregate as MutableRecord).criteria_results = completion.criteria_results;
  for (const entry of Object.values(object(object(snapshot.presentation).entries)).map(object)) {
    const target = object(entry.target),
      context = object(object(entry.binding).context),
      origin = object(entry.origin);
    if (
      entry.status !== 'current' ||
      target.kind !== 'objective_criterion' ||
      target.entity_id !== objective.id ||
      origin.pointer !== `/objective/criteria/${target.ordinal}` ||
      origin.revision !== snapshot.revision
    )
      continue;
    const value = Array.isArray(objective.criteria)
      ? objective.criteria[Number(target.ordinal)]
      : undefined;
    if (typeof value !== 'string') continue;
    for (const row of criteria)
      if (
        object(row.criterion).ordinal === target.ordinal &&
        object(row.criterion).text_sha256 === context.text_sha256
      )
        (row as MutableRecord).text = value;
  }
  if (brief.revision !== snapshot.revision || brief.program_id !== snapshot.program_id) return;
  const goal = object(brief.objective);
  if (referenceMatches(goal, '/objective')) {
    copy(goal, objective, ['title', 'next_action'], true);
    for (const row of rows(object(goal.criteria).items)) {
      const ordinal = row.ordinal;
      const source = criteria.find(
        (item) =>
          object(item.criterion).ordinal === ordinal &&
          object(item.criterion).text_sha256 === row.text_sha256,
      );
      if (!source || !referenceMatches(row, `/objective/criteria/${ordinal}`)) continue;
      copy(row, source, ['text'], true);
      const uncovered = rows(source.uncovered),
        copies = rows(object(row.uncovered).items);
      // Producer validation permits one uncovered record per criterion; do not join on reason text.
      if (uncovered.length === 1 && copies.length === 1)
        copy(copies[0]!, uncovered[0]!, ['reason'], true);
    }
  }
  for (const row of rows(object(brief.questions).items)) {
    const source = byId(snapshot.questions, text(row.id)),
      ref = object(row.reference);
    if (
      source &&
      row.version === source.version &&
      ref.question_id === source.id &&
      ref.revision === snapshot.revision &&
      ref.path === 'backlog.json' &&
      /^\/questions\/items\/\d+$/u.test(text(ref.pointer))
    )
      copy(row, source, ['prompt', 'why', 'requested_decision'], true);
  }
  for (const row of rows(object(brief.attention).items)) {
    const ref = object(row.reference);
    if (ref.path !== 'backlog.json' || ref.revision !== snapshot.revision) continue;
    if (row.kind === 'objective' && ref.pointer === '/objective')
      copy(row, objective, ['next_action'], true);
    else if (
      ['blocked', 'collision', 'review', 'dependency', 'revalidation'].includes(text(row.kind)) &&
      /^\/tasks\/\d+$/u.test(text(ref.pointer))
    ) {
      const task = snapshot.tasks.find(
        (task) => task.id === row.entity_id && task.id === ref.task_id,
      );
      if (task) copy(row, task, ['next_action'], true);
    }
  }
  const director = object(brief.director);
  if (referenceMatches(director, '/director'))
    copy(object(director.authority), object(object(snapshot.director).authority), ['basis'], true);
}

/** One immutable presentation view per captured snapshot, independent of the UI selector. */
export function presentSnapshot(raw: Snapshot): Snapshot {
  const existing = views.get(raw) || cache.get(raw);
  if (existing) return existing.snapshot;
  const projection = object(raw.presentation);
  const aliases = new Map<string, string>(),
    fields: PresentedField[] = [];
  const view = clone(raw) as Snapshot;
  const receipts = receiptRoots(view);
  const compatible =
    projection.schema_version === 1 &&
    projection.revision === raw.revision &&
    typeof raw.communication_language === 'string' &&
    projection.locale === raw.communication_language;
  if (compatible) {
    for (const entry of Object.values(object(projection.entries)).map(object)) {
      const target = object(entry.target),
        binding = object(entry.binding);
      if (
        entry.status !== 'current' ||
        !['translated', 'alias'].includes(text(entry.mode)) ||
        typeof entry.text !== 'string' ||
        binding.program_id !== raw.program_id ||
        !permitted(target)
      )
        continue;
      const origin = object(entry.origin);
      if (origin.path === 'backlog.json' && origin.revision !== raw.revision) continue;
      if (target.kind === 'actor_alias' && entry.mode === 'alias') {
        if (binding.source_value_sha256 !== presentationDigest(text(target.entity_id))) continue;
        aliases.set(text(target.entity_id), entry.text);
        fields.push({
          target,
          origin: object(entry.origin),
          original: text(target.entity_id),
          text: entry.text,
        });
        continue;
      }
      for (const [root, path] of locations(view, entry, receipts)) {
        const old = at(originalValue(root), path);
        if (typeof old !== 'string') continue;
        const isBody = target.kind === 'task_content' && target.field === 'source_body';
        const context = object(binding.context);
        if (isBody && context.projection_rule !== 'section_after_first_heading_v1') continue;
        const content = isBody ? old.slice(old.indexOf('\n') + 1) : old;
        if (binding.source_value_sha256 !== presentationDigest(content)) continue;
        const replacement = isBody ? old.slice(0, old.indexOf('\n') + 1) + entry.text : entry.text;
        if (put(root, path, replacement))
          fields.push({
            target,
            origin: object(entry.origin),
            original: content,
            text: entry.text,
          });
      }
    }
    updateDerivedCopies(view);
  }
  const unique = new Map(
    fields.map((field) => [JSON.stringify([field.origin, field.original]), field]),
  );
  const result = { snapshot: view, aliases, fields: [...unique.values()] };
  cache.set(raw, result);
  views.set(view, result);
  return view;
}

export function presentationFields(snapshot: Snapshot): readonly PresentedField[] {
  presentSnapshot(snapshot);
  return (views.get(snapshot) || cache.get(snapshot))!.fields;
}

export function actorLabel(snapshot: Snapshot, id: string): string {
  presentSnapshot(snapshot);
  return (views.get(snapshot) || cache.get(snapshot))!.aliases.get(id) || id;
}

export function presentedActorIds(snapshot: Snapshot): readonly string[] {
  presentSnapshot(snapshot);
  return [...(views.get(snapshot) || cache.get(snapshot))!.aliases.keys()];
}

export function taskLabel(snapshot: Snapshot, id: string): string {
  return presentSnapshot(snapshot).tasks.find((task) => task.id === id)?.title || id;
}

export function taskBody(task: Task): string {
  const source = text(object(task.source).text);
  return source.slice(source.indexOf('\n') + 1);
}
