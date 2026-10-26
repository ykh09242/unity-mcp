using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.SceneEdits;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Prefabs
{
    // A compare-before-save plan for bounded native root fields, not an asset transaction.
    internal static class PrefabSourcePlans
    {
        // Preserved unchanged through isolated load/save; these are not scene-edit property codecs.
        private static readonly HashSet<Type> PreservedSourceTypes = new HashSet<Type> { typeof(MeshFilter), typeof(MeshRenderer) };
        private const double Lifetime = 600;
        private const long MaxBytes = 256L * 1024 * 1024;
        private static readonly Dictionary<string, Plan> Previews = new Dictionary<string, Plan>(StringComparer.Ordinal);

        private sealed class Plan
        {
            internal string Path;
            internal string Hash;
            internal JObject Fields;
            internal JArray Impact;
            internal double ExpiresAt;
        }

        internal static object Preview(JObject request)
        {
            StableEditor();
            if (request["name"] != null)
                throw new ArgumentException(
                    "Root name edits are unsupported by safe prefab plans: SaveAsPrefabAsset normalizes the root name to the asset filename."
                );
            Keys(request, "action", "prefabPath", "position", "rotation", "scale", "setActive");
            string path = AssetPathUtility.GetContainedAssetPath(Text(request, "prefabPath"));
            if (string.IsNullOrEmpty(path) || !path.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("A contained .prefab asset path is required.");
            string hash = SourceHash(path);
            GameObject root = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            ValidateSource(root);
            var fields = new JObject();
            var impact = new JArray();
            foreach (JProperty property in request.Properties().Where(property => property.Name != "action" && property.Name != "prefabPath"))
            {
                JToken before;
                JToken after;
                switch (property.Name)
                {
                    case "setActive":
                        if (property.Value.Type != JTokenType.Boolean)
                            throw new ArgumentException("setActive must be a JSON boolean.");
                        before = new JValue(root.activeSelf);
                        after = property.Value.DeepClone();
                        break;
                    case "position":
                        before = Vector(root.transform.localPosition);
                        after = NormalizeVector(property.Value);
                        break;
                    case "rotation":
                        before = Vector(root.transform.localEulerAngles);
                        after = NormalizeVector(property.Value);
                        break;
                    case "scale":
                        before = Vector(root.transform.localScale);
                        after = NormalizeVector(property.Value);
                        break;
                    default:
                        throw new ArgumentException("Unsupported safe prefab field.");
                }
                fields[property.Name] = after;
                impact.Add(
                    new JObject
                    {
                        ["index"] = impact.Count,
                        ["target"] = "root",
                        ["property"] = property.Name,
                        ["before"] = before,
                        ["after"] = after.DeepClone(),
                    }
                );
            }
            if (fields.Count == 0)
                throw new ArgumentException("At least one root field is required.");
            if (SourceHash(path) != hash)
                throw new InvalidOperationException("Prefab source changed during preview. Preview again.");
            foreach (string expired in Previews.Where(pair => pair.Value.ExpiresAt <= EditorApplication.timeSinceStartup).Select(pair => pair.Key).ToArray())
                Previews.Remove(expired);
            if (Previews.Count >= 32)
                throw new ArgumentException("Prefab preview capacity reached; wait for expiry.");
            string token = Guid.NewGuid().ToString("N");
            Previews.Add(
                token,
                new Plan
                {
                    Path = path,
                    Hash = hash,
                    Fields = fields,
                    Impact = impact,
                    ExpiresAt = EditorApplication.timeSinceStartup + Lifetime,
                }
            );
            return new SuccessResponse(
                "Prefab source preview prepared; no asset saved.",
                new
                {
                    preview_id = token,
                    expires_in_seconds = Lifetime,
                    prefab_path = path,
                    source_hash = hash,
                    impact,
                    atomicity = "compare_before_save",
                }
            );
        }

        internal static object Apply(JObject request)
        {
            StableEditor();
            Keys(request, "action", "preview_id", "previewId");
            if (request["preview_id"] != null && request["previewId"] != null)
                throw new ArgumentException("Provide one preview token field.");
            string token = Text(request, request["preview_id"] != null ? "preview_id" : "previewId");
            if (!Previews.TryGetValue(token, out Plan plan))
                throw new ArgumentException("Unknown or consumed prefab preview_id. Preview again.");
            Previews.Remove(token);
            if (EditorApplication.timeSinceStartup >= plan.ExpiresAt)
                throw new ArgumentException("Prefab preview expired. Preview again.");
            Verify(plan);
            ValidateSource(AssetDatabase.LoadAssetAtPath<GameObject>(plan.Path));
            GameObject contents = PrefabUtility.LoadPrefabContents(plan.Path);
            try
            {
                // Recheck after loading and before any isolated native field mutation.
                Verify(plan);
                foreach (JProperty field in plan.Fields.Properties())
                    switch (field.Name)
                    {
                        case "setActive":
                            contents.SetActive(field.Value.Value<bool>());
                            break;
                        case "position":
                            contents.transform.localPosition = ReadVector(field.Value);
                            break;
                        case "rotation":
                            contents.transform.localEulerAngles = ReadVector(field.Value);
                            break;
                        case "scale":
                            contents.transform.localScale = ReadVector(field.Value);
                            break;
                        default:
                            throw new InvalidOperationException("Unsupported stored prefab field.");
                    }
                // The source remains untouched while isolated contents are prepared.
                Verify(plan);
                string path = AssetPathUtility.GetContainedAssetPath(plan.Path);
                PrefabUtility.SaveAsPrefabAsset(contents, path, out bool saved);
                if (!saved)
                    return new ErrorResponse(
                        "Prefab save failed; inspect the source before retrying.",
                        new { save_attempted = true, atomicity = "compare_before_save" }
                    );
                return new SuccessResponse(
                    "Previewed prefab root fields saved.",
                    new
                    {
                        prefab_path = path,
                        source_hash = plan.Hash,
                        impact = plan.Impact,
                        atomicity = "compare_before_save",
                    }
                );
            }
            finally
            {
                if (contents != null)
                    PrefabUtility.UnloadPrefabContents(contents);
            }
        }

        internal static JObject InspectBatchPlan(string token)
        {
            StableEditor();
            if (!Previews.TryGetValue(token, out Plan plan) || EditorApplication.timeSinceStartup >= plan.ExpiresAt)
                throw new ArgumentException("Unknown, consumed or expired prefab preview.");
            Verify(plan);
            return new JObject
            {
                ["keys"] = new JArray("asset:" + plan.Path),
                ["path"] = plan.Path,
                ["source"] = plan.Hash,
                ["fields"] = plan.Fields.DeepClone(),
                ["token"] = token,
            };
        }

        private static void Verify(Plan plan)
        {
            if (SourceHash(plan.Path) != plan.Hash)
                throw new InvalidOperationException("Prefab source or dependency drifted. Preview again.");
        }

        private static void StableEditor()
        {
            if (
                EditorApplication.isPlayingOrWillChangePlaymode
                || EditorApplication.isCompiling
                || EditorApplication.isUpdating
                || PrefabStageUtility.GetCurrentPrefabStage() != null
            )
                throw new ArgumentException("Safe prefab plans require stable Edit mode outside prefab stages.");
        }

        private static void ValidateSource(GameObject root)
        {
            if (root == null || PrefabUtility.GetPrefabAssetType(root) != PrefabAssetType.Regular)
                throw new ArgumentException("Safe source plans require a regular prefab; model and variant sources are excluded.");
            var pending = new Stack<Transform>();
            pending.Push(root.transform);
            int count = 0;
            while (pending.Count > 0)
            {
                Transform node = pending.Pop();
                if (++count > 10000 || pending.Count + node.childCount > 10000)
                    throw new ArgumentException("Safe prefab sources are limited to 10000 objects.");
                if (PrefabUtility.IsPartOfPrefabInstance(node.gameObject))
                    throw SourceRestriction(root, node, node.GetType().FullName, "nested prefab instance; safe source plans do not unpack nested instances");
                var components = node.gameObject.GetComponents<Component>();
                for (int index = 0; index < components.Length; index++)
                {
                    var component = components[index];
                    if (component == null)
                        throw SourceRestriction(root, node, "<missing script>", "missing component script at component index " + index);
                    if (!SceneEditValues.Types.Values.Contains(component.GetType()) && !PreservedSourceTypes.Contains(component.GetType()))
                        throw SourceRestriction(
                            root,
                            node,
                            component.GetType().FullName,
                            "unsupported component type at component index "
                                + index
                                + "; only the safe native allowlist is accepted, including RequireComponent dependencies"
                        );
                }
                for (int i = node.childCount - 1; i >= 0; i--)
                    pending.Push(node.GetChild(i));
            }
        }

        private static ArgumentException SourceRestriction(GameObject root, Transform node, string componentType, string reason)
        {
            string relative = AnimationUtility.CalculateTransformPath(node, root.transform);
            string path = root.name + (relative.Length == 0 ? "" : "/" + relative);
            return new ArgumentException("Safe prefab source rejected component '" + componentType + "' at hierarchy path '" + path + "': " + reason + ".");
        }

        private static string SourceHash(string path)
        {
            string[] dependencies = AssetDatabase
                .GetDependencies(path, true)
                .Append(path)
                .Distinct(StringComparer.Ordinal)
                .OrderBy(item => item, StringComparer.Ordinal)
                .ToArray();
            if (dependencies.Length > 4096)
                throw new ArgumentException("Safe prefab plans are limited to 4096 dependencies.");
            var fingerprint = new StringBuilder(AssetDatabase.AssetPathToGUID(path) + ":" + AssetDatabase.GetAssetDependencyHash(path));
            long bytes = 0;
            var buffer = new byte[65536];
            using (SHA256 sha = SHA256.Create())
            {
                foreach (string dependency in dependencies.Where(dependency => dependency.StartsWith("Assets/", StringComparison.Ordinal)))
                {
                    string full = AssetPathUtility.GetFullAssetPath(dependency);
                    if (Directory.Exists(full))
                        continue;
                    foreach (string file in new[] { full, AssetPathUtility.GetFullAssetPath(dependency + ".meta") })
                    {
                        if (!File.Exists(file))
                            throw new ArgumentException("Prefab source/dependency file is unavailable.");
                        using (var stream = File.OpenRead(file))
                        {
                            long length = stream.Length;
                            if (length > MaxBytes - bytes)
                                throw new ArgumentException("Safe prefab source/dependency bytes exceed 256 MiB.");
                            sha.Initialize();
                            long read = 0;
                            while (read < length)
                            {
                                int count = stream.Read(buffer, 0, (int)Math.Min(buffer.Length, length - read));
                                if (count == 0)
                                    throw new IOException("Prefab source changed during hashing.");
                                sha.TransformBlock(buffer, 0, count, buffer, 0);
                                read += count;
                            }
                            if (stream.Length != length || stream.ReadByte() != -1)
                                throw new IOException("Prefab source changed during hashing.");
                            sha.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                            bytes += read;
                            fingerprint
                                .Append('\n')
                                .Append(dependency)
                                .Append(file.EndsWith(".meta", StringComparison.Ordinal) ? ":meta:" : ":asset:")
                                .Append(Convert.ToBase64String(sha.Hash));
                            sha.Initialize();
                        }
                    }
                }
                return Convert.ToBase64String(sha.ComputeHash(Encoding.UTF8.GetBytes(fingerprint.ToString())));
            }
        }

        private static JArray NormalizeVector(JToken token)
        {
            if (!(token is JArray array) || array.Count != 3)
                throw new ArgumentException("Transforms require exactly three finite numbers.");
            foreach (JToken number in array)
                if (
                    (number.Type != JTokenType.Integer && number.Type != JTokenType.Float)
                    || float.IsNaN(number.Value<float>())
                    || float.IsInfinity(number.Value<float>())
                )
                    throw new ArgumentException("Transforms require finite float numbers.");
            return Vector(ReadVector(array));
        }

        private static Vector3 ReadVector(JToken token) => new Vector3(token[0].Value<float>(), token[1].Value<float>(), token[2].Value<float>());

        private static JArray Vector(Vector3 value) => new JArray(value.x, value.y, value.z);

        private static string Text(JObject request, string field)
        {
            if (request[field]?.Type != JTokenType.String || string.IsNullOrWhiteSpace(request[field].Value<string>()))
                throw new ArgumentException(field + " must be a nonempty string.");
            return request[field].Value<string>();
        }

        private static void Keys(JObject request, params string[] allowed)
        {
            if (request.Properties().Any(property => !allowed.Contains(property.Name)))
                throw new ArgumentException("Safe prefab plan contains unsupported fields.");
        }
    }
}
