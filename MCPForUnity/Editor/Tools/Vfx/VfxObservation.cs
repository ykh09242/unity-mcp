using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Graphics;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Vfx
{
    internal static class VfxObservation
    {
        internal static JObject ModuleEnabled(ParticleSystem system, string module)
        {
            try
            {
                bool? enabled = module.Replace("_", "") switch
                {
                    "emission" => system.emission.enabled,
                    "shape" => system.shape.enabled,
                    "coloroverlifetime" => system.colorOverLifetime.enabled,
                    "sizeoverlifetime" => system.sizeOverLifetime.enabled,
                    "velocityoverlifetime" => system.velocityOverLifetime.enabled,
                    "noise" => system.noise.enabled,
                    "collision" => system.collision.enabled,
                    "trails" => system.trails.enabled,
                    "lights" => system.lights.enabled,
                    _ => null,
                };
                return PostEditVerification.Summary(
                    "exact_particle_module_enabled",
                    new JObject { ["target_instance_id"] = system.GetInstanceIDCompat(), ["enabled"] = enabled },
                    complete: enabled.HasValue
                );
            }
            catch
            {
                return PostEditVerification.Unavailable("exact_particle_module_enabled", "observation_unavailable");
            }
        }

        internal static JObject Particle(ParticleSystem system, string module)
        {
            try
            {
                var value = new JObject { ["module"] = module };
                bool complete = true;
                switch (module)
                {
                    case "main":
                        var main = system.main;
                        value["duration"] = main.duration;
                        value["looping"] = main.loop;
                        value["prewarm"] = main.prewarm;
                        value["playOnAwake"] = main.playOnAwake;
                        value["maxParticles"] = main.maxParticles;
                        value["simulationSpace"] = main.simulationSpace.ToString();
                        value["scalingMode"] = main.scalingMode.ToString();
                        value["startDelay"] = Curve(main.startDelay, ref complete);
                        value["startLifetime"] = Curve(main.startLifetime, ref complete);
                        value["startSpeed"] = Curve(main.startSpeed, ref complete);
                        value["startSize"] = Curve(main.startSize, ref complete);
                        value["startRotation"] = Curve(main.startRotation, ref complete);
                        value["gravityModifier"] = Curve(main.gravityModifier, ref complete);
                        value["startColor"] = Gradient(main.startColor, ref complete);
                        break;
                    case "emission":
                        var emission = system.emission;
                        value["enabled"] = emission.enabled;
                        value["rateOverTime"] = Curve(emission.rateOverTime, ref complete);
                        value["rateOverDistance"] = Curve(emission.rateOverDistance, ref complete);
                        break;
                    case "shape":
                        var shape = system.shape;
                        value["enabled"] = shape.enabled;
                        value["shapeType"] = shape.shapeType.ToString();
                        value["radius"] = shape.radius;
                        value["radiusThickness"] = shape.radiusThickness;
                        value["angle"] = shape.angle;
                        value["arc"] = shape.arc;
                        value["scale"] = RenderingObservation.Vec(shape.scale);
                        value["position"] = RenderingObservation.Vec(shape.position);
                        value["rotation"] = RenderingObservation.Vec(shape.rotation);
                        break;
                    case "color":
                        var color = system.colorOverLifetime;
                        value["enabled"] = color.enabled;
                        value["color"] = Gradient(color.color, ref complete);
                        break;
                    case "size":
                        var size = system.sizeOverLifetime;
                        value["enabled"] = size.enabled;
                        value["separateAxes"] = size.separateAxes;
                        value["x"] = Curve(size.x, ref complete);
                        value["y"] = Curve(size.y, ref complete);
                        value["z"] = Curve(size.z, ref complete);
                        break;
                    case "velocity":
                        var velocity = system.velocityOverLifetime;
                        value["enabled"] = velocity.enabled;
                        value["space"] = velocity.space.ToString();
                        value["x"] = Curve(velocity.x, ref complete);
                        value["y"] = Curve(velocity.y, ref complete);
                        value["z"] = Curve(velocity.z, ref complete);
                        value["speedModifier"] = Curve(velocity.speedModifier, ref complete);
                        break;
                    case "noise":
                        var noise = system.noise;
                        value["enabled"] = noise.enabled;
                        value["frequency"] = noise.frequency;
                        value["octaveCount"] = noise.octaveCount;
                        value["damping"] = noise.damping;
                        value["quality"] = noise.quality.ToString();
                        value["strength"] = Curve(noise.strength, ref complete);
                        value["scrollSpeed"] = Curve(noise.scrollSpeed, ref complete);
                        break;
                    default:
                        complete = false;
                        break;
                }
                var report = PostEditVerification.Summary("exact_particle_module; max32_keys_per_curve", value, complete: complete);
                report["target_instance_id"] = system.GetInstanceIDCompat();
                return report;
            }
            catch
            {
                return PostEditVerification.Unavailable("exact_particle_module", "observation_unavailable");
            }
        }

        internal static JObject Renderer(Renderer renderer)
        {
            try
            {
                var value = new JObject { ["renderer_instance_id"] = renderer.GetInstanceIDCompat(), ["enabled"] = renderer.enabled };
                value["sorting_layer_id"] = renderer.sortingLayerID;
                value["sorting_order"] = renderer.sortingOrder;
                value["shadow_casting_mode"] = renderer.shadowCastingMode.ToString();
                value["receive_shadows"] = renderer.receiveShadows;
                var findings = new JArray();
                Material material = renderer.sharedMaterial;
                value["shared_material_instance_id"] = material != null ? material.GetInstanceIDCompat() : 0;
                if (material == null)
                    findings.Add(new JObject { ["code"] = "missing_material" });
                bool complete = true;
                if (renderer is LineRenderer line)
                {
                    value["position_count"] = line.positionCount;
                    value["use_world_space"] = line.useWorldSpace;
                    value["loop"] = line.loop;
                    value["width_multiplier"] = line.widthMultiplier;
                    value["start_color"] = Color(line.startColor);
                    value["end_color"] = Color(line.endColor);
                    value["width_curve"] = "not_observed_without_copy";
                    value["alignment"] = line.alignment.ToString();
                    value["texture_mode"] = line.textureMode.ToString();
                    value["corner_vertices"] = line.numCornerVertices;
                    value["cap_vertices"] = line.numCapVertices;
                    value["generate_lighting_data"] = line.generateLightingData;
                    value["gradient_keys"] = "not_observed_without_copy";
                    complete = false;
                    complete &= line.positionCount <= 16;
                    var points = new JArray();
                    for (int i = 0; i < Math.Min(16, line.positionCount); i++)
                        points.Add(RenderingObservation.Vec(line.GetPosition(i)));
                    value["positions"] = points;
                }
                else if (renderer is TrailRenderer trail)
                {
                    value["time"] = trail.time;
                    value["emitting"] = trail.emitting;
                    value["min_vertex_distance"] = trail.minVertexDistance;
                    value["width_multiplier"] = trail.widthMultiplier;
                    value["start_color"] = Color(trail.startColor);
                    value["end_color"] = Color(trail.endColor);
                    value["width_curve"] = "not_observed_without_copy";
                    value["alignment"] = trail.alignment.ToString();
                    value["texture_mode"] = trail.textureMode.ToString();
                    value["corner_vertices"] = trail.numCornerVertices;
                    value["cap_vertices"] = trail.numCapVertices;
                    value["autodestruct"] = trail.autodestruct;
                    value["generate_lighting_data"] = trail.generateLightingData;
                    value["gradient_keys"] = "not_observed_without_copy";
                    complete = false;
                }
                else if (renderer is ParticleSystemRenderer particle)
                {
                    value["render_mode"] = particle.renderMode.ToString();
                    value["sort_mode"] = particle.sortMode.ToString();
                    value["min_particle_size"] = particle.minParticleSize;
                    value["max_particle_size"] = particle.maxParticleSize;
                    value["alignment"] = particle.alignment.ToString();
                    value["pivot"] = RenderingObservation.Vec(particle.pivot);
                    value["length_scale"] = particle.lengthScale;
                    value["velocity_scale"] = particle.velocityScale;
                }
                return PostEditVerification.Summary("exact_vfx_renderer; max16_positions_32_curve_keys", value, findings, complete);
            }
            catch
            {
                return PostEditVerification.Unavailable("exact_vfx_renderer", "observation_unavailable");
            }
        }

        internal static object Attach(object response, Renderer renderer)
        {
            var json = JObject.FromObject(response);
            if ((bool?)json["success"] != true)
                return response;
            json["data"] ??= new JObject();
            json["data"]["verification"] = Renderer(renderer);
            return json;
        }

        private static JObject Curve(ParticleSystem.MinMaxCurve curve, ref bool complete)
        {
            var value = new JObject
            {
                ["mode"] = curve.mode.ToString(),
                ["constant_min"] = curve.constantMin,
                ["constant_max"] = curve.constantMax,
                ["multiplier"] = curve.curveMultiplier,
            };
            if (curve.mode == ParticleSystemCurveMode.Curve || curve.mode == ParticleSystemCurveMode.TwoCurves)
            {
                value["curve_keys"] = "not_observed_without_copy";
                complete = false;
            }
            return value;
        }

        private static JObject Gradient(ParticleSystem.MinMaxGradient gradient, ref bool complete)
        {
            // Unity exposes gradient key arrays only as copies. Do not allocate them automatically.
            complete &= gradient.mode == ParticleSystemGradientMode.Color || gradient.mode == ParticleSystemGradientMode.TwoColors;
            return new JObject
            {
                ["mode"] = gradient.mode.ToString(),
                ["color"] = Color(gradient.color),
                ["color_min"] = Color(gradient.colorMin),
                ["color_max"] = Color(gradient.colorMax),
            };
        }

        private static JArray Color(Color color) => new JArray(color.r, color.g, color.b, color.a);
    }
}
