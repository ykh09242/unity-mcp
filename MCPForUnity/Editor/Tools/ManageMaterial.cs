using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool("manage_material", AutoRegister = false)]
    public static class ManageMaterial
    {
        public static object HandleCommand(JObject @params)
        {
            string action = @params["action"]?.ToString()?.ToLowerInvariant();
            if (string.IsNullOrEmpty(action))
            {
                return new ErrorResponse("Action is required");
            }

            try
            {
                switch (action)
                {
                    case "ping":
                        return new SuccessResponse("pong", new { tool = "manage_material" });

                    case "create":
                        return CreateMaterial(@params);

                    case "set_material_shader_property":
                        return SetMaterialShaderProperty(@params);

                    case "set_material_color":
                        return SetMaterialColor(@params);

                    case "assign_material_to_renderer":
                        return AssignMaterialToRenderer(@params);

                    case "set_renderer_color":
                        return SetRendererColor(@params);

                    case "get_material_info":
                        return GetMaterialInfo(@params);

                    default:
                        return new ErrorResponse($"Unknown action: {action}");
                }
            }
            catch (Exception ex)
            {
                return new ErrorResponse(ex.Message, new { stackTrace = ex.StackTrace });
            }
        }

        private static string NormalizePath(string path)
        {
            if (string.IsNullOrEmpty(path)) return path;

            // Normalize separators and ensure Assets/ root
            path = AssetPathUtility.SanitizeAssetPath(path);

            // Ensure .mat extension
            if (!path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase))
            {
                path += ".mat";
            }

            return path;
        }

        private static object SetMaterialShaderProperty(JObject @params)
        {
            string materialPath = NormalizePath(@params["materialPath"]?.ToString());
            string property = @params["property"]?.ToString();
            JToken value = @params["value"];

            if (string.IsNullOrEmpty(materialPath) || string.IsNullOrEmpty(property) || value == null)
            {
                return new ErrorResponse("materialPath, property, and value are required");
            }

            materialPath = AssetPathUtility.GetContainedAssetPath(materialPath);

            // Find material
            var findInstruction = new JObject { ["find"] = materialPath };
            Material mat = ObjectResolver.Resolve(findInstruction, typeof(Material)) as Material;

            if (mat == null)
            {
                return new ErrorResponse($"Could not find material at path: {materialPath}");
            }

            // Normalize alias/casing once for all code paths
            property = MaterialOps.ResolvePropertyName(mat, property);

            // 1. Try handling Texture instruction explicitly (ManageMaterial special feature)
            if (value.Type == JTokenType.Object)
            {
                // Check if it looks like an instruction
                if (value is JObject obj && (obj.ContainsKey("find") || obj.ContainsKey("method")))
                {
                    Texture tex = ObjectResolver.Resolve(obj, typeof(Texture)) as Texture;
                    int propertyIndex = mat.shader != null ? mat.shader.FindPropertyIndex(property) : -1;
                    if (tex != null && propertyIndex >= 0 &&
                        mat.shader.GetPropertyType(propertyIndex) == UnityEngine.Rendering.ShaderPropertyType.Texture)
                    {
                        AssetPathUtility.GetFullAssetPath(materialPath);
                        Undo.RecordObject(mat, "Set Material Property");
                        mat.SetTexture(property, tex);
                        EditorUtility.SetDirty(mat);
                        return new SuccessResponse($"Set texture property {property} on {mat.name}");
                    }
                }
            }

            // 2. Fallback to standard logic via MaterialOps (handles Colors, Floats, Strings->Path)
            bool success = MaterialOps.TryPrepareShaderProperty(mat, property, value, UnityJsonSerializer.Instance, out var apply);

            if (success)
            {
                AssetPathUtility.GetFullAssetPath(materialPath);
                Undo.RecordObject(mat, "Set Material Property");
                apply();
                EditorUtility.SetDirty(mat);
                return new SuccessResponse($"Set property {property} on {mat.name}");
            }
            else
            {
                return new ErrorResponse($"Failed to set property {property}. Value format might be unsupported or texture not found.");
            }
        }

        private static object SetMaterialColor(JObject @params)
        {
            string materialPath = NormalizePath(@params["materialPath"]?.ToString());
            JToken colorToken = @params["color"];
            string property = @params["property"]?.ToString();

            if (string.IsNullOrEmpty(materialPath) || colorToken == null)
            {
                return new ErrorResponse("materialPath and color are required");
            }

            materialPath = AssetPathUtility.GetContainedAssetPath(materialPath);

            var findInstruction = new JObject { ["find"] = materialPath };
            Material mat = ObjectResolver.Resolve(findInstruction, typeof(Material)) as Material;

            if (mat == null)
            {
                return new ErrorResponse($"Could not find material at path: {materialPath}");
            }

            Color color;
            try
            {
                color = MaterialOps.ParseColor(colorToken, UnityJsonSerializer.Instance);
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Invalid color format: {e.Message}");
            }

            if (string.IsNullOrEmpty(property))
            {
                // Fallback logic: _BaseColor (URP/HDRP) then _Color (Built-in)
                if (mat.HasProperty("_BaseColor"))
                {
                    property = "_BaseColor";
                }
                else if (mat.HasProperty("_Color"))
                {
                    property = "_Color";
                }
            }

            if (!string.IsNullOrEmpty(property) && mat.HasProperty(property))
            {
                if (!MaterialOps.TryPrepareShaderProperty(mat, property,
                    new JArray(color.r, color.g, color.b, color.a), UnityJsonSerializer.Instance, out var apply))
                    return new ErrorResponse($"Property '{property}' does not support a color value.");
                AssetPathUtility.GetFullAssetPath(materialPath);
                Undo.RecordObject(mat, "Set Material Color");
                apply();
                EditorUtility.SetDirty(mat);
                return new SuccessResponse($"Set color on {property}");
            }
            else
            {
                return new ErrorResponse("Could not find suitable color property (_BaseColor or _Color) or specified property does not exist.");
            }
        }

        private static object AssignMaterialToRenderer(JObject @params)
        {
            string target = @params["target"]?.ToString();
            string searchMethod = @params["searchMethod"]?.ToString();
            string materialPath = NormalizePath(@params["materialPath"]?.ToString());
            int slot = @params["slot"]?.ToObject<int>() ?? 0;
            string mode = @params["mode"]?.ToString() ?? "shared";

            if (mode != "shared" && mode != "instance")
                return new ErrorResponse($"Unsupported assignment mode: {mode}. Use shared or instance.");

            if (string.IsNullOrEmpty(target) || string.IsNullOrEmpty(materialPath))
            {
                return new ErrorResponse("target and materialPath are required");
            }

            var goInstruction = new JObject { ["find"] = target };
            if (!string.IsNullOrEmpty(searchMethod)) goInstruction["method"] = searchMethod;

            GameObject go = ObjectResolver.Resolve(goInstruction, typeof(GameObject)) as GameObject;
            if (go == null)
            {
                return new ErrorResponse($"Could not find target GameObject: {target}");
            }

            Renderer renderer = go.GetComponent<Renderer>();
            if (renderer == null)
            {
                return new ErrorResponse($"GameObject {go.name} has no Renderer component");
            }

            var matInstruction = new JObject { ["find"] = materialPath };
            Material mat = ObjectResolver.Resolve(matInstruction, typeof(Material)) as Material;
            if (mat == null)
            {
                return new ErrorResponse($"Could not find material: {materialPath}");
            }

            Material[] sharedMats = renderer.sharedMaterials;
            if (slot < 0 || slot >= sharedMats.Length)
            {
                return new ErrorResponse($"Slot {slot} out of bounds (count: {sharedMats.Length})");
            }

            Material assignedMaterial = mat;
            if (mode == "instance")
            {
                assignedMaterial = new Material(mat);
                Undo.RegisterCreatedObjectUndo(assignedMaterial, "Create Material Instance");
            }
            Undo.RecordObject(renderer, "Assign Material");
            sharedMats[slot] = assignedMaterial;
            renderer.sharedMaterials = sharedMats;

            EditorUtility.SetDirty(renderer);
            return new SuccessResponse($"Assigned material {mat.name} to {go.name} slot {slot}");
        }

        private static object SetRendererColor(JObject @params)
        {
            var p = new ToolParams(@params);

            var targetResult = p.GetRequired("target");
            var targetError = targetResult.GetOrError(out string target);
            if (targetError != null) return targetError;

            string searchMethod = p.Get("searchMethod");
            JToken colorToken = p.GetRaw("color");
            if (colorToken == null)
            {
                return new ErrorResponse("'color' parameter is required.");
            }

            int slot = p.GetInt("slot") ?? 0;
            string mode = p.Get("mode", "property_block");
            if (mode != "property_block" && mode != "shared" && mode != "instance" && mode != "create_unique")
                return new ErrorResponse($"Unknown mode: {mode}");

            Color color;
            try
            {
                color = MaterialOps.ParseColor(colorToken, UnityJsonSerializer.Instance);
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Invalid color format: {e.Message}");
            }

            var goInstruction = new JObject { ["find"] = target };
            if (!string.IsNullOrEmpty(searchMethod)) goInstruction["method"] = searchMethod;

            GameObject go = ObjectResolver.Resolve(goInstruction, typeof(GameObject)) as GameObject;
            if (go == null)
            {
                return new ErrorResponse($"Could not find target GameObject: {target}");
            }

            Renderer renderer = go.GetComponent<Renderer>();
            if (renderer == null)
            {
                return new ErrorResponse($"GameObject {go.name} has no Renderer component");
            }

            var sharedMaterials = renderer.sharedMaterials;
            if (slot < 0 || slot >= Math.Max(1, sharedMaterials.Length))
                return new ErrorResponse($"Slot {slot} out of bounds (count: {sharedMaterials.Length})");

            if (slot == 0 && mode != "create_unique")
                RendererHelpers.EnsureMaterial(renderer);
            sharedMaterials = renderer.sharedMaterials;

            if (mode == "property_block")
            {
                if (slot >= sharedMaterials.Length)
                {
                    return new ErrorResponse($"Slot {slot} out of bounds (count: {sharedMaterials.Length})");
                }

                MaterialPropertyBlock block = new MaterialPropertyBlock();
                renderer.GetPropertyBlock(block, slot);

                if (sharedMaterials[slot] != null)
                {
                    Material mat = sharedMaterials[slot];
                    bool wroteAnyProperty = false;
                    if (mat.HasProperty("_BaseColor"))
                    {
                        block.SetColor("_BaseColor", color);
                        wroteAnyProperty = true;
                    }
                    if (mat.HasProperty("_Color"))
                    {
                        block.SetColor("_Color", color);
                        wroteAnyProperty = true;
                    }
                    if (!wroteAnyProperty)
                    {
                        block.SetColor("_BaseColor", color);
                        block.SetColor("_Color", color);
                    }
                }
                else
                {
                    block.SetColor("_BaseColor", color);
                    block.SetColor("_Color", color);
                }

                renderer.SetPropertyBlock(block, slot);
                EditorUtility.SetDirty(renderer);
                return new SuccessResponse($"Set renderer color (PropertyBlock) on slot {slot}");
            }
            else if (mode == "shared")
            {
                if (slot < sharedMaterials.Length)
                {
                    Material mat = sharedMaterials[slot];
                    if (mat == null)
                    {
                        return new ErrorResponse($"No material in slot {slot}");
                    }
                    if (AssetDatabase.Contains(mat))
                        AssetPathUtility.GetFullAssetPath(AssetDatabase.GetAssetPath(mat));
                    Undo.RecordObject(mat, "Set Material Color");
                    SetColorProperties(mat, color);
                    EditorUtility.SetDirty(mat);
                    return new SuccessResponse("Set shared material color");
                }
                return new ErrorResponse("Invalid slot");
            }
            else if (mode == "instance")
            {
                if (slot < sharedMaterials.Length)
                {
                    if (sharedMaterials[slot] == null)
                        return new ErrorResponse($"No material in slot {slot}");
                    Material mat = renderer.materials[slot];
                    if (mat == null)
                    {
                        return new ErrorResponse($"No material in slot {slot}");
                    }
                    // Note: Undo cannot fully revert material instantiation
                    Undo.RecordObject(mat, "Set Instance Material Color");
                    SetColorProperties(mat, color);
                    return new SuccessResponse("Set instance material color", new { warning = "Material instance created; Undo cannot fully revert instantiation." });
                }
                return new ErrorResponse("Invalid slot");
            }
            else if (mode == "create_unique")
            {
                return CreateUniqueAndAssign(renderer, go, color, slot);
            }

            return new ErrorResponse($"Unknown mode: {mode}");
        }

        private static void EnsureAssetFolderExists(string assetFolderPath)
        {
            assetFolderPath = AssetPathUtility.GetContainedAssetPath(assetFolderPath);
            if (AssetDatabase.IsValidFolder(assetFolderPath))
                return;

            string[] parts = assetFolderPath.Replace('\\', '/').Split('/');
            string current = parts[0]; // "Assets"
            for (int i = 1; i < parts.Length; i++)
            {
                string next = current + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next))
                {
                    AssetPathUtility.GetFullAssetPath(next);
                    AssetDatabase.CreateFolder(current, parts[i]);
                }
                current = next;
            }
        }

        private static void SetColorProperties(Material mat, Color color)
        {
            if (AssetDatabase.Contains(mat))
                AssetPathUtility.GetFullAssetPath(AssetDatabase.GetAssetPath(mat));
            bool wrote = false;
            if (mat.HasProperty("_BaseColor"))
            {
                mat.SetColor("_BaseColor", color);
                wrote = true;
            }
            if (mat.HasProperty("_Color"))
            {
                mat.SetColor("_Color", color);
                wrote = true;
            }
            if (!wrote)
            {
                mat.SetColor("_BaseColor", color);
                mat.SetColor("_Color", color);
            }
        }

        private static object CreateUniqueAndAssign(Renderer renderer, GameObject go, Color color, int slot)
        {
            Material[] sharedMats = renderer.sharedMaterials;
            if (slot < 0 || slot >= Math.Max(1, sharedMats.Length))
                return new ErrorResponse($"Slot {slot} out of bounds (count: {sharedMats.Length})");

            string safeName = go.name.Replace(" ", "_");

            // Derive material folder from the scene context so generated materials
            // live next to the scene/generation folder instead of a global dump.
            string materialFolder = "Assets/Materials";
            var scene = go.scene;
            if (scene.IsValid() && !string.IsNullOrEmpty(scene.path) && scene.path.StartsWith("Assets/"))
            {
                string sceneDir = System.IO.Path.GetDirectoryName(scene.path).Replace("\\", "/");
                materialFolder = $"{sceneDir}/Materials";
            }

            string slotSuffix = slot == 0 ? string.Empty : $"_slot{slot}";
            string matPath = $"{materialFolder}/{safeName}_{go.GetInstanceIDCompat()}{slotSuffix}_mat.mat";
            matPath = AssetPathUtility.GetContainedAssetPath(matPath);
            if (matPath == null)
            {
                return new ErrorResponse($"Invalid GameObject name '{go.name}' — cannot build a safe material path.");
            }

            var existingAsset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(matPath);
            Material existing = existingAsset as Material;
            if (existingAsset != null && existing == null)
                return new ErrorResponse($"An asset already exists at {matPath}");
            if (existing != null)
            {
                for (int i = 0; i < sharedMats.Length; i++)
                {
                    if (i != slot && sharedMats[i] == existing)
                    {
                        Material selected = slot < sharedMats.Length ? sharedMats[slot] : null;
                        string selectedPath = selected != null && AssetDatabase.Contains(selected)
                            ? AssetDatabase.GetAssetPath(selected).Replace("\\", "/") : string.Empty;
                        string baseStem = System.IO.Path.GetFileNameWithoutExtension(matPath) + " ";
                        string selectedStem = System.IO.Path.GetFileNameWithoutExtension(selectedPath);
                        string suffix = selectedStem.StartsWith(baseStem, StringComparison.Ordinal)
                            ? selectedStem.Substring(baseStem.Length) : string.Empty;
                        bool safeRetry = !string.IsNullOrEmpty(selectedPath)
                            && System.IO.Path.GetDirectoryName(selectedPath) == System.IO.Path.GetDirectoryName(matPath)
                            && System.IO.Path.GetExtension(selectedPath) == ".mat"
                            && int.TryParse(suffix, System.Globalization.NumberStyles.None,
                                System.Globalization.CultureInfo.InvariantCulture, out int uniqueIndex)
                            && uniqueIndex > 0 && suffix == uniqueIndex.ToString(System.Globalization.CultureInfo.InvariantCulture);
                        for (int j = 0; safeRetry && j < sharedMats.Length; j++)
                            if (j != slot && sharedMats[j] == selected) safeRetry = false;
                        if (safeRetry)
                        {
                            matPath = selectedPath;
                            existing = selected;
                        }
                        else
                        {
                            matPath = AssetDatabase.GenerateUniqueAssetPath(matPath);
                            existing = null;
                        }
                        break;
                    }
                }
            }

            Material created = null;
            try
            {
                matPath = AssetPathUtility.GetContainedAssetPath(matPath);
                if (existing != null)
                {
                    // Material already exists (e.g. retry) — update its color and re-assign
                    AssetPathUtility.GetFullAssetPath(matPath);
                    Undo.RecordObject(existing, "Update unique material color");
                    SetColorProperties(existing, color);
                    EditorUtility.SetDirty(existing);
                }
                else
                {
                    Shader shader = RenderPipelineUtility.ResolveShader("Standard");
                    if (shader == null)
                        return new ErrorResponse("Could not resolve a suitable shader for the active render pipeline.");

                    // Ensure the Materials directory exists (recursive).
                    EnsureAssetFolderExists(materialFolder);
                    existing = created = new Material(shader);
                    SetColorProperties(existing, color);
                    AssetPathUtility.GetFullAssetPath(matPath);
                    AssetDatabase.CreateAsset(existing, matPath);
                    if (!AssetDatabase.Contains(existing) || AssetDatabase.GetAssetPath(existing) != matPath)
                        return new ErrorResponse($"Failed to create material asset at {matPath}");
                }

                AssetDatabase.SaveAssets();

                // Assign to renderer
                Undo.RecordObject(renderer, "Assign unique material");
                if (sharedMats.Length == 0)
                    sharedMats = new Material[1];
                sharedMats[slot] = existing;
                renderer.sharedMaterials = sharedMats;
                EditorUtility.SetDirty(renderer);

                return new SuccessResponse($"Created unique material at {matPath} and assigned to {go.name}",
                    new { materialPath = matPath });
            }
            finally
            {
                if (created != null && !AssetDatabase.Contains(created))
                    UnityEngine.Object.DestroyImmediate(created);
            }
        }

        private static object GetMaterialInfo(JObject @params)
        {
            string materialPath = NormalizePath(@params["materialPath"]?.ToString());
            if (string.IsNullOrEmpty(materialPath))
            {
                return new ErrorResponse("materialPath is required");
            }

            var findInstruction = new JObject { ["find"] = materialPath };
            Material mat = ObjectResolver.Resolve(findInstruction, typeof(Material)) as Material;

            if (mat == null)
            {
                return new ErrorResponse($"Could not find material at path: {materialPath}");
            }

            Shader shader = mat.shader;
            var properties = new List<object>();

#if UNITY_6000_0_OR_NEWER
            int propertyCount = shader.GetPropertyCount();
            for (int i = 0; i < propertyCount; i++)
            {
                string name = shader.GetPropertyName(i);
                var type = shader.GetPropertyType(i);
                string description = shader.GetPropertyDescription(i);

                object currentValue = null;
                try
                {
                    if (mat.HasProperty(name))
                    {
                        switch (type)
                        {
                            case UnityEngine.Rendering.ShaderPropertyType.Color:
                                var c = mat.GetColor(name);
                                currentValue = new { r = c.r, g = c.g, b = c.b, a = c.a };
                                break;
                            case UnityEngine.Rendering.ShaderPropertyType.Vector:
                                var v = mat.GetVector(name);
                                currentValue = new { x = v.x, y = v.y, z = v.z, w = v.w };
                                break;
                            case UnityEngine.Rendering.ShaderPropertyType.Float:
                            case UnityEngine.Rendering.ShaderPropertyType.Range:
                                currentValue = mat.GetFloat(name);
                                break;
                            case UnityEngine.Rendering.ShaderPropertyType.Int:
                                currentValue = mat.GetInteger(name);
                                break;
                            case UnityEngine.Rendering.ShaderPropertyType.Texture:
                                currentValue = mat.GetTexture(name)?.name ?? "null";
                                break;
                        }
                    }
                }
                catch (Exception ex)
                {
                    currentValue = $"<error: {ex.Message}>";
                }

                properties.Add(new
                {
                    name = name,
                    type = type.ToString(),
                    description = description,
                    value = currentValue
                });
            }
#else
            int propertyCount = ShaderUtil.GetPropertyCount(shader);
            for (int i = 0; i < propertyCount; i++)
            {
                string name = ShaderUtil.GetPropertyName(shader, i);
                ShaderUtil.ShaderPropertyType type = ShaderUtil.GetPropertyType(shader, i);
                string description = ShaderUtil.GetPropertyDescription(shader, i);

                object currentValue = null;
                try
                {
                    if (mat.HasProperty(name))
                    {
                        int propertyIndex = shader.FindPropertyIndex(name);
                        if (propertyIndex >= 0 && shader.GetPropertyType(propertyIndex) == UnityEngine.Rendering.ShaderPropertyType.Int)
                            currentValue = mat.GetInteger(name);
                        else switch (type)
                        {
                            case ShaderUtil.ShaderPropertyType.Color:
                                var c = mat.GetColor(name);
                                currentValue = new { r = c.r, g = c.g, b = c.b, a = c.a };
                                break;
                            case ShaderUtil.ShaderPropertyType.Vector:
                                var v = mat.GetVector(name);
                                currentValue = new { x = v.x, y = v.y, z = v.z, w = v.w };
                                break;
                            case ShaderUtil.ShaderPropertyType.Float: currentValue = mat.GetFloat(name); break;
                            case ShaderUtil.ShaderPropertyType.Range: currentValue = mat.GetFloat(name); break;
                            case ShaderUtil.ShaderPropertyType.TexEnv: currentValue = mat.GetTexture(name)?.name ?? "null"; break;
                        }
                    }
                }
                catch (Exception ex)
                {
                    currentValue = $"<error: {ex.Message}>";
                }

                properties.Add(new
                {
                    name = name,
                    type = type.ToString(),
                    description = description,
                    value = currentValue
                });
            }
#endif

            return new SuccessResponse($"Retrieved material info for {mat.name}", new
            {
                material = mat.name,
                shader = shader.name,
                properties = properties
            });
        }

        private static object CreateMaterial(JObject @params)
        {
            string materialPath = NormalizePath(@params["materialPath"]?.ToString());
            string shaderName = @params["shader"]?.ToString() ?? "Standard";
            JToken colorToken = @params["color"];
            string colorProperty = @params["property"]?.ToString();

            JObject properties = null;
            JToken propsToken = @params["properties"];
            if (propsToken != null)
            {
                if (propsToken.Type == JTokenType.String)
                {
                    try { properties = JObject.Parse(propsToken.ToString()); }
                    catch (Exception ex) { return new ErrorResponse($"Invalid JSON in properties: {ex.Message}"); }
                }
                else if (propsToken is JObject obj)
                {
                    properties = obj;
                }
                else
                {
                    return new ErrorResponse("properties must be a JSON object or a JSON object string.");
                }
            }

            if (string.IsNullOrEmpty(materialPath))
            {
                return new ErrorResponse("materialPath is required");
            }

            materialPath = AssetPathUtility.GetContainedAssetPath(materialPath);

            Shader shader = RenderPipelineUtility.ResolveShader(shaderName);
            if (shader == null)
            {
                return new ErrorResponse($"Could not find shader: {shaderName}");
            }

            // Check for existing asset to avoid silent overwrite
            if (AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(materialPath) != null)
            {
                return new ErrorResponse($"An asset already exists at {materialPath}");
            }

            Material material = null;
            try
            {
                material = new Material(shader);

                // Apply color param during creation (keeps Python tool signature and C# implementation consistent).
                // If "properties" already contains a color property, let properties win.
                bool shouldApplyColor = false;
                if (colorToken != null)
                {
                    if (properties == null)
                    {
                        shouldApplyColor = true;
                    }
                    else if (!string.IsNullOrEmpty(colorProperty))
                    {
                        // If colorProperty is specified, only check that specific property.
                        shouldApplyColor = !properties.ContainsKey(colorProperty);
                    }
                    else
                    {
                        // If colorProperty is not specified, check fallback properties.
                        shouldApplyColor = !properties.ContainsKey("_BaseColor") && !properties.ContainsKey("_Color");
                    }
                }

                if (shouldApplyColor)
                {
                    Color color;
                    try
                    {
                        color = MaterialOps.ParseColor(colorToken, UnityJsonSerializer.Instance);
                    }
                    catch (Exception e)
                    {
                        return new ErrorResponse($"Invalid color format: {e.Message}");
                    }

                    if (!string.IsNullOrEmpty(colorProperty))
                    {
                        if (!material.HasProperty(colorProperty))
                            return new ErrorResponse($"Specified color property '{colorProperty}' does not exist on this material.");
                    }
                    else if (material.HasProperty("_BaseColor"))
                    {
                        colorProperty = "_BaseColor";
                    }
                    else if (material.HasProperty("_Color"))
                    {
                        colorProperty = "_Color";
                    }
                    else
                    {
                        return new ErrorResponse("Could not find suitable color property (_BaseColor or _Color) on this material's shader.");
                    }
                    if (!MaterialOps.TryPrepareShaderProperty(material, colorProperty,
                        new JArray(color.r, color.g, color.b, color.a), UnityJsonSerializer.Instance, out var applyColor))
                        return new ErrorResponse($"Property '{colorProperty}' does not support a color value.");
                    applyColor();
                }

                if (properties != null)
                {
                    MaterialOps.ApplyProperties(material, properties, UnityJsonSerializer.Instance);
                }

                AssetPathUtility.GetFullAssetPath(materialPath);
                AssetDatabase.CreateAsset(material, materialPath);
                if (!AssetDatabase.Contains(material) || AssetDatabase.GetAssetPath(material) != materialPath)
                    return new ErrorResponse($"Failed to create material asset at {materialPath}");

                EditorUtility.SetDirty(material);
                AssetDatabase.SaveAssets();

                return new SuccessResponse($"Created material at {materialPath} with shader {shaderName}");
            }
            finally
            {
                if (material != null && !AssetDatabase.Contains(material))
                {
                    UnityEngine.Object.DestroyImmediate(material);
                }
            }
        }
    }
}
