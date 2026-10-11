using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Runtime;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityMcpLifecycleSample.Editor;

namespace UnityMcpLifecycleSample.Tests
{
    public sealed class LifecycleScenarioTests
    {
        private const string JobsKey = "LifecycleSample.TestJobs";

        private sealed class Acquisition
        {
            internal SessionResources Owner;
            internal SessionConfig Clone;
            internal System.IO.MemoryStream Stream;
            internal JObject Evidence;
        }

        [UnitySetUp]
        public IEnumerator Prepare()
        {
            Assert.That(EditorApplication.isPlayingOrWillChangePlaymode, Is.False);
            string marker = Path.Combine(Path.GetDirectoryName(Application.dataPath), "ProjectSettings/LifecycleSample.json");
            Assert.That(JObject.Parse(File.ReadAllText(marker))["sample"].Value<string>(), Is.EqualTo("play-scenario-lifecycle"));
            SessionState.SetString(JobsKey, "[]");
            LifecycleSampleSetup.Create();
            Assert.That(AssetDatabase.LoadAssetAtPath<SceneAsset>(LifecycleSampleSetup.Menu), Is.Not.Null, "The sample builder must create Menu.");
            Assert.That(AssetDatabase.LoadAssetAtPath<SceneAsset>(LifecycleSampleSetup.Game), Is.Not.Null);
            SampleEvidence.Prepare();
            yield return null;
        }

        [UnityTearDown]
        public IEnumerator Stop()
        {
            foreach (string id in JArray.Parse(SessionState.GetString(JobsKey, "[]")).Values<string>())
                SampleEvidence.Command(new JObject { ["action"] = "cancel", ["job_id"] = id });
            LifecycleScene.ReleaseRetained();
            if (EditorApplication.isPlaying)
                yield return new ExitPlayMode();
            SessionState.EraseString(JobsKey);
        }

        [UnityTest]
        public IEnumerator NormalFailureCancellationRecoverInOnePlaySession()
        {
            yield return new EnterPlayMode();
            PlayScenarioRegisteredResources baseline = PlayScenarioResourceTracker.Capture();
            var reports = new List<JObject>();
            var identities = new JArray();
            var seen = new HashSet<long>();
            yield return Run("sample-normal", 3, "succeeded", baseline, reports, identities, seen);
            yield return Run("sample-failure", 1, "timed_out", baseline, reports, identities, seen);
            Assert.That((string)reports.Last()["failure"]["code"], Is.EqualTo("target_missing"));
            yield return Run("sample-normal", 1, "succeeded", baseline, reports, identities, seen);
            yield return Run("sample-cancel", 1, "cancelled", baseline, reports, identities, seen, cancel: true);
            Assert.That((string)reports.Last()["failure"]["code"], Is.EqualTo("cancelled"));
            yield return Run("sample-normal", 1, "succeeded", baseline, reports, identities, seen);
            Assert.That(EditorApplication.isPlaying, Is.True);
            Assert.That(identities.Count, Is.EqualTo(7));
            Assert.That(seen.Count, Is.EqualTo(21));
            AssertBaseline(baseline);
            SampleEvidence.Record(nameof(NormalFailureCancellationRecoverInOnePlaySession), reports, identities);
        }

        [UnityTest]
        public IEnumerator RetainedOwnerRequiresExplicitReleaseBeforeCleanReentry()
        {
            yield return new EnterPlayMode();
            PlayScenarioRegisteredResources baseline = PlayScenarioResourceTracker.Capture();
            var reports = new List<JObject>();
            var identities = new JArray();
            var seen = new HashSet<long>();
            yield return Run("sample-retained", 1, "failed", baseline, reports, identities, seen, retained: true);
            Assert.That((string)reports.Last()["failure"]["code"], Is.EqualTo("resource_assertion_failed"));
            Assert.That(LifecycleScene.Retained.Count, Is.EqualTo(1));
            SessionResources owner = LifecycleScene.Retained[0];
            SessionConfig clone = owner.Clone;
            var stream = owner.Stream;
            int eventsBefore = owner.EventsReceived;
            SampleSignals.Publish();
            Assert.That(owner.EventsReceived, Is.EqualTo(eventsBefore + 1));
            Assert.That(clone != null, Is.True);
            Assert.That(stream.CanRead, Is.True);
            yield return Run("sample-release", 1, "succeeded", baseline, reports, identities, seen, release: true);
            Assert.That(clone == null, Is.True);
            Assert.That(stream.CanRead, Is.False);
            int eventsAfter = owner.EventsReceived;
            SampleSignals.Publish();
            Assert.That(owner.EventsReceived, Is.EqualTo(eventsAfter));
            Assert.That(LifecycleScene.Retained.Count, Is.Zero);
            AssertBaseline(baseline);
            identities[0]["explicit_release_verified"] = true;
            yield return Run("sample-normal", 1, "succeeded", baseline, reports, identities, seen);
            Assert.That(identities.Count, Is.EqualTo(2));
            Assert.That(seen.Count, Is.EqualTo(6));
            SampleEvidence.Record(nameof(RetainedOwnerRequiresExplicitReleaseBeforeCleanReentry), reports, identities);
        }

        [UnityTest]
        public IEnumerator SceneTeardownReleasesOrRetainsOwnerAccordingToPolicy()
        {
            yield return new EnterPlayMode();
            PlayScenarioRegisteredResources baseline = PlayScenarioResourceTracker.Capture();
            var reports = new List<JObject>();
            var identities = new JArray();
            var seen = new HashSet<long>();
            yield return Run("sample-teardown", 1, "succeeded", baseline, reports, identities, seen);
            yield return Run("sample-retained-teardown", 1, "failed", baseline, reports, identities, seen, retained: true);
            Assert.That((string)reports.Last()["failure"]["code"], Is.EqualTo("resource_assertion_failed"));
            Assert.That(LifecycleScene.Retained.Count, Is.EqualTo(1));
            SessionResources owner = LifecycleScene.Retained[0];
            SessionConfig clone = owner.Clone;
            var stream = owner.Stream;
            yield return Run("sample-release", 1, "succeeded", baseline, reports, identities, seen, release: true);
            Assert.That(clone == null, Is.True);
            Assert.That(stream.CanRead, Is.False);
            int eventsAfter = owner.EventsReceived;
            SampleSignals.Publish();
            Assert.That(owner.EventsReceived, Is.EqualTo(eventsAfter));
            Assert.That(LifecycleScene.Retained.Count, Is.Zero);
            AssertBaseline(baseline);
            identities[1]["explicit_release_verified"] = true;
            yield return Run("sample-normal", 1, "succeeded", baseline, reports, identities, seen);
            Assert.That(identities.Count, Is.EqualTo(3));
            Assert.That(seen.Count, Is.EqualTo(9));
            SampleEvidence.Record(nameof(SceneTeardownReleasesOrRetainsOwnerAccordingToPolicy), reports, identities);
        }

        private static IEnumerator Run(
            string name,
            int repeats,
            string expected,
            PlayScenarioRegisteredResources baseline,
            List<JObject> reports,
            JArray identities,
            HashSet<long> seen,
            bool cancel = false,
            bool retained = false,
            bool release = false
        )
        {
            if (!release)
                AssertBaseline(baseline);
            var source = AssetDatabase.LoadAssetAtPath<SessionConfig>(LifecycleSampleSetup.Config);
            Assert.That(EditorUtility.IsPersistent(source), Is.True);
            int health = source.Health;
            string id = Guid.NewGuid().ToString("N");
            var ids = JArray.Parse(SessionState.GetString(JobsKey, "[]"));
            ids.Add(id);
            SessionState.SetString(JobsKey, ids.ToString());
            SampleEvidence.Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = name,
                    ["job_id"] = id,
                    ["repeat_count"] = repeats,
                    ["timeout_seconds"] = 45,
                }
            );
            var observed = new HashSet<int>();
            var acquired = new List<Acquisition>();
            double deadline = Time.realtimeSinceStartupAsDouble + 50;
            while (Time.realtimeSinceStartupAsDouble < deadline)
            {
                JObject status = Status(id);
                if ((string)status["status"] != "running")
                    break;
                JToken held = status["steps"].FirstOrDefault(s => (string)s["name"] == "Game ready" && (string)s["status"] == "running");
                if (held != null && observed.Add((int)held["iteration"]))
                {
                    var controller = GameObject.Find("Lifecycle").GetComponent<LifecycleScene>();
                    SessionResources owner = controller.Owner;
                    Assert.That(owner, Is.Not.Null);
                    Assert.That(owner.Clone != null, Is.True);
                    Assert.That(owner.Stream.CanRead, Is.True);
                    PlayScenarioRegisteredResources snapshot = PlayScenarioResourceTracker.Capture();
                    Assert.That(snapshot.Error, Is.Null);
                    Assert.That(snapshot.RegistrationFailureCount, Is.Zero);
                    long[][] before = { baseline.ScriptableObjectIds, baseline.SubscriptionIds, baseline.HandleIds };
                    long[][] current = { snapshot.ScriptableObjectIds, snapshot.SubscriptionIds, snapshot.HandleIds };
                    var resourceIds = new JArray();
                    for (int kind = 0; kind < before.Length; kind++)
                    {
                        Assert.That(before[kind], Is.SubsetOf(current[kind]));
                        long[] added = current[kind].Except(before[kind]).ToArray();
                        Assert.That(added.Length, Is.EqualTo(1));
                        Assert.That(seen.Add(added[0]), Is.True);
                        resourceIds.Add(added[0]);
                    }
                    var detail = new JObject
                    {
                        ["job_id"] = id,
                        ["iteration"] = (int)held["iteration"],
                        ["owner_id"] = owner.OwnerId,
                        ["native_clone_id"] = owner.Clone.GetInstanceID(),
                        ["resource_ids"] = resourceIds,
                    };
                    acquired.Add(
                        new Acquisition
                        {
                            Owner = owner,
                            Clone = owner.Clone,
                            Stream = owner.Stream,
                            Evidence = detail,
                        }
                    );
                    identities.Add(detail);
                    Debug.Log("LIFECYCLE_SAMPLE_ACQUIRED " + id + " " + owner.OwnerId + " " + resourceIds.ToString(Newtonsoft.Json.Formatting.None));
                    if (cancel)
                        SampleEvidence.Command(new JObject { ["action"] = "cancel", ["job_id"] = id });
                }
                yield return new WaitForSecondsRealtime(0.1f);
            }
            JObject done = Status(id);
            Assert.That((string)done["status"], Is.Not.EqualTo("running"), done.ToString());
            SampleEvidence.Export(done);
            Assert.That((string)done["status"], Is.EqualTo(expected), done.ToString());
            reports.Add(done);
            Assert.That(acquired.Count, Is.EqualTo(release ? 0 : repeats));
            Assert.That((bool)done["runner_resources_released"], Is.True);
            Assert.That((string)done["phase"], Is.EqualTo("finished"));
            Assert.That(done["cleanup_failures"].Count(), Is.EqualTo(retained ? 1 : 0), done.ToString());
            Assert.That(done["resource_checks"].Count(), Is.EqualTo(repeats));
            foreach (JToken check in done["resource_checks"])
            {
                Assert.That((bool)check["passed"], Is.EqualTo(!retained));
                foreach (string key in new[] { "new_scriptable_objects", "new_subscriptions", "new_handles" })
                    Assert.That((int)check[key], Is.EqualTo(retained ? 1 : 0));
                if (retained)
                {
                    Assert.That(
                        check["retained_resources"].Select(r => (long)r["id"]),
                        Is.EquivalentTo(acquired.Single().Evidence["resource_ids"].Values<long>())
                    );
                    Assert.That(check["retained_resources"].All(r => (string)r["owner"] == "lifecycle-session:" + acquired.Single().Owner.OwnerId), Is.True);
                }
                else
                    Assert.That(check["retained_resources"].Count(), Is.Zero);
            }
            Assert.That((int)done["iteration_results_version"], Is.EqualTo(1));
            Assert.That(done["iteration_results"].Count(), Is.EqualTo(repeats));
            Assert.That(done["iteration_results"].All(i => (string)i["status"] == (expected == "succeeded" ? "passed" : expected)), Is.True);
            Assert.That(UnityEngine.SceneManagement.SceneManager.GetActiveScene().path, Is.EqualTo(LifecycleSampleSetup.Menu));
            Assert.That(source.Health, Is.EqualTo(health));
            foreach (Acquisition item in acquired)
            {
                int beforeEvents = item.Owner.EventsReceived;
                SampleSignals.Publish();
                Assert.That(item.Owner.EventsReceived, Is.EqualTo(beforeEvents + (retained ? 1 : 0)));
                Assert.That(item.Clone != null, Is.EqualTo(retained));
                Assert.That(item.Stream.CanRead, Is.EqualTo(retained));
                item.Evidence["native_clone_retained"] = item.Clone != null;
                item.Evidence["stream_open"] = item.Stream.CanRead;
                item.Evidence["event_delivered_after_return"] = item.Owner.EventsReceived != beforeEvents;
                item.Evidence["source_unchanged"] = source.Health == health;
            }
            if (!retained)
                AssertBaseline(baseline);
        }

        private static JObject Status(string id) => SampleEvidence.Command(new JObject { ["action"] = "status", ["job_id"] = id });

        private static void AssertBaseline(PlayScenarioRegisteredResources baseline)
        {
            PlayScenarioRegisteredResources current = PlayScenarioResourceTracker.Capture();
            Assert.That(current.Error, Is.Null);
            Assert.That(current.RegistrationFailureCount, Is.EqualTo(baseline.RegistrationFailureCount));
            Assert.That(current.ScriptableObjectIds, Is.EquivalentTo(baseline.ScriptableObjectIds));
            Assert.That(current.SubscriptionIds, Is.EquivalentTo(baseline.SubscriptionIds));
            Assert.That(current.HandleIds, Is.EquivalentTo(baseline.HandleIds));
        }
    }
}
