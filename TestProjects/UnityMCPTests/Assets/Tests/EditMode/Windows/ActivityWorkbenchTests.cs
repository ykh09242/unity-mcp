using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Windows.Components.Activity;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class ActivityWorkbenchTests
    {
        private sealed class Host : EditorWindow { }

        private Host host;

        [SetUp]
        public void SetUp()
        {
            host = ScriptableObject.CreateInstance<Host>();
            host.ShowUtility();
        }

        [TearDown]
        public void TearDown()
        {
            if (host != null)
                host.Close();
        }

        private static void Click(VisualElement root, string name)
        {
            var button = root.Q<Button>(name);
            Assert.IsNotNull(button, name);
            Assert.IsTrue(button.enabledInHierarchy, name);
            typeof(Clickable).GetMethod("Invoke", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(button.clickable, new object[] { null });
        }

        [Test]
        public void PagingKeepsEveryRetainedJobAccessibleWithoutRecapturingOrLoadingEvidence()
        {
            var rows = Enumerable
                .Range(0, 1001)
                .Select(i => new OperationActivityRow
                {
                    Owner = i % 2 == 0 ? "test" : "build",
                    Id = i.ToString(),
                    Operation = "Job " + i,
                    State = i % 3 == 0 ? "failed" : "succeeded",
                    Phase = "finished",
                    EvidenceId = "evidence-" + i,
                })
                .ToList();
            var root = host.rootVisualElement;
            int captures = 0,
                reads = 0;
            using var section = new McpActivitySection(
                root,
                () =>
                {
                    captures++;
                    return rows;
                },
                () => 0,
                _ => Assert.Fail("No cancellation"),
                _ => reads++
            );
            section.SetVisible(true);
            Assert.IsNotNull(root.Q<DropdownField>("activity-state-filter"));
            var seen = new HashSet<string>();
            do
            {
                var page = root.Q("activity-rows");
                Assert.LessOrEqual(page.childCount, 25);
                foreach (var row in page.Children())
                    Assert.IsTrue(seen.Add(row.name));
                if (!root.Q<Button>("activity-next").enabledSelf)
                    break;
                Click(root, "activity-next");
            } while (true);
            Assert.AreEqual(rows.Count, seen.Count);
            Assert.AreEqual(1, captures);
            Assert.AreEqual(0, reads);
            root.Q<DropdownField>("activity-owner-filter").value = "build";
            root.Q<DropdownField>("activity-state-filter").value = "Failed";
            foreach (var row in root.Q("activity-rows").Children())
                StringAssert.StartsWith("activity-build:", row.name);
            StringAssert.Contains("167 matching", root.Q<Label>("activity-page").text);
            Assert.AreEqual(1, captures);
        }

        [Test]
        public void SelectionTracksOwnerAndIdAcrossFilteringAndRefresh()
        {
            var rows = new List<OperationActivityRow>
            {
                new OperationActivityRow
                {
                    Owner = "test",
                    Id = "shared",
                    Operation = "Tests",
                    State = "running",
                    Phase = "executing",
                    Active = true,
                    EvidenceId = "shared",
                },
                new OperationActivityRow
                {
                    Owner = "build",
                    Id = "shared",
                    Operation = "Build",
                    State = "failed",
                    Phase = "finished",
                    EvidenceId = "report",
                },
            };
            var root = host.rootVisualElement;
            long revision = 0;
            int reads = 0;
            using var section = new McpActivitySection(
                root,
                () => rows,
                () => revision,
                _ => Assert.Fail("No cancellation"),
                row =>
                {
                    reads++;
                    Assert.AreEqual("build", row.Owner);
                }
            );
            section.SetVisible(true);
            Assert.IsNotNull(root.Q("activity-build:shared").Q<Button>("activity-select"));
            Click(root.Q("activity-build:shared"), "activity-select");
            root.Q<DropdownField>("activity-state-filter").value = "Active";
            StringAssert.Contains("Build", root.Q<Label>("activity-selected-summary").text);
            Assert.AreEqual(0, reads);
            Click(root, "activity-selected-evidence");
            Assert.AreEqual(1, reads);
            rows.RemoveAt(1);
            revision++;
            section.Tick();
            StringAssert.Contains("no longer retained", root.Q<Label>("activity-selected-summary").text);
            Assert.IsFalse(root.Q<Button>("activity-selected-evidence").enabledSelf);
        }

        [Test]
        public void EvidenceReloadRetainsOnlyIdentityAndShowsExplicitUnavailableState()
        {
            var flags = BindingFlags.Instance | BindingFlags.NonPublic;
            var configure = typeof(OperationEvidenceWindow).GetMethod("SetOperation", flags);
            Assert.IsNotNull(configure);
            var window = ScriptableObject.CreateInstance<OperationEvidenceWindow>();
            try
            {
                int reads = 0;
                typeof(OperationEvidenceWindow)
                    .GetField("resolve", flags)
                    .SetValue(
                        window,
                        new Func<OperationActivityRow, object>(_ =>
                        {
                            reads++;
                            return new { result = "synthetic-raw-evidence" };
                        })
                    );
                configure.Invoke(
                    window,
                    new object[]
                    {
                        new OperationActivityRow
                        {
                            Owner = "test",
                            Id = "retained-id",
                            EvidenceId = "retained-id",
                        },
                    }
                );
                typeof(OperationEvidenceWindow).GetMethod("CreateGUI", flags).Invoke(window, null);
                Assert.AreEqual(0, reads, "Summary must not resolve or serialize evidence.");
                Click(window.rootVisualElement, "operation-evidence-raw-tab");
                Assert.AreEqual(1, reads);
                StringAssert.Contains("synthetic-raw-evidence", window.rootVisualElement.Q<TextField>("operation-evidence-text").value);
                string serialized = EditorJsonUtility.ToJson(window);
                StringAssert.Contains("retained-id", serialized);
                StringAssert.DoesNotContain("synthetic-raw-evidence", serialized);
                typeof(OperationEvidenceWindow).GetMethod("OnDisable", flags).Invoke(window, null);
                typeof(OperationEvidenceWindow)
                    .GetField("resolve", flags)
                    .SetValue(window, new Func<OperationActivityRow, object>(_ => throw new Exception("sensitive-error")));
                typeof(OperationEvidenceWindow).GetMethod("CreateGUI", flags).Invoke(window, null);
                Click(window.rootVisualElement, "operation-evidence-raw-tab");
                string unavailable = window.rootVisualElement.Q<TextField>("operation-evidence-text").value;
                StringAssert.Contains("unavailable", unavailable);
                StringAssert.DoesNotContain("sensitive-error", unavailable);
                typeof(OperationEvidenceWindow).GetField("projectScope", flags).SetValue(window, "different-project");
                typeof(OperationEvidenceWindow)
                    .GetField("resolve", flags)
                    .SetValue(window, new Func<OperationActivityRow, object>(_ => throw new AssertionException("Cross-project evidence lookup")));
                typeof(OperationEvidenceWindow).GetMethod("CreateGUI", flags).Invoke(window, null);
                Click(window.rootVisualElement, "operation-evidence-refresh");
                StringAssert.Contains("different project", window.rootVisualElement.Q<TextField>("operation-evidence-text").value);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(window);
            }
        }
    }
}
