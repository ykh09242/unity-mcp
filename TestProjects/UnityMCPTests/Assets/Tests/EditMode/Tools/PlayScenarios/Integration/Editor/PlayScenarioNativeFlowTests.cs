using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools;
using MCPForUnityTests.PlayScenarios.Integration.Runtime;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using UnityEngine.UI;

namespace MCPForUnityTests.PlayScenarios.Integration
{
    public class PlayScenarioNativeFlowTests
    {
        private const string ContextKey = "MCPForUnity.Tests.PlayScenarioIntegration.Context";
        private static TestContext Context
        {
            get
            {
                string json = SessionState.GetString(ContextKey, "");
                return string.IsNullOrEmpty(json) ? null : JsonUtility.FromJson<TestContext>(json);
            }
        }
        private static string Menu => Context.Root + "/Menu.unity";
        private static string Game => Context.Root + "/Game.unity";
        private static string Missing => Context.Root + "/Missing.unity";
        private static string RepeatId => Context.RepeatId;
        private static string WaitId => Context.WaitId;
        private static string NextId => Context.NextId;
        private static string MissingId => Context.MissingId;

        [Serializable]
        private sealed class SceneData
        {
            public string Path;
            public bool Loaded;
            public bool Active;
        }

        [Serializable]
        private sealed class BuildData
        {
            public string Path;
            public bool Enabled;
        }

        [Serializable]
        private sealed class TestContext
        {
            public string Root;
            public string FolderGuid;
            public bool EmptyOriginal;
            public SceneData[] Scenes;
            public BuildData[] Builds;
            public bool OptionsEnabled;
            public int Options;
            public string RepeatName;
            public string WaitName;
            public string NextName;
            public string MissingName;
            public string RepeatId;
            public string WaitId;
            public string NextId;
            public string MissingId;
        }

        [UnitySetUp]
        public IEnumerator Prepare()
        {
            SessionState.SetBool(ContextKey + ".Owned", false);
            var current = (PlayScenarioEngine)typeof(PlayScenarioService).GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            if (current?.Running == true)
                Assert.Ignore("An active scenario owns the editor.");
            if (EditorApplication.isPlaying || EditorApplication.isPlayingOrWillChangePlaymode)
                Assert.Ignore("Integration tests start only in stable Edit Mode.");
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                if (scene.isDirty || (string.IsNullOrEmpty(scene.path) && (scene.rootCount != 0 || SceneManager.sceneCount != 1)))
                    Assert.Ignore("Existing dirty or nonempty untitled scenes are preserved.");
            }
            string guid = Guid.NewGuid().ToString("N");
            var context = new TestContext
            {
                Root = "Assets/PlayScenarioIntegration_" + guid,
                EmptyOriginal = SceneManager.sceneCount == 1 && string.IsNullOrEmpty(SceneManager.GetActiveScene().path),
                Scenes = EditorSceneManager
                    .GetSceneManagerSetup()
                    .Select(scene => new SceneData
                    {
                        Path = scene.path,
                        Loaded = scene.isLoaded,
                        Active = scene.isActive,
                    })
                    .ToArray(),
                Builds = EditorBuildSettings.scenes.Select(scene => new BuildData { Path = scene.path, Enabled = scene.enabled }).ToArray(),
                OptionsEnabled = EditorSettings.enterPlayModeOptionsEnabled,
                Options = (int)EditorSettings.enterPlayModeOptions,
                RepeatName = "qa-repeat-" + guid,
                WaitName = "qa-wait-" + guid,
                NextName = "qa-next-" + guid,
                MissingName = "qa-missing-" + guid,
                RepeatId = Guid.NewGuid().ToString("N"),
                WaitId = Guid.NewGuid().ToString("N"),
                NextId = Guid.NewGuid().ToString("N"),
                MissingId = Guid.NewGuid().ToString("N"),
            };
            SessionState.SetString(ContextKey, JsonUtility.ToJson(context));
            SessionState.SetBool(ContextKey + ".Owned", true);
            context.FolderGuid = AssetDatabase.CreateFolder("Assets", "PlayScenarioIntegration_" + guid);
            SessionState.SetString(ContextKey, JsonUtility.ToJson(context));
            EditorSettings.enterPlayModeOptionsEnabled = false;
            string[] paths = { Menu, Game, Missing };
            for (int i = 0; i < paths.Length; i++)
            {
                Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                var bootstrap = new GameObject("ScenarioBootstrap").AddComponent<PlayScenarioIntegrationBootstrap>();
                bootstrap.Role = (PlayScenarioIntegrationBootstrap.SceneRole)i;
                bootstrap.GameScenePath = Game;
                Assert.That(EditorSceneManager.SaveScene(scene, paths[i]), Is.True);
            }
            EditorBuildSettings.scenes = context
                .Builds.Select(scene => new EditorBuildSettingsScene(scene.Path, scene.Enabled))
                .Concat(paths.Select(path => new EditorBuildSettingsScene(path, true)))
                .ToArray();
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            yield return null;
        }

        [UnityTearDown]
        public IEnumerator Stop()
        {
            if (!SessionState.GetBool(ContextKey + ".Owned", false))
                yield break;
            TestContext context = Context;
            if (context == null)
                yield break;
            try
            {
                foreach (string id in new[] { context.RepeatId, context.WaitId, context.NextId, context.MissingId })
                    ManagePlayScenario.HandleCommand(new JObject { ["action"] = "cancel", ["job_id"] = id });
                if (EditorApplication.isPlaying)
                    yield return new ExitPlayMode();
            }
            finally
            {
                try
                {
                    Cleanup(context);
                }
                finally
                {
                    SessionState.EraseString(ContextKey);
                    SessionState.EraseBool(ContextKey + ".Owned");
                }
            }
        }

        private static void Cleanup(TestContext context)
        {
            try
            {
                EditorBuildSettings.scenes = context.Builds.Select(scene => new EditorBuildSettingsScene(scene.Path, scene.Enabled)).ToArray();
                bool changedExternalScene = false;
                for (int i = 0; i < SceneManager.sceneCount; i++)
                {
                    Scene scene = SceneManager.GetSceneAt(i);
                    if (scene.isDirty && !scene.path.StartsWith(context.Root + "/", StringComparison.Ordinal))
                        changedExternalScene = true;
                }
                if (!changedExternalScene)
                {
                    if (context.EmptyOriginal)
                        EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                    else
                        EditorSceneManager.RestoreSceneManagerSetup(
                            context
                                .Scenes.Select(scene => new SceneSetup
                                {
                                    path = scene.Path,
                                    isLoaded = scene.Loaded,
                                    isActive = scene.Active,
                                })
                                .ToArray()
                        );
                    if (
                        context.Root.StartsWith("Assets/PlayScenarioIntegration_", StringComparison.Ordinal)
                        && Guid.TryParseExact(context.Root.Substring("Assets/PlayScenarioIntegration_".Length), "N", out _)
                        && !string.IsNullOrEmpty(context.FolderGuid)
                        && AssetDatabase.AssetPathToGUID(context.Root) == context.FolderGuid
                    )
                        AssetDatabase.DeleteAsset(context.Root);
                }
                else
                    Debug.LogWarning("Play scenario integration cleanup preserved a changed external scene and its loaded assets.");
                foreach (string name in new[] { context.RepeatName, context.WaitName, context.NextName, context.MissingName })
                    ManagePlayScenario.HandleCommand(new JObject { ["action"] = "delete", ["name"] = name });
                string project = Path.GetDirectoryName(Application.dataPath);
                foreach (string id in new[] { context.RepeatId, context.WaitId, context.NextId, context.MissingId })
                    if (Guid.TryParseExact(id, "N", out _))
                    {
                        string reportPath = Path.Combine(project, "Library/MCPForUnity/PlayScenarioRuns", id + ".json");
                        if (File.Exists(reportPath))
                            File.Delete(reportPath);
                    }
            }
            finally
            {
                EditorSettings.enterPlayModeOptions = (EnterPlayModeOptions)context.Options;
                EditorSettings.enterPlayModeOptionsEnabled = context.OptionsEnabled;
            }
        }

        [UnityTest]
        public IEnumerator SavedRunFromEditModeSurvivesReloadAndRepeatsMenuClickGamePlayerTwice()
        {
            Save(Context.RepeatName, RepeatSteps());
            JObject started = Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = Context.RepeatName,
                    ["repeat_count"] = 2,
                    ["job_id"] = RepeatId,
                    ["timeout_seconds"] = 30,
                }
            );
            Assert.That((string)started["phase"], Is.EqualTo("starting"));
            yield return new EnterPlayMode();
            yield return Finish(RepeatId);
            JObject done = Status(RepeatId);
            Assert.That((string)done["status"], Is.EqualTo("succeeded"), done.ToString());
            Assert.That(done["steps"].Count(), Is.EqualTo(8));
            Assert.That(done["steps"].All(step => (string)step["status"] == "passed"), Is.True);
            Assert.That(SceneManager.GetActiveScene().path, Is.EqualTo(Game));
            Assert.That(GameObject.Find("Player"), Is.Not.Null);
            Assert.That(done["logs"].Count(log => (string)log["message"] == "PLAY_SCENARIO_QA_CLICK"), Is.EqualTo(2));
            Assert.That(done["logs"].Count(log => (string)log["message"] == "PLAY_SCENARIO_QA_PLAYER"), Is.EqualTo(2));
            AssertReport(done);
            JObject previous = Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = Context.RepeatName,
                    ["repeat_count"] = 2,
                    ["job_id"] = RepeatId,
                    ["timeout_seconds"] = 30,
                }
            );
            Assert.That((string)previous["status"], Is.EqualTo("succeeded"));
            Assert.That((int)previous["cursor"], Is.EqualTo(8));
            Debug.Log("PLAY_SCENARIO_QA_STARTUP_REPEAT_VERIFIED");
        }

        [UnityTest]
        public IEnumerator StatusDoesNotPollCancelStopsPollingAndFreshRunSucceeds()
        {
            Save(Context.WaitName, new JArray(SceneStep("load", "load_scene", Menu), ObjectStep("missing", "wait_object", "NeverAppears", 20)));
            Save(Context.NextName, RepeatSteps());
            yield return new EnterPlayMode();
            Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = Context.WaitName,
                    ["job_id"] = WaitId,
                    ["timeout_seconds"] = 30,
                }
            );
            double deadline = Time.realtimeSinceStartupAsDouble + 15;
            while ((string)Status(WaitId)["steps"][1]["status"] != "running" && Time.realtimeSinceStartupAsDouble < deadline)
                yield return null;
            JObject before = Status(WaitId);
            Assert.That((string)before["steps"][1]["status"], Is.EqualTo("running"), before.ToString());
            int polls = (int)before["steps"][1]["poll_count"];
            for (int i = 0; i < 20; i++)
                Assert.That((int)Status(WaitId)["steps"][1]["poll_count"], Is.EqualTo(polls));
            JObject cancelled = Command(new JObject { ["action"] = "cancel", ["job_id"] = WaitId });
            Assert.That((string)cancelled["status"], Is.EqualTo("cancelled"));
            deadline = Time.realtimeSinceStartupAsDouble + 0.5;
            while (Time.realtimeSinceStartupAsDouble < deadline)
                yield return null;
            Assert.That((int)Status(WaitId)["steps"][1]["poll_count"], Is.EqualTo(polls));
            Assert.That(EditorApplication.isPlaying, Is.True);
            AssertReport(cancelled);
            Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = Context.NextName,
                    ["job_id"] = NextId,
                    ["timeout_seconds"] = 30,
                }
            );
            yield return Finish(NextId);
            Assert.That((string)Status(NextId)["status"], Is.EqualTo("succeeded"));
            AssertReport(Status(NextId));
            Debug.Log("PLAY_SCENARIO_QA_CANCEL_STATUS_FRESH_VERIFIED");
        }

        [UnityTest]
        public IEnumerator MissingPlayerTimesOutCapturesLogAndSkipsPendingStep()
        {
            Save(
                Context.MissingName,
                new JArray(
                    SceneStep("load", "load_scene", Missing),
                    ObjectStep("missing", "wait_object", "Player", 1),
                    ObjectStep("skipped", "wait_object", "NeverAppears", 1)
                )
            );
            yield return new EnterPlayMode();
            Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = Context.MissingName,
                    ["job_id"] = MissingId,
                    ["timeout_seconds"] = 30,
                }
            );
            yield return Finish(MissingId);
            JObject done = Status(MissingId);
            Assert.That((string)done["status"], Is.EqualTo("timed_out"), done.ToString());
            Assert.That((string)done["steps"][0]["status"], Is.EqualTo("passed"));
            Assert.That((string)done["steps"][1]["status"], Is.EqualTo("timed_out"));
            Assert.That((string)done["steps"][2]["status"], Is.EqualTo("skipped"));
            Assert.That(done["logs"].Any(log => (string)log["message"] == "PLAY_SCENARIO_QA_MISSING_PLAYER"), Is.True);
            Assert.That(EditorApplication.isPlaying, Is.True);
            AssertReport(done);
            Debug.Log("PLAY_SCENARIO_QA_TIMEOUT_LOG_REPORT_VERIFIED");
        }

        [UnityTest]
        public IEnumerator ThrowingButtonListenerFailsClickWithoutRetryAndPersistsError()
        {
            ConfigureMenu(throwOnClick: true);
            Save(
                Context.NextName,
                new JArray(
                    SceneStep("menu", "load_scene", Menu),
                    ObjectStep("start", "click_ui", "ScenarioBootstrap/Canvas/StartButton", 5),
                    ObjectStep("skipped", "wait_object", "Player", 1)
                )
            );
            yield return new EnterPlayMode();
            LogAssert.Expect(LogType.Exception, "InvalidOperationException: PLAY_SCENARIO_QA_LISTENER_FAILURE");
            Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = Context.NextName,
                    ["job_id"] = NextId,
                    ["timeout_seconds"] = 30,
                }
            );
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("failed"), done.ToString());
            Assert.That((string)done["steps"][0]["status"], Is.EqualTo("passed"));
            Assert.That((string)done["steps"][1]["status"], Is.EqualTo("failed"));
            Assert.That((int)done["steps"][1]["poll_count"], Is.EqualTo(1));
            Assert.That((string)done["steps"][2]["status"], Is.EqualTo("skipped"));
            Assert.That((string)done["error"], Does.Contain("PLAY_SCENARIO_QA_LISTENER_FAILURE"));
            Assert.That(
                done["logs"].Any(log => (string)log["type"] == "Exception" && ((string)log["message"]).Contains("PLAY_SCENARIO_QA_LISTENER_FAILURE")),
                Is.True
            );
            Assert.That(done["logs"].Count(log => (string)log["message"] == "PLAY_SCENARIO_QA_CLICK"), Is.EqualTo(1));
            AssertReport(done);
        }

        [UnityTest]
        public IEnumerator DisabledButtonWaitsUntilEnabledThenClicksOnce()
        {
            ConfigureMenu(disableButton: true);
            Save(Context.NextName, RepeatSteps());
            yield return new EnterPlayMode();
            Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = Context.NextName,
                    ["job_id"] = NextId,
                    ["timeout_seconds"] = 30,
                }
            );
            double deadline = Time.realtimeSinceStartupAsDouble + 5;
            while (
                (string)Status(NextId)["status"] == "running"
                && (int)Status(NextId)["steps"][1]["poll_count"] < 3
                && Time.realtimeSinceStartupAsDouble < deadline
            )
                yield return null;
            JObject waiting = Status(NextId);
            Assert.That((string)waiting["status"], Is.EqualTo("running"), waiting.ToString());
            Assert.That((int)waiting["steps"][1]["poll_count"], Is.GreaterThanOrEqualTo(3));
            Assert.That(waiting["logs"].Any(log => (string)log["message"] == "PLAY_SCENARIO_QA_CLICK"), Is.False);
            GameObject.Find("ScenarioBootstrap/Canvas/StartButton").GetComponent<Button>().enabled = true;
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("succeeded"), done.ToString());
            Assert.That(done["steps"].All(step => (string)step["status"] == "passed"), Is.True);
            Assert.That(done["logs"].Count(log => (string)log["message"] == "PLAY_SCENARIO_QA_CLICK"), Is.EqualTo(1));
            Assert.That(GameObject.Find("Player"), Is.Not.Null);
            AssertReport(done);
        }

        [UnityTest]
        public IEnumerator DisabledButtonUsesStepTimeoutAndSkipsRemainingSteps()
        {
            ConfigureMenu(disableButton: true);
            Save(
                Context.NextName,
                new JArray(
                    SceneStep("menu", "load_scene", Menu),
                    ObjectStep("start", "click_ui", "ScenarioBootstrap/Canvas/StartButton", 1),
                    ObjectStep("skipped", "wait_object", "Player", 1)
                )
            );
            yield return new EnterPlayMode();
            Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = Context.NextName,
                    ["job_id"] = NextId,
                    ["timeout_seconds"] = 30,
                }
            );
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("timed_out"), done.ToString());
            Assert.That((string)done["steps"][1]["status"], Is.EqualTo("timed_out"));
            Assert.That((int)done["steps"][1]["poll_count"], Is.GreaterThan(1));
            Assert.That((long)done["steps"][1]["finished_unix_ms"] - (long)done["steps"][1]["started_unix_ms"], Is.GreaterThanOrEqualTo(1000));
            Assert.That((string)done["steps"][2]["status"], Is.EqualTo("skipped"));
            Assert.That(done["logs"].Any(log => (string)log["message"] == "PLAY_SCENARIO_QA_CLICK"), Is.False);
            AssertReport(done);
        }

        private static void ConfigureMenu(bool disableButton = false, bool throwOnClick = false)
        {
            Scene scene = EditorSceneManager.OpenScene(Menu, OpenSceneMode.Single);
            var bootstrap = scene.GetRootGameObjects().Single(root => root.name == "ScenarioBootstrap").GetComponent<PlayScenarioIntegrationBootstrap>();
            bootstrap.DisableStartButton = disableButton;
            bootstrap.ThrowOnStartClick = throwOnClick;
            Assert.That(EditorSceneManager.SaveScene(scene), Is.True);
        }

        private static IEnumerator Finish(string id)
        {
            double deadline = Time.realtimeSinceStartupAsDouble + 35;
            while ((string)Status(id)["status"] == "running" && Time.realtimeSinceStartupAsDouble < deadline)
                yield return null;
            Assert.That((string)Status(id)["status"], Is.Not.EqualTo("running"), Status(id).ToString());
        }

        private static void AssertReport(JObject run)
        {
            string path = (string)run["report_path"];
            Assert.That(path, Does.StartWith("Library/MCPForUnity/PlayScenarioRuns/"));
            Assert.That((string)run["report_error"], Is.Null);
            string full = Path.Combine(Path.GetDirectoryName(Application.dataPath), path);
            Assert.That(File.Exists(full), Is.True);
            JObject stored = JObject.Parse(File.ReadAllText(full));
            Assert.That((string)stored["job_id"], Is.EqualTo((string)run["job_id"]));
            Assert.That((string)stored["status"], Is.EqualTo((string)run["status"]));
            string evidence = Path.Combine(Path.GetDirectoryName(Application.dataPath), "Library/MCPForUnity/PlayScenarioIntegrationEvidence");
            Directory.CreateDirectory(evidence);
            File.WriteAllText(Path.Combine(evidence, (string)run["job_id"] + ".json"), stored.ToString());
        }

        private static JObject Status(string id) => Command(new JObject { ["action"] = "status", ["job_id"] = id });

        private static JObject Command(JObject input)
        {
            object response = ManagePlayScenario.HandleCommand(input);
            Assert.That(response, Is.TypeOf<SuccessResponse>(), JObject.FromObject(response).ToString());
            object data = ((SuccessResponse)response).Data;
            return data is JObject json ? json : JObject.FromObject(data);
        }

        private static void Save(string name, JArray steps)
        {
            Command(
                new JObject
                {
                    ["action"] = "save",
                    ["scenario"] = new JObject
                    {
                        ["name"] = name,
                        ["poll_interval_ms"] = 100,
                        ["steps"] = steps,
                    },
                }
            );
        }

        private static JArray RepeatSteps() =>
            new JArray(
                SceneStep("menu", "load_scene", Menu),
                ObjectStep("start", "click_ui", "ScenarioBootstrap/Canvas/StartButton", 5),
                SceneStep("game", "wait_scene", Game),
                ObjectStep("player", "wait_object", "Player", 5)
            );

        private static JObject SceneStep(string name, string action, string scene) =>
            new JObject
            {
                ["name"] = name,
                ["action"] = action,
                ["scene"] = scene,
                ["timeout_seconds"] = 5,
            };

        private static JObject ObjectStep(string name, string action, string target, int timeout) =>
            new JObject
            {
                ["name"] = name,
                ["action"] = action,
                ["target"] = target,
                ["timeout_seconds"] = timeout,
            };
    }
}
