using System;
using System.Collections.Generic;
using System.Threading;
using MCPForUnity.Editor.Services.PlayScenarios;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Token-owned, main-thread-only transient records. No application callbacks or payloads are retained.</summary>
    public static class PlayScenarioEventRegistry
    {
        public const int RegistrationLimit = 32;
        public const int RecordLimit = 128;
        private static readonly object Gate = new object();
        private static readonly Dictionary<string, Entry> Entries = new Dictionary<string, Entry>(StringComparer.Ordinal);
        private static int mainThreadId;

        internal sealed class Entry
        {
            internal readonly string Id;
            internal readonly string Generation = Guid.NewGuid().ToString("N");
            internal readonly WeakReference Owner;
            internal WeakReference Token = null;
            internal long Sequence;
            internal bool Exhausted;
            internal readonly Record[] Records = new Record[RecordLimit];
            internal int Count;

            internal Entry(string id, object owner)
            {
                Id = id;
                Owner = new WeakReference(owner);
            }
        }

        internal readonly struct Record
        {
            internal readonly long Sequence;
            internal readonly string Name;

            internal Record(long sequence, string name)
            {
                Sequence = sequence;
                Name = name;
            }
        }

        internal sealed class Page
        {
            internal string Generation;
            internal long Head;
            internal long Floor;
            internal long Dropped;
            internal bool Overflow;
            internal readonly List<Record> Records = new List<Record>(RecordLimit);
        }

        internal sealed class Binding
        {
            private readonly Entry entry;

            internal Binding(Entry value) => entry = value;

            internal string Generation => entry.Generation;

            internal Page Read(long since) => ReadEntry(entry, since);

            internal Page Capture() => ReadEntry(entry, entry.Sequence);
        }

        /// <summary>Keep both the owner and returned token alive. Dispose may run on any thread.</summary>
        public static PlayScenarioEventStream Register(string id, object owner)
        {
            MainThread();
            if (!PlayScenarioTarget.IsValidTargetId(id))
                throw new ArgumentException("Event ID must contain 1-128 permitted identifier characters.", nameof(id));
            if (!LiveOwner(owner))
                throw new ArgumentNullException(nameof(owner));
            lock (Gate)
            {
                var expired = new List<string>(RegistrationLimit);
                foreach (var pair in Entries)
                    if (!Live(pair.Value))
                        expired.Add(pair.Key);
                foreach (string key in expired)
                {
                    Array.Clear(Entries[key].Records, 0, RecordLimit);
                    Entries.Remove(key);
                }
                if (Entries.ContainsKey(id))
                    throw new InvalidOperationException("Event ID is already registered: " + id);
                if (Entries.Count >= RegistrationLimit)
                    throw new InvalidOperationException("Event stream registration capacity exceeded.");
                var entry = new Entry(id, owner);
                var token = new PlayScenarioEventStream(entry);
                entry.Token = new WeakReference(token);
                Entries.Add(id, entry);
                return token;
            }
        }

        internal static bool ValidName(string name) => name != null && name.Length <= 64 && PlayScenarioTarget.IsValidTargetId(name);

        private static bool LiveOwner(object value) => value != null && (!(value is UnityEngine.Object native) || native != null);

        private static bool Live(Entry entry) => entry.Token?.Target != null && LiveOwner(entry.Owner.Target);

        private static void MainThread()
        {
            if (Thread.CurrentThread.ManagedThreadId != Volatile.Read(ref mainThreadId))
                throw new InvalidOperationException("Event registration, emission and reads require the Unity main thread.");
        }

        private static PlayScenarioException Failure(string code, string id) =>
            new PlayScenarioException(
                new PlayScenarioFailure
                {
                    Code = code,
                    Target = id,
                    Message =
                        code == "event_sequence_exhausted"
                            ? "Event sequence is exhausted; register a fresh owned stream."
                            : "The original owned event stream is missing, released, destroyed or replaced.",
                }
            );

        private static void Require(Entry entry)
        {
            if (!Entries.TryGetValue(entry.Id, out Entry current) || !ReferenceEquals(current, entry) || !Live(entry))
                throw Failure("event_stream_unavailable", entry.Id);
            if (entry.Exhausted)
                throw Failure("event_sequence_exhausted", entry.Id);
        }

        internal static Binding Bind(string id)
        {
            MainThread();
            lock (Gate)
            {
                if (!Entries.TryGetValue(id, out Entry entry) || !Live(entry))
                    return null;
                Require(entry);
                return new Binding(entry);
            }
        }

        internal static void Emit(Entry entry, string name)
        {
            MainThread();
            if (!ValidName(name))
                throw new ArgumentException("Event names must contain 1-64 permitted identifier characters.", nameof(name));
            lock (Gate)
            {
                Require(entry);
                if (entry.Sequence == long.MaxValue)
                {
                    entry.Exhausted = true;
                    throw Failure("event_sequence_exhausted", entry.Id);
                }
                long sequence = ++entry.Sequence;
                entry.Records[(int)((sequence - 1) % RecordLimit)] = new Record(sequence, name);
                if (entry.Count < RecordLimit)
                    entry.Count++;
            }
        }

        private static Page ReadEntry(Entry entry, long since)
        {
            MainThread();
            lock (Gate)
            {
                Require(entry);
                if (since < 0 || since > entry.Sequence)
                    throw Failure("event_history_unavailable", entry.Id);
                var page = new Page
                {
                    Generation = entry.Generation,
                    Head = entry.Sequence,
                    Floor = entry.Sequence - entry.Count + 1,
                    Dropped = entry.Sequence - entry.Count,
                };
                // Compare subtraction rather than since+1, which can overflow at long.MaxValue.
                page.Overflow = since < page.Floor - 1;
                if (!page.Overflow)
                {
                    for (int offset = 0; offset < entry.Count; offset++)
                    {
                        long sequence = page.Floor + offset;
                        if (sequence > since)
                            page.Records.Add(entry.Records[(int)((sequence - 1) % RecordLimit)]);
                    }
                }
                return page;
            }
        }

        internal static void Release(Entry entry)
        {
            lock (Gate)
            {
                if (Entries.TryGetValue(entry.Id, out Entry current) && ReferenceEquals(current, entry))
                {
                    Array.Clear(entry.Records, 0, RecordLimit);
                    Entries.Remove(entry.Id);
                }
            }
        }

#if UNITY_EDITOR
        [InitializeOnLoadMethod]
        private static void InitializeEditor() => Volatile.Write(ref mainThreadId, Thread.CurrentThread.ManagedThreadId);
#endif

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
        private static void Reset()
        {
            lock (Gate)
            {
                Volatile.Write(ref mainThreadId, Thread.CurrentThread.ManagedThreadId);
                foreach (Entry entry in Entries.Values)
                    Array.Clear(entry.Records, 0, RecordLimit);
                Entries.Clear();
            }
        }
    }

    /// <summary>Exclusive producer capability. The registry never retains this token strongly.</summary>
    public sealed class PlayScenarioEventStream : IDisposable
    {
        private readonly PlayScenarioEventRegistry.Entry entry;

        internal PlayScenarioEventStream(PlayScenarioEventRegistry.Entry value) => entry = value;

        public void Emit(string name)
        {
            try
            {
                PlayScenarioEventRegistry.Emit(entry, name);
            }
            finally
            {
                GC.KeepAlive(this);
            }
        }

        public void Dispose() => PlayScenarioEventRegistry.Release(entry);
    }
}
