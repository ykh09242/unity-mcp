using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Windows.PlayScenarios;
using MCPForUnityTests.Editor;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    public class PlayScenarioSoakComparisonEditorTests
    {
        private string root;
        private ComparisonTestWindow window;
        private OwnedTestTasks owned;

        private sealed class ComparisonTestWindow : UnityEditor.EditorWindow { }

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            owned = new OwnedTestTasks();
            root = Path.Combine(Path.GetTempPath(), "unity-mcp-soak-comparison-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            window = ScriptableObject.CreateInstance<ComparisonTestWindow>();
            window.Show();
            yield return null;
        }

        [UnityTearDown]
        public IEnumerator TearDown()
        {
            try
            {
                yield return owned.Cleanup();
            }
            finally
            {
                if (window != null)
                    UnityEngine.Object.DestroyImmediate(window);
                Directory.Delete(root, true);
            }
        }

        private static JObject EnvironmentMetadata() =>
            new JObject
            {
                ["schema_version"] = 1,
                ["operating_system"] = "Fixture OS",
                ["platform"] = "WindowsPlayer",
                ["architecture"] = "x64",
                ["scripting_backend"] = "mono",
                ["build_mode"] = "release",
                ["runtime_version"] = "4.0.30319.42000",
            };

        private static JObject Sample(int iteration, long value) =>
            new JObject
            {
                ["iteration"] = iteration,
                ["timestamp_unix_ms"] = iteration * 10 + 5,
                ["managed_bytes"] = value,
                ["allocated_bytes"] = value,
                ["object_count"] = value,
                ["runner_subscription_count"] = 0,
                ["runner_handle_count"] = 0,
                ["ignored_for_trend"] = iteration <= 1,
                ["error"] = null,
            };

        private static PlayScenarioSoakComparisonSnapshot Snapshot(int count = 5, long offset = 0)
        {
            var evidence = new PlayScenarioSoakEvidence
            {
                Summary = new JObject
                {
                    ["requested_iterations"] = count,
                    ["definition_hash"] = new string('a', 64),
                    ["failure_policy"] = "continue",
                    ["metrics_summary"] = new JObject { ["warmup_iterations"] = 1 },
                },
                Completeness = "Complete: fixture native evidence; process exit unverified.",
                VerifiedReproduction = new JObject
                {
                    ["unity_version"] = "2021.3.45f1",
                    ["package_version"] = "fixture",
                    ["environment"] = EnvironmentMetadata(),
                },
            };
            for (int index = 1; index <= count; index++)
                evidence.Rows.Add(new JObject { ["iteration"] = index, ["metrics"] = new JArray(Sample(index, index + offset)) });
            var snapshot = new PlayScenarioSoakComparisonSnapshot { Source = "fixture-soak.json", Evidence = evidence };
            snapshot.Initialize();
            return snapshot;
        }

        [Test]
        public void ExactRawDeltasExcludeWarmupAndIntervalsBreakAtMissingOrErrorMeasurements()
        {
            var left = Snapshot();
            var right = Snapshot(offset: 7);
            left.Evidence.Rows[2]["metrics"] = new JArray();
            right.Evidence.Rows[3]["metrics"][0]["error"] = "Unavailable boundary sample";
            left.Initialize();
            right.Initialize();
            var comparison = PlayScenarioSoakComparison.Create(left, right);
            Assert.AreEqual("unavailable", comparison.Delta("managed_bytes", 1));
            Assert.AreEqual("7", comparison.Delta("managed_bytes", 2));
            Assert.AreEqual("unavailable", comparison.Delta("managed_bytes", 3));
            Assert.AreEqual("unavailable", comparison.Delta("managed_bytes", 4));
            Assert.AreEqual(2, comparison.Intervals("managed_bytes").Count());
            StringAssert.Contains("missing 1", PlayScenarioSoakComparison.Counts(left, "managed_bytes"));
            StringAssert.Contains("error 1", PlayScenarioSoakComparison.Counts(right, "managed_bytes"));
            using (var chart = new PlayScenarioSoakComparisonChart())
            {
                chart.SetData(comparison, "managed_bytes");
                chart.Rebuild(320, 160);
                Assert.AreEqual(2, chart.BaselineEdges);
                Assert.AreEqual(2, chart.CandidateEdges);
                Assert.Greater(chart.CachedVertices.Length, 0);
            }
        }

        [Test]
        public void ValuesAboveDoublePrecisionRetainExactDeltaAndNonFlatGeometry()
        {
            var left = Snapshot(2);
            var right = Snapshot(2);
            for (int index = 0; index < 2; index++)
            {
                left.Evidence.Rows[index]["metrics"][0]["managed_bytes"] = long.MaxValue - 3 + index;
                right.Evidence.Rows[index]["metrics"][0]["managed_bytes"] = long.MaxValue - 2 + index;
            }
            left.Initialize();
            right.Initialize();
            var comparison = PlayScenarioSoakComparison.Create(left, right);
            Assert.AreEqual("1", comparison.Delta("managed_bytes", 2));
            using (var chart = new PlayScenarioSoakComparisonChart())
            {
                chart.SetData(comparison, "managed_bytes");
                chart.Rebuild(320, 160);
                var blue = chart.CachedVertices.Where(vertex => vertex.tint.Equals(PlayScenarioSoakComparisonChart.BaselineColor)).ToArray();
                Assert.Greater(blue.Max(vertex => vertex.position.y) - blue.Min(vertex => vertex.position.y), 40);
            }
        }

        [TestCase("definition_hash")]
        [TestCase("failure_policy")]
        [TestCase("environment")]
        [TestCase("warmup")]
        [TestCase("unity_version")]
        [TestCase("incomplete")]
        public void KnownIncompatibilitySuppressesCrossRunDeltas(string difference)
        {
            var left = Snapshot();
            var right = Snapshot(offset: 4);
            if (difference == "environment")
                right.Evidence.VerifiedReproduction["environment"]["architecture"] = "arm64";
            else if (difference == "warmup")
                right.Evidence.Summary["metrics_summary"]["warmup_iterations"] = 2;
            else if (difference == "unity_version")
                right.Evidence.VerifiedReproduction["unity_version"] = "2022.3";
            else if (difference == "incomplete")
                right.Evidence.Completeness = "Incomplete: missing final report.";
            else
                right.Evidence.Summary[difference] = difference == "failure_policy" ? "stop" : new string('b', 64);
            right.Initialize();
            var comparison = PlayScenarioSoakComparison.Create(left, right);
            Assert.IsFalse(comparison.AllowDeltas);
            Assert.AreEqual("unavailable", comparison.Delta("object_count", 3));
            Assert.IsNotEmpty(comparison.Differences);
        }

        [Test]
        public void AbsentEnvironmentRemainsUnknownAndDoesNotBecomeFalseKnownCompatibility()
        {
            var left = Snapshot();
            var right = Snapshot();
            right.Evidence.VerifiedReproduction.Remove("environment");
            right.Initialize();
            var comparison = PlayScenarioSoakComparison.Create(left, right);
            Assert.AreEqual(6, comparison.Unknown.Count);
            Assert.IsTrue(comparison.AllowDeltas, "Raw native diagnostics do not constitute process proof or budget admission.");
        }

        [Test]
        public void DifferentLengthsKeepUnpairedIterationsOutOfPairedDifferences()
        {
            var comparison = PlayScenarioSoakComparison.Create(Snapshot(3), Snapshot(5, 7));
            Assert.AreEqual("7", comparison.Delta("allocated_bytes", 3));
            Assert.AreEqual("unavailable", comparison.Delta("allocated_bytes", 4));
            Assert.AreEqual(1, comparison.Intervals("allocated_bytes").Count());
            StringAssert.Contains("unpaired candidate 2", comparison.PairingText("allocated_bytes"));
        }

        [TestCase("extra")]
        [TestCase("missing")]
        [TestCase("wrong-type")]
        [TestCase("wrong-schema")]
        [TestCase("oversized")]
        [TestCase("unsupported-architecture")]
        [TestCase("utf16-overflow")]
        [TestCase("control")]
        [TestCase("invalid-unicode")]
        public void OptionalEnvironmentMetadataHasStrictBounds(string invalid)
        {
            var environment = EnvironmentMetadata();
            if (invalid == "extra")
                environment["extra"] = true;
            else if (invalid == "missing")
                environment.Remove("runtime_version");
            else if (invalid == "wrong-type")
                environment["platform"] = 42;
            else if (invalid == "wrong-schema")
                environment["schema_version"] = 2;
            else if (invalid == "oversized")
                environment["operating_system"] = new string('x', 257);
            else if (invalid == "utf16-overflow")
                environment["operating_system"] = new string('x', 255) + "\U0001F600";
            else if (invalid == "control")
                environment["operating_system"] = "Test\u0085OS";
            else if (invalid == "invalid-unicode")
                environment["operating_system"] = "Test\uD800OS";
            else
                environment["architecture"] = "unknown";
            Assert.Throws<InvalidDataException>(() => PlayScenarioSoakComparisonSnapshot.ValidateEnvironment(environment));
        }

        [Test]
        public void ExactUtf16BoundaryAndExplicitUnknownFieldsRemainValid()
        {
            var environment = EnvironmentMetadata();
            environment["operating_system"] = new string('x', 254) + "\U0001F600";
            Assert.AreEqual(256, ((string)PlayScenarioSoakComparisonSnapshot.ValidateEnvironment(environment)["operating_system"]).Length);
            foreach (string field in PlayScenarioSoakComparisonSnapshot.EnvironmentFields)
                environment[field] = null;
            JObject validated = PlayScenarioSoakComparisonSnapshot.ValidateEnvironment(environment);
            Assert.IsTrue(PlayScenarioSoakComparisonSnapshot.EnvironmentFields.All(field => validated[field].Type == JTokenType.Null));
        }

        private string Save(string side, int count, long offset)
        {
            string directory = Path.Combine(root, side);
            Directory.CreateDirectory(directory);
            JObject Identity() =>
                new JObject
                {
                    ["schema_version"] = 1,
                    ["job_id"] = new string(side.StartsWith("baseline", StringComparison.Ordinal) ? '1' : '2', 32),
                    ["definition_hash"] = new string('a', 64),
                    ["build_id"] = new string('b', 32),
                    ["build_source_revision"] = null,
                    ["payload_hash"] = new string('c', 64),
                    ["process_id"] = 4567,
                };
            JObject[] rows = Enumerable
                .Range(1, count)
                .Select(index =>
                {
                    JObject row = Identity();
                    row["iteration"] = index;
                    row["status"] = "succeeded";
                    row["started_unix_ms"] = index * 10;
                    row["finished_unix_ms"] = index * 10 + 5;
                    row["runner_resources_released"] = true;
                    row["failure_code"] = null;
                    row["error"] = null;
                    row["cleanup_error"] = null;
                    row["unexpected_log_count"] = 0;
                    row["query_counts"] = new JObject { ["target_searches"] = 0, ["hierarchy_visits"] = 0 };
                    row["metrics"] = new JArray(Sample(index, index + offset));
                    row["report_file"] = "iteration-" + index.ToString("D4") + ".json";
                    return row;
                })
                .ToArray();
            byte[] bytes = new UTF8Encoding(false).GetBytes(string.Join("\n", rows.Select(row => row.ToString(Formatting.None))) + "\n");
            File.WriteAllBytes(Path.Combine(directory, "iterations.jsonl"), bytes);
            var summary = Identity();
            summary["process_scope"] = "same-process";
            summary["requested_iterations"] = count;
            summary["recorded_iterations"] = count;
            summary["attempted_iterations"] = count;
            summary["passed_iterations"] = count;
            summary["failed_iterations"] = 0;
            summary["skipped_iterations"] = 0;
            summary["first_failure_iteration"] = null;
            summary["started_unix_ms"] = 1;
            summary["deadline_unix_ms"] = count * 10 + 100;
            summary["finished_unix_ms"] = count * 10 + 6;
            summary["status"] = "succeeded";
            summary["finalization_state"] = "completed";
            summary["failure_policy"] = "continue";
            summary["retain_reports"] = 4;
            summary["journal_file"] = "iterations.jsonl";
            summary["journal_rows"] = count;
            summary["journal_bytes"] = bytes.Length;
            using (SHA256 hash = SHA256.Create())
                summary["journal_sha256"] = string.Concat(hash.ComputeHash(bytes).Select(b => b.ToString("x2")));
            summary["journal_complete"] = true;
            summary["runner_resources_released"] = true;
            summary["selected_iteration"] = count;
            summary["retained_report_files"] = new JArray("iteration-" + count.ToString("D4") + ".json");
            summary["error"] = null;
            summary["metrics_summary"] = new JObject
            {
                ["sample_count"] = count,
                ["usable_samples"] = count - 1,
                ["warmup_iterations"] = 1,
                ["bounded_samples"] = new JArray(rows.Skip(Math.Max(0, count - 10)).Select(row => row["metrics"][0].DeepClone())),
                ["warnings"] = new JArray(),
                ["summary"] = "Fixture measurements",
            };
            var report = new JObject
            {
                ["job_id"] = summary["job_id"].DeepClone(),
                ["player_schema_version"] = 2,
                ["repeat_count"] = 1,
                ["status"] = "succeeded",
                ["execution_environment"] = "player",
                ["finalization_state"] = "completed",
                ["started_unix_ms"] = count * 10,
                ["finished_unix_ms"] = count * 10 + 5,
                ["runner_resources_released"] = true,
                ["iteration_results_version"] = 1,
                ["iteration_results"] = new JArray(
                    new JObject
                    {
                        ["iteration"] = 1,
                        ["status"] = "passed",
                        ["started_unix_ms"] = count * 10,
                        ["finished_unix_ms"] = count * 10 + 5,
                    }
                ),
                ["reproduction"] = new JObject
                {
                    ["definition_hash"] = summary["definition_hash"].DeepClone(),
                    ["build_id"] = summary["build_id"].DeepClone(),
                    ["build_source_revision"] = null,
                    ["payload_hash"] = summary["payload_hash"].DeepClone(),
                    ["unity_version"] = "2021.3.45f1",
                    ["package_version"] = "fixture",
                    ["environment"] = EnvironmentMetadata(),
                },
                ["exit_code"] = 0,
                ["soak"] = summary.DeepClone(),
                ["steps"] = new JArray(),
            };
            File.WriteAllText(Path.Combine(directory, "run.json"), report.ToString(Formatting.None), new UTF8Encoding(false));
            string file = Path.Combine(directory, "soak.json");
            File.WriteAllText(file, summary.ToString(Formatting.None), new UTF8Encoding(false));
            return file;
        }

        private static void Click(VisualElement parent, string name) =>
            typeof(Clickable)
                .GetMethod("Invoke", BindingFlags.NonPublic | BindingFlags.Instance)
                .Invoke(parent.Q<Button>(name).clickable, new object[] { null });

        [UnityTest]
        public IEnumerator ActualTwoSourceImportsPageCachedRowsSwitchMetricsAndExposeNativeOnlyProof()
        {
            string left = Save("baseline", 1000, 0);
            string right = Save("candidate", 1000, 7);
            int reads = 0;
            var parent = new VisualElement();
            window.rootVisualElement.Add(parent);
            using (
                var view = new PlayScenarioSoakComparisonEditor(
                    parent,
                    side => side == "Baseline" ? left : right,
                    (source, token) =>
                    {
                        Interlocked.Increment(ref reads);
                        return PlayScenarioSoakComparisonSnapshot.Read(source, token);
                    }
                )
            )
            {
                parent.Q<Foldout>("playerSoakComparison").value = true;
                Click(parent, "chooseSoakBaseline");
                Click(parent, "importSoakBaseline");
                Click(parent, "chooseSoakCandidate");
                yield return PlayScenarioImportTestPump.Wait((PlayScenarioImportWorker)parent.Q<TextField>("soakBaselinePath").userData);
                yield return PlayScenarioImportTestPump.Wait((PlayScenarioImportWorker)parent.Q<TextField>("soakCandidatePath").userData);
                Assert.AreEqual(2, reads);
                StringAssert.Contains("process exit unverified", parent.Q<Label>("soakComparisonState").text);
                Assert.AreEqual(25, parent.Q("soakComparisonRows").childCount);
                StringAssert.Contains("candidate − baseline 7", parent.Q<Label>("soakComparisonIteration_2").text);
                File.Delete(left);
                File.Delete(right);
                Click(parent, "nextSoakComparisonPage");
                Assert.IsNotNull(parent.Q("soakComparisonIteration_26"));
                parent.Q<DropdownField>("soakComparisonMetric").value = "object_count";
                Assert.IsNotNull(parent.Q("soakComparisonIteration_1"));
                var chart = parent.Q<PlayScenarioSoakComparisonChart>("soakComparisonChart");
                chart.Rebuild(320, 160);
                Assert.Less(chart.CachedVertices.Length, 17000);
                Assert.AreEqual(999, chart.BaselineEdges);
                Assert.AreEqual(999, chart.CandidateEdges);
                Assert.AreEqual(2, reads, "Paging and repaint cannot refresh artifacts.");
                Click(parent, "importSoakCandidate");
                StringAssert.Contains("stale", parent.Q<Label>("soakComparisonState").text);
                yield return PlayScenarioImportTestPump.Wait((PlayScenarioImportWorker)parent.Q<TextField>("soakCandidatePath").userData);
                StringAssert.Contains("stale", parent.Q<Label>("soakCandidateState").text);
                StringAssert.Contains("candidate − baseline unavailable", parent.Q<Label>("soakComparisonIteration_2").text);
                parent.Q<TextField>("soakCandidatePath").value = Path.Combine(root, "new", "soak.json");
                Assert.AreEqual(0, parent.Q("soakComparisonRows").childCount);
                Assert.AreEqual(0, chart.CachedVertices.Length);
            }
        }

        [Test]
        public void ForeignRunIdentityCannotSupplyEnvironmentMetadata()
        {
            string file = Save("baseline", 2, 0);
            string reportFile = Path.Combine(Path.GetDirectoryName(file), "run.json");
            JObject report = PlayScenarioPlayerSessionEditor.ReadJson(reportFile);
            report["job_id"] = new string('f', 32);
            File.WriteAllText(reportFile, report.ToString(Formatting.None), new UTF8Encoding(false));
            Assert.Throws<InvalidDataException>(() => PlayScenarioSoakComparisonSnapshot.Read(file, CancellationToken.None));
        }

        [UnityTest]
        public IEnumerator DetachCancelsBothSlotWorkersWithoutRetainingCallbacks()
        {
            var entered = owned.Own(new CountdownEvent(2));
            var cancelled = owned.Own(new CountdownEvent(2));
            {
                var parent = new VisualElement();
                window.rootVisualElement.Add(parent);
                using (
                    var view = new PlayScenarioSoakComparisonEditor(
                        parent,
                        side => Path.Combine(root, side, "soak.json"),
                        (_, token) =>
                        {
                            entered.Signal();
                            token.WaitHandle.WaitOne();
                            cancelled.Signal();
                            token.ThrowIfCancellationRequested();
                            return null;
                        }
                    )
                )
                {
                    Click(parent, "chooseSoakBaseline");
                    Click(parent, "chooseSoakCandidate");
                    var left = (PlayScenarioImportWorker)parent.Q<TextField>("soakBaselinePath").userData;
                    var right = (PlayScenarioImportWorker)parent.Q<TextField>("soakCandidatePath").userData;
                    var leftTask = PlayScenarioImportTestPump.Track(owned, left);
                    var rightTask = PlayScenarioImportTestPump.Track(owned, right);
                    yield return TestUtilities.WaitUntil(
                        () => entered.IsSet,
                        "both comparison readers to enter",
                        () => leftTask.IsCompleted || rightTask.IsCompleted
                    );
                    parent.RemoveFromHierarchy();
                    yield return TestUtilities.WaitUntil(
                        () => cancelled.IsSet,
                        "both comparison readers to observe cancellation",
                        () => leftTask.IsCompleted && rightTask.IsCompleted
                    );
                    yield return null;
                    Assert.IsTrue(left.IsDisposed && right.IsDisposed);
                    Assert.IsFalse(left.IsPumping || right.IsPumping);
                    Assert.AreEqual(0, parent.Q("soakComparisonRows").childCount);
                }
            }
        }

        [UnityTest]
        public IEnumerator ActualWindowHostsComparisonAndDisposesBothSlotsOnRecreationAndClose()
        {
            string previousJob = UnityEditor.SessionState.GetString(PlayScenarioWindow.SessionJobKey, "");
            bool previousMode = UnityEditor.SessionState.GetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
            UnityEditor.SessionState.SetString(PlayScenarioWindow.SessionJobKey, "");
            UnityEditor.SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
            PlayScenarioWindow actual = null;
            try
            {
                actual = ScriptableObject.CreateInstance<PlayScenarioWindow>();
                actual.ConfigureForTests(new PlayScenarioStore(Path.Combine(root, "definitions")));
                actual.ShowDialog = (_, __, ___, ____, _____) => 1;
                actual.Show();
                yield return null;
                actual.CreateGUI();
                var oldLeft = (PlayScenarioImportWorker)actual.rootVisualElement.Q<TextField>("soakBaselinePath").userData;
                var oldRight = (PlayScenarioImportWorker)actual.rootVisualElement.Q<TextField>("soakCandidatePath").userData;
                actual.CreateGUI();
                Assert.IsTrue(oldLeft.IsDisposed && oldRight.IsDisposed);
                Assert.AreEqual(1, actual.rootVisualElement.Query<Foldout>("playerSoakComparison").ToList().Count);
                var left = (PlayScenarioImportWorker)actual.rootVisualElement.Q<TextField>("soakBaselinePath").userData;
                var right = (PlayScenarioImportWorker)actual.rootVisualElement.Q<TextField>("soakCandidatePath").userData;
                typeof(UnityEditor.EditorWindow).GetProperty("hasUnsavedChanges").SetValue(actual, false);
                actual.Close();
                yield return null;
                Assert.IsTrue(left.IsDisposed && right.IsDisposed);
                Assert.IsFalse(left.IsPumping || right.IsPumping);
            }
            finally
            {
                if (actual != null)
                {
                    typeof(UnityEditor.EditorWindow).GetProperty("hasUnsavedChanges").SetValue(actual, false);
                    UnityEngine.Object.DestroyImmediate(actual);
                }
                UnityEditor.SessionState.SetString(PlayScenarioWindow.SessionJobKey, previousJob);
                UnityEditor.SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, previousMode);
            }
        }

        [UnityTest]
        public IEnumerator MinimumWindowKeepsChartFullyViewableWithExpandedMetadataAndLongSources()
        {
            string leftSource = Save("baseline-" + new string('x', 112), 30, 0);
            string rightSource = Save("candidate-" + new string('x', 112), 30, 7);
            Assert.Greater(leftSource.Length, 180, "The regression must use a source long enough to wrap in the original narrow column.");
            string previousJob = UnityEditor.SessionState.GetString(PlayScenarioWindow.SessionJobKey, "");
            bool previousMode = UnityEditor.SessionState.GetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
            UnityEditor.SessionState.SetString(PlayScenarioWindow.SessionJobKey, "");
            UnityEditor.SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
            PlayScenarioWindow actual = null;
            try
            {
                actual = ScriptableObject.CreateInstance<PlayScenarioWindow>();
                actual.ConfigureForTests(new PlayScenarioStore(Path.Combine(root, "definitions")));
                actual.ShowDialog = (_, __, ___, ____, _____) => 1;
                actual.Show();
                // Show restores this window type's saved geometry before the test requests its viewport.
                actual.position = new Rect(100, 100, 850, 560);
                yield return TestUtilities.WaitUntil(
                    () =>
                    {
                        actual.Repaint();
                        var bounds = actual.rootVisualElement.worldBound;
                        return Math.Abs(bounds.width - 850) <= 2 && bounds.height > 0 && bounds.height <= 562;
                    },
                    "the shown Play Scenarios window to apply its requested 850x560 viewport"
                );
                actual.CreateGUI();
                actual.ChoosePlayerSoakComparisonFile = side => side == "Baseline" ? leftSource : rightSource;
                var elements = actual.rootVisualElement;
                Click(elements, "scenario-tab-results");
                elements.Q<Foldout>("scenarioResultTools").value = true;
                elements.Q<Foldout>("playerSoakComparison").value = true;
                var metadata = elements.Q<Foldout>("soakComparisonMetadata");
                metadata.value = true;
                Click(elements, "chooseSoakBaseline");
                Click(elements, "chooseSoakCandidate");
                yield return PlayScenarioImportTestPump.Wait((PlayScenarioImportWorker)elements.Q<TextField>("soakBaselinePath").userData);
                yield return PlayScenarioImportTestPump.Wait((PlayScenarioImportWorker)elements.Q<TextField>("soakCandidatePath").userData);
                var outer = elements.Q<ScrollView>("scenarioResultsScroll");
                var inner = elements.Q<ScrollView>("soakComparisonScroll");
                var metadataScroll = elements.Q<ScrollView>("soakComparisonMetadataScroll");
                var chart = elements.Q<PlayScenarioSoakComparisonChart>("soakComparisonChart");
                var geometryTargets = new VisualElement[] { elements, outer.contentViewport, inner.contentViewport, metadataScroll, metadata, chart };
                Rect[] previousBounds = null;
                yield return TestUtilities.WaitUntil(
                    () =>
                    {
                        actual.Repaint();
                        Rect[] bounds = geometryTargets.Select(element => element.worldBound).ToArray();
                        bool visible = geometryTargets.All(element => element.panel != null && element.worldBound.width > 0 && element.worldBound.height > 0);
                        bool stable = previousBounds != null && bounds.SequenceEqual(previousBounds);
                        previousBounds = bounds;
                        return visible && stable;
                    },
                    "the visible Results comparison and expanded metadata geometry to settle"
                );
                Assert.AreEqual(850, elements.worldBound.width, 2);
                Assert.LessOrEqual(elements.worldBound.height, 560 + 2);
                Assert.GreaterOrEqual(inner.contentViewport.worldBound.height, 180);
                Assert.LessOrEqual(metadataScroll.worldBound.height, 141);
                Assert.LessOrEqual(metadata.worldBound.height, 170);
                Assert.IsFalse(elements.Q<Label>("soakBaselineState").displayTooltipWhenElided);
                Assert.IsFalse(elements.Q<Label>("soakCandidateState").displayTooltipWhenElided);
                Assert.AreEqual(leftSource, elements.Q<Label>("soakBaselineState").tooltip);
                Assert.AreEqual(rightSource, elements.Q<Label>("soakCandidateState").tooltip);
                Assert.LessOrEqual(elements.Q<Label>("soakBaselineState").worldBound.height, 21);
                outer.ScrollTo(inner);
                yield return null;
                inner.ScrollTo(chart);
                yield return null;
                yield return null;
                AssertFullyVisible(chart.worldBound, inner.contentViewport.worldBound, "The complete chart must fit inside the comparison viewport.");
                AssertFullyVisible(chart.worldBound, outer.contentViewport.worldBound, "The results viewport must not clip the comparison chart.");
                AssertFullyVisible(outer.worldBound, elements.worldBound, "The results scroller must stay inside the minimum window.");
                Assert.Greater(chart.CachedVertices.Length, 0);
                Assert.IsTrue(metadata.value, "Imports and layout must preserve the user's expanded metadata state.");
                inner.ScrollTo(metadataScroll);
                yield return null;
                var lastDetail = elements.Q("soakComparisonSummary").Query<Label>().ToList().Last();
                metadataScroll.ScrollTo(lastDetail);
                yield return null;
                AssertFullyVisible(lastDetail.worldBound, metadataScroll.contentViewport.worldBound, "Expanded metadata must remain independently scrollable.");
                elements.Q<DropdownField>("soakComparisonMetric").value = "object_count";
                yield return null;
                Assert.IsTrue(metadata.value, "Changing measurements must not reset expanded state.");
                inner.ScrollTo(chart);
                yield return null;
                AssertFullyVisible(chart.worldBound, inner.contentViewport.worldBound, "Metadata must not steal chart space after a measurement switch.");
            }
            finally
            {
                if (actual != null)
                {
                    typeof(UnityEditor.EditorWindow).GetProperty("hasUnsavedChanges").SetValue(actual, false);
                    UnityEngine.Object.DestroyImmediate(actual);
                }
                UnityEditor.SessionState.SetString(PlayScenarioWindow.SessionJobKey, previousJob);
                UnityEditor.SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, previousMode);
            }
        }

        private static void AssertFullyVisible(Rect element, Rect viewport, string message)
        {
            Assert.GreaterOrEqual(element.xMin, viewport.xMin - 1, message);
            Assert.GreaterOrEqual(element.yMin, viewport.yMin - 1, message);
            Assert.LessOrEqual(element.xMax, viewport.xMax + 1, message);
            Assert.LessOrEqual(element.yMax, viewport.yMax + 1, message);
        }

        // QA can export the same cached mesh used by UITK without native screen automation.
        // This is chart geometry evidence, not a screenshot of the surrounding Editor layout.
        internal static void WriteChartPreview(PlayScenarioSoakComparisonChart chart, string file, int width = 320, int height = 160)
        {
            chart.Rebuild(width, height);
            var texture = new Texture2D(width, height, TextureFormat.RGBA32, false);
            try
            {
                Color32[] pixels = Enumerable.Repeat(new Color32(30, 30, 30, 255), width * height).ToArray();
                Vertex[] vertices = chart.CachedVertices;
                ushort[] indices = chart.CachedIndices;
                for (int index = 0; index < indices.Length; index += 3)
                {
                    Vector2 a = vertices[indices[index]].position;
                    Vector2 b = vertices[indices[index + 1]].position;
                    Vector2 c = vertices[indices[index + 2]].position;
                    int minX = Math.Max(0, Mathf.FloorToInt(Math.Min(a.x, Math.Min(b.x, c.x))));
                    int maxX = Math.Min(width - 1, Mathf.CeilToInt(Math.Max(a.x, Math.Max(b.x, c.x))));
                    int minY = Math.Max(0, Mathf.FloorToInt(Math.Min(a.y, Math.Min(b.y, c.y))));
                    int maxY = Math.Min(height - 1, Mathf.CeilToInt(Math.Max(a.y, Math.Max(b.y, c.y))));
                    Color32 tint = vertices[indices[index]].tint;
                    for (int y = minY; y <= maxY; y++)
                    for (int x = minX; x <= maxX; x++)
                    {
                        var point = new Vector2(x + 0.5f, y + 0.5f);
                        float ab = Cross(b - a, point - a),
                            bc = Cross(c - b, point - b),
                            ca = Cross(a - c, point - c);
                        if (!(ab >= 0 && bc >= 0 && ca >= 0 || ab <= 0 && bc <= 0 && ca <= 0))
                            continue;
                        int pixel = (height - 1 - y) * width + x;
                        Color32 prior = pixels[pixel];
                        pixels[pixel] = new Color32(
                            (byte)((tint.r * tint.a + prior.r * (255 - tint.a)) / 255),
                            (byte)((tint.g * tint.a + prior.g * (255 - tint.a)) / 255),
                            (byte)((tint.b * tint.a + prior.b * (255 - tint.a)) / 255),
                            255
                        );
                    }
                }
                texture.SetPixels32(pixels);
                texture.Apply();
                File.WriteAllBytes(file, texture.EncodeToPNG());
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
            }

            float Cross(Vector2 left, Vector2 right) => left.x * right.y - left.y * right.x;
        }

        [Test]
        public void CachedChartMeshExportsARealBoundedImageWithoutReadingArtifacts()
        {
            using (var chart = new PlayScenarioSoakComparisonChart())
            {
                chart.SetData(PlayScenarioSoakComparison.Create(Snapshot(5), Snapshot(5, 3)), "managed_bytes");
                string file = Path.Combine(root, "chart.png");
                WriteChartPreview(chart, file);
                byte[] bytes = File.ReadAllBytes(file);
                CollectionAssert.AreEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 }, bytes.Take(8).ToArray());
                Assert.Greater(bytes.Length, 100);
            }
        }
    }
}
