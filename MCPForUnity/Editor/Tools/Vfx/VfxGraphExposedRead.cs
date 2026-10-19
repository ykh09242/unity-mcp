using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
#if UNITY_VFX_GRAPH
using UnityEngine.VFX;
#endif

namespace MCPForUnity.Editor.Tools.Vfx
{
    internal static class VfxGraphExposedRead
    {
        public static object Read(JObject request)
        {
#if !UNITY_VFX_GRAPH
            return new ErrorResponse("VFX Graph package (com.unity.visualeffectgraph) not installed");
#else
            try
            {
                return ReadInstalled(request);
            }
            catch (Exception error)
            {
                return new ErrorResponse(error.Message);
            }
#endif
        }

#if UNITY_VFX_GRAPH
        private sealed class Budget
        {
            internal int CurveKeys;
            internal int GradientKeys;
            internal int RemainingKeys = 4096;
        }

        private static int Integer(JObject request, string key, int fallback, int minimum, int maximum)
        {
            JToken token = request[key];
            if (token == null)
                return fallback;
            if (token.Type != JTokenType.Integer || !int.TryParse(token.ToString(), out int value) || value < minimum || value > maximum)
                throw new ArgumentException(key + " is outside its integer bounds.");
            return value;
        }

        private static JObject AssetIdentity(UnityEngine.Object asset)
        {
            if (asset == null)
                return null;
            string path = AssetDatabase.GetAssetPath(asset);
            if (asset.name.Length > 1024 || path.Length > 1024)
                throw new ArgumentException("Asset identity exceeds the string limit.");
            bool persistent = AssetDatabase.TryGetGUIDAndLocalFileIdentifier(asset, out string guid, out long id);
            return new JObject
            {
                ["name"] = asset.name,
                ["type"] = asset.GetType().FullName,
                ["persistent"] = persistent,
                ["path"] = persistent ? path : null,
                ["guid"] = persistent ? guid : null,
                ["local_file_id"] = persistent ? id.ToString(System.Globalization.CultureInfo.InvariantCulture) : null,
                ["instance_id"] = asset.GetInstanceIDCompat(),
            };
        }

        private static object ReadInstalled(JObject request)
        {
            var allowed = new HashSet<string>
            {
                "action",
                "target",
                "searchMethod",
                "componentIndex",
                "parameter",
                "pageSize",
                "pageNumber",
                "maxCurveKeys",
                "maxGradientKeys",
                "maxCharacters",
            };
            if (request.Properties().Any(p => !allowed.Contains(p.Name)))
                throw new ArgumentException("Unsupported exposed-property read parameter.");
            // Existing VFX setters resolve the first effect. Refuse a different explicit index
            // instead of advertising setters for a component those setters cannot address.
            if (Integer(request, "componentIndex", 0, 0, int.MaxValue) != 0)
                throw new ArgumentException("VFX exposed reads and existing setters support component_index 0 only.");
            int pageSize = Integer(request, "pageSize", 50, 1, 200);
            int pageNumber = Integer(request, "pageNumber", 1, 1, 1000000);
            int maxCharacters = Integer(request, "maxCharacters", 32768, 1024, 131072);
            var budget = new Budget { CurveKeys = Integer(request, "maxCurveKeys", 64, 0, 256), GradientKeys = Integer(request, "maxGradientKeys", 16, 0, 32) };
            string filter = null;
            if (request["parameter"] != null)
            {
                if (
                    request["parameter"].Type != JTokenType.String
                    || string.IsNullOrEmpty(request.Value<string>("parameter"))
                    || request.Value<string>("parameter").Length > 1024
                )
                    throw new ArgumentException("parameter must be an exact nonempty name up to 1024 characters.");
                filter = request.Value<string>("parameter");
            }
            VisualEffect effect = VfxGraphCommon.FindVisualEffect(request);
            if (effect == null)
                return new ErrorResponse("VisualEffect not found.");
            var asset = effect.visualEffectAsset;
            if (asset == null)
                return new ErrorResponse("VisualEffect has no assigned graph asset.");
            var exposed = new List<VFXExposedProperty>();
            asset.GetExposedProperties(exposed);
            if (exposed.Count > 4096)
                return new ErrorResponse("Graph exceeds the 4096 exposed-property schema limit.");
            if (exposed.Any(p => string.IsNullOrEmpty(p.name) || p.name.Length > 1024 || (p.type?.FullName?.Length ?? 0) > 512))
                return new ErrorResponse("Exposed-property schema contains an unsupported name/type length.");
            var selected = exposed
                .Where(p => filter == null || p.name == filter)
                .OrderBy(p => p.name, StringComparer.Ordinal)
                .ThenBy(p => p.type?.FullName, StringComparer.Ordinal)
                .ToArray();
            var duplicateNames = new HashSet<string>(selected.GroupBy(p => p.name).Where(g => g.Count() > 1).Select(g => g.Key), StringComparer.Ordinal);
            var rows = new JArray();
            int offset = (pageNumber - 1) * pageSize;
            foreach (var property in selected.Skip(offset).Take(pageSize))
            {
                JObject row = Describe(effect, property, budget, duplicateNames.Contains(property.name));
                rows.Add(row);
            }
            string assetPath = AssetDatabase.GetAssetPath(asset);
            var package = UnityEditor.PackageManager.PackageInfo.FindForAssetPath("Packages/com.unity.visualeffectgraph");
            var data = new JObject
            {
                ["schema_version"] = 1,
                ["component_instance_id"] = effect.GetInstanceIDCompat(),
                ["component_dirty_count"] = EditorUtility.GetDirtyCount(effect),
                ["asset"] = AssetIdentity(asset),
                ["asset_revision"] = string.IsNullOrEmpty(assetPath) ? null : AssetDatabase.GetAssetDependencyHash(assetPath).ToString(),
                ["package_version"] = package?.version,
                ["total_count"] = exposed.Count,
                ["matched_count"] = selected.Length,
                ["parameter"] = filter,
                ["filter_status"] =
                    filter == null ? "not_requested"
                    : selected.Length == 0 ? "not_exposed"
                    : selected.Length > 1 ? "ambiguous"
                    : "found",
                ["page_size"] = pageSize,
                ["page_number"] = pageNumber,
                ["has_more"] = offset + rows.Count < selected.Length,
                ["properties"] = rows,
                ["max_curve_keys"] = budget.CurveKeys,
                ["max_gradient_keys"] = budget.GradientKeys,
                ["shared_key_limit"] = 4096,
                ["max_characters"] = maxCharacters,
                ["override_status"] = "unavailable",
                ["override_reason"] = "No version-verified public override-state provider; value equality is not override evidence.",
                ["truncated"] = rows.OfType<JObject>().Any(r => r.Value<string>("value_status") == "key_limit"),
                ["budget_scope"] = "data_json_utf16_and_256KiB_utf8",
                ["limitations"] = new JArray(
                    "Schema enumeration and native value getter copies are indivisible calls.",
                    "Paging is a current read, not an immutable snapshot; compare asset_revision/component_dirty_count between calls.",
                    "No graph compilation, simulation, playback, override reset or asset writes are requested."
                ),
            };
            // Keep every page's schema row. Drop whole values, never return an incomplete curve
            // or gradient as complete and never skip rows behind a fixed page continuation.
            foreach (JObject row in rows.Reverse().OfType<JObject>())
            {
                if (Fits(data, maxCharacters))
                    break;
                if (row["value"]?.Type != JTokenType.Null)
                {
                    row["value"] = null;
                    row["value_status"] = "character_limit";
                    data["truncated"] = true;
                }
            }
            if (!Fits(data, maxCharacters))
                return new ErrorResponse("Serialization budget is too small for page metadata; reduce page_size or increase max_characters.");
            return new SuccessResponse("Exposed VFX schema and current values observed; override state is unavailable.", data);
        }

        private static bool Fits(JObject data, int maxCharacters)
        {
            string json = data.ToString(Formatting.None);
            return json.Length <= maxCharacters && Encoding.UTF8.GetByteCount(json) <= 262144;
        }

        private static JObject Describe(VisualEffect effect, VFXExposedProperty property, Budget budget, bool duplicate)
        {
            Type type = property.type;
            string getter = null;
            string[] setters = Array.Empty<string>();
            Func<bool> has = null;
            Func<JToken> read = null;
            string name = property.name;
            var row = new JObject
            {
                ["name"] = name,
                ["type"] = type?.FullName,
                ["value"] = null,
                ["value_status"] = "unsupported_type",
                ["override_status"] = "unavailable",
            };
            if (type == typeof(float))
            {
                getter = "GetFloat";
                setters = new[] { "vfx_set_float" };
                has = () => effect.HasFloat(name);
                read = () => new JValue(effect.GetFloat(name));
            }
            else if (type == typeof(int))
            {
                getter = "GetInt";
                setters = new[] { "vfx_set_int" };
                has = () => effect.HasInt(name);
                read = () => new JValue(effect.GetInt(name));
            }
            else if (type == typeof(uint))
            {
                getter = "GetUInt";
                has = () => effect.HasUInt(name);
                read = () => new JValue(effect.GetUInt(name));
            }
            else if (type == typeof(bool))
            {
                getter = "GetBool";
                setters = new[] { "vfx_set_bool" };
                has = () => effect.HasBool(name);
                read = () => new JValue(effect.GetBool(name));
            }
            else if (type == typeof(Vector2))
            {
                getter = "GetVector2";
                setters = new[] { "vfx_set_vector2" };
                has = () => effect.HasVector2(name);
                read = () =>
                {
                    var v = effect.GetVector2(name);
                    return new JArray(v.x, v.y);
                };
            }
            else if (type == typeof(Vector3))
            {
                getter = "GetVector3";
                setters = new[] { "vfx_set_vector3" };
                has = () => effect.HasVector3(name);
                read = () =>
                {
                    var v = effect.GetVector3(name);
                    return new JArray(v.x, v.y, v.z);
                };
            }
            else if (type == typeof(Vector4) || type == typeof(Color))
            {
                getter = "GetVector4";
                setters = new[] { "vfx_set_vector4", "vfx_set_color" };
                has = () => effect.HasVector4(name);
                read = () =>
                {
                    var v = effect.GetVector4(name);
                    return new JArray(v.x, v.y, v.z, v.w);
                };
            }
            else if (type == typeof(Matrix4x4))
            {
                getter = "GetMatrix4x4";
                has = () => effect.HasMatrix4x4(name);
                read = () =>
                {
                    var m = effect.GetMatrix4x4(name);
                    return new JArray(Enumerable.Range(0, 16).Select(i => m[i]));
                };
            }
            else if (type != null && typeof(Texture).IsAssignableFrom(type))
            {
                getter = "GetTexture";
                setters = new[] { "vfx_set_texture" };
                has = () => effect.HasTexture(name);
                read = () =>
                {
                    row["texture_dimension"] = effect.GetTextureDimension(name).ToString();
                    return AssetIdentity(effect.GetTexture(name));
                };
            }
            else if (type == typeof(Mesh))
            {
                getter = "GetMesh";
                setters = new[] { "vfx_set_mesh" };
                has = () => effect.HasMesh(name);
                read = () => AssetIdentity(effect.GetMesh(name));
            }
            else if (type == typeof(AnimationCurve))
            {
                getter = "GetAnimationCurve";
                setters = new[] { "vfx_set_curve" };
                has = () => effect.HasAnimationCurve(name);
                read = () => Curve(effect.GetAnimationCurve(name), row, budget);
            }
            else if (type == typeof(Gradient))
            {
                getter = "GetGradient";
                setters = new[] { "vfx_set_gradient" };
                has = () => effect.HasGradient(name);
                read = () => GradientValue(effect.GetGradient(name), row, budget);
            }
            row["getter"] = getter;
            row["getter_supported"] = getter != null;
            row["setter_actions"] = new JArray(setters);
            row["setter_supported"] = setters.Length > 0;
            if (duplicate)
            {
                row["value_status"] = "ambiguous_name";
                return row;
            }
            if (read == null)
                return row;
            try
            {
                bool available = has();
                row["native_property_available"] = available;
                if (!available)
                {
                    row["value_status"] = "property_unavailable";
                    return row;
                }
                row["value_status"] = "available";
                JToken value = read();
                IEnumerable<JToken> tokens = value is JContainer container ? container.DescendantsAndSelf() : new[] { value };
                if (value != null && tokens.OfType<JValue>().Any(IsNonfinite))
                {
                    row["value_status"] = "nonfinite_value";
                    return row;
                }
                row["value"] = value;
            }
            catch (Exception)
            {
                row["value_status"] = "getter_unavailable";
                row["value"] = null;
            }
            return row;
        }

        private static bool IsNonfinite(JValue value)
        {
            if (value.Type != JTokenType.Float)
                return false;
            double number = value.Value<double>();
            return double.IsNaN(number) || double.IsInfinity(number);
        }

        private static JToken Curve(AnimationCurve curve, JObject row, Budget budget)
        {
            if (curve == null)
                return null;
            row["key_count"] = curve.length;
            if (curve.length > budget.CurveKeys || curve.length > budget.RemainingKeys)
            {
                row["value_status"] = "key_limit";
                return null;
            }
            budget.RemainingKeys -= curve.length;
            return new JObject
            {
                ["pre_wrap_mode"] = curve.preWrapMode.ToString(),
                ["post_wrap_mode"] = curve.postWrapMode.ToString(),
                ["keys"] = new JArray(
                    curve.keys.Select(k => new JObject
                    {
                        ["time"] = k.time,
                        ["value"] = k.value,
                        ["in_tangent"] = k.inTangent,
                        ["out_tangent"] = k.outTangent,
                        ["in_weight"] = k.inWeight,
                        ["out_weight"] = k.outWeight,
                        ["weighted_mode"] = k.weightedMode.ToString(),
                    })
                ),
            };
        }

        private static JToken GradientValue(Gradient gradient, JObject row, Budget budget)
        {
            if (gradient == null)
                return null;
            var colors = gradient.colorKeys;
            var alpha = gradient.alphaKeys;
            int count = colors.Length + alpha.Length;
            row["color_key_count"] = colors.Length;
            row["alpha_key_count"] = alpha.Length;
            if (count > budget.GradientKeys || count > budget.RemainingKeys)
            {
                row["value_status"] = "key_limit";
                return null;
            }
            budget.RemainingKeys -= count;
            return new JObject
            {
                ["mode"] = gradient.mode.ToString(),
                ["color_keys"] = new JArray(
                    colors.Select(k => new JObject { ["time"] = k.time, ["color"] = new JArray(k.color.r, k.color.g, k.color.b, k.color.a) })
                ),
                ["alpha_keys"] = new JArray(alpha.Select(k => new JObject { ["time"] = k.time, ["alpha"] = k.alpha })),
            };
        }
#endif
    }
}
