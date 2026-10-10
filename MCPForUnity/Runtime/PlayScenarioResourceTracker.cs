using System;
using System.Collections.Generic;
using System.Threading;
using MCPForUnity.Runtime.Helpers;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace MCPForUnity.Runtime
{
    /// <summary>Opt-in resource registrations. Entries retain IDs and weak references, never game resources or release callbacks.</summary>
    public static class PlayScenarioResourceTracker
    {
        public const int RegistrationLimit = 4096;
#if UNITY_EDITOR
        private static readonly object Gate = new object();
        private static readonly Dictionary<ulong, ScriptableRegistration> ScriptableObjects = new Dictionary<ulong, ScriptableRegistration>();
        private static readonly Dictionary<long, bool> Tokens = new Dictionary<long, bool>();
        private static long _nextToken;
        private static int _registrationFailures;
        private static int _mainThreadId;

        /// <summary>Register a runtime ScriptableObject clone on the Unity main thread. Destroy the native object during the game's cleanup.</summary>
        public static void RegisterScriptableObject(ScriptableObject resource)
        {
            if (resource == null)
                throw new ArgumentException("A live runtime ScriptableObject is required.", nameof(resource));
#if UNITY_EDITOR
            if (EditorUtility.IsPersistent(resource))
                throw new ArgumentException("Persistent ScriptableObject assets cannot be registered as runtime resources.", nameof(resource));
#endif
            ulong id = NativeIdentity(resource);
            lock (Gate)
            {
                _mainThreadId = Thread.CurrentThread.ManagedThreadId;
                if (ScriptableObjects.TryGetValue(id, out ScriptableRegistration existing))
                {
                    var previous = existing.Reference.Target as ScriptableObject;
                    if (previous == resource)
                        return;
                    ScriptableObjects.Remove(id);
                }
                if (ScriptableObjects.Count + Tokens.Count >= RegistrationLimit)
                    PruneScriptableObjects();
                EnsureCapacity();
                ScriptableObjects.Add(id, new ScriptableRegistration(++_nextToken, resource));
            }
        }

        /// <summary>Dispose this tracking token after the game unsubscribes. At capacity, native lifetime reclamation requires the main thread.</summary>
        public static IDisposable RegisterSubscription() => RegisterToken(true);

        /// <summary>Dispose this tracking token after the game releases its handle. At capacity, native lifetime reclamation requires the main thread.</summary>
        public static IDisposable RegisterHandle() => RegisterToken(false);

        private static IDisposable RegisterToken(bool subscription)
        {
            lock (Gate)
            {
                if (ScriptableObjects.Count > 0 && ScriptableObjects.Count + Tokens.Count >= RegistrationLimit)
                {
                    if (Thread.CurrentThread.ManagedThreadId == _mainThreadId)
                        PruneScriptableObjects();
                    else
                        EnsureCapacity(
                            "Resource capacity cannot be reclaimed off the Unity main thread. Capture registered resources on the main thread before retrying."
                        );
                }
                EnsureCapacity();
                long id = ++_nextToken;
                Tokens.Add(id, subscription);
                return new RegistrationToken(id);
            }
        }

        private static void EnsureCapacity(string error = "Play scenario resource registration limit exceeded.")
        {
            if (ScriptableObjects.Count + Tokens.Count < RegistrationLimit)
                return;
            if (_registrationFailures < int.MaxValue)
                _registrationFailures++;
            throw new InvalidOperationException(error);
        }

        /// <summary>Capture only registered resources on the Unity main thread. Editor snapshots resolve native IDs so collected wrappers cannot hide a live native clone.</summary>
        public static PlayScenarioRegisteredResources Capture()
        {
            lock (Gate)
            {
                PruneScriptableObjects();
                var scriptableIds = new List<long>();
                foreach (ScriptableRegistration registration in ScriptableObjects.Values)
                    scriptableIds.Add(registration.Id);
                var subscriptionIds = new List<long>();
                var handleIds = new List<long>();
                foreach (var token in Tokens)
                    (token.Value ? subscriptionIds : handleIds).Add(token.Key);
                int subscriptions = 0;
                foreach (bool subscription in Tokens.Values)
                    if (subscription)
                        subscriptions++;
                return new PlayScenarioRegisteredResources
                {
                    ScriptableObjectIds = scriptableIds.ToArray(),
                    SubscriptionIds = subscriptionIds.ToArray(),
                    HandleIds = handleIds.ToArray(),
                    ScriptableObjectCount = ScriptableObjects.Count,
                    SubscriptionCount = subscriptions,
                    HandleCount = Tokens.Count - subscriptions,
                    RegistrationFailureCount = _registrationFailures,
#if !UNITY_EDITOR
                    Error = "Native ScriptableObject lifetime verification is supported only in the Editor.",
#endif
                };
            }
        }

        private static ulong NativeIdentity(ScriptableObject resource)
        {
#if UNITY_6000_6_OR_NEWER
            return EntityId.ToULong(resource.GetEntityId());
#else
            return unchecked((ulong)(uint)resource.GetInstanceIDCompat());
#endif
        }

        private static ScriptableObject ResolveNativeIdentity(ulong id)
        {
#if UNITY_6000_6_OR_NEWER
            return EditorUtility.EntityIdToObject(EntityId.FromULong(id)) as ScriptableObject;
#else
            return UnityObjectIdCompat.InstanceIDToObjectCompat(unchecked((int)(uint)id)) as ScriptableObject;
#endif
        }

        private static void PruneScriptableObjects()
        {
            var removed = new List<ulong>();
            foreach (var entry in ScriptableObjects)
            {
#if UNITY_EDITOR
                var wrapper = entry.Value.Reference.Target as ScriptableObject;
                var resource = !ReferenceEquals(wrapper, null) && wrapper == null ? null : ResolveNativeIdentity(entry.Key);
#else
                var resource = entry.Value.Reference.Target as ScriptableObject;
#endif
                if (resource == null)
                    removed.Add(entry.Key);
            }
            foreach (ulong id in removed)
                ScriptableObjects.Remove(id);
        }

        [InitializeOnLoadMethod]
        private static void InitializeEditorMainThread()
        {
            lock (Gate)
                _mainThreadId = Thread.CurrentThread.ManagedThreadId;
        }

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
        private static void ResetOnPlayEntry()
        {
            lock (Gate)
            {
                _mainThreadId = Thread.CurrentThread.ManagedThreadId;
                ScriptableObjects.Clear();
                Tokens.Clear();
                _registrationFailures = 0;
                // Token IDs never repeat when domain reload is disabled; old tokens cannot release a new registration.
            }
        }

        private sealed class ScriptableRegistration
        {
            internal readonly long Id;
            internal readonly WeakReference Reference;

            internal ScriptableRegistration(long id, ScriptableObject resource)
            {
                Id = id;
                Reference = new WeakReference(resource);
            }
        }

        private sealed class RegistrationToken : IDisposable
        {
            private long _id;

            internal RegistrationToken(long id) => _id = id;

            public void Dispose()
            {
                long id = Interlocked.Exchange(ref _id, 0);
                if (id == 0)
                    return;
                lock (Gate)
                    Tokens.Remove(id);
            }
        }
#else
        public static void RegisterScriptableObject(ScriptableObject resource) { }

        public static IDisposable RegisterSubscription() => NoopRegistration.Instance;

        public static IDisposable RegisterHandle() => NoopRegistration.Instance;

        public static PlayScenarioRegisteredResources Capture() =>
            new PlayScenarioRegisteredResources { Error = "Native resource lifetime verification is supported only in the Editor." };

        private sealed class NoopRegistration : IDisposable
        {
            internal static readonly NoopRegistration Instance = new NoopRegistration();

            public void Dispose() { }
        }
#endif
    }

    public sealed class PlayScenarioRegisteredResources
    {
        public long[] ScriptableObjectIds = Array.Empty<long>();
        public long[] SubscriptionIds = Array.Empty<long>();
        public long[] HandleIds = Array.Empty<long>();
        public int ScriptableObjectCount;
        public int SubscriptionCount;
        public int HandleCount;
        public int RegistrationFailureCount;
        public string Error;
    }
}
