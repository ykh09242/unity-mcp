using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>A scene-only Player adapter. Unity objects are borrowed during each evaluation.</summary>
    public sealed class PlayScenarioPlayerHost : IPlayScenarioHost, IPlayScenarioQueryHost
    {
        private readonly HashSet<string> scenes;
        private AsyncOperation load;
        private PlayScenarioQueryCounts counts;
        private string preparedTarget;
        private string preparedTargetId;
        private string preparedComponent;
        private string[] targetSegments;
        private Type componentType;

        public PlayScenarioPlayerHost(IEnumerable<string> scenePaths) => scenes = new HashSet<string>(scenePaths, StringComparer.Ordinal);

        public PlayScenarioQueryCounts CaptureQueryCounts() => counts;

        public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll)
        {
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
            counts.TargetSearches++;
            PrepareCondition(step);
            Scene active = SceneManager.GetActiveScene();
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
            load = null;
            preparedTarget = null;
            preparedTargetId = null;
            preparedComponent = null;
            targetSegments = null;
            componentType = null;
        }
    }
}
