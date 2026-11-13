using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Dependencies.Models;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Windows;
using MCPForUnity.Editor.Windows.Components.Activity;
using MCPForUnity.Editor.Windows.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class AuxiliaryWindowLayoutTests
    {
        private const BindingFlags Instance = BindingFlags.Instance | BindingFlags.NonPublic;
        private const BindingFlags Static = BindingFlags.Static | BindingFlags.NonPublic;

        private sealed class SyntheticClient : JsonFileMcpConfigurator
        {
            internal SyntheticClient(int index)
                : base(new McpClient { name = "Synthetic client " + index }) { }

            public override bool IsInstalled => true;

            public override string GetConfigPath() => "synthetic-config";

            public override void Configure() => Assert.Fail("Layout checks must not configure a client.");
        }

        private static void OwnedRun() => Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));

        private static IEnumerator Settle(EditorWindow window)
        {
            for (int frame = 0; frame < 12; frame++)
            {
                window.Repaint();
                yield return null;
            }
            Assert.IsNotNull(window.rootVisualElement.panel);
            Assert.Greater(window.rootVisualElement.worldBound.width, 0);
            Assert.Greater(window.rootVisualElement.worldBound.height, 0);
        }

        private static void WindowBounds(EditorWindow window, float width, float height)
        {
            TestContext.WriteLine($"AUX_WINDOW {window.GetType().Name} position={window.position} root={window.rootVisualElement.worldBound}");
            Assert.AreEqual(width, window.position.width, 1, "The native window must actually use the requested test width.");
            Assert.AreEqual(height, window.position.height, 1, "The native window must actually use the requested test height.");
        }

        private static void Click(VisualElement root, string name)
        {
            var button = root.Q<Button>(name);
            Assert.IsNotNull(button, name);
            Assert.IsTrue(button.enabledInHierarchy, name);
            typeof(Clickable).GetMethod("Invoke", Instance).Invoke(button.clickable, new object[] { null });
        }

        private static void Measure(string state, ScrollView scroll, VisualElement target)
        {
            TestContext.WriteLine(
                $"AUX_LAYOUT {state} viewport={scroll.contentViewport.worldBound} content={scroll.contentContainer.worldBound} "
                    + $"target={target.worldBound} offset={scroll.scrollOffset}"
            );
            Assert.Greater(scroll.contentViewport.worldBound.width, 0, state);
            Assert.Greater(scroll.contentViewport.worldBound.height, 0, state);
            Assert.Greater(target.worldBound.width, 0, state);
            Assert.Greater(target.worldBound.height, 0, state);
        }

        [UnityTest, Explicit("Owned attached-panel setup navigation with synthetic dependencies and clients.")]
        public IEnumerator SetupStepNavigationStartsAtTopAfterLongContent()
        {
            OwnedRun();
            var probe = typeof(MCPSetupWindow).GetField("dependencyCheck", Static);
            object previous = probe.GetValue(null);
            var result = new DependencyCheckResult { IsSystemReady = true };
            foreach (string name in new[] { "Python", "uv Package Manager", "Git" })
                result.Dependencies.Add(
                    new DependencyStatus(name)
                    {
                        IsAvailable = true,
                        Version = "1.0",
                        Details = string.Join("\n", Enumerable.Repeat("Synthetic dependency detail", 20)),
                    }
                );
            MCPSetupWindow window = null;
            try
            {
                probe.SetValue(null, (Func<bool, Task<DependencyCheckResult>>)(_ => Task.FromResult(result)));
                window = ScriptableObject.CreateInstance<MCPSetupWindow>();
                typeof(MCPSetupWindow).GetMethod("SetDependencyResult", Instance).Invoke(window, new object[] { result });
                var clients = Enumerable.Range(0, 32).Select(index => (IMcpClientConfigurator)new SyntheticClient(index)).ToArray();
                typeof(MCPSetupWindow).GetField("getClients", Instance).SetValue(window, (Func<IReadOnlyList<IMcpClientConfigurator>>)(() => clients));
                window.minSize = new Vector2(480, 320);
                window.ShowUtility();
                window.position = new Rect(30, 30, 480, 320);
                yield return Settle(window);
                WindowBounds(window, 480, 320);
                var root = window.rootVisualElement;
                var scroll = root.Q<ScrollView>("root-container");
                scroll.ScrollTo(root.Q("done-button"));
                yield return Settle(window);
                Assert.Greater(scroll.scrollOffset.y, 100, "The dependency page must really be scrolled before navigating.");
                Click(root, "done-button");
                yield return Settle(window);
                Measure("setup-next", scroll, root.Q("clients-list"));
                Assert.LessOrEqual(scroll.scrollOffset.y, 1, "Next must reveal the first client rather than retain the dependency scroll offset.");
                Assert.IsTrue(root.Q("clients-list").worldBound.Overlaps(scroll.contentViewport.worldBound));
                scroll.ScrollTo(root.Q("back-button"));
                yield return Settle(window);
                Assert.Greater(scroll.scrollOffset.y, 100);
                Click(root, "back-button");
                yield return Settle(window);
                Measure("setup-back", scroll, root.Q("python-version"));
                Assert.LessOrEqual(scroll.scrollOffset.y, 1, "Back must reveal the dependency heading.");
                Assert.IsTrue(root.Q("python-version").worldBound.Overlaps(scroll.contentViewport.worldBound));
            }
            finally
            {
                if (window != null)
                    UnityEngine.Object.DestroyImmediate(window);
                probe.SetValue(null, previous);
            }
        }

        [UnityTest, Explicit("Owned evidence window with long operation names and raw evidence.")]
        public IEnumerator EvidenceLongHeadingKeepsRawViewportAndActionsVisible()
        {
            OwnedRun();
            var window = ScriptableObject.CreateInstance<OperationEvidenceWindow>();
            try
            {
                window.SetOperation(
                    new OperationActivityRow
                    {
                        Owner = "synthetic",
                        Id = new string('a', 32),
                        Operation = string.Join(" ", Enumerable.Repeat("Long operation name", 40)),
                        Detail = "Synthetic summary",
                        State = "completed",
                    }
                );
                window.resolve = _ => string.Join("\n", Enumerable.Range(0, 120).Select(index => "Synthetic evidence line " + index));
                window.minSize = new Vector2(360, 180);
                window.ShowUtility();
                window.position = new Rect(30, 30, 360, 180);
                yield return Settle(window);
                WindowBounds(window, 360, 180);
                var root = window.rootVisualElement;
                var heading = root.Q<Label>("operation-evidence-heading");
                var scroll = root.Q<ScrollView>();
                Measure("evidence-long-heading", scroll, heading);
                Assert.LessOrEqual(heading.worldBound.height, 28, "A long operation name must not consume the evidence viewport.");
                Assert.AreEqual(heading.text, heading.tooltip, "The full operation identity remains available.");
                Assert.IsTrue(root.ClassListContains("mcp-editor"));
                Assert.IsTrue(root.ClassListContains(EditorGUIUtility.isProSkin ? "unity-theme-dark" : "unity-theme-light"));
                Click(root, "operation-evidence-raw-tab");
                yield return Settle(window);
                var raw = root.Q<TextField>("operation-evidence-text");
                Measure("evidence-raw", scroll, raw);
                Assert.GreaterOrEqual(scroll.contentViewport.worldBound.height, 40);
                Assert.IsTrue(root.Q("operation-evidence-copy").worldBound.Overlaps(root.worldBound));
                Assert.Greater(scroll.contentContainer.worldBound.height, scroll.contentViewport.worldBound.height);
                scroll.scrollOffset = new Vector2(0, scroll.verticalScroller.highValue);
                yield return Settle(window);
                Assert.Greater(scroll.scrollOffset.y, 0, "Long evidence remains scrollable.");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(window);
            }
        }

        [UnityTest, Explicit("Owned diagnostic and preference windows: normal, narrow, generated and empty states.")]
        public IEnumerator DiagnosticAndPreferenceContentRemainsReachable()
        {
            OwnedRun();
            var type = typeof(MCPForUnityEditorWindow).Assembly.GetType("MCPForUnity.Editor.Windows.McpDiagnosticWindow");
            var diagnostic = (EditorWindow)ScriptableObject.CreateInstance(type);
            EditorPrefsWindow preferences = null;
            try
            {
                type.GetMethod("Configure", Instance)
                    .Invoke(
                        diagnostic,
                        new object[]
                        {
                            (Func<int, JObject>)(
                                _ => new JObject
                                {
                                    ["versions"] = new JObject { ["unity"] = "6000.0.69f1", ["package"] = "11.16.0" },
                                }
                            ),
                        }
                    );
                diagnostic.minSize = new Vector2(360, 320);
                diagnostic.ShowUtility();
                diagnostic.position = new Rect(30, 30, 360, 320);
                yield return Settle(diagnostic);
                WindowBounds(diagnostic, 360, 320);
                var root = diagnostic.rootVisualElement;
                var scroll = root.Q<ScrollView>("diagnostics-body");
                Measure("diagnostic-empty", scroll, root.Q("diagnostics-generate"));
                Click(root, "diagnostics-generate");
                yield return Settle(diagnostic);
                var preview = root.Q<TextField>("diagnostics-preview");
                scroll.ScrollTo(preview);
                yield return Settle(diagnostic);
                Measure("diagnostic-generated", scroll, preview);
                Assert.IsTrue(preview.worldBound.Overlaps(scroll.contentViewport.worldBound));
                Assert.IsNotEmpty(preview.value);
                Assert.IsTrue(root.Q<Button>("diagnostics-copy").enabledInHierarchy);
                preferences = ScriptableObject.CreateInstance<EditorPrefsWindow>();
                preferences.minSize = new Vector2(600, 400);
                preferences.ShowUtility();
                preferences.position = new Rect(30, 30, 600, 400);
                yield return Settle(preferences);
                WindowBounds(preferences, 600, 400);
                var prefRoot = preferences.rootVisualElement;
                var prefScroll = prefRoot.Q<ScrollView>("scroll-view");
                Measure("preferences-populated", prefScroll, prefRoot.Q("prefs-container"));
                prefRoot.Q<TextField>("search-field").value = "synthetic-no-matching-preference-9ddab501";
                yield return Settle(preferences);
                var empty = prefRoot.Q<Label>("empty-state");
                Measure("preferences-empty", prefScroll, empty);
                Assert.AreEqual(DisplayStyle.Flex, empty.resolvedStyle.display);
                Assert.IsTrue(empty.worldBound.Overlaps(prefScroll.contentViewport.worldBound));
                Assert.IsTrue(prefRoot.Q("refresh-button").worldBound.Overlaps(prefRoot.worldBound));
            }
            finally
            {
                if (preferences != null)
                    UnityEngine.Object.DestroyImmediate(preferences);
                UnityEngine.Object.DestroyImmediate(diagnostic);
            }
        }

        [UnityTest, Explicit("Owned Play Scenario window through normal workflow navigation without running or saving.")]
        public IEnumerator PlayScenarioWorkflowKeepsContentViewportAtNarrowAndNormalSizes()
        {
            OwnedRun();
            string directory = Path.Combine(Path.GetTempPath(), "mcp-aux-layout-" + Guid.NewGuid().ToString("N"));
            string previousJob = SessionState.GetString(PlayScenarioWindow.SessionJobKey, "");
            bool previousSuite = SessionState.GetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
            PlayScenarioWindow window = null;
            try
            {
                SessionState.SetString(PlayScenarioWindow.SessionJobKey, "");
                SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
                window = ScriptableObject.CreateInstance<PlayScenarioWindow>();
                window.ConfigureForTests(new PlayScenarioStore(directory));
                window.ShowDialog = (_, __, ___, ____, _____) => 1;
                window.ShowUtility();
                window.position = new Rect(30, 30, 500, 340);
                yield return Settle(window);
                WindowBounds(window, 500, 340);
                var root = window.rootVisualElement;
                Click(root, "newScenario");
                yield return Settle(window);
                var name = root.Q<TextField>("scenarioName");
                name.value = "synthetic-layout-draft";
                foreach (var size in new[] { new Vector2(500, 340), new Vector2(1100, 700), new Vector2(500, 340) })
                {
                    window.position = new Rect(30, 30, size.x, size.y);
                    yield return Settle(window);
                    WindowBounds(window, size.x, size.y);
                    foreach (string pane in new[] { "edit", "run", "results", "library" })
                    {
                        Click(root, "scenario-tab-" + pane);
                        yield return Settle(window);
                        string scrollName =
                            pane == "edit" ? "scenarioEditorScroll"
                            : pane == "run" ? "scenarioRunScroll"
                            : pane == "results" ? "runResults"
                            : "savedScenarios";
                        var scroll = root.Q<ScrollView>(scrollName);
                        Measure($"scenario-{pane}-{size.x}-{size.y}", scroll, scroll);
                        Assert.GreaterOrEqual(scroll.contentViewport.worldBound.height, 140);
                        Assert.IsTrue(scroll.worldBound.Overlaps(root.worldBound));
                        Assert.AreSame(name, root.Q<TextField>("scenarioName"), "Resizing and navigating must retain the same draft field.");
                    }
                }
            }
            finally
            {
                if (window != null)
                {
                    typeof(EditorWindow).GetProperty("hasUnsavedChanges").SetValue(window, false);
                    UnityEngine.Object.DestroyImmediate(window);
                }
                SessionState.SetString(PlayScenarioWindow.SessionJobKey, previousJob);
                SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, previousSuite);
                if (Directory.Exists(directory))
                    Directory.Delete(directory, true);
            }
        }
    }
}
