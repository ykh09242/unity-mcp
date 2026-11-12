using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    // Read-only metadata. No instantiation, pixel/PCM extraction, reimport, or provider calls.
    internal static class ImportObservation
    {
        internal static JObject Read(string path, string expectedGuid, string kind)
        {
            const string scope = "imported_asset_metadata_and_bounded_model_references";
            try
            {
                string guid = AssetDatabase.AssetPathToGUID(path ?? "");
                if (string.IsNullOrEmpty(expectedGuid) || guid != expectedGuid)
                    return PostEditVerification.Unavailable(scope, "Imported asset identity is missing or changed; no replacement was selected.");
                UnityEngine.Object asset = AssetDatabase.LoadMainAssetAtPath(path);
                var values = new JObject
                {
                    ["asset_path"] = path,
                    ["asset_guid"] = guid,
                    ["kind"] = kind,
                };
                var findings = new JArray();
                bool complete = true;
                if (asset is Texture2D texture && kind == "image")
                {
                    values["width"] = texture.width;
                    values["height"] = texture.height;
                    values["mipmap_count"] = texture.mipmapCount;
                    values["format"] = texture.format.ToString();
                    if (AssetImporter.GetAtPath(path) is TextureImporter importer)
                    {
                        values["texture_type"] = importer.textureType.ToString();
                        values["readable"] = importer.isReadable;
                    }
                    else
                        complete = false;
                }
                else if (asset is AudioClip clip && kind == "audio")
                {
                    values["channels"] = clip.channels;
                    values["frequency"] = clip.frequency;
                    values["samples"] = clip.samples;
                    values["duration_seconds"] = clip.length;
                    values["load_state"] = clip.loadState.ToString();
                    if (AssetImporter.GetAtPath(path) is AudioImporter importer)
                    {
                        values["load_type"] = importer.defaultSampleSettings.loadType.ToString();
                        values["compression_format"] = importer.defaultSampleSettings.compressionFormat.ToString();
                    }
                    else
                        complete = false;
                }
                else if (asset is GameObject root && (kind == "model" || kind == "marketplace"))
                {
                    JObject model = Model(root);
                    values["model"] = model["observed_values"];
                    foreach (JToken finding in (JArray)model["findings"])
                        findings.Add(finding.DeepClone());
                    complete = model.Value<bool>("complete");
                    if (AssetImporter.GetAtPath(path) is ModelImporter importer)
                    {
                        values["animation_type"] = importer.animationType.ToString();
                        values["import_animation"] = importer.importAnimation;
                        values["global_scale"] = importer.globalScale;
                    }
                }
                else
                {
                    complete = false;
                    findings.Add(
                        new JObject { ["code"] = "imported_asset_type_unavailable", ["actual_type"] = asset == null ? null : asset.GetType().FullName }
                    );
                }
                complete &= AssetDatabase.AssetPathToGUID(path) == expectedGuid;
                return PostEditVerification.Summary(scope, values, findings, complete);
            }
            catch (Exception error)
            {
                return PostEditVerification.Unavailable(scope, error.GetType().Name + ": imported metadata could not be observed.");
            }
        }

        internal static JObject Placed(string path, string guid, GameObject root)
        {
            JObject asset = Read(path, guid, "model");
            JObject placement = Model(root);
            var findings = new JArray();
            foreach (JObject report in new[] { asset, placement })
            foreach (JToken finding in (JArray)report["findings"])
                findings.Add(finding.DeepClone());
            return PostEditVerification.Summary(
                "blender_import_and_exact_placement",
                new JObject { ["asset"] = asset, ["placement"] = placement },
                findings,
                asset.Value<bool>("complete") && placement.Value<bool>("complete")
            );
        }

        internal static JObject Model(GameObject root)
        {
            const string scope = "exact_model_hierarchy_material_and_rig_references";
            try
            {
                if (root == null)
                    return PostEditVerification.Unavailable(scope, "The retained model root is unavailable.");
                var nodes = new JArray();
                var materials = new JArray();
                var rigs = new JArray();
                var findings = new JArray();
                var queue = new Queue<Transform>();
                queue.Enqueue(root.transform);
                bool complete = true;
                int componentCount = 0,
                    renderers = 0,
                    meshes = 0,
                    bones = 0;
                while (queue.Count > 0 && nodes.Count < 32 && componentCount < 64)
                {
                    Transform node = queue.Dequeue();
                    nodes.Add(
                        new JObject
                        {
                            ["instance_id"] = node.gameObject.GetInstanceIDCompat(),
                            ["name"] = node.name,
                            ["child_count"] = node.childCount,
                        }
                    );
                    for (int i = 0; i < node.childCount; i++)
                    {
                        if (nodes.Count + queue.Count >= 32)
                        {
                            complete = false;
                            break;
                        }
                        queue.Enqueue(node.GetChild(i));
                    }
                    var components = PostEditVerification.Components(node.gameObject, out bool allComponents);
                    complete &= allComponents;
                    foreach (Component component in components)
                    {
                        if (componentCount >= 64)
                        {
                            complete = false;
                            break;
                        }
                        componentCount++;
                        if (component == null)
                        {
                            Finding("missing_script", node.gameObject.GetInstanceIDCompat());
                            continue;
                        }
                        if (component is MeshFilter filter)
                        {
                            meshes++;
                            if (filter.sharedMesh == null)
                                Finding("missing_mesh", component.GetInstanceIDCompat());
                        }
                        if (component is Animator animator)
                        {
                            Avatar avatar = animator.avatar;
                            rigs.Add(
                                new JObject
                                {
                                    ["animator_instance_id"] = animator.GetInstanceIDCompat(),
                                    ["avatar_path"] = avatar == null ? null : AssetDatabase.GetAssetPath(avatar),
                                    ["avatar_valid"] = avatar == null ? JValue.CreateNull() : new JValue(avatar.isValid),
                                    ["humanoid"] = avatar == null ? JValue.CreateNull() : new JValue(avatar.isHuman),
                                    ["controller_path"] =
                                        animator.runtimeAnimatorController == null ? null : AssetDatabase.GetAssetPath(animator.runtimeAnimatorController),
                                }
                            );
                            if (avatar != null && !avatar.isValid)
                                Finding("invalid_avatar", component.GetInstanceIDCompat());
                        }
                        if (!(component is Renderer renderer))
                            continue;
                        renderers++;
                        using var serialized = new SerializedObject(renderer);
                        SerializedProperty slots = serialized.FindProperty("m_Materials");
                        if (slots == null || !slots.isArray)
                        {
                            complete = false;
                            continue;
                        }
                        int available = Math.Min(16, 64 - materials.Count);
                        if (slots.arraySize > available)
                            complete = false;
                        for (int i = 0; i < Math.Min(available, slots.arraySize); i++)
                        {
                            var material = slots.GetArrayElementAtIndex(i).objectReferenceValue as Material;
                            materials.Add(
                                new JObject
                                {
                                    ["renderer_instance_id"] = renderer.GetInstanceIDCompat(),
                                    ["slot"] = i,
                                    ["material_path"] = material == null ? null : AssetDatabase.GetAssetPath(material),
                                    ["material_instance_id"] = material == null ? 0 : material.GetInstanceIDCompat(),
                                    ["shader"] = material == null || material.shader == null ? null : material.shader.name,
                                }
                            );
                            if (material == null)
                                Finding("missing_material", renderer.GetInstanceIDCompat());
                            else if (material.shader == null || !material.shader.isSupported)
                                Finding("unavailable_shader", material.GetInstanceIDCompat());
                        }
                        if (renderer is SkinnedMeshRenderer skin)
                        {
                            meshes++;
                            if (skin.sharedMesh == null)
                                Finding("missing_mesh", skin.GetInstanceIDCompat());
                            SerializedProperty references = serialized.FindProperty("m_Bones");
                            if (references == null || !references.isArray)
                            {
                                complete = false;
                                continue;
                            }
                            int remaining = Math.Max(0, 64 - bones);
                            if (references.arraySize > remaining)
                                complete = false;
                            for (int i = 0; i < Math.Min(remaining, references.arraySize); i++)
                            {
                                bones++;
                                if (references.GetArrayElementAtIndex(i).objectReferenceValue == null)
                                    Finding("missing_bone", skin.GetInstanceIDCompat());
                            }
                        }
                    }
                }
                complete &= queue.Count == 0;
                var values = new JObject
                {
                    ["root_instance_id"] = root.GetInstanceIDCompat(),
                    ["nodes"] = nodes,
                    ["components_inspected"] = componentCount,
                    ["renderers_inspected"] = renderers,
                    ["meshes_inspected"] = meshes,
                    ["bones_inspected"] = bones,
                    ["material_slots"] = materials,
                    ["rigs"] = rigs,
                };
                return PostEditVerification.Summary(scope, values, findings, complete);

                void Finding(string code, int id)
                {
                    if (findings.Count < 32)
                        findings.Add(new JObject { ["code"] = code, ["instance_id"] = id });
                    else
                        complete = false;
                }
            }
            catch (Exception error)
            {
                return PostEditVerification.Unavailable(scope, error.GetType().Name + ": model references could not be observed.");
            }
        }
    }
}
