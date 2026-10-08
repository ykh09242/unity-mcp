using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Vfx
{
    internal static class ParticleWrite
    {
        private static void EnsureParticleRendererMaterial(ParticleSystemRenderer renderer)
        {
            if (renderer != null)
                RendererHelpers.EnsureMaterial(renderer);
        }

        public static object SetMain(JObject @params)
        {
            ParticleSystem ps = ParticleCommon.FindParticleSystem(@params);
            if (ps == null)
                return new { success = false, message = ParticleCommon.FindParticleSystemError(@params) };

            var main = ps.main;
            var changes = new List<string>();
            var updates = new List<Action>();

            if (@params["duration"] != null)
            {
                var value = @params["duration"].ReadScalar<float>();
                updates.Add(() => main.duration = value);
                changes.Add("duration");
            }
            if (@params["looping"] != null)
            {
                var value = @params["looping"].ReadScalar<bool>();
                updates.Add(() => main.loop = value);
                changes.Add("looping");
            }
            if (@params["prewarm"] != null)
            {
                var value = @params["prewarm"].ReadScalar<bool>();
                updates.Add(() => main.prewarm = value);
                changes.Add("prewarm");
            }
            if (@params["startDelay"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["startDelay"], 0f);
                updates.Add(() => main.startDelay = value);
                changes.Add("startDelay");
            }
            if (@params["startLifetime"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["startLifetime"], 5f);
                updates.Add(() => main.startLifetime = value);
                changes.Add("startLifetime");
            }
            if (@params["startSpeed"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["startSpeed"], 5f);
                updates.Add(() => main.startSpeed = value);
                changes.Add("startSpeed");
            }
            if (@params["startSize"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["startSize"], 1f);
                updates.Add(() => main.startSize = value);
                changes.Add("startSize");
            }
            if (@params["startRotation"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["startRotation"], 0f);
                updates.Add(() => main.startRotation = value);
                changes.Add("startRotation");
            }
            if (@params["startColor"] != null)
            {
                var value = ParticleCommon.ParseMinMaxGradient(@params["startColor"]);
                updates.Add(() => main.startColor = value);
                changes.Add("startColor");
            }
            if (@params["gravityModifier"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["gravityModifier"], 0f);
                updates.Add(() => main.gravityModifier = value);
                changes.Add("gravityModifier");
            }
            if (
                @params["simulationSpace"] != null
                && Enum.TryParse<ParticleSystemSimulationSpace>(@params["simulationSpace"].ToString(), true, out var simSpace)
            )
            {
                var value = simSpace;
                updates.Add(() => main.simulationSpace = value);
                changes.Add("simulationSpace");
            }
            if (@params["scalingMode"] != null && Enum.TryParse<ParticleSystemScalingMode>(@params["scalingMode"].ToString(), true, out var scaleMode))
            {
                var value = scaleMode;
                updates.Add(() => main.scalingMode = value);
                changes.Add("scalingMode");
            }
            if (@params["playOnAwake"] != null)
            {
                var value = @params["playOnAwake"].ReadScalar<bool>();
                updates.Add(() => main.playOnAwake = value);
                changes.Add("playOnAwake");
            }
            if (@params["maxParticles"] != null)
            {
                var value = @params["maxParticles"].ReadScalar<int>();
                updates.Add(() => main.maxParticles = value);
                changes.Add("maxParticles");
            }

            EnsureParticleRendererMaterial(ParticleCommon.FindParticleSystemRenderer(ps));

            // Stop particle system if it's playing and duration needs to be changed
            bool wasPlaying = ps.isPlaying;
            bool needsStop = @params["duration"] != null && wasPlaying;
            if (needsStop)
            {
                ps.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
            }

            Undo.RecordObject(ps, "Set ParticleSystem Main");
            foreach (var update in updates)
                update();
            EditorUtility.SetDirty(ps);

            // Restart particle system if it was playing
            if (needsStop && wasPlaying)
            {
                ps.Play(true);
                changes.Add("(restarted after duration change)");
            }

            return new { success = true, message = $"Updated: {string.Join(", ", changes)}" };
        }

        public static object SetEmission(JObject @params)
        {
            ParticleSystem ps = ParticleCommon.FindParticleSystem(@params);
            if (ps == null)
                return new { success = false, message = ParticleCommon.FindParticleSystemError(@params) };

            var emission = ps.emission;
            var changes = new List<string>();
            var updates = new List<Action>();

            if (@params["enabled"] != null)
            {
                var value = @params["enabled"].ReadScalar<bool>();
                updates.Add(() => emission.enabled = value);
                changes.Add("enabled");
            }
            if (@params["rateOverTime"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["rateOverTime"], 10f);
                updates.Add(() => emission.rateOverTime = value);
                changes.Add("rateOverTime");
            }
            if (@params["rateOverDistance"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["rateOverDistance"], 0f);
                updates.Add(() => emission.rateOverDistance = value);
                changes.Add("rateOverDistance");
            }

            EnsureParticleRendererMaterial(ParticleCommon.FindParticleSystemRenderer(ps));
            Undo.RecordObject(ps, "Set ParticleSystem Emission");
            foreach (var update in updates)
                update();
            EditorUtility.SetDirty(ps);
            return new { success = true, message = $"Updated emission: {string.Join(", ", changes)}" };
        }

        public static object SetShape(JObject @params)
        {
            ParticleSystem ps = ParticleCommon.FindParticleSystem(@params);
            if (ps == null)
                return new { success = false, message = ParticleCommon.FindParticleSystemError(@params) };

            var shape = ps.shape;
            var changes = new List<string>();
            var updates = new List<Action>();

            if (@params["enabled"] != null)
            {
                var value = @params["enabled"].ReadScalar<bool>();
                updates.Add(() => shape.enabled = value);
                changes.Add("enabled");
            }
            if (@params["shapeType"] != null && Enum.TryParse<ParticleSystemShapeType>(@params["shapeType"].ToString(), true, out var shapeType))
            {
                var value = shapeType;
                updates.Add(() => shape.shapeType = value);
                changes.Add("shapeType");
            }
            if (@params["radius"] != null)
            {
                var value = @params["radius"].ReadScalar<float>();
                updates.Add(() => shape.radius = value);
                changes.Add("radius");
            }
            if (@params["radiusThickness"] != null)
            {
                var value = @params["radiusThickness"].ReadScalar<float>();
                updates.Add(() => shape.radiusThickness = value);
                changes.Add("radiusThickness");
            }
            if (@params["angle"] != null)
            {
                var value = @params["angle"].ReadScalar<float>();
                updates.Add(() => shape.angle = value);
                changes.Add("angle");
            }
            if (@params["arc"] != null)
            {
                var value = @params["arc"].ReadScalar<float>();
                updates.Add(() => shape.arc = value);
                changes.Add("arc");
            }
            if (@params["position"] != null)
            {
                var value = ManageVfxCommon.ParseVector3(@params["position"]);
                updates.Add(() => shape.position = value);
                changes.Add("position");
            }
            if (@params["rotation"] != null)
            {
                var value = ManageVfxCommon.ParseVector3(@params["rotation"]);
                updates.Add(() => shape.rotation = value);
                changes.Add("rotation");
            }
            if (@params["scale"] != null)
            {
                var value = ManageVfxCommon.ParseVector3(@params["scale"]);
                updates.Add(() => shape.scale = value);
                changes.Add("scale");
            }

            EnsureParticleRendererMaterial(ParticleCommon.FindParticleSystemRenderer(ps));
            Undo.RecordObject(ps, "Set ParticleSystem Shape");
            foreach (var update in updates)
                update();
            EditorUtility.SetDirty(ps);
            return new { success = true, message = $"Updated shape: {string.Join(", ", changes)}" };
        }

        public static object SetColorOverLifetime(JObject @params)
        {
            ParticleSystem ps = ParticleCommon.FindParticleSystem(@params);
            if (ps == null)
                return new { success = false, message = ParticleCommon.FindParticleSystemError(@params) };

            var col = ps.colorOverLifetime;
            var changes = new List<string>();
            var updates = new List<Action>();

            if (@params["enabled"] != null)
            {
                var value = @params["enabled"].ReadScalar<bool>();
                updates.Add(() => col.enabled = value);
                changes.Add("enabled");
            }
            if (@params["color"] != null)
            {
                var value = ParticleCommon.ParseMinMaxGradient(@params["color"]);
                updates.Add(() => col.color = value);
                changes.Add("color");
            }

            EnsureParticleRendererMaterial(ParticleCommon.FindParticleSystemRenderer(ps));
            Undo.RecordObject(ps, "Set ParticleSystem Color Over Lifetime");
            foreach (var update in updates)
                update();
            EditorUtility.SetDirty(ps);
            return new { success = true, message = $"Updated: {string.Join(", ", changes)}" };
        }

        public static object SetSizeOverLifetime(JObject @params)
        {
            ParticleSystem ps = ParticleCommon.FindParticleSystem(@params);
            if (ps == null)
                return new { success = false, message = ParticleCommon.FindParticleSystemError(@params) };

            var sol = ps.sizeOverLifetime;
            var changes = new List<string>();
            var updates = new List<Action>();

            bool hasSizeProperty = @params["size"] != null || @params["sizeX"] != null || @params["sizeY"] != null || @params["sizeZ"] != null;
            if (hasSizeProperty && @params["enabled"] == null && !sol.enabled)
            {
                updates.Add(() => sol.enabled = true);
                changes.Add("enabled");
            }
            else if (@params["enabled"] != null)
            {
                bool enabled = @params["enabled"].ReadScalar<bool>();
                updates.Add(() => sol.enabled = enabled);
                changes.Add("enabled");
            }

            if (@params["separateAxes"] != null)
            {
                var value = @params["separateAxes"].ReadScalar<bool>();
                updates.Add(() => sol.separateAxes = value);
                changes.Add("separateAxes");
            }
            if (@params["size"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["size"], 1f);
                updates.Add(() => sol.size = value);
                changes.Add("size");
            }
            if (@params["sizeX"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["sizeX"], 1f);
                updates.Add(() => sol.x = value);
                changes.Add("sizeX");
            }
            if (@params["sizeY"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["sizeY"], 1f);
                updates.Add(() => sol.y = value);
                changes.Add("sizeY");
            }
            if (@params["sizeZ"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["sizeZ"], 1f);
                updates.Add(() => sol.z = value);
                changes.Add("sizeZ");
            }

            EnsureParticleRendererMaterial(ParticleCommon.FindParticleSystemRenderer(ps));
            Undo.RecordObject(ps, "Set ParticleSystem Size Over Lifetime");
            foreach (var update in updates)
                update();
            EditorUtility.SetDirty(ps);
            return new { success = true, message = $"Updated: {string.Join(", ", changes)}" };
        }

        public static object SetVelocityOverLifetime(JObject @params)
        {
            ParticleSystem ps = ParticleCommon.FindParticleSystem(@params);
            if (ps == null)
                return new { success = false, message = ParticleCommon.FindParticleSystemError(@params) };

            var vol = ps.velocityOverLifetime;
            var changes = new List<string>();
            var updates = new List<Action>();

            if (@params["enabled"] != null)
            {
                var value = @params["enabled"].ReadScalar<bool>();
                updates.Add(() => vol.enabled = value);
                changes.Add("enabled");
            }
            if (@params["space"] != null && Enum.TryParse<ParticleSystemSimulationSpace>(@params["space"].ToString(), true, out var space))
            {
                var value = space;
                updates.Add(() => vol.space = value);
                changes.Add("space");
            }
            if (@params["x"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["x"], 0f);
                updates.Add(() => vol.x = value);
                changes.Add("x");
            }
            if (@params["y"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["y"], 0f);
                updates.Add(() => vol.y = value);
                changes.Add("y");
            }
            if (@params["z"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["z"], 0f);
                updates.Add(() => vol.z = value);
                changes.Add("z");
            }
            if (@params["speedModifier"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["speedModifier"], 1f);
                updates.Add(() => vol.speedModifier = value);
                changes.Add("speedModifier");
            }

            EnsureParticleRendererMaterial(ParticleCommon.FindParticleSystemRenderer(ps));
            Undo.RecordObject(ps, "Set ParticleSystem Velocity Over Lifetime");
            foreach (var update in updates)
                update();
            EditorUtility.SetDirty(ps);
            return new { success = true, message = $"Updated: {string.Join(", ", changes)}" };
        }

        public static object SetNoise(JObject @params)
        {
            ParticleSystem ps = ParticleCommon.FindParticleSystem(@params);
            if (ps == null)
                return new { success = false, message = ParticleCommon.FindParticleSystemError(@params) };

            var noise = ps.noise;
            var changes = new List<string>();
            var updates = new List<Action>();

            if (@params["enabled"] != null)
            {
                var value = @params["enabled"].ReadScalar<bool>();
                updates.Add(() => noise.enabled = value);
                changes.Add("enabled");
            }
            if (@params["strength"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["strength"], 1f);
                updates.Add(() => noise.strength = value);
                changes.Add("strength");
            }
            if (@params["frequency"] != null)
            {
                var value = @params["frequency"].ReadScalar<float>();
                updates.Add(() => noise.frequency = value);
                changes.Add("frequency");
            }
            if (@params["scrollSpeed"] != null)
            {
                var value = ParticleCommon.ParseMinMaxCurve(@params["scrollSpeed"], 0f);
                updates.Add(() => noise.scrollSpeed = value);
                changes.Add("scrollSpeed");
            }
            if (@params["damping"] != null)
            {
                var value = @params["damping"].ReadScalar<bool>();
                updates.Add(() => noise.damping = value);
                changes.Add("damping");
            }
            if (@params["octaveCount"] != null)
            {
                var value = @params["octaveCount"].ReadScalar<int>();
                updates.Add(() => noise.octaveCount = value);
                changes.Add("octaveCount");
            }
            if (@params["quality"] != null && Enum.TryParse<ParticleSystemNoiseQuality>(@params["quality"].ToString(), true, out var quality))
            {
                var value = quality;
                updates.Add(() => noise.quality = value);
                changes.Add("quality");
            }

            EnsureParticleRendererMaterial(ParticleCommon.FindParticleSystemRenderer(ps));
            Undo.RecordObject(ps, "Set ParticleSystem Noise");
            foreach (var update in updates)
                update();
            EditorUtility.SetDirty(ps);
            return new { success = true, message = $"Updated noise: {string.Join(", ", changes)}" };
        }

        public static object SetRenderer(JObject @params)
        {
            ParticleSystem ps = ParticleCommon.FindParticleSystem(@params);
            if (ps == null)
                return new { success = false, message = ParticleCommon.FindParticleSystemError(@params) };

            var renderer = ParticleCommon.FindParticleSystemRenderer(ps);
            if (renderer == null)
                return new { success = false, message = $"ParticleSystemRenderer not found on '{ps.gameObject.name}'" };

            var changes = new List<string>();
            var updates = new List<Action>();

            if (@params["renderMode"] != null && Enum.TryParse<ParticleSystemRenderMode>(@params["renderMode"].ToString(), true, out var renderMode))
            {
                var value = renderMode;
                updates.Add(() => renderer.renderMode = value);
                changes.Add("renderMode");
            }
            if (@params["sortMode"] != null && Enum.TryParse<ParticleSystemSortMode>(@params["sortMode"].ToString(), true, out var sortMode))
            {
                var value = sortMode;
                updates.Add(() => renderer.sortMode = value);
                changes.Add("sortMode");
            }

            if (@params["minParticleSize"] != null)
            {
                var value = @params["minParticleSize"].ReadScalar<float>();
                updates.Add(() => renderer.minParticleSize = value);
                changes.Add("minParticleSize");
            }
            if (@params["maxParticleSize"] != null)
            {
                var value = @params["maxParticleSize"].ReadScalar<float>();
                updates.Add(() => renderer.maxParticleSize = value);
                changes.Add("maxParticleSize");
            }

            if (@params["lengthScale"] != null)
            {
                var value = @params["lengthScale"].ReadScalar<float>();
                updates.Add(() => renderer.lengthScale = value);
                changes.Add("lengthScale");
            }
            if (@params["velocityScale"] != null)
            {
                var value = @params["velocityScale"].ReadScalar<float>();
                updates.Add(() => renderer.velocityScale = value);
                changes.Add("velocityScale");
            }
            if (@params["cameraVelocityScale"] != null)
            {
                var value = @params["cameraVelocityScale"].ReadScalar<float>();
                updates.Add(() => renderer.cameraVelocityScale = value);
                changes.Add("cameraVelocityScale");
            }
            if (@params["normalDirection"] != null)
            {
                var value = @params["normalDirection"].ReadScalar<float>();
                updates.Add(() => renderer.normalDirection = value);
                changes.Add("normalDirection");
            }

            if (@params["alignment"] != null && Enum.TryParse<ParticleSystemRenderSpace>(@params["alignment"].ToString(), true, out var alignment))
            {
                var value = alignment;
                updates.Add(() => renderer.alignment = value);
                changes.Add("alignment");
            }
            if (@params["pivot"] != null)
            {
                var value = ManageVfxCommon.ParseVector3(@params["pivot"]);
                updates.Add(() => renderer.pivot = value);
                changes.Add("pivot");
            }
            if (@params["flip"] != null)
            {
                var value = ManageVfxCommon.ParseVector3(@params["flip"]);
                updates.Add(() => renderer.flip = value);
                changes.Add("flip");
            }
            if (@params["allowRoll"] != null)
            {
                var value = @params["allowRoll"].ReadScalar<bool>();
                updates.Add(() => renderer.allowRoll = value);
                changes.Add("allowRoll");
            }

            if (@params["shadowBias"] != null)
            {
                var value = @params["shadowBias"].ReadScalar<float>();
                updates.Add(() => renderer.shadowBias = value);
                changes.Add("shadowBias");
            }

            updates.Add(RendererHelpers.PrepareCommonRendererProperties(renderer, @params, changes));

            if (@params["materialPath"] != null)
            {
                string matPath = @params["materialPath"].ToString();
                var findInst = new JObject { ["find"] = matPath };
                Material mat = ObjectResolver.Resolve(findInst, typeof(Material)) as Material;
                if (mat != null)
                {
                    updates.Add(() => renderer.sharedMaterial = mat);
                    changes.Add($"material={mat.name}");
                }
                else
                {
                    McpLog.Warn($"Material not found at path: {matPath}. Keeping existing material.");
                }
            }

            if (@params["trailMaterialPath"] != null)
            {
                var findInst = new JObject { ["find"] = @params["trailMaterialPath"].ToString() };
                Material mat = ObjectResolver.Resolve(findInst, typeof(Material)) as Material;
                if (mat != null)
                {
                    var value = mat;
                    updates.Add(() => renderer.trailMaterial = value);
                    changes.Add("trailMaterial");
                }
            }

            Undo.RecordObject(renderer, "Set ParticleSystem Renderer");
            foreach (var update in updates)
                update();

            // Validate the final material after renderer edits to catch invalid pipeline shader assignments.
            var ensureResult = RendererHelpers.EnsureMaterial(renderer);

            EditorUtility.SetDirty(renderer);
            return new
            {
                success = true,
                message = $"Updated renderer: {string.Join(", ", changes)}",
                materialReplaced = ensureResult.MaterialReplaced,
                replacementReason = ensureResult.ReplacementReason,
            };
        }
    }
}
