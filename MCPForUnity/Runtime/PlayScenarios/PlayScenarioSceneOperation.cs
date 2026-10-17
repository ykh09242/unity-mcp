using System;
using MCPForUnity.Editor.Services.PlayScenarios;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Once-only scene effects and one retained owned lease. Releasing a waiter never queues an effect.</summary>
    public sealed class PlayScenarioSceneOperation
    {
        private static Func<bool> completion;
        private static bool unknown;
        private readonly bool guarded;
        private readonly Func<string, LoadSceneMode, AsyncOperation> load;
        private readonly Func<AsyncOperation, bool> completionReader;
        private AsyncOperation operation;
        private PlayScenarioStep prepared;
        private bool dispatched;
        private bool released;
        private Scene removed;
        private readonly PlayScenarioSceneScope waitScope = new PlayScenarioSceneScope();

        public PlayScenarioSceneOperation(bool guarded, Func<string, LoadSceneMode, AsyncOperation> load)
            : this(guarded, load, native => native.isDone) { }

        internal PlayScenarioSceneOperation(bool guarded, Func<string, LoadSceneMode, AsyncOperation> load, Func<AsyncOperation, bool> completionReader)
        {
            this.guarded = guarded;
            this.load = load ?? throw new ArgumentNullException(nameof(load));
            this.completionReader = completionReader ?? throw new ArgumentNullException(nameof(completionReader));
        }

        internal static void TrackCompletion(Func<bool> done)
        {
            if (completion != null || unknown)
                throw new InvalidOperationException("An owned scene operation is already pending or unknown.");
            completion = done ?? throw new ArgumentNullException(nameof(done));
        }

        public int HandleCount => operation == null ? 0 : 1;

        public static bool TryAdmit(bool enabled)
        {
            if (!enabled)
                return true;
            if (unknown)
                throw new InvalidOperationException(
                    "scene_operation_state_unknown: an owned operation crossed assembly reload; start a fresh Editor session before new scene effects."
                );
            if (completion == null)
                return true;
            try
            {
                if (!completion())
                    return false;
            }
            catch
            {
                unknown = true;
                throw new InvalidOperationException("scene_operation_state_unknown: native operation completion is unavailable.");
            }
            completion = null;
            return true;
        }

        public static bool CaptureReloadUnknown()
        {
            if (unknown)
                return true;
            if (completion == null)
                return false;
            try
            {
                if (completion())
                {
                    completion = null;
                    return false;
                }
            }
            catch { }
            unknown = true;
            return true;
        }

        public static void RestoreReloadUnknown(bool value) => unknown |= value;

        internal static void ResetForTests()
        {
            completion = null;
            unknown = false;
        }

        public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll)
        {
            if (released)
                throw new InvalidOperationException("A released scene-operation waiter cannot dispatch.");
            if (firstPoll || !ReferenceEquals(prepared, step))
            {
                if (!guarded && operation != null && !completionReader(operation))
                    throw new InvalidOperationException("The previous scene effect has not completed.");
                prepared = step;
                operation = null;
                dispatched = false;
                removed = default;
                waitScope.Release();
            }
            if (step.Action == "wait_scene")
            {
                string state = step.SceneState ?? "active";
                if (state == "unloaded")
                    return Observe(step.Scene, state);
                Scene selected = waitScope.Resolve(step.Scene, firstPoll);
                return ObserveResolved(selected, step.Scene, state);
            }
            if (!dispatched)
            {
                if (!TryAdmit(guarded))
                    return new PlayScenarioObservation(false, "Waiting for the previous owned scene operation; no effect dispatched.");
                if (step.Action == "load_scene")
                {
                    LoadSceneMode mode = step.LoadMode == "additive" ? LoadSceneMode.Additive : LoadSceneMode.Single;
                    if (mode == LoadSceneMode.Additive && PlayScenarioSceneScope.Find(step.Scene, false).IsValid())
                        throw new InvalidOperationException("The additive scene path is already loaded or loading.");
                    dispatched = true;
                    operation = load(step.Scene, mode);
                }
                else if (step.Action == "unload_scene")
                {
                    Scene selected = PlayScenarioSceneScope.Find(step.Scene, false);
                    if (!selected.IsValid())
                    {
                        dispatched = true;
                        return Observe(step.Scene, "unloaded");
                    }
                    if (!selected.isLoaded)
                        throw new InvalidOperationException("The scene is loading or unloading.");
                    if (selected == SceneManager.GetActiveScene())
                        throw new InvalidOperationException("Refusing to unload the active scene; explicitly select another active scene first.");
                    int loaded = 0;
                    for (int i = 0; i < SceneManager.sceneCount; i++)
                        if (SceneManager.GetSceneAt(i).isLoaded)
                            loaded++;
                    if (loaded <= 1)
                        throw new InvalidOperationException("Refusing to unload the last loaded scene.");
                    removed = selected;
                    dispatched = true;
                    operation = SceneManager.UnloadSceneAsync(selected);
                }
                else if (step.Action == "set_active_scene")
                {
                    Scene selected = PlayScenarioSceneScope.Find(step.Scene);
                    if (!selected.IsValid())
                        return new PlayScenarioObservation(false, "Waiting for the selected scene to be loaded before activation.");
                    if (SceneManager.GetActiveScene() != selected)
                        SceneManager.SetActiveScene(selected);
                    if (selected.IsValid() && selected.isLoaded && SceneManager.GetActiveScene() == selected)
                    {
                        dispatched = true;
                        return new PlayScenarioObservation(true, "Selected scene is loaded and active.");
                    }
                    throw new InvalidOperationException("The selected scene did not become active.");
                }
                else
                    throw new ArgumentException("Unsupported scene action.");
                if (operation == null)
                    throw new InvalidOperationException("The native scene adapter did not admit an operation.");
                if (guarded)
                {
                    AsyncOperation owned = operation;
                    TrackCompletion(() => completionReader(owned));
                }
                return new PlayScenarioObservation(false, "Scene operation requested exactly once; waiting for native completion.");
            }
            if (operation != null && !completionReader(operation))
                return new PlayScenarioObservation(false, "Waiting for the owned native scene operation.");
            if (guarded)
                TryAdmit(true);
            if (step.Action == "unload_scene")
            {
                if (removed.IsValid() && removed.isLoaded)
                    return new PlayScenarioObservation(false, "Waiting for the removed scene to leave SceneManager.");
                return Observe(step.Scene, "unloaded");
            }
            if (step.Action == "set_active_scene")
                return Observe(step.Scene, "active");
            return Observe(step.Scene, step.LoadMode == "additive" ? "loaded" : "active");
        }

        public static PlayScenarioObservation Observe(string path, string state)
        {
            return ObserveResolved(PlayScenarioSceneScope.Find(path, false), path, state);
        }

        private static PlayScenarioObservation ObserveResolved(Scene scene, string path, string state)
        {
            bool ready =
                state == "unloaded" ? !scene.IsValid() : scene.IsValid() && scene.isLoaded && (state == "loaded" || SceneManager.GetActiveScene() == scene);
            string detail = ready ? "Selected scene satisfies " + state + "." : "Waiting for selected scene state " + state + ".";
            return new PlayScenarioObservation(
                ready,
                detail,
                ready
                    ? null
                    : new PlayScenarioFailure
                    {
                        Code = "condition_unmet",
                        Expected = path + "; state=" + state,
                        Actual = scene.IsValid() ? "present; loaded=" + scene.isLoaded : "absent",
                        Message = detail,
                    }
            );
        }

        public void Release()
        {
            released = true;
            waitScope.Release();
            operation = null;
            prepared = null;
            removed = default;
        }
    }
}
