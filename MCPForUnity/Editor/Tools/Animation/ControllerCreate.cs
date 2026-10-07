using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Animation
{
    internal static class ControllerCreate
    {
        public static object Create(JObject @params)
        {
            string controllerPath = @params["controllerPath"]?.ToString();
            if (string.IsNullOrEmpty(controllerPath))
                return new { success = false, message = "'controllerPath' is required (e.g. 'Assets/Animations/Player.controller')" };

            controllerPath = AssetPathUtility.GetContainedAssetPath(controllerPath);
            if (controllerPath == null)
                return new { success = false, message = "Invalid asset path" };

            if (!controllerPath.EndsWith(".controller", StringComparison.OrdinalIgnoreCase))
                controllerPath += ".controller";

            controllerPath = AssetPathUtility.GetContainedAssetPath(controllerPath);

            if (AssetDatabase.LoadMainAssetAtPath(controllerPath) != null
                || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(controllerPath, AssetPathToGUIDOptions.OnlyExistingAssets)))
                return new { success = false, message = $"An asset already exists at '{controllerPath}'. Delete it first or use a different path." };

            using var folders = new AssetFolderScope();
            folders.EnsureParentDirectory(controllerPath);

            AssetPathUtility.GetFullAssetPath(controllerPath);
            var controller = AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
            if (controller == null)
                return new { success = false, message = $"Failed to create AnimatorController at '{controllerPath}'." };
            if (!AssetDatabase.Contains(controller))
            {
                // The factory creates fresh layer state machines before returning.
                foreach (var layer in controller.layers)
                {
                    if (layer.stateMachine != null && !AssetDatabase.Contains(layer.stateMachine))
                        UnityEngine.Object.DestroyImmediate(layer.stateMachine);
                }
                UnityEngine.Object.DestroyImmediate(controller);
                return new { success = false, message = $"AnimatorController was not persisted at '{controllerPath}'." };
            }
            if (!string.Equals(AssetDatabase.GetAssetPath(controller), controllerPath, StringComparison.OrdinalIgnoreCase))
                return new { success = false, message = $"AnimatorController was not persisted at the requested path '{controllerPath}'." };
            AssetDatabase.SaveAssets();
            folders.Complete();

            return new
            {
                success = true,
                message = $"Created AnimatorController at '{controllerPath}'",
                data = new
                {
                    path = controllerPath,
                    name = controller.name,
                    layerCount = controller.layers.Length,
                    parameterCount = controller.parameters.Length
                }
            };
        }

        public static object AddState(JObject @params)
        {
            var controller = LoadController(@params);
            if (controller == null)
                return ControllerNotFoundError(@params);

            string stateName = @params["stateName"]?.ToString();
            if (string.IsNullOrEmpty(stateName))
                return new { success = false, message = "'stateName' is required" };

            int layerIndex = @params["layerIndex"]?.ReadScalar<int?>() ?? 0;
            if (layerIndex < 0 || layerIndex >= controller.layers.Length)
                return new { success = false, message = $"Layer index {layerIndex} out of range (controller has {controller.layers.Length} layers)" };

            var rootStateMachine = controller.layers[layerIndex].stateMachine;

            // Check for duplicate state name
            foreach (var existingState in rootStateMachine.states)
            {
                if (existingState.state.name == stateName)
                    return new { success = false, message = $"State '{stateName}' already exists in layer {layerIndex}" };
            }

            // Optionally assign a clip
            AnimationClip clip = null;
            string clipPath = @params["clipPath"]?.ToString();
            if (!string.IsNullOrEmpty(clipPath))
            {
                clipPath = AssetPathUtility.GetAssetReferencePath(clipPath, allowPackages: true);
                if (clipPath == null)
                    return new { success = false, message = "Invalid clip asset path" };
                clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath);
                if (clip == null)
                    return new { success = false, message = $"AnimationClip not found at '{clipPath}'." };
            }

            float speed = @params["speed"]?.ReadScalar<float?>() ?? 1f;
            bool isDefault = @params["isDefault"]?.ReadScalar<bool?>() ?? false;

            AssetPathUtility.GetFullAssetPath(AssetDatabase.GetAssetPath(controller));
            var state = rootStateMachine.AddState(stateName);
            if (clip != null)
                state.motion = clip;
            state.speed = speed;
            if (isDefault)
                rootStateMachine.defaultState = state;

            EditorUtility.SetDirty(controller);
            AssetDatabase.SaveAssets();

            return new
            {
                success = true,
                message = $"Added state '{stateName}' to layer {layerIndex}",
                data = new
                {
                    stateName,
                    layerIndex,
                    hasMotion = state.motion != null,
                    speed = state.speed,
                    isDefault
                }
            };
        }

        public static object AddTransition(JObject @params)
        {
            var controller = LoadController(@params);
            if (controller == null)
                return ControllerNotFoundError(@params);

            string fromStateName = @params["fromState"]?.ToString();
            string toStateName = @params["toState"]?.ToString();
            if (string.IsNullOrEmpty(fromStateName) || string.IsNullOrEmpty(toStateName))
                return new { success = false, message = "'fromState' and 'toState' are required" };

            int layerIndex = @params["layerIndex"]?.ReadScalar<int?>() ?? 0;
            if (layerIndex < 0 || layerIndex >= controller.layers.Length)
                return new { success = false, message = $"Layer index {layerIndex} out of range" };

            var rootStateMachine = controller.layers[layerIndex].stateMachine;

            // Check for AnyState as source
            bool isAnyState = string.Equals(fromStateName, "AnyState", StringComparison.OrdinalIgnoreCase)
                           || string.Equals(fromStateName, "Any", StringComparison.OrdinalIgnoreCase)
                           || string.Equals(fromStateName, "Any State", StringComparison.OrdinalIgnoreCase);

            AnimatorState toState = null;
            foreach (var cs in rootStateMachine.states)
            {
                if (cs.state.name == toStateName) toState = cs.state;
            }

            if (toState == null)
                return new { success = false, message = $"State '{toStateName}' not found in layer {layerIndex}" };

            AnimatorState fromState = null;
            if (!isAnyState)
            {
                foreach (var cs in rootStateMachine.states)
                {
                    if (cs.state.name == fromStateName) fromState = cs.state;
                }

                if (fromState == null)
                    return new { success = false, message = $"State '{fromStateName}' not found in layer {layerIndex}" };

            }

            bool hasExitTime = @params["hasExitTime"]?.ReadScalar<bool?>() ?? true;
            float duration = @params["duration"]?.ReadScalar<float?>() ?? 0.25f;
            float exitTime = @params["exitTime"]?.ReadScalar<float?>() ?? 0.75f;

            // Prepare all conditions before creating a transition subasset.
            JToken conditionsToken = @params["conditions"];
            var conditions = new List<AnimatorCondition>();
            if (conditionsToken is JArray conditionsArray)
            {
                foreach (var condItem in conditionsArray)
                {
                    if (condItem is not JObject condObj) continue;

                    string paramName = condObj["parameter"]?.ToString();
                    if (string.IsNullOrEmpty(paramName)) continue;

                    string modeStr = condObj["mode"]?.ToString()?.ToLowerInvariant() ?? "greater";
                    float threshold = condObj["threshold"]?.ReadScalar<float?>() ?? 0f;

                    AnimatorConditionMode mode;
                    switch (modeStr)
                    {
                        case "greater": mode = AnimatorConditionMode.Greater; break;
                        case "less": mode = AnimatorConditionMode.Less; break;
                        case "equals": mode = AnimatorConditionMode.Equals; break;
                        case "notequal":
                        case "not_equal": mode = AnimatorConditionMode.NotEqual; break;
                        case "if":
                        case "true": mode = AnimatorConditionMode.If; break;
                        case "ifnot":
                        case "if_not":
                        case "false": mode = AnimatorConditionMode.IfNot; break;
                        default: mode = AnimatorConditionMode.Greater; break;
                    }

                    conditions.Add(new AnimatorCondition { mode = mode, threshold = threshold, parameter = paramName });
                }
            }

            AnimatorStateTransition transition;
            AssetPathUtility.GetFullAssetPath(AssetDatabase.GetAssetPath(controller));
            if (isAnyState)
            {
                transition = rootStateMachine.AddAnyStateTransition(toState);
                fromStateName = "AnyState";
            }
            else
            {
                transition = fromState.AddTransition(toState);
            }
            transition.hasExitTime = hasExitTime;
            transition.duration = duration;
            transition.exitTime = exitTime;
            foreach (var condition in conditions)
                transition.AddCondition(condition.mode, condition.threshold, condition.parameter);
            int conditionCount = conditions.Count;

            EditorUtility.SetDirty(controller);
            AssetDatabase.SaveAssets();

            return new
            {
                success = true,
                message = $"Added transition from '{fromStateName}' to '{toStateName}' with {conditionCount} conditions",
                data = new
                {
                    fromState = fromStateName,
                    toState = toStateName,
                    hasExitTime,
                    duration,
                    conditionCount
                }
            };
        }

        public static object AddParameter(JObject @params)
        {
            var controller = LoadController(@params);
            if (controller == null)
                return ControllerNotFoundError(@params);

            string paramName = @params["parameterName"]?.ToString();
            if (string.IsNullOrEmpty(paramName))
                return new { success = false, message = "'parameterName' is required" };

            string typeStr = @params["parameterType"]?.ToString()?.ToLowerInvariant() ?? "float";

            AnimatorControllerParameterType paramType;
            switch (typeStr)
            {
                case "float": paramType = AnimatorControllerParameterType.Float; break;
                case "int":
                case "integer": paramType = AnimatorControllerParameterType.Int; break;
                case "bool":
                case "boolean": paramType = AnimatorControllerParameterType.Bool; break;
                case "trigger": paramType = AnimatorControllerParameterType.Trigger; break;
                default:
                    return new { success = false, message = $"Unknown parameter type '{typeStr}'. Valid: float, int, bool, trigger" };
            }

            // Check for duplicate
            foreach (var existing in controller.parameters)
            {
                if (existing.name == paramName)
                    return new { success = false, message = $"Parameter '{paramName}' already exists" };
            }

            // Convert the default before adding the parameter to the controller.
            JToken defaultValue = @params["defaultValue"];
            float defaultFloat = 0f;
            int defaultInt = 0;
            bool defaultBool = false;
            if (defaultValue != null)
            {
                switch (paramType)
                {
                    case AnimatorControllerParameterType.Float:
                        defaultFloat = defaultValue.ReadScalar<float>();
                        break;
                    case AnimatorControllerParameterType.Int:
                        defaultInt = defaultValue.ReadScalar<int>();
                        break;
                    case AnimatorControllerParameterType.Bool:
                        defaultBool = defaultValue.ReadScalar<bool>();
                        break;
                }
            }
            AssetPathUtility.GetFullAssetPath(AssetDatabase.GetAssetPath(controller));
            controller.AddParameter(paramName, paramType);
            if (defaultValue != null)
            {
                var allParams = controller.parameters;
                var addedParam = allParams[allParams.Length - 1];
                switch (paramType)
                {
                    case AnimatorControllerParameterType.Float: addedParam.defaultFloat = defaultFloat; break;
                    case AnimatorControllerParameterType.Int: addedParam.defaultInt = defaultInt; break;
                    case AnimatorControllerParameterType.Bool: addedParam.defaultBool = defaultBool; break;
                }
                controller.parameters = allParams;
            }

            EditorUtility.SetDirty(controller);
            AssetDatabase.SaveAssets();

            return new
            {
                success = true,
                message = $"Added {typeStr} parameter '{paramName}'",
                data = new
                {
                    parameterName = paramName,
                    parameterType = typeStr,
                    totalParameters = controller.parameters.Length
                }
            };
        }

        public static object GetInfo(JObject @params)
        {
            var controller = LoadController(@params, writable: false);
            if (controller == null)
                return ControllerNotFoundError(@params);

            var controllerLayers = controller.layers;
            var controllerParameters = controller.parameters;
            var layers = new List<object>();
            for (int i = 0; i < controllerLayers.Length; i++)
            {
                var layer = controllerLayers[i];
                var layerStates = layer.stateMachine.states;
                var states = new List<object>();
                foreach (var cs in layerStates)
                {
                    var stateTransitions = cs.state.transitions;
                    var transitions = new List<object>();
                    foreach (var t in stateTransitions)
                    {
                        var transitionConditions = t.conditions;
                        var conditions = new List<object>();
                        foreach (var c in transitionConditions)
                        {
                            conditions.Add(new
                            {
                                parameter = c.parameter,
                                mode = c.mode.ToString(),
                                threshold = c.threshold
                            });
                        }

                        transitions.Add(new
                        {
                            destinationState = t.destinationState?.name,
                            hasExitTime = t.hasExitTime,
                            exitTime = t.exitTime,
                            duration = t.duration,
                            conditionCount = transitionConditions.Length,
                            conditions
                        });
                    }

                    states.Add(new
                    {
                        name = cs.state.name,
                        speed = cs.state.speed,
                        hasMotion = cs.state.motion != null,
                        motionName = cs.state.motion?.name,
                        isDefault = layer.stateMachine.defaultState == cs.state,
                        transitionCount = stateTransitions.Length,
                        transitions
                    });
                }

                layers.Add(new
                {
                    index = i,
                    name = layer.name,
                    stateCount = layerStates.Length,
                    states
                });
            }

            var parameters = new List<object>();
            foreach (var p in controllerParameters)
            {
                parameters.Add(new
                {
                    name = p.name,
                    type = p.type.ToString(),
                    defaultFloat = p.defaultFloat,
                    defaultInt = p.defaultInt,
                    defaultBool = p.defaultBool
                });
            }

            return new
            {
                success = true,
                data = new
                {
                    path = AssetDatabase.GetAssetPath(controller),
                    name = controller.name,
                    layerCount = controllerLayers.Length,
                    parameterCount = controllerParameters.Length,
                    layers,
                    parameters
                }
            };
        }

        public static object AssignToGameObject(JObject @params)
        {
            var controller = LoadController(@params, writable: false);
            if (controller == null)
                return ControllerNotFoundError(@params);

            var go = ObjectResolver.ResolveGameObject(@params["target"], @params["searchMethod"]?.ToString());
            if (go == null)
                return new { success = false, message = "Target GameObject not found" };

            var animator = go.GetComponent<Animator>();
            if (animator == null)
            {
                Undo.RecordObject(go, "Add Animator Component");
                animator = Undo.AddComponent<Animator>(go);
            }

            Undo.RecordObject(animator, "Assign AnimatorController");
            animator.runtimeAnimatorController = controller;
            EditorUtility.SetDirty(go);
            AssetDatabase.SaveAssets();

            return new
            {
                success = true,
                message = $"Assigned controller '{controller.name}' to '{go.name}'",
                data = new
                {
                    gameObject = go.name,
                    controllerName = controller.name,
                    controllerPath = AssetDatabase.GetAssetPath(controller)
                }
            };
        }

        private static AnimatorController LoadController(JObject @params, bool writable = true)
        {
            string controllerPath = @params["controllerPath"]?.ToString();
            if (string.IsNullOrEmpty(controllerPath))
                return null;

            controllerPath = writable
                ? AssetPathUtility.GetContainedAssetPath(controllerPath)
                : AssetPathUtility.GetAssetReferencePath(controllerPath, allowPackages: true);
            if (controllerPath == null)
                return null;

            return AssetDatabase.LoadAssetAtPath<AnimatorController>(controllerPath);
        }

        private static object ControllerNotFoundError(JObject @params)
        {
            string path = @params["controllerPath"]?.ToString() ?? "(not specified)";
            return new { success = false, message = $"AnimatorController not found at '{path}'. Provide a valid 'controllerPath'." };
        }
    }
}
