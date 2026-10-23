using System;
using System.Collections;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Windows;
using MCPForUnity.Editor.Windows.Components.ReferenceGraph;
using MCPForUnity.Editor.Windows.PlayScenarios;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class ResponsiveWindowLayoutTests
    {
        private const string Base = "Packages/com.ykh09242.unity-mcp/Editor/Windows/";
        private const BindingFlags Static = BindingFlags.NonPublic | BindingFlags.Static;

        private sealed class Host : EditorWindow { }

        private static IEnumerator Frames(EditorWindow window)
        {
            for (int i = 0; i < 12; i++)
            {
                window.Repaint();
                yield return null;
            }
        }

        private static string EvidenceDirectory()
        {
            string p = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/responsive-" + Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(p);
            return p;
        }

        private static void Capture(EditorWindow window, string directory, string file)
        {
            string path = Path.Combine(directory, file + ".png");
            typeof(OperationActivityTests).GetMethod("Capture", Static).Invoke(null, new object[] { window, path });
            TestContext.Out.WriteLine("RESPONSIVE_CAPTURE " + path);
        }

        [UnityTest, Explicit("Actual compact-height main shell in both native themes.")]
        public IEnumerator MainShellReclaimsShortViewportWithoutHidingPrimaryGraphAction()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var skin = typeof(EditorGUIUtility).GetProperty("skinIndex", Static);
            var original = skin.GetValue(null);
            string directory = EvidenceDirectory();
            try
            {
                foreach (int theme in new[] { 1, 0 })
                {
                    skin.SetValue(null, theme);
                    UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                    var window = ScriptableObject.CreateInstance<Host>();
                    McpWindowNavigation navigation = null;
                    McpReferenceGraphSection graph = null;
                    try
                    {
                        window.minSize = new Vector2(500, 340);
                        window.ShowUtility();
                        var root = window.rootVisualElement;
                        root.AddToClassList("mcp-editor");
                        root.AddToClassList(theme == 1 ? "unity-theme-dark" : "unity-theme-light");
                        AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "MCPForUnityEditorWindow.uxml").CloneTree(root);
                        root.Q("clients-panel").AddToClassList("hidden");
                        root.Q("graph-panel").RemoveFromClassList("hidden");
                        graph = new McpReferenceGraphSection(root.Q("graph-container"));
                        navigation = new McpWindowNavigation(root, _ => { });
                        navigation.Select("graph");
                        foreach (var size in new[] { new Vector2(500, 340), new Vector2(500, 600), new Vector2(1100, 700) })
                        {
                            window.position = new Rect(30, 30, size.x, size.y);
                            yield return Frames(window);
                            var view = root.Q<ScrollView>("graph-panel");
                            view.scrollOffset = Vector2.zero;
                            yield return Frames(window);
                            Assert.LessOrEqual(view.horizontalScroller.highValue, 1);
                            if (size.y == 340)
                                Assert.GreaterOrEqual(view.contentViewport.worldBound.height, 230, "Short main content must gain usable space.");
                            var button = root.Q<Button>("graph-refresh");
                            Assert.IsTrue(button.worldBound.Overlaps(view.contentViewport.worldBound));
                            Capture(window, directory, $"main-{size.x}-{size.y}-{theme}");
                        }
                    }
                    finally
                    {
                        graph?.Dispose();
                        navigation?.Dispose();
                        window.Close();
                    }
                }
            }
            finally
            {
                skin.SetValue(null, original);
                UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            }
        }

        [UnityTest, Explicit("Actual scenario panels, drafts and splitters across compact and wide windows.")]
        public IEnumerator ScenarioPanelsKeepDraftsAndPrimaryRunActionsAcrossResize()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var skin = typeof(EditorGUIUtility).GetProperty("skinIndex", Static);
            var original = skin.GetValue(null);
            string directory = EvidenceDirectory();
            string oldJob = SessionState.GetString(PlayScenarioWindow.SessionJobKey, "");
            bool oldSuite = SessionState.GetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
            SessionState.SetString(PlayScenarioWindow.SessionJobKey, "");
            SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
            try
            {
                foreach (int theme in new[] { 1, 0 })
                {
                    skin.SetValue(null, theme);
                    UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                    var window = ScriptableObject.CreateInstance<PlayScenarioWindow>();
                    window.ConfigureForTests(new PlayScenarioStore(Path.Combine(directory, "store-" + theme)));
                    window.ShowDialog = (_, __, ___, ____, _____) => 1;
                    try
                    {
                        window.ShowUtility();
                        Assert.LessOrEqual(window.minSize.x, 500);
                        Assert.LessOrEqual(window.minSize.y, 340);
                        window.ShowUtility();
                        window.CreateGUI();
                        var root = window.rootVisualElement;
                        var workers = new System.Collections.Generic.List<PlayScenarioImportWorker>();
                        root.Query<VisualElement>()
                            .ForEach(element =>
                            {
                                if (element.userData is PlayScenarioImportWorker worker)
                                    workers.Add(worker);
                            });
                        Assert.GreaterOrEqual(workers.Count, 5, "Session, soak, comparison and baseline readers must exist.");
                        var name = root.Q<TextField>("scenarioName");
                        name.value = "responsive-draft";
                        var step = root.Q<TextField>("stepName0");
                        step.value = "Retained step draft";
                        foreach (var size in new[] { new Vector2(500, 340), new Vector2(500, 600), new Vector2(1100, 700), new Vector2(500, 340) })
                        {
                            window.position = new Rect(30, 30, size.x, size.y);
                            yield return Frames(window);
                            foreach (var worker in workers)
                                Assert.IsFalse(worker.IsDisposed, "Moving panes must not dispose an evidence reader.");
                            Assert.AreSame(name, root.Q<TextField>("scenarioName"));
                            Assert.AreEqual("responsive-draft", window.Draft.Name);
                            Assert.AreEqual("Retained step draft", window.Draft.Steps[0].Name);
                            var run = root.Q<Button>("runScenario");
                            Assert.IsNull(run.GetFirstAncestorOfType<ScrollView>());
                            Assert.IsTrue(run.worldBound.Overlaps(root.worldBound));
                            if (size.x < 1000)
                            {
                                foreach (string pane in new[] { "library", "edit", "run" })
                                {
                                    var tab = root.Q<Button>("scenario-tab-" + pane);
                                    Assert.IsNotNull(tab);
                                    using (var evt = NavigationSubmitEvent.GetPooled())
                                    {
                                        evt.target = tab;
                                        tab.SendEvent(evt);
                                    }
                                    yield return Frames(window);
                                    string panelClass =
                                        pane == "edit" ? "scenario-editor"
                                        : pane == "run" ? "scenario-results"
                                        : "scenario-library";
                                    var panel = root.Q(className: panelClass);
                                    Assert.Greater(panel.worldBound.width, 440);
                                    Assert.Greater(panel.worldBound.height, 150);
                                    Assert.AreEqual(DisplayStyle.Flex, panel.resolvedStyle.display);
                                    Capture(window, directory, $"scenario-{pane}-{size.x}-{size.y}-{theme}");
                                }
                            }
                            else
                            {
                                Assert.AreEqual(2, root.Query<TwoPaneSplitView>().ToList().Count);
                                Capture(window, directory, $"scenario-wide-{theme}");
                            }
                        }
                        typeof(EditorWindow).GetProperty("hasUnsavedChanges").SetValue(window, false);
                        window.Close();
                        foreach (var worker in workers)
                            Assert.IsTrue(worker.IsDisposed, "Closing the owner must dispose all evidence readers.");
                    }
                    finally
                    {
                        if (window != null)
                        {
                            typeof(EditorWindow).GetProperty("hasUnsavedChanges").SetValue(window, false);
                            window.Close();
                        }
                    }
                }
            }
            finally
            {
                skin.SetValue(null, original);
                UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                SessionState.SetString(PlayScenarioWindow.SessionJobKey, oldJob);
                SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, oldSuite);
            }
        }

        private static void Submit(VisualElement root, string name)
        {
            var button = root.Q<Button>(name);
            Assert.IsNotNull(button, name);
            Assert.IsTrue(button.enabledInHierarchy, name);
            typeof(Clickable).GetMethod("Invoke", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(button.clickable, new object[] { null });
        }

        [UnityTest, Explicit("Saved scenario, filled reports and imported soak evidence at narrow sizes.")]
        public IEnumerator PopulatedScenarioEvidenceRemainsReachableAfterResize()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var flags = BindingFlags.NonPublic | BindingFlags.Instance;
            var skin = typeof(EditorGUIUtility).GetProperty("skinIndex", Static);
            var original = skin.GetValue(null);
            string oldJob = SessionState.GetString(PlayScenarioWindow.SessionJobKey, "");
            bool oldSuite = SessionState.GetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
            string directory = EvidenceDirectory();
            try
            {
                foreach (int theme in new[] { 1, 0 })
                {
                    SessionState.SetString(PlayScenarioWindow.SessionJobKey, "");
                    SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
                    skin.SetValue(null, theme);
                    UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                    string storeRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/ux-" + Guid.NewGuid().ToString("N").Substring(0, 8)));
                    Directory.CreateDirectory(storeRoot);
                    var store = new PlayScenarioStore(storeRoot);
                    var definition = new PlayScenarioDefinition
                    {
                        Name = "retained-flow",
                        Steps = new System.Collections.Generic.List<PlayScenarioStep>
                        {
                            new PlayScenarioStep
                            {
                                Name = "Load menu",
                                Action = "load_scene",
                                Scene = "Assets/FixtureMenu.unity",
                            },
                        },
                    };
                    store.Save(definition);
                    var report = new PlayScenarioRun
                    {
                        JobId = Guid.NewGuid().ToString("N"),
                        Scenario = definition,
                        Status = "succeeded",
                        Phase = "finished",
                        RepeatCount = 50,
                        StartedUnixMs = 1000,
                        FinishedUnixMs = 2000,
                    };
                    for (int index = 0; index < 50; index++)
                        report.Steps.Add(
                            new PlayScenarioStepResult
                            {
                                Name = "Saved result " + index,
                                StepIndex = 0,
                                Iteration = index + 1,
                                Status = "passed",
                                Detail = "Synthetic retained step result",
                            }
                        );
                    store.SaveReport(report);
                    var window = ScriptableObject.CreateInstance<PlayScenarioWindow>();
                    window.ConfigureForTests(store);
                    try
                    {
                        window.position = new Rect(30, 30, 1100, 700);
                        window.ShowUtility();
                        window.CreateGUI();
                        var root = window.rootVisualElement;
                        Submit(root, "savedScenario_retained-flow");
                        typeof(PlayScenarioWindow)
                            .GetMethod("AcceptReport", flags)
                            .Invoke(window, new object[] { new MCPForUnity.Editor.Helpers.SuccessResponse("Saved fixture report", report) });
                        Submit(root, "compareReports");
                        Assert.Greater(root.Q<ScrollView>("runResults").childCount, 40);
                        Assert.Greater(root.Q<ScrollView>("reportComparison").childCount, 0);
                        string soakDirectory = Path.Combine(storeRoot, "soak");
                        Directory.CreateDirectory(soakDirectory);
                        var fixture = new PlayScenarioSoakEditorTests();
                        typeof(PlayScenarioSoakEditorTests).GetField("root", flags).SetValue(fixture, soakDirectory);
                        string soakPath = (string)typeof(PlayScenarioSoakEditorTests).GetMethod("Save", flags).Invoke(fixture, new object[] { 53, (int?)7 });
                        window.ChoosePlayerSoakFile = () => soakPath;
                        Submit(root, "openPlayerSoak");
                        PlayScenarioImportTestPump.Drain(root, "playerSoak");
                        Assert.Greater(root.Q("playerSoakSummary").childCount, 0);
                        yield return Frames(window);
                        var name = root.Q<TextField>("copyScenarioName");
                        name.value = "retained-flow-copy";
                        name.Focus();
                        yield return Frames(window);
                        var beforeResize = root.focusController.focusedElement as VisualElement;
                        Assert.IsTrue(beforeResize == name || name.Contains(beforeResize), "Fixture must focus the scenario field before resizing.");
                        window.position = new Rect(30, 30, 500, 340);
                        yield return Frames(window);
                        var focused = root.focusController.focusedElement as VisualElement;
                        Assert.IsTrue(focused == name || name.Contains(focused), "Focused scenario field must survive wide-to-compact resize.");
                        Assert.AreEqual("retained-flow", window.Draft.Name);
                        Assert.AreEqual("retained-flow-copy", name.value);
                        Submit(root, "scenario-tab-run");
                        foreach (var size in new[] { new Vector2(500, 340), new Vector2(500, 600) })
                        {
                            window.position = new Rect(30, 30, size.x, size.y);
                            yield return Frames(window);
                            var scroll = root.Q<ScrollView>(className: "scenario-results-scroll");
                            foreach (string target in new[] { "runResults", "reportHistory", "playerSoak" })
                            {
                                var item = root.Q(target);
                                if (item is Foldout foldout)
                                    foldout.value = true;
                                yield return Frames(window);
                                scroll.ScrollTo(item);
                                yield return Frames(window);
                                Assert.LessOrEqual(scroll.horizontalScroller.highValue, 1, target);
                                Assert.IsTrue(item.worldBound.Overlaps(scroll.contentViewport.worldBound), target);
                                Capture(window, directory, $"populated-{target}-{size.y}-{theme}");
                            }
                            Assert.IsTrue(root.Q<Button>("runScenario").worldBound.Overlaps(root.worldBound));
                            Assert.IsFalse(window.IsPolling, "Saved evidence must not start polling or a new run.");
                        }
                        TestContext.Out.WriteLine("DISPLAY_PIXELS_PER_POINT " + EditorGUIUtility.pixelsPerPoint);
                    }
                    finally
                    {
                        typeof(EditorWindow).GetProperty("hasUnsavedChanges").SetValue(window, false);
                        window.Close();
                    }
                }
            }
            finally
            {
                skin.SetValue(null, original);
                UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                SessionState.SetString(PlayScenarioWindow.SessionJobKey, oldJob);
                SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, oldSuite);
            }
        }
    }
}
