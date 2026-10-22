using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Windows.Components.Activity;
using NUnit.Framework;
using Unity.Profiling;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public class ActivityReconciliationTests
    {
        public sealed class ActivityWindow : EditorWindow { }

        private static OperationActivityRow Row(int id) =>
            new()
            {
                Owner = "test",
                Id = id.ToString(),
                Operation = "Tests",
                State = "succeeded",
                Phase = "complete",
                Detail = "Completed",
                Cancellation = "No cancellation request observed",
                Started = 1000,
                Finished = 6000,
                EvidenceId = id.ToString(),
            };

        [Test]
        public void RevisionsPreserveRowsAndUpdateEveryChangedScalar()
        {
            var root = new VisualElement();
            var rows = new List<OperationActivityRow> { Row(1), Row(2) };
            long revision = 0;
            using var section = new McpActivitySection(root, () => rows, () => revision);
            section.SetVisible(true);
            var list = root.Q("activity-rows");
            var retained = list[0];
            var untouched = list[1];
            revision++;
            section.Tick();
            Assert.AreSame(retained, list[0]);
            Assert.AreSame(untouched, list[1]);
            rows[0].Operation = "Build";
            rows[0].State = "unknown";
            rows[0].Phase = "reconciling";
            rows[0].Detail = "Later evidence";
            rows[0].Cancellation = "Not supported";
            rows[0].CanCancel = true;
            rows[0].EvidenceId = null;
            rows[0].Started = 2000;
            rows[0].Finished = 12000;
            revision++;
            section.Tick();
            var labels = retained.Query<Label>().ToList().Select(label => label.text).ToArray();
            CollectionAssert.AreEqual(
                new[] { "Build · 1", "State: unknown · Phase: reconciling", "Later evidence", "Not supported", "Elapsed: 0:00:10" },
                labels
            );
            Assert.IsTrue(retained.Q<Button>("activity-cancel").enabledSelf);
            Assert.IsFalse(retained.Q<Button>("activity-evidence").enabledSelf);
            rows[0].CanCancel = false;
            rows[0].EvidenceId = "new-evidence";
            revision++;
            section.Tick();
            Assert.IsFalse(retained.Q<Button>("activity-cancel").enabledSelf);
            Assert.AreEqual("Not supported", retained.Q<Button>("activity-cancel").tooltip);
            Assert.IsTrue(retained.Q<Button>("activity-evidence").enabledSelf);
            Assert.AreSame(untouched, list[1]);
        }

        [Test]
        public void ReorderingRemovalHideAndDisposePreserveMembership()
        {
            var root = new VisualElement();
            var rows = new List<OperationActivityRow> { Row(1), Row(2), Row(3) };
            long revision = 0;
            using var section = new McpActivitySection(root, () => rows, () => revision);
            section.SetVisible(true);
            var list = root.Q("activity-rows");
            var first = list[0];
            var third = list[2];
            rows.Reverse();
            revision++;
            section.Tick();
            Assert.AreSame(third, list[0]);
            Assert.AreSame(first, list[2]);
            rows.RemoveAt(1);
            rows.Insert(1, Row(4));
            revision++;
            section.Tick();
            CollectionAssert.AreEqual(new[] { "activity-test:3", "activity-test:4", "activity-test:1" }, list.Children().Select(row => row.name));
            section.SetVisible(false);
            rows.Clear();
            revision++;
            section.Tick();
            Assert.AreEqual(3, list.childCount);
            section.SetVisible(true);
            Assert.Zero(list.childCount);
            Assert.AreEqual(DisplayStyle.Flex, root.Q<Label>("activity-empty").style.display.value);
            section.Dispose();
            Assert.Zero(root.childCount);
        }

        [UnityTest]
        public IEnumerator RepeatedActivityRevisionsMeasureCompleteUpdates()
        {
            Assert.AreNotEqual(UnityEngine.Rendering.GraphicsDeviceType.Null, SystemInfo.graphicsDeviceType);
            var window = ScriptableObject.CreateInstance<ActivityWindow>();
            McpActivitySection section = null;
            try
            {
                window.ShowUtility();
                foreach (var cohort in new[] { (count: 16, changed: false), (count: 128, changed: false), (count: 128, changed: true) })
                {
                    var rows = Enumerable.Range(0, cohort.count).Select(Row).ToList();
                    long revision = 0;
                    section = new McpActivitySection(window.rootVisualElement, () => rows, () => revision);
                    section.SetVisible(true);
                    for (int frame = 0; frame < 3; frame++)
                        yield return null;
                    Action work = () =>
                    {
                        for (int i = 0; i < 100; i++)
                        {
                            if (cohort.changed)
                                rows[0].Detail = i % 2 == 0 ? "Even progress" : "Odd progress";
                            revision++;
                            section.Tick();
                        }
                    };
                    work();
                    for (int repetition = 0; repetition < 3; repetition++)
                    {
                        var timer = Stopwatch.StartNew();
                        work();
                        timer.Stop();
                        long allocationEvents = 0;
                        using (
                            var recorder = ProfilerRecorder.StartNew(
                                ProfilerCategory.Memory,
                                "GC.Alloc",
                                262144,
                                ProfilerRecorderOptions.StartImmediately | ProfilerRecorderOptions.CollectOnlyOnCurrentThread
                            )
                        )
                        {
                            work();
                            recorder.Stop();
                            Assert.Less(recorder.Count, recorder.Capacity, "Allocation samples must not be truncated.");
                            Assert.IsTrue(recorder.Valid, "Allocation event instrumentation is unavailable.");
                            allocationEvents = recorder.Count;
                        }
                        var list = window.rootVisualElement.Q("activity-rows");
                        CollectionAssert.AreEqual(rows.Select(row => "activity-" + row.Key), list.Children().Select(child => child.name));
                        if (cohort.changed)
                            Assert.AreEqual("Odd progress", ((Label)list[0][2]).text);
                        TestContext.WriteLine(
                            $"BETA10_ROWS rows={cohort.count} changed={cohort.changed} revisions=100 repetition={repetition} ms={timer.Elapsed.TotalMilliseconds:F3} alloc_events={allocationEvents}"
                        );
                    }
                    section.Dispose();
                    section = null;
                }
            }
            finally
            {
                section?.Dispose();
                window.Close();
            }
        }
    }
}
