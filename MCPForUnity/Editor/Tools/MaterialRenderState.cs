using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnity.Editor.Tools
{
    internal static class MaterialRenderState
    {
        private const int Limit = 128;
        private const string UndoLabel = "Set Material Render State";

        internal static object Handle(JObject parameters, bool edit)
        {
            Resolve(parameters, out Material source, out Renderer renderer, out int slot);
            if (source.shader == null)
                throw new ArgumentException("Material has no shader.");
            if (!edit)
                return new SuccessResponse("Retrieved material render state", Snapshot(source, renderer, slot, null));

            string mode = parameters["mode"]?.Type == JTokenType.String ? parameters["mode"].Value<string>() : null;
            if (mode != "shared" && mode != "instance")
                throw new ArgumentException("set_render_state requires explicit mode shared or instance; property_block and create_unique are unsupported.");
            if (renderer == null && mode != "shared")
                throw new ArgumentException("Material asset addressing supports shared mode only. Use target and slot for instance mode.");
            if (!(parameters["renderState"] is JObject patch) || patch.Count == 0)
                throw new ArgumentException("renderState must be a nonempty object.");

            // Prepare the complete patch before creating a clone, assigning a slot or recording Undo.
            var operations = Prepare(source, patch);
            if (mode == "shared" && EditorUtility.IsPersistent(source))
                AssetPathUtility.GetFullAssetPath(AssetDatabase.GetAssetPath(source));

            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName(UndoLabel);
            try
            {
                Material material = source;
                if (mode == "instance")
                {
                    material = new Material(source) { name = source.name + " (Render State Instance)" };
                    Undo.RegisterCreatedObjectUndo(material, UndoLabel);
                    Undo.RegisterCompleteObjectUndo(renderer, UndoLabel);
                    var materials = renderer.sharedMaterials;
                    materials[slot] = material;
                    renderer.sharedMaterials = materials;
                    PrefabUtility.RecordPrefabInstancePropertyModifications(renderer);
                    EditorUtility.SetDirty(renderer);
                }
                else
                {
                    Undo.RegisterCompleteObjectUndo(material, UndoLabel);
                }
                foreach (var operation in operations)
                    operation(material);
                EditorUtility.SetDirty(material);
                var state = Snapshot(material, renderer, slot, mode);
                Undo.CollapseUndoOperations(group);
                return new SuccessResponse("Updated material render state", state);
            }
            catch
            {
                Undo.RevertAllDownToGroup(group);
                throw;
            }
        }

        private static void Resolve(JObject parameters, out Material material, out Renderer renderer, out int slot)
        {
            renderer = null;
            slot = 0;
            bool hasPath = parameters["materialPath"] != null;
            bool hasTarget = parameters["target"] != null;
            if (hasPath == hasTarget)
                throw new ArgumentException("Specify exactly one of materialPath or target (with optional slot). Null is not an address.");
            if (hasPath)
            {
                if (parameters["slot"] != null || parameters["searchMethod"] != null)
                    throw new ArgumentException("slot and searchMethod require target addressing.");
                if (parameters["materialPath"].Type != JTokenType.String || string.IsNullOrWhiteSpace(parameters["materialPath"].Value<string>()))
                    throw new ArgumentException("materialPath must be a nonempty Assets path.");
                string path = AssetPathUtility.GetContainedAssetPath(parameters["materialPath"].Value<string>());
                if (!path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase))
                    path += ".mat";
                material = ObjectResolver.Resolve(new JObject { ["find"] = path }, typeof(Material)) as Material;
            }
            else
            {
                if (parameters["target"].Type != JTokenType.String || string.IsNullOrWhiteSpace(parameters["target"].Value<string>()))
                    throw new ArgumentException("target must be a nonempty GameObject identifier.");
                var instruction = new JObject { ["find"] = parameters["target"] };
                if (parameters["searchMethod"] != null)
                {
                    if (parameters["searchMethod"].Type != JTokenType.String)
                        throw new ArgumentException("searchMethod must be a string.");
                    instruction["method"] = parameters["searchMethod"];
                }
                var gameObject = ObjectResolver.Resolve(instruction, typeof(GameObject)) as GameObject;
                renderer = gameObject != null ? gameObject.GetComponent<Renderer>() : null;
                if (renderer == null)
                    throw new ArgumentException("Target GameObject was not found or has no Renderer.");
                if (parameters["slot"] != null)
                    slot = Integer(parameters["slot"], "slot", 0, int.MaxValue);
                // Never access renderer.material/materials: even inspection must not instantiate.
                var materials = renderer.sharedMaterials;
                if (slot >= materials.Length)
                    throw new ArgumentException($"Slot {slot} out of bounds (count: {materials.Length}).");
                material = materials[slot];
            }
            if (material == null)
                throw new ArgumentException("Material was not found or selected slot is empty.");
        }

        private static List<Action<Material>> Prepare(Material source, JObject patch)
        {
            var operations = new List<Action<Material>>();
            foreach (var field in patch.Properties())
            {
                switch (field.Name)
                {
                    case "render_mode":
                        operations.Add(MaterialRenderMode.Prepare(source, field.Value, patch));
                        break;
                    case "render_queue":
                        int queue = Integer(field.Value, "render_queue", -1, 5000);
                        operations.Add(material => material.renderQueue = queue);
                        break;
                    case "keywords":
                        foreach (var entry in Map(field.Value, field.Name).Properties())
                        {
                            bool enabled = Boolean(entry.Value, "keywords." + entry.Name);
                            var keyword = source.shader.keywordSpace.FindKeyword(entry.Name);
                            if (!keyword.isValid)
                                throw new ArgumentException($"Keyword '{entry.Name}' is not declared by this shader. Global keyword creation is unsupported.");
                            operations.Add(material => material.SetKeyword(keyword, enabled));
                        }
                        break;
                    case "shader_passes":
                        var passes = PassIdentifiers(source.shader);
                        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                        foreach (var entry in Map(field.Value, field.Name).Properties())
                        {
                            bool enabled = Boolean(entry.Value, "shader_passes." + entry.Name);
                            if (!passes.TryGetValue(entry.Name, out string lightMode) || !seen.Add(lightMode))
                                throw new ArgumentException(
                                    $"Shader pass '{entry.Name}' must identify one declared LightMode in the inspected active SubShader (limit {Limit}); aliases/duplicates are unsupported."
                                );
                            operations.Add(material => material.SetShaderPassEnabled(lightMode, enabled));
                        }
                        break;
                    case "texture_transforms":
                        foreach (var entry in Map(field.Value, field.Name).Properties())
                        {
                            int index = source.shader.FindPropertyIndex(entry.Name);
                            if (index < 0 || source.shader.GetPropertyType(index) != ShaderPropertyType.Texture)
                                throw new ArgumentException($"'{entry.Name}' is not an exact declared texture property.");
                            var transform = Map(entry.Value, "texture_transforms." + entry.Name);
                            if (transform.Count == 0)
                                throw new ArgumentException("Texture transform must contain scale or offset.");
                            string property = entry.Name;
                            foreach (var component in transform.Properties())
                            {
                                Vector2 value = Vector(component.Value, property + "." + component.Name);
                                if (component.Name == "scale")
                                    operations.Add(material => material.SetTextureScale(property, value));
                                else if (component.Name == "offset")
                                    operations.Add(material => material.SetTextureOffset(property, value));
                                else
                                    throw new ArgumentException("Texture transforms support scale and offset only.");
                            }
                        }
                        break;
                    default:
                        throw new ArgumentException(
                            $"Unsupported renderState field '{field.Name}'. Only raw state fields and explicit supported render_mode presets are accepted."
                        );
                }
            }
            if (operations.Count == 0)
                throw new ArgumentException("renderState must contain at least one edit.");
            return operations;
        }

        private static JObject Snapshot(Material material, Renderer renderer, int slot, string mode)
        {
            var shader = material.shader;
            var keywords = new JArray();
            var declared = shader.keywordSpace.keywords;
            for (int i = 0; i < Math.Min(declared.Length, Limit); i++)
            {
                var keyword = declared[i];
                bool localEnabled = material.IsKeywordEnabled(keyword);
                bool globalEnabled = Shader.IsKeywordEnabled(keyword.name);
                keywords.Add(
                    new JObject
                    {
                        ["name"] = keyword.name,
                        ["local_enabled"] = localEnabled,
                        ["is_overridable"] = keyword.isOverridable,
                        ["global_enabled"] = globalEnabled,
                        ["global_override_applies"] = keyword.isOverridable && globalEnabled,
                        ["effective_enabled"] = localEnabled || (keyword.isOverridable && globalEnabled),
                    }
                );
            }
            var passes = new JArray();
            for (int i = 0; i < Math.Min(shader.passCount, Limit); i++)
            {
                string lightMode = shader.FindPassTagValue(i, new ShaderTagId("LightMode")).name;
                bool controllable = !string.IsNullOrEmpty(lightMode);
                passes.Add(
                    new JObject
                    {
                        ["index"] = i,
                        ["name"] = material.GetPassName(i),
                        ["light_mode"] = lightMode,
                        ["controllable"] = controllable,
                        ["enabled"] = controllable ? (JToken)material.GetShaderPassEnabled(lightMode) : JValue.CreateNull(),
                    }
                );
            }
            var textures = new JObject();
            int propertyCount = shader.GetPropertyCount();
            for (int i = 0; i < Math.Min(propertyCount, Limit); i++)
            {
                if (shader.GetPropertyType(i) != ShaderPropertyType.Texture)
                    continue;
                string property = shader.GetPropertyName(i);
                Vector2 scale = material.GetTextureScale(property);
                Vector2 offset = material.GetTextureOffset(property);
                textures[property] = new JObject { ["scale"] = new JArray(scale.x, scale.y), ["offset"] = new JArray(offset.x, offset.y) };
            }
            var couplingProperties = new JObject();
            foreach (string property in new[] { "_Mode", "_Surface", "_SurfaceType", "_Blend", "_SrcBlend", "_DstBlend", "_ZWrite", "_AlphaClip" })
            {
                int index = shader.FindPropertyIndex(property);
                if (index >= 0 && (shader.GetPropertyType(index) == ShaderPropertyType.Float || shader.GetPropertyType(index) == ShaderPropertyType.Range))
                    couplingProperties[property] = material.GetFloat(property);
            }
            return new JObject
            {
                ["material"] = Identity(material),
                ["renderer"] = renderer != null ? (JToken)Identity(renderer) : JValue.CreateNull(),
                ["slot"] = renderer != null ? (JToken)slot : JValue.CreateNull(),
                ["mode"] = mode,
                ["shader"] = shader.name,
                ["pipeline"] = RenderPipelineUtility.GetActivePipeline().ToString(),
                ["render_queue"] = material.renderQueue,
                ["shader_render_queue"] = shader.renderQueue,
                ["keywords"] = keywords,
                ["keyword_count"] = declared.Length,
                ["keywords_truncated"] = declared.Length > Limit,
                ["shader_passes"] = passes,
                ["pass_count"] = shader.passCount,
                ["passes_truncated"] = shader.passCount > Limit,
                ["pass_scope"] = "active_subshader_light_mode; repeated LightMode tags share one enable flag; untagged passes unsupported",
                ["texture_transforms"] = textures,
                ["shader_property_count"] = propertyCount,
                ["texture_property_scan_truncated"] = propertyCount > Limit,
                ["collection_limit"] = Limit,
                ["render_mode_coupling"] = MaterialRenderMode.Describe(material, couplingProperties),
            };
        }

        private static JObject Identity(UnityEngine.Object value)
        {
            string path = AssetDatabase.GetAssetPath(value);
            return new JObject
            {
                ["instance_id"] = value.GetInstanceIDCompat(),
                ["name"] = value.name,
                ["asset_path"] = path,
                ["asset_guid"] = string.IsNullOrEmpty(path) ? null : AssetDatabase.AssetPathToGUID(path),
                ["persistent"] = EditorUtility.IsPersistent(value),
            };
        }

        private static Dictionary<string, string> PassIdentifiers(Shader shader)
        {
            var identifiers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < Math.Min(shader.passCount, Limit); i++)
            {
                string lightMode = shader.FindPassTagValue(i, new ShaderTagId("LightMode")).name;
                if (!string.IsNullOrEmpty(lightMode))
                    identifiers[lightMode] = lightMode;
            }
            return identifiers;
        }

        private static JObject Map(JToken value, string field)
        {
            if (!(value is JObject map) || map.Count > Limit)
                throw new ArgumentException($"{field} must be an object with at most {Limit} entries.");
            foreach (var entry in map.Properties())
                if (string.IsNullOrWhiteSpace(entry.Name) || entry.Name.Length > 256)
                    throw new ArgumentException($"{field} keys must contain 1..256 non-whitespace characters.");
            return map;
        }

        private static int Integer(JToken value, string field, int minimum, int maximum)
        {
            if (value.Type != JTokenType.Integer || !long.TryParse(value.ToString(), out long number) || number < minimum || number > maximum)
                throw new ArgumentException($"{field} must be an integer in {minimum}..{maximum}.");
            return (int)number;
        }

        private static bool Boolean(JToken value, string field)
        {
            if (value.Type != JTokenType.Boolean)
                throw new ArgumentException($"{field} must be a boolean.");
            return value.Value<bool>();
        }

        private static Vector2 Vector(JToken value, string field)
        {
            if (!(value is JArray array) || array.Count != 2)
                throw new ArgumentException($"{field} must contain exactly two finite numbers.");
            var numbers = new float[2];
            for (int i = 0; i < 2; i++)
            {
                if (array[i].Type != JTokenType.Float && array[i].Type != JTokenType.Integer)
                    throw new ArgumentException($"{field} must contain exactly two finite numbers.");
                numbers[i] = array[i].Value<float>();
                if (float.IsNaN(numbers[i]) || float.IsInfinity(numbers[i]))
                    throw new ArgumentException($"{field} must contain exactly two finite numbers.");
            }
            return new Vector2(numbers[0], numbers[1]);
        }
    }
}
