using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using System.Threading.Tasks;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Dependencies.Models;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Windows;
using MCPForUnity.Editor.Windows.Components.AssetGen;
using MCPForUnity.Editor.Windows.Components.ClientConfig;
using MCPForUnity.Editor.Windows.Components.Connection;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class SetupAndProviderRecoveryTests
    {
        private const BindingFlags Instance = BindingFlags.Instance | BindingFlags.NonPublic;
        private const BindingFlags Static = BindingFlags.Static | BindingFlags.NonPublic;
        private const string Base = "Packages/com.ykh09242.unity-mcp/Editor/Windows/";

        private sealed class EvidenceWindow : EditorWindow { }

        private sealed class SetupClaudeClient : ClaudeCliMcpConfigurator
        {
            internal SetupClaudeClient()
                : base(new McpClient { name = "Synthetic Claude" }) { }

            public override bool IsInstalled => true;
        }

        private sealed class SetupHttpClient : JsonFileMcpConfigurator
        {
            internal SetupHttpClient()
                : base(new McpClient { name = "Synthetic HTTP" }) { }

            public override bool IsInstalled => true;

            public override string GetConfigPath() => "synthetic-http-config";

            public override void Configure() => Assert.Fail("Real configuration is forbidden.");

            internal void ValidateSyntheticHeaders(IDictionary<string, string> headers)
            {
                string prerequisite = AuthenticationPrerequisite(false, headers);
                client.SetStatus(prerequisite == null ? McpStatus.Configured : McpStatus.IncorrectPath, prerequisite);
            }

            internal void PrivateFailure(string detail) => client.SetStatus(McpStatus.Error, detail);
        }

        private static void Set(object target, string name, object value)
        {
            var field = target.GetType().GetField(name, Instance);
            Assert.IsNotNull(field, "Expected recovery state: " + name);
            field.SetValue(target, value);
        }

        private static object Invoke(object target, string name, params object[] args) => target.GetType().GetMethod(name, Instance).Invoke(target, args);

        private sealed class Client : IMcpClientConfigurator
        {
            public string Id { get; set; }
            public string DisplayName => Id;
            public McpStatus Status { get; set; } = McpStatus.Configured;
            public ConfiguredTransport ConfiguredTransport => default;
            public bool SupportsAutoConfigure { get; set; } = true;
            public bool IsInstalled => true;
            public IReadOnlyList<ConfiguredTransport> SupportedTransports { get; set; } = Array.Empty<ConfiguredTransport>();

            public string GetConfigureActionLabel() => "Configure";

            public string GetConfigPath() => "synthetic";

            public McpStatus CheckStatus(bool attemptAutoRewrite = true) => default;

            public void Configure() => Assert.Fail("Real configuration is forbidden.");

            public void Unregister() => Assert.Fail("No unregister.");

            public string GetManualSnippet() => "";

            public IList<string> GetInstallationSteps() => new[] { "Open the synthetic client and paste the manual configuration." };

            public bool SupportsSkills => false;

            public string GetSkillInstallPath() => null;
        }

        private static DependencyCheckResult ReadyDependencies() =>
            new DependencyCheckResult
            {
                IsSystemReady = true,
                Dependencies = new List<DependencyStatus>
                {
                    new DependencyStatus("Python")
                    {
                        IsAvailable = true,
                        Version = "3.11",
                        Details = "Python ready",
                    },
                    new DependencyStatus("uv Package Manager")
                    {
                        IsAvailable = true,
                        Version = "0.8",
                        Details = "uv ready",
                    },
                    new DependencyStatus("Git", false)
                    {
                        IsAvailable = true,
                        Version = "2.50",
                        Details = "Git ready",
                    },
                },
            };

        [TestCase(false)]
        [TestCase(true)]
        public void PostInstallProbeSupersedesEarlierCheck(bool earlierCompletesFirst)
        {
            WithReadySetup(
                (window, probe) =>
                {
                    var earlier = new TaskCompletionSource<DependencyCheckResult>();
                    var afterInstall = new TaskCompletionSource<DependencyCheckResult>();
                    var forces = new List<bool>();
                    probe.SetValue(
                        null,
                        (Func<bool, Task<DependencyCheckResult>>)(
                            force =>
                            {
                                forces.Add(force);
                                return forces.Count == 1 ? earlier.Task : afterInstall.Task;
                            }
                        )
                    );
                    Invoke(window, "BeginDependencyCheck", false);
                    if (earlierCompletesFirst)
                    {
                        earlier.SetResult(ReadyDependencies());
                        Invoke(window, "PollDependencies");
                    }
                    Invoke(window, "QueuePostInstallCheck");
                    if (!earlierCompletesFirst)
                    {
                        Invoke(window, "QueuePostInstallCheck");
                        Invoke(window, "OnDisable");
                        Invoke(window, "OnEnable");
                        earlier.SetResult(ReadyDependencies());
                        Invoke(window, "PollDependencies");
                    }
                    CollectionAssert.AreEqual(new[] { false, true }, forces);
                    Assert.AreEqual("Checking dependencies...", window.rootVisualElement.Q<Label>("status-message").text);
                    afterInstall.SetResult(ReadyDependencies());
                    Invoke(window, "PollDependencies");
                    Assert.AreEqual("System requirements met", window.rootVisualElement.Q<Label>("status-message").text);
                }
            );
        }

        private static void WithReadySetup(Action<MCPSetupWindow, FieldInfo> assertion)
        {
            var probe = typeof(MCPSetupWindow).GetField("dependencyCheck", Static);
            object previous = probe.GetValue(null);
            MCPSetupWindow window = null;
            try
            {
                probe.SetValue(null, (Func<bool, Task<DependencyCheckResult>>)(_ => throw new InvalidOperationException("Unexpected real dependency probe.")));
                window = ScriptableObject.CreateInstance<MCPSetupWindow>();
                Invoke(window, "SetDependencyResult", ReadyDependencies());
                window.CreateGUI();
                assertion(window, probe);
            }
            finally
            {
                if (window != null)
                    UnityEngine.Object.DestroyImmediate(window);
                probe.SetValue(null, previous);
            }
        }

        [TestCase("fault")]
        [TestCase("sync")]
        [TestCase("cancelled")]
        [TestCase("empty")]
        [TestCase("null")]
        [TestCase("partial")]
        [TestCase("summary")]
        public void PopulatedSetupRowsClearAfterUnsuccessfulCheck(string outcome)
        {
            WithReadySetup(
                (window, probe) =>
                {
                    var root = window.rootVisualElement;
                    Assert.AreEqual("v3.11", root.Q<Label>("python-version").text);
                    Assert.IsTrue(root.Q("python-indicator").ClassListContains("valid"));
                    var result = ReadyDependencies();
                    if (outcome == "empty")
                        result.Dependencies.Clear();
                    if (outcome == "partial")
                        result.Dependencies.RemoveAt(1);
                    if (outcome == "summary")
                        result.Summary = "Dependency check failed: Probe unavailable; Bearer synthetic-secret";
                    probe.SetValue(
                        null,
                        (Func<bool, Task<DependencyCheckResult>>)(
                            _ =>
                            {
                                if (outcome == "sync")
                                    throw new InvalidOperationException("Probe unavailable; Bearer synthetic-secret");
                                if (outcome == "fault")
                                    return Task.FromException<DependencyCheckResult>(
                                        new InvalidOperationException("Probe unavailable; Bearer synthetic-secret")
                                    );
                                if (outcome == "cancelled")
                                    return Task.FromCanceled<DependencyCheckResult>(new System.Threading.CancellationToken(true));
                                return Task.FromResult(outcome == "null" ? null : result);
                            }
                        )
                    );
                    Invoke(window, "BeginDependencyCheck", true);
                    Invoke(window, "PollDependencies");

                    StringAssert.Contains("Dependency check failed", root.Q<Label>("status-message").text);
                    if (outcome == "fault" || outcome == "sync" || outcome == "summary")
                    {
                        StringAssert.Contains("Probe unavailable", root.Q<Label>("dependency-check-error").text);
                        StringAssert.DoesNotContain("synthetic-secret", root.Q<Label>("dependency-check-error").text);
                    }
                    Assert.AreEqual("Retry", root.Q<Button>("refresh-button").text);
                    Assert.AreEqual("Close", root.Q<Button>("done-button").text);
                    Assert.IsTrue(root.Q<Button>("refresh-button").enabledSelf);
                    foreach (string name in new[] { "python", "uv", "git" })
                    {
                        Assert.AreEqual("Not checked", root.Q<Label>(name + "-version").text);
                        Assert.IsEmpty(root.Q<Label>(name + "-details").text);
                        Assert.IsFalse(root.Q(name + "-indicator").ClassListContains("valid"));
                        Assert.IsFalse(root.Q(name + "-indicator").ClassListContains("invalid"));
                    }
                    Assert.IsFalse(root.Q("installation-section").ClassListContains("visible"));
                    foreach (string action in new[] { "install-uv-button", "open-python-link-button", "open-uv-link-button" })
                        Assert.IsTrue(root.Q(action).ClassListContains("hidden"));
                }
            );
        }

        [TestCase(true)]
        [TestCase(false)]
        public void MissingSetupDependencyShowsOnlyItsRecoveryActions(bool pythonMissing)
        {
            WithReadySetup(
                (window, _) =>
                {
                    var result = ReadyDependencies();
                    result.Dependencies[pythonMissing ? 0 : 1].IsAvailable = false;
                    result.GenerateSummary();
                    Invoke(window, "SetDependencyResult", result);
                    var root = window.rootVisualElement;
                    Assert.AreEqual("Required dependencies missing", root.Q<Label>("status-message").text);
                    Assert.IsTrue(root.Q("installation-section").ClassListContains("visible"));
                    Assert.AreEqual(!pythonMissing, root.Q("open-python-link-button").ClassListContains("hidden"));
                    Assert.AreEqual(pythonMissing, root.Q("open-uv-link-button").ClassListContains("hidden"));
                    if (pythonMissing)
                        Assert.IsTrue(root.Q("install-uv-button").ClassListContains("hidden"));
                    StringAssert.DoesNotContain(pythonMissing ? "uv" : "Python", root.Q<Label>("installation-instructions").text);
                }
            );
        }

        [Test]
        public void StdioOnlySuccessKeepsSwitchVisibleUntilUserFinishes()
        {
            WithReadySetup(
                (window, _) =>
                {
                    var client = new Client { Id = "stdio", SupportedTransports = new[] { ConfiguredTransport.Stdio } };
                    int finished = 0,
                        opened = 0;
                    Set(window, "getClients", (Func<IReadOnlyList<IMcpClientConfigurator>>)(() => new[] { client }));
                    Set(window, "configureClient", (Action<IMcpClientConfigurator>)(_ => { }));
                    Set(window, "finishSetup", (Action)(() => finished++));
                    Set(window, "openConnectionControls", (Action)(() => opened++));
                    Set(window, "useHttpTransport", (Func<bool>)(() => true));
                    Invoke(window, "ShowClientsStep");
                    Invoke(window, "OnConfigureSelectedClicked");
                    Assert.Zero(finished);
                    Assert.IsFalse(window.rootVisualElement.Q<Button>("open-connection-controls-button").ClassListContains("hidden"));
                    Assert.That(window.rootVisualElement.Q("clients-list").Query<Label>().ToList().Any(label => label.text.Contains("Switch to stdio")));
                    Invoke(window, "OnOpenConnectionControlsClicked");
                    Assert.AreEqual(1, opened);
                    Invoke(window, "OnSkipClientsClicked");
                    Assert.AreEqual(1, finished);
                }
            );
        }

        [Test]
        public void MissingLocalTokenShowsConnectionRecoveryAndRetryOnlyConfiguresOutstandingClients()
        {
            string directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "setup-http-" + Guid.NewGuid().ToString("N"));
            string tokenFile = System.IO.Path.Combine(directory, "synthetic-token");
            string previousToken = Environment.GetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN");
            string previousFile = Environment.GetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE");
            System.IO.Directory.CreateDirectory(directory);
            try
            {
                Environment.SetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN", null);
                Environment.SetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", tokenFile);
                WithReadySetup(
                    (window, _) =>
                    {
                        var successful = new Client { Id = "successful" };
                        var http = new SetupHttpClient();
                        var clients = new IMcpClientConfigurator[] { successful, http };
                        var calls = new Dictionary<string, int>();
                        int completed = 0,
                            opened = 0;
                        Set(window, "getClients", (Func<IReadOnlyList<IMcpClientConfigurator>>)(() => clients));
                        Set(window, "finishSetup", (Action)(() => completed++));
                        Set(window, "openConnectionControls", (Action)(() => opened++));
                        Set(
                            window,
                            "configureClient",
                            (Action<IMcpClientConfigurator>)(
                                c =>
                                {
                                    calls[c.Id] = calls.TryGetValue(c.Id, out int count) ? count + 1 : 1;
                                    if (c == http)
                                        http.ValidateSyntheticHeaders(
                                            new HttpEndpointUtility.ConnectionSettings("http://127.0.0.1:1", false, "").GetAuthHeaders()
                                        );
                                }
                            )
                        );
                        Invoke(window, "ShowClientsStep");
                        Invoke(window, "OnConfigureSelectedClicked");
                        var labels = window.rootVisualElement.Q("clients-list").Query<Label>(className: "help-text").ToList();
                        Assert.That(labels.Any(label => label.text.Contains(JsonFileMcpConfigurator.LocalServerRequiredReason)));
                        Assert.That(labels.Any(label => label.text.Contains("Start Server")));
                        Assert.That(labels.All(label => !label.enableRichText));
                        Assert.IsFalse(window.rootVisualElement.Q("open-connection-controls-button").ClassListContains("hidden"));
                        Assert.AreEqual(0, completed);
                        Invoke(window, "OnOpenConnectionControlsClicked");
                        Assert.AreEqual(1, opened);
                        // Simulate server launch by publishing only a synthetic token in our isolated directory.
                        System.IO.File.WriteAllText(tokenFile, "synthetic-private-launch-token");
                        Invoke(window, "OnConfigureSelectedClicked");
                        Assert.AreEqual(1, calls[successful.Id]);
                        Assert.AreEqual(2, calls[http.Id]);
                        Assert.AreEqual(1, completed);
                        Assert.IsTrue(window.rootVisualElement.Q("open-connection-controls-button").ClassListContains("hidden"));
                        Assert.That(window.rootVisualElement.Q("clients-list").Query<Label>().ToList().All(label => !label.text.Contains("synthetic-private")));
                    }
                );
            }
            finally
            {
                Environment.SetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN", previousToken);
                Environment.SetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", previousFile);
                System.IO.Directory.Delete(directory, true);
            }
        }

        [Test]
        public void SetupDoesNotPublishUnrecognizedValidationDetailsOrExceptions()
        {
            WithReadySetup(
                (window, _) =>
                {
                    var http = new SetupHttpClient();
                    const string secret = "<b>synthetic-private-path-and-token</b>";
                    http.PrivateFailure(secret);
                    Set(window, "getClients", (Func<IReadOnlyList<IMcpClientConfigurator>>)(() => new[] { http }));
                    Set(window, "configureClient", (Action<IMcpClientConfigurator>)(_ => { }));
                    Invoke(window, "ShowClientsStep");
                    Invoke(window, "OnConfigureSelectedClicked");
                    Assert.That(window.rootVisualElement.Q("clients-list").Query<Label>().ToList().All(label => !label.text.Contains(secret)));
                    Assert.IsTrue(window.rootVisualElement.Q("open-connection-controls-button").ClassListContains("hidden"));
                    Set(window, "configureClient", (Action<IMcpClientConfigurator>)(_ => throw new InvalidOperationException(secret)));
                    Invoke(window, "OnConfigureSelectedClicked");
                    Assert.That(window.rootVisualElement.Q("clients-list").Query<Label>().ToList().All(label => !label.text.Contains(secret)));
                }
            );
        }

        [Test]
        public void SetupRetryOwnsOneProbeAndReplacesFailureWithFreshReadyRows()
        {
            WithReadySetup(
                (window, probe) =>
                {
                    int checks = 0;
                    var pending = new TaskCompletionSource<DependencyCheckResult>();
                    probe.SetValue(
                        null,
                        (Func<bool, Task<DependencyCheckResult>>)(
                            force =>
                            {
                                Assert.IsTrue(force);
                                checks++;
                                return checks == 1
                                    ? Task.FromException<DependencyCheckResult>(new InvalidOperationException("Probe unavailable"))
                                    : pending.Task;
                            }
                        )
                    );
                    Invoke(window, "OnRefreshClicked");
                    Invoke(window, "PollDependencies");
                    Invoke(window, "OnRefreshClicked");
                    Invoke(window, "OnRefreshClicked");
                    var root = window.rootVisualElement;
                    Assert.AreEqual(2, checks, "Retry must not replace an owned in-flight probe.");
                    Assert.AreEqual("Checking dependencies...", root.Q<Label>("status-message").text);
                    Assert.IsFalse(root.Q<Button>("refresh-button").enabledSelf);
                    Assert.IsFalse(root.Q<Button>("done-button").enabledSelf);
                    Assert.IsTrue(root.Q("dependency-check-error").ClassListContains("hidden"));
                    pending.SetResult(ReadyDependencies());
                    Invoke(window, "PollDependencies");
                    Assert.AreEqual("System requirements met", root.Q<Label>("status-message").text);
                    Assert.AreEqual("Refresh", root.Q<Button>("refresh-button").text);
                    Assert.IsTrue(root.Q<Button>("done-button").enabledSelf);
                    Assert.AreEqual("v3.11", root.Q<Label>("python-version").text);
                }
            );
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FailedSetupRemainsOpenAndRetryDoesNotRepeatSuccess(bool allFail)
        {
            var probe = typeof(MCPSetupWindow).GetField("dependencyCheck", Static);
            object old = probe.GetValue(null);
            MCPSetupWindow window = null;
            try
            {
                probe.SetValue(null, (Func<bool, Task<DependencyCheckResult>>)(_ => Task.FromResult(new DependencyCheckResult())));
                window = ScriptableObject.CreateInstance<MCPSetupWindow>();
                window.CreateGUI();
                Assert.IsNotNull(window.rootVisualElement.Q<Label>("clients-result"), "Failures need a persistent inline result.");
                var clients = new IMcpClientConfigurator[]
                {
                    new Client { Id = "first" },
                    new Client { Id = "second" },
                };
                var calls = new Dictionary<string, int>();
                bool retry = false;
                int completed = 0;
                Set(window, "getClients", (Func<IReadOnlyList<IMcpClientConfigurator>>)(() => clients));
                Set(
                    window,
                    "configureClient",
                    (Action<IMcpClientConfigurator>)(
                        c =>
                        {
                            calls[c.Id] = calls.TryGetValue(c.Id, out int count) ? count + 1 : 1;
                            if (!retry && (allFail || c.Id == "second"))
                                throw new InvalidOperationException("synthetic-private-error");
                        }
                    )
                );
                Set(window, "finishSetup", (Action)(() => completed++));
                Invoke(window, "ShowClientsStep");
                Invoke(window, "OnConfigureSelectedClicked");
                Assert.AreEqual(0, completed);
                StringAssert.Contains("failed", window.rootVisualElement.Q<Label>("clients-result").text);
                StringAssert.DoesNotContain("synthetic-private-error", window.rootVisualElement.Q<Label>("clients-result").text);
                Invoke(window, "ShowClientsStep");
                retry = true;
                Invoke(window, "OnConfigureSelectedClicked");
                Assert.AreEqual(allFail ? 2 : 1, calls["first"]);
                Assert.AreEqual(2, calls["second"]);
                Assert.AreEqual(1, completed);
            }
            finally
            {
                if (window != null)
                    UnityEngine.Object.DestroyImmediate(window);
                probe.SetValue(null, old);
            }
        }

        [TestCase("retryable")]
        [TestCase("uncertain")]
        [TestCase("cancelled")]
        [TestCase("disposed")]
        [TestCase("context-changed")]
        [TestCase("context-changed-retryable-failure")]
        public void PendingSetupConfigurationRemainsResponsiveAndOwnsItsCompletion(string outcome)
        {
            WithReadySetup(
                (window, _) =>
                {
                    var client = new Client { Id = "synthetic-client" };
                    var pending = new TaskCompletionSource<IMcpClientConfigurator>();
                    int calls = 0,
                        completed = 0;
                    System.Threading.CancellationToken capturedToken = default;
                    Set(window, "getClients", (Func<IReadOnlyList<IMcpClientConfigurator>>)(() => new[] { client }));
                    Set(window, "finishSetup", (Action)(() => completed++));
                    Set(
                        window,
                        "configureClientAsync",
                        (Func<IMcpClientConfigurator, System.Threading.CancellationToken, Task<IMcpClientConfigurator>>)(
                            (selected, token) =>
                            {
                                Assert.AreSame(client, selected);
                                capturedToken = token;
                                calls++;
                                return calls == 1 ? pending.Task : Task.FromResult(selected);
                            }
                        )
                    );
                    Invoke(window, "ShowClientsStep");
                    Invoke(window, "OnConfigureSelectedClicked");
                    Invoke(window, "OnConfigureSelectedClicked");
                    Invoke(window, "PollClientConfiguration");
                    var root = window.rootVisualElement;
                    Assert.AreEqual(1, calls, "Pending configuration must not submit twice.");
                    Assert.IsFalse(root.Q<Button>("configure-selected-button").enabledSelf);
                    Assert.IsFalse(root.Q<Button>("skip-clients-button").enabledSelf);
                    StringAssert.Contains("Configuring", root.Q<Label>("clients-result").text);
                    Assert.AreEqual(0, completed);
                    if (outcome == "disposed")
                    {
                        string before = root.Q<Label>("clients-result").text;
                        Invoke(window, "OnDisable");
                        Assert.IsTrue(capturedToken.IsCancellationRequested);
                        pending.SetResult(client);
                        Invoke(window, "PollClientConfiguration");
                        Assert.AreEqual(before, root.Q<Label>("clients-result").text, "A late result must not touch disposed UI.");
                        Invoke(window, "OnEnable");
                        Invoke(window, "ShowClientsStep");
                    }
                    else
                    {
                        if (outcome == "context-changed-retryable-failure")
                        {
                            Set(window, "configurationContextIsCurrent", (Func<bool>)(() => false));
                            pending.SetException(
                                new ClaudeCliMcpConfigurator.ConfigurationFailureException(
                                    "Previous local registrations were restored; retry is available for the captured target.",
                                    true
                                )
                            );
                        }
                        else if (outcome == "context-changed")
                        {
                            Set(window, "configurationContextIsCurrent", (Func<bool>)(() => false));
                            pending.SetResult(client);
                        }
                        else if (outcome == "cancelled")
                            pending.SetCanceled();
                        else if (outcome == "uncertain")
                            pending.SetException(
                                new ClaudeCliMcpConfigurator.ConfigurationFailureException(
                                    "Wait for the CLI to stop, then inspect the selected local registration before retrying.",
                                    false
                                )
                            );
                        else
                            pending.SetException(new InvalidOperationException("synthetic-private-error"));
                        Invoke(window, "PollClientConfiguration");
                    }
                    Assert.IsTrue(root.Q<Button>("skip-clients-button").enabledSelf);
                    StringAssert.DoesNotContain("synthetic-private-error", root.Q<Label>("clients-result").text);
                    Invoke(window, "OnConfigureSelectedClicked");
                    Assert.AreEqual(outcome == "retryable" ? 2 : 1, calls);
                    Assert.AreEqual(outcome == "retryable" ? 1 : 0, completed);
                    if (outcome != "retryable")
                    {
                        Assert.IsFalse(root.Q<Button>("configure-selected-button").enabledSelf);
                        Assert.That(root.Q("clients-list").Q<Label>(className: "help-text").text, Does.Contain("inspect"));
                    }
                }
            );
        }

        [TestCase("matches")]
        [TestCase("changed")]
        [TestCase("missing")]
        public void SetupCompletesOnlyWhenCapturedClaudeRegistrationStillMatches(string snapshot)
        {
            string directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "mcp-setup-final-state-" + Guid.NewGuid().ToString("N"));
            string project = System.IO.Path.Combine(directory, "project");
            string configuration = System.IO.Path.Combine(directory, ".claude.json");
            string identity = configuration + "|" + System.IO.Path.GetFullPath(project);
            System.IO.Directory.CreateDirectory(project);
            try
            {
                var expected = new JObject { ["type"] = "stdio", ["command"] = "synthetic-expected" };
                var current = snapshot == "changed" ? new JObject { ["type"] = "stdio", ["command"] = "synthetic-concurrent" } : expected;
                var document = new JObject
                {
                    ["projects"] = new JObject { [project] = new JObject { ["mcpServers"] = new JObject { ["UnityMCP"] = current } } },
                };
                string written = document.ToString(Formatting.None);
                System.IO.File.WriteAllText(configuration, written);
                WithReadySetup(
                    (window, _) =>
                    {
                        var client = new SetupClaudeClient();
                        var operation = client.CreateOperationClient(configuration);
                        operation.Client.SetStatus(McpStatus.Configured);
                        if (snapshot != "missing")
                        {
                            foreach (
                                var field in new Dictionary<string, string>
                                {
                                    ["pendingOwnershipProject"] = project,
                                    ["pendingOwnershipPath"] = identity,
                                    ["pendingOwnershipSnapshot"] = new JObject { ["UnityMCP"] = expected }.ToString(Formatting.None),
                                    ["pendingOwnershipEntry"] = expected.ToString(Formatting.None),
                                }
                            )
                                typeof(ClaudeCliMcpConfigurator).GetField(field.Key, Instance).SetValue(operation, field.Value);
                        }
                        int completed = 0;
                        var pending = new TaskCompletionSource<IMcpClientConfigurator>();
                        Set(window, "getClients", (Func<IReadOnlyList<IMcpClientConfigurator>>)(() => new[] { client }));
                        Set(window, "finishSetup", (Action)(() => completed++));
                        Set(
                            window,
                            "configureClientAsync",
                            (Func<IMcpClientConfigurator, System.Threading.CancellationToken, Task<IMcpClientConfigurator>>)((_, token) => pending.Task)
                        );
                        Invoke(window, "ShowClientsStep");
                        Invoke(window, "OnConfigureSelectedClicked");
                        pending.SetResult(operation);
                        Invoke(window, "PollClientConfiguration");
                        Assert.AreEqual(snapshot == "matches" ? 1 : 0, completed, "An earlier Configured status does not verify the final registration.");
                        Assert.IsFalse(window.rootVisualElement.Q<Button>("configure-selected-button").enabledSelf);
                        if (snapshot != "matches")
                        {
                            StringAssert.Contains("0 configured, 1 failed", window.rootVisualElement.Q<Label>("clients-result").text);
                            Assert.That(window.rootVisualElement.Q("clients-list").Q<Label>(className: "help-text").text, Does.Contain("inspect"));
                        }
                        Assert.AreEqual(written, System.IO.File.ReadAllText(configuration), "Final verification preserves a concurrent registration.");
                        Assert.AreEqual(snapshot == "matches", GeneratedClientConfigOwnership.Receipt("Claude", identity) != null);
                    }
                );
            }
            finally
            {
                GeneratedClientConfigOwnership.RestoreReceipt("Claude", identity, null);
                System.IO.Directory.Delete(directory, true);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void InvalidSavedSetupRemainsRetryableWithoutRepeatingValidatedPeers(bool allInvalid)
        {
            WithReadySetup(
                (window, _) =>
                {
                    var first = new Client { Id = "first" };
                    var second = new Client { Id = "second" };
                    var clients = new IMcpClientConfigurator[] { first, second };
                    var calls = new Dictionary<string, int>();
                    bool retry = false;
                    int completed = 0;
                    Set(window, "getClients", (Func<IReadOnlyList<IMcpClientConfigurator>>)(() => clients));
                    Set(
                        window,
                        "configureClient",
                        (Action<IMcpClientConfigurator>)(
                            client =>
                            {
                                calls[client.Id] = calls.TryGetValue(client.Id, out int count) ? count + 1 : 1;
                                ((Client)client).Status = !retry && (allInvalid || client.Id == "second") ? McpStatus.IncorrectPath : McpStatus.Configured;
                            }
                        )
                    );
                    Set(window, "finishSetup", (Action)(() => completed++));
                    Invoke(window, "ShowClientsStep");
                    Invoke(window, "OnConfigureSelectedClicked");

                    Assert.Zero(completed, "Saving an invalid entry must not complete setup.");
                    var root = window.rootVisualElement;
                    StringAssert.Contains(allInvalid ? "0 configured, 2 failed" : "1 configured, 1 failed", root.Q<Label>("clients-result").text);
                    Assert.IsTrue(root.Q<Button>("configure-selected-button").enabledSelf);
                    var failedRow = root.Query<Toggle>().ToList().Find(toggle => toggle.label == "second");
                    Assert.IsTrue(failedRow.value);
                    Assert.IsTrue(failedRow.enabledSelf);
                    Assert.IsTrue(root.Query<Label>().ToList().Exists(label => label.text.Contains("validation") && label.text.Contains("retry")));

                    Invoke(window, "ShowClientsStep");
                    retry = true;
                    Invoke(window, "OnConfigureSelectedClicked");
                    Assert.AreEqual(allInvalid ? 2 : 1, calls["first"]);
                    Assert.AreEqual(2, calls["second"]);
                    Assert.AreEqual(1, completed);
                }
            );
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ManualOnlyClientShowsInstructionsAndCannotBeAutomaticallyConfigured(bool includeAutomatic)
        {
            WithReadySetup(
                (window, _) =>
                {
                    var manual = new Client { Id = "manual-only", SupportsAutoConfigure = false };
                    var clients = new List<IMcpClientConfigurator> { manual };
                    if (includeAutomatic)
                        clients.Add(new Client { Id = "automatic" });
                    var configured = new List<string>();
                    Set(window, "getClients", (Func<IReadOnlyList<IMcpClientConfigurator>>)(() => clients));
                    Set(window, "configureClient", (Action<IMcpClientConfigurator>)(c => configured.Add(c.Id)));
                    Set(window, "finishSetup", (Action)(() => { }));
                    Invoke(window, "ShowClientsStep");
                    var row = window.rootVisualElement.Query<Toggle>().ToList().Find(toggle => toggle.label == manual.Id);
                    Assert.IsNotNull(row);
                    Assert.IsFalse(row.enabledSelf);
                    Assert.IsFalse(row.value);
                    var instructions = window.rootVisualElement.Query<Label>().ToList().Find(label => label.text.Contains("paste the manual configuration"));
                    Assert.IsNotNull(instructions, "Detected manual clients must expose their installation steps.");
                    StringAssert.DoesNotContain("retry", instructions.text.ToLowerInvariant());
                    // Even a stale/programmatic selection must never bypass the capability check.
                    row.SetValueWithoutNotify(true);
                    Invoke(window, "OnConfigureSelectedClicked");
                    CollectionAssert.AreEqual(includeAutomatic ? new[] { "automatic" } : Array.Empty<string>(), configured);
                    Assert.IsFalse(
                        window.rootVisualElement.Q<Button>("configure-selected-button").enabledSelf,
                        "Completed and manual-only clients cannot be submitted again."
                    );
                }
            );
        }

        [Test]
        public void CancelledPreviewClearsPreviousClientStatus()
        {
            var section = (McpClientConfigSection)FormatterServices.GetUninitializedObject(typeof(McpClientConfigSection));
            var status = new Label("previous client ready");
            Set(section, "configurationChangeStatusLabel", status);
            var preview = new TextField { value = "previous" };
            Set(section, "configurationPreviewText", preview);
            var apply = new Button();
            Set(section, "applyConfigurationButton", apply);
            Invoke(section, "OnCancelConfigurationPreviewClicked");
            Assert.AreEqual("Choose Preview change to see what will be saved.", status.text);
            StringAssert.DoesNotContain("previous client", status.text);
            Assert.IsEmpty(preview.value);
            Assert.IsFalse(apply.enabledSelf);
        }

        [TestCase("stale_file", "Preview again")]
        [TestCase("locked", "unlock")]
        [TestCase("applied", "Saved")]
        [TestCase("restored", "Restored")]
        public void ClientResultExplainsAffectedClientAndRecovery(string code, string expected)
        {
            var method = typeof(McpClientConfigSection).GetMethod("ConfigurationStatusMessage", Static);
            Assert.IsNotNull(method);
            string message = (string)method.Invoke(null, new object[] { code, "Synthetic Client" });
            StringAssert.Contains("Synthetic Client", message);
            StringAssert.Contains(expected, message);
            StringAssert.DoesNotContain(code + ":", message);
        }

        [UnityTest]
        public IEnumerator LocalConnectionBlockerFocusesItsFieldAndClearsAfterCorrection()
        {
            var fields = new[] { "_bridgeService", "_serverManagementService" };
            var previousServices = fields.ToDictionary(n => n, n => typeof(MCPServiceLocator).GetField(n, Static).GetValue(null));
            var keys = new[] { EditorPrefKeys.HttpBaseUrl, EditorPrefKeys.HttpTransportScope };
            var prefs = keys.ToDictionary(k => k, k => (exists: EditorPrefs.HasKey(k), value: EditorPrefs.GetString(k)));
            bool hadHttp = EditorPrefs.HasKey(EditorPrefKeys.UseHttpTransport);
            bool http = EditorPrefs.GetBool(EditorPrefKeys.UseHttpTransport);
            bool pinned = SessionState.GetBool("MCPForUnity.ForceStdioForSession", false);
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            try
            {
                var fixture = typeof(MCPForUnityTests.Editor.Windows.McpConnectionSectionInteractionTests);
                object bridge = Activator.CreateInstance(fixture.GetNestedType("FakeBridge", BindingFlags.NonPublic), true);
                var serverType = fixture.GetNestedType("FakeServer", BindingFlags.NonPublic);
                object server = Activator.CreateInstance(serverType, true);
                serverType.GetField("Reachable").SetValue(server, true);
                typeof(MCPServiceLocator).GetField("_bridgeService", Static).SetValue(null, bridge);
                typeof(MCPServiceLocator).GetField("_serverManagementService", Static).SetValue(null, server);
                EditorConfigurationCache.Instance.UnpinStdioForSession();
                EditorConfigurationCache.Instance.SetUseHttpTransport(true);
                EditorConfigurationCache.Instance.SetHttpTransportScope("local");
                EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, "http://not-local.example:8080");
                window.ShowUtility();
                var root = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/Connection/McpConnectionSection.uxml").CloneTree();
                window.rootVisualElement.Add(root);
                var section = (McpConnectionSection)FormatterServices.GetUninitializedObject(typeof(McpConnectionSection));
                Set(section, "<Root>k__BackingField", root);
                Invoke(section, "CacheUIElements");
                var protocol = typeof(McpConnectionSection).GetNestedType("TransportProtocol", BindingFlags.NonPublic);
                root.Q<EnumField>("transport-dropdown").Init((Enum)Enum.Parse(protocol, "HTTPLocal"));
                section.UpdateConnectionStatus();
                var blocker = root.Q<Label>("connection-blocker");
                Assert.IsNotEmpty(blocker.text);
                Assert.IsFalse(root.Q<Button>("connection-toggle").enabledSelf);
                var action = root.Q<Button>("connection-fix");
                Assert.IsFalse(action.ClassListContains("hidden"));
                action.Focus();
                yield return null;
                using (var submit = NavigationSubmitEvent.GetPooled())
                    action.SendEvent(submit);
                yield return null;
                var target = root.Q<TextField>("http-url");
                var focus = target.focusController.focusedElement as VisualElement;
                Assert.IsTrue(focus == target || focus != null && target.Contains(focus));
                EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, "http://127.0.0.1:8080");
                section.UpdateConnectionStatus();
                Assert.IsEmpty(blocker.text);
                Assert.IsTrue(action.ClassListContains("hidden"));
                Assert.IsTrue(root.Q<Button>("connection-toggle").enabledSelf);
            }
            finally
            {
                window.Close();
                EditorConfigurationCache.Instance.SetUseHttpTransport(http);
                if (!hadHttp)
                    EditorPrefs.DeleteKey(EditorPrefKeys.UseHttpTransport);
                foreach (var pref in prefs)
                    if (pref.Value.exists)
                        EditorPrefs.SetString(pref.Key, pref.Value.value);
                    else
                        EditorPrefs.DeleteKey(pref.Key);
                if (pinned)
                    EditorConfigurationCache.Instance.PinStdioForSession();
                EditorConfigurationCache.Instance.Refresh();
                foreach (var previous in previousServices)
                    typeof(MCPServiceLocator).GetField(previous.Key, Static).SetValue(null, previous.Value);
            }
        }

        private sealed class Store : ISecureKeyStore
        {
            public int Writes;

            public bool Has(string id) => Writes > 0;

            public bool TryGet(string id, out string key)
            {
                key = null;
                throw new InvalidOperationException("No key read");
            }

            public void Set(string id, string key)
            {
                Assert.AreEqual("synthetic-key", key);
                Writes++;
            }

            public void Delete(string id) => Assert.Fail("No deletion");
        }

        [UnityTest]
        public IEnumerator ProviderCredentialsUseExplicitSaveAndSharedRoutes()
        {
            var slot = typeof(SecureKeyStore).GetField("_current", Static);
            var previous = (ISecureKeyStore)slot.GetValue(null);
            var store = new Store();
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            var transport = new FakeHttpTransport { Handler = _ => throw new InvalidOperationException("No network") };
            try
            {
                SecureKeyStore.OverrideForTests(store);
                AssetGenModelCatalog.ResetForTests(true);
                FalModelCatalog.TransportOverrideForTests = transport;
                OpenRouterModelCatalog.TransportOverrideForTests = transport;
                window.ShowUtility();
                var root = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/AssetGen/McpAssetGenSection.uxml").CloneTree();
                root.Q("blender-bridge-panel").RemoveFromHierarchy();
                window.rootVisualElement.Add(root);
                var section = new McpAssetGenSection(root);
                var credentials = root.Q<Foldout>("provider-credentials");
                Assert.IsNotNull(credentials, "Credentials must have their own visible group.");
                var key = root.Q<TextField>("provider-key-tripo");
                Assert.AreEqual("API key", key.label);
                key.value = "synthetic-key";
                key.Focus();
                yield return null;
                var save = root.Q<Button>("provider-save-tripo");
                save.Focus();
                yield return null;
                Assert.AreEqual(0, store.Writes, "Leaving the field is not a Save action.");
                section.Refresh();
                Assert.AreSame(key, root.Q<TextField>("provider-key-tripo"));
                Assert.AreEqual("synthetic-key", key.value, "Tab refresh keeps the unsaved field.");
                using (var submit = NavigationSubmitEvent.GetPooled())
                    save.SendEvent(submit);
                yield return null;
                Assert.AreEqual(1, store.Writes);
                Assert.IsEmpty(key.value);
                credentials.value = false;
                var route = root.Q<Button>("provider-settings-audio-fal");
                Assert.IsNotNull(route);
                var sound = route.GetFirstAncestorOfType<Foldout>();
                Assert.IsNotNull(sound);
                Assert.AreEqual("Sound (fal.ai)", sound.text);
                sound.value = true;
                yield return null;
                route.Focus();
                yield return null;
                using (var submit = NavigationSubmitEvent.GetPooled())
                    route.SendEvent(submit);
                yield return null;
                Assert.IsTrue(credentials.value);
                var falKey = root.Q<TextField>("provider-key-fal");
                var focused = falKey.focusController.focusedElement as VisualElement;
                Assert.IsTrue(focused == falKey || (focused != null && falKey.Contains(focused)), "The route must focus the shared key field.");
                key.value = "synthetic-key";
                falKey.value = "synthetic-key";
                var saveFal = root.Q<Button>("provider-save-fal");
                saveFal.Focus();
                yield return null;
                using (var submit = NavigationSubmitEvent.GetPooled())
                    saveFal.SendEvent(submit);
                yield return null;
                Assert.AreEqual("synthetic-key", key.value, "Saving the shared key must not destroy another provider draft.");
                Assert.IsEmpty(transport.RecordedRequests);
            }
            finally
            {
                window.Close();
                AssetGenModelCatalog.ResetForTests(true);
                SecureKeyStore.OverrideForTests(previous);
            }
        }

        [UnityTest, Explicit("Controller-owned provider and recovery layout evidence.")]
        public IEnumerator NarrowProviderCredentialsRemainVisible()
        {
            var skinIndex = typeof(EditorGUIUtility).GetProperty("skinIndex", Static);
            object originalSkin = skinIndex.GetValue(null);
            try
            {
                foreach (int skin in new[] { 1, 0 })
                {
                    skinIndex.SetValue(null, skin);
                    UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                    for (int i = 0; i < 12; i++)
                        yield return null;
                    Assert.AreEqual(skin == 1, EditorGUIUtility.isProSkin);
                    yield return CaptureProviderCredentials();
                }
            }
            finally
            {
                skinIndex.SetValue(null, originalSkin);
                UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            }
        }

        private IEnumerator CaptureProviderCredentials()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var slot = typeof(SecureKeyStore).GetField("_current", Static);
            var previous = (ISecureKeyStore)slot.GetValue(null);
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            McpWindowNavigation navigation = null;
            try
            {
                SecureKeyStore.OverrideForTests(new Store());
                AssetGenModelCatalog.ResetForTests(true);
                var transport = new FakeHttpTransport { Handler = _ => throw new InvalidOperationException("No network") };
                FalModelCatalog.TransportOverrideForTests = transport;
                OpenRouterModelCatalog.TransportOverrideForTests = transport;
                window.ShowUtility();
                window.position = new Rect(30, 30, 500, 600);
                var root = window.rootVisualElement;
                root.AddToClassList("mcp-editor");
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "MCPForUnityEditorWindow.uxml").CloneTree(root);
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/AssetGen/McpAssetGenSection.uxml").CloneTree(root.Q("assetgen-container"));
                root.Q("blender-bridge-panel").RemoveFromHierarchy();
                var section = new McpAssetGenSection(root.Q("assetgen-container"));
                root.Q("clients-panel").AddToClassList("hidden");
                root.Q("assetgen-panel").RemoveFromClassList("hidden");
                navigation = new McpWindowNavigation(root, _ => { });
                navigation.Select("assetgen");
                string directory = System.IO.Path.GetFullPath(
                    System.IO.Path.Combine(Application.dataPath, "../Library/McpTestEvidence/setup-recovery-" + Guid.NewGuid().ToString("N"))
                );
                System.IO.Directory.CreateDirectory(directory);
                foreach (string theme in new[] { EditorGUIUtility.isProSkin ? "dark" : "light" })
                {
                    root.EnableInClassList("unity-theme-dark", theme == "dark");
                    root.EnableInClassList("unity-theme-light", theme == "light");
                    for (int i = 0; i < 12; i++)
                    {
                        window.Repaint();
                        yield return null;
                    }
                    section.FocusProviderCredentials("fal");
                    for (int i = 0; i < 12; i++)
                    {
                        window.Repaint();
                        yield return null;
                    }
                    var scroll = root.Q<ScrollView>("assetgen-panel");
                    var key = root.Q<TextField>("provider-key-fal");
                    Assert.IsTrue(key.worldBound.Overlaps(scroll.contentViewport.worldBound));
                    Assert.GreaterOrEqual(key.worldBound.width, key.parent.worldBound.width * 0.9f);
                    Assert.GreaterOrEqual(root.Q<Button>("provider-save-fal").worldBound.yMin, key.worldBound.yMax);
                    Assert.LessOrEqual(root.Q<Button>("provider-save-fal").worldBound.yMax, scroll.contentViewport.worldBound.yMax);
                    Assert.LessOrEqual(scroll.horizontalScroller.highValue, 1);
                    string path = System.IO.Path.Combine(directory, "credentials-500-" + theme + ".png");
                    typeof(OperationActivityTests).GetMethod("Capture", Static).Invoke(null, new object[] { window, path });
                    TestContext.Out.WriteLine("SETUP_RECOVERY_CAPTURE " + path);
                }
            }
            finally
            {
                navigation?.Dispose();
                window.Close();
                AssetGenModelCatalog.ResetForTests(true);
                SecureKeyStore.OverrideForTests(previous);
            }
        }

        [Test]
        public void BundledModelRefreshFollowsSharedSelectionWithoutReplacingCredentials()
        {
            var slot = typeof(SecureKeyStore).GetField("_current", Static);
            var previous = (ISecureKeyStore)slot.GetValue(null);
            string originalModel = AssetGenPrefs.GetSelectedModel("model", "tripo");
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            try
            {
                SecureKeyStore.OverrideForTests(new Store());
                AssetGenModelCatalog.ResetForTests(true);
                var transport = new FakeHttpTransport { Handler = _ => throw new InvalidOperationException("No network") };
                FalModelCatalog.TransportOverrideForTests = transport;
                OpenRouterModelCatalog.TransportOverrideForTests = transport;
                AssetGenPrefs.SetSelectedModel("model", "tripo", AssetGenModelCatalog.ForProvider("tripo", "model").First().Id);
                window.ShowUtility();
                var root = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/AssetGen/McpAssetGenSection.uxml").CloneTree();
                root.Q("blender-bridge-panel").RemoveFromHierarchy();
                window.rootVisualElement.Add(root);
                var section = new McpAssetGenSection(root);
                var key = root.Q<TextField>("provider-key-tripo");
                key.value = "synthetic-key";
                AssetGenPrefs.SetSelectedModel("model", "tripo", "external-synthetic-model");
                section.Refresh();
                StringAssert.Contains("Saved model unavailable: external-synthetic-model", root.Q<Label>("model-current-model-tripo").text);
                Assert.AreEqual("external-synthetic-model", AssetGenPrefs.GetSelectedModel("model", "tripo"));
                Assert.AreEqual("Choose a matching model", root.Q("provider-model-model-tripo").Q<DropdownField>().value);
                Assert.AreSame(key, root.Q<TextField>("provider-key-tripo"));
                Assert.AreEqual("synthetic-key", key.value);
                Assert.IsEmpty(transport.RecordedRequests);
            }
            finally
            {
                window.Close();
                AssetGenPrefs.SetSelectedModel("model", "tripo", originalModel);
                AssetGenModelCatalog.ResetForTests(true);
                SecureKeyStore.OverrideForTests(previous);
            }
        }

        [Test]
        public void ScopeLabelsDistinguishUserDefaultsFromProjectDeployment()
        {
            var root = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/Advanced/McpAdvancedSection.uxml").CloneTree();
            Assert.That(root.Q<Label>("settings-user-scope")?.text, Does.Contain("User"));
            Assert.That(root.Q<Label>("settings-project-scope")?.text, Does.Contain("Project"));
            var gen = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/AssetGen/McpAssetGenSection.uxml").CloneTree();
            Assert.That(gen.Q<Label>("generation-scope")?.text, Does.Contain("User"));
            Assert.AreEqual("Host", gen.Q<TextField>("blender-host").label);
            Assert.AreEqual("Port", gen.Q<TextField>("blender-port").label);
        }
    }
}
