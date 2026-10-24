using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Build;
using MCPForUnity.Editor.Windows.Components.Activity;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public class OperationActivityTests
    {
        private const string Template = "Packages/com.ykh09242.unity-mcp/Editor/Windows/MCPForUnityEditorWindow.uxml";

        private sealed class EvidenceWindow : EditorWindow { }

        [Test]
        public void MainTemplateExposesDedicatedActivityPanel()
        {
            var root = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Template).CloneTree();
            Assert.IsNotNull(root.Q("activity-tab"));
            Assert.IsNotNull(root.Q("activity-container"));
        }

        [Test]
        public void DeadlineAndCancellationKeepNativeOwnershipVisible()
        {
            var job = new TestJob
            {
                JobId = "same-id",
                Mode = "EditMode",
                Status = TestJobStatus.Failed,
                CompletionKind = "deadline_exceeded",
                NativeControl = new JObject { ["released"] = false },
            };
            var deadline = OperationActivity.TestRow(job);
            Assert.IsTrue(deadline.Active);
            Assert.That(deadline.State, Is.EqualTo("failed"));
            StringAssert.Contains("Native termination not confirmed", deadline.Cancellation);
            job.Status = TestJobStatus.Cancelled;
            job.NativeControl["reason"] = "user";
            job.NativeControl["termination"] = "pending";
            var pending = OperationActivity.TestRow(job);
            Assert.IsTrue(pending.Active);
            Assert.IsFalse(pending.CanCancel);
            StringAssert.Contains("requested", pending.Cancellation);
            job.NativeControl["released"] = true;
            var done = OperationActivity.TestRow(job);
            Assert.IsFalse(done.Active);
            StringAssert.Contains("confirmed", done.Cancellation);
            Assert.IsTrue(deadline.Active, "Rows are detached observations, not live job references.");
        }

        [Test]
        public void UnownedTestCancellationIsUnavailableAndStaleActionsDoNotThrow()
        {
            var row = OperationActivity.TestRow(new TestJob { JobId = "missing-activity-test", Status = TestJobStatus.Running });
            Assert.IsFalse(row.CanCancel, "Ordinary runs without captured native ownership cannot be cancelled.");
            StringAssert.Contains("unavailable", row.Cancellation.ToLowerInvariant());
            Assert.DoesNotThrow(() => OperationActivity.Cancel(row), "A row may expire while the user confirms cancellation.");
            Assert.IsNotNull(OperationActivity.Cancel(row));
            var oldFactory = NativeTestRunControl.FactoryOverride;
            using var control = new NativeTestRunControl("owned", 0, () => 0, () => null, _ => false);
            try
            {
                NativeTestRunControl.FactoryOverride = (_, __) => control;
                StringAssert.Contains("expired", JObject.FromObject(OperationActivity.Cancel(row))["observation"].Value<string>());
                var owned = new TestJob
                {
                    JobId = "owned",
                    Status = TestJobStatus.Running,
                    Control = control,
                    NativeControl = control.State,
                };
                Assert.IsTrue(OperationActivity.TestRow(owned).CanCancel);
                control.Dispose();
                Assert.IsFalse(OperationActivity.TestRow(owned).CanCancel);
            }
            finally
            {
                NativeTestRunControl.FactoryOverride = oldFactory;
            }
        }

        [Test]
        public void ScenarioFinalizationAndReloadInterruptionAreNotSuccessfulCancellation()
        {
            var run = new PlayScenarioRun
            {
                JobId = "same-id",
                Status = "cancelled",
                Phase = "completed",
            };
            var finalizing = OperationActivity.ScenarioRow(run, false);
            Assert.IsTrue(finalizing.Active);
            Assert.That(finalizing.Phase, Is.EqualTo("finalizing"));
            StringAssert.Contains("requested", finalizing.Cancellation);
            run.RunnerResourcesReleased = true;
            Assert.That(OperationActivity.ScenarioRow(run, true).Cancellation, Is.EqualTo("Cancellation confirmed"));
            run.Status = "interrupted";
            var interrupted = OperationActivity.ScenarioRow(run, true);
            Assert.IsFalse(interrupted.Active);
            Assert.That(interrupted.State, Is.EqualTo("interrupted"));
            Assert.AreNotEqual(OperationActivity.TestRow(new TestJob { JobId = run.JobId }).Key, interrupted.Key);
        }

        [Test]
        public void BatchCancellationKeepsRunningChildActiveAndDoesNotReadReports()
        {
            var field = typeof(BuildJobStore).GetField("_batchJobs", BindingFlags.Static | BindingFlags.NonPublic);
            var jobs = (Dictionary<string, BatchJob>)field.GetValue(null);
            string id = "activity-test-" + Guid.NewGuid().ToString("N");
            var child = new BuildJob(id + "-child", BuildTarget.StandaloneWindows64, "unused") { State = BuildJobState.Building };
            var batch = new BatchJob(id) { State = BuildJobState.Cancelled };
            batch.Children.Add(child);
            jobs.Add(id, batch);
            try
            {
                var rows = new List<OperationActivityRow>();
                BuildJobStore.AppendActivity(rows);
                var row = rows.Single(item => item.Id == id);
                Assert.IsTrue(row.Active);
                Assert.That(row.State, Is.EqualTo("cancelling"));
                Assert.IsFalse(row.CanCancel);
                Assert.IsNull(row.EvidenceId);
                long revision = OperationActivity.Revision;
                child.State = BuildJobState.Succeeded;
                Assert.That(OperationActivity.Revision, Is.GreaterThan(revision));
                rows.Clear();
                BuildJobStore.AppendActivity(rows);
                Assert.IsFalse(rows.Single(item => item.Id == id).Active);
            }
            finally
            {
                jobs.Remove(id);
                OperationActivity.Invalidate();
            }
        }

        [Test]
        public void HiddenIdleAndDisposedViewsDoNotCaptureOrRunActions()
        {
            var root = new VisualElement();
            long revision = 0;
            int captures = 0,
                actions = 0;
            var rows = SampleRows();
            var section = new McpActivitySection(
                root,
                () =>
                {
                    captures++;
                    return rows;
                },
                () => revision,
                _ => actions++,
                _ => actions++
            );
            section.Tick();
            Assert.That(captures, Is.Zero);
            section.SetVisible(true);
            Assert.That(captures, Is.EqualTo(1));
            var original = root.Q<Button>("activity-select");
            for (int i = 0; i < 10000; i++)
                section.Tick();
            Assert.That(captures, Is.EqualTo(1));
            Assert.That(actions, Is.Zero);
            Assert.That(root.Query<Label>().ToList().Any(label => label.text == "Elapsed time unavailable"), Is.True);
            revision++;
            section.Tick();
            Assert.AreSame(original, root.Q<Button>("activity-select"));
            section.SetVisible(false);
            revision++;
            section.Tick();
            Assert.That(captures, Is.EqualTo(2));
            section.SetVisible(true);
            Assert.That(captures, Is.EqualTo(3));
            rows = new List<OperationActivityRow>();
            revision++;
            section.Tick();
            Assert.That(root.Q("activity-rows").childCount, Is.Zero);
            section.Dispose();
            section.SetVisible(true);
            revision++;
            section.Tick();
            Assert.That(captures, Is.EqualTo(4));
            Assert.That(root.childCount, Is.Zero);
            Assert.That(actions, Is.Zero);
        }

        [Test, Explicit("Controller-owned large workload cohort only.")]
        public async System.Threading.Tasks.Task SustainedActivityCohortPreservesIdleAndActiveCaptureCounts()
        {
            VisualElement lastRoot = null;
            await MCPForUnityTests.WorkloadProbe.RunObserved(
                "editor_activity",
                "tick_cycles",
                32000,
                () =>
                {
                    var root = lastRoot = new VisualElement();
                    long revision = 0;
                    int captures = 0;
                    using var section = new McpActivitySection(
                        root,
                        () =>
                        {
                            captures++;
                            return SampleRows();
                        },
                        () => revision,
                        _ => Assert.Fail("Unexpected action"),
                        _ => Assert.Fail("Unexpected action")
                    );
                    for (int cycle = 0; cycle < 32; cycle++)
                    {
                        section.SetVisible(true);
                        int before = captures;
                        for (int idle = 0; idle < 1000; idle++)
                            section.Tick();
                        Assert.AreEqual(before, captures, "Unchanged visible ticks must not recapture.");
                        revision++;
                        section.Tick();
                        Assert.AreEqual(before + 1, captures);
                        section.SetVisible(false);
                        revision++;
                        section.Tick();
                        Assert.AreEqual(before + 1, captures, "Hidden transitions must not capture.");
                    }
                    return captures.ToString(System.Globalization.CultureInfo.InvariantCulture);
                },
                () => new Dictionary<string, long> { ["owned_view_children"] = lastRoot?.childCount ?? 0 }
            );
        }

        [Test]
        public async System.Threading.Tasks.Task FailedObservedWorkReleasesUpdateSubscription()
        {
            string previous = Environment.GetEnvironmentVariable("UNITY_MCP_WORKLOAD_OUTPUT");
            string output = Path.Combine(Path.GetDirectoryName(Application.dataPath), ".work", "failed-observation-" + Guid.NewGuid().ToString("N"));
            int before = WorkloadProbe.ActiveObservers;
            Environment.SetEnvironmentVariable("UNITY_MCP_WORKLOAD_OUTPUT", output);
            try
            {
                try
                {
                    await WorkloadProbe.RunObservedAsync(
                        "failed",
                        "fixture",
                        1,
                        () => System.Threading.Tasks.Task.FromException<string>(new InvalidOperationException("owned-failure")),
                        () => new Dictionary<string, long>()
                    );
                    Assert.Fail("Failed work cannot publish a successful measurement.");
                }
                catch (InvalidOperationException error)
                {
                    Assert.AreEqual("owned-failure", error.Message);
                }
                Assert.AreEqual(before, WorkloadProbe.ActiveObservers);
                Assert.IsFalse(File.Exists(Path.Combine(output, "failed-fixture.json")), "Failed work must not publish completed evidence.");
                Assert.IsEmpty(Directory.GetFiles(output), "The temporary observation journal must be released on failure.");
            }
            finally
            {
                Environment.SetEnvironmentVariable("UNITY_MCP_WORKLOAD_OUTPUT", previous);
            }
        }

        private static void Click(VisualElement root, string name)
        {
            var button = root.Q<Button>(name);
            Assert.IsNotNull(button, name);
            Assert.IsTrue(button.enabledInHierarchy, name);
            typeof(Clickable).GetMethod("Invoke", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(button.clickable, new object[] { null });
        }

        private static void EvidenceGui(OperationEvidenceWindow window) =>
            typeof(OperationEvidenceWindow).GetMethod("CreateGUI", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(window, null);

        private static void DisableEvidence(OperationEvidenceWindow window) =>
            typeof(OperationEvidenceWindow).GetMethod("OnDisable", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(window, null);

        [Test]
        public void EmptyAndSinglePageKeepSelectionAndPaginationQuietAndFinishedIncludesAllInactiveStates()
        {
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            try
            {
                window.ShowUtility();
                var root = window.rootVisualElement;
                var rows = new List<OperationActivityRow>();
                long revision = 0;
                using var section = new McpActivitySection(root, () => rows, () => revision);
                section.SetVisible(true);
                Assert.AreEqual(DisplayStyle.None, root.Q("activity-selection").style.display.value);
                Assert.AreEqual(DisplayStyle.None, root.Q("activity-paging").style.display.value);
                rows.AddRange(
                    new[] { "succeeded", "failed", "cancelled", "timed_out", "interrupted" }.Select(state => new OperationActivityRow
                    {
                        Owner = "test",
                        Id = state,
                        State = state,
                        Operation = state,
                        EvidenceId = state,
                    })
                );
                rows.Add(
                    new OperationActivityRow
                    {
                        Owner = "test",
                        Id = "still-owned",
                        State = "failed",
                        Active = true,
                    }
                );
                revision++;
                section.Tick();
                var filter = root.Q<DropdownField>("activity-state-filter");
                Assert.Contains("Finished", filter.choices);
                Assert.IsFalse(filter.choices.Contains("Completed"));
                filter.value = "Finished";
                CollectionAssert.AreEquivalent(
                    rows.Where(row => !row.Active).Select(row => "activity-" + row.Key),
                    root.Q("activity-rows").Children().Select(row => row.name)
                );
                Assert.AreEqual(DisplayStyle.None, root.Q("activity-paging").style.display.value);
                Assert.AreEqual(DisplayStyle.None, root.Q("activity-selection").style.display.value);
                Click(root.Q("activity-test:cancelled"), "activity-select");
                StringAssert.Contains("cancelled", root.Q<Label>("activity-selected-summary").text);
                Assert.AreEqual(DisplayStyle.Flex, root.Q("activity-selection").style.display.value);
            }
            finally
            {
                window.Close();
            }
        }

        [Test]
        public void EvidenceSummaryIsLazyRawCopyIsExactAndRefreshAloneRereadsLargeEvidence()
        {
            var window = ScriptableObject.CreateInstance<OperationEvidenceWindow>();
            string clipboard = EditorGUIUtility.systemCopyBuffer;
            try
            {
                string raw = "  {\r\n  \"message\": \"한글\",\r\n  \"payload\": \"" + new string('x', 262144) + "\"\r\n}  ";
                int reads = 0;
                var row = SampleRows()[0];
                window.SetOperation(row);
                window.resolve = _ =>
                {
                    reads++;
                    return raw;
                };
                EvidenceGui(window);
                for (int i = 0; i < 20; i++)
                    EvidenceGui(window);
                Assert.Zero(reads, "Creating and rebuilding Summary does not load raw evidence.");
                StringAssert.Contains(row.Id, window.rootVisualElement.Q<Label>("operation-evidence-heading").text);
                StringAssert.Contains(row.Detail, window.rootVisualElement.Q<Label>("operation-evidence-summary").text);
                Assert.IsEmpty(window.rootVisualElement.Q<TextField>("operation-evidence-text").value);
                Click(window.rootVisualElement, "operation-evidence-copy");
                Assert.AreEqual(1, reads);
                Assert.AreEqual(raw, EditorGUIUtility.systemCopyBuffer);
                Click(window.rootVisualElement, "operation-evidence-raw-tab");
                Assert.AreEqual(raw, window.rootVisualElement.Q<TextField>("operation-evidence-text").value);
                Click(window.rootVisualElement, "operation-evidence-summary-tab");
                Click(window.rootVisualElement, "operation-evidence-raw-tab");
                Assert.AreEqual(1, reads, "Switching views reuses the explicitly loaded raw result.");
                Click(window.rootVisualElement, "operation-evidence-refresh");
                Assert.AreEqual(2, reads);
                Assert.AreEqual(raw, window.rootVisualElement.Q<TextField>("operation-evidence-text").value);
                StringAssert.DoesNotContain(raw, EditorJsonUtility.ToJson(window), "Raw is never serialized into window state.");
                DisableEvidence(window);
                Assert.IsNull(window.resolve);
                Assert.Zero(window.rootVisualElement.childCount);
                EvidenceGui(window);
                Assert.IsEmpty(window.rootVisualElement.Q<TextField>("operation-evidence-text").value);
                Assert.AreEqual(2, reads, "Reload returns to a summary without retaining the raw payload.");
            }
            finally
            {
                EditorGUIUtility.systemCopyBuffer = clipboard;
                UnityEngine.Object.DestroyImmediate(window);
            }
        }

        private sealed class SerializedProbe
        {
            internal int Reads;
            public string Detail
            {
                get
                {
                    Reads++;
                    return "large report detail";
                }
            }
        }

        [Test]
        public void EvidenceWindowsKeepDistinctOwnersAndRejectLateReplacementAndClose()
        {
            var first = ScriptableObject.CreateInstance<OperationEvidenceWindow>();
            var second = ScriptableObject.CreateInstance<OperationEvidenceWindow>();
            try
            {
                var rows = SampleRows();
                first.SetOperation(rows[0]);
                second.SetOperation(rows[1]);
                Assert.AreNotEqual(first.titleContent.text, second.titleContent.text, "Same IDs under different owners remain distinct.");
                EvidenceGui(first);
                EvidenceGui(second);
                var abandoned = new SerializedProbe();
                first.resolve = _ =>
                {
                    first.SetOperation(rows[1]);
                    return abandoned;
                };
                Click(first.rootVisualElement, "operation-evidence-raw-tab");
                Assert.Zero(abandoned.Reads, "A replaced owner cannot even serialize its late result.");
                StringAssert.Contains(rows[1].Operation, first.rootVisualElement.Q<Label>("operation-evidence-summary").text);
                Assert.IsEmpty(first.rootVisualElement.Q<TextField>("operation-evidence-text").value);
                Assert.IsEmpty(second.rootVisualElement.Q<TextField>("operation-evidence-text").value);
                int reads = 0;
                first.resolve = _ =>
                {
                    reads++;
                    return abandoned;
                };
                EvidenceGui(first);
                Assert.Zero(reads);
                Click(first.rootVisualElement, "operation-evidence-raw-tab");
                Assert.AreEqual(1, reads);
                Assert.AreEqual(1, abandoned.Reads);
                first.resolve = _ =>
                {
                    DisableEvidence(first);
                    return abandoned;
                };
                Click(first.rootVisualElement, "operation-evidence-refresh");
                Assert.AreEqual(1, abandoned.Reads, "Closed windows cannot serialize a late result.");
                Assert.Zero(first.rootVisualElement.childCount);
                Assert.IsNull(first.resolve);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(first);
                UnityEngine.Object.DestroyImmediate(second);
            }
        }

        [UnityTest]
        public IEnumerator EvidenceSummaryAndRawKeepIdentityVisibleInShortAndNarrowWindows()
        {
            var first = ScriptableObject.CreateInstance<OperationEvidenceWindow>();
            var second = ScriptableObject.CreateInstance<OperationEvidenceWindow>();
            string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/" + Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(directory);
            try
            {
                var row = SampleRows()[0];
                row.Id = "synthetic-shared-prefix-job-24";
                first.SetOperation(row);
                row.Id = "synthetic-shared-prefix-job-25";
                second.SetOperation(row);
                Assert.AreNotEqual(first.titleContent.text, second.titleContent.text);
                int reads = 0;
                first.resolve = _ =>
                {
                    reads++;
                    return new { error = "Synthetic failure detail", report = "Retained report" };
                };
                second.resolve = _ => throw new AssertionException("Second summary must stay lazy");
                first.minSize = second.minSize = new Vector2(360, 180);
                second.ShowUtility();
                first.ShowUtility();
                foreach (var size in new[] { new Vector2(500, 340), new Vector2(1200, 180) })
                {
                    first.position = new Rect(40, 40, size.x, size.y);
                    for (int i = 0; i < 12; i++)
                        yield return null;
                    if (first.rootVisualElement.Q<Button>("operation-evidence-summary-tab").enabledSelf)
                        Click(first.rootVisualElement, "operation-evidence-summary-tab");
                    for (int i = 0; i < 3; i++)
                        yield return null;
                    var header = first.rootVisualElement.Q<Label>("operation-evidence-heading");
                    StringAssert.Contains("synthetic-shared-prefix-job-24", header.text);
                    Assert.Greater(header.worldBound.yMax, first.rootVisualElement.worldBound.yMin);
                    Assert.Less(header.worldBound.yMin, first.rootVisualElement.worldBound.yMax);
                    string summaryPath = Path.Combine(directory, "evidence-summary-" + size.x + "x" + size.y + ".png");
                    Capture(first, summaryPath);
                    TestContext.WriteLine("EVIDENCE_SUMMARY_CAPTURE " + summaryPath);
                    Click(first.rootVisualElement, "operation-evidence-raw-tab");
                    for (int i = 0; i < 6; i++)
                        yield return null;
                    Assert.AreEqual(1, reads, "Raw is loaded once across views and resize.");
                    string rawPath = Path.Combine(directory, "evidence-raw-" + size.x + "x" + size.y + ".png");
                    Capture(first, rawPath);
                    TestContext.WriteLine("EVIDENCE_RAW_CAPTURE " + rawPath);
                    StringAssert.Contains("Synthetic failure detail", first.rootVisualElement.Q<TextField>("operation-evidence-text").value);
                    StringAssert.Contains("synthetic-shared-prefix-job-25", second.rootVisualElement.Q<Label>("operation-evidence-heading").text);
                    Assert.IsEmpty(second.rootVisualElement.Q<TextField>("operation-evidence-text").value);
                }
            }
            finally
            {
                first.Close();
                second.Close();
            }
        }

        [UnityTest]
        public IEnumerator LowerRowInspectRevealsCorrectDetailsWithoutBackgroundFocusOrScrollTheft()
        {
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            McpActivitySection section = null;
            string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/" + Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(directory);
            try
            {
                window.minSize = new Vector2(360, 180);
                window.ShowUtility();
                var root = window.rootVisualElement;
                root.AddToClassList("mcp-editor");
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Template).CloneTree(root);
                using var navigation = new MCPForUnity.Editor.Windows.McpWindowNavigation(root, _ => { });
                navigation.Select("activity");
                foreach (string page in new[] { "clients", "tools", "resources", "assetgen", "graph", "activity", "deps", "advanced" })
                    root.Q<UnityEditor.UIElements.ToolbarToggle>(page + "-tab").SetValueWithoutNotify(page == "activity");
                Assert.IsTrue(root.Q<UnityEditor.UIElements.ToolbarToggle>("activity-tab").value);
                root.Q("clients-panel").AddToClassList("hidden");
                root.Q("activity-panel").RemoveFromClassList("hidden");
                var rows = Enumerable
                    .Range(0, 1001)
                    .Select(i => new OperationActivityRow
                    {
                        Owner = "test",
                        Id = "synthetic-job-" + i,
                        Operation = "Synthetic job " + i,
                        State = "failed",
                        Phase = "finished",
                        Detail = "Job detail " + i,
                        EvidenceId = i.ToString(),
                    })
                    .ToList();
                long revision = 0;
                int reads = 0;
                section = new McpActivitySection(root.Q("activity-container"), () => rows, () => revision, _ => Assert.Fail("No cancellation"), _ => reads++);
                section.SetVisible(true);
                foreach (var size in new[] { new Vector2(1200, 800), new Vector2(500, 340), new Vector2(1200, 180) })
                {
                    window.position = new Rect(40, 40, size.x, size.y);
                    window.Focus();
                    for (int i = 0; i < 12; i++)
                    {
                        EditorApplication.QueuePlayerLoopUpdate();
                        window.Repaint();
                        yield return null;
                    }
                    var scroll = root.Q<ScrollView>("activity-panel");
                    var lower = root.Q("activity-test:synthetic-job-24");
                    scroll.ScrollTo(lower);
                    var inspect = lower.Q<Button>("activity-select");
                    inspect.Focus();
                    yield return null;
                    Assert.AreSame(inspect, root.focusController.focusedElement, "Inspect starts from its focused row action.");
                    using (var submit = NavigationSubmitEvent.GetPooled())
                        inspect.SendEvent(submit);
                    for (int i = 0; i < 12; i++)
                    {
                        EditorApplication.QueuePlayerLoopUpdate();
                        window.Repaint();
                        yield return null;
                    }
                    var summary = root.Q<Label>("activity-selected-summary");
                    StringAssert.Contains("synthetic-job-24", summary.text);
                    Assert.AreSame(summary, root.focusController.focusedElement);
                    Assert.Greater(summary.worldBound.yMax, scroll.contentViewport.worldBound.yMin);
                    Assert.Less(summary.worldBound.yMin, scroll.contentViewport.worldBound.yMax);
                    string path = Path.Combine(directory, "activity-inspect-" + size.x + "x" + size.y + ".png");
                    Capture(window, path);
                    TestContext.WriteLine(
                        "ACTIVITY_INSPECT_CAPTURE " + path + " summary=" + summary.worldBound + " viewport=" + scroll.contentViewport.worldBound
                    );
                    Click(root, "activity-return");
                    yield return null;
                    inspect.Focus();
                    var before = scroll.scrollOffset;
                    rows[24].Detail = "Updated job detail " + size.x;
                    revision++;
                    section.Tick();
                    for (int i = 0; i < 3; i++)
                        yield return null;
                    Assert.AreSame(inspect, root.focusController.focusedElement, "A background revision preserves focus.");
                    Assert.AreEqual(before, scroll.scrollOffset, "A background revision preserves the reading position.");
                    StringAssert.Contains(rows[24].Detail, summary.text);
                }
                Assert.Zero(reads, "Inspect, paging and refresh never load raw evidence.");
            }
            finally
            {
                section?.Dispose();
                window.Close();
            }
        }

        private static List<OperationActivityRow> SampleRows() =>
            new List<OperationActivityRow>
            {
                new OperationActivityRow
                {
                    Owner = "test",
                    Id = "same-id",
                    Operation = "Tests · EditMode",
                    State = "running",
                    Phase = "executing",
                    Detail = "420 completed / 12000",
                    Cancellation = "No cancellation request observed",
                    Active = true,
                    CanCancel = true,
                    EvidenceId = "same-id",
                    Started = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - 45000,
                },
                new OperationActivityRow
                {
                    Owner = "batch",
                    Id = "same-id",
                    Operation = "Build batch",
                    State = "cancelling",
                    Phase = "child build",
                    Detail = "2 / 12 child builds finished",
                    Cancellation = "Remaining builds cancelled; active child still running",
                    Active = true,
                    EvidenceId = "report-id",
                },
                new OperationActivityRow
                {
                    Owner = "scenario",
                    Id = "same-id",
                    Operation = "Play scenario · Long-running world loading",
                    State = "interrupted",
                    Phase = "reload",
                    Detail = "Runner resources released",
                    Cancellation = "No cancellation request observed",
                    EvidenceId = "same-id",
                    Started = 1,
                    Finished = 120001,
                },
            };

        [UnityTest]
        public IEnumerator RealActivityLayoutKeyboardAndBothEditorThemes()
        {
            Assert.That(
                SystemInfo.graphicsDeviceType,
                Is.Not.EqualTo(UnityEngine.Rendering.GraphicsDeviceType.Null),
                "Use a graphics-enabled isolated Editor."
            );
            bool originalSkin = EditorGUIUtility.isProSkin;
            var skinIndex = typeof(EditorGUIUtility).GetProperty("skinIndex", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(skinIndex);
            object originalIndex = skinIndex.GetValue(null);
            EvidenceWindow window = null;
            McpActivitySection section = null;
            string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/" + Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(directory);
            try
            {
                for (int theme = 0; theme < 2; theme++)
                {
                    if (theme == 1)
                    {
                        skinIndex.SetValue(null, originalSkin ? 0 : 1);
                        InternalEditorUtility.RepaintAllViews();
                        for (int i = 0; i < 12; i++)
                            yield return null;
                    }
                    Assert.That(EditorGUIUtility.isProSkin, Is.EqualTo(theme == 0 ? originalSkin : !originalSkin), "Actual Editor theme must change.");
                    window = ScriptableObject.CreateInstance<EvidenceWindow>();
                    window.titleContent = new GUIContent("Unity MCP Activity");
                    window.minSize = new Vector2(360, 420);
                    window.ShowUtility();
                    var root = window.rootVisualElement;
                    root.AddToClassList("mcp-editor");
                    AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Template).CloneTree(root);
                    using var navigation = new MCPForUnity.Editor.Windows.McpWindowNavigation(root, _ => { });
                    navigation.Select("activity");
                    root.Q("clients-panel").AddToClassList("hidden");
                    root.Q("activity-panel").RemoveFromClassList("hidden");
                    root.Q<UnityEditor.UIElements.ToolbarToggle>("clients-tab").SetValueWithoutNotify(false);
                    root.Q<UnityEditor.UIElements.ToolbarToggle>("activity-tab").SetValueWithoutNotify(true);
                    long revision = 0;
                    int cancel = 0,
                        evidence = 0;
                    section = new McpActivitySection(root.Q("activity-container"), SampleRows, () => revision, _ => cancel++, _ => evidence++);
                    section.SetVisible(true);
                    Click(root, "activity-select");
                    foreach (int width in new[] { 420, 1000 })
                    {
                        window.position = new Rect(40, 40, width, 1000);
                        for (int i = 0; i < 12; i++)
                        {
                            EditorApplication.QueuePlayerLoopUpdate();
                            window.Repaint();
                            yield return null;
                        }
                        Assert.That(root.resolvedStyle.width, Is.EqualTo(width).Within(2));
                        foreach (var control in root.Q("activity-container").Query<Button>().ToList().Where(button => button.worldBound.height > 0))
                        {
                            Assert.That(control.worldBound.width, Is.GreaterThan(8));
                            Assert.That(control.worldBound.xMin, Is.GreaterThanOrEqualTo(root.worldBound.xMin));
                            Assert.That(control.worldBound.xMax, Is.LessThanOrEqualTo(root.worldBound.xMax));
                        }
                        var button = root.Q<Button>("activity-selected-evidence");
                        button.Focus();
                        yield return null;
                        Assert.AreSame(button, root.focusController.focusedElement);
                        revision++;
                        section.Tick();
                        Assert.AreSame(button, root.focusController.focusedElement, "Refresh preserves keyboard focus.");
                        window.SendEvent(
                            new Event
                            {
                                type = EventType.KeyDown,
                                keyCode = KeyCode.Tab,
                                character = '\t',
                            }
                        );
                        yield return null;
                        Assert.AreSame(root.Q<Button>("activity-cancel"), root.focusController.focusedElement, "Tab navigates to cancellation.");
                        using (var submit = NavigationSubmitEvent.GetPooled())
                            root.Q<Button>("activity-cancel").SendEvent(submit);
                        Assert.That(cancel, Is.GreaterThan(0));
                        Assert.That(evidence, Is.Zero);
                        string path = Path.Combine(directory, (EditorGUIUtility.isProSkin ? "dark" : "light") + "-" + width + ".png");
                        Capture(window, path);
                        TestContext.WriteLine("ACTIVITY_LAYOUT_CAPTURE " + path);
                    }
                    section.Dispose();
                    section = null;
                    window.Close();
                    window = null;
                }
            }
            finally
            {
                section?.Dispose();
                if (window != null)
                    window.Close();
                skinIndex.SetValue(null, originalIndex);
                InternalEditorUtility.RepaintAllViews();
            }
            for (int i = 0; i < 12; i++)
                yield return null;
            Assert.That(EditorGUIUtility.isProSkin, Is.EqualTo(originalSkin), "Restore the original Editor theme.");
        }

        private static void Capture(EditorWindow window, string path)
        {
            object host = typeof(EditorWindow).GetField("m_Parent", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(window);
            MethodInfo grab = host.GetType()
                .GetMethod(
                    "GrabPixels",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
                    null,
                    new[] { typeof(RenderTexture), typeof(Rect) },
                    null
                );
            Assert.IsNotNull(grab, "Use actual Editor view pixels.");
            int width = Mathf.RoundToInt(window.rootVisualElement.resolvedStyle.width * EditorGUIUtility.pixelsPerPoint);
            int height = Mathf.RoundToInt(window.rootVisualElement.resolvedStyle.height * EditorGUIUtility.pixelsPerPoint);
            var rt = new RenderTexture(width, height, 0, RenderTextureFormat.ARGB32);
            var old = RenderTexture.active;
            Texture2D texture = null;
            try
            {
                rt.Create();
                grab.Invoke(host, new object[] { rt, new Rect(0, 0, width, height) });
                RenderTexture.active = rt;
                texture = new Texture2D(width, height, TextureFormat.RGBA32, false);
                texture.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                var pixels = texture.GetPixels32();
                var flipped = new Color32[pixels.Length];
                for (int y = 0; y < height; y++)
                    Array.Copy(pixels, y * width, flipped, (height - y - 1) * width, width);
                texture.SetPixels32(flipped);
                texture.Apply();
                File.WriteAllBytes(path, texture.EncodeToPNG());
            }
            finally
            {
                RenderTexture.active = old;
                if (texture != null)
                    UnityEngine.Object.DestroyImmediate(texture);
                rt.Release();
                UnityEngine.Object.DestroyImmediate(rt);
            }
        }
    }
}
