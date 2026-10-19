using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using MCPForUnity.Runtime.PlayScenarios.UIToolkit;
using UnityEngine;
using UnityEngine.Profiling;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>A scene-only Player adapter. Unity objects are borrowed during each evaluation.</summary>
    public sealed class PlayScenarioPlayerHost
        : IPlayScenarioInputHost,
            IPlayScenarioHost,
            IPlayScenarioQueryHost,
            IPlayScenarioResourceHost,
            IPlayScenarioMetricsHost
    {
        private readonly PlayScenarioInputSession inputSession = new PlayScenarioInputSession();

        public void MaintainInput() => inputSession.Maintain();

        public void ReleaseInput() => inputSession.Release();

        private readonly HashSet<string> scenes;
        private readonly bool sceneSemantics;
        private readonly PlayScenarioSceneOperation sceneOperation;
        private readonly PlayScenarioSceneScope sceneScope = new PlayScenarioSceneScope();
        private AsyncOperation load;
        private PlayScenarioQueryCounts counts;
        private string preparedTarget;
        private string preparedTargetId;
        private string preparedComponent;
        private string[] targetSegments;
        private Type componentType;
        private Func<int> subscriptionCount;

        public PlayScenarioPlayerHost(IEnumerable<string> scenePaths, Func<int> subscriptionCount = null, bool sceneSemantics = false)
        {
            scenes = new HashSet<string>(scenePaths, StringComparer.Ordinal);
            this.subscriptionCount = subscriptionCount;
            this.sceneSemantics = sceneSemantics;
            sceneOperation = new PlayScenarioSceneOperation(sceneSemantics, (path, mode) => SceneManager.LoadSceneAsync(path, mode));
        }

        public PlayScenarioQueryCounts CaptureQueryCounts() => counts;

        public PlayScenarioRegisteredResources CaptureResources() => PlayScenarioResourceTracker.Capture();

        /// <summary>Sample diagnostic scalars at an iteration boundary without collecting garbage or retaining scene objects.</summary>
        /// <remarks>ObjectCount includes active and inactive GameObjects in loaded SceneManager scenes, excluding the separate DontDestroyOnLoad scene.</remarks>
        public PlayScenarioMetricsSnapshot CaptureMetrics(int iteration, long now)
        {
            var snapshot = new PlayScenarioMetricsSnapshot
            {
                Iteration = iteration,
                TimestampUnixMs = now,
                RunnerHandleCount = (load == null ? 0 : 1) + sceneOperation.HandleCount,
            };
            try
            {
                int subscriptions = subscriptionCount?.Invoke() ?? 0;
                if (subscriptions < 0)
                    throw new InvalidOperationException("Runner subscription count must be nonnegative.");
                snapshot.RunnerSubscriptionCount = subscriptions;
                snapshot.ManagedBytes = GC.GetTotalMemory(false);
                long allocated = Profiler.GetTotalAllocatedMemoryLong();
                if (allocated > 0)
                    snapshot.AllocatedBytes = allocated;
                else
                    snapshot.Error = "Unity allocated-memory statistics are unavailable.";
                snapshot.ObjectCount = CountLoadedSceneObjects();
            }
            catch (Exception error)
            {
                snapshot.Error = error.GetType().Name + ": " + error.Message;
            }
            return snapshot;
        }

        private static int CountLoadedSceneObjects()
        {
            const int limit = 100000;
            int count = 0;
            var roots = new List<GameObject>();
            var pending = new Stack<Transform>();
            for (int index = 0; index < SceneManager.sceneCount; index++)
            {
                Scene scene = SceneManager.GetSceneAt(index);
                if (!scene.IsValid() || !scene.isLoaded)
                    continue;
                if (scene.rootCount > limit - count)
                    throw new InvalidOperationException("Loaded-scene object sampling exceeds the 100000-entry inspection limit.");
                roots.Clear();
                scene.GetRootGameObjects(roots);
                foreach (GameObject root in roots)
                    if (root != null)
                        pending.Push(root.transform);
                while (pending.Count > 0)
                {
                    Transform current = pending.Pop();
                    if (current == null)
                        continue;
                    if (++count > limit || current.childCount > limit - count - pending.Count)
                        throw new InvalidOperationException("Loaded-scene object sampling exceeds the 100000-entry inspection limit.");
                    for (int child = 0; child < current.childCount; child++)
                        pending.Push(current.GetChild(child));
                }
            }
            return count;
        }

        public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll)
        {
            if (step.Scene != null && !scenes.Contains(step.Scene))
                throw new ArgumentException("Scene is absent from the frozen Player bundle: " + step.Scene);
            if (
                sceneSemantics
                && (step.Action == "load_scene" || step.Action == "wait_scene" || step.Action == "unload_scene" || step.Action == "set_active_scene")
            )
                return sceneOperation.Evaluate(step, firstPoll);
            if (step.Action == "input")
                return inputSession.Evaluate(step, firstPoll);
            if (step.Action == "load_scene" || step.Action == "wait_scene")
            {
                if (!scenes.Contains(step.Scene))
                    throw new ArgumentException("Scene is absent from the frozen Player bundle: " + step.Scene);
                if (step.Action == "load_scene" && firstPoll)
                {
                    if (load != null && !load.isDone)
                        throw new InvalidOperationException("Previous Player scene load is still pending.");
                    load = SceneManager.LoadSceneAsync(step.Scene, LoadSceneMode.Single);
                    if (load == null)
                        throw new InvalidOperationException("Player scene load was not admitted.");
                }
                Scene scene = SceneManager.GetActiveScene();
                return new PlayScenarioObservation(
                    scene.IsValid() && scene.isLoaded && scene.path == step.Scene && (step.Action != "load_scene" || load?.isDone == true),
                    "Active Player scene: " + scene.path + "; expected: " + step.Scene + "."
                );
            }
            if (step.Action != "wait_object" && step.Action != "click_ui")
                throw new ArgumentException("Unsupported Player host action.");
            if (step.Property != null)
                throw new InvalidOperationException("Player property capability must be rejected before execution.");
            bool toolkit =
                step.Action == "click_ui" && UIToolkitScenarioInputAdapter.ValidateRequest(step.UiBackend, step.ElementName, step.ElementPath, step.ClickMode);
            counts.TargetSearches++;
            PrepareCondition(step);
            Scene active = step.Scene == null ? SceneManager.GetActiveScene() : sceneScope.Resolve(step.Scene, firstPoll);
            if (step.Scene != null && (!active.IsValid() || !active.isLoaded))
                return Pending(step, "Waiting for the selected scene to be loaded.", "condition_unmet");
            int inspected = 0;
            int matched = 0,
                activeCount = 0;
            GameObject target = null;
            var pending = new Stack<(Transform Node, int Segment)>();
            string[] parts = targetSegments;
            if (active.IsValid() && active.isLoaded)
            {
                if (active.rootCount > 100000)
                    throw new InvalidOperationException("Target inspection exceeds the bounded hierarchy budget.");
                foreach (GameObject root in active.GetRootGameObjects())
                {
                    Inspect(ref inspected);
                    if (parts == null || root.name == parts[0])
                        pending.Push((root.transform, 1));
                }
            }
            while (pending.Count > 0)
            {
                var entry = pending.Pop();
                Transform current = entry.Node;
                bool match = parts == null ? current.GetComponent<PlayScenarioTarget>()?.TargetId == step.TargetId : entry.Segment == parts.Length;
                if (match)
                {
                    matched++;
                    if (current.gameObject.activeInHierarchy)
                        activeCount++;
                    if (target == null)
                        target = current.gameObject;
                    if ((step.TargetId != null || !step.Count.HasValue) && matched > 1)
                        throw new PlayScenarioException(
                            new PlayScenarioFailure
                            {
                                Code = "target_ambiguous",
                                Target = step.TargetId ?? step.Target,
                                Message = "Target is ambiguous in the active Player scene, including inactive objects.",
                            }
                        );
                }
                if (parts != null && entry.Segment == parts.Length)
                    continue;
                for (int index = 0; index < current.childCount; index++)
                {
                    Inspect(ref inspected);
                    Transform child = current.GetChild(index);
                    if (parts == null || child.name == parts[entry.Segment])
                        pending.Push((child, entry.Segment + 1));
                }
            }
            int expected = step.Count ?? 1;
            string detail = "Exact Player matches: " + matched + "; expected: " + expected + "; active matches: " + activeCount + ".";
            if (matched != expected)
                return Pending(step, detail, matched == 0 ? "target_missing" : "condition_unmet");
            if (expected == 0)
                return new PlayScenarioObservation(true, detail);
            bool expectedActive = step.Active ?? true;
            if (expectedActive ? activeCount != matched : activeCount != 0)
                return Pending(step, detail + " Waiting for active state.", "condition_unmet");
            if (step.Component != null && target.GetComponent(componentType) == null)
                return Pending(step, detail + " Waiting for component " + step.Component + ".", "condition_unmet");
            if (step.Action == "wait_object")
                return new PlayScenarioObservation(true, detail);
            if (toolkit)
                return UIToolkitScenarioInputAdapter.EvaluateForScenario(target, step.ElementName, step.ElementPath, step.ClickMode, null, ref counts);
            if (step.ClickMode == "raycast")
            {
                if (!(PlayScenarioPlayerInput.Backend is IUguiScenarioRaycastClickBackend raycast))
                    throw new InvalidOperationException("Runtime raycast backend is unavailable.");
                return new PlayScenarioObservation(raycast.TryRaycastClick(target, out _, out string clickDetail), clickDetail);
            }
            if (!(PlayScenarioPlayerInput.Backend is IUguiScenarioClickBackend click))
                throw new InvalidOperationException("Runtime click backend is unavailable.");
            return new PlayScenarioObservation(click.TryClick(target, out _, out string directDetail), directDetail);
        }

        private void PrepareCondition(PlayScenarioStep step)
        {
            if (preparedTarget == step.Target && preparedTargetId == step.TargetId && preparedComponent == step.Component)
                return;
            string[] segments = step.Target?.Split('/');
            Type type = step.Component == null ? null : PlayScenarioPlayerCapabilities.ResolveComponent(step.Component);
            preparedTarget = step.Target;
            preparedTargetId = step.TargetId;
            preparedComponent = step.Component;
            targetSegments = segments;
            componentType = type;
        }

        private void Inspect(ref int inspected)
        {
            counts.HierarchyVisits++;
            if (++inspected > 100000)
                throw new InvalidOperationException("Target inspection exceeds the bounded hierarchy budget.");
        }

        private static PlayScenarioObservation Pending(PlayScenarioStep step, string detail, string code) =>
            new PlayScenarioObservation(
                false,
                detail,
                new PlayScenarioFailure
                {
                    Code = code,
                    Message = detail,
                    Target = step.TargetId ?? step.Target,
                }
            );

        public void Release()
        {
            ReleaseInput();
            sceneOperation.Release();
            sceneScope.Release();
            load = null;
            preparedTarget = null;
            preparedTargetId = null;
            preparedComponent = null;
            targetSegments = null;
            componentType = null;
            subscriptionCount = null;
        }
    }
}
