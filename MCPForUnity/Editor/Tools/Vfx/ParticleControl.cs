using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Vfx
{
    internal static class ParticleControl
    {
        public static object Create(JObject @params)
        {
            string target = @params["target"]?.ToString();
            if (string.IsNullOrWhiteSpace(target))
            {
                return new { success = false, message = "target is required for particle_create" };
            }

            Vector3? position = @params["position"] != null ? ManageVfxCommon.ParseVector3(@params["position"]) : null;
            Vector3? rotation = @params["rotation"] != null ? ManageVfxCommon.ParseVector3(@params["rotation"]) : null;
            Vector3? scale = @params["scale"] != null ? ManageVfxCommon.ParseVector3(@params["scale"]) : null;
            bool? playOnAwake = @params["playOnAwake"] != null ? @params["playOnAwake"].ReadScalar<bool>() : null;
            bool? looping = @params["looping"] != null ? @params["looping"].ReadScalar<bool>() : null;

            GameObject go = ManageVfxCommon.FindTargetGameObject(@params);
            bool createdGameObject = false;
            bool addedParticleSystem = false;

            if (go == null)
            {
                string objectName = target;
                int slashIndex = target.LastIndexOf('/');
                if (slashIndex >= 0 && slashIndex < target.Length - 1)
                {
                    objectName = target.Substring(slashIndex + 1);
                }

                go = new GameObject(objectName);
                createdGameObject = true;

                if (!EditorApplication.isPlaying)
                {
                    Undo.RegisterCreatedObjectUndo(go, $"Create {objectName}");
                }
            }

            if (position.HasValue)
            {
                go.transform.position = position.Value;
            }
            if (rotation.HasValue)
            {
                go.transform.eulerAngles = rotation.Value;
            }
            if (scale.HasValue)
            {
                go.transform.localScale = scale.Value;
            }

            var ps = go.GetComponent<ParticleSystem>();
            if (ps == null)
            {
                ps = go.AddComponent<ParticleSystem>();
                addedParticleSystem = true;

                // Apply sensible defaults so newly created particles aren't oversized.
                RendererHelpers.SetSensibleParticleDefaults(ps);
            }

            var renderer = go.GetComponent<ParticleSystemRenderer>();
            if (renderer != null)
            {
                RendererHelpers.EnsureMaterial(renderer);
            }

            // Allow caller overrides for playOnAwake and looping.
            var main = ps.main;
            if (playOnAwake.HasValue)
            {
                main.playOnAwake = playOnAwake.Value;
            }
            if (looping.HasValue)
            {
                main.loop = looping.Value;
            }

            EditorUtility.SetDirty(go);
            if (!EditorApplication.isPlaying)
            {
                UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(UnityEditor.SceneManagement.EditorSceneManager.GetActiveScene());
            }

            return new
            {
                success = true,
                message = $"ParticleSystem ready on '{go.name}'",
                target = go.name,
                targetId = go.GetInstanceIDCompat(),
                createdGameObject,
                addedParticleSystem,
                assignedMaterial = renderer?.sharedMaterial?.name,
            };
        }

        public static object EnableModule(JObject @params)
        {
            ParticleSystem ps = ParticleCommon.FindParticleSystem(@params);
            if (ps == null)
                return new { success = false, message = ParticleCommon.FindParticleSystemError(@params) };

            string moduleName = @params["module"]?.ToString()?.ToLowerInvariant();
            bool enabled = @params["enabled"]?.ReadScalar<bool?>() ?? true;

            if (string.IsNullOrEmpty(moduleName))
                return new { success = false, message = "Module name required" };

            Action update;

            switch (moduleName.Replace("_", ""))
            {
                case "emission":
                    var em = ps.emission;
                    update = () => em.enabled = enabled;
                    break;
                case "shape":
                    var sh = ps.shape;
                    update = () => sh.enabled = enabled;
                    break;
                case "coloroverlifetime":
                    var col = ps.colorOverLifetime;
                    update = () => col.enabled = enabled;
                    break;
                case "sizeoverlifetime":
                    var sol = ps.sizeOverLifetime;
                    update = () => sol.enabled = enabled;
                    break;
                case "velocityoverlifetime":
                    var vol = ps.velocityOverLifetime;
                    update = () => vol.enabled = enabled;
                    break;
                case "noise":
                    var n = ps.noise;
                    update = () => n.enabled = enabled;
                    break;
                case "collision":
                    var coll = ps.collision;
                    update = () => coll.enabled = enabled;
                    break;
                case "trails":
                    var tr = ps.trails;
                    update = () => tr.enabled = enabled;
                    break;
                case "lights":
                    var li = ps.lights;
                    update = () => li.enabled = enabled;
                    break;
                default:
                    return new { success = false, message = $"Unknown module: {moduleName}" };
            }

            Undo.RecordObject(ps, $"Toggle {moduleName}");
            update();
            EditorUtility.SetDirty(ps);
            return new { success = true, message = $"Module '{moduleName}' {(enabled ? "enabled" : "disabled")}" };
        }

        public static object Control(JObject @params, string action)
        {
            ParticleSystem ps = ParticleCommon.FindParticleSystem(@params);
            if (ps == null)
                return new { success = false, message = ParticleCommon.FindParticleSystemError(@params) };

            bool withChildren = @params["withChildren"]?.ReadScalar<bool?>() ?? true;

            RendererHelpers.EnsureMaterialResult ensureResult = default;
            bool materialChecked = false;

            // Ensure material is assigned before playing
            if (action == "play" || action == "restart")
            {
                var renderer = ParticleCommon.FindParticleSystemRenderer(ps);
                if (renderer != null)
                {
                    ensureResult = RendererHelpers.EnsureMaterial(renderer);
                    materialChecked = true;
                }
            }

            switch (action)
            {
                case "play":
                    ps.Play(withChildren);
                    break;
                case "stop":
                    ps.Stop(withChildren, ParticleSystemStopBehavior.StopEmitting);
                    break;
                case "pause":
                    ps.Pause(withChildren);
                    break;
                case "restart":
                    ps.Stop(withChildren, ParticleSystemStopBehavior.StopEmittingAndClear);
                    ps.Play(withChildren);
                    break;
                case "clear":
                    ps.Clear(withChildren);
                    break;
                default:
                    return new { success = false, message = $"Unknown action: {action}" };
            }

            return new
            {
                success = true,
                message = $"ParticleSystem {action}",
                materialReplaced = materialChecked ? ensureResult.MaterialReplaced : false,
                replacementReason = materialChecked ? ensureResult.ReplacementReason : string.Empty,
            };
        }

        public static object AddBurst(JObject @params)
        {
            ParticleSystem ps = ParticleCommon.FindParticleSystem(@params);
            if (ps == null)
                return new { success = false, message = ParticleCommon.FindParticleSystemError(@params) };

            float time = @params["time"]?.ReadScalar<float?>() ?? 0f;
            int minCountRaw = @params["minCount"]?.ReadScalar<int?>() ?? @params["count"]?.ReadScalar<int?>() ?? 30;
            int maxCountRaw = @params["maxCount"]?.ReadScalar<int?>() ?? @params["count"]?.ReadScalar<int?>() ?? 30;
            short minCount = (short)Math.Clamp(minCountRaw, 0, short.MaxValue);
            short maxCount = (short)Math.Clamp(maxCountRaw, 0, short.MaxValue);
            int cycles = @params["cycles"]?.ReadScalar<int?>() ?? 1;
            float interval = @params["interval"]?.ReadScalar<float?>() ?? 0.01f;
            var burst = new ParticleSystem.Burst(time, minCount, maxCount, cycles, interval);
            burst.probability = @params["probability"]?.ReadScalar<float?>() ?? 1f;

            // Ensure material is assigned
            var renderer = ParticleCommon.FindParticleSystemRenderer(ps);
            RendererHelpers.EnsureMaterialResult ensureResult = default;
            bool materialChecked = false;
            if (renderer != null)
            {
                ensureResult = RendererHelpers.EnsureMaterial(renderer);
                materialChecked = true;
            }

            Undo.RecordObject(ps, "Add Burst");
            var emission = ps.emission;

            int idx = emission.burstCount;
            var bursts = new ParticleSystem.Burst[idx + 1];
            emission.GetBursts(bursts);
            bursts[idx] = burst;
            emission.SetBursts(bursts);

            EditorUtility.SetDirty(ps);
            return new
            {
                success = true,
                message = $"Added burst at t={time}",
                burstIndex = idx,
                materialReplaced = materialChecked ? ensureResult.MaterialReplaced : false,
                replacementReason = materialChecked ? ensureResult.ReplacementReason : string.Empty,
            };
        }

        public static object ClearBursts(JObject @params)
        {
            ParticleSystem ps = ParticleCommon.FindParticleSystem(@params);
            if (ps == null)
                return new { success = false, message = ParticleCommon.FindParticleSystemError(@params) };

            Undo.RecordObject(ps, "Clear Bursts");
            var emission = ps.emission;
            int count = emission.burstCount;
            emission.SetBursts(new ParticleSystem.Burst[0]);

            EditorUtility.SetDirty(ps);
            return new { success = true, message = $"Cleared {count} bursts" };
        }
    }
}
