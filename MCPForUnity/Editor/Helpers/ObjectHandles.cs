using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Bounded handles to original loaded references; no serialized identity or rediscovery.</summary>
    [InitializeOnLoad]
    internal static class ObjectHandles
    {
        private const string Prefix = "mcp-oh-v2.";
        private const int EntryLimit = 512;
        private const double LifetimeSeconds = 600;
        private static readonly Dictionary<string, Entry> Entries = new Dictionary<string, Entry>(StringComparer.Ordinal);
        private static string _epoch = Guid.NewGuid().ToString("N");
        private static long _access;
        private static Action<string> OperationForTests = null;
        private static Func<double> ClockForTests = null;

        static ObjectHandles()
        {
            EditorSceneManager.sceneClosing += (_, __) => Invalidate();
            EditorSceneManager.sceneClosed += _ => Invalidate();
            EditorSceneManager.sceneOpened += (_, __) => Invalidate();
            EditorSceneManager.newSceneCreated += (_, __, ___) => Invalidate();
            SceneManager.sceneUnloaded += _ => Invalidate();
            EditorApplication.playModeStateChanged += OnPlayModeStateChanged;
            Undo.undoRedoPerformed += Invalidate;
            AssemblyReloadEvents.beforeAssemblyReload += Invalidate;
        }

        private static void OnPlayModeStateChanged(PlayModeStateChange state) => Invalidate();

        private static void Invalidate()
        {
            Entries.Clear();
            _epoch = Guid.NewGuid().ToString("N");
            _access = 0;
        }

        internal sealed class Failure : Exception
        {
            internal readonly string Code;

            internal Failure(string code, string message)
                : base(message) => Code = code;

            internal ErrorResponse Response() => new ErrorResponse(Code, new { reason = Code, message = Text(Message) });
        }

        internal sealed class Resolved
        {
            internal readonly Object Object;
            internal readonly JObject Descriptor;
            internal readonly string Handle;

            internal Resolved(Object target, JObject descriptor, string handle)
            {
                Object = target;
                Descriptor = descriptor;
                Handle = handle;
            }
        }

        private sealed class Entry
        {
            internal WeakReference Target;
            internal WeakReference Owner;
            internal Scene Scene;
            internal bool Stage;
            internal string Epoch;
            internal double Expires;
            internal long Access;
        }

        private static Failure Fail(string code, string message) => new Failure(code, message);

        private static double Now => ClockForTests?.Invoke() ?? EditorApplication.timeSinceStartup;

        private static void CheckEpoch(string epoch)
        {
            if (epoch != _epoch)
                throw Fail("stale_handle", "The Editor session changed during the operation; capture a fresh handle explicitly.");
        }

        internal static T Operation<T>(string operation, Func<T> action, CancellationToken token)
        {
            string epoch = _epoch;
            token.ThrowIfCancellationRequested();
            OperationForTests?.Invoke("before:" + operation);
            token.ThrowIfCancellationRequested();
            CheckEpoch(epoch);
            T result = action();
            OperationForTests?.Invoke("after:" + operation);
            token.ThrowIfCancellationRequested();
            CheckEpoch(epoch);
            return result;
        }

        internal static Object Lookup(int id, CancellationToken token) =>
            Operation("instance_lookup", () => UnityObjectIdCompat.InstanceIDToObjectCompat(id), token);

        internal static JObject Capture(Object target, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            string epoch = _epoch;
            GameObject owner = Owner(target);
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            bool inStage = stage != null && owner.scene == stage.scene;
            if (
                EditorUtility.IsPersistent(target)
                || !owner.scene.IsValid()
                || !owner.scene.isLoaded
                || (EditorSceneManager.IsPreviewScene(owner.scene) && !inStage)
            )
                throw Fail("unsupported_context", "Only loaded scene GameObjects and Components, including the current prefab stage, support session handles.");
            double now = Now;
            var entry = new Entry
            {
                Target = new WeakReference(target),
                Owner = new WeakReference(owner),
                Scene = owner.scene,
                Stage = inStage,
                Epoch = epoch,
                Expires = now + LifetimeSeconds,
                Access = ++_access,
            };
            string handle = Prefix + epoch + "_" + Guid.NewGuid().ToString("N");
            JObject descriptor = Operation(
                "descriptor",
                () =>
                {
                    ValidateEntry(entry, token);
                    return Describe(target, handle, entry);
                },
                token
            );
            ValidateEntry(entry, token);
            foreach (
                string expired in Entries
                    .Where(pair => pair.Value.Expires <= now || !(pair.Value.Target.Target is Object value) || value == null)
                    .Select(pair => pair.Key)
                    .ToArray()
            )
                Entries.Remove(expired);
            if (Entries.Count >= EntryLimit)
                Entries.Remove(Entries.OrderBy(pair => pair.Value.Access).First().Key);
            Entries.Add(handle, entry);
            try
            {
                ValidateEntry(entry, token);
                return descriptor;
            }
            catch
            {
                Entries.Remove(handle);
                throw;
            }
        }

        internal static Resolved Resolve(string handle, CancellationToken token)
        {
            Entry entry = ReadEntry(handle, token, out Object target);
            JObject descriptor = Operation(
                "descriptor",
                () =>
                {
                    ValidateEntry(entry, token);
                    return Describe(target, handle, entry);
                },
                token
            );
            Entry final = ReadEntry(handle, token, out Object finalTarget);
            if (!ReferenceEquals(entry, final) || !ReferenceEquals(target, finalTarget))
                throw Fail("stale_handle", "The original session reference is no longer available.");
            entry.Access = ++_access;
            return new Resolved(target, descriptor, handle);
        }

        internal static void Validate(Resolved resolved, CancellationToken token)
        {
            ReadEntry(resolved.Handle, token, out Object target);
            if (!ReferenceEquals(target, resolved.Object))
                throw Fail("stale_handle", "The original session reference changed during the read.");
        }

        private static Entry ReadEntry(string handle, CancellationToken token, out Object target)
        {
            token.ThrowIfCancellationRequested();
            ValidateShape(handle);
            if (!Entries.TryGetValue(handle, out Entry entry))
                throw Fail("stale_handle", "The session handle expired, was evicted, or belongs to another Editor epoch; capture again explicitly.");
            try
            {
                target = ValidateEntry(entry, token);
                return entry;
            }
            catch (Failure)
            {
                Entries.Remove(handle);
                throw;
            }
        }

        private static Object ValidateEntry(Entry entry, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            CheckEpoch(entry.Epoch);
            Object target = entry.Target.Target as Object;
            var originalOwner = entry.Owner.Target as GameObject;
            double now = Now;
            token.ThrowIfCancellationRequested();
            CheckEpoch(entry.Epoch);
            if (entry.Expires <= now || target == null || originalOwner == null)
                throw Fail("stale_handle", "The original reference or fixed session lifetime is no longer valid.");
            GameObject owner = Owner(target);
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            bool inStage = stage != null && owner.scene == stage.scene;
            if (
                !ReferenceEquals(owner, originalOwner)
                || owner.scene != entry.Scene
                || !entry.Scene.IsValid()
                || !entry.Scene.isLoaded
                || entry.Stage != inStage
                || EditorUtility.IsPersistent(target)
            )
                throw Fail("stale_handle", "The original owner, scene or prefab-stage context is no longer valid.");
            token.ThrowIfCancellationRequested();
            CheckEpoch(entry.Epoch);
            return target;
        }

        private static void ValidateShape(string handle)
        {
            if (handle == null || handle.Length == 0 || handle.Length > 1024 || handle.Any(c => c > 127))
                throw Fail("invalid_handle", "Expected a bounded opaque session handle.");
            if (!handle.StartsWith(Prefix, StringComparison.Ordinal))
                throw Fail("unsupported_handle_version", "Only v2 session handles are supported; capture a fresh handle.");
            if (handle.Length != Prefix.Length + 65 || handle[Prefix.Length + 32] != '_')
                throw Fail("invalid_handle", "Malformed session handle.");
            for (int i = Prefix.Length; i < handle.Length; i++)
                if (i != Prefix.Length + 32 && !(handle[i] >= '0' && handle[i] <= '9' || handle[i] >= 'a' && handle[i] <= 'f'))
                    throw Fail("invalid_handle", "Malformed session handle.");
        }

        private static GameObject Owner(Object target)
        {
            if (target == null)
                throw Fail("stale_handle", "The original object no longer exists.");
            if (target is GameObject go)
                return go;
            if (target is Component component)
                return component.gameObject;
            throw Fail("unsupported_context", "Only GameObject and Component session handles are supported.");
        }

        private static JObject Describe(Object target, string handle, Entry entry)
        {
            var descriptor = new JObject
            {
                ["contractVersion"] = 2,
                ["handle"] = handle,
                ["persistence"] = "session",
                ["objectKind"] = target is GameObject ? "gameobject" : "component",
                ["instanceID"] = target.GetInstanceIDCompat(),
                ["name"] = Text(target.name),
                ["typeName"] = Text(target.GetType().FullName),
                ["validUntil"] = new JObject
                {
                    ["editorTimeSeconds"] = entry.Expires,
                    ["epoch"] = entry.Epoch,
                    ["ttlSeconds"] = LifetimeSeconds,
                    ["mayEvict"] = true,
                },
            };
            if (target is Component component)
                descriptor["gameObjectInstanceID"] = component.gameObject.GetInstanceIDCompat();
            return descriptor;
        }

        private static string Text(string value)
        {
            if (value == null || value.Length <= 256)
                return value;
            return value.Substring(0, char.IsHighSurrogate(value[255]) ? 255 : 256);
        }
    }
}
