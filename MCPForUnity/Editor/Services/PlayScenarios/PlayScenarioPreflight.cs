using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json.Linq;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>A one-shot editor snapshot; it never loads a scene or dispatches input.</summary>
    internal static class PlayScenarioPreflight
    {
        internal static JObject Check(PlayScenarioDefinition definition)
        {
            var checks = new JArray();
            bool valid = true;
            void Add(string stage, int index, string name, string status, string detail)
            {
                valid &= status != "failed";
                checks.Add(
                    new JObject
                    {
                        ["stage"] = stage,
                        ["index"] = index,
                        ["name"] = name,
                        ["status"] = status,
                        ["detail"] = PlayScenarioEngine.Bounded(detail, 2048),
                    }
                );
            }
            JObject Result() =>
                new JObject
                {
                    ["success"] = true,
                    ["data"] = new JObject { ["valid"] = valid, ["checks"] = checks },
                };
            try
            {
                if (definition == null)
                    throw new ArgumentException("A scenario definition is required.");
                // Validate a value copy without changing the editor's authored definition.
                definition = PlayScenarioDefinition.Parse(JObject.FromObject(definition));
            }
            catch (Exception exception)
            {
                Add("definition", -1, "definition", "failed", exception.Message);
                return Result();
            }

            Scene activeScene = SceneManager.GetActiveScene();
            string expectedScene = null;
            void CheckStage(string stage, List<PlayScenarioStep> steps)
            {
                for (int index = 0; index < steps.Count; index++)
                {
                    PlayScenarioStep step = steps[index];
                    try
                    {
                        if (step.Action == "load_scene" || step.Action == "wait_scene")
                        {
                            expectedScene = step.Scene;
                            UnityPlayScenarioHost.RequireSceneAsset(step.Scene);
                            string detail =
                                "The permitted scene asset exists. Runtime " + (step.Action == "load_scene" ? "reload" : "readiness") + " is deferred.";
                            Add(stage, index, step.Name, "passed", detail);
                            continue;
                        }
                        PlayScenarioObjectCondition.ValidateSelector(step);
                        string[] segments = step.TargetId == null ? PlayScenarioObjectCondition.ParseTarget(step.Target) : null;
                        Type componentType = PlayScenarioObjectCondition.ValidateCondition(step);
                        if (step.Action == "click_ui" && ManageInput.UguiBackend == null)
                            throw new InvalidOperationException(
                                "Scenario UI clicks require the optional uGUI input backend; UI Toolkit clicks are unsupported."
                            );
                        if (step.ClickMode == "raycast" && !(ManageInput.UguiBackend is IUguiScenarioRaycastClickBackend))
                            throw UnityPlayScenarioHost.CapabilityFailure(step, "The optional uGUI backend does not support raycast-verified clicks.");
                        if (!activeScene.IsValid() || !activeScene.isLoaded || !string.Equals(activeScene.path, expectedScene, StringComparison.Ordinal))
                        {
                            Add(stage, index, step.Name, "deferred", "Target inspection is deferred until its expected scene is active; no scene was loaded.");
                            continue;
                        }
                        var matches =
                            step.TargetId != null
                                ? PlayScenarioObjectCondition.ResolveId(activeScene, step.TargetId)
                                : PlayScenarioObjectCondition.Resolve(activeScene, segments);
                        if (step.Property != null && matches.Count == 1)
                        {
                            var component = matches.Target.GetComponent(componentType);
                            if (component != null)
                                PlayScenarioObjectCondition.ReadScalar(component, step.Property, out _);
                        }
                        PlayScenarioObservation observation = PlayScenarioObjectCondition.Observe(step, matches, componentType);
                        if (step.Action == "click_ui")
                            Add(stage, index, step.Name, "deferred", observation.Detail + " Click dispatch is deferred; no input was sent.");
                        else if (!observation.Ready || step.StableForMs.GetValueOrDefault() > 0)
                            Add(
                                stage,
                                index,
                                step.Name,
                                "deferred",
                                observation.Detail + " Runtime readiness and uninterrupted stability require scenario execution."
                            );
                        else
                            Add(stage, index, step.Name, "passed", "Current scene snapshot: " + observation.Detail);
                    }
                    catch (Exception exception)
                    {
                        Add(stage, index, step.Name, "failed", exception.Message);
                    }
                }
            }
            CheckStage("setup", definition.SetupSteps);
            CheckStage("main", definition.Steps);
            CheckStage("cleanup", definition.CleanupSteps);
            return Result();
        }
    }
}
