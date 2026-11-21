using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
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
        private static readonly Dictionary<string, Plan> Previews = new Dictionary<string, Plan>(StringComparer.Ordinal);

        private sealed class Plan
        {
            internal string Path;
            internal string Hash;
            internal JObject Fields;
            internal JArray Impact;
            internal double ExpiresAt;
        }

        private sealed class IsolatedContents : IDisposable
        {
            internal GameObject Root { get; private set; }

            internal IsolatedContents(string path)
            {
                Root = PrefabUtility.LoadPrefabContents(path);
                AssemblyReloadEvents.beforeAssemblyReload += Dispose;
                EditorApplication.quitting += Dispose;
            }

            public void Dispose()
            {
                AssemblyReloadEvents.beforeAssemblyReload -= Dispose;
                EditorApplication.quitting -= Dispose;
                if (Root != null)
                    PrefabUtility.UnloadPrefabContents(Root);
                Root = null;
            }
        }

        internal static async Task<object> Preview(JObject request, bool background, CancellationToken cancellationToken)
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
            ValidateSource(AssetDatabase.LoadAssetAtPath<GameObject>(path));
            using var revision = await PrefabSourceRevision.Observe(path, background, cancellationToken);
            StableEditor();
            string hash = revision.Hash;
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
            cancellationToken.ThrowIfCancellationRequested();
            revision.VerifyCurrent();
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

        internal static async Task<object> Apply(JObject request, bool background, CancellationToken cancellationToken)
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
            ValidateSource(AssetDatabase.LoadAssetAtPath<GameObject>(plan.Path));
            using var revision = await PrefabSourceRevision.Observe(plan.Path, background, cancellationToken);
            StableEditor();
            if (revision.Hash != plan.Hash || EditorApplication.timeSinceStartup >= plan.ExpiresAt)
                throw new InvalidOperationException("Prefab source or dependency drifted, or the preview expired. Preview again.");
            ValidateSource(AssetDatabase.LoadAssetAtPath<GameObject>(plan.Path));
            using var isolated = new IsolatedContents(plan.Path);
            GameObject contents = isolated.Root;
            cancellationToken.ThrowIfCancellationRequested();
            var propertyPaths = plan
                .Fields.Properties()
                .Select(field =>
                    field.Name switch
                    {
                        "setActive" => "m_IsActive",
                        "position" => "m_LocalPosition",
                        "rotation" => "m_LocalRotation",
                        "scale" => "m_LocalScale",
                        _ => throw new InvalidOperationException("Unsupported stored prefab field."),
                    }
                )
                .ToArray();
            var before = ReadProperties(contents, propertyPaths);
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
            await revision.VerifyBeforeSave(background, cancellationToken);
            StableEditor();
            string path = AssetPathUtility.GetContainedAssetPath(plan.Path);
            ValidateSource(AssetDatabase.LoadAssetAtPath<GameObject>(path));
            revision.Dispose();
            cancellationToken.ThrowIfCancellationRequested();
            PrefabUtility.SaveAsPrefabAsset(contents, path, out bool saved);
            if (!saved)
                return new ErrorResponse(
                    "Prefab save failed; inspect the source before retrying.",
                    new { save_attempted = true, atomicity = "compare_before_save" }
                );
            GameObject asset = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            var applied = ReadProperties(asset, propertyPaths);
            string guid = AssetDatabase.AssetPathToGUID(path);
            string objectId = GlobalObjectId.GetGlobalObjectIdSlow(asset).ToString();
            string transformId = GlobalObjectId.GetGlobalObjectIdSlow(asset.transform).ToString();
            string receipt = PropertyUndoReceipts.Retain((asyncRead, token) => Restore(guid, objectId, transformId, before, applied, asyncRead, token));
            return new SuccessResponse(
                "Previewed prefab root fields saved.",
                new
                {
                    prefab_path = path,
                    receipt_id = receipt,
                    source_hash = plan.Hash,
                    impact = plan.Impact,
                    atomicity = "compare_before_save",
                    saved = true,
                    verification = ManagePrefabs.SavedVerification(path, System.Array.Empty<int>(), System.Array.Empty<string>(), plan.Fields),
                }
            );
        }

        private static UnityEngine.Object Owner(GameObject root, string property) => property == "m_IsActive" ? (UnityEngine.Object)root : root.transform;

        private static JObject ReadProperties(GameObject root, IEnumerable<string> names)
        {
            var values = new JObject();
            foreach (string name in names)
            {
                using var serialized = new SerializedObject(Owner(root, name));
                values[name] = SceneEditValues.Read(serialized.FindProperty(name));
            }
            return values;
        }

        private static async Task<object> Restore(
            string guid,
            string objectId,
            string transformId,
            JObject before,
            JObject applied,
            bool background,
            CancellationToken cancellationToken
        )
        {
            StableEditor();
            string path = AssetPathUtility.GetContainedAssetPath(AssetDatabase.GUIDToAssetPath(guid));
            if (string.IsNullOrEmpty(path) || !path.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("The original prefab GUID no longer resolves to a prefab asset.");
            ValidateSource(AssetDatabase.LoadAssetAtPath<GameObject>(path));
            using var revision = await PrefabSourceRevision.Observe(path, background, cancellationToken);
            StableEditor();
            GameObject asset = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            ValidateSource(asset);
            if (
                GlobalObjectId.GetGlobalObjectIdSlow(asset).ToString() != objectId
                || GlobalObjectId.GetGlobalObjectIdSlow(asset.transform).ToString() != transformId
            )
                throw new InvalidOperationException("The recorded prefab root or transform was replaced.");

            // Reload the CURRENT source. The receipt never contains a copy of the prefab file.
            using var isolated = new IsolatedContents(path);
            GameObject contents = isolated.Root;
            bool saveAttempted = false;
            try
            {
                var conflicts = new JArray();
                var restored = new JObject();
                var restoredFields = new JArray();
                var pending = new JObject();
                var current = ReadProperties(contents, before.Properties().Select(p => p.Name));
                foreach (var field in before.Properties())
                {
                    string name = field.Name;
                    var identity = new JObject { ["asset_guid"] = guid, ["property"] = name };
                    var decision = PropertyUndoReceipts.Compare(field.Value, applied[name], current[name], identity, conflicts);
                    if (decision == PropertyUndoReceipts.Decision.Conflict)
                        pending[name] = field.Value;
                    if (decision != PropertyUndoReceipts.Decision.Restore)
                        continue;
                    using var serialized = new SerializedObject(Owner(contents, name));
                    SceneEditValues.Write(serialized.FindProperty(name), field.Value);
                    serialized.ApplyModifiedPropertiesWithoutUndo();
                    restored[name] = field.Value;
                    restoredFields.Add(identity);
                }
                if (restored.Count != 0)
                {
                    await revision.VerifyBeforeSave(background, cancellationToken);
                    StableEditor();
                    ValidateSource(AssetDatabase.LoadAssetAtPath<GameObject>(path));
                    revision.Dispose();
                    cancellationToken.ThrowIfCancellationRequested();
                    saveAttempted = true;
                    PrefabUtility.SaveAsPrefabAsset(contents, path, out bool saved);
                    if (
                        !saved
                        || !JToken.DeepEquals(
                            restored,
                            ReadProperties(AssetDatabase.LoadAssetAtPath<GameObject>(path), restored.Properties().Select(p => p.Name))
                        )
                    )
                        return new ErrorResponse("Could not verify the saved property undo.", new { save_attempted = true });
                }
                return PropertyUndoReceipts.Report(restoredFields, conflicts, (asyncRead, token) =>
                    Restore(guid, objectId, transformId, pending, applied, asyncRead, token)
                );
            }
            catch (Exception exception) when (saveAttempted)
            {
                return new ErrorResponse("Could not verify the saved property undo: " + exception.Message, new { save_attempted = true });
            }
        }

        internal static JObject InspectBatchPlan(string token, PrefabSourceRevision revision)
        {
            StableEditor();
            if (!Previews.TryGetValue(token, out Plan plan) || EditorApplication.timeSinceStartup >= plan.ExpiresAt)
                throw new ArgumentException("Unknown, consumed or expired prefab preview.");
            ValidateSource(AssetDatabase.LoadAssetAtPath<GameObject>(plan.Path));
            if (revision != null)
            {
                revision.VerifyCurrent();
                if (revision.Hash != plan.Hash)
                    throw new InvalidOperationException("Prefab source or dependency drifted. Preview again.");
            }
            return new JObject
            {
                ["keys"] = new JArray("asset:" + plan.Path),
                ["path"] = plan.Path,
                ["source"] = plan.Hash,
                ["fields"] = plan.Fields.DeepClone(),
                ["token"] = token,
            };
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
                if (EditorUtility.IsDirty(node.gameObject))
                    throw new InvalidOperationException("The imported prefab has unsaved edits. Resolve them before a source save.");
                var components = node.gameObject.GetComponents<Component>();
                for (int index = 0; index < components.Length; index++)
                {
                    var component = components[index];
                    if (component == null)
                        throw SourceRestriction(root, node, "<missing script>", "missing component script at component index " + index);
                    if (EditorUtility.IsDirty(component))
                        throw new InvalidOperationException("The imported prefab has unsaved edits. Resolve them before a source save.");
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
