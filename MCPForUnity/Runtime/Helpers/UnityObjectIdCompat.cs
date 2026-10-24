using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
using System.Collections.Generic;
#endif

namespace MCPForUnity.Runtime.Helpers
{
    // Part of MCP for Unity's compat-shim family. See UnityCompatShims.cs in this
    // folder for the full list of shims, the audit policy, and the reflection pattern.
    /// <summary>
    /// Version-gated wrappers for the InstanceID ↔ EntityId migration introduced in Unity 6.5
    /// and tightened in 6.6.
    ///   Forward (Object → int): <see cref="GetInstanceIDCompat"/>
    ///   Reverse (int → Object, Editor-only): <see cref="InstanceIDToObjectCompat"/>
    /// </summary>
    public static class UnityObjectIdCompat
    {
#if UNITY_EDITOR && UNITY_6000_6_OR_NEWER
        // 6000.6 stubs out both EditorUtility.InstanceIDToObject(int) and EntityId's
        // implicit int->EntityId conversion (both throw NotImplementedException at
        // runtime despite existing in the API surface - verified against 6000.6.0f1).
        // The only working reverse path is EntityIdToObject(EntityId) fed a value built
        // via EntityId.FromULong(ulong), which needs the full 64-bit id, not the
        // truncated int this class hands out. So we cache the full value behind the
        // truncated int at mint time and look it up here instead of trying to
        // reconstruct it from the int alone.
        private static readonly EditorObjectIdCache _fullIdCache = new EditorObjectIdCache(
            full => EditorUtility.EntityIdToObject(EntityId.FromULong(full)),
            () => EditorApplication.timeSinceStartup
        );
#endif

        /// <summary>
        /// Returns a session-scoped int handle for the object. On 6.5+ truncates the
        /// EntityId's underlying ulong; lossy but stable within a session and preserves
        /// the int-shaped wire format that older consumers expect. For deserialization
        /// round-trips on 6.5+, prefer the full <c>entityID</c> field.
        /// </summary>
        public static int GetInstanceIDCompat(this Object obj)
        {
            if (obj == null)
            {
                return 0;
            }

#if UNITY_6000_5_OR_NEWER
            ulong full = EntityId.ToULong(obj.GetEntityId());
            int truncated = (int)full;
#if UNITY_EDITOR && UNITY_6000_6_OR_NEWER
            _fullIdCache.Remember(full);
#endif
            return truncated;
#else
            return obj.GetInstanceID();
#endif
        }

#if UNITY_EDITOR
        /// <summary>
        /// Resolves an int instance ID handle back to a UnityEngine.Object.
        ///   Pre-6.0  : EditorUtility.InstanceIDToObject(int)
        ///   6.0–6.5  : EditorUtility.EntityIdToObject(int)  (implicit int→EntityId cast)
        ///   6.6+     : EditorUtility.EntityIdToObject(EntityId.FromULong(full)) using the
        ///              full 64-bit value cached in GetInstanceIDCompat, since the int-only
        ///              paths (including the implicit int→EntityId cast) are unimplemented
        ///              stubs on this Unity version. Only resolves ids minted this session.
        /// </summary>
        public static Object InstanceIDToObjectCompat(int instanceId)
        {
#if UNITY_6000_6_OR_NEWER
            return _fullIdCache.Resolve(instanceId);
#elif UNITY_6000_3_OR_NEWER
            return EditorUtility.EntityIdToObject(instanceId);
#else
            return EditorUtility.InstanceIDToObject(instanceId);
#endif
        }
#endif
    }

#if UNITY_EDITOR
    // Kept version-independent so the ownership policy is exercised on supported older Editors too.
    // Resolve native identities when pruning: a collected managed wrapper is not proof its native object died.
    internal sealed class EditorObjectIdCache : System.IDisposable
    {
        private struct Entry
        {
            // The dictionary key already contains the low 32 bits of the full ID.
            public uint Upper;
            public int Previous;
            public int Next;
        }

        // Intrusive circular links keep the cursor valid across additions/removals without
        // allocating a key snapshot or one managed linked-list node per live handle.
        private readonly Dictionary<int, Entry> entries = new Dictionary<int, Entry>();
        private const int LookupsPerUpdate = 128;
        private readonly System.Func<ulong, Object> resolve;
        private readonly System.Func<double> clock;
        private double nextPrune;
        private int first;
        private int cursor;
        private int remaining;
        private uint mutationVersion;
        public int Count => entries.Count;
        public bool IsScheduled { get; private set; }

        public EditorObjectIdCache(System.Func<ulong, Object> resolve, System.Func<double> clock)
        {
            this.resolve = resolve;
            this.clock = clock;
            AssemblyReloadEvents.beforeAssemblyReload += Clear;
            EditorApplication.quitting += Clear;
            EditorApplication.playModeStateChanged += OnPlayModeStateChanged;
        }

        public void Remember(ulong full)
        {
            int handle = unchecked((int)full);
            uint upper = (uint)(full >> 32);
            if (entries.TryGetValue(handle, out Entry existing))
            {
                if (existing.Upper != upper)
                {
                    mutationVersion++;
                    existing.Upper = upper;
                    entries[handle] = existing;
                }
            }
            else if (entries.Count == 0)
            {
                mutationVersion++;
                entries.Add(
                    handle,
                    new Entry
                    {
                        Upper = upper,
                        Previous = handle,
                        Next = handle,
                    }
                );
                first = handle;
                cursor = handle;
            }
            else
            {
                mutationVersion++;
                int last = entries[first].Previous;
                Entry tail = entries[last];
                tail.Next = handle;
                entries[last] = tail;
                Entry head = entries[first];
                head.Previous = handle;
                entries[first] = head;
                entries.Add(
                    handle,
                    new Entry
                    {
                        Upper = upper,
                        Previous = last,
                        Next = first,
                    }
                );
            }
            if (IsScheduled)
                return;
            nextPrune = clock() + 10;
            IsScheduled = true;
            EditorApplication.update += Update;
        }

        public Object Resolve(int handle)
        {
            if (!entries.TryGetValue(handle, out Entry entry))
                return null;
            uint version = mutationVersion;
            Object value = resolve(((ulong)entry.Upper << 32) | unchecked((uint)handle));
            if (value == null)
            {
                if (version != mutationVersion)
                {
                    // Preserve a replacement identity, but still remove this dead
                    // identity immediately using links updated by the callback.
                    if (!entries.TryGetValue(handle, out Entry current) || current.Upper != entry.Upper)
                        return value;
                    entry = current;
                }
                mutationVersion++;
                Remove(handle, entry);
            }
            return value;
        }

        private void Update()
        {
            if (remaining == 0)
            {
                if (clock() < nextPrune)
                    return;
                BeginSweep();
            }
            uint version = mutationVersion;
            try
            {
                // Keep ordinary live traversal small. Only enter the retirement path
                // after finding a dead handle; both paths share one native-call budget.
                for (int inspected = 0; inspected < LookupsPerUpdate && remaining > 0; inspected++)
                {
                    int handle = cursor;
                    Entry entry = entries[handle];
                    cursor = entry.Next;
                    remaining--;
                    if (resolve(((ulong)entry.Upper << 32) | unchecked((uint)handle)) == null)
                    {
                        if (version == mutationVersion)
                            ContinueSweep(LookupsPerUpdate - inspected - 1, handle, entry, version);
                        break;
                    }
                }
            }
            finally
            {
                // Loading can invoke user callbacks. Public mutations preserve cursor;
                // cover a full current ring from it, without rescanning a loaded prefix.
                if (version != mutationVersion)
                    remaining = entries.Count;
            }
            if (remaining == 0)
                nextPrune = clock() + 10;
        }

        private void ContinueSweep(int lookupsLeft, int deadStart, Entry deadFirst, uint version)
        {
            int deadCount = 1;
            Entry deadLast = deadFirst;
            bool havePrevious = false;
            int previousHandle = 0;
            Entry previous = default;
            try
            {
                if (deadCount == entries.Count)
                {
                    RemoveRun(deadStart, deadFirst, deadLast, deadCount);
                    deadCount = 0;
                    return;
                }
                for (int inspected = 0; inspected < lookupsLeft && remaining > 0; inspected++)
                {
                    int handle = cursor;
                    Entry entry = entries[handle];
                    cursor = entry.Next;
                    remaining--;
                    if (resolve(((ulong)entry.Upper << 32) | unchecked((uint)handle)) == null)
                    {
                        if (deadCount == 0)
                        {
                            deadStart = handle;
                            deadFirst = entry;
                        }
                        deadLast = entry;
                        deadCount++;
                        // Public lookups may have removed most of the original sweep
                        // between updates. Do not circle a wholly dead remaining ring.
                        if (deadCount == entries.Count)
                        {
                            if (version != mutationVersion)
                                break;
                            RemoveRun(deadStart, deadFirst, deadLast, deadCount);
                            deadCount = 0;
                        }
                    }
                    else
                    {
                        if (deadCount > 0)
                        {
                            // Validate cached writes without checking every read-only lookup.
                            if (version != mutationVersion)
                                break;
                            if (deadCount == 1)
                            {
                                // Reuse neighbors only after encountering an isolated dead
                                // entry; all-live traversal needs no predecessor copies.
                                if (deadFirst.Previous == handle)
                                {
                                    // Both neighbors are the same surviving entry.
                                    entry.Previous = handle;
                                    entry.Next = handle;
                                }
                                else
                                {
                                    if (!havePrevious || previousHandle != deadFirst.Previous)
                                        previous = entries[deadFirst.Previous];
                                    previous.Next = handle;
                                    entries[deadFirst.Previous] = previous;
                                    entry.Previous = deadFirst.Previous;
                                }
                                entries[handle] = entry;
                                if (first == deadStart)
                                    first = handle;
                                if (cursor == deadStart)
                                    cursor = handle;
                                entries.Remove(deadStart);
                                havePrevious = true;
                            }
                            else
                            {
                                RemoveRun(deadStart, deadFirst, deadLast, deadCount);
                                havePrevious = false;
                            }
                            deadCount = 0;
                        }
                        if (havePrevious)
                        {
                            previousHandle = handle;
                            previous = entry;
                        }
                    }
                }
            }
            finally
            {
                // An unchanged epoch permits cleanup even when native lookup throws.
                // The outer update handles invalidated runs and resumes from its cursor.
                if (version == mutationVersion && deadCount > 0)
                    RemoveRun(deadStart, deadFirst, deadLast, deadCount);
            }
        }

        private void OnPlayModeStateChanged(PlayModeStateChange _) => BeginSweep();

        private void BeginSweep()
        {
            // Do not restart a sweep during repeated transitions. New handles join the
            // circular list and at the latest participate in the next complete sweep.
            if (remaining > 0 || entries.Count == 0)
                return;
            cursor = first;
            remaining = entries.Count;
        }

        private void Remove(int handle, Entry entry)
        {
            if (entries.Count == 1)
            {
                entries.Remove(handle);
                StopMaintenance();
                return;
            }
            Entry previous = entries[entry.Previous];
            previous.Next = entry.Next;
            entries[entry.Previous] = previous;
            Entry next = entries[entry.Next];
            next.Previous = entry.Previous;
            entries[entry.Next] = next;
            if (first == handle)
                first = entry.Next;
            if (cursor == handle)
                cursor = entry.Next;
            entries.Remove(handle);
        }

        private void RemoveRun(int start, Entry head, Entry tail, int count)
        {
            if (count == 1)
            {
                Remove(start, head);
                return;
            }
            int handle = start;
            for (int removed = 0; removed < count; removed++)
            {
                int next = entries[handle].Next;
                entries.Remove(handle);
                if (first == handle)
                    first = tail.Next;
                if (cursor == handle)
                    cursor = tail.Next;
                handle = next;
            }
            if (entries.Count == 0)
            {
                // Remove already cleared the occupied slots. Avoid clearing the entire
                // retained capacity on the final bounded update of a large sweep.
                StopMaintenance();
                return;
            }
            Entry previous = entries[head.Previous];
            previous.Next = tail.Next;
            entries[head.Previous] = previous;
            Entry nextEntry = entries[tail.Next];
            nextEntry.Previous = head.Previous;
            entries[tail.Next] = nextEntry;
        }

        public void Clear()
        {
            mutationVersion++;
            entries.Clear();
            StopMaintenance();
        }

        private void StopMaintenance()
        {
            first = 0;
            cursor = 0;
            remaining = 0;
            EditorApplication.update -= Update;
            IsScheduled = false;
        }

        public void Dispose()
        {
            Clear();
            AssemblyReloadEvents.beforeAssemblyReload -= Clear;
            EditorApplication.quitting -= Clear;
            EditorApplication.playModeStateChanged -= OnPlayModeStateChanged;
        }
    }
#endif
}
