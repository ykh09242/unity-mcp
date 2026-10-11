using System;
using System.Collections;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using UnityEngine.UI;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public sealed class UguiScenarioRecordingLifecycleTests
    {
        private const string SceneKey = "MCPForUnityTests.RecorderLifecycle.Scene";
        private const string NameKey = "MCPForUnityTests.RecorderLifecycle.Name";
        private const string ExitIdKey = "MCPForUnityTests.RecorderLifecycle.ExitId";

        [UnityTest]
        public IEnumerator LiveRecordingSaveCancelTimeoutAndPlayExitReleaseObservers()
        {
            string suffix = Guid.NewGuid().ToString("N");
            string scenePath = "Assets/McpRecorderLifecycle-" + suffix + ".unity";
            SessionState.SetString(SceneKey, scenePath);
            SessionState.SetString(NameKey, "recorder-native-" + suffix);
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            SceneManager.SetActiveScene(scene);
            Assert.That(EditorSceneManager.SaveScene(scene, scenePath), Is.True);
            yield return new EnterPlayMode();

            // Keep captured locals inside the Play Mode phase. Unity cannot restore compiler
            // display-class instances allocated before the EnterPlayMode domain reload.
            {
                string name = SessionState.GetString(NameKey, "");
                var store = new PlayScenarioStore(Path.GetDirectoryName(Application.dataPath));
                var target = new GameObject("RecordedStart", typeof(RectTransform), typeof(Button));
                var eventObject = new GameObject("RecordedEventSystem", typeof(EventSystem));
                EventSystem events = eventObject.GetComponent<EventSystem>();
                int callbacks = 0;
                target.GetComponent<Button>().onClick.AddListener(() => callbacks++);
                var pointer = new PointerEventData(events) { button = PointerEventData.InputButton.Left, pointerId = -1 };

                // A real EventSystem click is observed once, then an explicit stop saves only a draft.
                JObject started = PlayScenarioRecordingService.Start(name, 10, 30);
                string id = (string)started["recording_id"];
                Assert.That(ActiveObservers(), Is.GreaterThanOrEqualTo(1));
                ExecuteEvents.Execute(target, pointer, ExecuteEvents.pointerDownHandler);
                ExecuteEvents.Execute(target, pointer, ExecuteEvents.pointerClickHandler);
                Assert.That(callbacks, Is.EqualTo(1));
                Assert.That((int)PlayScenarioRecordingService.Status(id)["event_count"], Is.EqualTo(1));
                JObject stopped = PlayScenarioRecordingService.Stop(id);
                yield return null; // Destroy is deferred until the real Player loop releases components.
                Assert.That((bool)stopped["saved"], Is.True);
                Assert.That((bool)stopped["requires_review"], Is.True);
                Assert.That(store.Get(name).Draft, Is.True);
                Assert.That(store.Get(name).Steps[0].Action, Is.EqualTo("load_scene"));
                Assert.That(store.Get(name).Steps[1].Action, Is.EqualTo("click_ui"));
                Assert.That(ActiveObservers(), Is.Zero);
                Assert.That((bool)PlayScenarioRecordingService.Stop(id)["saved"], Is.True);

                // Cancel disposes its observer and never creates a scenario definition.
                started = PlayScenarioRecordingService.Start(name + "-cancel", 10, 30);
                id = (string)started["recording_id"];
                PlayScenarioRecordingService.Cancel(id);
                yield return null;
                Assert.That(ActiveObservers(), Is.Zero);
                Assert.That(store.List(), Does.Not.Contain(name + "-cancel"));

                // The real Editor timer stops capture at the duration bound without silently saving.
                started = PlayScenarioRecordingService.Start(name + "-timeout", 10, 1);
                id = (string)started["recording_id"];
                double deadline = EditorApplication.timeSinceStartup + 5;
                while ((string)PlayScenarioRecordingService.Status(id)["status"] == "recording" && EditorApplication.timeSinceStartup < deadline)
                    yield return null;
                JObject expired = PlayScenarioRecordingService.Status(id);
                Assert.That((string)expired["status"], Is.EqualTo("interrupted"));
                Assert.That((string)expired["reason"], Is.EqualTo("duration_limit"));
                Assert.That((bool)expired["saved"], Is.False);
                yield return null;
                Assert.That(ActiveObservers(), Is.Zero);
                Assert.Throws<InvalidOperationException>(() => PlayScenarioRecordingService.Start(name + "-replacement", 10, 30));
                PlayScenarioRecordingService.Cancel(id);

                // Exiting Play Mode exercises actual lifecycle callbacks and domain-reloaded status storage.
                started = PlayScenarioRecordingService.Start(name + "-exit", 10, 30);
                SessionState.SetString(ExitIdKey, (string)started["recording_id"]);
                Assert.That(ActiveObservers(), Is.GreaterThanOrEqualTo(1));
            }
            yield return new ExitPlayMode();
            string exitId = SessionState.GetString(ExitIdKey, "");
            JObject interrupted = PlayScenarioRecordingService.Status(exitId);
            Assert.That((string)interrupted["status"], Is.EqualTo("interrupted"));
            Assert.That((string)interrupted["reason"], Is.EqualTo("play_mode_changed"));
            Assert.That((bool)interrupted["saved"], Is.False);
            Assert.That(ActiveObservers(), Is.Zero);
        }

        private static int ActiveObservers() =>
            UnityEngine.Resources.FindObjectsOfTypeAll<UguiScenarioRecordingObserver>().Count(observer => observer != null && observer.enabled);

        [UnityTearDown]
        public IEnumerator RestoreSceneAndRemoveOwnedTestArtifacts()
        {
            EditorApplication.isPaused = false;
            try
            {
                JObject current = PlayScenarioRecordingService.Status();
                if (!(bool)current["saved"] && (string)current["status"] != "cancelled")
                    PlayScenarioRecordingService.Cancel((string)current["recording_id"]);
            }
            catch (InvalidOperationException) { /* No in-memory recording survives a domain reload. */ }
            if (EditorApplication.isPlaying)
                yield return new ExitPlayMode();
            string name = SessionState.GetString(NameKey, "");
            if (!string.IsNullOrEmpty(name))
            {
                var store = new PlayScenarioStore(Path.GetDirectoryName(Application.dataPath));
                foreach (string suffix in new[] { "", "-cancel", "-timeout", "-exit", "-replacement" })
                    store.Delete(name + suffix);
            }
            string scenePath = SessionState.GetString(SceneKey, "");
            if (!string.IsNullOrEmpty(scenePath))
            {
                Scene owned = SceneManager.GetSceneByPath(scenePath);
                if (owned.IsValid() && owned.isLoaded)
                {
                    if (SceneManager.sceneCount == 1)
                        EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                    for (int index = 0; index < SceneManager.sceneCount; index++)
                    {
                        Scene other = SceneManager.GetSceneAt(index);
                        if (other != owned)
                        {
                            SceneManager.SetActiveScene(other);
                            break;
                        }
                    }
                    EditorSceneManager.CloseScene(owned, true);
                }
                AssetDatabase.DeleteAsset(scenePath);
            }
            SessionState.EraseString(SceneKey);
            SessionState.EraseString(NameKey);
            SessionState.EraseString(ExitIdKey);
        }
    }
}
