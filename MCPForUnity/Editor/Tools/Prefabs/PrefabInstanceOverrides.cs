using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.Prefabs
{
    /// <summary>Explicit, bounded selection of overrides on the nearest scene instance root.</summary>
    internal static class PrefabInstanceOverrides
    {
        private const int MaxObjects = 10000;
        private const int MaxProperties = 100000;
        private const int MaxRows = 20000;

        private sealed class Entry
        {
            public string Id;
            public string Kind;
            public Object Instance;
            public Object Asset;
            public string PropertyPath;
            public JObject Data;
            public GameObject Owner => Instance is Component c ? c.gameObject : (GameObject)Instance;
        }

        public static object Handle(GameObject target, string action, JObject args)
        {
            try
            {
                if (EditorApplication.isPlayingOrWillChangePlaymode)
                    return new ErrorResponse("Prefab overrides require Edit Mode.");
                if (EditorUtility.IsPersistent(target) || !target.scene.IsValid() || PrefabStageUtility.GetPrefabStage(target) != null)
                    return new ErrorResponse("Target must be a scene prefab instance, outside Prefab Mode.");
                GameObject root = PrefabUtility.GetNearestPrefabInstanceRoot(target);
                if (root == null || PrefabUtility.GetPrefabInstanceStatus(root) != PrefabInstanceStatus.Connected)
                    return new ErrorResponse("Target must belong to a connected prefab instance.");
                var pending = new Stack<Transform>();
                pending.Push(root.transform);
                int objectCount = 0;
                while (pending.Count > 0)
                {
                    Transform current = pending.Pop();
                    if (++objectCount > MaxObjects)
                        return new ErrorResponse($"Instance exceeds {MaxObjects} objects; target a smaller nested instance.");
                    if (objectCount + pending.Count + current.childCount > MaxObjects)
                        return new ErrorResponse($"Instance exceeds {MaxObjects} objects; target a smaller nested instance.");
                    for (int i = 0; i < current.childCount; i++)
                        pending.Push(current.GetChild(i));
                }
                string assetPath = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(root);
                int offset = Integer(args, "offset", 0);
                int pageSize = Integer(args, "pageSize", 100);
                if (offset < 0 || pageSize < 1 || pageSize > 500)
                    return new ErrorResponse("offset must be nonnegative and pageSize between 1 and 500.");
                List<Entry> entries = Collect(root, assetPath);
                if (action == "list_overrides")
                {
                    int? objectId = args["objectId"] == null ? (int?)null : Integer(args, "objectId", 0);
                    if (
                        args["propertyFilter"] != null
                        && (args["propertyFilter"].Type != JTokenType.String || args["propertyFilter"].Value<string>().Length > 512)
                    )
                        return new ErrorResponse("propertyFilter must be a string of at most 512 characters.");
                    string filter = args["propertyFilter"]?.Value<string>();
                    var filtered = entries
                        .Where(e =>
                            (!objectId.HasValue || e.Instance.GetInstanceID() == objectId || e.Owner.GetInstanceID() == objectId)
                            && (
                                string.IsNullOrEmpty(filter)
                                || (e.PropertyPath != null && e.PropertyPath.IndexOf(filter, StringComparison.OrdinalIgnoreCase) >= 0)
                            )
                        )
                        .ToList();
                    var page = filtered.Skip(offset).Take(pageSize).ToList();
                    return new SuccessResponse(
                        "Prefab instance overrides listed.",
                        new
                        {
                            instanceRoot = Describe(root),
                            prefabPath = assetPath,
                            total = filtered.Count,
                            offset,
                            pageSize,
                            nextOffset = offset + page.Count < filtered.Count ? (int?)(offset + page.Count) : null,
                            removedGameObjectsSupported = RemovedGameObjectsSupported,
                            entries = page.Select(e => e.Data).ToArray(),
                            groups = page.GroupBy(e => e.Instance.GetInstanceID())
                                .Select(g => new { target = Describe(g.First().Instance), overrideIds = g.Select(e => e.Id).ToArray() })
                                .ToArray(),
                        }
                    );
                }
                if (args["objectId"] != null || args["propertyFilter"] != null || offset != 0 || pageSize != 100)
                    return new ErrorResponse("Paging/filtering are for list_overrides only; writes require explicit overrideIds.");
                if (
                    !(args["overrideIds"] is JArray ids)
                    || ids.Count < 1
                    || ids.Count > 100
                    || ids.Any(id => id.Type != JTokenType.String || string.IsNullOrWhiteSpace(id.Value<string>()) || id.Value<string>().Length > 2048)
                )
                    return new ErrorResponse("overrideIds must contain 1 to 100 explicit IDs returned by list_overrides.");
                var selectedIds = ids.Values<string>().ToArray();
                if (selectedIds.Distinct(StringComparer.Ordinal).Count() != selectedIds.Length)
                    return new ErrorResponse("overrideIds must be unique.");
                var selected = new List<Entry>();
                foreach (string id in selectedIds)
                {
                    Entry entry = entries.FirstOrDefault(e => e.Id == id);
                    if (entry == null)
                        return new ErrorResponse($"Override '{id}' no longer exists on this instance. List overrides again.");
                    selected.Add(entry);
                }
                bool apply = action == "apply_overrides";
                if (apply)
                {
                    string requested = args["prefabPath"]?.Value<string>();
                    if (string.IsNullOrWhiteSpace(requested))
                        return new ErrorResponse("apply_overrides requires prefabPath explicitly.");
                    string contained = AssetPathUtility.GetContainedAssetPath(requested);
                    if (contained != assetPath)
                        return new ErrorResponse(
                            "prefabPath must equal the nearest instance root's prefabPath returned by list_overrides. Target a different nested root to select a different asset."
                        );
                    GameObject assetRoot = AssetDatabase.LoadAssetAtPath<GameObject>(contained);
                    if (assetRoot == null || PrefabUtility.GetPrefabAssetType(assetRoot) == PrefabAssetType.Model || !AssetDatabase.IsOpenForEdit(assetRoot))
                        return new ErrorResponse("The destination prefab must be an editable prefab asset.");
                }
                // Structural changes can invalidate other selected IDs or imply dependency changes.
                if (selected.Any(e => e.Kind != "property") && selected.Count != 1)
                    return new ErrorResponse("Select one structural override per call; property overrides may be batched.");
                foreach (Entry entry in selected)
                    Validate(entry, assetPath, apply);
                Undo.IncrementCurrentGroup();
                int group = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName(apply ? "Apply selected prefab overrides" : "Revert selected prefab overrides");
                try
                {
                    foreach (Entry entry in selected)
                        Mutate(entry, assetPath, apply);
                    EditorSceneManager.MarkSceneDirty(root.scene);
                    Undo.CollapseUndoOperations(group);
                }
                catch
                {
                    Undo.RevertAllDownToGroup(group);
                    throw;
                }
                return new SuccessResponse(
                    apply ? "Selected overrides applied." : "Selected overrides reverted.",
                    new
                    {
                        instanceRoot = Describe(root),
                        prefabPath = assetPath,
                        overrideIds = selectedIds,
                        count = selected.Count,
                    }
                );
            }
            catch (ArgumentException ex)
            {
                return new ErrorResponse(ex.Message);
            }
            catch (InvalidOperationException ex)
            {
                return new ErrorResponse(ex.Message);
            }
        }

        private static int Integer(JObject args, string key, int fallback)
        {
            if (args[key] == null)
                return fallback;
            if (args[key].Type != JTokenType.Integer || !int.TryParse(args[key].ToString(), out int value))
                throw new ArgumentException($"{key} must be a 32-bit integer.");
            return value;
        }

        private static List<Entry> Collect(GameObject root, string assetPath)
        {
            var entries = new List<Entry>();
            int visited = 0;
            void Add(Entry entry)
            {
                if (entries.Count >= MaxRows)
                    throw new InvalidOperationException($"Instance exceeds {MaxRows} overrides; target a smaller nested instance.");
                entry.Id = entry.Kind + ":" + entry.Instance.GetInstanceID() + ":" + (entry.PropertyPath ?? entry.Asset?.GetInstanceID().ToString() ?? "added");
                entry.Data = new JObject
                {
                    ["overrideId"] = entry.Id,
                    ["kind"] = entry.Kind,
                    ["target"] = Describe(entry.Instance),
                    ["assetTarget"] = Describe(entry.Asset),
                    ["propertyPath"] = entry.PropertyPath,
                };
                entries.Add(entry);
            }
            foreach (var item in PrefabUtility.GetObjectOverrides(root, false))
            {
                Object instance = item.instanceObject;
                Object source = PrefabUtility.GetCorrespondingObjectFromSourceAtPath(instance, assetPath);
                if (source == null)
                    continue;
                using var serialized = new SerializedObject(instance);
                using var prefab = new SerializedObject(source);
                SerializedProperty property = serialized.GetIterator();
                bool enter = true;
                while (property.Next(enter))
                {
                    if (++visited > MaxProperties)
                        throw new InvalidOperationException($"Instance exceeds the {MaxProperties} serialized property inspection limit.");
                    // Serializable classes are containers; expose their leaf property paths and values.
                    if (property.propertyType == SerializedPropertyType.Generic)
                    {
                        enter = true;
                        continue;
                    }
                    bool overridden = property.prefabOverride && !property.isDefaultOverride;
                    enter = !overridden;
                    if (!overridden)
                        continue;
                    var entry = new Entry
                    {
                        Kind = "property",
                        Instance = instance,
                        Asset = source,
                        PropertyPath = property.propertyPath,
                    };
                    Add(entry);
                    entry.Data["currentValue"] = Value(property);
                    entry.Data["prefabValue"] = Value(prefab.FindProperty(property.propertyPath));
                    entry.Data["propertyType"] = property.propertyType.ToString();
                    entry.Data["applySupported"] = IsSelectiveProperty(property);
                }
            }
            foreach (var item in PrefabUtility.GetAddedComponents(root))
                Add(new Entry { Kind = "added_component", Instance = item.instanceComponent });
            foreach (var item in PrefabUtility.GetRemovedComponents(root))
                Add(
                    new Entry
                    {
                        Kind = "removed_component",
                        Instance = item.containingInstanceGameObject,
                        Asset = item.assetComponent,
                    }
                );
            foreach (var item in PrefabUtility.GetAddedGameObjects(root))
                Add(new Entry { Kind = "added_gameobject", Instance = item.instanceGameObject });
#if UNITY_2022_2_OR_NEWER
            foreach (var item in PrefabUtility.GetRemovedGameObjects(root))
                Add(
                    new Entry
                    {
                        Kind = "removed_gameobject",
                        Instance = item.parentOfRemovedGameObjectInInstance,
                        Asset = item.assetGameObject,
                    }
                );
#endif
            return entries.OrderBy(e => e.Id, StringComparer.Ordinal).ToList();
        }

        private static bool RemovedGameObjectsSupported
        {
            get
            {
#if UNITY_2022_2_OR_NEWER
                return true;
#else
                return false;
#endif
            }
        }

        private static JObject Describe(Object obj)
        {
            if (obj == null)
                return null;
            GameObject go = obj is Component component ? component.gameObject : obj as GameObject;
            return new JObject
            {
                ["instanceId"] = obj.GetInstanceID(),
                ["name"] = obj.name,
                ["type"] = obj.GetType().FullName,
                ["gameObjectInstanceId"] = go?.GetInstanceID(),
                ["path"] = go == null ? null : GameObjectLookup.GetGameObjectPath(go),
                ["assetPath"] = AssetDatabase.GetAssetPath(obj),
            };
        }

        private static JToken Value(SerializedProperty property)
        {
            if (property == null)
                return JValue.CreateNull();
            switch (property.propertyType)
            {
                case SerializedPropertyType.Boolean:
                    return new JValue(property.boolValue);
                case SerializedPropertyType.Integer:
                    return new JValue(property.longValue);
                case SerializedPropertyType.Float:
                    return new JValue(property.doubleValue);
                case SerializedPropertyType.String:
                    return new JValue(property.stringValue.Length > 4096 ? property.stringValue.Substring(0, 4096) : property.stringValue);
                case SerializedPropertyType.Enum:
                    return new JValue(property.intValue);
                case SerializedPropertyType.ObjectReference:
                    return Describe(property.objectReferenceValue) ?? (JToken)JValue.CreateNull();
                case SerializedPropertyType.Vector2:
                    return new JArray(property.vector2Value.x, property.vector2Value.y);
                case SerializedPropertyType.Vector3:
                    return new JArray(property.vector3Value.x, property.vector3Value.y, property.vector3Value.z);
                case SerializedPropertyType.Vector4:
                    return new JArray(property.vector4Value.x, property.vector4Value.y, property.vector4Value.z, property.vector4Value.w);
                case SerializedPropertyType.Quaternion:
                    return new JArray(property.quaternionValue.x, property.quaternionValue.y, property.quaternionValue.z, property.quaternionValue.w);
                case SerializedPropertyType.Color:
                    return new JArray(property.colorValue.r, property.colorValue.g, property.colorValue.b, property.colorValue.a);
                default:
                    return new JObject { ["serializedType"] = property.type, ["valueAvailable"] = false };
            }
        }

        private static bool IsSelectiveProperty(SerializedProperty property) =>
            !property.isArray && !property.propertyPath.Contains(".Array.") && property.propertyType != SerializedPropertyType.Generic;

        private static void Validate(Entry entry, string path, bool apply)
        {
            if (entry.Kind == "property")
            {
                using var serialized = new SerializedObject(entry.Instance);
                var property = serialized.FindProperty(entry.PropertyPath);
                if (property == null || !property.prefabOverride)
                    throw new ArgumentException("Property override no longer exists; list overrides again.");
                if (!IsSelectiveProperty(property))
                    throw new ArgumentException(
                        "Array and compound serialized properties cannot be changed selectively; Unity may change additional overrides."
                    );
                if (
                    apply
                    && property.propertyType == SerializedPropertyType.ObjectReference
                    && property.objectReferenceValue != null
                    && !EditorUtility.IsPersistent(property.objectReferenceValue)
                )
                    throw new ArgumentException("Applying a scene object reference is unsupported. Select a persistent asset reference first.");
            }
            else
            {
                if (apply && entry.Asset != null && AssetDatabase.GetAssetPath(entry.Asset) != path)
                    throw new ArgumentException("Structural override belongs to a different nested prefab asset. Target that nested instance directly.");
                // Component operations may implicitly affect coupled/required components or open dialogs.
                // Reject those before invoking Unity, including dependencies on the asset being restored.
                GameObject owner = entry.Owner;
                Component selected = entry.Instance as Component ?? entry.Asset as Component;
                if (selected != null)
                {
                    if (selected is ParticleSystem || selected is ParticleSystemRenderer)
                        throw new ArgumentException("Coupled ParticleSystem component overrides must be handled together in the Unity Inspector.");
                    var components = owner.GetComponents<Component>().Where(c => c != null).ToList();
                    GameObject assetOwner =
                        selected == entry.Instance ? PrefabUtility.GetCorrespondingObjectFromSourceAtPath(owner, path) : selected.gameObject;
                    if (assetOwner != null)
                        components.AddRange(assetOwner.GetComponents<Component>().Where(c => c != null));
                    bool stableTransform =
                        assetOwner != null && PrefabUtility.GetCorrespondingObjectFromSourceAtPath(owner.transform, path) == assetOwner.transform;
                    if (
                        components.Any(c =>
                            c.GetType()
                                .GetCustomAttributes(typeof(RequireComponent), true)
                                .Cast<RequireComponent>()
                                .SelectMany(requirement => new[] { requirement.m_Type0, requirement.m_Type1, requirement.m_Type2 })
                                .Any(required => required != null && !(required == typeof(Transform) && stableTransform))
                        )
                    )
                        throw new ArgumentException("Component overrides involving RequireComponent dependencies must be handled in the Unity Inspector.");
                }
                if (apply && entry.Kind.StartsWith("added_", StringComparison.Ordinal))
                {
                    var objects = entry.Kind == "added_gameobject" ? owner.GetComponentsInChildren<Component>(true).Cast<Object>() : new[] { entry.Instance };
                    foreach (Object obj in objects)
                    {
                        if (obj == null)
                            throw new ArgumentException("Added objects with missing scripts cannot be applied.");
                        using var serialized = new SerializedObject(obj);
                        var property = serialized.GetIterator();
                        int scanned = 0;
                        while (property.Next(true))
                        {
                            if (++scanned > MaxProperties)
                                throw new ArgumentException("Added object exceeds serialized property inspection limit.");
                            if (property.propertyType != SerializedPropertyType.ObjectReference)
                                continue;
                            Object value = property.objectReferenceValue;
                            if (value == null || EditorUtility.IsPersistent(value))
                                continue;
                            GameObject reference = value is Component c ? c.gameObject : value as GameObject;
                            bool inAddedSubtree =
                                entry.Kind == "added_gameobject" && reference != null && (reference == owner || reference.transform.IsChildOf(owner.transform));
                            if (!inAddedSubtree && PrefabUtility.GetCorrespondingObjectFromSourceAtPath(value, path) == null)
                                throw new ArgumentException("Added override references a scene object absent from the destination prefab.");
                        }
                    }
                }
            }
        }

        private static void Mutate(Entry entry, string path, bool apply)
        {
            const InteractionMode mode = InteractionMode.UserAction;
            switch (entry.Kind)
            {
                case "property":
                    using (var serialized = new SerializedObject(entry.Instance))
                    {
                        var property = serialized.FindProperty(entry.PropertyPath);
                        if (apply)
                            PrefabUtility.ApplyPropertyOverride(property, path, mode);
                        else
                            PrefabUtility.RevertPropertyOverride(property, mode);
                    }
                    break;
                case "added_component":
                    if (apply)
                        PrefabUtility.ApplyAddedComponent((Component)entry.Instance, path, mode);
                    else
                        PrefabUtility.RevertAddedComponent((Component)entry.Instance, mode);
                    break;
                case "removed_component":
                    if (apply)
                        PrefabUtility.ApplyRemovedComponent(entry.Owner, (Component)entry.Asset, mode);
                    else
                        PrefabUtility.RevertRemovedComponent(entry.Owner, (Component)entry.Asset, mode);
                    break;
                case "added_gameobject":
                    if (apply)
                        PrefabUtility.ApplyAddedGameObject(entry.Owner, path, mode);
                    else
                        PrefabUtility.RevertAddedGameObject(entry.Owner, mode);
                    break;
#if UNITY_2022_2_OR_NEWER
                case "removed_gameobject":
                    if (apply)
                        PrefabUtility.ApplyRemovedGameObject(entry.Owner, (GameObject)entry.Asset, mode);
                    else
                        PrefabUtility.RevertRemovedGameObject(entry.Owner, (GameObject)entry.Asset, mode);
                    break;
#endif
            }
        }
    }
}
