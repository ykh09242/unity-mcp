using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Profiling;
using UProfiler = UnityEngine.Profiling.Profiler;

namespace MCPForUnity.Editor.Tools.Profiler
{
    internal static class ObjectMemoryOps
    {
        internal static object GetObjectMemory(JObject @params)
        {
            var p = new ToolParams(@params);
            var objectPathToken = p.GetRaw("object_path");
            if (objectPathToken != null && objectPathToken.Type != JTokenType.Null && objectPathToken.Type != JTokenType.String)
                return new ErrorResponse("'object_path' must be a string or null.");
            var objectPathResult = p.GetRequired("object_path");
            if (!objectPathResult.IsSuccess)
                return new ErrorResponse(objectPathResult.ErrorMessage);

            string objectPath = objectPathResult.Value;

            // Try scene hierarchy first
            var go = GameObject.Find(objectPath);
            if (go != null)
            {
                long bytes = UProfiler.GetRuntimeMemorySizeLong(go);
                return new SuccessResponse(
                    $"Memory for '{objectPath}'.",
                    new
                    {
                        object_name = go.name,
                        object_type = go.GetType().Name,
                        size_bytes = bytes,
                        size_mb = Math.Round(bytes / (1024.0 * 1024.0), 3),
                        source = "scene_hierarchy",
                    }
                );
            }

            // Try asset path
            var asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(
                AssetPathUtility.GetAssetReferencePath(objectPath, allowPackages: true, allowBuiltIn: true)
            );
            if (asset != null)
            {
                long bytes = UProfiler.GetRuntimeMemorySizeLong(asset);
                return new SuccessResponse(
                    $"Memory for '{objectPath}'.",
                    new
                    {
                        object_name = asset.name,
                        object_type = asset.GetType().Name,
                        size_bytes = bytes,
                        size_mb = Math.Round(bytes / (1024.0 * 1024.0), 3),
                        source = "asset_database",
                    }
                );
            }

            return new ErrorResponse(
                $"Object not found at path '{objectPath}'. Try a scene hierarchy path (e.g. /Player/Mesh) or an asset path (e.g. Assets/Textures/hero.png)."
            );
        }
    }
}
