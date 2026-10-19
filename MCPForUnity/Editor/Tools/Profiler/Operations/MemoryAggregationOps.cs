using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;
using RuntimeProfiler = UnityEngine.Profiling.Profiler;

namespace MCPForUnity.Editor.Tools.Profiler
{
    internal static class MemoryAggregationOps
    {
        private sealed class Row
        {
            internal Object Object;
            internal long Bytes;
            internal readonly HashSet<int> Owners = new HashSet<int>();
        }

        internal static object Read(JObject parameters)
        {
            int maximum = MemoryAnalysisParams.Integer(parameters, "max_objects", 1, 4096, 1024);
            int outputLimit = MemoryAnalysisParams.Integer(parameters, "object_limit", 1, 256, 128);
            bool references = MemoryAnalysisParams.Boolean(parameters, "include_references", true);
            bool objects = MemoryAnalysisParams.Boolean(parameters, "include_objects", false);
            if (!(parameters["targets"] is JArray targets) || targets.Count < 1 || targets.Count > 32)
                throw new ArgumentException("targets must contain 1–32 loaded hierarchy GameObject IDs.");
            var roots = new HashSet<GameObject>();
            foreach (JToken token in targets)
            {
                if ((token.Type != JTokenType.Integer && token.Type != JTokenType.String) || !int.TryParse(token.ToString(), out int id))
                    throw new ArgumentException("Each target must be a GameObject session instance ID.");
                GameObject root = GameObjectLookup.FindById(id);
                if (root == null || EditorUtility.IsPersistent(root) || !root.scene.IsValid() || !root.scene.isLoaded)
                    throw new ArgumentException("Each target must uniquely identify a loaded scene GameObject.");
                roots.Add(root);
            }

            var rows = new Dictionary<Object, Row>();
            int work = 0;
            int missingComponents = 0;
            try
            {
                foreach (GameObject root in roots)
                {
                    int owner = root.GetInstanceIDCompat();
                    var seen = new HashSet<Object>();
                    var hierarchy = new Stack<(GameObject, int)>();
                    hierarchy.Push((root, 0));
                    while (hierarchy.Count > 0)
                    {
                        var (go, depth) = hierarchy.Pop();
                        if (go == null)
                            throw new InvalidOperationException("A selected hierarchy changed during measurement.");
                        if (depth > 128 || go.transform.childCount > maximum)
                            throw new MemoryWorkLimit();
                        Visit(go, owner, 0);
                        foreach (Component component in go.GetComponents<Component>())
                        {
                            if (component == null)
                            {
                                missingComponents++;
                                continue;
                            }
                            Visit(component, owner, 0);
                        }
                        for (int i = go.transform.childCount - 1; i >= 0; i--)
                            hierarchy.Push((go.transform.GetChild(i).gameObject, depth + 1));
                    }

                    void Visit(Object value, int ownerId, int depth)
                    {
                        if (value == null || !seen.Add(value))
                            return;
                        if (++work > 65536 || depth > 32)
                            throw new MemoryWorkLimit();
                        if (!rows.TryGetValue(value, out Row row))
                        {
                            if (rows.Count >= maximum)
                                throw new MemoryWorkLimit();
                            long bytes = RuntimeProfiler.GetRuntimeMemorySizeLong(value);
                            if (bytes < 0)
                                throw new InvalidOperationException("A native runtime memory size was unavailable.");
                            rows.Add(value, row = new Row { Object = value, Bytes = bytes });
                        }
                        row.Owners.Add(ownerId);
                        if (!references)
                            return;
                        if (value is SpriteRenderer spriteRenderer)
                            Visit(spriteRenderer.sprite, ownerId, depth + 1);
                        if (value is Sprite sprite)
                            Visit(sprite.texture, ownerId, depth + 1);
                        if (value is Renderer renderer)
                        {
                            Material[] materials = renderer.sharedMaterials;
                            if (materials.Length > maximum)
                                throw new MemoryWorkLimit();
                            foreach (Material material in materials)
                                Visit(material, ownerId, depth + 1);
                        }
                        if (value is Material materialValue)
                        {
                            string[] names = materialValue.GetTexturePropertyNames();
                            if (names.Length > 256)
                                throw new MemoryWorkLimit();
                            foreach (string name in names)
                                Visit(materialValue.GetTexture(name), ownerId, depth + 1);
                        }
                        if (value is MeshFilter filter)
                            Visit(filter.sharedMesh, ownerId, depth + 1);
                        if (value is SkinnedMeshRenderer skinned)
                            Visit(skinned.sharedMesh, ownerId, depth + 1);
                        if (value is MeshCollider collider)
                            Visit(collider.sharedMesh, ownerId, depth + 1);
                        if (value is AudioSource audio)
                            Visit(audio.clip, ownerId, depth + 1);
                        if (value is Animator animator)
                            Visit(animator.runtimeAnimatorController, ownerId, depth + 1);
                    }
                }
                long total = 0;
                foreach (Row row in rows.Values)
                    total = checked(total + row.Bytes);
                var types = rows.Values.GroupBy(row => row.Object.GetType().FullName).OrderBy(group => group.Key, StringComparer.Ordinal).ToArray();
                if (types.Length > 256)
                    throw new MemoryWorkLimit();
                var result = new JObject
                {
                    ["root_ids"] = new JArray(roots.Select(root => root.GetInstanceIDCompat())),
                    ["unique_object_count"] = rows.Count,
                    ["runtime_size_bytes_sum"] = total,
                    ["byte_semantics"] = "Unity_Profiler_GetRuntimeMemorySizeLong_per_unique_Unity_object",
                    ["managed_heap_bytes"] = JValue.CreateNull(),
                    ["retained_bytes"] = JValue.CreateNull(),
                    ["complete_within_declared_scope"] = missingComponents == 0,
                    ["missing_component_slots"] = missingComponents,
                    ["objects_truncated"] = objects && rows.Count > outputLimit,
                    ["reference_policy"] =
                        "hierarchy_components; shared_material_textures/sprites/meshes/audio_clips/animator_controller; no_custom_serialized_reference_walk",
                    ["types"] = new JArray(
                        types.Select(group => new JObject
                        {
                            ["type"] = group.Key,
                            ["count"] = group.Count(),
                            ["runtime_size_bytes_sum"] = group.Sum(row => row.Bytes),
                        })
                    ),
                    ["observed_editor_time_seconds"] = EditorApplication.timeSinceStartup,
                    ["limitations"] = new JArray(
                        "Runtime API object sizes are not managed heap size, exclusive ownership, retained size, or a process-memory total.",
                        "Shared objects reached through the declared reference policy are counted once; owner_ids records reachability, not exclusive ownership.",
                        "Inactive hierarchy objects are included. Custom serialized fields, animation dependencies, shader memory and arbitrary asset dependencies are outside this bounded policy.",
                        "No scene/component/asset mutation, asset loading, dependency collection, garbage collection or resource unload is requested."
                    ),
                };
                if (objects)
                    result["objects"] = new JArray(
                        rows.Values.OrderBy(row => row.Object.GetInstanceIDCompat())
                            .Take(outputLimit)
                            .Select(row => new JObject
                            {
                                ["object_id"] = row.Object.GetInstanceIDCompat(),
                                ["name"] = MemoryAnalysisParams.BoundedName(row.Object.name),
                                ["type"] = row.Object.GetType().FullName,
                                ["runtime_size_bytes"] = row.Bytes,
                                ["owner_ids"] = new JArray(row.Owners.OrderBy(id => id)),
                            })
                    );
                return new SuccessResponse("Read bounded hierarchy runtime-object sizes with shared-object deduplication.", result);
            }
            catch (MemoryWorkLimit)
            {
                return new ErrorResponse(
                    "memory_work_limit",
                    new
                    {
                        max_objects = maximum,
                        work_limit = 65536,
                        message = "No successful partial total is returned after a work/depth/output type budget is exceeded.",
                    }
                );
            }
            catch (OverflowException)
            {
                return new ErrorResponse("memory_size_overflow");
            }
        }
    }

    internal sealed class MemoryWorkLimit : Exception { }

    internal static class MemoryAnalysisParams
    {
        internal static string BoundedName(string value) => value == null || value.Length <= 512 ? value : value.Substring(0, 512);

        internal static int Integer(JObject parameters, string key, int minimum, int maximum, int fallback)
        {
            JToken token = parameters[key];
            if (token == null || token.Type == JTokenType.Null)
                return fallback;
            if (token.Type != JTokenType.Integer || !long.TryParse(token.ToString(), out long number) || number < minimum || number > maximum)
                throw new ArgumentException($"{key} must be an integer between {minimum} and {maximum}.");
            return (int)number;
        }

        internal static bool Boolean(JObject parameters, string key, bool fallback)
        {
            JToken token = parameters[key];
            if (token == null || token.Type == JTokenType.Null)
                return fallback;
            if (token.Type != JTokenType.Boolean)
                throw new ArgumentException(key + " must be a boolean.");
            return token.Value<bool>();
        }
    }
}
