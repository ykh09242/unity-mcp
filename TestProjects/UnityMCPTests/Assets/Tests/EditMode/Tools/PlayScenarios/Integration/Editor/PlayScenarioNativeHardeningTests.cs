using System.Collections;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnityTests.PlayScenarios.Integration.Runtime;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.PlayScenarios.Integration
{
    public partial class PlayScenarioNativeFlowTests
    {
        private const string RuntimeComponent = "MCPForUnityTests.PlayScenarios.Integration.Runtime.PlayScenarioIntegrationBootstrap";

        [UnityTest]
        public IEnumerator DelayedErrorAfterPassedClickFailsDuringCompletionObservationAndRunsCleanup()
        {
            ConfigureHardening(delayedError: true);
            SaveHardening(new JArray(HardeningClick("start", "StartButton")), cleanup: true, screenshot: true);
            yield return new EnterPlayMode();
            LogAssert.Expect(LogType.Error, "PLAY_SCENARIO_QA_DELAYED_ERROR");
            StartHardening();
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("failed"), done.ToString());
            Assert.That((string)done["error"], Does.Contain("PLAY_SCENARIO_QA_DELAYED_ERROR"));
            Assert.That(done["steps"].Single(step => (string)step["stage"] == "main")["status"].Value<string>(), Is.EqualTo("passed"));
            AssertCleanup(done);
            JObject diagnostics = (JObject)done["failure_diagnostics"];
            Assert.That(diagnostics, Is.Not.Null, done.ToString());
            Assert.That((string)diagnostics["active_scene"], Is.EqualTo(Hardening));
            Assert.That((long)diagnostics["captured_unix_ms"], Is.GreaterThan(0));
            Assert.That(
                !string.IsNullOrEmpty((string)diagnostics["screenshot_path"]) || !string.IsNullOrEmpty((string)diagnostics["screenshot_error"]),
                Is.True
            );
            AssertReport(done);
        }

        [UnityTest]
        public IEnumerator AllowlistedPrefixDoesNotPermitLongerDelayedError()
        {
            ConfigureHardening(delayedError: true);
            SaveHardening(
                new JArray(HardeningClick("start", "StartButton")),
                cleanup: true,
                policy: new JObject { ["mode"] = "strict", ["allowed_messages"] = new JArray("PLAY_SCENARIO_QA_DELAYED") }
            );
            yield return new EnterPlayMode();
            LogAssert.Expect(LogType.Error, "PLAY_SCENARIO_QA_DELAYED_ERROR");
            StartHardening();
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("failed"), done.ToString());
            Assert.That((string)done["error"], Does.Contain("PLAY_SCENARIO_QA_DELAYED_ERROR"));
            AssertCleanup(done);
            AssertReport(done);
        }

        [UnityTest]
        public IEnumerator ExactAllowedDelayedErrorStillCompletesSuccessfully()
        {
            PrepareAllowedDelayedError("strict", true);
            yield return new EnterPlayMode();
            LogAssert.Expect(LogType.Error, "PLAY_SCENARIO_QA_DELAYED_ERROR");
            IEnumerator flow = VerifyAllowedDelayedError();
            while (flow.MoveNext())
                yield return flow.Current;
            yield return new ExitPlayMode();
        }

        [UnityTest]
        public IEnumerator LogOnlyDelayedErrorStillCompletesSuccessfully()
        {
            PrepareAllowedDelayedError("log_only", false);
            yield return new EnterPlayMode();
            LogAssert.Expect(LogType.Error, "PLAY_SCENARIO_QA_DELAYED_ERROR");
            IEnumerator flow = VerifyAllowedDelayedError();
            while (flow.MoveNext())
                yield return flow.Current;
            yield return new ExitPlayMode();
        }

        private static void PrepareAllowedDelayedError(string mode, bool allow)
        {
            ConfigureHardening(delayedError: true);
            var policy = new JObject { ["mode"] = mode };
            if (allow)
                policy["allowed_messages"] = new JArray("PLAY_SCENARIO_QA_DELAYED_ERROR");
            SaveHardening(new JArray(HardeningClick("start", "StartButton")), cleanup: true, policy: policy);
        }

        private static IEnumerator VerifyAllowedDelayedError()
        {
            StartHardening();
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("succeeded"), done.ToString());
            Assert.That(done["logs"].Count(log => (string)log["message"] == "PLAY_SCENARIO_QA_DELAYED_ERROR"), Is.EqualTo(1));
            Assert.That(HardeningBootstrap().StartClickCount, Is.EqualTo(1));
            AssertCleanup(done);
            AssertReport(done);
            Debug.Log("PLAY_SCENARIO_QA_ALLOWED_DELAYED_REPORT_VERIFIED " + NextId);
        }

        [UnityTest]
        public IEnumerator SerializedInitializationNeedsUninterruptedStabilityAndExplicitInactiveCountWorks()
        {
            JObject initialized = ObjectStep("initialized", "wait_object", "ScenarioBootstrap", 5);
            initialized["component"] = RuntimeComponent;
            initialized["property"] = new JObject { ["path"] = "Initialized", ["equals"] = true };
            initialized["stable_for_ms"] = 500;
            JObject inactive = ObjectStep("inactive-pair", "wait_object", "ScenarioBootstrap/InactiveDuplicate", 5);
            inactive["count"] = 2;
            inactive["active"] = false;
            JObject absent = ObjectStep("absent", "wait_object", "NeverCreated", 5);
            absent["count"] = 0;
            SaveHardening(new JArray(initialized, inactive, absent), cleanup: true);
            yield return new EnterPlayMode();
            StartHardening();
            yield return WaitMainRunning();
            var bootstrap = HardeningBootstrap();
            bootstrap.Initialized = true;
            yield return ObserveSeconds(0.25);
            Assert.That((string)Status(NextId)["status"], Is.EqualTo("running"));
            bootstrap.Initialized = false;
            yield return ObserveSeconds(0.25);
            bootstrap.Initialized = true;
            yield return ObserveSeconds(0.25);
            JObject transient = Status(NextId);
            Assert.That(
                (string)transient["steps"].Single(step => (string)step["stage"] == "main" && (string)step["name"] == "initialized")["status"],
                Is.EqualTo("running"),
                transient.ToString()
            );
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("succeeded"), done.ToString());
            Assert.That(done["steps"].All(step => (string)step["status"] == "passed"), Is.True);
            Assert.That(HardeningBootstrap().Initialized, Is.False);
            AssertReport(done);
        }

        [UnityTest]
        public IEnumerator SetupMainCleanupRepeatCapturesOneScalarMetricSnapshotPerIteration()
        {
            SaveHardening(new JArray(HardeningClick("start", "StartButton")), cleanup: true, metrics: true);
            yield return new EnterPlayMode();
            StartHardening(repeat: 2);
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("succeeded"), done.ToString());
            Assert.That(done["steps"].Select(step => (string)step["stage"]), Is.EqualTo(new[] { "setup", "main", "cleanup", "setup", "main", "cleanup" }));
            Assert.That(done["logs"].Count(log => (string)log["message"] == "PLAY_SCENARIO_QA_HARDENING_START"), Is.EqualTo(2));
            Assert.That(done["logs"].Count(log => (string)log["message"] == "PLAY_SCENARIO_QA_HARDENING_CLEANUP"), Is.EqualTo(2));
            JArray samples = (JArray)done["metrics"];
            Assert.That(samples, Is.Not.Null, done.ToString());
            Assert.That(samples.Count, Is.EqualTo(2));
            Assert.That((string)done["metrics_summary"], Does.StartWith("Insufficient post-warmup samples"));
            Assert.That(samples.Select(sample => (int)sample["iteration"]), Is.EqualTo(new[] { 1, 2 }));
            foreach (JObject sample in samples)
            {
                Assert.That(sample.Properties().All(property => property.Value is JValue), Is.True, sample.ToString());
                Assert.That((string)sample["error"], Is.Null, sample.ToString());
                Assert.That((long)sample["managed_bytes"], Is.GreaterThanOrEqualTo(0));
                Assert.That((long)sample["object_count"], Is.GreaterThan(0));
            }
            Assert.That((bool)done["runner_resources_released"], Is.True);
            AssertReport(done);
            JObject history = Command(new JObject { ["action"] = "reports", ["name"] = Context.NextName });
            Assert.That(history["reports"].Any(report => (string)report["job_id"] == NextId), Is.True, history.ToString());
        }

        [UnityTest]
        public IEnumerator CancellationRunsCleanupAndPreservesCancelledOutcome()
        {
            SaveHardening(new JArray(ObjectStep("pending", "wait_object", "NeverCreated", 20)), cleanup: true);
            yield return new EnterPlayMode();
            StartHardening();
            yield return WaitMainRunning();
            Command(new JObject { ["action"] = "cancel", ["job_id"] = NextId });
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("cancelled"), done.ToString());
            AssertCleanup(done);
            Assert.That((bool)done["runner_resources_released"], Is.True);
            AssertReport(done);
        }

        [UnityTest]
        public IEnumerator CancellationWithScreenshotAndEmptyCleanupFinalizesReportBeforeTerminalStatus()
        {
            SaveHardening(new JArray(ObjectStep("pending", "wait_object", "NeverCreated", 20)), cleanup: false, screenshot: true);
            yield return new EnterPlayMode();
            StartHardening();
            yield return WaitMainRunning();
            JObject cancellation = Command(new JObject { ["action"] = "cancel", ["job_id"] = NextId });
            if ((string)cancellation["status"] == "running")
            {
                Assert.That((string)cancellation["phase"], Is.EqualTo("finalizing"), cancellation.ToString());
                Assert.That((string)cancellation["report_path"], Is.Null);
                Assert.That((string)Status(NextId)["status"], Is.EqualTo("running"));
            }
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("cancelled"), done.ToString());
            Assert.That((bool)done["runner_resources_released"], Is.True);
            JObject diagnostics = (JObject)done["failure_diagnostics"];
            Assert.That(diagnostics, Is.Not.Null, done.ToString());
            if (!string.IsNullOrEmpty((string)diagnostics["screenshot_path"]))
                Assert.That((string)cancellation["status"], Is.EqualTo("running"), cancellation.ToString());
            else
                Assert.That((string)diagnostics["screenshot_error"], Is.Not.Null.And.Not.Empty);
            AssertReport(done);
        }

        [UnityTest]
        public IEnumerator StepTimeoutRunsCleanupAndPreservesTimedOutOutcome()
        {
            ConfigureHardening();
            SaveHardening(new JArray(ObjectStep("pending", "wait_object", "NeverCreated", 1)), cleanup: true);
            yield return new EnterPlayMode();
            IEnumerator flow = VerifyTimeoutCleanup();
            while (flow.MoveNext())
                yield return flow.Current;
            yield return new ExitPlayMode();
        }

        [UnityTest]
        public IEnumerator CleanupErrorPreservesOriginalTimeoutAndReportsCleanupFailure()
        {
            ConfigureHardening(cleanupError: true);
            SaveHardening(new JArray(ObjectStep("pending", "wait_object", "NeverCreated", 1)), cleanup: true);
            yield return new EnterPlayMode();
            LogAssert.Expect(LogType.Error, "PLAY_SCENARIO_QA_CLEANUP_ERROR");
            StartHardening();
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("timed_out"), done.ToString());
            Assert.That((string)done["error"], Does.Not.Contain("PLAY_SCENARIO_QA_CLEANUP_ERROR"));
            Assert.That(HardeningBootstrap().CleanupClickCount, Is.EqualTo(1));
            Assert.That((string)done["cleanup_error"], Does.Contain("PLAY_SCENARIO_QA_CLEANUP_ERROR"));
            AssertReport(done);
        }

        private static IEnumerator VerifyTimeoutCleanup()
        {
            StartHardening();
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("timed_out"), done.ToString());
            Assert.That((string)done["error"], Does.Not.Contain("PLAY_SCENARIO_QA_CLEANUP_ERROR"));
            Assert.That(HardeningBootstrap().CleanupClickCount, Is.EqualTo(1));
            AssertCleanup(done);
            AssertReport(done);
            Debug.Log("PLAY_SCENARIO_QA_TIMEOUT_CLEANUP_REPORT_VERIFIED " + NextId);
        }

        [Test]
        public void PreflightRemainsReadOnlyAndDefersRuntimeCreatedTargets()
        {
            Scene scene = EditorSceneManager.OpenScene(Hardening, OpenSceneMode.Single);
            string path = scene.path;
            int roots = scene.rootCount;
            bool dirty = scene.isDirty;
            var selected = Selection.activeObject;
            var definition = PlayScenarioDefinition.Parse(
                new JObject
                {
                    ["name"] = Context.NextName,
                    ["steps"] = new JArray(SceneStep("setup", "load_scene", Hardening), HardeningClick("future-start", "StartButton")),
                }
            );
            JObject result = UnityPlayScenarioHost.Preflight(definition);
            Assert.That((bool)result["success"], Is.True, result.ToString());
            Assert.That((bool)result["data"]["valid"], Is.True, result.ToString());
            Assert.That(result["data"]["checks"].Any(check => (string)check["status"] == "deferred"), Is.True, result.ToString());
            Assert.That(EditorApplication.isPlayingOrWillChangePlaymode, Is.False);
            Assert.That(SceneManager.GetActiveScene().path, Is.EqualTo(path));
            Assert.That(scene.rootCount, Is.EqualTo(roots));
            Assert.That(scene.isDirty, Is.EqualTo(dirty));
            Assert.That(Selection.activeObject, Is.SameAs(selected));
        }

        private static IEnumerator WaitMainRunning()
        {
            double deadline = Time.realtimeSinceStartupAsDouble + 10;
            while (Time.realtimeSinceStartupAsDouble < deadline)
            {
                JObject state = Status(NextId);
                if (state["steps"].Any(step => (string)step["stage"] == "main" && (string)step["status"] == "running"))
                    yield break;
                yield return null;
            }
            Assert.Fail("Main wait did not become running: " + Status(NextId));
        }

        private static IEnumerator ObserveSeconds(double seconds)
        {
            double until = Time.realtimeSinceStartupAsDouble + seconds;
            while (Time.realtimeSinceStartupAsDouble < until)
                yield return null;
        }

        private static void StartHardening(int repeat = 1) =>
            Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = Context.NextName,
                    ["job_id"] = NextId,
                    ["repeat_count"] = repeat,
                    ["timeout_seconds"] = 30,
                }
            );

        private static PlayScenarioIntegrationBootstrap HardeningBootstrap() =>
            GameObject.Find("ScenarioBootstrap").GetComponent<PlayScenarioIntegrationBootstrap>();

        private static void AssertCleanup(JObject done)
        {
            Assert.That(done["steps"].Any(step => (string)step["stage"] == "cleanup" && (string)step["status"] == "passed"), Is.True, done.ToString());
            Assert.That(HardeningBootstrap().CleanupClickCount, Is.EqualTo(1));
        }

        private static JObject HardeningClick(string name, string button) => ObjectStep(name, "click_ui", "ScenarioBootstrap/Canvas/" + button, 5);

        private static void SaveHardening(JArray main, bool cleanup, JObject policy = null, bool metrics = false, bool screenshot = false)
        {
            var definition = new JObject
            {
                ["name"] = Context.NextName,
                ["poll_interval_ms"] = 100,
                ["completion_stable_ms"] = 750,
                ["setup_steps"] = new JArray(SceneStep("setup", "load_scene", Hardening)),
                ["steps"] = main,
            };
            if (cleanup)
                definition["cleanup_steps"] = new JArray(HardeningClick("cleanup", "CleanupButton"));
            if (policy != null)
                definition["log_policy"] = policy;
            if (metrics)
                definition["metrics"] = new JObject { ["enabled"] = true, ["warmup_iterations"] = 0 };
            if (screenshot)
                definition["diagnostics"] = new JObject { ["screenshot_on_failure"] = true };
            Command(new JObject { ["action"] = "save", ["scenario"] = definition });
        }

        private static void ConfigureHardening(bool delayedError = false, bool cleanupError = false)
        {
            Scene scene = EditorSceneManager.OpenScene(Hardening, OpenSceneMode.Single);
            var bootstrap = scene.GetRootGameObjects().Single(root => root.name == "ScenarioBootstrap").GetComponent<PlayScenarioIntegrationBootstrap>();
            bootstrap.DelayedError = delayedError;
            bootstrap.CleanupError = cleanupError;
            Assert.That(EditorSceneManager.SaveScene(scene), Is.True);
        }
    }
}
