using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools.Addressables;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using StdioPeer = MCPForUnityTests.Editor.Services.StdioBridgeReconnectTests;

namespace MCPForUnityTests.Editor.Tools
{
    internal sealed class PrefabWorkflowImportObserver : AssetPostprocessor
    {
        internal static Action<string[]> Imported;

        private static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom) => Imported?.Invoke(imported);
    }

    public class AddressablesCompatibilityTests
    {
        private static object Policy(string name, params object[] args)
        {
            Type type = Type.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablesCompatibility, MCPForUnity.Editor", true);
            return type.GetMethod(name, BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, args);
        }

        private static void ReportShape(JObject report, bool modern)
        {
            Type type = Type.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablesLayout, MCPForUnity.Editor", true);
            type.GetMethod("RequireSupportedReportShape", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { report, modern });
        }

        [TestCase(null, false, true)]
        [TestCase(null, true, false)]
        [TestCase("[]", true, true)]
        [TestCase("[]", false, true)]
        [TestCase("null", true, false)]
        [TestCase("{}", true, false)]
        [TestCase("[{}]", true, false)]
        [TestCase("[{}]", false, false)]
        [TestCase("\"unsupported\"", true, false)]
        public void ContentDirectoryReportShapeRequiresEmptySupportedEvidence(string contentDirectories, bool modern, bool accepted)
        {
            var report = new JObject { ["DuplicatedAssets"] = new JArray(), ["Groups"] = new JArray() };
            if (contentDirectories != null)
                report["ContentDirectories"] = JToken.Parse(contentDirectories);
            if (accepted)
                Assert.DoesNotThrow(() => ReportShape(report, modern));
            else
            {
                var error = Assert.Throws<TargetInvocationException>(() => ReportShape(report, modern));
                Assert.AreEqual(
                    "addressables_layout_schema_unsupported",
                    error.InnerException.GetType().GetField("Code", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error.InnerException)
                );
            }
        }

        [TestCase(null, true)]
        [TestCase("\"\"", true)]
        [TestCase("\"owned-directory\"", false)]
        [TestCase("1", false)]
        [TestCase("{}", false)]
        [TestCase("false", false)]
        public void GroupContentDirectoryCoverageIsCheckedInlineAndInManagedReferences(string value, bool accepted)
        {
            foreach (bool referenced in new[] { false, true })
            {
                var group = new JObject { ["ContentDirectoryName"] = value == null ? JValue.CreateNull() : JToken.Parse(value) };
                var report = new JObject
                {
                    ["DuplicatedAssets"] = new JArray(),
                    ["Groups"] = new JArray(),
                    ["ContentDirectories"] = new JArray(),
                };
                if (referenced)
                    report["references"] = new JObject { ["RefIds"] = new JArray(new JObject { ["data"] = group }) };
                else
                    ((JArray)report["Groups"]).Add(group);
                if (accepted)
                    Assert.DoesNotThrow(() => ReportShape(report, true));
                else
                {
                    var error = Assert.Throws<TargetInvocationException>(() => ReportShape(report, true));
                    Assert.AreEqual(
                        "addressables_layout_schema_unsupported",
                        error.InnerException.GetType().GetField("Code", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error.InnerException)
                    );
                }
            }
        }

        private sealed class EditApi
        {
            internal int Calls;

            public void SetAddress(string address, bool postEvent) => Calls++;

            public void SetAddress(string address) => Calls++;

            public bool SetValue(string profile, string name, string value)
            {
                Calls++;
                return true;
            }

            public static void StaticEdit(string value) { }

            public void GenericEdit<T>(string value) => Calls++;
        }

        [Test]
        public void EditCapabilityResolutionChecksExactSignatureWithoutInvokingIt()
        {
            var target = new EditApi();
            var method = (MethodInfo)Policy("RequireMethod", target, "set_address", "SetAddress", typeof(void), new[] { typeof(string), typeof(bool) });
            Assert.AreEqual(2, method.GetParameters().Length);
            Assert.AreEqual(0, target.Calls, "Capability checks must never invoke native edits.");
        }

        [TestCase("SetAddress", "move_entry")]
        [TestCase("SetValue", "set_profile_value")]
        [TestCase("StaticEdit", "set_address")]
        [TestCase("Missing", "set_address")]
        public void MissingOrIncompatibleEditApisRefuseBeforeInvocation(string method, string action)
        {
            var target = new EditApi();
            var error = Assert.Throws<TargetInvocationException>(() =>
                Policy("RequireMethod", target, action, method, typeof(void), new[] { typeof(string), typeof(string), typeof(string) })
            );
            Assert.AreEqual(
                "addressables_api_unsupported",
                error.InnerException.GetType().GetField("Code", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error.InnerException)
            );
            StringAssert.Contains(action, error.InnerException.Message);
            Assert.AreEqual(0, target.Calls);
        }

        [TestCase("StaticEdit")]
        [TestCase("GenericEdit")]
        public void StaticAndOpenGenericMethodsAreNotNativeEditCapabilities(string method)
        {
            var target = new EditApi();
            var error = Assert.Throws<TargetInvocationException>(() =>
                Policy("RequireMethod", target, "set_address", method, typeof(void), new[] { typeof(string) })
            );
            Assert.AreEqual(
                "addressables_api_unsupported",
                error.InnerException.GetType().GetField("Code", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error.InnerException)
            );
            Assert.AreEqual(0, target.Calls);
        }

        [Test]
        public void MissingLayoutSchemaRefusesWithAnActionSpecificCapabilityError()
        {
            Type type = Type.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablesLayout, MCPForUnity.Editor", true);
            var error = Assert.Throws<TargetInvocationException>(() =>
                type.GetMethod("RequireSchema", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { typeof(object) })
            );
            Assert.AreEqual(
                "addressables_api_unsupported",
                error.InnerException.GetType().GetField("Code", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error.InnerException)
            );
            StringAssert.Contains("analyze_layout", error.InnerException.Message);
        }

#pragma warning disable CS0649, CS0169 // Deliberately incomplete native schema fixtures are inspected through reflection.
        private class LegacyLayout
        {
            public class Group { }
        }

        private class ModernLayout
        {
            public class Group
            {
                public string ContentDirectoryName;
            }

            public class ContentDirectory { }

            public List<ContentDirectory> ContentDirectories;
        }

        private class PartialLayout
        {
            public class Group
            {
                public string ContentDirectoryName;
            }
        }

        private class MalformedLayout
        {
            public class Group
            {
                public int ContentDirectoryName;
            }

            public class ContentDirectory { }

            public List<ContentDirectory> ContentDirectories;
        }

        private class PrivateExtensionLayout
        {
            public class Group { }

            private List<object> ContentDirectories;
        }

#pragma warning restore CS0649, CS0169

        [TestCase(typeof(LegacyLayout), false)]
        [TestCase(typeof(ModernLayout), true)]
        public void ContentDirectoryCapabilityUsesNativeSchemaRatherThanVersion(Type layout, bool expected)
        {
            Type adapter = Type.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablesLayout, MCPForUnity.Editor", true);
            Assert.AreEqual(
                expected,
                adapter.GetMethod("UsesContentDirectories", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { layout })
            );
        }

        [TestCase(typeof(PartialLayout))]
        [TestCase(typeof(MalformedLayout))]
        [TestCase(typeof(PrivateExtensionLayout))]
        public void IncompleteNativeContentDirectorySchemaRefusesBeforeReadingReports(Type layout)
        {
            Type adapter = Type.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablesLayout, MCPForUnity.Editor", true);
            var error = Assert.Throws<TargetInvocationException>(() =>
                adapter.GetMethod("UsesContentDirectories", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { layout })
            );
            Assert.AreEqual(
                "addressables_api_unsupported",
                error.InnerException.GetType().GetField("Code", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error.InnerException)
            );
            StringAssert.Contains("analyze_layout", error.InnerException.Message);
        }

        [Test]
        public void InstalledPackageHasTheCompleteConsumedLayoutSchema()
        {
            Type layout = Type.GetType("UnityEditor.AddressableAssets.Build.Layout.BuildLayout, Unity.Addressables.Editor");
            if (layout == null)
                Assert.Ignore("A supported real Addressables package is required.");
            Type adapter = Type.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablesLayout, MCPForUnity.Editor", true);
            Assert.DoesNotThrow(() => adapter.GetMethod("RequireSchema", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { layout }));
            Assert.DoesNotThrow(() =>
                adapter.GetMethod("UsesContentDirectories", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { layout })
            );
        }
    }

    [Parallelizable(ParallelScope.None)]
    public class AddressablesAuthoringTests
    {
        private readonly PrefabTestSceneFixture scenes = new PrefabTestSceneFixture();
        private string root,
            rootGuid,
            settingsPath,
            assetGuid;
        private object settings,
            first,
            second,
            entry,
            other,
            profiles;
        private string activeProfile;
        private Type settingsType;

        [OneTimeSetUp]
        public void Prepare() => scenes.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void Restore() => scenes.RestoreRunnerBootstrap();

        internal static object Get(object value, string name)
        {
            var property = value.GetType().GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            return property != null
                ? property.GetValue(value)
                : value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)?.GetValue(value);
        }

        internal static object Invoke(object value, string name, params object[] args)
        {
            var method = value
                .GetType()
                .GetMethods(BindingFlags.Public | BindingFlags.Instance)
                .Single(m =>
                    m.Name == name
                    && m.GetParameters().Length == args.Length
                    && m.GetParameters().Select((p, i) => args[i] == null || p.ParameterType.IsInstanceOfType(args[i])).All(v => v)
                );
            return method.Invoke(value, args);
        }

        [SetUp]
        public void SetUp()
        {
            root = null;
            settingsType = Type.GetType("UnityEditor.AddressableAssets.Settings.AddressableAssetSettings, Unity.Addressables.Editor");
            if (settingsType == null)
                Assert.Ignore("A supported real Addressables package is required.");
            scenes.Create("McpAddressables_", Guid.NewGuid().ToString("N"));
            root = "Assets/__McpAddressables_" + Guid.NewGuid().ToString("N");
            rootGuid = AssetDatabase.CreateFolder("Assets", root.Substring(7));
            // Persist only the owned temporary factory result. The persisted factory invokes global SaveAssets.
            UnityEngine.Object[] preservedDirty = UnityEngine
                .Resources.FindObjectsOfTypeAll<UnityEngine.Object>()
                .Where(v => v != null && EditorUtility.IsDirty(v) && AssetDatabase.Contains(v))
                .Take(16)
                .ToArray();
            foreach (UnityEngine.Object value in preservedDirty)
                Debug.Log("Addressables fixture preserves dirty asset " + AssetDatabase.GetAssetPath(value) + " nativeId=" + value.GetInstanceIDCompat());
            AssetDatabase.CreateFolder(root, "Config");
            var temporary = (UnityEngine.Object)
                settingsType
                    .GetMethod("Create", new[] { typeof(string), typeof(string), typeof(bool), typeof(bool) })
                    .Invoke(null, new object[] { root + "/Config", "Settings", false, false });
            Assert.IsNotNull(temporary);
            settingsPath = root + "/Config/Settings.asset";
            AssetDatabase.CreateAsset(temporary, settingsPath);
            AssetDatabase.SaveAssetIfDirty(temporary);
            // m_IsTemporary is not serialized in either baseline package. Reload establishes persisted native settings.
            UnityEngine.Resources.UnloadAsset(temporary);
            settings = AssetDatabase.LoadAssetAtPath(settingsPath, settingsType);
            Assert.IsNotNull(settings);
            Assert.IsTrue((bool)Get(settings, "IsPersisted"), "Owned settings must reload as a real persistent settings asset.");
            foreach (UnityEngine.Object value in preservedDirty)
                Assert.IsTrue(value != null && EditorUtility.IsDirty(value), "Unrelated dirty assets must not be saved or cleared.");
            first = Invoke(settings, "CreateGroup", "OwnedA", false, false, false, null, Array.Empty<Type>());
            second = Invoke(settings, "CreateGroup", "OwnedB", false, false, false, null, Array.Empty<Type>());
            var texture = new Texture2D(2, 2);
            AssetDatabase.CreateAsset(texture, root + "/First.asset");
            assetGuid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(texture));
            entry = Invoke(settings, "CreateOrMoveEntry", assetGuid, first, false, false);
            Assert.IsNotNull(entry, "The real package must create the owned asset entry.");
            Invoke(entry, "SetAddress", "owned/first", false);
            Invoke(settings, "AddLabel", "owned-label", false);
            Invoke(entry, "SetLabel", "owned-label", true, false, false);
            var reference = ScriptableObject.CreateInstance<AddressablesReferenceFixture>();
            Type assetReferenceType = Type.GetType("UnityEngine.AddressableAssets.AssetReference, Unity.Addressables", true);
            reference.Reference = Activator.CreateInstance(assetReferenceType, new object[] { new string('e', 32) });
            AssetDatabase.CreateAsset(reference, root + "/Reference.asset");
            string referenceGuid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(reference));
            other = Invoke(settings, "CreateOrMoveEntry", referenceGuid, first, false, false);
            Invoke(other, "SetAddress", "owned/reference", false);
            profiles = Get(settings, "profileSettings");
            activeProfile = (string)Get(settings, "activeProfileId");
            Assert.IsFalse(string.IsNullOrEmpty(activeProfile), "Native settings factory must establish its real default profile.");
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (root != null)
                {
                    foreach (string guid in AssetDatabase.FindAssets("", new[] { root }))
                    {
                        UnityEngine.Object value = AssetDatabase.LoadMainAssetAtPath(AssetDatabase.GUIDToAssetPath(guid));
                        if (value != null)
                            Undo.ClearUndo(value);
                    }
                    StringAssert.StartsWith("Assets/__McpAddressables_", root);
                    Assert.AreEqual(rootGuid, AssetDatabase.AssetPathToGUID(root));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(root), "Only the fixture-owned asset tree is removed.");
                }
            }
            finally
            {
                scenes.Close();
            }
        }

        private JObject Call(string action, JObject options = null)
        {
            Type handler = Type.GetType("MCPForUnity.Editor.Tools.Addressables.ManageAddressables, MCPForUnity.Editor");
            Assert.IsNotNull(handler, "SPEC15 dedicated Addressables handler is missing.");
            var args = options == null ? new JObject() : (JObject)options.DeepClone();
            args["action"] = action;
            args["settings_path"] = settingsPath;
            return JObject.FromObject(handler.GetMethod("HandleCommand", new[] { typeof(JObject) }).Invoke(null, new object[] { args }));
        }

        private JObject Read(string section = "entries", int page = 1, int size = 50, string revision = null)
        {
            var args = new JObject
            {
                ["section"] = section,
                ["page_number"] = page,
                ["page_size"] = size,
            };
            if (revision != null)
                args["expected_revision"] = revision;
            JObject response = Call("inspect", args);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response["data"];
        }

        private static string[] Labels(object value) => ((IEnumerable)Get(value, "labels")).Cast<string>().OrderBy(x => x).ToArray();

        private static JObject Workflow(string key, string action, JObject args = null)
        {
            args = args ?? new JObject();
            args["action"] = action;
            args["checkpoint"] = key;
            return JObject.FromObject(ManageAddressables.HandleCommand(args));
        }

        private string PreviewPrefab(string key)
        {
            var ownedScene = UnityEngine.SceneManagement.SceneManager.GetActiveScene();
            StringAssert.StartsWith("McpAddressables_", ownedScene.name);
            ownedScene.name = "McpAddressables_PrefabRecoveryFixture";
            string source = root + "/Template.prefab",
                destination = root + "/Result.prefab";
            var template = new GameObject("Template");
            try
            {
                Assert.IsNotNull(PrefabUtility.SaveAsPrefabAsset(template, source));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(template);
            }
            AssetDatabase.SaveAssetIfDirty((UnityEngine.Object)first);
            var preview = Workflow(
                key,
                "preview_prefab",
                new JObject
                {
                    ["source_prefab"] = source,
                    ["prefab_path"] = destination,
                    ["settings_path"] = settingsPath,
                    ["group_guid"] = (string)Get(first, "m_GUID"),
                }
            );
            Assert.IsTrue(preview.Value<bool>("success"), preview.ToString());
            Assert.IsTrue(((JArray)preview["data"]["affected_paths"]).Values<string>().Contains(destination));
            Assert.IsFalse(File.Exists(destination));
            return destination;
        }

        private static void SendWorkflow(TcpClient peer, string key, string action)
        {
            var request = new JObject
            {
                ["type"] = "manage_addressables",
                ["params"] = new JObject { ["action"] = action, ["checkpoint"] = key },
            };
            StdioPeer.SendFrame(peer.GetStream(), Encoding.UTF8.GetBytes(request.ToString(Newtonsoft.Json.Formatting.None)));
        }

        private static IEnumerator WaitFor(Func<bool> predicate)
        {
            var timer = System.Diagnostics.Stopwatch.StartNew();
            while (!predicate() && timer.Elapsed < TimeSpan.FromSeconds(20))
                yield return null;
            Assert.IsTrue(predicate(), "The owned Editor transport did not settle.");
        }

        [UnityTest, Explicit("Requires a disposable batch Editor and its own authenticated loopback bridge.")]
        public IEnumerator PrefabRegistrationRecoversDroppedCreationResponseWithoutDuplicatingNativeWrites()
        {
            string key = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            string destination = PreviewPrefab(key);
            Assert.IsFalse(StdioBridgeHost.IsRunning, "An owned idle Editor is required.");
            string token = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            StdioBridgeHost.StartOwned(0, token);
            JObject status;
            try
            {
                int port = StdioBridgeHost.GetCurrentPort();
                var firstClient = Task.Run(() => StdioPeer.ConnectProgressPeer(port, token, null, out _));
                yield return WaitFor(() => firstClient.IsCompleted);
                using (var peer = firstClient.GetAwaiter().GetResult())
                {
                    SendWorkflow(peer, key, "create_prefab");
                    yield return WaitFor(() => !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(destination)));
                    // Disconnect without reading the first delivery, after the actual import.
                }
                var secondClient = Task.Run(() => StdioPeer.ConnectProgressPeer(port, token, null, out _));
                yield return WaitFor(() => secondClient.IsCompleted);
                using (var peer = secondClient.GetAwaiter().GetResult())
                {
                    SendWorkflow(peer, key, "prefab_status");
                    var read = Task.Run(() => JObject.Parse(Encoding.UTF8.GetString(StdioPeer.ReadFrame(peer.GetStream(), 10000))));
                    yield return WaitFor(() => read.IsCompleted);
                    var wire = read.GetAwaiter().GetResult();
                    Assert.AreEqual("success", wire.Value<string>("status"), wire.ToString());
                    status = (JObject)wire["result"];
                    Assert.AreEqual("created", (string)status["data"]["state"], status.ToString());
                    SendWorkflow(peer, key, "register_prefab");
                    read = Task.Run(() => JObject.Parse(Encoding.UTF8.GetString(StdioPeer.ReadFrame(peer.GetStream(), 10000))));
                    yield return WaitFor(() => read.IsCompleted);
                    wire = read.GetAwaiter().GetResult();
                    Assert.AreEqual("registered", (string)wire.SelectToken("result.data.state"), wire.ToString());
                }
            }
            finally
            {
                StdioBridgeHost.Stop();
            }
            string guid = AssetDatabase.AssetPathToGUID(destination);
            Assert.IsNotEmpty(guid);
            Assert.AreNotEqual(AssetDatabase.AssetPathToGUID(root + "/Template.prefab"), guid);
            Assert.AreEqual(guid, status["data"].Value<string>("prefab_guid"), status.ToString());
            Assert.AreEqual("created", status["data"].Value<string>("state"));
            Assert.IsFalse(Workflow(new string('f', 64), "register_prefab").Value<bool>("success"));
            Assert.AreEqual("registered", Workflow(key, "create_prefab")["data"].Value<string>("state"));
            foreach (var action in new[] { "register_prefab", "register_prefab", "prefab_status" })
            {
                var result = Workflow(key, action);
                Assert.AreEqual("registered", result["data"].Value<string>("state"), result.ToString());
                Assert.IsEmpty((JArray)result["data"]["outstanding_steps"]);
            }
            var entries = ((IEnumerable)Get(first, "entries")).Cast<object>().Where(item => (string)Get(item, "guid") == guid).ToArray();
            Assert.AreEqual(1, entries.Length);
            Assert.AreEqual(destination, Get(entries[0], "address"));
            Assert.AreEqual(1, Directory.GetFiles(root, "Result*.prefab").Length);
            Debug.Log(
                "Prefab recovery confirmed new GUID "
                    + guid
                    + ", one asset, one saved explicit entry in "
                    + AssetDatabase.GetAssetPath((UnityEngine.Object)first)
            );
        }

        [Test]
        public void PrefabRegistrationPreservesInterveningAddressEditAndReportsOutstandingStep()
        {
            string key = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            string destination = PreviewPrefab(key);
            Workflow(key, "create_prefab");
            Invoke(entry, "SetAddress", "manual/intervening", true);
            var result = Workflow(key, "register_prefab");
            Assert.AreEqual("conflict", result["data"].Value<string>("state"), result.ToString());
            StringAssert.Contains("Addressables revision changed", result["data"].Value<string>("detail"));
            CollectionAssert.AreEqual(new[] { "create_prefab" }, ((JArray)result["data"]["completed_steps"]).Values<string>());
            CollectionAssert.AreEqual(new[] { "register_prefab" }, ((JArray)result["data"]["outstanding_steps"]).Values<string>());
            Assert.AreEqual("manual/intervening", Get(entry, "address"));
            Assert.IsNull(Invoke(settings, "FindAssetEntry", AssetDatabase.AssetPathToGUID(destination)));
        }

        [Test]
        public void PrefabCancellationAndDestinationCollisionPreserveExistingAssets()
        {
            string key = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            string destination = PreviewPrefab(key);
            Assert.IsTrue(AssetDatabase.CopyAsset(root + "/Template.prefab", destination));
            string foreignGuid = AssetDatabase.AssetPathToGUID(destination);
            Assert.AreEqual("conflict", Workflow(key, "create_prefab")["data"].Value<string>("state"));
            Assert.AreEqual("cancelled", Workflow(key, "cancel_prefab")["data"].Value<string>("state"));
            Assert.AreEqual("cancelled", Workflow(key, "register_prefab")["data"].Value<string>("state"));
            Assert.AreEqual(foreignGuid, AssetDatabase.AssetPathToGUID(destination));
            Assert.IsNull(Invoke(settings, "FindAssetEntry", foreignGuid));
        }

        private const string ReloadEvidence = "MCPForUnity.Tests.PrefabWorkflowReload";

        [Test]
        public void CancellationDuringNativeImportObservesRunningAndRetainsSettledCreation()
        {
            string key = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            string destination = PreviewPrefab(key);
            string running = null;
            PrefabWorkflowImportObserver.Imported = paths =>
            {
                if (paths.Contains(destination))
                    running = (string)Workflow(key, "cancel_prefab")["data"]["state"];
            };
            try
            {
                var settlement = Workflow(key, "create_prefab");
                Assert.AreEqual("running", running, settlement.ToString());
                Assert.AreEqual("cancelled", (string)settlement["data"]["state"]);
                Assert.AreEqual(AssetDatabase.AssetPathToGUID(destination), (string)settlement["data"]["prefab_guid"]);
                Assert.IsNull(Invoke(settings, "FindAssetEntry", (string)settlement["data"]["prefab_guid"]));
                Assert.AreEqual("cancelled", (string)Workflow(key, "register_prefab")["data"]["state"]);
            }
            finally
            {
                PrefabWorkflowImportObserver.Imported = null;
            }
        }

        private static readonly string DomainIdentity = Guid.NewGuid().ToString("N");
        private static string RestartEvidence => Path.Combine(Application.dataPath, "..", "Library", "McpPrefabWorkflowRestart.json");

        [UnityTest, Explicit("Stages a process-bound checkpoint in a disposable Editor and actually reloads its domain.")]
        public IEnumerator PrefabCheckpointSurvivesDomainReload()
        {
            Assert.IsTrue(Application.isBatchMode);
            Assert.IsFalse(File.Exists(RestartEvidence), "Preserve any existing restart evidence.");
            string key = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            string destination = PreviewPrefab(key);
            var created = Workflow(key, "create_prefab");
            Assert.AreEqual("created", (string)created["data"]["state"], created.ToString());
            var marker = new JObject
            {
                ["key"] = key,
                ["domain"] = DomainIdentity,
                ["session"] = created["data"]["editor_session_id"],
                ["guid"] = AssetDatabase.AssetPathToGUID(destination),
                ["destination"] = destination,
            };
            SessionState.SetString(ReloadEvidence, marker.ToString());
            EditorUtility.RequestScriptReload();
            yield return new WaitForDomainReload();
            marker = JObject.Parse(SessionState.GetString(ReloadEvidence, ""));
            Assert.AreNotEqual((string)marker["domain"], DomainIdentity, "The Editor must actually reload its managed domain.");
            var observed = Workflow((string)marker["key"], "prefab_status");
            Assert.AreEqual((string)marker["session"], (string)observed["data"]["editor_session_id"], observed.ToString());
            Assert.AreEqual((string)marker["guid"], (string)observed["data"]["prefab_guid"]);
            Assert.AreEqual("created", (string)observed["data"]["state"], observed.ToString());
            var cancelled = Workflow((string)marker["key"], "cancel_prefab");
            Assert.AreEqual("cancelled", (string)cancelled["data"]["state"]);
            Assert.AreEqual("cancelled", (string)Workflow((string)marker["key"], "register_prefab")["data"]["state"]);
            using (var file = new FileStream(RestartEvidence, FileMode.CreateNew, FileAccess.Write))
            using (var writer = new StreamWriter(file))
                writer.Write(marker.ToString());
            SessionState.EraseString(ReloadEvidence);
        }

        [Test, Explicit("Second Editor process in the disposable prefab recovery profile.")]
        public void ReplacementEditorRefusesPriorPrefabCheckpoint()
        {
            Assert.IsTrue(Application.isBatchMode);
            var marker = JObject.Parse(File.ReadAllText(RestartEvidence));
            Assert.AreNotEqual((string)marker["session"], MCPForUnity.Editor.Services.Transport.CommandResumeState.SessionId);
            string currentKey = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            string destination = PreviewPrefab(currentKey);
            var refused = Workflow((string)marker["key"], "register_prefab");
            Assert.IsFalse(refused.Value<bool>("success"), refused.ToString());
            Assert.AreEqual("unknown", (string)refused["data"]["state"], refused.ToString());
            Assert.IsFalse(File.Exists(destination));
            var created = Workflow(currentKey, "create_prefab");
            Assert.AreEqual("created", (string)created["data"]["state"], created.ToString());
            Assert.AreNotEqual((string)marker["guid"], AssetDatabase.AssetPathToGUID(destination));
        }

        private static void SameAsset(UnityEngine.Object before, UnityEngine.Object after)
        {
            Assert.IsTrue(before == after, "Unity native object identity must be preserved.");
            Assert.AreEqual(before.GetInstanceIDCompat(), after.GetInstanceIDCompat());
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(before, out string guid, out long id));
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(after, out string nextGuid, out long nextId));
            Assert.AreEqual(guid, nextGuid);
            Assert.AreEqual(id, nextId);
        }

        [Test]
        public void LegacyProviderReadsSettingsAndRefusesUnavailableLayout()
        {
            if (settingsType.Assembly.GetType("UnityEditor.AddressableAssets.Build.Layout.BuildLayout") != null)
                Assert.Ignore("This profile exercises the installed provider without a native JSON BuildLayout.");
            var readOnlyGroup = Invoke(settings, "CreateGroup", "OwnedReadOnly", false, true, false, null, Array.Empty<Type>());
            string groupGuid = (string)Get(readOnlyGroup, "m_GUID");
            Assert.IsTrue(Read("groups")["items"].Single(g => g.Value<string>("guid") == groupGuid).Value<bool>("read_only"));
            CheckRefusalPreservesNativeSettings(before =>
            {
                JObject response = JObject.FromObject(
                    ManageAddressables.HandleCommand(
                        new JObject { ["action"] = "analyze_layout", ["layout_path"] = "Library/MCPForUnity/Addressables/layout.json" }
                    )
                );
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual("addressables_api_unsupported", response.Value<string>("code"), response.ToString());
                StringAssert.Contains("analyze_layout", response["data"].Value<string>("message"));
                var move = Call(
                    "move_entry",
                    new JObject
                    {
                        ["entry_guid"] = assetGuid,
                        ["group_guid"] = groupGuid,
                        ["expected_revision"] = before["revision"],
                    }
                );
                Assert.IsFalse(move.Value<bool>("success"), move.ToString());
                Assert.AreEqual("addressables_read_only", move.Value<string>("code"), move.ToString());
            });
        }

        // Only the mutation receiver is reduced. The snapshot, backing assets and providers remain native.
        private sealed class EntryWithoutWriter
        {
            private readonly object native;

            internal EntryWithoutWriter(object native) => this.native = native;

            public bool ReadOnly => (bool)Get(native, "ReadOnly");
            public object parentGroup => Get(native, "parentGroup");
        }

        [Test]
        public void MissingEntryWriterPreservesNativeSettingsAndUndo()
        {
            CheckRefusalPreservesNativeSettings(before =>
            {
                var request = new JObject
                {
                    ["action"] = "set_address",
                    ["settings_path"] = settingsPath,
                    ["expected_revision"] = before["revision"],
                    ["entry_guid"] = assetGuid,
                    ["address"] = "owned/changed",
                };
                var state = AddressablesState.Capture(request);
                Assert.AreSame(entry, state.Entries[assetGuid]);
                state.Entries[assetGuid] = new EntryWithoutWriter(entry);
                var error = Assert.Throws<AddressablesException>(() => AddressablesEdit.Apply(state, request));
                Assert.AreEqual("addressables_api_unsupported", error.Code);
                StringAssert.Contains("set_address", error.Message);
                StringAssert.Contains("SetAddress", error.Message);
            });
        }

        private void CheckRefusalPreservesNativeSettings(Action<JObject> attempt)
        {
            var before = Read();
            var bytes = Directory.GetFiles(root, "*", SearchOption.AllDirectories).ToDictionary(path => path, File.ReadAllBytes);
            var dirty = new[] { (UnityEngine.Object)settings, (UnityEngine.Object)first }.ToDictionary(obj => obj, EditorUtility.IsDirty);
            int undo = Undo.GetCurrentGroup();
            attempt(before);
            Assert.AreEqual(undo, Undo.GetCurrentGroup(), "Refusal must precede Undo registration.");
            Assert.IsTrue(JToken.DeepEquals(before, Read()));
            Assert.AreSame(entry, Invoke(settings, "FindAssetEntry", assetGuid));
            SameAsset((UnityEngine.Object)settings, AssetDatabase.LoadMainAssetAtPath(settingsPath));
            SameAsset((UnityEngine.Object)first, (UnityEngine.Object)Get(entry, "parentGroup"));
            foreach (var pair in dirty)
                Assert.AreEqual(pair.Value, EditorUtility.IsDirty(pair.Key));
            CollectionAssert.AreEquivalent(bytes.Keys, Directory.GetFiles(root, "*", SearchOption.AllDirectories));
            foreach (var pair in bytes)
                CollectionAssert.AreEqual(pair.Value, File.ReadAllBytes(pair.Key), pair.Key);
        }

        [Test]
        public void PagedEntriesGroupsProfilesPreserveNativeIdentityAndLabels()
        {
            var bytes = Directory.GetFiles(root, "*", SearchOption.AllDirectories).ToDictionary(path => path, File.ReadAllBytes);
            bool dirty = EditorUtility.IsDirty((UnityEngine.Object)settings);
            JObject firstPage = Read("entries", 1, 1);
            Assert.AreEqual(2, firstPage.Value<int>("total"));
            JObject lastPage = Read("entries", 2, 1, firstPage.Value<string>("revision"));
            string[] guids = ((JArray)firstPage["items"]).Concat((JArray)lastPage["items"]).Select(t => t.Value<string>("guid")).ToArray();
            CollectionAssert.AreEquivalent(new[] { assetGuid, (string)Get(other, "guid") }, guids);
            Assert.AreEqual(2, guids.Distinct().Count());
            Assert.GreaterOrEqual(Read("groups").Value<int>("total"), 2);
            Assert.Greater(Read("profiles").Value<int>("total"), 0);
            CollectionAssert.AreEqual(new[] { "owned-label" }, Labels(entry));
            SameAsset((UnityEngine.Object)settings, AssetDatabase.LoadMainAssetAtPath(settingsPath));
            Assert.AreEqual(UnityEditor.PackageManager.PackageInfo.FindForAssembly(settingsType.Assembly).version, firstPage.Value<string>("package_version"));
            Assert.AreEqual(dirty, EditorUtility.IsDirty((UnityEngine.Object)settings), "Inspection must not dirty or migrate settings.");
            foreach (var pair in bytes)
                CollectionAssert.AreEqual(pair.Value, File.ReadAllBytes(pair.Key), "Inspection must preserve existing owned asset bytes.");
        }

        [TestCase("inspect")]
        [TestCase("preflight")]
        [TestCase("set_address")]
        [TestCase("move_entry")]
        [TestCase("set_profile_value")]
        public void UnknownStoredProfileSchemaRefusesWithoutMigration(string action)
        {
            FieldInfo field = profiles.GetType().GetField("m_ProfileVersion", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(field);
            object original = field.GetValue(profiles);
            bool dirty = EditorUtility.IsDirty((UnityEngine.Object)settings);
            string address = (string)Get(entry, "address");
            int undo = Undo.GetCurrentGroup();
            try
            {
                field.SetValue(profiles, 2);
                JObject response = Call(action);
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual("addressables_profile_schema_unsupported", response.Value<string>("code"));
                StringAssert.Contains(action, response["data"].Value<string>("message"));
                Assert.AreEqual(2, field.GetValue(profiles), "The action must not invoke a native profile migration.");
                Assert.AreEqual(address, Get(entry, "address"));
                Assert.AreEqual(undo, Undo.GetCurrentGroup(), "Unsupported stored schemas must refuse before Undo registration.");
                Assert.AreEqual(dirty, EditorUtility.IsDirty((UnityEngine.Object)settings));
            }
            finally
            {
                field.SetValue(profiles, original);
            }
        }

        [Test]
        public void InspectionPreservesMissingGroupIdentityAndRejectsForeignOwnership()
        {
            var flags = BindingFlags.NonPublic | BindingFlags.Instance;
            FieldInfo id = first.GetType().GetField("m_GUID", flags);
            FieldInfo owner = first.GetType().GetField("m_Settings", flags);
            object originalId = id.GetValue(first);
            int undo = Undo.GetCurrentGroup();
            try
            {
                id.SetValue(first, "");
                JObject error = Call("inspect");
                Assert.AreEqual("addressables_settings_invalid", error.Value<string>("code"), error.ToString());
                Assert.AreEqual("", id.GetValue(first), "Inspection must never synthesize a group GUID.");
                id.SetValue(first, originalId);
                owner.SetValue(first, null);
                error = Call("inspect");
                Assert.AreEqual("addressables_settings_invalid", error.Value<string>("code"), error.ToString());
                Assert.IsNull(owner.GetValue(first), "Inspection must not attach a foreign group to the default settings.");
                Assert.AreEqual(undo, Undo.GetCurrentGroup());
            }
            finally
            {
                id.SetValue(first, originalId);
                owner.SetValue(first, settings);
            }
        }

        [Test]
        public void ExistingNativeLayoutChecksProducerAndCompleteSerializedSchemaBeforeDecode()
        {
            string version = UnityEditor.PackageManager.PackageInfo.FindForAssembly(settingsType.Assembly).version;
            Type layoutType = settingsType.Assembly.GetType("UnityEditor.AddressableAssets.Build.Layout.BuildLayout", true);
            object layout = Activator.CreateInstance(layoutType);
            layoutType.GetField("PackageVersion").SetValue(layout, "com.unity.addressables: " + version);
            layoutType.GetField("BuildTarget").SetValue(layout, EditorUserBuildSettings.activeBuildTarget);
            layoutType.GetField("UnityVersion").SetValue(layout, Application.unityVersion);
            string directory = "Library/MCPForUnity/Addressables/compat-" + Guid.NewGuid().ToString("N");
            string path = directory + "/layout.json";
            Assert.IsFalse(Directory.Exists(directory));
            Directory.CreateDirectory(directory);
            try
            {
                string json = EditorJsonUtility.ToJson(layout);
                File.WriteAllText(path, json);
                Type handler = Type.GetType("MCPForUnity.Editor.Tools.Addressables.ManageAddressables, MCPForUnity.Editor", true);
                var request = new JObject { ["action"] = "analyze_layout", ["layout_path"] = path };
                JObject response = JObject.FromObject(handler.GetMethod("HandleCommand").Invoke(null, new object[] { request }));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(version, response["data"].Value<string>("package_version"));
                Assert.AreEqual(0, response["data"].Value<int>("total"));
                Assert.IsTrue(response["data"].Value<bool>("analysis_complete"), "An empty compatible native report has complete legacy duplication evidence.");
                Assert.IsFalse(response["data"].Value<bool>("content_build_started"));
                Assert.AreEqual(json, File.ReadAllText(path), "Existing reports are read-only.");

                // Use native serialization for a nonempty managed-reference graph.
                object group = Activator.CreateInstance(layoutType.GetNestedType("Group"));
                ((IList)layoutType.GetField("Groups").GetValue(layout)).Add(group);
                object schema = Activator.CreateInstance(layoutType.GetNestedType("SchemaData"));
                ((IList)group.GetType().GetField("Schemas").GetValue(group)).Add(schema);
                ((IList)schema.GetType().GetField("KvpDetails").GetValue(schema)).Add(Tuple.Create("example", "value"));
                object bundle = Activator.CreateInstance(layoutType.GetNestedType("Bundle"));
                ((IList)group.GetType().GetField("Bundles").GetValue(group)).Add(bundle);
                bundle.GetType().GetField("Group").SetValue(bundle, group);
                object file = Activator.CreateInstance(layoutType.GetNestedType("File"));
                ((IList)bundle.GetType().GetField("Files").GetValue(bundle)).Add(file);
                file.GetType().GetField("Bundle").SetValue(file, bundle);
                object duplicateAsset = Activator.CreateInstance(layoutType.GetNestedType("AssetDuplicationData"));
                ((IList)layoutType.GetField("DuplicatedAssets").GetValue(layout)).Add(duplicateAsset);
                object duplicateObject = Activator.CreateInstance(layoutType.GetNestedType("ObjectDuplicationData"));
                ((IList)duplicateAsset.GetType().GetField("DuplicatedObjects").GetValue(duplicateAsset)).Add(duplicateObject);
                ((IList)duplicateObject.GetType().GetField("IncludedInBundleFiles").GetValue(duplicateObject)).Add(file);
                layoutType.GetField("PackageVersion").SetValue(layout, "com.unity.addressables: 12.0.0-preview.2+fixture");
                JObject report = JObject.Parse(EditorJsonUtility.ToJson(layout));
                JObject Analyze(JObject input)
                {
                    File.WriteAllText(path, input.ToString());
                    return JObject.FromObject(handler.GetMethod("HandleCommand").Invoke(null, new object[] { request }));
                }
                response = Analyze(report);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(1, response["data"].Value<int>("total"));
                Assert.AreEqual(1, response["data"]["items"][0].Value<int>("file_count"));
                Assert.AreEqual(version, response["data"].Value<string>("package_version"));
                Assert.AreEqual("12.0.0-preview.2+fixture", response["data"].Value<string>("report_package_version"));
                layoutType.GetMethod("WriteToFile").Invoke(layout, new object[] { path, true });
                response = JObject.FromObject(handler.GetMethod("HandleCommand").Invoke(null, new object[] { request }));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                JToken GroupRecord(JObject input) => input["references"]["RefIds"].Single(r => (string)r["type"]["class"] == "BuildLayout/Group");
                if (GroupRecord(report)["data"]["ContentDirectoryName"] != null)
                {
                    var noDirectory = (JObject)report.DeepClone();
                    GroupRecord(noDirectory)["data"]["ContentDirectoryName"] = JValue.CreateNull();
                    response = Analyze(noDirectory);
                    Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                }
                foreach (
                    Action<JObject> damage in new Action<JObject>[]
                    {
                        r => r.Remove("BuildError"),
                        r => r["Duration"] = "12",
                        r => r["Groups"][0]["rid"] = 98765432,
                        r => r["DuplicatedAssets"][0]["DuplicatedObjects"][0]["IncludedInBundleFiles"][0]["rid"] = -2,
                        r =>
                        {
                            var orphan = (JObject)GroupRecord(r).DeepClone();
                            orphan["rid"] = 98765432;
                            ((JArray)r["references"]["RefIds"]).Add(orphan);
                        },
                        r => ((JObject)GroupRecord(r)["data"]).Remove("Name"),
                        r => GroupRecord(r)["data"]["UnknownField"] = "uncovered",
                        r => GroupRecord(r)["type"]["asm"] = "Assembly-CSharp",
                        r => GroupRecord(r)["type"]["class"] = "BuildLayout/File",
                    }
                )
                {
                    var invalid = (JObject)report.DeepClone();
                    damage(invalid);
                    response = Analyze(invalid);
                    Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                    Assert.AreEqual("addressables_layout_schema_unsupported", response.Value<string>("code"), response.ToString());
                    Assert.AreEqual(invalid.ToString(), File.ReadAllText(path));
                }
                report["PackageVersion"] = "other.package: 2.7.6";
                json = report.ToString();
                response = Analyze(report);
                Assert.AreEqual("addressables_layout_version_unsupported", response.Value<string>("code"));
                Assert.AreEqual(json, File.ReadAllText(path));
            }
            finally
            {
                File.Delete(path);
                Directory.Delete(directory);
            }
        }

        [Test]
        public void ContentDirectoryReportsRefuseWithoutCompleteCoverageAndPreserveStoredBytes()
        {
            string version = UnityEditor.PackageManager.PackageInfo.FindForAssembly(settingsType.Assembly).version;
            Type layoutType = settingsType.Assembly.GetType("UnityEditor.AddressableAssets.Build.Layout.BuildLayout", true);
            object layout = Activator.CreateInstance(layoutType);
            layoutType.GetField("PackageVersion").SetValue(layout, "com.unity.addressables: " + version);
            layoutType.GetField("BuildTarget").SetValue(layout, EditorUserBuildSettings.activeBuildTarget);
            layoutType.GetField("UnityVersion").SetValue(layout, Application.unityVersion);
            JObject baseline = JObject.Parse(EditorJsonUtility.ToJson(layout));
            var reports = new List<JObject>();
            foreach (JToken value in new JToken[] { new JArray(new JObject()), new JObject(), new JValue("unsupported"), JValue.CreateNull() })
            {
                var report = (JObject)baseline.DeepClone();
                report["ContentDirectories"] = value;
                reports.Add(report);
            }
            foreach (bool referenced in new[] { false, true })
            foreach (JToken value in new JToken[] { new JValue("owned-directory"), new JValue(1), new JObject() })
            {
                var report = (JObject)baseline.DeepClone();
                report["ContentDirectories"] = new JArray();
                var group = new JObject { ["ContentDirectoryName"] = value };
                if (referenced)
                    report["references"] = new JObject { ["RefIds"] = new JArray(new JObject { ["data"] = group }) };
                else
                    report["Groups"] = new JArray(group);
                reports.Add(report);
            }
            if (layoutType.GetField("ContentDirectories") != null)
            {
                var missing = (JObject)baseline.DeepClone();
                missing.Remove("ContentDirectories");
                reports.Add(missing);
            }
            string directory = "Library/MCPForUnity/Addressables/compat-content-" + Guid.NewGuid().ToString("N");
            string path = directory + "/layout.json";
            Assert.IsFalse(Directory.Exists(directory));
            Directory.CreateDirectory(directory);
            try
            {
                Type handler = Type.GetType("MCPForUnity.Editor.Tools.Addressables.ManageAddressables, MCPForUnity.Editor", true);
                foreach (JObject report in reports)
                {
                    File.WriteAllText(path, report.ToString());
                    byte[] before = File.ReadAllBytes(path);
                    JObject response = JObject.FromObject(
                        handler
                            .GetMethod("HandleCommand")
                            .Invoke(
                                null,
                                new object[]
                                {
                                    new JObject { ["action"] = "analyze_layout", ["layout_path"] = path },
                                }
                            )
                    );
                    Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                    Assert.AreEqual("addressables_layout_schema_unsupported", response.Value<string>("code"), response.ToString());
                    CollectionAssert.AreEqual(before, File.ReadAllBytes(path), "Unsupported reports must remain byte-for-byte unchanged.");
                }
            }
            finally
            {
                File.Delete(path);
                Directory.Delete(directory);
            }
        }

        [Test]
        public void ReadPreflightFindsDuplicateTypedMissingReferenceAndProfileErrorsWithoutAuthoring()
        {
            Invoke(other, "SetAddress", "owned/first", false);
            Invoke(profiles, "SetValue", activeProfile, "Local.BuildPath", "[MissingOwnedVariable]");
            Invoke(profiles, "CreateValue", "OwnedCycleA", "[OwnedCycleB]");
            Invoke(profiles, "CreateValue", "OwnedCycleB", "[OwnedCycleA]");
            string[] labels = Labels(entry);
            JObject before = Read();
            JObject response = Call("preflight");
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var codes = ((JArray)response["data"]["items"]).Select(i => i.Value<string>("code")).ToArray();
            CollectionAssert.Contains(codes, "duplicate_address");
            CollectionAssert.Contains(codes, "missing_asset_reference");
            CollectionAssert.Contains(codes, "unknown_profile_token");
            CollectionAssert.Contains(codes, "profile_cycle");
            Assert.IsFalse(response["data"].Value<bool>("content_build_started"), response.ToString());
            Assert.AreEqual(before.Value<string>("revision"), Read().Value<string>("revision"), "Read preflight must not author settings.");
            CollectionAssert.AreEqual(labels, Labels(entry));
            Assert.AreEqual("owned/first", Get(other, "address"));
        }

        [TestCase(256, 0)]
        [TestCase(256, 255)]
        [TestCase(257, 0)]
        [TestCase(257, 256)]
        public void PreflightFindsArrayBoundaryReferenceOrSpecifiesTheSkippedField(int length, int brokenIndex)
        {
            Invoke(profiles, "SetValue", activeProfile, "Remote.LoadPath", "fixture/remote");
            var fixture = AssetDatabase.LoadAssetAtPath<AddressablesReferenceFixture>(root + "/Reference.asset");
            fixture.Reference = null;
            fixture.References = new object[length];
            Type referenceType = Type.GetType("UnityEngine.AddressableAssets.AssetReference, Unity.Addressables", true);
            fixture.References[brokenIndex] = Activator.CreateInstance(referenceType, new object[] { new string('e', 32) });
            EditorUtility.SetDirty(fixture);
            AssetDatabase.SaveAssetIfDirty(fixture);
            byte[] bytes = File.ReadAllBytes(root + "/Reference.asset");

            JObject response = Call("preflight", new JObject { ["page_size"] = 1 });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var data = (JObject)response["data"];
            var coverage = (JObject)data["reference_scan_coverage"];
            Assert.IsNotNull(coverage, response.ToString());
            Assert.AreEqual("serialized_arrays_in_explicit_entry_main_assets_and_prefab_components", coverage.Value<string>("scope"));
            Assert.AreEqual(256, coverage.Value<int>("array_element_limit"));
            Assert.AreEqual(length == 256, coverage.Value<bool>("complete"));
            Assert.AreEqual(length == 256 ? 0 : 1, coverage.Value<int>("skipped_array_count"));
            Assert.AreEqual(1, data.Value<int>("total"), response.ToString());
            Assert.AreEqual(1, ((JArray)data["items"]).Count);
            Assert.IsFalse(data.Value<bool>("has_more"));
            var diagnostic = (JObject)data["items"][0];
            Assert.AreEqual(Get(other, "guid"), diagnostic.Value<string>("entry_guid"));
            Assert.AreEqual(root + "/Reference.asset", diagnostic.Value<string>("path"));
            if (length == 256)
            {
                Assert.AreEqual("missing_asset_reference", diagnostic.Value<string>("code"));
                Assert.AreEqual("References.Array.data[" + brokenIndex + "].m_AssetGUID", diagnostic.Value<string>("property_path"));
            }
            else
            {
                Assert.AreEqual(coverage.Value<string>("skipped_array_diagnostic_code"), diagnostic.Value<string>("code"));
                Assert.AreEqual("References", diagnostic.Value<string>("property_path"));
                Assert.AreEqual(length, diagnostic.Value<int>("array_size"));
                Assert.AreEqual(256, diagnostic.Value<int>("array_element_limit"));
                Assert.AreEqual(Get(other, "guid"), diagnostic["object_identity"].Value<string>("guid"));
            }
            Assert.IsNull(Read()["reference_scan_coverage"], "Array coverage applies only to full preflight, not settings inspection.");
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(root + "/Reference.asset"));
        }

        [Test]
        public void PreflightPagesSkippedArraysAndRejectsContinuationAfterCoverageChanges()
        {
            Invoke(profiles, "SetValue", activeProfile, "Remote.LoadPath", "fixture/remote");
            var fixture = AssetDatabase.LoadAssetAtPath<AddressablesReferenceFixture>(root + "/Reference.asset");
            fixture.Reference = null;
            fixture.References = new object[257];
            EditorUtility.SetDirty(fixture);
            AssetDatabase.SaveAssetIfDirty(fixture);
            Invoke(profiles, "SetValue", activeProfile, "Local.BuildPath", "[MissingOwnedVariable]");

            JObject firstPage = Call("preflight", new JObject { ["page_size"] = 1 });
            Assert.IsTrue(firstPage.Value<bool>("success"), firstPage.ToString());
            var firstData = (JObject)firstPage["data"];
            string revision = firstData.Value<string>("revision");
            Assert.AreEqual(2, firstData.Value<int>("total"));
            Assert.IsTrue(firstData.Value<bool>("has_more"));
            JObject secondPage = Call(
                "preflight",
                new JObject
                {
                    ["page_number"] = 2,
                    ["page_size"] = 1,
                    ["expected_revision"] = revision,
                }
            );
            Assert.IsTrue(secondPage.Value<bool>("success"), secondPage.ToString());
            var secondData = (JObject)secondPage["data"];
            Assert.AreEqual(1, ((JArray)secondData["items"]).Count);
            Assert.IsFalse(secondData.Value<bool>("has_more"));
            Assert.IsTrue(JToken.DeepEquals(firstData["reference_scan_coverage"], secondData["reference_scan_coverage"]));
            CollectionAssert.AreEquivalent(
                new[] { "reference_scan_array_skipped", "unknown_profile_token" },
                ((JArray)firstData["items"]).Concat((JArray)secondData["items"]).Select(row => row.Value<string>("code")).ToArray()
            );

            foreach (int length in new[] { 258, 256 })
            {
                fixture.References = new object[length];
                EditorUtility.SetDirty(fixture);
                AssetDatabase.SaveAssetIfDirty(fixture);
                JObject stale = Call(
                    "preflight",
                    new JObject
                    {
                        ["page_number"] = 2,
                        ["page_size"] = 1,
                        ["expected_revision"] = revision,
                    }
                );
                Assert.IsFalse(stale.Value<bool>("success"), stale.ToString());
                Assert.AreEqual("stale_addressables_revision", stale.Value<string>("code"));
                JObject current = Call("preflight", new JObject { ["page_size"] = 1 });
                Assert.IsTrue(current.Value<bool>("success"), current.ToString());
                Assert.AreNotEqual(revision, current["data"].Value<string>("revision"));
                Assert.AreEqual(length == 256, current["data"]["reference_scan_coverage"].Value<bool>("complete"));
                revision = current["data"].Value<string>("revision");
            }
        }

        [Test]
        public void GuardedAddressAndGroupMovePreserveGuidLabelsAssetsAndUndo()
        {
            var nativeSettings = (UnityEngine.Object)settings;
            var nativeFirst = (UnityEngine.Object)first;
            string[] labels = Labels(entry);
            JObject response = Call(
                "set_address",
                new JObject
                {
                    ["entry_guid"] = assetGuid,
                    ["address"] = "owned/renamed",
                    ["expected_revision"] = Read().Value<string>("revision"),
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("owned/renamed", Get(entry, "address"));
            JObject moved = Call(
                "move_entry",
                new JObject
                {
                    ["entry_guid"] = assetGuid,
                    ["group_guid"] = (string)Get(second, "Guid"),
                    ["expected_revision"] = Read().Value<string>("revision"),
                }
            );
            Assert.IsTrue(moved.Value<bool>("success"), moved.ToString());
            object fresh = Invoke(settings, "FindAssetEntry", assetGuid);
            Assert.AreEqual((string)Get(second, "Guid"), Get(Get(fresh, "parentGroup"), "Guid"));
            Assert.AreEqual(assetGuid, Get(fresh, "guid"));
            CollectionAssert.AreEqual(labels, Labels(fresh));
            SameAsset(nativeSettings, AssetDatabase.LoadMainAssetAtPath(settingsPath));
            SameAsset(nativeFirst, AssetDatabase.LoadMainAssetAtPath(AssetDatabase.GetAssetPath(nativeFirst)));
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            object sourceRestored = Invoke(first, "GetAssetEntry", assetGuid);
            object destinationRestored = Invoke(second, "GetAssetEntry", assetGuid);
            Assert.IsNotNull(sourceRestored, "Native Undo must restore the source group's entry collection.");
            Assert.IsNull(destinationRestored, "Native Undo must remove the entry from the destination group's collection.");
            object restored = Invoke(settings, "FindAssetEntry", assetGuid);
            Assert.AreEqual(
                (string)Get(first, "Guid"),
                Get(Get(restored, "parentGroup"), "Guid"),
                "Native settings lookup must agree with restored group collections after Undo."
            );
            CollectionAssert.AreEqual(labels, Labels(restored));
            Assert.AreEqual("owned/renamed", Get(restored, "address"), "Undoing the move must preserve the preceding address edit.");
            Undo.PerformRedo();
            restored = Invoke(settings, "FindAssetEntry", assetGuid);
            Assert.AreEqual((string)Get(second, "Guid"), Get(Get(restored, "parentGroup"), "Guid"), "Redo must refresh the native settings lookup too.");
            CollectionAssert.AreEqual(labels, Labels(restored));
            Undo.PerformUndo();
            Undo.PerformUndo();
            restored = Invoke(settings, "FindAssetEntry", assetGuid);
            Assert.AreEqual((string)Get(first, "Guid"), Get(Get(restored, "parentGroup"), "Guid"));
            Assert.AreEqual("owned/first", Get(restored, "address"), "Undo must also restore the address through the settings lookup cache.");
            Undo.PerformRedo();
            Undo.PerformRedo();
            restored = Invoke(settings, "FindAssetEntry", assetGuid);
            Assert.AreEqual((string)Get(second, "Guid"), Get(Get(restored, "parentGroup"), "Guid"));
            Assert.AreEqual("owned/renamed", Get(restored, "address"));

            var bytes = Directory.GetFiles(root, "*", SearchOption.AllDirectories).ToDictionary(path => path, File.ReadAllBytes);
            JObject metadata = Read();
            Texture2D unrelated = AssetDatabase.LoadAssetAtPath<Texture2D>(root + "/First.asset");
            string name = unrelated.name;
            Undo.IncrementCurrentGroup();
            Undo.RegisterCompleteObjectUndo(unrelated, "Unrelated texture name");
            unrelated.name = name + "Changed";
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            Assert.AreEqual(name, unrelated.name);
            Assert.AreEqual(metadata.Value<string>("revision"), Read().Value<string>("revision"), "Unrelated Undo must not change Addressables metadata.");
            restored = Invoke(settings, "FindAssetEntry", assetGuid);
            Assert.AreEqual((string)Get(second, "Guid"), Get(Get(restored, "parentGroup"), "Guid"));
            foreach (var pair in bytes)
                CollectionAssert.AreEqual(pair.Value, File.ReadAllBytes(pair.Key), "Undo/Redo cache refresh must not write owned asset files.");
        }

        [Test]
        public void ScopedPostEditPreflightRetainsEntryAndIgnoresUnrelatedBrokenReference()
        {
            JObject response = Call(
                "set_address",
                new JObject
                {
                    ["entry_guid"] = assetGuid,
                    ["address"] = "owned/observed",
                    ["expected_revision"] = Read().Value<string>("revision"),
                    ["include_preflight"] = true,
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            JToken verification = response["data"]["verification"];
            Assert.AreEqual("passed", (string)verification["status"], response.ToString());
            Assert.IsTrue((bool)verification["complete"]);
            Assert.AreEqual(assetGuid, (string)verification["observed_values"]["entry"]["guid"]);
            Assert.AreEqual("owned/observed", (string)verification["observed_values"]["entry"]["address"]);
            Assert.AreEqual(response["data"]["revision"], verification["observed_values"]["revision"]);
            Assert.AreEqual("owned/observed", Get(entry, "address"));
        }

        [TestCase(true)]
        [TestCase(false)]
        public void CustomInlineReferenceCannotPassAndUnrelatedGuidFieldsAreNotMissingReferences(bool includeCustomReference)
        {
            Type fixtureType = Type.GetType(
                "MCPForUnityTests.AddressablesFixtures.CustomAddressablesReferenceFixture, MCPForUnity.Addressables.Fixtures",
                true
            );
            Type referenceType = fixtureType.Assembly.GetType("MCPForUnityTests.AddressablesFixtures.UserAssetReference", true);
            var source = new GameObject("Custom reference fixture");
            string path = root + "/CustomReference.prefab";
            try
            {
                Component fixture = source.AddComponent(fixtureType);
                Assert.IsNotNull(fixture, "The optional runtime fixture assembly must support attaching its MonoBehaviour.");
                Type colliding = fixtureType.GetField("Colliding").FieldType;
                Assert.AreEqual("AssetReference", colliding.Name);
                Assert.IsFalse(Type.GetType("UnityEngine.AddressableAssets.AssetReference, Unity.Addressables", true).IsAssignableFrom(colliding));
                if (includeCustomReference)
                    fixtureType.GetField("CustomReference").SetValue(fixture, Activator.CreateInstance(referenceType, new object[] { new string('e', 32) }));
                using (var serialized = new SerializedObject(fixture))
                {
                    Assert.AreEqual(SerializedPropertyType.Generic, serialized.FindProperty("Unrelated").propertyType);
                    Assert.IsNotNull(serialized.FindProperty("Unrelated.m_AssetGUID"));
                    Assert.AreEqual("AssetReference", serialized.FindProperty("Colliding").type);
                    Assert.AreEqual(new string('f', 32), serialized.FindProperty("Colliding.m_AssetGUID").stringValue);
                    if (includeCustomReference)
                    {
                        Assert.AreEqual(SerializedPropertyType.Generic, serialized.FindProperty("CustomReference").propertyType);
                        Assert.AreEqual(new string('e', 32), serialized.FindProperty("CustomReference.m_AssetGUID").stringValue);
                    }
                }
                PrefabUtility.SaveAsPrefabAsset(source, path);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(source);
            }
            string guid = AssetDatabase.AssetPathToGUID(path);
            object customEntry = Invoke(settings, "CreateOrMoveEntry", guid, first, false, false);
            Invoke(customEntry, "SetAddress", "owned/custom", false);
            JObject response = Call(
                "set_address",
                new JObject
                {
                    ["entry_guid"] = guid,
                    ["address"] = "owned/custom-observed",
                    ["expected_revision"] = Read().Value<string>("revision"),
                    ["include_preflight"] = true,
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            JToken verification = response["data"]["verification"];
            Assert.IsFalse((bool)verification["complete"], "Unresolved inline field types must not imply complete reference coverage.");
            Assert.AreNotEqual("passed", (string)verification["status"]);
            Assert.IsFalse(
                verification["findings"]
                    .Any(f =>
                        (string)f["code"] == "missing_asset_reference"
                        && (
                            (string)f["property_path"] == "Unrelated.m_AssetGUID"
                            || (string)f["property_path"] == "m_AssetGUID"
                            || (string)f["property_path"] == "Colliding.m_AssetGUID"
                        )
                    ),
                response.ToString()
            );
            Assert.AreEqual("owned/custom-observed", Get(customEntry, "address"));
        }

        [Test]
        public void ScopedPostEditPreflightRecognizesManagedReferenceAssemblyIdentity()
        {
            JObject response = Call(
                "set_address",
                new JObject
                {
                    ["entry_guid"] = (string)Get(other, "guid"),
                    ["address"] = "owned/managed-observed",
                    ["expected_revision"] = Read().Value<string>("revision"),
                    ["include_preflight"] = true,
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            JToken verification = response["data"]["verification"];
            Assert.IsTrue((bool)verification["complete"], response.ToString());
            Assert.AreEqual("issues_found", (string)verification["status"]);
            Assert.IsTrue(
                verification["findings"].Any(f => (string)f["code"] == "missing_asset_reference" && (string)f["property_path"] == "Reference.m_AssetGUID"),
                response.ToString()
            );
        }

        [Test]
        public void GuardedProfileCorrectionPreservesExistingProfileIdentityAndOtherValues()
        {
            string name = "Local.BuildPath";
            string oldValue = (string)Invoke(profiles, "GetValueByName", activeProfile, "Local.LoadPath");
            string[] labels = Labels(entry);
            JObject response = Call(
                "set_profile_value",
                new JObject
                {
                    ["profile_id"] = activeProfile,
                    ["variable_name"] = name,
                    ["value"] = "Library/OwnedAddressables",
                    ["expected_revision"] = Read("profiles").Value<string>("revision"),
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("Library/OwnedAddressables", Invoke(profiles, "GetValueByName", activeProfile, name));
            Assert.AreEqual(oldValue, Invoke(profiles, "GetValueByName", activeProfile, "Local.LoadPath"));
            Assert.AreEqual(activeProfile, Get(settings, "activeProfileId"));
            CollectionAssert.AreEqual(labels, Labels(entry));
            Assert.AreEqual(assetGuid, Get(entry, "guid"));
        }

        [Test]
        public void BracketedAddressRefusesBeforeNativeMutationAndPreservesIdentityLabelsAndBytes()
        {
            foreach (string guid in AssetDatabase.FindAssets("", new[] { root }))
            {
                UnityEngine.Object asset = AssetDatabase.LoadMainAssetAtPath(AssetDatabase.GUIDToAssetPath(guid));
                if (asset != null)
                    AssetDatabase.SaveAssetIfDirty(asset);
            }
            string[] labels = Labels(entry);
            string address = (string)Get(entry, "address");
            string guidBefore = (string)Get(entry, "guid");
            JObject before = Read();
            UnityEngine.Object nativeSettings = (UnityEngine.Object)settings;
            var bytes = Directory.GetFiles(root, "*", SearchOption.AllDirectories).ToDictionary(path => path, File.ReadAllBytes);
            JObject response = Call(
                "set_address",
                new JObject
                {
                    ["entry_guid"] = assetGuid,
                    ["address"] = "[bad]",
                    ["expected_revision"] = before["revision"],
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), "Package-invalid addresses must be rejected before native SetAddress: " + response);
            Assert.AreEqual("invalid_addressables_request", response.Value<string>("code"));
            Assert.AreEqual(address, Get(entry, "address"), "Refusal must preserve the existing in-memory address.");
            Assert.AreEqual(guidBefore, Get(entry, "guid"));
            CollectionAssert.AreEqual(labels, Labels(entry));
            SameAsset(nativeSettings, AssetDatabase.LoadMainAssetAtPath(settingsPath));
            Assert.AreEqual(before.Value<string>("revision"), Read().Value<string>("revision"));
            CollectionAssert.AreEquivalent(bytes.Keys, Directory.GetFiles(root, "*", SearchOption.AllDirectories));
            foreach (var pair in bytes)
                CollectionAssert.AreEqual(pair.Value, File.ReadAllBytes(pair.Key), "Refusal must preserve owned file bytes: " + pair.Key);
        }

        [Test]
        public void StaleContinuationDuplicateAddressAndUnknownProfileRefuseBeforeWrites()
        {
            JObject before = Read("entries", 1, 1);
            Invoke(entry, "SetAddress", "owned/external", false);
            JObject stale = Call(
                "inspect",
                new JObject
                {
                    ["section"] = "entries",
                    ["page_number"] = 2,
                    ["page_size"] = 1,
                    ["expected_revision"] = before["revision"],
                }
            );
            Assert.IsFalse(stale.Value<bool>("success"), stale.ToString());
            Assert.AreEqual("stale_addressables_revision", stale.Value<string>("code"));
            JObject current = Read();
            JObject duplicate = Call(
                "set_address",
                new JObject
                {
                    ["entry_guid"] = assetGuid,
                    ["address"] = (string)Get(other, "address"),
                    ["expected_revision"] = current["revision"],
                }
            );
            Assert.IsFalse(duplicate.Value<bool>("success"), duplicate.ToString());
            JObject profile = Call(
                "set_profile_value",
                new JObject
                {
                    ["profile_id"] = new string('d', 32),
                    ["variable_name"] = "Local.BuildPath",
                    ["value"] = "Library/Owned",
                    ["expected_revision"] = current["revision"],
                }
            );
            Assert.IsFalse(profile.Value<bool>("success"), profile.ToString());
            Assert.AreEqual("owned/external", Get(entry, "address"));
            CollectionAssert.AreEqual(new[] { "owned-label" }, Labels(entry));
        }
    }
}
