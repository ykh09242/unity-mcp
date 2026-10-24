using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Windows.Components.ClientConfig;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    [Parallelizable(ParallelScope.None)]
    public class ClaudeCliContextTests
    {
        private sealed class TestWindow : EditorWindow { }

        private readonly List<TestWindow> windows = new();
        private readonly List<Work> pending = new();
        private string root,
            projectA,
            projectB,
            configPath,
            originalConfigDir;
        private object originalClients,
            originalPaths;
        private string originalProject;
        private bool hadProject,
            hadRefresh,
            savedRefresh;
        private readonly Dictionary<string, string> savedStringPrefs = new();
        private MemoryCli client;
        private MemoryPaths paths;
        private readonly Dictionary<string, object> cacheValues = new();

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "mcp-cli-context-" + Guid.NewGuid().ToString("N"));
            projectA = Path.Combine(root, "a");
            projectB = Path.Combine(root, "b");
            Directory.CreateDirectory(projectA);
            Directory.CreateDirectory(projectB);
            configPath = Path.Combine(root, ".claude.json");
            originalConfigDir = Environment.GetEnvironmentVariable("CLAUDE_CONFIG_DIR");
            Environment.SetEnvironmentVariable("CLAUDE_CONFIG_DIR", root);
            hadProject = EditorPrefs.HasKey(EditorPrefKeys.ClientProjectDirOverride);
            originalProject = EditorPrefs.GetString(EditorPrefKeys.ClientProjectDirOverride, "");
            SelectProject(projectA);
            hadRefresh = EditorPrefs.HasKey(EditorPrefKeys.DevModeForceServerRefresh);
            savedRefresh = EditorPrefs.GetBool(EditorPrefKeys.DevModeForceServerRefresh, false);
            EditorPrefs.SetBool(EditorPrefKeys.DevModeForceServerRefresh, true);
            foreach (string key in new[] { EditorPrefKeys.GitUrlOverride, EditorPrefKeys.HttpBaseUrl })
                savedStringPrefs[key] = EditorPrefs.HasKey(key) ? EditorPrefs.GetString(key) : null;
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, "synthetic-package-source-a");
            EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, "http://127.0.0.1:19040");
            foreach (string name in new[] { "_forceStdioForSession", "_useHttpTransport", "_devModeForceServerRefresh" })
                cacheValues[name] = CacheField(name).GetValue(EditorConfigurationCache.Instance);
            cacheValues["_httpTransportScope"] = CacheField("_httpTransportScope").GetValue(EditorConfigurationCache.Instance);
            CacheField("_httpTransportScope").SetValue(EditorConfigurationCache.Instance, "local");
            CacheField("_forceStdioForSession").SetValue(EditorConfigurationCache.Instance, false);
            CacheField("_useHttpTransport").SetValue(EditorConfigurationCache.Instance, false);
            CacheField("_devModeForceServerRefresh").SetValue(EditorConfigurationCache.Instance, true);
            originalClients = ServiceField("_clientService").GetValue(null);
            originalPaths = ServiceField("_pathService").GetValue(null);
            client = new MemoryCli(configPath);
            paths = new MemoryPaths();
            ServiceField("_clientService").SetValue(null, new MemoryClients(client));
            ServiceField("_pathService").SetValue(null, paths);
            WriteRegistration(projectA);
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var window in windows)
                if (window != null)
                    window.Close();
            windows.Clear();
            foreach (var work in pending)
                work.Cancel();
            pending.Clear();
            ServiceField("_clientService").SetValue(null, originalClients);
            ServiceField("_pathService").SetValue(null, originalPaths);
            foreach (var pair in cacheValues)
                CacheField(pair.Key).SetValue(EditorConfigurationCache.Instance, pair.Value);
            cacheValues.Clear();
            if (hadProject)
                EditorPrefs.SetString(EditorPrefKeys.ClientProjectDirOverride, originalProject);
            else
                EditorPrefs.DeleteKey(EditorPrefKeys.ClientProjectDirOverride);
            if (hadRefresh)
                EditorPrefs.SetBool(EditorPrefKeys.DevModeForceServerRefresh, savedRefresh);
            else
                EditorPrefs.DeleteKey(EditorPrefKeys.DevModeForceServerRefresh);
            foreach (var preference in savedStringPrefs)
                if (preference.Value == null)
                    EditorPrefs.DeleteKey(preference.Key);
                else
                    EditorPrefs.SetString(preference.Key, preference.Value);
            savedStringPrefs.Clear();
            Environment.SetEnvironmentVariable("CLAUDE_CONFIG_DIR", originalConfigDir);
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        [UnityTest]
        public IEnumerator CompletedProjectStatusIsInvalidatedBeforeTtl()
        {
            var section = CreateSection();
            yield return WaitFor(() => Label(section) == "Configured", "Configured status for selected context");
            SelectProject(projectB);
            section.RefreshSelectedClient();
            yield return WaitFor(() => Label(section) == "Not Configured", "Not Configured status for project B");
            Assert.AreEqual("Configure", Button(section));
        }

        [UnityTest]
        public IEnumerator ActionRechecksExactProjectInsteadOfUsingCompletedStatus()
        {
            var section = CreateSection();
            yield return WaitFor(() => Label(section) == "Configured", "Configured status for selected context");
            SelectProject(projectB);
            // A's result says unregister, but B has no registration when clicked.
            Invoke(section, "OnConfigureClicked");
            yield return WaitFor(() => client.Calls.Count > 0 && Label(section) == "Configured", "Completed CLI action for project B");
            Assert.That(client.Calls[0], Does.StartWith(projectB + "|mcp add "));
        }

        [UnityTest]
        public IEnumerator BlockedOldProjectCannotPublishOverNewProject()
        {
            var section = CreateSection();
            yield return WaitFor(() => Label(section) == "Configured", "Configured status for selected context");
            ControlWork(section);
            section.RefreshSelectedClient(true);
            Assert.AreEqual(1, pending.Count);
            SelectProject(projectB);
            section.RefreshSelectedClient();
            Assert.AreEqual(2, pending.Count, "A pending probe must not suppress the new target.");
            pending[1].Complete();
            yield return WaitFor(() => Label(section) == "Not Configured", "Not Configured status for project B");
            pending[0].Complete();
            yield return null;
            yield return null;
            Assert.AreEqual("Not Configured", Label(section));
            Assert.AreEqual("Configure", Button(section));
        }

        [UnityTest]
        public IEnumerator ExecutableAndTransportChangesInvalidateCompletedStatus()
        {
            var section = CreateSection();
            yield return WaitFor(() => Label(section) == "Configured", "Configured status for selected context");
            ControlWork(section);
            paths.Claude = "replacement-claude";
            section.RefreshSelectedClient();
            Assert.AreEqual(1, pending.Count);
            pending[0].Complete();
            yield return WaitFor(() => Label(section) == "Configured", "Configured status for selected context");
            CacheField("_useHttpTransport").SetValue(EditorConfigurationCache.Instance, true);
            section.RefreshSelectedClient();
            Assert.AreEqual(2, pending.Count);
            pending[1].Complete();
            yield return WaitFor(() => Label(section) == "Configured", "Configured status for selected context");
        }

        [UnityTest]
        public IEnumerator DetachAndReattachRejectsPreviousLifetimeCompletion()
        {
            var section = CreateSection();
            yield return WaitFor(() => Label(section) == "Configured", "Configured status for selected context");
            ControlWork(section);
            section.RefreshSelectedClient(true);
            var panelRoot = windows[0].rootVisualElement;
            section.Root.RemoveFromHierarchy();
            SelectProject(projectB);
            panelRoot.Add(section.Root);
            section.RefreshSelectedClient();
            Assert.AreEqual(2, pending.Count);
            pending[1].Complete();
            yield return WaitFor(() => Label(section) == "Not Configured", "Not Configured status for project B");
            pending[0].Complete();
            yield return null;
            yield return null;
            Assert.AreEqual("Not Configured", Label(section));
        }

        [UnityTest]
        public IEnumerator TwoWindowsDoNotShareMutableProbeState()
        {
            var first = CreateSection();
            yield return WaitFor(() => Label(first) == "Configured", "First window A status");
            SelectProject(projectB);
            var second = CreateSection();
            yield return WaitFor(() => Label(second) == "Not Configured", "Second window B status");
            SelectProject(projectA);
            first.RefreshSelectedClient();
            Assert.AreEqual("Configured", Label(first));
            Assert.AreEqual("Unregister", Button(first));
            Assert.AreEqual(McpStatus.NotConfigured, client.Status, "Window probes must not mutate the shared service configurator.");
        }

        [UnityTest]
        public IEnumerator ClosedWindowProbeCannotAffectReopenedWindow()
        {
            var first = CreateSection();
            yield return WaitFor(() => Label(first) == "Configured", "First window A status before close");
            ControlWork(first);
            first.RefreshSelectedClient(true);
            windows[0].Close();
            SelectProject(projectB);
            var reopened = CreateSection();
            yield return WaitFor(() => Label(reopened) == "Not Configured", "Reopened window B status");
            pending[0].Complete();
            yield return null;
            yield return null;
            Assert.AreEqual("Not Configured", Label(reopened));
            Assert.AreEqual("Configure", Button(reopened));
            Assert.AreEqual(McpStatus.NotConfigured, client.Status);
        }

        [UnityTest]
        public IEnumerator PackageSourceAndEndpointChangesInvalidateCompletedStatus()
        {
            var section = CreateSection();
            yield return WaitFor(() => Label(section) == "Configured", "Initial project A status");
            ControlWork(section);
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, "synthetic-package-source-b");
            section.RefreshSelectedClient();
            Assert.AreEqual(1, pending.Count, "Package source changed within the status TTL.");
            pending[0].Complete();
            yield return WaitFor(() => Label(section) == "Configured", "Updated source status");
            CacheField("_useHttpTransport").SetValue(EditorConfigurationCache.Instance, true);
            section.RefreshSelectedClient();
            Assert.AreEqual(2, pending.Count);
            pending[1].Complete();
            yield return WaitFor(() => Label(section) == "Configured", "HTTP context status");
            EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, "http://127.0.0.1:19041");
            section.RefreshSelectedClient();
            Assert.AreEqual(3, pending.Count, "Endpoint changed within the status TTL.");
            pending[2].Complete();
            yield return WaitFor(() => Label(section) == "Configured", "Updated endpoint status");
        }

        [UnityTest]
        public IEnumerator ActionProbeDiscardedAfterTargetChanges()
        {
            var section = CreateSection();
            yield return WaitFor(() => Label(section) == "Configured", "Initial project A status");
            ControlWork(section);
            Invoke(section, "OnConfigureClicked");
            Assert.AreEqual(1, pending.Count);
            SelectProject(projectB);
            pending[0].Complete();
            yield return null;
            yield return null;
            Assert.IsEmpty(client.Calls, "A stale action probe must not start any CLI mutation.");
            section.RefreshSelectedClient();
            Assert.AreEqual(2, pending.Count);
            pending[1].Complete();
            yield return WaitFor(() => Label(section) == "Not Configured", "Project B after abandoned A action");
        }

        [UnityTest]
        public IEnumerator InvalidContextDoesNotReuseConfiguredStatusAndCanRecover()
        {
            var section = CreateSection();
            yield return WaitFor(() => Label(section) == "Configured", "Initial project A status");
            paths.FailResolution = true;
            Assert.DoesNotThrow(() => section.UpdateClientStatus());
            Assert.AreEqual("Configuration unavailable", Label(section));
            Assert.IsFalse(section.Root.Q<Button>("configure-button").enabledSelf);
            paths.FailResolution = false;
            section.RefreshSelectedClient();
            yield return WaitFor(() => Label(section) == "Configured", "Recovered project A context");
        }

        [UnityTest]
        public IEnumerator CompletedProbeWithUnavailableContextDoesNotStrandPendingStatus()
        {
            yield return RejectedCompletionCanRefresh(false, true);
        }

        [UnityTest]
        public IEnumerator CompletedProbeAfterTemporaryContextChangeCanRefreshOriginalTarget()
        {
            yield return RejectedCompletionCanRefresh(false, false);
        }

        [UnityTest]
        public IEnumerator AbandonedActionWithUnavailableContextDoesNotStrandPendingStatus()
        {
            yield return RejectedCompletionCanRefresh(true, true);
        }

        [UnityTest]
        public IEnumerator AbandonedActionAfterTemporaryContextChangeCanRefreshOriginalTarget()
        {
            yield return RejectedCompletionCanRefresh(true, false);
        }

        private IEnumerator RejectedCompletionCanRefresh(bool action, bool unavailable)
        {
            var section = CreateSection();
            yield return WaitFor(() => Label(section) == "Configured", "Initial project A status");
            ControlWork(section);
            if (action)
                Invoke(section, "OnConfigureClicked");
            else
                section.RefreshSelectedClient(true);
            Assert.AreEqual(1, pending.Count);
            if (unavailable)
                paths.FailResolution = true;
            else
                SelectProject(projectB);
            pending[0].Complete();
            yield return null;
            yield return null;
            paths.FailResolution = false;
            SelectProject(projectA);
            section.RefreshSelectedClient(true);
            Assert.AreEqual(2, pending.Count, "Rejected completed work must no longer suppress a fresh probe.");
            pending[1].Complete();
            yield return WaitFor(() => Label(section) == "Configured", "Project A after rejected completion");
            Assert.IsTrue(section.Root.Q<Button>("configure-button").enabledSelf);
            Assert.IsEmpty(client.Calls);
        }

        private McpClientConfigSection CreateSection()
        {
            var window = ScriptableObject.CreateInstance<TestWindow>();
            windows.Add(window);
            window.ShowUtility();
            var rootElement = new VisualElement();
            AssetDatabase
                .LoadAssetAtPath<VisualTreeAsset>("Packages/com.ykh09242.unity-mcp/Editor/Windows/Components/ClientConfig/McpClientConfigSection.uxml")
                .CloneTree(rootElement);
            window.rootVisualElement.Add(rootElement);
            return new McpClientConfigSection(rootElement);
        }

        private void ControlWork(McpClientConfigSection section)
        {
            var field = typeof(McpClientConfigSection).GetField("runCliWork", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(field, "CLI work needs an owned scheduler so completion races can be tested deterministically.");
            field.SetValue(
                section,
                new Func<Action, Task>(action =>
                {
                    var work = new Work(action);
                    pending.Add(work);
                    return work.Task;
                })
            );
        }

        private sealed class Work
        {
            private readonly Action action;
            private readonly TaskCompletionSource<bool> completion = new();
            internal Task Task => completion.Task;

            internal Work(Action action) => this.action = action;

            internal void Complete()
            {
                try
                {
                    action();
                    completion.TrySetResult(true);
                }
                catch (Exception error)
                {
                    completion.TrySetException(error);
                }
            }

            internal void Cancel() => completion.TrySetCanceled();
        }

        private static IEnumerator WaitFor(Func<bool> predicate, string stage)
        {
            double deadline = EditorApplication.timeSinceStartup + 10;
            while (!predicate() && EditorApplication.timeSinceStartup < deadline)
                yield return null;
            Assert.IsTrue(predicate(), "Timed out waiting for: " + stage);
        }

        private static FieldInfo ServiceField(string name) => typeof(MCPServiceLocator).GetField(name, BindingFlags.NonPublic | BindingFlags.Static);

        private static FieldInfo CacheField(string name) => typeof(EditorConfigurationCache).GetField(name, BindingFlags.NonPublic | BindingFlags.Instance);

        private static void SelectProject(string project) => EditorPrefs.SetString(EditorPrefKeys.ClientProjectDirOverride, project);

        private static string Label(McpClientConfigSection section) => section.Root.Q<Label>("client-status").text;

        private static string Button(McpClientConfigSection section) => section.Root.Q<Button>("configure-button").text;

        private static void Invoke(McpClientConfigSection section, string method) =>
            typeof(McpClientConfigSection).GetMethod(method, BindingFlags.NonPublic | BindingFlags.Instance).Invoke(section, null);

        private void WriteRegistration(string project) =>
            File.WriteAllText(configPath, new JObject { ["projects"] = new JObject { [project] = new JObject { ["mcpServers"] = Servers() } } }.ToString());

        private static JObject Servers() =>
            new JObject
            {
                ["UnityMCP"] = new JObject { ["type"] = "stdio", ["command"] = "synthetic" },
            };

        private sealed class MemoryCli : ClaudeCliMcpConfigurator
        {
            internal readonly List<string> Calls = new();
            private readonly string config;

            internal MemoryCli(string config)
                : base(new McpClient { name = "Synthetic Claude" }) => this.config = config;

            public override string GetManualSnippet() => "synthetic";

            protected override bool RunCli(
                string executable,
                string arguments,
                string directory,
                out string stdout,
                out string stderr,
                int timeout,
                string pathPrepend
            )
            {
                Calls.Add(directory + "|" + arguments);
                var document = JObject.Parse(File.ReadAllText(config));
                var projects = (JObject)document["projects"];
                if (arguments.StartsWith("mcp add ", StringComparison.Ordinal))
                    projects[directory] = new JObject { ["mcpServers"] = Servers() };
                else if (arguments.StartsWith("mcp remove ", StringComparison.Ordinal))
                    ((JObject)projects[directory]?["mcpServers"])?.Remove("UnityMCP");
                File.WriteAllText(config, document.ToString());
                stdout = stderr = "";
                return true;
            }
        }

        private sealed class MemoryClients : IClientConfigurationService
        {
            private readonly IMcpClientConfigurator client;

            internal MemoryClients(IMcpClientConfigurator client) => this.client = client;

            public IReadOnlyList<IMcpClientConfigurator> GetAllClients() => new[] { client };

            public void ConfigureClient(IMcpClientConfigurator value) => throw new InvalidOperationException("Unexpected shared configuration.");

            public bool CheckClientStatus(IMcpClientConfigurator value, bool attemptAutoRewrite = true) =>
                throw new InvalidOperationException("Unexpected shared probe.");

            public ClientConfigurationSummary ConfigureAllDetectedClients() => throw new InvalidOperationException("Unexpected bulk configuration.");
        }

        private sealed class MemoryPaths : IPathResolverService
        {
            internal string Claude = "synthetic-claude";
            internal bool FailResolution;

            public string GetClaudeCliPath() => FailResolution ? throw new InvalidOperationException("Synthetic path resolution failure.") : Claude;

            public string GetUvxPath() => "synthetic-uvx";

            public bool IsPythonDetected() => false;

            public bool IsClaudeCliDetected() => true;

            public bool HasUvxPathOverride => true;
            public bool HasClaudeCliPathOverride => true;
            public bool HasUvxPathFallback => false;

            public void SetUvxPathOverride(string path) { }

            public void SetClaudeCliPathOverride(string path) => Claude = path;

            public void ClearUvxPathOverride() { }

            public void ClearClaudeCliPathOverride() { }

            public bool TryValidateUvxExecutable(string path, out string version)
            {
                version = null;
                return false;
            }

            public Task<string> ValidateUvxExecutableAsync(string path) => Task.FromResult<string>(null);
        }
    }
}
