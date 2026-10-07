using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor.Animations;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Animation
{
    internal static class AnimatorRead
    {
        public static object GetInfo(JObject @params)
        {
            var go = ObjectResolver.ResolveGameObject(@params["target"], @params["searchMethod"]?.ToString());
            if (go == null)
                return new { success = false, message = "Target GameObject not found" };

            var animator = AnimatorResolver.Find(go, out var animatorCandidates);
            if (animator == null)
                return AnimatorResolver.NotResolvedError(go, animatorCandidates);

            var runtimeController = animator.runtimeAnimatorController;
            bool isPlaying = Application.isPlaying;
            var controller = runtimeController as AnimatorController;
            if (!isPlaying && runtimeController is AnimatorOverrideController overrideController)
                controller = overrideController.runtimeAnimatorController as AnimatorController;
            var definitions = !isPlaying && controller != null ? controller.parameters : animator.parameters;
            var parameters = new List<object>();
            foreach (var p in definitions)
            {
                parameters.Add(
                    new
                    {
                        name = p.name,
                        type = p.type.ToString(),
                        defaultFloat = p.defaultFloat,
                        defaultInt = p.defaultInt,
                        defaultBool = p.defaultBool,
                    }
                );
            }

            var layers = new List<object>();
            int layerCount = animator.layerCount;
            for (int i = 0; i < layerCount; i++)
            {
                bool isInTransition = animator.IsInTransition(i);
                var stateInfo = animator.GetCurrentAnimatorStateInfo(i);

                layers.Add(
                    new
                    {
                        index = i,
                        name = animator.GetLayerName(i),
                        weight = animator.GetLayerWeight(i),
                        currentStateHash = stateInfo.fullPathHash,
                        currentStateNormalizedTime = stateInfo.normalizedTime,
                        currentStateLength = stateInfo.length,
                        isInTransition,
                    }
                );
            }

            var clips = new List<object>();
            if (runtimeController != null)
            {
                foreach (var clip in runtimeController.animationClips)
                {
                    clips.Add(
                        new
                        {
                            name = clip.name,
                            length = clip.length,
                            frameRate = clip.frameRate,
                            isLooping = clip.isLooping,
                            wrapMode = clip.wrapMode.ToString(),
                        }
                    );
                }
            }

            return new
            {
                success = true,
                data = new
                {
                    gameObject = go.name,
                    animatorGameObject = animator.gameObject.name,
                    enabled = animator.enabled,
                    speed = animator.speed,
                    hasController = runtimeController != null,
                    controllerName = runtimeController?.name,
                    applyRootMotion = animator.applyRootMotion,
                    updateMode = animator.updateMode.ToString(),
                    cullingMode = animator.cullingMode.ToString(),
                    parameterCount = definitions.Length,
                    layerCount,
                    parameters,
                    layers,
                    clips,
                },
            };
        }

        public static object GetParameter(JObject @params)
        {
            var go = ObjectResolver.ResolveGameObject(@params["target"], @params["searchMethod"]?.ToString());
            if (go == null)
                return new { success = false, message = "Target GameObject not found" };

            var animator = AnimatorResolver.Find(go, out var animatorCandidates);
            if (animator == null)
                return AnimatorResolver.NotResolvedError(go, animatorCandidates);

            string paramName = @params["parameterName"]?.ToString();
            if (string.IsNullOrEmpty(paramName))
                return new { success = false, message = "'parameterName' is required" };

            bool isPlaying = Application.isPlaying;
            var runtimeController = animator.runtimeAnimatorController;
            var controller = runtimeController as AnimatorController;
            if (!isPlaying && runtimeController is AnimatorOverrideController overrideController)
                controller = overrideController.runtimeAnimatorController as AnimatorController;
            var definitions = !isPlaying && controller != null ? controller.parameters : animator.parameters;
            AnimatorControllerParameter found = null;
            foreach (var p in definitions)
            {
                if (p.name == paramName)
                {
                    found = p;
                    break;
                }
            }

            if (found == null)
                return new { success = false, message = $"Parameter '{paramName}' not found on Animator" };

            object value;
            switch (found.type)
            {
                case AnimatorControllerParameterType.Float:
                    value = isPlaying ? animator.GetFloat(paramName) : found.defaultFloat;
                    break;
                case AnimatorControllerParameterType.Int:
                    value = isPlaying ? animator.GetInteger(paramName) : found.defaultInt;
                    break;
                case AnimatorControllerParameterType.Bool:
                    value = isPlaying ? animator.GetBool(paramName) : found.defaultBool;
                    break;
                case AnimatorControllerParameterType.Trigger:
                    value = animator.GetBool(paramName);
                    break;
                default:
                    value = null;
                    break;
            }

            return new
            {
                success = true,
                data = new
                {
                    name = found.name,
                    type = found.type.ToString(),
                    value,
                },
            };
        }
    }
}
