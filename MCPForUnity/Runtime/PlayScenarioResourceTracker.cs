using System;
using System.Collections.Generic;
using System.Runtime.CompilerServices;
using System.Threading;
using MCPForUnity.Runtime.Helpers;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace MCPForUnity.Runtime
{
    /// <summary>Opt-in resource registrations. Entries retain bounded scalars and weak references, never game resources or release callbacks.</summary>
    public static class PlayScenarioResourceTracker
    {
        public const int RegistrationLimit = 4096;
#if UNITY_EDITOR
        private static readonly object Gate = new object();
        private static readonly Dictionary<ulong, ScriptableRegistration> ScriptableObjects = new Dictionary<ulong, ScriptableRegistration>();
        private static readonly Dictionary<long, PlayScenarioRegisteredResourceInfo> Tokens = new Dictionary<long, PlayScenarioRegisteredResourceInfo>();
        private static long _nextToken;
        private static int _registrationFailures;
        private static int _mainThreadId;

        /// <summary>Register a runtime ScriptableObject clone on the Unity main thread. Destroy the native object during the game's cleanup.</summary>
        public static void RegisterScriptableObject(
            ScriptableObject resource,
            string owner = null,
            [CallerFilePath] string sourceFile = "",
            [CallerMemberName] string sourceMember = "",
            [CallerLineNumber] int sourceLine = 0
        )
        {
            if (resource == null)
                throw new ArgumentException("A live runtime ScriptableObject is required.", nameof(resource));
            if (EditorUtility.IsPersistent(resource))
                throw new ArgumentException("Persistent ScriptableObject assets cannot be registered as runtime resources.", nameof(resource));
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
                var details = new PlayScenarioRegisteredResourceInfo(
                    ++_nextToken,
                    "scriptable_object",
                    owner,
                    resource.GetType().FullName,
                    resource.name,
                    sourceFile,
                    sourceMember,
                    sourceLine
                );
                ScriptableObjects.Add(id, new ScriptableRegistration(resource, details));
            }
        }

        /// <summary>Dispose this tracking token after the game unsubscribes. At capacity, native lifetime reclamation requires the main thread.</summary>
        public static IDisposable RegisterSubscription(
            string owner = null,
            [CallerFilePath] string sourceFile = "",
            [CallerMemberName] string sourceMember = "",
            [CallerLineNumber] int sourceLine = 0
        ) => RegisterToken(true, owner, sourceFile, sourceMember, sourceLine);

        /// <summary>Dispose this tracking token after the game releases its handle. At capacity, native lifetime reclamation requires the main thread.</summary>
        public static IDisposable RegisterHandle(
            string owner = null,
            [CallerFilePath] string sourceFile = "",
            [CallerMemberName] string sourceMember = "",
            [CallerLineNumber] int sourceLine = 0
        ) => RegisterToken(false, owner, sourceFile, sourceMember, sourceLine);

        private static IDisposable RegisterToken(bool subscription, string owner, string sourceFile, string sourceMember, int sourceLine)
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
                Tokens.Add(
                    id,
                    new PlayScenarioRegisteredResourceInfo(
                        id,
                        subscription ? "subscription" : "handle",
                        owner,
                        null,
                        null,
                        sourceFile,
                        sourceMember,
                        sourceLine
                    )
                );
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
                var details = new List<PlayScenarioRegisteredResourceInfo>(ScriptableObjects.Count + Tokens.Count);
                foreach (ScriptableRegistration registration in ScriptableObjects.Values)
                {
                    scriptableIds.Add(registration.Id);
                    details.Add(registration.Details);
                }
                var subscriptionIds = new List<long>();
                var handleIds = new List<long>();
                foreach (var token in Tokens)
                {
                    (token.Value.Kind == "subscription" ? subscriptionIds : handleIds).Add(token.Key);
                    details.Add(token.Value);
                }
                return new PlayScenarioRegisteredResources
                {
                    ScriptableObjectIds = scriptableIds.ToArray(),
                    SubscriptionIds = subscriptionIds.ToArray(),
                    HandleIds = handleIds.ToArray(),
                    ScriptableObjectCount = ScriptableObjects.Count,
                    SubscriptionCount = subscriptionIds.Count,
                    HandleCount = handleIds.Count,
                    RegistrationFailureCount = _registrationFailures,
                    ResourceDetails = details.ToArray(),
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
                var wrapper = entry.Value.Reference.Target as ScriptableObject;
                var resource = !ReferenceEquals(wrapper, null) && wrapper == null ? null : ResolveNativeIdentity(entry.Key);
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
            internal long Id => Details.Id;
            internal readonly WeakReference Reference;
            internal readonly PlayScenarioRegisteredResourceInfo Details;

            internal ScriptableRegistration(ScriptableObject resource, PlayScenarioRegisteredResourceInfo details)
            {
                Reference = new WeakReference(resource);
                Details = details;
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
        public static void RegisterScriptableObject(
            ScriptableObject resource,
            string owner = null,
            [CallerFilePath] string sourceFile = "",
            [CallerMemberName] string sourceMember = "",
            [CallerLineNumber] int sourceLine = 0
        ) { }

        public static IDisposable RegisterSubscription(
            string owner = null,
            [CallerFilePath] string sourceFile = "",
            [CallerMemberName] string sourceMember = "",
            [CallerLineNumber] int sourceLine = 0
        ) => NoopRegistration.Instance;

        public static IDisposable RegisterHandle(
            string owner = null,
            [CallerFilePath] string sourceFile = "",
            [CallerMemberName] string sourceMember = "",
            [CallerLineNumber] int sourceLine = 0
        ) => NoopRegistration.Instance;

        public static PlayScenarioRegisteredResources Capture() =>
            new PlayScenarioRegisteredResources { Error = "Native resource lifetime verification is supported only in the Editor." };

        private sealed class NoopRegistration : IDisposable
        {
            internal static readonly NoopRegistration Instance = new NoopRegistration();

            public void Dispose() { }
        }
#endif
    }

    /// <summary>Immutable registration-time scalar attribution. Caller file paths are reduced to basenames before storage.</summary>
    public sealed class PlayScenarioRegisteredResourceInfo
    {
        public long Id { get; }
        public string Kind { get; }
        public string Owner { get; }
        public string TypeName { get; }
        public string ResourceName { get; }
        public string SourceFile { get; }
        public string SourceMember { get; }
        public int SourceLine { get; }

        public PlayScenarioRegisteredResourceInfo(
            long id,
            string kind,
            string owner = null,
            string typeName = null,
            string resourceName = null,
            string sourceFile = null,
            string sourceMember = null,
            int sourceLine = 0
        )
        {
            if (kind != "scriptable_object" && kind != "subscription" && kind != "handle")
                throw new ArgumentException("A supported registered resource kind is required.", nameof(kind));
            Id = id;
            Kind = kind;
            Owner = Bounded(owner, 128);
            TypeName = Bounded(typeName, 256);
            ResourceName = Bounded(resourceName, 128);
            SourceFile = Bounded(Basename(sourceFile), 128);
            SourceMember = Bounded(sourceMember, 128);
            SourceLine = Math.Max(0, sourceLine);
        }

        private static string Basename(string value)
        {
            if (value == null)
                return null;
            int separator = Math.Max(value.LastIndexOf('/'), value.LastIndexOf('\\'));
            separator = Math.Max(separator, value.LastIndexOf(':'));
            return value.Substring(separator + 1);
        }

        private static string Bounded(string value, int limit)
        {
            if (value == null)
                return null;
            int length = Math.Min(value.Length, limit);
            if (length > 0 && char.IsHighSurrogate(value[length - 1]))
                length--;
            var characters = new char[length];
            for (int index = 0; index < length; index++)
            {
                char character = value[index];
                bool invalidSurrogate =
                    char.IsSurrogate(character)
                    && !(
                        char.IsHighSurrogate(character)
                            ? index + 1 < length && char.IsLowSurrogate(value[index + 1])
                            : index > 0 && char.IsHighSurrogate(value[index - 1])
                    );
                characters[index] =
                    char.IsControl(character)
                    || char.GetUnicodeCategory(character) == System.Globalization.UnicodeCategory.Format
                    || character == '\u2028'
                    || character == '\u2029'
                    || invalidSurrogate
                        ? ' '
                        : character;
            }
            return new string(characters).Trim();
        }
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
        private PlayScenarioRegisteredResourceInfo[] _resourceDetails = Array.Empty<PlayScenarioRegisteredResourceInfo>();

        /// <summary>A defensive bounded snapshot of scalar descriptions for registered IDs.</summary>
        public PlayScenarioRegisteredResourceInfo[] ResourceDetails
        {
            get => (PlayScenarioRegisteredResourceInfo[])_resourceDetails.Clone();
            set
            {
                int length = Math.Min(value?.Length ?? 0, PlayScenarioResourceTracker.RegistrationLimit);
                _resourceDetails = new PlayScenarioRegisteredResourceInfo[length];
                if (length > 0)
                    Array.Copy(value, _resourceDetails, length);
            }
        }
    }
}
