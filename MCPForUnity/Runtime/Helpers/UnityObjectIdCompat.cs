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
            _fullIdCache.Remember(truncated, full);
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
        private readonly Dictionary<int, ulong> entries = new Dictionary<int, ulong>();
        private readonly System.Func<ulong, Object> resolve;
        private readonly System.Func<double> clock;
        private double nextPrune;
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

        public void Remember(int handle, ulong full)
        {
            entries[handle] = full;
            if (IsScheduled)
                return;
            nextPrune = clock() + 10;
            IsScheduled = true;
            EditorApplication.update += Update;
        }

        public Object Resolve(int handle)
        {
            if (!entries.TryGetValue(handle, out ulong full))
                return null;
            Object value = resolve(full);
            if (value == null)
            {
                entries.Remove(handle);
                if (entries.Count == 0)
                    Clear();
            }
            return value;
        }

        private void Update()
        {
            if (clock() < nextPrune)
                return;
            Prune();
            nextPrune = clock() + 10;
        }

        private void OnPlayModeStateChanged(PlayModeStateChange _) => Prune();

        private void Prune()
        {
            foreach (int handle in new List<int>(entries.Keys))
                Resolve(handle);
        }

        public void Clear()
        {
            entries.Clear();
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
