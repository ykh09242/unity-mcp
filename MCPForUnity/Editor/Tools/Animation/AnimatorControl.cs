using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Animation
{
    internal static class AnimatorControl
    {
        private static string ReadName(JObject parameters, string key)
        {
            JToken token = parameters[key];
            // Command deserialization can turn a JSON ISO string into a Date token.
            if (token != null && token.Type != JTokenType.String && token.Type != JTokenType.Date && token.Type != JTokenType.Null)
                throw new ArgumentException($"'{key}' must be a string.");
            return token?.ToString();
        }

        public static object Play(JObject @params)
        {
            var go = ObjectResolver.ResolveGameObject(@params["target"], @params["searchMethod"]?.ToString());
            if (go == null)
                return new { success = false, message = "Target GameObject not found" };

            var animator = AnimatorResolver.Find(go, out var animatorCandidates);
            if (animator == null)
                return AnimatorResolver.NotResolvedError(go, animatorCandidates);

            string stateName = ReadName(@params, "stateName");
            if (string.IsNullOrEmpty(stateName))
                return new { success = false, message = "'stateName' is required" };

            int layer = @params["layer"]?.ReadScalar<int?>() ?? -1;
            if (layer < -1 || (layer >= 0 && layer >= animator.layerCount))
                return new { success = false, message = $"Layer index {layer} is out of range. Use -1 or a valid Animator layer index." };

            Undo.RecordObject(animator, "Play Animation State");
            animator.Play(stateName, layer);

            return new { success = true, message = $"Playing state '{stateName}' on {AnimatorResolver.Describe(go, animator)}" };
        }

        public static object Crossfade(JObject @params)
        {
            var go = ObjectResolver.ResolveGameObject(@params["target"], @params["searchMethod"]?.ToString());
            if (go == null)
                return new { success = false, message = "Target GameObject not found" };

            var animator = AnimatorResolver.Find(go, out var animatorCandidates);
            if (animator == null)
                return AnimatorResolver.NotResolvedError(go, animatorCandidates);

            string stateName = ReadName(@params, "stateName");
            if (string.IsNullOrEmpty(stateName))
                return new { success = false, message = "'stateName' is required" };

            float duration = @params["duration"]?.ReadScalar<float?>() ?? 0.25f;
            int layer = @params["layer"]?.ReadScalar<int?>() ?? -1;
            if (layer < -1 || (layer >= 0 && layer >= animator.layerCount))
                return new { success = false, message = $"Layer index {layer} is out of range. Use -1 or a valid Animator layer index." };

            Undo.RecordObject(animator, "Crossfade Animation State");
            animator.CrossFadeInFixedTime(stateName, duration, layer);

            return new { success = true, message = $"Crossfading to '{stateName}' over {duration}s on {AnimatorResolver.Describe(go, animator)}" };
        }

        public static object SetParameter(JObject @params)
        {
            var go = ObjectResolver.ResolveGameObject(@params["target"], @params["searchMethod"]?.ToString());
            if (go == null)
                return new { success = false, message = "Target GameObject not found" };

            var animator = AnimatorResolver.Find(go, out var animatorCandidates);
            if (animator == null)
                return AnimatorResolver.NotResolvedError(go, animatorCandidates);

            string paramName = ReadName(@params, "parameterName");
            if (string.IsNullOrEmpty(paramName))
                return new { success = false, message = "'parameterName' is required" };

            bool isPlaying = Application.isPlaying;
            AnimatorController controller = null;
            AnimatorControllerParameter[] allParams = null;
            AnimatorControllerParameter found = null;
            int paramIndex = -1;

            if (isPlaying)
            {
                int parameterCount = animator.parameterCount;
                for (int i = 0; i < parameterCount; i++)
                {
                    var parameter = animator.GetParameter(i);
                    if (parameter.name == paramName)
                    {
                        found = parameter;
                        break;
                    }
                }
            }
            else
            {
                // The controller owns Edit-mode definitions/defaults even without an active Animator graph.
                controller = animator.runtimeAnimatorController as AnimatorController;
                if (controller == null)
                    return new
                    {
                        success = false,
                        message = $"No AnimatorController assigned to the Animator on {AnimatorResolver.Describe(go, animator)}. Cannot set parameter defaults in Edit mode.",
                    };

                allParams = controller.parameters;
                for (int i = 0; i < allParams.Length; i++)
                {
                    if (allParams[i].name == paramName)
                    {
                        found = allParams[i];
                        paramIndex = i;
                        break;
                    }
                }
            }

            if (found == null)
                return new
                {
                    success = false,
                    message = $"Parameter '{paramName}' not found on {(isPlaying ? "Animator" : $"controller '{controller.name}'")}.",
                };

            string paramType = @params["parameterType"]?.ToString()?.ToLowerInvariant();
            if (string.IsNullOrEmpty(paramType))
                paramType = found.type.ToString().ToLowerInvariant();
            if (paramType == "integer")
                paramType = "int";
            if (paramType == "boolean")
                paramType = "bool";

            AnimatorControllerParameterType requestedType;
            switch (paramType)
            {
                case "float":
                    requestedType = AnimatorControllerParameterType.Float;
                    break;
                case "int":
                    requestedType = AnimatorControllerParameterType.Int;
                    break;
                case "bool":
                    requestedType = AnimatorControllerParameterType.Bool;
                    break;
                case "trigger":
                    requestedType = AnimatorControllerParameterType.Trigger;
                    break;
                default:
                    return new { success = false, message = $"Unknown parameter type: {paramType}. Valid: float, int, bool, trigger" };
            }
            if (requestedType != found.type)
                return new { success = false, message = $"Parameter '{paramName}' has type '{found.type}', not '{paramType}'." };

            JToken valueToken = @params["value"];
            float fVal = 0f;
            int iVal = 0;
            bool bVal = false;
            switch (paramType)
            {
                case "float":
                    fVal = valueToken?.ReadScalar<float?>() ?? 0f;
                    break;
                case "int":
                    iVal = valueToken?.ReadScalar<int?>() ?? 0;
                    break;
                case "bool":
                    bVal = valueToken?.ReadScalar<bool?>() ?? false;
                    break;
            }

            if (isPlaying)
            {
                Undo.RecordObject(animator, $"Set Animator Parameter {paramName}");
                switch (paramType)
                {
                    case "float":
                        animator.SetFloat(paramName, fVal);
                        return new { success = true, message = $"Set float '{paramName}' = {fVal}" + AnimatorResolver.ResolvedSuffix(go, animator) };
                    case "int":
                        animator.SetInteger(paramName, iVal);
                        return new { success = true, message = $"Set int '{paramName}' = {iVal}" + AnimatorResolver.ResolvedSuffix(go, animator) };
                    case "bool":
                        animator.SetBool(paramName, bVal);
                        return new { success = true, message = $"Set bool '{paramName}' = {bVal}" + AnimatorResolver.ResolvedSuffix(go, animator) };
                    default:
                        animator.SetTrigger(paramName);
                        return new { success = true, message = $"Set trigger '{paramName}'" + AnimatorResolver.ResolvedSuffix(go, animator) };
                }
            }

            if (paramType == "trigger")
                return new
                {
                    success = true,
                    message = $"Trigger '{paramName}' noted (triggers are runtime-only, no default to set)" + AnimatorResolver.ResolvedSuffix(go, animator),
                };

            Undo.RecordObject(controller, $"Set Parameter Default {paramName}");
            string valueDescription;
            switch (paramType)
            {
                case "float":
                    allParams[paramIndex].defaultFloat = fVal;
                    valueDescription = $"{fVal}";
                    break;
                case "int":
                    allParams[paramIndex].defaultInt = iVal;
                    valueDescription = $"{iVal}";
                    break;
                default:
                    allParams[paramIndex].defaultBool = bVal;
                    valueDescription = $"{bVal}";
                    break;
            }
            controller.parameters = allParams;
            EditorUtility.SetDirty(controller);
            AssetDatabase.SaveAssets();
            return new
            {
                success = true,
                message = $"Set {paramType} '{paramName}' = {valueDescription} (default value, Edit mode)" + AnimatorResolver.ResolvedSuffix(go, animator),
            };
        }

        public static object SetSpeed(JObject @params)
        {
            var go = ObjectResolver.ResolveGameObject(@params["target"], @params["searchMethod"]?.ToString());
            if (go == null)
                return new { success = false, message = "Target GameObject not found" };

            var animator = AnimatorResolver.Find(go, out var animatorCandidates);
            if (animator == null)
                return AnimatorResolver.NotResolvedError(go, animatorCandidates);

            float speed = @params["speed"]?.ReadScalar<float?>() ?? 1f;

            Undo.RecordObject(animator, "Set Animator Speed");
            animator.speed = speed;

            return new { success = true, message = $"Set animator speed to {speed} on {AnimatorResolver.Describe(go, animator)}" };
        }

        public static object SetEnabled(JObject @params)
        {
            var go = ObjectResolver.ResolveGameObject(@params["target"], @params["searchMethod"]?.ToString());
            if (go == null)
                return new { success = false, message = "Target GameObject not found" };

            var animator = AnimatorResolver.Find(go, out var animatorCandidates);
            if (animator == null)
                return AnimatorResolver.NotResolvedError(go, animatorCandidates);

            bool enabled = @params["enabled"]?.ReadScalar<bool?>() ?? true;

            Undo.RecordObject(animator, "Set Animator Enabled");
            animator.enabled = enabled;

            return new { success = true, message = $"Animator {(enabled ? "enabled" : "disabled")} on {AnimatorResolver.Describe(go, animator)}" };
        }
    }
}
