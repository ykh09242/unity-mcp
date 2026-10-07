using System;
using System.Collections;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public class EditorSizeAndReadinessTests
    {
        private static JObject Invoke(JObject parameters) => JObject.FromObject(ManageEditor.HandleCommand(parameters));

        [TestCase(0, 720)]
        [TestCase(8193, 720)]
        [TestCase(8192, 8192)]
        public void FixedSize_RejectsInvalidBudgetBeforeChangingSelection(int width, int height)
        {
            // Given dimensions outside the public bounds.
            bool playing = EditorApplication.isPlaying;
            // When attempting to select them.
            var result = Invoke(
                new JObject
                {
                    ["action"] = "set_game_view_size",
                    ["width"] = width,
                    ["height"] = height,
                }
            );
            // Then the command fails without entering Play Mode.
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual(playing, EditorApplication.isPlaying);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void GameViewSize_SelectsAndRestoresPreviousPreset(bool aspect)
        {
            // Given an open Game View with its selection captured.
            Type viewType = typeof(UnityEditor.Editor).Assembly.GetType("UnityEditor.GameView", true);
            var existing = UnityEngine.Resources.FindObjectsOfTypeAll(viewType).OfType<EditorWindow>().FirstOrDefault();
            EditorWindow window = existing ?? EditorWindow.GetWindow(viewType, false, "Game", false);
            var original = Invoke(new JObject { ["action"] = "get_game_view_size" });
            string savedRestores = SessionState.GetString("MCPForUnity.GameViewRestoreV1", "{}");
            Type apiType = typeof(ManageEditor)
                .Assembly.GetType("MCPForUnity.Editor.Helpers.GameViewSizeControl", true)
                .GetNestedType("Api", BindingFlags.NonPublic);
            object api = Activator.CreateInstance(apiType, new object[] { null });
            object group = apiType.GetField("group").GetValue(api);
            int originalCount = (int)group.GetType().GetMethod("GetTotalCount").Invoke(group, null);
            string restoreToken = null;
            try
            {
                Assert.IsTrue(original.Value<bool>("success"), original.ToString());
                var parameters = new JObject { ["action"] = "set_game_view_size" };
                if (aspect)
                    parameters["aspect_ratio"] = "16:9";
                else
                {
                    parameters["width"] = 1280;
                    parameters["height"] = 720;
                }
                // When selecting an explicit preset.
                JObject selected = Invoke(parameters);
                Assert.IsTrue(selected.Value<bool>("success"), selected.ToString());
                restoreToken = selected["data"].Value<string>("restore_token");
                var current = selected["data"]["current_size"];
                // Then the selection reflects the requested type and dimensions, and is reversible.
                Assert.AreEqual(aspect ? "AspectRatio" : "FixedResolution", current.Value<string>("size_type"));
                Assert.AreEqual(aspect ? 16 : 1280, current.Value<int>("width"));
                Assert.AreEqual(aspect ? 9 : 720, current.Value<int>("height"));
                var restored = Invoke(new JObject { ["action"] = "restore_game_view_size", ["restore_token"] = restoreToken });
                Assert.IsTrue(restored.Value<bool>("success"), restored.ToString());
                restoreToken = null;
                Assert.IsTrue(JToken.DeepEquals(original["data"]["current_size"], restored["data"]["current_size"]));
            }
            finally
            {
                if (restoreToken != null)
                    Invoke(new JObject { ["action"] = "restore_game_view_size", ["restore_token"] = restoreToken });
                int count = (int)group.GetType().GetMethod("GetTotalCount").Invoke(group, null);
                for (int i = count - 1; i >= originalCount; i--)
                    group.GetType().GetMethod("RemoveCustomSize").Invoke(group, new object[] { i });
                SessionState.SetString("MCPForUnity.GameViewRestoreV1", savedRestores);
                if (existing == null && window != null)
                    window.Close();
            }
        }

        [TestCase("FixedResolution", 8192, 8192, false)]
        [TestCase("FixedResolution", 8193, 720, false)]
        [TestCase("FixedResolution", 1280, 720, true)]
        [TestCase("AspectRatio", 8192, 8192, true)]
        public void NamedGameViewPreset_EnforcesFixedResolutionBudgetWithoutChangingRejectedSelection(string sizeType, int width, int height, bool accepted)
        {
            // Given one test-owned preset in the current Game View group.
            Assembly editorAssembly = typeof(UnityEditor.Editor).Assembly;
            Type viewType = editorAssembly.GetType("UnityEditor.GameView", true);
            var existing = UnityEngine.Resources.FindObjectsOfTypeAll(viewType).OfType<EditorWindow>().FirstOrDefault();
            EditorWindow window = existing ?? EditorWindow.GetWindow(viewType, false, "Game", false);
            Type apiType = typeof(ManageEditor)
                .Assembly.GetType("MCPForUnity.Editor.Helpers.GameViewSizeControl", true)
                .GetNestedType("Api", BindingFlags.NonPublic);
            string savedRestores = SessionState.GetString("MCPForUnity.GameViewRestoreV1", "{}");
            object group = null;
            object ownedPreset = null;
            MethodInfo select = null;
            int originalIndex = -1;
            try
            {
                object api = Activator.CreateInstance(apiType, new object[] { null });
                window = (EditorWindow)apiType.GetField("window").GetValue(api);
                group = apiType.GetField("group").GetValue(api);
                select = viewType.GetMethod("SizeSelectionCallback", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                var original = Invoke(new JObject { ["action"] = "get_game_view_size" });
                Assert.IsTrue(original.Value<bool>("success"), original.ToString());
                originalIndex = original["data"]["current_size"].Value<int>("index");
                int count = (int)group.GetType().GetMethod("GetTotalCount").Invoke(group, null);
                Assert.Less(count, 256, "The isolated fixture preset must fit in the discoverable preset page.");
                string label = "MCP Budget Test " + Guid.NewGuid().ToString("N");
                ownedPreset = Activator.CreateInstance(
                    editorAssembly.GetType("UnityEditor.GameViewSize", true),
                    Enum.Parse(editorAssembly.GetType("UnityEditor.GameViewSizeType", true), sizeType),
                    width,
                    height,
                    label
                );
                group.GetType().GetMethod("AddCustomSize").Invoke(group, new[] { ownedPreset });
                JObject available = Invoke(new JObject { ["action"] = "get_game_view_size" });
                Assert.IsTrue(available.Value<bool>("success"), available.ToString());
                JToken returnedPreset = available["data"]["presets"].Single(preset => preset.Value<int>("index") == count);
                Assert.AreEqual(sizeType, returnedPreset.Value<string>("size_type"));
                Assert.AreEqual(width, returnedPreset.Value<int>("width"));
                Assert.AreEqual(height, returnedPreset.Value<int>("height"));
                string returnedLabel = returnedPreset.Value<string>("label");
                Assert.IsFalse(string.IsNullOrEmpty(returnedLabel));
                Assert.AreEqual(1, available["data"]["presets"].Count(preset => preset.Value<string>("label") == returnedLabel));

                // When the existing preset is selected by its public name.
                JObject result = Invoke(new JObject { ["action"] = "set_game_view_size", ["preset"] = returnedLabel });

                // Then fixed-size limits reject before selection or restoration state changes;
                // aspect ratios remain ratios, regardless of their component product.
                Assert.AreEqual(accepted, result.Value<bool>("success"), result.ToString());
                JObject observed = Invoke(new JObject { ["action"] = "get_game_view_size" });
                Assert.IsTrue(observed.Value<bool>("success"), observed.ToString());
                if (accepted)
                {
                    Assert.AreEqual(sizeType, observed["data"]["current_size"].Value<string>("size_type"));
                    Assert.AreEqual(count, observed["data"]["current_size"].Value<int>("index"));
                }
                else
                {
                    Assert.AreEqual("game_view_size_limit_exceeded", result.Value<string>("code"), result.ToString());
                    Assert.IsTrue(JToken.DeepEquals(original["data"]["current_size"], observed["data"]["current_size"]));
                    Assert.AreEqual(savedRestores, SessionState.GetString("MCPForUnity.GameViewRestoreV1", "{}"));
                }
            }
            finally
            {
                // Restore synchronously before another repaint can render the oversized baseline preset.
                if (select != null && originalIndex >= 0)
                {
                    select.Invoke(window, new object[] { originalIndex, null });
                }
                if (group != null && ownedPreset != null)
                {
                    int count = (int)group.GetType().GetMethod("GetTotalCount").Invoke(group, null);
                    for (int i = count - 1; i >= 0; i--)
                    {
                        object candidate = group.GetType().GetMethod("GetGameViewSize").Invoke(group, new object[] { i });
                        if (ReferenceEquals(candidate, ownedPreset))
                        {
                            group.GetType().GetMethod("RemoveCustomSize").Invoke(group, new object[] { i });
                            break;
                        }
                    }
                }
                SessionState.SetString("MCPForUnity.GameViewRestoreV1", savedRestores);
                if (existing == null && window != null)
                {
                    window.Close();
                }
            }
        }

        [TestCase("scene_loaded", 0)]
        [TestCase("first_frame", 301)]
        [TestCase("rendered", 30)]
        public void PlayReadiness_RejectsInvalidRequestWithoutEnteringPlay(string target, int timeout)
        {
            // Given an invalid target or deadline.
            bool original = EditorApplication.isPlayingOrWillChangePlaymode;
            // When requesting readiness.
            JObject result = Invoke(
                new JObject
                {
                    ["action"] = "play",
                    ["wait_until"] = target,
                    ["timeout_seconds"] = timeout,
                }
            );
            // Then validation fails before a transition is requested.
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual(original, EditorApplication.isPlayingOrWillChangePlaymode);
        }
    }

    public class PlayReadinessLifecycleTests
    {
        private const string StateKey = "MCPForUnityTests.PlayReadinessLifecycleV1";
        private const string JobIdKey = StateKey + ".JobId";
        private const string NativeJobKey = "MCPForUnity.PlayModeReadinessV1";
        private const string NativeEnteredKey = "MCPForUnity.PlayModeReadiness.EnteredV1";
        private static readonly string DomainIdentity = Guid.NewGuid().ToString("N");

        [UnityTest]
        [Timeout(90000)]
        public IEnumerator ToolPlay_ConfirmsFirstFrameAcrossDomainReload_ThenToolStopExits()
        {
            // The runner creates its own unsaved default scene after external setup callbacks.
            // Only an empty scene or that exact runner-owned bootstrap scene may be replaced.
            Assert.IsFalse(EditorApplication.isPlayingOrWillChangePlaymode);
            Scene initial = SceneManager.GetActiveScene();
            if (
                SceneManager.sceneCount != 1
                || !string.IsNullOrEmpty(initial.path)
                || (initial.rootCount != 0 && !IsRunnerBootstrapScene(initial))
                || initial.isDirty
            )
                Assert.Ignore("This lifecycle test requires an empty scene or the active runner's clean bootstrap scene.");
            string oldJob = SessionState.GetString(NativeJobKey, "");
            if (!string.IsNullOrEmpty(oldJob) && JObject.Parse(oldJob).Value<string>("status") == "running")
                Assert.Ignore("An existing readiness job must not be replaced by the lifecycle fixture.");
            string folder = "Assets/__MCPReadinessLifecycle_" + Guid.NewGuid().ToString("N");
            SessionState.SetString(
                StateKey,
                new JObject
                {
                    ["native_job"] = oldJob,
                    ["native_entered"] = SessionState.GetBool(NativeEnteredKey, false),
                    ["options_enabled"] = EditorSettings.enterPlayModeOptionsEnabled,
                    ["options"] = (int)EditorSettings.enterPlayModeOptions,
                    ["domain_identity"] = DomainIdentity,
                    ["overall_deadline"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + 75000,
                    ["restore_default_scene"] = initial.rootCount != 0,
                    ["folder"] = folder,
                }.ToString()
            );
            RestoreNativeJob("");
            EditorSettings.enterPlayModeOptionsEnabled = false; // Exercise a real entry domain reload.
            AssetDatabase.CreateFolder("Assets", folder.Substring("Assets/".Length));
            Scene owned = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            Assert.IsTrue(EditorSceneManager.SaveScene(owned, folder + "/Readiness.unity"));

            // When the tool itself requests Play Mode. The yield adapter only lets the runner
            // persist its coroutine; it never sets EditorApplication.isPlaying itself.
            yield return new ToolPlayTransition(true);

            // Locals before the yield are lost on reload. Recover the tool job by SessionState.
            JObject persisted = JObject.Parse(SessionState.GetString(StateKey, ""));
            Assert.AreNotEqual(
                persisted.Value<string>("domain_identity"),
                DomainIdentity,
                "The lifecycle scenario must exercise an actual managed domain reload."
            );
            string id = SessionState.GetString(JobIdKey, "");
            Assert.IsNotEmpty(id);
            long deadline = Math.Min(DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + 30000, persisted.Value<long>("overall_deadline"));
            JObject status = null;
            while (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() < deadline)
            {
                status = Invoke(new JObject { ["action"] = "get_play_mode_job", ["job_id"] = id });
                Assert.IsTrue(status.Value<bool>("success"), status.ToString());
                if (status["data"].Value<string>("status") != "running")
                    break;
                yield return null;
            }
            // Then the persisted job confirms real entry and a subsequent runtime frame.
            Assert.AreEqual("succeeded", status?["data"]?.Value<string>("status"), status?.ToString());
            Assert.IsTrue(EditorApplication.isPlaying);
            Assert.Greater(status["data"].Value<int>("frame_count"), 0);
            string expectedPath = JObject.Parse(SessionState.GetString(StateKey, "")).Value<string>("folder") + "/Readiness.unity";
            Assert.AreEqual(expectedPath, SceneManager.GetActiveScene().path);
            Assert.IsTrue(SceneManager.GetActiveScene().isLoaded);

            yield return new ToolPlayTransition(false);
            Assert.IsFalse(EditorApplication.isPlayingOrWillChangePlaymode);
            var completed = Invoke(new JObject { ["action"] = "get_play_mode_job", ["job_id"] = id });
            Assert.AreEqual("succeeded", completed["data"].Value<string>("status"));
        }

        [UnityTearDown]
        public IEnumerator RestoreOwnedState()
        {
            string saved = SessionState.GetString(StateKey, "");
            if (string.IsNullOrEmpty(saved))
                yield break;
            try
            {
                if (EditorApplication.isPlayingOrWillChangePlaymode)
                    yield return new ToolPlayTransition(false);
            }
            finally
            {
                JObject state = JObject.Parse(saved);
                if (!EditorApplication.isPlayingOrWillChangePlaymode)
                {
                    EditorSceneManager.NewScene(
                        state.Value<bool>("restore_default_scene") ? NewSceneSetup.DefaultGameObjects : NewSceneSetup.EmptyScene,
                        NewSceneMode.Single
                    );
                    string folder = state.Value<string>("folder");
                    if (folder != null && folder.StartsWith("Assets/__MCPReadinessLifecycle_", StringComparison.Ordinal))
                        AssetDatabase.DeleteAsset(folder);
                }
                EditorSettings.enterPlayModeOptions = (EnterPlayModeOptions)state.Value<int>("options");
                EditorSettings.enterPlayModeOptionsEnabled = state.Value<bool>("options_enabled");
                SessionState.SetBool(NativeEnteredKey, state.Value<bool>("native_entered"));
                RestoreNativeJob(state.Value<string>("native_job"));
                SessionState.EraseString(StateKey);
                SessionState.EraseString(JobIdKey);
            }
        }

        private sealed class ToolPlayTransition : IEditModeTestYieldInstruction
        {
            private readonly bool _enter;

            public ToolPlayTransition(bool enter)
            {
                _enter = enter;
            }

            public bool ExpectDomainReload => _enter;
            public bool ExpectedPlaymodeState => _enter;

            public IEnumerator Perform()
            {
                yield return null; // Match EnterPlayMode's runner preparation barrier.
                JObject result = Invoke(
                    _enter
                        ? new JObject
                        {
                            ["action"] = "play",
                            ["wait_until"] = "first_frame",
                            ["timeout_seconds"] = 30,
                        }
                        : new JObject { ["action"] = "stop" }
                );
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                if (_enter)
                {
                    Assert.AreEqual("running", result["data"].Value<string>("status"));
                    SessionState.SetString(JobIdKey, result["data"].Value<string>("job_id"));
                    EditorApplication.UnlockReloadAssemblies();
                }
                long deadline = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + (_enter ? 30000 : 15000);
                if (_enter)
                    deadline = Math.Min(deadline, JObject.Parse(SessionState.GetString(StateKey, "")).Value<long>("overall_deadline"));
                while (EditorApplication.isPlaying != _enter && DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() < deadline)
                    yield return null;
                Assert.AreEqual(_enter, EditorApplication.isPlaying, "Tool Play Mode transition exceeded its deadline.");
            }
        }

        private static void RestoreNativeJob(string saved)
        {
            Type service = typeof(ManageEditor).Assembly.GetType("MCPForUnity.Editor.Services.PlayModeReadiness", true);
            const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Static;
            service.GetField("_job", flags).SetValue(null, null);
            SessionState.SetString(NativeJobKey, saved ?? "");
            service.GetMethod("Restore", flags).Invoke(null, null);
        }

        private static bool IsRunnerBootstrapScene(Scene scene)
        {
            // TestJobData.InitTestScene is assigned by CreateBootstrapSceneTask, not by user
            // scene setup. Probe the pinned runner instead of identifying scenes by object names.
            Type holderType = typeof(UnityEditor.TestTools.TestRunner.Api.TestRunnerApi).Assembly.GetType(
                "UnityEditor.TestTools.TestRunner.TestRun.TestJobDataHolder"
            );
            if (holderType == null)
            {
                return false;
            }
            FieldInfo runsField = holderType.GetField("TestRuns");
            if (runsField == null)
            {
                return false;
            }
            foreach (UnityEngine.Object holder in UnityEngine.Resources.FindObjectsOfTypeAll(holderType))
            {
                if (!(runsField.GetValue(holder) is IEnumerable runs))
                {
                    continue;
                }
                foreach (object run in runs)
                {
                    FieldInfo runningField = run.GetType().GetField("isRunning");
                    FieldInfo sceneField = run.GetType().GetField("InitTestScene");
                    if (runningField?.GetValue(run) is bool running && running && sceneField?.GetValue(run) is Scene bootstrap && bootstrap == scene)
                    {
                        return true;
                    }
                }
            }
            return false;
        }

        private static JObject Invoke(JObject parameters) => JObject.FromObject(ManageEditor.HandleCommand(parameters));
    }

    public class PlayReadinessPersistenceTests
    {
        private const BindingFlags Flags = BindingFlags.NonPublic | BindingFlags.Static;
        private const string SessionKey = "MCPForUnity.PlayModeReadinessV1";
        private Type _service;
        private FieldInfo _jobField;
        private object _originalJob;
        private string _originalSaved;

        [SetUp]
        public void SetUp()
        {
            Assert.IsFalse(EditorApplication.isPlayingOrWillChangePlaymode, "Readiness unit fixture requires Edit Mode.");
            _service = typeof(ManageEditor).Assembly.GetType("MCPForUnity.Editor.Services.PlayModeReadiness", true);
            _jobField = _service.GetField("_job", Flags);
            _originalJob = _jobField.GetValue(null);
            _originalSaved = SessionState.GetString(SessionKey, "");
            _jobField.SetValue(null, null);
        }

        [TearDown]
        public void TearDown()
        {
            var callback = (EditorApplication.CallbackFunction)
                Delegate.CreateDelegate(typeof(EditorApplication.CallbackFunction), _service.GetMethod("RequestPlay", Flags));
            EditorApplication.delayCall -= callback;
            _jobField.SetValue(null, _originalJob);
            SessionState.SetString(SessionKey, _originalSaved);
        }

        [Test]
        public void ReadinessJob_RestoresAcrossManagedStateLossAndKeepsDeadline()
        {
            // Given a persisted pending job, before Play Mode has actually entered.
            var start = Invoke("play", new JObject { ["wait_until"] = "first_frame", ["timeout_seconds"] = 30 });
            string id = start["data"].Value<string>("job_id");
            long deadline = start["data"].Value<long>("deadline_unix_ms");
            _jobField.SetValue(null, null);
            // When managed state is restored as after a domain reload.
            _service.GetMethod("Restore", Flags).Invoke(null, null);
            var status = Invoke("get_play_mode_job", new JObject { ["job_id"] = id });
            // Then it remains pending and keeps the original deadline rather than falsely succeeding in Edit Mode.
            Assert.AreEqual("running", status["data"].Value<string>("status"));
            Assert.AreEqual(deadline, status["data"].Value<long>("deadline_unix_ms"));
        }

        [Test]
        public void CancelledReadiness_DoesNotExecuteDeferredPlayRequest()
        {
            // Given a play request whose mutation is deferred until after the acknowledgement.
            var start = Invoke("play", new JObject { ["wait_until"] = "scene_loaded" });
            string id = start["data"].Value<string>("job_id");
            Invoke("cancel_play_mode_job", new JObject { ["job_id"] = id });
            // When the deferred callback runs after cancellation.
            _service.GetMethod("RequestPlay", Flags).Invoke(null, null);
            // Then monitoring is cancelled and Play Mode remains unchanged.
            var status = Invoke("get_play_mode_job", new JObject { ["job_id"] = id });
            Assert.AreEqual("cancelled", status["data"].Value<string>("status"));
            Assert.IsFalse(EditorApplication.isPlayingOrWillChangePlaymode);
        }

        [Test]
        public void ExpiredPersistedJob_TimesOutWithoutStartingPlay()
        {
            // Given an expired job restored after a long reload or blocked editor.
            string id = Guid.NewGuid().ToString("N");
            SessionState.SetString(
                SessionKey,
                new JObject
                {
                    ["job_id"] = id,
                    ["status"] = "running",
                    ["wait_until"] = "scene_loaded",
                    ["deadline_unix_ms"] = 1,
                    ["transition_requested"] = false,
                }.ToString()
            );
            _service.GetMethod("Restore", Flags).Invoke(null, null);
            // When the job is observed.
            var status = Invoke("get_play_mode_job", new JObject { ["job_id"] = id });
            // Then timeout is terminal and no transition occurs.
            Assert.AreEqual("timed_out", status["data"].Value<string>("status"));
            Assert.IsFalse(EditorApplication.isPlayingOrWillChangePlaymode);
        }

        private static JObject Invoke(string action, JObject parameters)
        {
            parameters["action"] = action;
            return JObject.FromObject(ManageEditor.HandleCommand(parameters));
        }
    }
}
