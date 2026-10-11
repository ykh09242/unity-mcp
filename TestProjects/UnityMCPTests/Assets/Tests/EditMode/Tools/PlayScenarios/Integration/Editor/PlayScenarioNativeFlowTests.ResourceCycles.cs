using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Runtime;
using MCPForUnityTests.PlayScenarios.Integration.Runtime;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.PlayScenarios.Integration
{
    public partial class PlayScenarioNativeFlowTests
    {
        [UnityTest]
        public IEnumerator NativeServiceTenResourceCyclesPreserveBaselineAcrossFailureAndCancellation()
        {
            JObject template = ConfigureNativeResources(cleanup: true, replacement: false, cancel: false);
            JObject held = ObjectStep("observe acquired", "wait_object", "ResourceProbe", 5);
            held["component"] = ResourceProbeType;
            held["property"] = new JObject { ["path"] = "Acquired", ["equals"] = true };
            held["stable_for_ms"] = 250;
            ((JArray)template["steps"]).Add(held);
            foreach (string name in new[] { Context.RepeatName, Context.MissingName, Context.WaitName, Context.NextName })
            {
                var definition = (JObject)template.DeepClone();
                definition["name"] = name;
                if (name == Context.MissingName)
                    ((JArray)definition["steps"]).Add(
                        new JObject
                        {
                            ["name"] = "fail after acquisition",
                            ["action"] = "reset_state",
                            ["reset_ids"] = new JArray("qa-missing-" + Context.MissingId),
                        }
                    );
                Command(new JObject { ["action"] = "save", ["scenario"] = definition });
            }
            SessionState.EraseString(ContextKey + ".ResourceDefinitionHash");
            yield return new EnterPlayMode();

            // These controls must survive every scene reload and job; a tracker reset cannot hide residual resources.
            PlayScenarioRegisteredResources original = PlayScenarioResourceTracker.Capture();
            var baselineClone = ScriptableObject.CreateInstance<PlayScenarioResourceIntegrationClone>();
            baselineClone.hideFlags = HideFlags.DontUnloadUnusedAsset;
            IDisposable subscription = null;
            IDisposable handle = null;
            var reports = new List<JObject>();
            var acquiredIds = new HashSet<long>();
            string recoveryId = Guid.NewGuid().ToString("N");
            try
            {
                PlayScenarioResourceTracker.RegisterScriptableObject(baselineClone, owner: "resource-cycle-baseline");
                subscription = PlayScenarioResourceTracker.RegisterSubscription(owner: "resource-cycle-baseline");
                handle = PlayScenarioResourceTracker.RegisterHandle(owner: "resource-cycle-baseline");
                PlayScenarioRegisteredResources baseline = PlayScenarioResourceTracker.Capture();
                yield return RunResourceCycles(Context.RepeatName, RepeatId, 6, "succeeded", baseline, acquiredIds, reports);
                yield return RunResourceCycles(Context.MissingName, MissingId, 1, "failed", baseline, acquiredIds, reports);
                yield return RunResourceCycles(Context.NextName, NextId, 1, "succeeded", baseline, acquiredIds, reports);
                yield return RunResourceCycles(Context.WaitName, WaitId, 1, "cancelled", baseline, acquiredIds, reports);
                yield return RunResourceCycles(Context.NextName, recoveryId, 1, "succeeded", baseline, acquiredIds, reports);
                Assert.That(EditorApplication.isPlaying, Is.True, "All ten cycles must share the same Play session.");
                Assert.That(baselineClone != null, Is.True, "The original native baseline clone must remain alive.");
                Assert.That(acquiredIds.Count, Is.EqualTo(30), "Each of ten cycles must acquire three distinct resource identities.");
            }
            finally
            {
                // The common teardown owns the other four IDs. Preserve this extra job's exported evidence, not its runner cache.
                MCPForUnity.Editor.Tools.ManagePlayScenario.HandleCommand(new JObject { ["action"] = "cancel", ["job_id"] = recoveryId });
                string extraReport = Path.Combine(Path.GetDirectoryName(Application.dataPath), "Library/MCPForUnity/PlayScenarioRuns", recoveryId + ".json");
                if (File.Exists(extraReport))
                    File.Delete(extraReport);
                handle?.Dispose();
                subscription?.Dispose();
                if (baselineClone != null)
                    UnityEngine.Object.DestroyImmediate(baselineClone);
            }
            AssertResourceIdentityBaseline(original);
            Assert.That(reports.Count, Is.EqualTo(5));
            Debug.Log("PLAY_SCENARIO_QA_TEN_RESOURCE_CYCLES_VERIFIED");
            PlayScenarioNativeEvidence.Record(nameof(NativeServiceTenResourceCyclesPreserveBaselineAcrossFailureAndCancellation), reports.ToArray());
        }

        private static IEnumerator RunResourceCycles(
            string name,
            string id,
            int repeatCount,
            string expectedStatus,
            PlayScenarioRegisteredResources baseline,
            HashSet<long> acquiredIds,
            List<JObject> reports
        )
        {
            AssertResourceIdentityBaseline(baseline);
            Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = name,
                    ["job_id"] = id,
                    ["repeat_count"] = repeatCount,
                    ["timeout_seconds"] = 30,
                }
            );
            var observedIterations = new HashSet<int>();
            double deadline = Time.realtimeSinceStartupAsDouble + 35;
            while (Time.realtimeSinceStartupAsDouble < deadline)
            {
                JObject before = Status(id);
                if ((string)before["status"] != "running")
                    break;
                JToken held = before["steps"].FirstOrDefault(step => (string)step["name"] == "observe acquired" && (string)step["status"] == "running");
                if (held != null && observedIterations.Add((int)held["iteration"]))
                {
                    PlayScenarioRegisteredResources acquired = PlayScenarioResourceTracker.Capture();
                    Assert.That(acquired.Error, Is.Null);
                    Assert.That(acquired.RegistrationFailureCount, Is.Zero);
                    long[][] originalIds = { baseline.ScriptableObjectIds, baseline.SubscriptionIds, baseline.HandleIds };
                    long[][] currentIds = { acquired.ScriptableObjectIds, acquired.SubscriptionIds, acquired.HandleIds };
                    var cycleIds = new List<long>();
                    for (int index = 0; index < originalIds.Length; index++)
                    {
                        Assert.That(originalIds[index], Is.SubsetOf(currentIds[index]), "A persistent baseline identity disappeared.");
                        long[] added = currentIds[index].Except(originalIds[index]).ToArray();
                        Assert.That(added.Length, Is.EqualTo(1), "Each cycle must own one live resource of each kind.");
                        Assert.That(acquiredIds.Add(added[0]), Is.True, "A new cycle must not reuse a registered resource identity.");
                        cycleIds.Add(added[0]);
                    }
                    for (int read = 0; read < 20; read++)
                    {
                        JObject snapshot = Status(id);
                        Assert.That(JToken.DeepEquals(snapshot["query_counts"], before["query_counts"]), Is.True, "Status must not query the scene.");
                        Assert.That(JToken.DeepEquals(snapshot["steps"], before["steps"]), Is.True, "Status must not poll or advance any step.");
                    }
                    Debug.Log("PLAY_SCENARIO_QA_RESOURCE_CYCLE_ACQUIRED " + acquiredIds.Count / 3 + " ids=" + string.Join(",", cycleIds));
                    if (expectedStatus == "cancelled")
                        Command(new JObject { ["action"] = "cancel", ["job_id"] = id });
                }
                yield return null;
            }
            JObject done = Status(id);
            Assert.That((string)done["status"], Is.Not.EqualTo("running"), done.ToString());
            AssertReport(done); // Preserve a real failed report before the success assertions, including late-cycle regressions.
            string savedPath = Path.Combine(Path.GetDirectoryName(Application.dataPath), (string)done["report_path"]);
            JObject stored = JObject.Parse(File.ReadAllText(savedPath));
            foreach (
                string field in new[]
                {
                    "resource_checks",
                    "iteration_results_version",
                    "iteration_results",
                    "phase",
                    "repeat_count",
                    "runner_resources_released",
                    "failure",
                    "cleanup_failures",
                }
            )
                Assert.That(JToken.DeepEquals(stored[field], done[field]), Is.True, "Persisted " + field + " must match the completed run.");
            reports.Add(done);
            Assert.That((string)done["status"], Is.EqualTo(expectedStatus), done.ToString());
            Assert.That(observedIterations, Is.EquivalentTo(Enumerable.Range(1, repeatCount)));
            Assert.That((bool)done["runner_resources_released"], Is.True);
            Assert.That((string)done["phase"], Is.EqualTo("finished"));
            Assert.That(done["cleanup_failures"].Count(), Is.Zero, done.ToString());
            Assert.That(done["resource_checks"].Count(), Is.EqualTo(repeatCount));
            Assert.That(done["resource_checks"].Select(check => (int)check["iteration"]), Is.EqualTo(Enumerable.Range(1, repeatCount)));
            foreach (JToken check in done["resource_checks"])
            {
                Assert.That((bool)check["passed"], Is.True, check.ToString());
                Assert.That((int)check["new_scriptable_objects"], Is.Zero);
                Assert.That((int)check["new_subscriptions"], Is.Zero);
                Assert.That((int)check["new_handles"], Is.Zero);
                Assert.That(check["retained_resources"].Count(), Is.Zero);
            }
            Assert.That((int)done["iteration_results_version"], Is.EqualTo(1));
            Assert.That(done["iteration_results"].Count(), Is.EqualTo(repeatCount));
            Assert.That(done["iteration_results"].Select(iteration => (int)iteration["iteration"]), Is.EqualTo(Enumerable.Range(1, repeatCount)));
            string iterationStatus = expectedStatus == "succeeded" ? "passed" : expectedStatus;
            Assert.That(done["iteration_results"].All(iteration => (string)iteration["status"] == iterationStatus), Is.True, done.ToString());
            if (expectedStatus == "failed")
                Assert.That((string)done["failure"]["code"], Is.EqualTo("reset_participant_unavailable"));
            if (expectedStatus == "cancelled")
            {
                Assert.That((string)done["failure"]["code"], Is.EqualTo("cancelled"));
                double quietUntil = Time.realtimeSinceStartupAsDouble + 0.5;
                while (Time.realtimeSinceStartupAsDouble < quietUntil)
                    yield return null;
                JObject after = Status(id);
                Assert.That(JToken.DeepEquals(after["steps"], done["steps"]), Is.True, "Cancelled steps must remain inert after finalization.");
                Assert.That(JToken.DeepEquals(after["query_counts"], done["query_counts"]), Is.True);
            }
            AssertResourceIdentityBaseline(baseline);
            Assert.That(EditorApplication.isPlaying, Is.True);
        }

        private static void AssertResourceIdentityBaseline(PlayScenarioRegisteredResources expected)
        {
            PlayScenarioRegisteredResources actual = PlayScenarioResourceTracker.Capture();
            Assert.That(actual.Error, Is.Null);
            Assert.That(actual.RegistrationFailureCount, Is.EqualTo(expected.RegistrationFailureCount));
            Assert.That(actual.ScriptableObjectIds, Is.EquivalentTo(expected.ScriptableObjectIds));
            Assert.That(actual.SubscriptionIds, Is.EquivalentTo(expected.SubscriptionIds));
            Assert.That(actual.HandleIds, Is.EquivalentTo(expected.HandleIds));
            Assert.That(actual.ScriptableObjectCount, Is.EqualTo(expected.ScriptableObjectCount));
            Assert.That(actual.SubscriptionCount, Is.EqualTo(expected.SubscriptionCount));
            Assert.That(actual.HandleCount, Is.EqualTo(expected.HandleCount));
        }
    }
}
