using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.Graphics
{
    /// <summary>Bounded retained-state observations; never constructs renderers or executes rendering.</summary>
    internal static class RenderingObservation
    {
        internal static JObject Properties(Object target, JObject requested, IEnumerable<string> failed = null)
        {
            try
            {
                JObject report = PostEditVerification.Properties(target, requested);
                if (failed != null)
                {
                    var findings = (JArray)report["findings"];
                    foreach (string field in failed.Take(32))
                        if (findings.Count < PostEditVerification.MaxFindings)
                            findings.Add(new JObject { ["code"] = "field_not_applied", ["field"] = Clip(field) });
                    if (findings.Count > 0)
                    {
                        report["status"] = "issues_found";
                        report["further_fixes_required"] = true;
                    }
                }
                return report;
            }
            catch
            {
                return PostEditVerification.Unavailable("exact_rendering_object", "observation_unavailable");
            }
        }

        internal static JObject Read(Object target, params string[] paths)
        {
            try
            {
                return PostEditVerification.Readback(target, paths);
            }
            catch
            {
                return PostEditVerification.Unavailable("exact_rendering_object", "observation_unavailable");
            }
        }

        internal static JObject ReadKnown(Object target, params string[] paths)
        {
            try
            {
                using var serialized = new SerializedObject(target);
                var existing = new List<string>();
                foreach (string path in paths.Take(64))
                    if (serialized.FindProperty(path) != null)
                        existing.Add(path);
                return existing.Count == 0
                    ? PostEditVerification.Unavailable("exact_rendering_object", "unsupported_serialized_fields")
                    : Read(target, existing.ToArray());
            }
            catch
            {
                return PostEditVerification.Unavailable("exact_rendering_object", "observation_unavailable");
            }
        }

        internal static JObject Pending(JObject report, string reason)
        {
            report["complete"] = false;
            if ((string)report["status"] == "passed")
                report["status"] = "pending";
            report["reason"] = reason;
            if ((string)report["status"] != "issues_found")
                report["further_fixes_required"] = JValue.CreateNull();
            return report;
        }

        internal static JObject Renderer(
            ScriptableObject renderer,
            ScriptableObject feature = null,
            JObject requested = null,
            IEnumerable<string> failed = null
        )
        {
            try
            {
                var observed = new JObject
                {
                    ["renderer_instance_id"] = renderer.GetInstanceIDCompat(),
                    ["feature_instance_id"] = feature != null ? feature.GetInstanceIDCompat() : 0,
                };
                var findings = new JArray();
                using var serialized = new SerializedObject(renderer);
                using var entries = serialized.FindProperty("m_RendererFeatures");
                bool complete = entries != null && entries.isArray;
                var features = new JArray();
                if (complete)
                {
                    observed["feature_count"] = entries.arraySize;
                    complete = entries.arraySize <= 16;
                    for (int index = 0; index < Math.Min(16, entries.arraySize); index++)
                    {
                        using var entry = entries.GetArrayElementAtIndex(index);
                        Object value = entry.objectReferenceValue;
                        features.Add(new JObject { ["index"] = index, ["instance_id"] = value != null ? value.GetInstanceIDCompat() : 0 });
                        if (value == null)
                            findings.Add(new JObject { ["code"] = "missing_renderer_feature", ["index"] = index });
                    }
                }
                observed["features"] = features;
                if (feature != null)
                {
                    JObject retained = Properties(feature, requested, failed);
                    observed["retained_fields"] = retained;
                    complete &= (bool)retained["complete"];
                    foreach (JToken finding in (JArray)retained["findings"])
                        if (findings.Count < PostEditVerification.MaxFindings)
                            findings.Add(finding.DeepClone());
                }
                return PostEditVerification.Summary("exact_renderer; max16_feature_references; no_callbacks", observed, findings, complete);
            }
            catch
            {
                return PostEditVerification.Unavailable("exact_renderer", "observation_unavailable");
            }
        }

        internal static JObject Material(
            Material material,
            IEnumerable<string> properties,
            Renderer renderer = null,
            int slot = 0,
            string ownership = "shared",
            JToken expected = null,
            IEnumerable<string> failed = null
        )
        {
            try
            {
                var values = new JObject
                {
                    ["material_instance_id"] = material.GetInstanceIDCompat(),
                    ["shader_instance_id"] = material.shader != null ? material.shader.GetInstanceIDCompat() : 0,
                };
                var fields = new JObject();
                var findings = new JArray();
                bool complete = true;
                int count = 0;
                foreach (string field in (failed ?? Array.Empty<string>()).Take(33))
                {
                    findings.Add(new JObject { ["code"] = "field_not_applied", ["field"] = Clip(field) });
                }
                foreach (string property in properties ?? Array.Empty<string>())
                {
                    if (++count > 64)
                    {
                        complete = false;
                        break;
                    }
                    int index = material.shader != null ? material.shader.FindPropertyIndex(property) : -1;
                    if (index < 0)
                    {
                        complete = false;
                        continue;
                    }
                    switch (material.shader.GetPropertyType(index))
                    {
                        case ShaderPropertyType.Color:
                            Color c = material.GetColor(property);
                            fields[Clip(property)] = new JArray(c.r, c.g, c.b, c.a);
                            break;
                        case ShaderPropertyType.Vector:
                            Vector4 v = material.GetVector(property);
                            fields[Clip(property)] = new JArray(v.x, v.y, v.z, v.w);
                            break;
                        case ShaderPropertyType.Float:
                        case ShaderPropertyType.Range:
                            fields[Clip(property)] = material.GetFloat(property);
                            break;
                        case ShaderPropertyType.Int:
                            fields[Clip(property)] = material.GetInteger(property);
                            break;
                        case ShaderPropertyType.Texture:
                            var texture = material.GetTexture(property);
                            fields[Clip(property)] = texture != null ? texture.GetInstanceIDCompat() : 0;
                            break;
                        default:
                            complete = false;
                            break;
                    }
                }
                values["properties"] = fields;
                if (expected != null && fields.Count == 1)
                {
                    bool? match = Equivalent(fields.Properties().First().Value, expected);
                    values["matches_requested"] = match.HasValue ? new JValue(match.Value) : JValue.CreateNull();
                    complete &= match.HasValue;
                    if (match == false)
                        findings.Add(new JObject { ["code"] = "retained_value_differs" });
                }
                if (material.shader == null || !material.shader.isSupported)
                    findings.Add(new JObject { ["code"] = "missing_or_unsupported_shader" });
                if (renderer != null)
                {
                    values["renderer_instance_id"] = renderer.GetInstanceIDCompat();
                    values["slot"] = slot;
                    using var serialized = new SerializedObject(renderer);
                    using var materials = serialized.FindProperty("m_Materials");
                    bool bound = materials != null && materials.isArray && slot >= 0 && slot < materials.arraySize;
                    Object retained = bound ? materials.GetArrayElementAtIndex(slot).objectReferenceValue : null;
                    values["bound_material_instance_id"] = retained != null ? retained.GetInstanceIDCompat() : 0;
                    if (retained != material)
                        findings.Add(new JObject { ["code"] = "material_binding_differs" });
                }
                JObject report = PostEditVerification.Summary("exact_material_and_selected_slot; max64_properties", values, findings, complete);
                report["ownership"] = ownership;
                return report;
            }
            catch
            {
                return PostEditVerification.Unavailable("exact_material", "observation_unavailable");
            }
        }

        internal static JObject Camera(Camera camera, JObject requested = null)
        {
            try
            {
                var values = new JObject
                {
                    ["camera_instance_id"] = camera.GetInstanceIDCompat(),
                    ["enabled"] = camera.enabled,
                    ["fieldOfView"] = camera.fieldOfView,
                    ["nearClipPlane"] = camera.nearClipPlane,
                    ["farClipPlane"] = camera.farClipPlane,
                    ["orthographic"] = camera.orthographic,
                    ["orthographicSize"] = camera.orthographicSize,
                    ["depth"] = camera.depth,
                    ["position"] = Vec(camera.transform.position),
                    ["forward"] = Vec(camera.transform.forward),
                };
                var findings = new JArray();
                if (camera.nearClipPlane <= 0 || camera.farClipPlane <= camera.nearClipPlane)
                    findings.Add(new JObject { ["code"] = "invalid_clip_range" });
                if (camera.orthographic && camera.orthographicSize <= 0)
                    findings.Add(new JObject { ["code"] = "invalid_orthographic_size" });
                bool complete = requested == null || requested.Count <= 64;
                if (requested != null)
                    foreach (JProperty property in requested.Properties().Take(64))
                    {
                        if (!values.TryGetValue(property.Name, out JToken retained))
                        {
                            complete = false;
                            continue;
                        }
                        bool? matches = Equivalent(retained, property.Value);
                        complete &= matches.HasValue;
                        if (matches == false)
                            findings.Add(new JObject { ["code"] = "retained_value_differs", ["property"] = Clip(property.Name) });
                    }
                return PostEditVerification.Summary("exact_camera_configuration; no_render_or_activation", values, findings, complete);
            }
            catch
            {
                return PostEditVerification.Unavailable("exact_camera", "observation_unavailable");
            }
        }

        private static bool? Equivalent(JToken actual, JToken expected)
        {
            if (actual is JArray left && expected is JArray right)
            {
                if (left.Count != right.Count)
                    return false;
                for (int i = 0; i < left.Count; i++)
                    if (Equivalent(left[i], right[i]) != true)
                        return false;
                return true;
            }
            bool Numeric(JToken token) => token.Type == JTokenType.Integer || token.Type == JTokenType.Float;
            if (Numeric(actual) && Numeric(expected))
            {
                double a = actual.Value<double>(),
                    b = expected.Value<double>();
                return Math.Abs(a - b) <= 0.00001 * Math.Max(1, Math.Abs(b));
            }
            return actual.Type == expected.Type ? JToken.DeepEquals(actual, expected) : (bool?)null;
        }

        internal static JArray Vec(Vector3 value) => new JArray(value.x, value.y, value.z);

        internal static JObject Quality(int expected)
        {
            int observed = QualitySettings.GetQualityLevel();
            return PostEditVerification.Summary(
                "active_quality",
                new JObject { ["level"] = observed, ["requested_level"] = expected },
                observed == expected ? null : new JArray(new JObject { ["code"] = "retained_value_differs" })
            );
        }

        internal static JObject Environment()
        {
            try
            {
                return PostEditVerification.Summary(
                    "active_scene_environment",
                    new JObject
                    {
                        ["scene_handle"] = UnityEngine.SceneManagement.SceneManager.GetActiveScene().handle,
                        ["skybox_instance_id"] = RenderSettings.skybox != null ? RenderSettings.skybox.GetInstanceIDCompat() : 0,
                        ["ambient_mode"] = RenderSettings.ambientMode.ToString(),
                        ["ambient_intensity"] = RenderSettings.ambientIntensity,
                        ["fog"] = RenderSettings.fog,
                        ["fog_mode"] = RenderSettings.fogMode.ToString(),
                        ["fog_density"] = RenderSettings.fogDensity,
                        ["fog_start"] = RenderSettings.fogStartDistance,
                        ["fog_end"] = RenderSettings.fogEndDistance,
                        ["reflection_intensity"] = RenderSettings.reflectionIntensity,
                        ["reflection_bounces"] = RenderSettings.reflectionBounces,
                        ["sun_instance_id"] = RenderSettings.sun != null ? RenderSettings.sun.GetInstanceIDCompat() : 0,
                    }
                );
            }
            catch
            {
                return PostEditVerification.Unavailable("active_scene_environment", "observation_unavailable");
            }
        }

        internal static JObject Volume(Object profile, Object component, JObject parameters, IEnumerable<string> failed)
        {
            var expected = new JObject();
            bool unsupported = false;
            foreach (JProperty property in parameters.Properties().Take(64))
            {
                JToken value = property.Value is JObject config ? config["value"] : property.Value;
                if (value is JValue || (value is JArray vector && vector.Count <= 4 && vector.All(item => item is JValue)))
                    expected[property.Name + ".m_Value"] = value.DeepClone();
                else
                    unsupported = true;
                if (property.Value is JObject settings && settings["overrideState"] != null)
                    expected[property.Name + ".m_OverrideState"] = settings["overrideState"].DeepClone();
            }
            JObject report = Properties(component, expected, failed);
            report["profile_instance_id"] = profile != null ? profile.GetInstanceIDCompat() : 0;
            if (parameters.Count > 64 || unsupported)
            {
                report["complete"] = false;
                if ((string)report["status"] == "passed")
                    report["status"] = "incomplete";
                report["reason"] = "unsupported_or_bounded_parameters";
                if ((string)report["status"] != "issues_found")
                    report["further_fixes_required"] = JValue.CreateNull();
            }
            return report;
        }

        private static string Clip(string text) => text == null ? null : text.Substring(0, Math.Min(256, text.Length));
    }
}
