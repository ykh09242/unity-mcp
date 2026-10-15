import { describe, expect, it } from 'vitest';
import { categoryTasks, parseSnapshot, semanticSnapshotEqual } from './model';
import { selectTasks, snapshotIndex } from './selectors';

const fixture = () =>
  parseSnapshot({
    snapshot_version: 1,
    revision: 'one',
    program_id: 'test',
    tasks: [
      {
        id: 'A',
        title: 'Alpha',
        status: 'active',
        owner: 'actor',
        category_id: 'child',
        depends_on: ['B', 'B'],
        notes: ['needle'],
        attempts: [{ evidence: [{ path: 'proof.json', sha256: 'hash', label: 'Executed check' }] }],
      },
      { id: 'B', title: 'Beta', status: 'planned', owner: null, category_id: 'root' },
    ],
    actors: [{ id: 'actor', parent_id: null }, 'legacy'],
    categories: [
      { id: 'root', parent_id: null, task_ids: ['A', 'missing'] },
      { id: 'child', parent_id: 'root', task_ids: ['B', 'A', 8] },
    ],
    questions: [{ id: 'Q', status: 'open', affected_task_ids: ['A', 'A', null] }, { legacy: true }],
  });

describe('snapshot-scoped canonical selectors', () => {
  it('shares record identity and deduplicates canonical memberships, questions and reverse edges', () => {
    // Given
    const snapshot = fixture();
    // When
    const index = snapshotIndex(snapshot);
    // Then
    expect(snapshotIndex(snapshot)).toBe(index);
    expect(index.tasks.get('A')).toBe(snapshot.tasks[0]);
    expect([...categoryTasks(snapshot, 'root')].sort()).toEqual(['A', 'B']);
    expect(index.categoryTasks('root')).toBe(index.categoryTasks('root'));
    expect(index.reverseDependencies.get('B')?.map((task) => task.id)).toEqual(['A']);
    expect(index.questionsByTask.get('A')).toEqual([index.questions.get('Q')]);
    expect(index.tasksByOwner.get('actor')).toEqual([snapshot.tasks[0]]);
    expect(index.actors.size).toBe(1);
    expect(index.questions.size).toBe(1);
  });

  it('rebuilds memberships from the new snapshot without mutating prior indexes', () => {
    // Given
    const old = fixture();
    const prior = snapshotIndex(old);
    const changed = parseSnapshot({
      ...old,
      tasks: [old.tasks[1]],
      categories: [{ id: 'root', task_ids: ['B'] }],
      questions: [],
    });
    // When
    const index = snapshotIndex(changed);
    // Then
    expect(index).not.toBe(prior);
    expect(index.tasks.has('A')).toBe(false);
    expect([...index.categoryTasks('root')]).toEqual(['B']);
    expect(index.reverseDependencies.size).toBe(0);
    expect(index.questions.size).toBe(0);
    expect(prior.tasks.has('A')).toBe(true);
  });

  it('terminates legacy category cycles and keeps task results unique', () => {
    // Given
    const snapshot = parseSnapshot({
      ...fixture(),
      categories: [
        { id: 'x', parent_id: 'y', task_ids: ['A'] },
        { id: 'y', parent_id: 'x', task_ids: ['A', 'B'] },
      ],
    });
    // When
    const selected = snapshotIndex(snapshot).categoryTasks('x');
    // Then
    expect([...selected].sort()).toEqual(['A', 'B']);
  });

  it('applies shared text/category/owner filters to current canonical task records', () => {
    // Given
    const snapshot = fixture();
    // When
    const result = selectTasks(snapshot, {
      search: 'NEEDLE',
      category: 'root',
      owner: 'actor',
      status: 'active',
      workstream: '',
      sort: 'title',
    });
    // Then
    expect(result).toEqual([snapshot.tasks[0]]);
  });

  it('compares key order semantically while preserving nested and array content', () => {
    // Given
    const snapshot = fixture();
    const reordered = parseSnapshot({
      program_id: 'test',
      tasks: snapshot.tasks,
      categories: snapshot.categories,
      questions: snapshot.questions,
      actors: snapshot.actors,
      revision: 'one',
      snapshot_version: 1,
      generated_at: 'new observation',
    });
    // When
    const equal = semanticSnapshotEqual(reordered, snapshot);
    // Then
    expect(equal).toBe(true);
    expect(
      semanticSnapshotEqual(
        snapshot,
        parseSnapshot({ ...snapshot, tasks: [...snapshot.tasks].reverse() }),
      ),
    ).toBe(false);
  });
});
