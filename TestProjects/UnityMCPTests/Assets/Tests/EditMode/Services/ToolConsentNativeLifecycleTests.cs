using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    [Parallelizable(ParallelScope.None)]
    public class ToolConsentNativeLifecycleTests
    {
        [UnityTest, Explicit("Owned CORE12 reload bootstrap only; never select in ordinary49.")]
        public IEnumerator ActualDomainReload_RetainsSessionAndProjectScopes()
        {
            try
            {
                NativeConsentLifecycle.Begin();
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                // Only iterator position survives; assertions reload their owned state.
                NativeConsentLifecycle.AfterReload();
            }
            finally
            {
                NativeConsentLifecycle.EndReloadAttempt();
            }
        }

        [Test, Explicit("Separate root-owned reopening of the same physical A project.")]
        public void ActualRestart_ExpiresSessionAndKeepsProjectScope() => NativeConsentLifecycle.AfterRestart();

        [Test, Explicit("Separate root-owned fresh B project; A grant must still exist.")]
        public void ActualOtherRoot_DoesNotInheritRememberedGrant() => NativeConsentLifecycle.OtherRoot("other-root");

        [Test, Explicit("Root must independently bind the actual moved/copied project receipt.")]
        public void ActualMovedRoot_DoesNotInheritRememberedGrant() => NativeConsentLifecycle.OtherRoot("moved-root");

        [Test, Explicit("Mandatory final/failure cleanup in the original owned A project.")]
        public void RestoreOnlyRecordedOriginalProjectKeys() => NativeConsentLifecycle.Cleanup();
    }

    // Native storage and strict real identity, never an enable-all discovery adapter.
    // This scope is usable only in an explicitly admitted disposable Editor.
    internal sealed class NativeConsentProjectScope : IDisposable
    {
        internal static readonly string[] Tools = { "execute_code", "manage_script" };
        private static readonly FieldInfo ConsentField = typeof(MCPServiceLocator).GetField(
            "_toolConsentService",
            BindingFlags.Static | BindingFlags.NonPublic
        );
        private static readonly FieldInfo DiscoveryField = typeof(MCPServiceLocator).GetField(
            "_toolDiscoveryService",
            BindingFlags.Static | BindingFlags.NonPublic
        );
        private static readonly FieldInfo ResourcesField = typeof(MCPServiceLocator).GetField(
            "_resourceDiscoveryService",
            BindingFlags.Static | BindingFlags.NonPublic
        );
        private readonly object beforeConsent,
            beforeDiscovery,
            beforeResources;
        private readonly List<NativeConsentKey> keys;
        private List<NativeConsentKey> expected;
        internal readonly OrdinaryToolDefaults Defaults;
        internal readonly NativeConsentObservedStore Store = new();
        internal readonly string Root,
            Run;
        internal readonly int MainThread;
        internal readonly ToolConsentService Service;
        internal readonly ToolDiscoveryService Discovery;
        private string activeTool;
        private bool keepKeys,
            disposed;

        internal NativeConsentProjectScope(bool includeResources = false)
        {
            Root = AdmittedRoot();
            Run = RunId();
            MainThread = Thread.CurrentThread.ManagedThreadId;
            beforeConsent = ConsentField.GetValue(null);
            beforeDiscovery = DiscoveryField.GetValue(null);
            beforeResources = ResourcesField.GetValue(null);
            Defaults = new OrdinaryToolDefaults();
            keys = NativeConsentKey.CaptureFresh(Root, Run);
            expected = NativeConsentKey.Allowed(Root, Run);
            Store.OwnedWrite = RecordOwnedWrite;
            Service = new ToolConsentService(Store, () => ToolConsentThread.IsMainThread, StrictRoot, ToolConsentProcessState.Shared, () => Run);
            Discovery = new ToolDiscoveryService();
            try
            {
                ConsentField.SetValue(null, Service);
                DiscoveryField.SetValue(null, Discovery);
                ResourceDiscoveryService resources = includeResources ? new ResourceDiscoveryService() : null;
                if (resources != null)
                    ResourcesField.SetValue(null, resources);
                Defaults.Discover(Discovery, resources);
            }
            catch
            {
                Dispose();
                throw;
            }
        }

        internal static string StrictRoot()
        {
            Assert.IsTrue(ToolConsentThread.IsMainThread, "Native consent must run on the actual Editor main thread.");
            Assert.IsTrue(ProjectIdentityUtility.TryGetConsentProjectRoot(out string root));
            return root;
        }

        internal static string RunId()
        {
            string run = Environment.GetEnvironmentVariable("UNITY_MCP_CORE12_RUN_ID");
            Assert.IsTrue(Guid.TryParseExact(run, "N", out _), "A fresh owned run ID is required.");
            return run;
        }

        internal static string AdmittedRoot()
        {
            string root = ToolConsentService.NormalizeProjectRoot(StrictRoot());
            Assert.AreEqual(root, ToolConsentService.NormalizeProjectRoot(Path.GetDirectoryName(Application.dataPath)));
            Assert.AreEqual(
                root,
                ToolConsentService.NormalizeProjectRoot(Environment.GetEnvironmentVariable("UNITY_MCP_CORE12_PROJECT")),
                "Never use a user or unadmitted project."
            );
            Assert.IsTrue(Directory.Exists(root));
            for (var entry = new DirectoryInfo(root); entry != null; entry = entry.Parent)
                Assert.AreEqual(0, (int)(entry.Attributes & FileAttributes.ReparsePoint));
            Assert.AreEqual("1", Environment.GetEnvironmentVariable("UNITY_MCP_OWNED_TRANSPORT_TESTS"));
            RunId();
            return root;
        }

        internal static void AssertDefaultPolicy(ToolConsentScope session, ToolConsentScope project)
        {
            object oldConsent = ConsentField.GetValue(null),
                oldDiscovery = DiscoveryField.GetValue(null);
            var defaults = new OrdinaryToolDefaults();
            var errors = new List<string>();
            try
            {
                var service = new ToolConsentService(); // Default native store/root/latch after reload or restart.
                MCPServiceLocator.Register<IToolConsentService>(service);
                var discovery = new ToolDiscoveryService();
                MCPServiceLocator.Register<IToolDiscoveryService>(discovery);
                defaults.Discover(discovery);
                foreach (var pair in new[] { ("execute_code", session), ("manage_script", project) })
                {
                    var state = service.GetStatus(pair.Item1);
                    Assert.AreEqual(pair.Item2, state.Scope);
                    Assert.AreEqual(pair.Item2 != ToolConsentScope.None, state.Enabled);
                    Assert.AreEqual(
                        state.Enabled,
                        discovery.IsToolEnabled(pair.Item1),
                        "Default authoritative discovery must use the real current-root state."
                    );
                }
            }
            finally
            {
                try
                {
                    defaults.Restore(errors);
                }
                finally
                {
                    ConsentField.SetValue(null, oldConsent);
                    DiscoveryField.SetValue(null, oldDiscovery);
                }
                Assert.IsEmpty(errors, string.Join("; ", errors));
            }
        }

        internal JObject Preimage() =>
            new()
            {
                ["version"] = 1,
                ["run"] = Run,
                ["root"] = Root,
                ["keys"] = JArray.FromObject(keys),
                ["ordinary"] = Defaults.Preimage(),
            };

        internal void KeepForLifecycle() => keepKeys = true;

        private NativeConsentKey Expected(string key) => expected.Single(item => item.Key == key);

        private static void ExpectBool(List<NativeConsentKey> states, string key, bool present, bool value = false)
        {
            var item = states.Single(entry => entry.Key == key);
            item.Present = present;
            item.Value = value;
        }

        private void RecordOwnedWrite(ToolConsentStorageScope storageScope, string key, bool present, bool value, string text)
        {
            if (activeTool == null)
                return;
            var target = ToolConsentService.KeysFor(Root, activeTool);
            bool projectBool = storageScope == ToolConsentStorageScope.Project && (key == target.Enabled || key == target.ProjectGrant);
            bool sessionBool = storageScope == ToolConsentStorageScope.Session && (key == target.Denied || key == target.SessionGrant(Run));
            bool sessionString = storageScope == ToolConsentStorageScope.Session && key == target.SessionId && text == Run;
            if (!projectBool && !sessionBool && !sessionString)
                return;
            var intended = new NativeConsentKey
            {
                Kind =
                    sessionString ? "session-string"
                    : projectBool ? "project-bool"
                    : "session-bool",
                Key = key,
                Present = present,
                Value = value,
                Text = text,
            };
            // A storage call can fail after a native write. Own only its exact
            // requested value when a native readback confirms that write.
            try
            {
                if (!NativeConsentKey.Matches(NativeConsentKey.Read(intended), intended))
                    return;
            }
            catch
            {
                return;
            } // An unconfirmed native write is never claimed for cleanup.
            var item = Expected(key);
            item.Present = present;
            item.Value = value;
            item.Text = text;
        }

        internal void DuringOwnedMutation(string name, Action action)
        {
            Assert.Contains(name, Tools);
            Assert.IsNull(activeTool, "Nested fixture consent mutations are not owned by the outer operation.");
            activeTool = name;
            try
            {
                action();
            }
            finally
            {
                activeTool = null;
            }
        }

        private void AssertOwnedState(IEnumerable<NativeConsentKey> states)
        {
            foreach (var item in states)
            {
                var actual = NativeConsentKey.Read(item);
                Assert.IsTrue(NativeConsentKey.Matches(actual, item), "Unowned native consent change: " + item.Key);
            }
        }

        internal void AssertOwnedState() => AssertOwnedState(expected);

        private List<NativeConsentKey> DesiredState() =>
            expected
                .Select(item => new NativeConsentKey
                {
                    Kind = item.Kind,
                    Key = item.Key,
                    Present = item.Present,
                    Value = item.Value,
                    Text = item.Text,
                })
                .ToList();

        internal void RecordGrant(string name, ToolConsentScope scope)
        {
            Assert.Contains(name, Tools);
            Assert.IsTrue(scope == ToolConsentScope.EditorSession || scope == ToolConsentScope.Project);
            var keysForTool = ToolConsentService.KeysFor(Root, name);
            var desired = DesiredState();
            ExpectBool(desired, keysForTool.Enabled, true, true);
            ExpectBool(desired, keysForTool.ProjectGrant, scope == ToolConsentScope.Project, scope == ToolConsentScope.Project);
            ExpectBool(desired, keysForTool.SessionGrant(Run), scope == ToolConsentScope.EditorSession, scope == ToolConsentScope.EditorSession);
            ExpectBool(desired, keysForTool.Denied, false);
            if (scope == ToolConsentScope.EditorSession)
            {
                var sessionId = desired.Single(item => item.Key == keysForTool.SessionId);
                sessionId.Present = true;
                sessionId.Text = Run;
            }
            AssertOwnedState(desired);
        }

        internal void RecordRevoke(string name)
        {
            Assert.Contains(name, Tools);
            var keysForTool = ToolConsentService.KeysFor(Root, name);
            var desired = DesiredState();
            ExpectBool(desired, keysForTool.Enabled, true, false);
            ExpectBool(desired, keysForTool.ProjectGrant, false);
            ExpectBool(desired, keysForTool.SessionGrant(Run), false);
            ExpectBool(desired, keysForTool.Denied, true, true);
            AssertOwnedState(desired);
        }

        internal ToolConsentChangeResult Grant(string name, ToolConsentScope scope)
        {
            ToolConsentChangeResult result = default;
            DuringOwnedMutation(name, () => result = Service.Grant(name, scope));
            if (result.Succeeded)
                RecordGrant(name, scope);
            return result;
        }

        internal ToolConsentChangeResult Revoke(string name)
        {
            ToolConsentChangeResult result = default;
            DuringOwnedMutation(name, () => result = Service.Revoke(name));
            if (result.Succeeded)
                RecordRevoke(name);
            return result;
        }

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            var errors = new List<string>();
            try
            {
                if (!keepKeys)
                    NativeConsentKey.RestoreAll(keys, expected, errors);
                Defaults.Restore(errors);
            }
            finally
            {
                ConsentField.SetValue(null, beforeConsent);
                DiscoveryField.SetValue(null, beforeDiscovery);
                ResourcesField.SetValue(null, beforeResources);
            }
            Assert.IsEmpty(errors, string.Join("; ", errors));
        }
    }

    internal sealed class NativeConsentObservedStore : IToolConsentStorage
    {
        private readonly NativeToolConsentStorage native = new();
        internal readonly HashSet<int> AccessThreads = new();
        internal Action<ToolConsentStorageScope, string, bool, bool, string> OwnedWrite;
        internal int Reads,
            Writes;

        private void Observe(bool write)
        {
            lock (AccessThreads)
                AccessThreads.Add(Thread.CurrentThread.ManagedThreadId);
            if (write)
                Interlocked.Increment(ref Writes);
            else
                Interlocked.Increment(ref Reads);
            Assert.IsTrue(ToolConsentThread.IsMainThread);
        }

        public bool GetBool(ToolConsentStorageScope scope, string key)
        {
            Observe(false);
            return native.GetBool(scope, key);
        }

        public void SetBool(ToolConsentStorageScope scope, string key, bool value)
        {
            Observe(true);
            try
            {
                native.SetBool(scope, key, value);
            }
            finally
            {
                OwnedWrite?.Invoke(scope, key, true, value, null);
            }
        }

        public void DeleteBool(ToolConsentStorageScope scope, string key)
        {
            Observe(true);
            try
            {
                native.DeleteBool(scope, key);
            }
            finally
            {
                OwnedWrite?.Invoke(scope, key, false, false, null);
            }
        }

        public string GetString(ToolConsentStorageScope scope, string key)
        {
            Observe(false);
            return native.GetString(scope, key);
        }

        public void SetString(ToolConsentStorageScope scope, string key, string value)
        {
            Observe(true);
            try
            {
                native.SetString(scope, key, value);
            }
            finally
            {
                OwnedWrite?.Invoke(scope, key, true, false, value);
            }
        }
    }

    internal sealed class NativeConsentKey
    {
        public string Kind,
            Key,
            Text;
        public bool Present,
            Value;

        internal static bool Matches(NativeConsentKey actual, NativeConsentKey expected) =>
            actual.Kind == expected.Kind
            && actual.Key == expected.Key
            && actual.Present == expected.Present
            && (!actual.Present || (actual.Kind == "session-string" ? actual.Text == expected.Text : actual.Value == expected.Value));

        internal static List<NativeConsentKey> CaptureFresh(string root, string run)
        {
            var result = new List<NativeConsentKey>();
            foreach (var item in Allowed(root, run))
            {
                var observed = Read(item);
                Assert.IsFalse(observed.Present, "Owned scratch project has an unexpected existing consent value; refuse before mutation: " + item.Key);
                result.Add(observed);
            }
            return result;
        }

        internal static List<NativeConsentKey> Allowed(string root, string run)
        {
            var result = new List<NativeConsentKey>();
            foreach (string name in NativeConsentProjectScope.Tools)
            {
                var keys = ToolConsentService.KeysFor(root, name);
                result.Add(new NativeConsentKey { Kind = "project-bool", Key = keys.ProjectGrant });
                result.Add(new NativeConsentKey { Kind = "project-bool", Key = keys.Enabled });
                result.Add(new NativeConsentKey { Kind = "session-bool", Key = keys.Denied });
                result.Add(new NativeConsentKey { Kind = "session-bool", Key = keys.SessionGrant(run) });
                if (name == NativeConsentProjectScope.Tools[0])
                    result.Add(new NativeConsentKey { Kind = "session-string", Key = keys.SessionId });
            }
            return result;
        }

        internal static NativeConsentKey Read(NativeConsentKey item)
        {
            var observed = new NativeConsentKey { Kind = item.Kind, Key = item.Key };
            if (item.Kind == "project-bool")
            {
                observed.Present = EditorPrefs.HasKey(item.Key);
                if (observed.Present)
                    observed.Value = EditorPrefs.GetBool(item.Key);
            }
            else if (item.Kind == "session-bool")
            {
                observed.Present = SessionState.GetBool(item.Key, false) == SessionState.GetBool(item.Key, true);
                if (observed.Present)
                    observed.Value = SessionState.GetBool(item.Key, false);
            }
            else if (item.Kind == "session-string")
            {
                string a = SessionState.GetString(item.Key, "missing-a"),
                    b = SessionState.GetString(item.Key, "missing-b");
                observed.Present = a == b;
                if (observed.Present)
                    observed.Text = a;
            }
            else
                throw new InvalidOperationException("Unexpected key kind");
            return observed;
        }

        internal static void RestoreAll(IEnumerable<NativeConsentKey> keys, IEnumerable<NativeConsentKey> expected, List<string> errors)
        {
            Assert.IsTrue(ToolConsentThread.IsMainThread);
            var expectations = expected.ToDictionary(item => item.Kind + ":" + item.Key, StringComparer.Ordinal);
            foreach (var item in keys)
            {
                try
                {
                    string identity = item.Kind + ":" + item.Key;
                    if (!expectations.TryGetValue(identity, out var prior))
                    {
                        errors.Add("owned_consent_missing_expectation:" + item.Key);
                        continue;
                    }
                    var current = Read(item);
                    if (!Matches(current, prior))
                    {
                        errors.Add("owned_consent_cleanup_conflict:" + item.Key);
                        continue;
                    }
                    if (item.Kind == "project-bool")
                    {
                        if (item.Present)
                            EditorPrefs.SetBool(item.Key, item.Value);
                        else
                            EditorPrefs.DeleteKey(item.Key);
                    }
                    else if (item.Kind == "session-bool")
                    {
                        if (item.Present)
                            SessionState.SetBool(item.Key, item.Value);
                        else
                            SessionState.EraseBool(item.Key);
                    }
                    else if (item.Kind == "session-string")
                    {
                        if (item.Present)
                            SessionState.SetString(item.Key, item.Text);
                        else
                            SessionState.EraseString(item.Key);
                    }
                    else
                        throw new InvalidOperationException("Unexpected key kind");
                    var restored = Read(item);
                    if (!Matches(restored, item))
                        errors.Add("owned_consent_restore_unconfirmed:" + item.Key);
                }
                catch (Exception error)
                {
                    errors.Add("owned_consent_restore:" + error.GetType().Name + ":" + item.Key);
                }
            }
        }
    }

    internal sealed class OrdinaryToolDefaults
    {
        private readonly Dictionary<string, bool> missing = new(StringComparer.Ordinal);
        private readonly Dictionary<string, bool> owned = new(StringComparer.Ordinal);

        internal OrdinaryToolDefaults()
        {
            Assert.IsTrue(ToolConsentThread.IsMainThread);
            var types = ToolDiscoveryService.InRegistrationOrder(TypeCache.GetTypesWithAttribute<McpForUnityToolAttribute>()).ToArray();
            Assert.LessOrEqual(types.Length, 512, "Only the bounded installed metadata set may be snapshotted.");
            foreach (Type type in types)
            {
                var attr = type.GetCustomAttribute<McpForUnityToolAttribute>();
                if (attr == null || attr.RequiresExplicitConsent || CommandRegistry.GetCommandMethod(type) == null)
                    continue;
                string name = string.IsNullOrEmpty(attr.Name) ? StringCaseUtility.ToSnakeCase(type.Name) : attr.Name;
                Assert.IsTrue(!string.IsNullOrEmpty(name) && name.Length <= 128);
                Assert.IsFalse(name.Contains("ExplicitConsent."), "Legacy consent keys are never owned by this fixture.");
                string key = EditorPrefKeys.ToolEnabledPrefix + name;
                bool initial = attr.AutoRegister || StringCaseUtility.IsBuiltInMcpType(type, type.Assembly.GetName().Name, "MCPForUnity.Editor.Tools");
                if (!EditorPrefs.HasKey(key) && !missing.ContainsKey(key))
                    missing.Add(key, initial);
            }
            var resources = ToolDiscoveryService.InRegistrationOrder(TypeCache.GetTypesWithAttribute<McpForUnityResourceAttribute>()).ToArray();
            Assert.LessOrEqual(resources.Length, 256);
            foreach (Type type in resources)
            {
                var attr = type.GetCustomAttribute<McpForUnityResourceAttribute>();
                if (attr == null || CommandRegistry.GetCommandMethod(type) == null)
                    continue;
                string name = string.IsNullOrEmpty(attr.ResourceName) ? StringCaseUtility.ToSnakeCase(type.Name) : attr.ResourceName;
                Assert.IsTrue(!string.IsNullOrEmpty(name) && name.Length <= 128);
                Assert.IsFalse(name.Contains("ExplicitConsent."));
                string key = EditorPrefKeys.ResourceEnabledPrefix + name;
                if (!EditorPrefs.HasKey(key) && !missing.ContainsKey(key))
                    missing.Add(key, true);
            }
        }

        internal void Discover(ToolDiscoveryService discovery, ResourceDiscoveryService resources = null)
        {
            // No await occurs between the bounded snapshot and the real scan. Only
            // observed absent-to-default initialization is owned by this phase.
            try
            {
                discovery.DiscoverAllTools();
                resources?.DiscoverAllResources();
            }
            finally
            {
                foreach (var item in missing)
                    if (EditorPrefs.HasKey(item.Key))
                        owned[item.Key] = item.Value;
            }
        }

        internal JObject Preimage() => JObject.FromObject(owned);

        internal void Restore(List<string> errors)
        {
            foreach (var item in owned)
            {
                try
                {
                    if (!EditorPrefs.HasKey(item.Key))
                        continue;
                    bool a = EditorPrefs.GetBool(item.Key, false),
                        b = EditorPrefs.GetBool(item.Key, true);
                    if (a != b || a != item.Value)
                    {
                        errors.Add("ordinary_default_cleanup_conflict:" + item.Key);
                        continue;
                    }
                    EditorPrefs.DeleteKey(item.Key);
                    if (EditorPrefs.HasKey(item.Key))
                        errors.Add("ordinary_default_cleanup_unconfirmed:" + item.Key);
                }
                catch (Exception error)
                {
                    errors.Add("ordinary_default_cleanup:" + error.GetType().Name + ":" + item.Key);
                }
            }
            owned.Clear();
        }
    }

    [InitializeOnLoad]
    internal static class NativeConsentLifecycle
    {
        private static readonly string Domain = Guid.NewGuid().ToString("N");
        private static string Witness => "MCPForUnity.Core12.ReloadWitness." + NativeConsentProjectScope.RunId();
        private static bool verified;

        static NativeConsentLifecycle() => AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;

        private static string RecordPath()
        {
            string directory = Environment.GetEnvironmentVariable("UNITY_MCP_CORE12_EVIDENCE");
            Assert.IsTrue(Path.IsPathRooted(directory ?? "") && Directory.Exists(directory));
            for (var entry = new DirectoryInfo(directory); entry != null; entry = entry.Parent)
                Assert.AreEqual(0, (int)(entry.Attributes & FileAttributes.ReparsePoint));
            return Path.Combine(Path.GetFullPath(directory), "consent-before-" + NativeConsentProjectScope.RunId() + ".json");
        }

        private static JObject Read()
        {
            NativeConsentProjectScope.AdmittedRoot();
            string path = RecordPath();
            Assert.IsTrue(File.Exists(path) && new FileInfo(path).Length <= 131072);
            Assert.AreEqual(0, (int)(File.GetAttributes(path) & FileAttributes.ReparsePoint));
            var data = JObject.Parse(File.ReadAllText(path));
            Assert.AreEqual(1, data.Value<int>("version"));
            Assert.AreEqual(JTokenType.Boolean, data["complete"]?.Type, "Lifecycle checkpoint must state whether both grants completed.");
            Assert.AreEqual(NativeConsentProjectScope.RunId(), data.Value<string>("run"));
            string original = ToolConsentService.NormalizeProjectRoot(Environment.GetEnvironmentVariable("UNITY_MCP_CORE12_ORIGINAL_PROJECT"));
            Assert.IsNotNull(original);
            Assert.AreEqual(original, data.Value<string>("root"));
            var expected = NativeConsentKey.Allowed(original, data.Value<string>("run"));
            var keys = data["keys"].ToObject<List<NativeConsentKey>>();
            CollectionAssert.AreEquivalent(expected.Select(key => key.Kind + ":" + key.Key), keys.Select(key => key.Kind + ":" + key.Key));
            Assert.IsTrue(keys.All(key => !key.Present), "The admitted disposable root had no preexisting v2 values.");
            return data;
        }

        private static (int, long) ProcessIdentity()
        {
            using var process = Process.GetCurrentProcess();
            return (process.Id, process.StartTime.ToUniversalTime().Ticks);
        }

        private static void RestoreWitness(JObject data, List<string> errors, bool requirePresent = false)
        {
            var before = new NativeConsentKey { Kind = "session-string", Key = Witness };
            if (!data.Value<bool>("complete"))
            {
                if (NativeConsentKey.Read(before).Present)
                    errors.Add("unowned_reload_witness:" + Witness);
                return;
            }
            if (!requirePresent && !NativeConsentKey.Read(before).Present)
                return;
            var owned = new NativeConsentKey
            {
                Kind = "session-string",
                Key = Witness,
                Present = true,
                Text = data.Value<string>("domain"),
            };
            NativeConsentKey.RestoreAll(new[] { before }, new[] { owned }, errors);
        }

        private static void MarkComplete(JObject data, byte[] initial)
        {
            string path = RecordPath();
            Assert.AreEqual(0, (int)(File.GetAttributes(path) & FileAttributes.ReparsePoint));
            using var record = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
            Assert.AreEqual(initial.Length, record.Length, "Lifecycle checkpoint changed before grant completion.");
            var before = new byte[initial.Length];
            int offset = 0;
            while (offset < before.Length)
            {
                int count = record.Read(before, offset, before.Length - offset);
                Assert.Greater(count, 0, "Lifecycle checkpoint could not be read in full.");
                offset += count;
            }
            CollectionAssert.AreEqual(initial, before, "Lifecycle checkpoint changed before grant completion.");
            data["complete"] = true;
            byte[] completed = System.Text.Encoding.UTF8.GetBytes(data.ToString(Formatting.Indented));
            Assert.LessOrEqual(completed.Length, 131072);
            record.Position = 0;
            record.SetLength(0);
            record.Write(completed, 0, completed.Length);
            record.Flush(true);
        }

        internal static void Begin()
        {
            using var scope = new NativeConsentProjectScope();
            Assert.AreEqual(scope.Root, ToolConsentService.NormalizeProjectRoot(Environment.GetEnvironmentVariable("UNITY_MCP_CORE12_ORIGINAL_PROJECT")));
            Assert.AreNotEqual(SessionState.GetString(Witness, "missing-a"), SessionState.GetString(Witness, "missing-b"));
            var data = scope.Preimage();
            var process = ProcessIdentity();
            data["domain"] = Domain;
            data["pid"] = process.Item1;
            data["startTicks"] = process.Item2;
            data["complete"] = false;
            byte[] bytes = System.Text.Encoding.UTF8.GetBytes(data.ToString(Formatting.Indented));
            Assert.LessOrEqual(bytes.Length, 131072);
            using (var output = new FileStream(RecordPath(), FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                output.Write(bytes, 0, bytes.Length);
                output.Flush(true);
            }
            Assert.IsTrue(scope.Grant("execute_code", ToolConsentScope.EditorSession).Succeeded);
            Assert.IsTrue(scope.Grant("manage_script", ToolConsentScope.Project).Succeeded);
            Assert.IsTrue(scope.Discovery.IsToolEnabled("execute_code") && scope.Discovery.IsToolEnabled("manage_script"));
            MarkComplete(data, bytes);
            scope.KeepForLifecycle();
        }

        private static void BeforeReload()
        {
            if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("UNITY_MCP_CORE12_RUN_ID")))
                return;
            try
            {
                var data = Read();
                if (
                    data.Value<bool>("complete")
                    && data.Value<string>("root") == NativeConsentProjectScope.StrictRoot()
                    && data.Value<string>("domain") == Domain
                )
                {
                    var key = new NativeConsentKey { Kind = "session-string", Key = Witness };
                    if (NativeConsentKey.Read(key).Present)
                        return;
                    SessionState.SetString(Witness, Domain);
                }
            }
            catch (Exception) { /* A missing or foreign lifecycle record never arms a reload witness. */ }
        }

        internal static void AfterReload()
        {
            var data = Read();
            var process = ProcessIdentity();
            Assert.IsTrue(data.Value<bool>("complete"), "Both grants must have completed before the reload proof.");
            Assert.AreEqual(data.Value<string>("root"), NativeConsentProjectScope.StrictRoot());
            Assert.AreEqual(data.Value<int>("pid"), process.Item1);
            Assert.AreEqual(data.Value<long>("startTicks"), process.Item2);
            Assert.AreNotEqual(data.Value<string>("domain"), Domain);
            Assert.AreEqual(data.Value<string>("domain"), SessionState.GetString(Witness, ""));
            NativeConsentProjectScope.AssertDefaultPolicy(ToolConsentScope.EditorSession, ToolConsentScope.Project);
            var key = ToolConsentService.KeysFor(NativeConsentProjectScope.StrictRoot(), "execute_code");
            Assert.AreEqual(data.Value<string>("run"), SessionState.GetString(key.SessionId, ""));
            verified = true;
        }

        internal static void EndReloadAttempt()
        {
            if (!Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_CORE12_RUN_ID"), "N", out _))
                return;
            NativeConsentProjectScope.AdmittedRoot();
            if (!File.Exists(RecordPath()))
                return;
            var data = Read();
            var errors = new List<string>();
            RestoreWitness(data, errors, verified || (data.Value<bool>("complete") && data.Value<string>("domain") != Domain));
            if (!verified)
                Cleanup();
            Assert.IsEmpty(errors, string.Join("; ", errors));
        }

        internal static void AfterRestart()
        {
            var data = Read();
            var process = ProcessIdentity();
            Assert.IsTrue(data.Value<bool>("complete"), "Both grants must have completed before the restart proof.");
            Assert.AreEqual(data.Value<string>("root"), NativeConsentProjectScope.StrictRoot());
            Assert.IsTrue(
                data.Value<int>("pid") != process.Item1 || data.Value<long>("startTicks") != process.Item2,
                "Require an actual new Editor incarnation."
            );
            foreach (var key in NativeConsentKey.Allowed(data.Value<string>("root"), data.Value<string>("run")))
            {
                if (key.Kind == "session-string")
                    Assert.AreNotEqual(SessionState.GetString(key.Key, "missing-a"), SessionState.GetString(key.Key, "missing-b"));
                else if (key.Kind == "session-bool")
                    Assert.AreNotEqual(
                        SessionState.GetBool(key.Key, false),
                        SessionState.GetBool(key.Key, true),
                        "Old Editor session state must actually expire: " + key.Key
                    );
            }
            NativeConsentProjectScope.AssertDefaultPolicy(ToolConsentScope.None, ToolConsentScope.Project);
        }

        internal static void OtherRoot(string role)
        {
            var data = Read();
            Assert.IsTrue(data.Value<bool>("complete"), "Both grants must have completed before root-isolation proof.");
            Assert.AreEqual(role, Environment.GetEnvironmentVariable("UNITY_MCP_CORE12_ROLE"));
            Assert.AreNotEqual(data.Value<string>("root"), NativeConsentProjectScope.StrictRoot());
            var process = ProcessIdentity();
            Assert.IsTrue(data.Value<int>("pid") != process.Item1 || data.Value<long>("startTicks") != process.Item2);
            NativeConsentKey.CaptureFresh(NativeConsentProjectScope.StrictRoot(), NativeConsentProjectScope.RunId());
            var a = ToolConsentService.KeysFor(data.Value<string>("root"), "manage_script");
            Assert.IsTrue(
                EditorPrefs.GetBool(a.ProjectGrant, false) && EditorPrefs.GetBool(a.Enabled, false),
                "The original project's remembered grant must still be live for a meaningful isolation refusal."
            );
            NativeConsentProjectScope.AssertDefaultPolicy(ToolConsentScope.None, ToolConsentScope.None);
        }

        internal static void Cleanup()
        {
            var data = Read();
            Assert.AreEqual(data.Value<string>("root"), NativeConsentProjectScope.StrictRoot(), "Cleanup can restore only the admitted original A project.");
            var errors = new List<string>();
            var keys = data["keys"].ToObject<List<NativeConsentKey>>();
            var expected = NativeConsentKey.Allowed(data.Value<string>("root"), data.Value<string>("run"));
            if (data.Value<bool>("complete"))
            {
                var process = ProcessIdentity();
                bool sameEditor = data.Value<int>("pid") == process.Item1 && data.Value<long>("startTicks") == process.Item2;
                var execute = ToolConsentService.KeysFor(data.Value<string>("root"), "execute_code");
                var manage = ToolConsentService.KeysFor(data.Value<string>("root"), "manage_script");
                foreach (var item in expected)
                {
                    if (
                        item.Key == execute.Enabled
                        || item.Key == manage.Enabled
                        || item.Key == manage.ProjectGrant
                        || sameEditor && item.Key == execute.SessionGrant(data.Value<string>("run"))
                    )
                    {
                        item.Present = true;
                        item.Value = true;
                    }
                    else if (sameEditor && item.Key == execute.SessionId)
                    {
                        item.Present = true;
                        item.Text = data.Value<string>("run");
                    }
                }
            }
            NativeConsentKey.RestoreAll(keys, expected, errors);
            // Each synchronous default-discovery phase cleans its own ordinary
            // initialization in finally. Never replay that old global snapshot.
            RestoreWitness(data, errors);
            Assert.IsEmpty(errors, string.Join("; ", errors));
        }
    }
}
