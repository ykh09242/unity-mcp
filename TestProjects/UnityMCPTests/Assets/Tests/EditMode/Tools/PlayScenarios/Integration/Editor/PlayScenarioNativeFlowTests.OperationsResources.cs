using System.Collections;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime;
using MCPForUnityTests.PlayScenarios.Integration.Runtime;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.PlayScenarios.Integration
{
    public partial class PlayScenarioNativeFlowTests
    {
        private const string ResourceProbeType = "MCPForUnityTests.PlayScenarios.Integration.Runtime.PlayScenarioResourceIntegrationProbe";
        private const string ResourceRevision = "qa-native-resource-flow";

        [UnityTest]
        public IEnumerator NativeServiceCleanupDestroysCloneUnsubscribesAndReleasesHandleBeforeCheck()
        {
            ConfigureNativeResources(cleanup: true, replacement: false, cancel: false);
            yield return new EnterPlayMode();
            IEnumerator flow = ExecuteNativeResourceFlow(cleanup: true, replacement: false, cancel: false);
            while (flow.MoveNext())
                yield return flow.Current;
            yield return new ExitPlayMode();
            PlayScenarioNativeEvidence.RecordExported(nameof(NativeServiceCleanupDestroysCloneUnsubscribesAndReleasesHandleBeforeCheck), NextId);
        }

        [UnityTest]
        public IEnumerator NativeServiceDetectsRegisteredCloneSubscriptionAndHandleLeaksInStoredReport()
        {
            ConfigureNativeResources(cleanup: false, replacement: false, cancel: false);
            yield return new EnterPlayMode();
            IEnumerator flow = ExecuteNativeResourceFlow(cleanup: false, replacement: false, cancel: false);
            while (flow.MoveNext())
                yield return flow.Current;
            yield return new ExitPlayMode();
            PlayScenarioNativeEvidence.RecordExported(nameof(NativeServiceDetectsRegisteredCloneSubscriptionAndHandleLeaksInStoredReport), NextId);
        }

        [UnityTest]
        public IEnumerator NativeServiceNewHandleIdentityFailsEvenWhenOldReleaseKeepsCountUnchanged()
        {
            ConfigureNativeResources(cleanup: false, replacement: true, cancel: false);
            yield return new EnterPlayMode();
            IEnumerator flow = ExecuteNativeResourceFlow(cleanup: false, replacement: true, cancel: false);
            while (flow.MoveNext())
                yield return flow.Current;
            yield return new ExitPlayMode();
            PlayScenarioNativeEvidence.RecordExported(nameof(NativeServiceNewHandleIdentityFailsEvenWhenOldReleaseKeepsCountUnchanged), NextId);
        }

        [UnityTest]
        public IEnumerator NativeServiceCancellationKeepsPrimaryOutcomeAndRecordsSecondaryResourceFailure()
        {
            ConfigureNativeResources(cleanup: false, replacement: false, cancel: true);
            yield return new EnterPlayMode();
            IEnumerator flow = ExecuteNativeResourceFlow(cleanup: false, replacement: false, cancel: true);
            while (flow.MoveNext())
                yield return flow.Current;
            yield return new ExitPlayMode();
            PlayScenarioNativeEvidence.RecordExported(nameof(NativeServiceCancellationKeepsPrimaryOutcomeAndRecordsSecondaryResourceFailure), NextId);
        }

        private static JObject ConfigureNativeResources(bool cleanup, bool replacement, bool cancel)
        {
            var scene = EditorSceneManager.OpenScene(Hardening, OpenSceneMode.Single);
            var probe = new GameObject("ResourceProbe").AddComponent<PlayScenarioResourceIntegrationProbe>();
            probe.ReplacementOnly = replacement;
            Assert.That(EditorSceneManager.SaveScene(scene), Is.True);
            var steps = new JArray(ObjectStep("acquire", "click_ui", "ResourceProbe/Canvas/Acquire", 5));
            if (cancel)
                steps.Add(ObjectStep("pending", "wait_object", "ResourceNeverAppears", 20));
            var definition = new JObject
            {
                ["name"] = Context.NextName,
                ["poll_interval_ms"] = 100,
                ["setup_steps"] = new JArray(SceneStep("setup", "load_scene", Hardening)),
                ["steps"] = steps,
                ["resources"] = new JObject { ["enabled"] = true },
            };
            if (cleanup)
            {
                JObject released = ObjectStep("released", "wait_object", "ResourceProbe", 5);
                released["component"] = ResourceProbeType;
                released["property"] = new JObject { ["path"] = "Released", ["equals"] = true };
                released["stable_for_ms"] = 100;
                definition["cleanup_steps"] = new JArray(ObjectStep("release", "click_ui", "ResourceProbe/Canvas/Release", 5), released);
            }
            Command(new JObject { ["action"] = "save", ["scenario"] = definition });
            SessionState.SetString(ContextKey + ".ResourceDefinitionHash", PlayScenarioReproduction.Hash(PlayScenarioDefinition.Parse(definition)));
            return definition;
        }

        private static IEnumerator ExecuteNativeResourceFlow(bool cleanup, bool replacement, bool cancel)
        {
            if (replacement)
                PlayScenarioResourceIntegrationProbe.BaselineHandle = PlayScenarioResourceTracker.RegisterHandle();
            int before = PlayScenarioResourceTracker.Capture().HandleCount;
            Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = Context.NextName,
                    ["job_id"] = NextId,
                    ["timeout_seconds"] = 30,
                    ["source_revision"] = ResourceRevision,
                }
            );
            if (cancel)
            {
                double deadline = Time.realtimeSinceStartupAsDouble + 10;
                while ((string)Status(NextId)["steps"].Last()["status"] != "running" && Time.realtimeSinceStartupAsDouble < deadline)
                    yield return null;
                Assert.That((string)Status(NextId)["steps"].Last()["status"], Is.EqualTo("running"));
                Command(new JObject { ["action"] = "cancel", ["job_id"] = NextId });
            }
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That(
                (string)done["status"],
                Is.EqualTo(
                    cancel ? "cancelled"
                    : cleanup ? "succeeded"
                    : "failed"
                ),
                done.ToString()
            );
            var current = GameObject.Find("ResourceProbe").GetComponent<PlayScenarioResourceIntegrationProbe>();
            Assert.That(current.AcquireCount, Is.EqualTo(1));
            Assert.That(current.ReleaseCount, Is.EqualTo(cleanup ? 1 : 0));
            Assert.That(done["resource_checks"].Count(), Is.EqualTo(1), done.ToString());
            JToken check = done["resource_checks"][0];
            Assert.That((bool)check["passed"], Is.EqualTo(cleanup), check.ToString());
            Assert.That((int)check["new_handles"], Is.EqualTo(cleanup ? 0 : 1));
            Assert.That((int)check["new_scriptable_objects"], Is.EqualTo(cleanup || replacement ? 0 : 1));
            Assert.That((int)check["new_subscriptions"], Is.EqualTo(cleanup || replacement ? 0 : 1));
            if (replacement)
                Assert.That(
                    PlayScenarioResourceTracker.Capture().HandleCount,
                    Is.EqualTo(before),
                    "Native identity replacement must keep the scalar count unchanged."
                );
            if (!cleanup)
            {
                if (cancel)
                {
                    Assert.That((string)done["failure"]["code"], Is.EqualTo("cancelled"));
                    Assert.That(done["cleanup_failures"].Any(failure => (string)failure["code"] == "resource_assertion_failed"), Is.True, done.ToString());
                }
                else
                    Assert.That((string)done["failure"]["code"], Is.EqualTo("resource_assertion_failed"));
            }
            Assert.That((string)done["reproduction"]["source_revision"], Is.EqualTo(ResourceRevision));
            Assert.That((string)done["reproduction"]["unity_version"], Is.EqualTo(Application.unityVersion));
            Assert.That((string)done["reproduction"]["package_version"], Is.Not.Null.And.Not.Empty);
            Assert.That((string)done["reproduction"]["definition_hash"], Is.EqualTo(SessionState.GetString(ContextKey + ".ResourceDefinitionHash", "")));
            AssertReport(done);
            string reportPath = Path.Combine(Path.GetDirectoryName(Application.dataPath), (string)done["report_path"]);
            JObject stored = JObject.Parse(File.ReadAllText(reportPath));
            Assert.That(JToken.DeepEquals(stored["resource_checks"], done["resource_checks"]), Is.True, stored.ToString());
            Assert.That(JToken.DeepEquals(stored["reproduction"], done["reproduction"]), Is.True, stored.ToString());
            Assert.That(JToken.DeepEquals(stored["failure"], done["failure"]), Is.True, stored.ToString());
            Assert.That(JToken.DeepEquals(stored["cleanup_failures"], done["cleanup_failures"]), Is.True, stored.ToString());
            SessionState.EraseString(ContextKey + ".ResourceDefinitionHash");
            Debug.Log("PLAY_SCENARIO_QA_RESOURCE_REPORT_VERIFIED " + (string)done["job_id"]);
        }
    }
}
