using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    public interface IPlayScenarioRecordingSession : IDisposable
    {
        void Refresh();
    }

    public interface IPlayScenarioRecordingBackend
    {
        IPlayScenarioRecordingSession Begin(Action<PlayScenarioRecordedTarget> click, Action<string> diagnose);
    }

    /// <summary>Editor-owned recording lifecycle. Reloads retain a bounded snapshot, never live Unity references.</summary>
    [InitializeOnLoad]
    public static class PlayScenarioRecordingService
    {
        private const string SnapshotKey = "MCPForUnity.PlayScenarioRecording.Last";
        private static PlayScenarioRecordingDraft draft;
        private static IPlayScenarioRecordingSession session;
        private static readonly List<(string Path, string State)> PendingScenes = new List<(string, string)>();
        private static readonly Dictionary<Scene, string> ScenePaths = new Dictionary<Scene, string>();
        private static string sceneScope = "active";
        private static double deadline;
        private static double nextScan;
        private static bool hooked;
        public static IPlayScenarioRecordingBackend Backend { get; set; }

        static PlayScenarioRecordingService()
        {
            AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
            EditorApplication.quitting += BeforeQuit;
        }

        public static JObject Start(string name, int maxEvents = 31, int durationSeconds = 300, string sceneScope = "active")
        {
            if (draft != null && !draft.Saved && draft.Status != "cancelled")
                throw new InvalidOperationException("A scenario recording has not been saved or cancelled. Stop or cancel it first.");
            if (!EditorApplication.isPlaying || EditorApplication.isPaused || EditorApplication.isPlayingOrWillChangePlaymode != EditorApplication.isPlaying)
                throw new InvalidOperationException("Scenario recording requires running, unpaused Play Mode.");
            if (durationSeconds < 1 || durationSeconds > 600)
                throw new ArgumentException("duration_seconds must be an integer between 1 and 600.");
            if (Backend == null)
                throw new InvalidOperationException(
                    "The optional uGUI scenario recorder is unavailable. Install/enable com.unity.ugui to record supported Buttons."
                );
            var store = Store();
            PlayScenarioDefinition.ValidateName(name);
            if (store.List().Contains(name, StringComparer.Ordinal))
                throw new InvalidOperationException("A scenario with this name already exists. Choose a new draft name.");
            if (sceneScope != "active" && sceneScope != "all_loaded")
                throw new ArgumentException("scene_scope must be active or all_loaded.");
            string[] context = null;
            if (sceneScope == "all_loaded")
            {
                if (SceneManager.sceneCount < 1 || SceneManager.sceneCount > 16)
                    throw new InvalidOperationException("Recording context requires one to sixteen loaded normal scenes.");
                var paths = new List<string>();
                for (int i = 0; i < SceneManager.sceneCount; i++)
                {
                    Scene selected = SceneManager.GetSceneAt(i);
                    if (!PlayScenarioSceneScope.Normal(selected) || !selected.isLoaded)
                        throw new InvalidOperationException("Recording context contains an unnamed, preview, prefab or loading scene.");
                    UnityPlayScenarioHost.RequireSceneAsset(selected.path);
                    if (paths.Contains(selected.path, StringComparer.Ordinal))
                        throw new InvalidOperationException("Recording context has duplicate scene paths.");
                    paths.Add(selected.path);
                }
                context = paths.ToArray();
            }
            Cleanup();
            PlayScenarioRecordingService.sceneScope = sceneScope;
            draft = new PlayScenarioRecordingDraft(name, SceneManager.GetActiveScene().path, maxEvents, sceneScope, context);
            if (sceneScope == "all_loaded")
                for (int i = 0; i < SceneManager.sceneCount; i++)
                {
                    Scene selected = SceneManager.GetSceneAt(i);
                    ScenePaths.Add(selected, selected.path);
                }
            if (sceneScope == "active" && SceneManager.sceneCount > 1)
                draft.Diagnose(
                    "Multiple scenes were loaded at recording start. The draft loads only the active scene; additive setup requires manual authoring."
                );
            try
            {
                session = Backend.Begin(OnClick, draft.Diagnose);
                if (session == null)
                    throw new InvalidOperationException("The uGUI recorder did not provide a recording session.");
                deadline = EditorApplication.timeSinceStartup + durationSeconds;
                nextScan = EditorApplication.timeSinceStartup + 0.25;
                SceneManager.activeSceneChanged += SceneChanged;
                SceneManager.sceneLoaded += SceneLoaded;
                SceneManager.sceneUnloaded += SceneUnloaded;
                EditorApplication.update += Tick;
                EditorApplication.playModeStateChanged += PlayModeChanged;
                hooked = true;
                Remember();
                return draft.Snapshot();
            }
            catch
            {
                draft.Finish("interrupted", "recorder_start_failed");
                Cleanup();
                Remember();
                throw;
            }
        }

        public static JObject Status(string id = null)
        {
            if (draft != null)
            {
                MatchId(id, draft.Id);
                return draft.Snapshot();
            }
            string json = SessionState.GetString(SnapshotKey, "");
            if (json.Length == 0)
                throw new InvalidOperationException("No scenario recording is available.");
            JObject previous = JObject.Parse(json);
            MatchId(id, (string)previous["recording_id"]);
            return previous;
        }

        public static JObject Stop(string id)
        {
            RequireLiveDraft(id);
            if (draft.Saved)
                return draft.Snapshot();
            if (draft.Status == "cancelled")
                throw new InvalidOperationException("A cancelled recording cannot be saved.");
            FlushScenes();
            draft.Finish("stopped", "requested");
            Cleanup();
            Remember();
            var store = Store();
            if (store.List().Contains(draft.Name, StringComparer.Ordinal))
                throw new InvalidOperationException("A scenario with the draft name appeared during recording. The draft was not saved or overwritten.");
            store.Save(draft.Definition());
            draft.MarkSaved();
            Remember();
            return draft.Snapshot();
        }

        public static JObject Cancel(string id)
        {
            RequireLiveDraft(id);
            draft.Cancel();
            Cleanup();
            Remember();
            return draft.Snapshot();
        }

        public static JObject Checkpoint(string id, string name)
        {
            RequireLiveDraft(id);
            FlushScenes();
            draft.Checkpoint(name);
            if (!draft.Recording)
                Cleanup();
            Remember();
            return draft.Snapshot();
        }

        private static PlayScenarioStore Store() => new PlayScenarioStore(Path.GetDirectoryName(Application.dataPath));

        private static void MatchId(string requested, string actual)
        {
            if (requested != null && requested != actual)
                throw new ArgumentException("Unknown recording_id.");
        }

        private static void RequireLiveDraft(string id)
        {
            if (draft == null)
                throw new InvalidOperationException(
                    "The recording session ended at domain reload. Its status snapshot is available, but it cannot be resumed or saved."
                );
            MatchId(id, draft.Id);
        }

        private static void OnClick(PlayScenarioRecordedTarget target)
        {
            if (draft == null || !draft.Recording)
                return;
            // Button callbacks may load a scene before the observer receives pointerClick.
            // Keep that source click before its queued transition; a click in the new scene flushes first.
            if (sceneScope == "all_loaded" || (target != null && target.Scene != draft.CurrentScene))
                FlushScenes();
            draft.Click(target);
            if (!draft.Recording)
                Cleanup();
            Remember();
        }

        private static void SceneChanged(Scene previous, Scene current)
        {
            if (draft == null || !draft.Recording)
                return;
            if (PendingScenes.Count >= 31)
            {
                Interrupt("scene_transition_limit");
                return;
            }
            PendingScenes.Add((current.path, "active"));
        }

        private static void SceneLoaded(Scene scene, LoadSceneMode mode)
        {
            if (sceneScope == "all_loaded" && draft?.Recording == true)
            {
                try
                {
                    if (!PlayScenarioSceneScope.Normal(scene) || ScenePaths.Count >= 32 || SceneManager.sceneCount > 32)
                        throw new InvalidOperationException("Unsupported or excessive loaded scene context.");
                    UnityPlayScenarioHost.RequireSceneAsset(scene.path);
                    if (ScenePaths.Values.Contains(scene.path, StringComparer.Ordinal) && !ScenePaths.ContainsKey(scene))
                        throw new InvalidOperationException("Duplicate loaded scene path.");
                    ScenePaths[scene] = scene.path;
                    QueueScene(scene.path, "loaded");
                }
                catch (Exception error)
                {
                    draft.Diagnose("Scene observation unavailable: " + error.GetType().Name);
                    Interrupt("scene_context_unavailable");
                    return;
                }
            }
            else if (mode == LoadSceneMode.Additive)
                draft?.Diagnose("An additive scene load was observed; only active-scene transitions are captured.");
            Refresh();
        }

        private static void SceneUnloaded(Scene scene)
        {
            if (sceneScope != "all_loaded" || draft?.Recording != true)
                return;
            if (!ScenePaths.TryGetValue(scene, out string path))
            {
                Interrupt("scene_context_unavailable");
                return;
            }
            ScenePaths.Remove(scene);
            QueueScene(path, "unloaded");
        }

        private static void QueueScene(string path, string state)
        {
            if (PendingScenes.Count >= 31)
            {
                Interrupt("scene_transition_limit");
                return;
            }
            PendingScenes.Add((path, state));
        }

        private static void FlushScenes()
        {
            foreach (var scene in PendingScenes)
            {
                if (draft == null || !draft.Recording)
                    break;
                if (sceneScope == "all_loaded")
                    draft.SceneObserved(scene.Path, scene.State);
                else
                    draft.SceneChanged(scene.Path);
            }
            PendingScenes.Clear();
        }

        private static void Tick()
        {
            FlushScenes();
            if (draft == null || !draft.Recording)
            {
                Cleanup();
                Remember();
                return;
            }
            double now = EditorApplication.timeSinceStartup;
            if (now >= deadline)
            {
                draft.Diagnose("The recording duration limit was reached. Stop to save the bounded draft or cancel to discard it.");
                Interrupt("duration_limit");
                return;
            }
            if (now >= nextScan)
            {
                nextScan = now + 0.25;
                Refresh();
            }
        }

        private static void Refresh()
        {
            try
            {
                session?.Refresh();
            }
            catch (Exception error)
            {
                draft?.Diagnose("Observer refresh failed: " + error.GetType().Name);
                Interrupt("observer_refresh_failed");
            }
        }

        private static void Interrupt(string reason)
        {
            FlushScenes();
            draft?.Finish("interrupted", reason);
            if (draft != null && !draft.Saved && draft.Status != "cancelled")
                draft.Diagnose("Observation stopped: " + reason + ". The draft remains unreviewed and was not automatically saved.");
            Cleanup();
            Remember();
        }

        private static void BeforeReload() => Interrupt("assembly_reload");

        private static void BeforeQuit() => Interrupt("editor_quit");

        private static void PlayModeChanged(PlayModeStateChange change)
        {
            if (change == PlayModeStateChange.ExitingPlayMode || change == PlayModeStateChange.ExitingEditMode)
                Interrupt("play_mode_changed");
        }

        private static void Remember()
        {
            if (draft != null)
                SessionState.SetString(SnapshotKey, draft.Snapshot().ToString(Formatting.None));
        }

        private static void Cleanup()
        {
            if (hooked)
            {
                SceneManager.activeSceneChanged -= SceneChanged;
                SceneManager.sceneLoaded -= SceneLoaded;
                SceneManager.sceneUnloaded -= SceneUnloaded;
                EditorApplication.update -= Tick;
                EditorApplication.playModeStateChanged -= PlayModeChanged;
                hooked = false;
            }
            PendingScenes.Clear();
            ScenePaths.Clear();
            IPlayScenarioRecordingSession previous = session;
            session = null;
            previous?.Dispose();
        }

        public static PlayScenarioRecordedTarget DescribeTarget(GameObject target)
        {
            var result = new PlayScenarioRecordedTarget();
            if (target == null || !target.scene.IsValid() || !target.scene.isLoaded)
            {
                result.Error = "The clicked Button no longer belongs to a loaded scene.";
                return result;
            }
            result.Scene = target.scene.path;
            PlayScenarioTarget identity = target.GetComponent<PlayScenarioTarget>();
            if (identity != null)
            {
                if (!PlayScenarioTarget.IsValidTargetId(identity.TargetId))
                {
                    result.Error = "A clicked Button has an invalid PlayScenarioTarget ID.";
                    return result;
                }
                int count = 0;
                int visited = 0;
                var queue = new Queue<Transform>(target.scene.GetRootGameObjects().Select(root => root.transform));
                while (queue.Count > 0)
                {
                    Transform node = queue.Dequeue();
                    if (++visited > 10000)
                    {
                        result.Error = "The stable target ID uniqueness check exceeded 10000 hierarchy nodes.";
                        return result;
                    }
                    PlayScenarioTarget marker = node.GetComponent<PlayScenarioTarget>();
                    if (marker != null && marker.TargetId == identity.TargetId)
                        count++;
                    foreach (Transform child in node)
                        queue.Enqueue(child);
                }
                if (count != 1)
                    result.Error = "A clicked Button has a duplicate PlayScenarioTarget ID in its scene.";
                else
                    result.TargetId = identity.TargetId;
                return result;
            }
            var names = new List<string>();
            for (Transform node = target.transform; node != null; node = node.parent)
            {
                if (
                    names.Count >= 128
                    || node.name.Length == 0
                    || node.name == "."
                    || node.name == ".."
                    || node.name.Any(character => character == '/' || character == '\\' || char.IsControl(character))
                )
                {
                    result.Error = "A clicked Button has an unsupported exact hierarchy path. Assign a stable PlayScenarioTarget ID.";
                    return result;
                }
                IEnumerable<Transform> siblings =
                    node.parent == null ? target.scene.GetRootGameObjects().Select(root => root.transform) : node.parent.Cast<Transform>();
                if (siblings.Count(sibling => sibling.name == node.name) != 1)
                {
                    result.Error = "A clicked Button hierarchy path is ambiguous because sibling names are duplicated. Assign a stable PlayScenarioTarget ID.";
                    return result;
                }
                names.Add(node.name);
            }
            names.Reverse();
            result.Target = string.Join("/", names);
            if (result.Target.Length > 4096)
                result.Error = "A clicked Button hierarchy path exceeds 4096 characters.";
            return result;
        }
    }
}
