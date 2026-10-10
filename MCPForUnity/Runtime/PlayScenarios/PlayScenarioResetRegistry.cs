using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services.PlayScenarios;
using UnityEngine;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Explicit project-owned reset; the runner never discovers or reflects reset methods.</summary>
    public interface IPlayScenarioResetParticipant
    {
        void BeginReset();
        bool IsResetComplete { get; }
    }

    /// <summary>Bounded weak registrations with exact case-sensitive IDs and removable ownership tokens.</summary>
    public static class PlayScenarioResetRegistry
    {
        public const int RegistrationLimit = 128;
        private static readonly object Gate = new object();
        private static readonly Dictionary<string, Entry> Entries = new Dictionary<string, Entry>(StringComparer.Ordinal);

        private sealed class Entry
        {
            internal readonly WeakReference Participant;

            internal Entry(IPlayScenarioResetParticipant participant) => Participant = new WeakReference(participant);
        }

        private sealed class Registration : IDisposable
        {
            private string _id;
            private readonly Entry _entry;

            internal Registration(string id, Entry entry)
            {
                _id = id;
                _entry = entry;
            }

            public void Dispose()
            {
                lock (Gate)
                {
                    if (_id != null && Entries.TryGetValue(_id, out Entry current) && ReferenceEquals(current, _entry))
                        Entries.Remove(_id);
                    _id = null;
                }
            }
        }

        public static IDisposable Register(string id, IPlayScenarioResetParticipant participant)
        {
            if (!PlayScenarioTarget.IsValidTargetId(id))
                throw new ArgumentException("Reset ID must match [A-Za-z0-9][A-Za-z0-9_.:-]{0,127}.", nameof(id));
            if (!Alive(participant))
                throw new ArgumentNullException(nameof(participant));
            lock (Gate)
            {
                var expired = new List<string>();
                foreach (var pair in Entries)
                    if (!Alive(pair.Value.Participant.Target))
                        expired.Add(pair.Key);
                foreach (string key in expired)
                    Entries.Remove(key);
                if (Entries.ContainsKey(id))
                    throw new InvalidOperationException("A reset participant is already registered for ID: " + id);
                if (Entries.Count >= RegistrationLimit)
                    throw new InvalidOperationException("Reset participant registration exceeds its bounded capacity.");
                var entry = new Entry(participant);
                Entries.Add(id, entry);
                return new Registration(id, entry);
            }
        }

        private static bool Alive(object participant) => participant != null && (!(participant is UnityEngine.Object native) || native != null);

        private static PlayScenarioException Missing(string id) =>
            new PlayScenarioException(
                new PlayScenarioFailure
                {
                    Code = "reset_participant_unavailable",
                    Target = id,
                    Expected = "the original live registered reset participant",
                    Actual = "missing, destroyed, released or replaced",
                    Message = "Reset participant is unavailable: " + id,
                }
            );

        /// <summary>Owns only IDs and weak entries. Strong borrows exist only during Begin or a completion poll.</summary>
        internal sealed class Snapshot
        {
            private readonly string[] _ids;
            private readonly Entry[] _entries;
            private bool _began;

            internal Snapshot(IList<string> ids)
            {
                if (ids == null || ids.Count < 1 || ids.Count > 16)
                    throw new ArgumentException("reset_ids must contain 1-16 unique stable identifiers.");
                _ids = new string[ids.Count];
                _entries = new Entry[ids.Count];
                var unique = new HashSet<string>(StringComparer.Ordinal);
                lock (Gate)
                {
                    for (int index = 0; index < ids.Count; index++)
                    {
                        string id = ids[index];
                        if (!PlayScenarioTarget.IsValidTargetId(id) || !unique.Add(id))
                            throw new ArgumentException("reset_ids must contain unique case-sensitive stable identifiers.");
                        if (!Entries.TryGetValue(id, out Entry entry) || !Alive(entry.Participant.Target))
                            throw Missing(id);
                        _ids[index] = id;
                        _entries[index] = entry;
                    }
                }
            }

            private IPlayScenarioResetParticipant Borrow(int index)
            {
                lock (Gate)
                {
                    if (!Entries.TryGetValue(_ids[index], out Entry current) || !ReferenceEquals(current, _entries[index]))
                        throw Missing(_ids[index]);
                    object participant = current.Participant.Target;
                    if (!Alive(participant))
                        throw Missing(_ids[index]);
                    return (IPlayScenarioResetParticipant)participant;
                }
            }

            internal void Begin()
            {
                if (_began)
                    throw new InvalidOperationException("Reset BeginReset cannot be replayed.");
                _began = true;
                // Resolve the entire requested set before any project code runs.
                var participants = new IPlayScenarioResetParticipant[_ids.Length];
                try
                {
                    for (int index = 0; index < participants.Length; index++)
                        participants[index] = Borrow(index);
                    for (int index = 0; index < participants.Length; index++)
                    {
                        if (!ReferenceEquals(Borrow(index), participants[index]))
                            throw Missing(_ids[index]);
                        participants[index].BeginReset();
                    }
                }
                finally
                {
                    Array.Clear(participants, 0, participants.Length);
                }
            }

            internal bool Complete()
            {
                bool complete = true;
                for (int index = 0; index < _ids.Length; index++)
                    complete &= Borrow(index).IsResetComplete;
                return complete;
            }
        }

        internal static Snapshot Resolve(IList<string> ids) => new Snapshot(ids);
    }
}
