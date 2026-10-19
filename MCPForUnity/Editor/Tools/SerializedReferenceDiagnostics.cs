using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Paged broken PPtr observations without opening scenes, applying properties or repairing references.</summary>
    internal static class SerializedReferenceDiagnostics
    {
        internal static object Handle(JObject parameters)
        {
            try
            {
                ReferenceDiagnosticUtility.Allowed(
                    parameters,
                    "action",
                    "path",
                    "referenceScope",
                    "maxObjects",
                    "maxWork",
                    "pageSize",
                    "pageNumber",
                    "referenceRevision"
                );
                string scope = ReferenceDiagnosticUtility.Text(parameters, "referenceScope", "active_scene");
                if (scope != "active_scene" && scope != "loaded_scenes" && scope != "prefab" && scope != "scriptable_object")
                    throw new ArgumentException("Unknown referenceScope; use active_scene, loaded_scenes, prefab or scriptable_object.");
                bool assetScope = scope == "prefab" || scope == "scriptable_object";
                string path = assetScope ? ReferenceDiagnosticUtility.Path(parameters, "path") : null;
                if (!assetScope && parameters["path"] != null)
                    throw new ArgumentException("Loaded scene scopes do not accept an asset path.");
                int maxObjects = ReferenceDiagnosticUtility.Integer(parameters, "maxObjects", 1024, 4096);
                int maxWork = ReferenceDiagnosticUtility.Integer(parameters, "maxWork", 100000, 1000000);
                int size = ReferenceDiagnosticUtility.Integer(parameters, "pageSize", 50, 100);
                int page = ReferenceDiagnosticUtility.Integer(parameters, "pageNumber", 1, int.MaxValue);
                string expected = ReferenceDiagnosticUtility.ExpectedRevision(parameters);
                using (var observation = new ReferenceDiagnosticUtility.Observation())
                {
                    observation.Add(scope);
                    observation.Add(path);
                    observation.Add(maxObjects.ToString(CultureInfo.InvariantCulture));
                    observation.Add(maxWork.ToString(CultureInfo.InvariantCulture));
                    var scan = new Scan
                    {
                        MaxObjects = maxObjects,
                        MaxWork = maxWork,
                        Observation = observation,
                    };
                    if (scope == "prefab")
                    {
                        if (!path.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase))
                            throw new ArgumentException("prefab scope requires a .prefab asset.");
                        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path);
                        if (prefab == null)
                            throw new ArgumentException("The prefab asset could not be loaded.");
                        scan.Hierarchy(new[] { prefab }, null);
                    }
                    else if (scope == "scriptable_object")
                    {
                        if (AssetDatabase.IsValidFolder(path))
                            throw new ArgumentException("scriptable_object scope requires a single asset file.");
                        Object[] objects = AssetDatabase.LoadAllAssetsAtPath(path);
                        if (objects.Length > maxObjects)
                            throw new ArgumentException("Selected asset has more objects than maxObjects.");
                        var owners = objects.OfType<ScriptableObject>().OrderBy(value => value.GetInstanceIDCompat()).ToArray();
                        if (owners.Length == 0)
                            throw new ArgumentException("The selected asset has no ScriptableObject owners.");
                        foreach (Object owner in owners)
                            if (!scan.Owner(owner, null, null, -1))
                                break;
                    }
                    else
                    {
                        var scenes = new List<Scene>();
                        if (scope == "active_scene")
                            scenes.Add(SceneManager.GetActiveScene());
                        else
                            for (int i = 0; i < SceneManager.sceneCount; i++)
                                scenes.Add(SceneManager.GetSceneAt(i));
                        foreach (Scene scene in scenes)
                        {
                            if (!scene.IsValid() || !scene.isLoaded)
                                continue;
#if UNITY_6000_4_OR_NEWER
                            observation.Add(scene.handle.GetRawData().ToString(CultureInfo.InvariantCulture));
#else
                            observation.Add(((int)scene.handle).ToString(CultureInfo.InvariantCulture));
#endif
                            observation.Add(scene.path);
                            scan.Hierarchy(scene.GetRootGameObjects(), scene.path);
                            if (!scan.Complete)
                                break;
                        }
                    }
                    observation.Add(scan.Complete.ToString());
                    observation.Add(scan.StopReason);
                    string revision = observation.Finish();
                    if (expected != null && expected != revision)
                        return new ErrorResponse(
                            "reference_revision_conflict",
                            new { reason = "Selected owners, object-reference properties or scan options changed since observation." }
                        );
                    long offset = PaginationBounds.StartIndex(page, size);
                    var rows = new JArray(scan.Issues.Skip((int)Math.Min(offset, scan.Issues.Count)).Take(size));
                    var data = new JObject
                    {
                        ["referenceScope"] = scope,
                        ["path"] = path,
                        ["referenceRevision"] = revision,
                        ["revisionMatches"] = expected == null ? JValue.CreateNull() : new JValue(true),
                        ["issues"] = rows,
                        ["totalIssues"] = scan.Complete ? new JValue(scan.Issues.Count) : JValue.CreateNull(),
                        ["observedIssues"] = scan.Issues.Count,
                        ["scanComplete"] = scan.Complete,
                        ["stopReason"] = scan.StopReason,
                        ["objectsInspected"] = scan.Objects,
                        ["maxObjects"] = maxObjects,
                        ["workUsed"] = scan.Work,
                        ["maxWork"] = maxWork,
                        ["pageSize"] = size,
                        ["pageNumber"] = page,
                        ["hasMore"] = offset + rows.Count < scan.Issues.Count,
                        ["repaired"] = 0,
                        ["limitations"] =
                            "Broken PPtrs are null objectReferenceValue with nonzero objectReferenceInstanceIDValue. Ordinary nulls are excluded; missing scripts/prefabs remain in legacy validate. Target GUID/fileID are unavailable from this public SerializedProperty evidence and are never inferred. Loaded scopes never open scenes; asset scopes explicitly load only the selected prefab or ScriptableObject file. Native object-array/asset-load calls are indivisible. Maximum 10000 observed issues and 512 KiB per response page; no automatic repair.",
                    };
                    ReferenceDiagnosticUtility.PageBytes(data);
                    return new SuccessResponse("Observed broken serialized object references without applying changes.", data);
                }
            }
            catch (Exception failure)
            {
                return new ErrorResponse("reference_diagnostics_unavailable", new { reason = failure.Message });
            }
        }

        private sealed class Scan
        {
            internal int MaxObjects;
            internal int MaxWork;
            internal int Objects;
            internal int Work;
            internal bool Complete = true;
            internal string StopReason;
            internal ReferenceDiagnosticUtility.Observation Observation;
            internal readonly List<JObject> Issues = new List<JObject>();

            private bool Step()
            {
                if (!Complete)
                    return false;
                if (Work >= MaxWork)
                {
                    Stop("maxWork");
                    return false;
                }
                Work++;
                return true;
            }

            private void Stop(string reason)
            {
                Complete = false;
                StopReason = reason;
            }

            internal void Hierarchy(GameObject[] roots, string scenePath)
            {
                var stack = new Stack<Transform>();
                for (int i = roots.Length - 1; i >= 0; i--)
                {
                    if (!Step())
                        return;
                    stack.Push(roots[i].transform);
                }
                while (stack.Count > 0 && Complete)
                {
                    Transform transform = stack.Pop();
                    if (!Owner(transform.gameObject, transform.gameObject, scenePath, -1))
                        return;
                    var components = transform.GetComponents<Component>();
                    for (int i = 0; i < components.Length; i++)
                    {
                        if (!Step())
                            return;
                        if (components[i] != null && !Owner(components[i], transform.gameObject, scenePath, i))
                            return;
                    }
                    for (int i = transform.childCount - 1; i >= 0; i--)
                    {
                        if (!Step())
                            return;
                        stack.Push(transform.GetChild(i));
                    }
                }
            }

            internal bool Owner(Object owner, GameObject gameObject, string scenePath, int componentIndex)
            {
                if (!Step())
                    return false;
                if (Objects >= MaxObjects)
                {
                    Stop("maxObjects");
                    return false;
                }
                Objects++;
                string location = gameObject == null ? owner.name : HierarchyPath(gameObject.transform);
                if (location.Length > 2048)
                {
                    Stop("ownerPath2048");
                    return false;
                }
                string assetPath = AssetDatabase.GetAssetPath(owner);
                Observation.Add(assetPath);
                Observation.Add(scenePath);
                Observation.Add(location);
                Observation.Add(owner.GetInstanceIDCompat().ToString(CultureInfo.InvariantCulture));
                Observation.Add(componentIndex.ToString(CultureInfo.InvariantCulture));
                using (var serialized = new SerializedObject(owner))
                using (var iterator = serialized.GetIterator())
                {
                    // Do not call Update/ApplyModifiedProperties or enter a prefab stage.
                    while (true)
                    {
                        if (!Step())
                            return false;
                        if (!iterator.Next(true))
                            break;
                        if (iterator.propertyType != SerializedPropertyType.ObjectReference)
                            continue;
                        string propertyPath = iterator.propertyPath;
                        if (propertyPath.Length > 2048)
                        {
                            Stop("propertyPath2048");
                            return false;
                        }
#if UNITY_6000_4_OR_NEWER
                        ulong targetId = EntityId.ToULong(iterator.objectReferenceEntityIdValue);
                        const string targetIdKind = "entity_id_raw64";
#else
                        int targetId = iterator.objectReferenceInstanceIDValue;
                        const string targetIdKind = "instance_id_raw32";
#endif
                        bool missing = iterator.objectReferenceValue == null && targetId != 0;
                        Observation.Add(propertyPath);
                        Observation.Add(targetId.ToString(CultureInfo.InvariantCulture));
                        Observation.Add(missing.ToString());
                        if (!missing)
                            continue;
                        if (Issues.Count >= 10000)
                        {
                            Stop("maxIssues10000");
                            return false;
                        }
                        bool persistent = AssetDatabase.TryGetGUIDAndLocalFileIdentifier(owner, out string guid, out long fileId);
                        Issues.Add(
                            new JObject
                            {
                                ["type"] = "broken_object_reference",
                                ["ownerName"] = owner.name,
                                ["ownerInstanceId"] = owner.GetInstanceIDCompat(),
                                ["ownerGuid"] = persistent ? guid : null,
                                ["ownerFileId"] = persistent ? new JValue(fileId.ToString(CultureInfo.InvariantCulture)) : JValue.CreateNull(),
                                ["assetPath"] = assetPath,
                                ["scenePath"] = scenePath,
                                ["gameObjectPath"] = gameObject == null ? null : location,
                                ["gameObjectInstanceId"] = gameObject == null ? JValue.CreateNull() : new JValue(gameObject.GetInstanceIDCompat()),
                                ["componentType"] = owner.GetType().FullName,
                                ["componentIndex"] = componentIndex,
                                ["propertyPath"] = propertyPath,
#if UNITY_6000_4_OR_NEWER
                                ["targetInstanceId"] = JValue.CreateNull(),
#else
                                ["targetInstanceId"] = targetId,
#endif
                                ["targetRawId"] = targetId.ToString(CultureInfo.InvariantCulture),
                                ["targetIdKind"] = targetIdKind,
                                ["targetGuid"] = JValue.CreateNull(),
                                ["targetFileId"] = JValue.CreateNull(),
                                ["evidence"] =
                                    "SerializedProperty null value with nonzero native object ID; target GUID/fileID unavailable from this public API.",
                            }
                        );
                    }
                }
                return true;
            }

            private static string HierarchyPath(Transform transform)
            {
                var parts = new List<string>();
                for (Transform current = transform; current != null; current = current.parent)
                {
                    parts.Add(current.name);
                    if (parts.Count > 256 || parts.Sum(part => part.Length) > 2048)
                        return new string('?', 2049);
                }
                parts.Reverse();
                return string.Join("/", parts);
            }
        }
    }
}
