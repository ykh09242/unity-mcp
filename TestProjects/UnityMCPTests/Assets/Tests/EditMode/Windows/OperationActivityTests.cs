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
            var original = root.Q<Button>("activity-evidence");
            for (int i = 0; i < 10000; i++)
                section.Tick();
            Assert.That(captures, Is.EqualTo(1));
            Assert.That(actions, Is.Zero);
            Assert.That(root.Query<Label>().ToList().Any(label => label.text == "Elapsed time unavailable"), Is.True);
            revision++;
            section.Tick();
            Assert.AreSame(original, root.Q<Button>("activity-evidence"));
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
                Assert.IsFalse(Directory.Exists(output));
            }
            finally
            {
                Environment.SetEnvironmentVariable("UNITY_MCP_WORKLOAD_OUTPUT", previous);
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
                    root.Q("clients-panel").AddToClassList("hidden");
                    root.Q("activity-panel").RemoveFromClassList("hidden");
                    root.Q<UnityEditor.UIElements.ToolbarToggle>("clients-tab").SetValueWithoutNotify(false);
                    root.Q<UnityEditor.UIElements.ToolbarToggle>("activity-tab").SetValueWithoutNotify(true);
                    long revision = 0;
                    int cancel = 0,
                        evidence = 0;
                    section = new McpActivitySection(root.Q("activity-container"), SampleRows, () => revision, _ => cancel++, _ => evidence++);
                    section.SetVisible(true);
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
                        foreach (var control in root.Q("activity-container").Query<Button>().ToList())
                        {
                            Assert.That(control.worldBound.width, Is.GreaterThan(8));
                            Assert.That(control.worldBound.xMin, Is.GreaterThanOrEqualTo(root.worldBound.xMin));
                            Assert.That(control.worldBound.xMax, Is.LessThanOrEqualTo(root.worldBound.xMax));
                        }
                        var button = root.Q<Button>("activity-evidence");
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
