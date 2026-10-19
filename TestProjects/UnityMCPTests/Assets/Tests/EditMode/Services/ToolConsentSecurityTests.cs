using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Blender;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    public class ToolConsentSecurityTests
    {
        private static readonly string[] Names =
        {
            "execute_code",
            "manage_packages",
            "execute_menu_item",
            "manage_build",
            "batch_execute",
            "manage_script",
            "blender_bridge",
        };
        private readonly Dictionary<string, bool?> saved = new();
        private ScopedConsentTestState consent;

        [SetUp]
        public void SetUp()
        {
            consent = new ScopedConsentTestState();
            foreach (string name in Names)
            foreach (string key in new[] { EditorPrefKeys.ToolEnabledPrefix + name, ToolDiscoveryService.GetConsentPreferenceKey(name) })
            {
                saved[key] = EditorPrefs.HasKey(key) ? EditorPrefs.GetBool(key) : (bool?)null;
                EditorPrefs.DeleteKey(key);
            }
        }

        [TearDown]
        public void TearDown()
        {
            consent?.Dispose();
            foreach (var entry in saved)
                if (entry.Value.HasValue)
                    EditorPrefs.SetBool(entry.Key, entry.Value.Value);
                else
                    EditorPrefs.DeleteKey(entry.Key);
        }

        [TestCaseSource(nameof(Names))]
        public async Task DispatcherRejectsToolsWithoutConsent(string name)
        {
            string result = await TransportCommandDispatcher.ExecuteCommandJsonAsync(
                new JObject { ["type"] = name, ["params"] = new JObject() }.ToString(),
                CancellationToken.None
            );
            StringAssert.Contains("disabled", result.ToLowerInvariant());
        }

        [TestCase("execute_code")]
        [TestCase("manage_packages")]
        [TestCase("execute_menu_item")]
        [TestCase("manage_build")]
        [TestCase("manage_script")]
        [TestCase("blender_bridge")]
        public async Task BatchRejectsToolsWithoutConsent(string name)
        {
            Assert.IsTrue(consent.Service.Grant("batch_execute", ToolConsentScope.EditorSession).Succeeded);
            var result = JObject.FromObject(await BatchExecute.HandleCommand(new JObject { ["commands"] = new JArray(new JObject { ["tool"] = name }) }));
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("disabled", result.ToString());
        }

        [TestCaseSource(nameof(Names))]
        public void FreshAndPreviouslyImplicitEnablementRequireEditorConsent(string name)
        {
            var service = new ToolDiscoveryService();
            Assert.IsTrue(service.GetToolMetadata(name).RequiresExplicitConsent);
            Assert.IsFalse(service.IsToolEnabled(name));
            EditorPrefs.SetBool(EditorPrefKeys.ToolEnabledPrefix + name, true);
            Assert.IsFalse(service.IsToolEnabled(name));
            service.InvalidateCache();
            Assert.IsFalse(service.IsToolEnabled(name));
            service.SetToolEnabled(name, true);
            Assert.IsTrue(new ToolDiscoveryService().IsToolEnabled(name));
            service.SetToolEnabled(name, false);
            Assert.IsFalse(new ToolDiscoveryService().IsToolEnabled(name));
        }

        [TestCase("run_python")]
        [TestCase(" RUN_PYTHON ")]
        [TestCase("object_info")]
        [TestCase("sync_addon")]
        public async Task DirectBlenderCallsRejectLegacyEnablement(string action)
        {
            // An unconfigured checkout keeps sync_addon inert if the consent guard regresses.
            string forkKey = EditorPrefKeys.BlenderForkPath;
            string savedFork = EditorPrefs.HasKey(forkKey) ? EditorPrefs.GetString(forkKey) : null;
            EditorPrefs.DeleteKey(forkKey);
            try
            {
                EditorPrefs.SetBool(EditorPrefKeys.ToolEnabledPrefix + "blender_bridge", true);
                var response = JObject.FromObject(await BlenderBridgeTool.HandleCommand(new JObject { ["action"] = action }));
                Assert.IsFalse(response.Value<bool>("success"));
                Assert.AreEqual("blender_consent_required", (string)response["code"]);
            }
            finally
            {
                if (savedFork != null)
                    EditorPrefs.SetString(forkKey, savedFork);
                else
                    EditorPrefs.DeleteKey(forkKey);
            }
        }

        [TestCase("direct")]
        [TestCase("registry")]
        [TestCase("dispatcher")]
        [TestCase("batch")]
        public async Task BlenderConsentRevocationBlocksExistingDispatchPaths(string route)
        {
            var service = MCPServiceLocator.ToolDiscovery;
            service.SetToolEnabled("batch_execute", true);
            service.SetToolEnabled("blender_bridge", true);
            var allowed = await InvokeBlender(route);
            StringAssert.Contains(
                "'code' is required",
                allowed.ToString(),
                "An explicit grant must reach normal parameter validation without contacting Blender."
            );

            service.SetToolEnabled("blender_bridge", false);
            var denied = await InvokeBlender(route);
            Assert.IsFalse(denied.Value<bool>("success"));
            if (route == "direct" || route == "registry")
                Assert.AreEqual("blender_consent_required", (string)denied["code"]);
            else
                StringAssert.Contains("disabled", denied.ToString().ToLowerInvariant());
        }

        private static async Task<JObject> InvokeBlender(string route)
        {
            var parameters = new JObject { ["action"] = "run_python" };
            switch (route)
            {
                case "direct":
                    return JObject.FromObject(await BlenderBridgeTool.HandleCommand(parameters));
                case "registry":
                    return JObject.FromObject(await CommandRegistry.InvokeCommandAsync("blender_bridge", parameters));
                case "dispatcher":
                    return JObject.Parse(
                        await TransportCommandDispatcher.ExecuteCommandJsonAsync(
                            new JObject { ["type"] = "blender_bridge", ["params"] = parameters }.ToString(),
                            CancellationToken.None
                        )
                    );
                default:
                    return JObject.FromObject(
                        await BatchExecute.HandleCommand(
                            new JObject { ["commands"] = new JArray(new JObject { ["tool"] = "blender_bridge", ["params"] = parameters }) }
                        )
                    );
            }
        }

        [TestCase("create")]
        [TestCase("update")]
        [TestCase("delete")]
        [TestCase("apply_text_edits")]
        [TestCase("edit")]
        [TestCase("read")]
        [TestCase("get_sha")]
        [TestCase("validate")]
        [TestCase("preview_text_edits")]
        [TestCase("preview_edit")]
        public void DirectScriptAliasesCannotUseLegacyEnablementAsConsent(string action)
        {
            EditorPrefs.SetBool(EditorPrefKeys.ToolEnabledPrefix + "manage_script", true);
            var response = JObject.FromObject(
                ManageScript.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["name"] = "OwnedConsentProbe",
                        ["path"] = "Assets",
                        ["options"] = new JObject { ["preview"] = true },
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("script_consent_required", (string)response["code"]);
        }

        [TestCase("duplicate", ".cs")]
        [TestCase("move", ".dll")]
        [TestCase("rename", ".asmdef")]
        [TestCase("duplicate", ".asmref")]
        [TestCase("move", ".rsp")]
        [TestCase("duplicate", ".so")]
        [TestCase("move", ".dylib")]
        [TestCase("rename", ".bundle")]
        [TestCase("duplicate", ".winmd")]
        [TestCase("move", ".framework")]
        [TestCase("duplicate", ".dll.meta")]
        public void ExecutableAssetDestinationsRequireConsentBeforeFilesystemWork(string action, string extension)
        {
            string directory = "Assets/__McpConsent_" + System.Guid.NewGuid().ToString("N");
            string full = System.IO.Path.Combine(UnityEngine.Application.dataPath, System.IO.Path.GetFileName(directory));
            Assert.IsFalse(System.IO.Directory.Exists(full) || System.IO.File.Exists(full));
            EditorPrefs.SetBool(EditorPrefKeys.ToolEnabledPrefix + "manage_script", true);
            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["path"] = directory + "/Inert.uss",
                        ["destination"] = directory + "/New/Probe" + extension,
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("script_consent_required", (string)response["code"]);
            Assert.IsFalse(System.IO.Directory.Exists(full));
        }

        [TestCase(0, 0)]
        [TestCase(1, 129)]
        public void IncompleteFolderInspectionRequiresConsentWithoutTraversal(int remaining, int depth)
        {
            var method = typeof(ManageAsset).GetMethod("AffectsCompilation", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            // Exhaust the actual classification budget without constructing a huge tree.
            object[] args = { "Assets/OwnedInertFolder", remaining, depth, System.Diagnostics.Stopwatch.StartNew() };
            Assert.IsTrue((bool)method.Invoke(null, args));
        }

        [TestCase("execute_code")]
        [TestCase("manage_script")]
        public async Task CapturedCatalogDoesNotAuthorizeChangedRootOrRevokedEntry(string name)
        {
            Assert.IsTrue(consent.Service.Grant(name, ToolConsentScope.Project).Succeeded);
            var discovery = new ToolDiscoveryService();
            Assert.IsTrue(discovery.GetEnabledTools().Exists(tool => tool.Name == name), "Capture an actually enabled catalog first.");
            using var handlers = new ProgressHandlerScope();
            int calls = 0;
            handlers.Set(
                name,
                new HandlerInfo(
                    name,
                    _ =>
                    {
                        calls++;
                        return new { success = true };
                    },
                    null
                )
            );
            string rootA = consent.Root;
            consent.Root = rootA + "-other";
            string other = await TransportCommandDispatcher.ExecuteCommandJsonAsync(
                new JObject { ["type"] = name, ["params"] = new JObject() }.ToString(),
                CancellationToken.None
            );
            StringAssert.Contains("disabled", other.ToLowerInvariant());
            Assert.AreEqual(0, calls);
            consent.Root = rootA;
            Assert.IsTrue(consent.Service.Revoke(name).DeniedInCurrentDomain);
            discovery.InvalidateCache();
            string revoked = await TransportCommandDispatcher.ExecuteCommandJsonAsync(
                new JObject { ["type"] = name, ["params"] = new JObject() }.ToString(),
                CancellationToken.None
            );
            StringAssert.Contains("disabled", revoked.ToLowerInvariant());
            Assert.AreEqual(0, calls, "A stale registered catalog is not an entry grant.");
        }

        [Test]
        public async Task OffThreadDiscoveryAndStrictIdentityRefuseBeforeNativeFallback()
        {
            Assert.IsTrue(consent.Service.Grant("execute_code", ToolConsentScope.Project).Succeeded);
            var discovery = new ToolDiscoveryService();
            int reads = consent.StorageReads;
            await Task.Run(() =>
            {
                Assert.IsFalse(discovery.IsToolEnabled("execute_code"));
                Assert.IsNull(discovery.GetToolMetadata("execute_code"));
                Assert.IsEmpty(discovery.DiscoverAllTools());
                discovery.SetToolEnabled("execute_code", true);
                Assert.IsFalse(MCPForUnity.Editor.Helpers.ProjectIdentityUtility.TryGetConsentProjectRoot(out var root));
                Assert.IsNull(root);
            });
            Assert.AreEqual(reads, consent.StorageReads);
            Assert.IsTrue(discovery.IsToolEnabled("execute_code"), "The off-thread refusal must not revoke the main-thread grant.");
        }

        [Test]
        public void DirectScriptGuardUsesCurrentProjectAndRevocation()
        {
            Assert.IsTrue(consent.Service.Grant("manage_script", ToolConsentScope.Project).Succeeded);
            Assert.IsNull(ManageScript.RequireExplicitConsent(), "The actual direct/asset-alias guard accepts the scoped grant without executing script work.");
            string rootA = consent.Root;
            consent.Root = rootA + "-other";
            Assert.AreEqual("script_consent_required", JObject.FromObject(ManageScript.RequireExplicitConsent()).Value<string>("code"));
            consent.Root = rootA;
            Assert.IsTrue(consent.Service.Revoke("manage_script").DeniedInCurrentDomain);
            Assert.AreEqual("script_consent_required", JObject.FromObject(ManageScript.RequireExplicitConsent()).Value<string>("code"));
        }
    }

    // Uses the real consent transaction with an isolated preference domain. It
    // never grants into the user's EditorPrefs or consumes Shared latch capacity.
    internal sealed class ScopedConsentTestState : IToolConsentStorage, IDisposable
    {
        private static readonly FieldInfo ServiceField = typeof(MCPServiceLocator).GetField(
            "_toolConsentService",
            BindingFlags.Static | BindingFlags.NonPublic
        );
        private readonly object previous;
        private readonly int mainThread = Thread.CurrentThread.ManagedThreadId;
        private readonly Dictionary<(ToolConsentStorageScope, string), bool> bools = new();
        private readonly Dictionary<(ToolConsentStorageScope, string), string> strings = new();
        internal readonly ToolConsentProcessState Domain = new();
        internal string Root = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "core12-entry-" + Guid.NewGuid().ToString("N")));
        internal ToolConsentService Service { get; }
        internal int StorageReads,
            StorageWrites;
        internal bool FailAfterNextWrite,
            FailAll;

        internal ScopedConsentTestState(params string[] enabled)
        {
            previous = ServiceField.GetValue(null);
            Service = new ToolConsentService(this, () => Thread.CurrentThread.ManagedThreadId == mainThread, () => Root, Domain);
            ServiceField.SetValue(null, Service);
            try
            {
                foreach (string name in enabled)
                    Assert.IsTrue(Service.Grant(name, ToolConsentScope.EditorSession).Succeeded);
            }
            catch
            {
                Dispose();
                throw;
            }
        }

        private void Read()
        {
            StorageReads++;
            if (FailAll)
                throw new IOException("Owned consent read failure");
        }

        private void BeforeWrite()
        {
            StorageWrites++;
            if (FailAll)
                throw new IOException("Owned consent write failure");
        }

        private void AfterWrite()
        {
            if (!FailAfterNextWrite)
                return;
            FailAfterNextWrite = false;
            throw new IOException("Owned committed write then failure");
        }

        public bool GetBool(ToolConsentStorageScope scope, string key)
        {
            Read();
            if (key.StartsWith(EditorPrefKeys.ToolEnabledPrefix + "ExplicitConsent.", StringComparison.Ordinal))
                return EditorPrefs.GetBool(key, false);
            return bools.TryGetValue((scope, key), out bool value) && value;
        }

        public void SetBool(ToolConsentStorageScope scope, string key, bool value)
        {
            BeforeWrite();
            bools[(scope, key)] = value;
            AfterWrite();
        }

        public void DeleteBool(ToolConsentStorageScope scope, string key)
        {
            BeforeWrite();
            bools.Remove((scope, key));
            AfterWrite();
        }

        public string GetString(ToolConsentStorageScope scope, string key)
        {
            Read();
            return strings.TryGetValue((scope, key), out string value) ? value : null;
        }

        public void SetString(ToolConsentStorageScope scope, string key, string value)
        {
            BeforeWrite();
            strings[(scope, key)] = value;
            AfterWrite();
        }

        public void Dispose() => ServiceField.SetValue(null, previous);
    }
}
