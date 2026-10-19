using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.SceneEdits
{
    // This plan deliberately excludes component lifecycle, prefab instances and callbacks.
    internal static class SceneStructurePlans
    {
        private const int MaxNodes = 256;
        private const double Lifetime = 600;
        private static readonly Dictionary<string, Plan> Previews = new Dictionary<string, Plan>(StringComparer.Ordinal);

        private sealed class Plan
        {
            internal GameObject Root;
            internal Scene Scene;
            internal string Hash;
            internal JArray Operations;
            internal Dictionary<string, string> Parents;
            internal double ExpiresAt;
        }

        internal static object Preview(JObject request)
        {
            Keys(request, "action", "target", "operations", "undo_name");
            if (request["target"]?.Type != JTokenType.Integer)
                throw new ArgumentException("Structural target must be a scene root instance ID.");
            GameObject root = GameObjectLookup.FindById(request["target"].Value<int>());
            string hash = Snapshot(root);
            if (!(request["operations"] is JArray operations) || operations.Count < 1 || operations.Count > 32)
                throw new ArgumentException("operations must contain 1-32 ordered operations.");
            var parents = Objects(root)
                .ToDictionary(node => Key(node.gameObject), node => node.parent == null ? null : Key(node.parent.gameObject), StringComparer.Ordinal);
            string rootKey = Key(root);
            var prepared = new JArray();
            var allocated = new HashSet<string>(StringComparer.Ordinal);
            foreach (JToken entry in operations)
            {
                if (!(entry is JObject operation))
                    throw new ArgumentException("Every structural operation must be an object.");
                string op = Text(operation, "op");
                var item = (JObject)operation.DeepClone();
                string target;
                switch (op)
                {
                    case "create":
                        Keys(operation, "op", "temp_id", "name", "parent");
                        target = Text(operation, "temp_id");
                        if (!Regex.IsMatch(target, @"^\$[A-Za-z][A-Za-z0-9_]{0,31}$") || !allocated.Add(target))
                            throw new ArgumentException("create requires a unique $temporary ID.");
                        string name = Text(operation, "name");
                        if (name.Length > 128 || name.IndexOf('/') >= 0 || name.Any(char.IsControl))
                            throw new ArgumentException("Created names must contain 1-128 characters without slashes or control characters.");
                        string createParent = Reference(operation, "parent", parents);
                        if (parents.Count >= MaxNodes)
                            throw new ArgumentException($"Structural scope may contain at most {MaxNodes} objects.");
                        parents.Add(target, createParent);
                        item["affected_ids"] = new JArray(target);
                        break;
                    case "delete":
                        Keys(operation, "op", "target");
                        target = Reference(operation, "target", parents);
                        if (target == rootKey)
                            throw new ArgumentException("The structural scope root cannot be deleted.");
                        string[] removed = parents.Keys.Where(key => DescendsFrom(key, target, parents)).ToArray();
                        item["affected_ids"] = new JArray(removed);
                        foreach (string key in removed)
                            parents.Remove(key);
                        break;
                    case "reparent":
                        Keys(operation, "op", "target", "parent");
                        target = Reference(operation, "target", parents);
                        string parent = Reference(operation, "parent", parents);
                        if (target == rootKey || DescendsFrom(parent, target, parents))
                            throw new ArgumentException("Cannot reparent the scope root or create a hierarchy cycle.");
                        item["affected_ids"] = new JArray(parents.Keys.Where(key => DescendsFrom(key, target, parents)));
                        parents[target] = parent;
                        break;
                    default:
                        throw new ArgumentException("Structural operations support create, delete and reparent only.");
                }
                item["index"] = prepared.Count;
                prepared.Add(item);
            }
            foreach (string expired in Previews.Where(pair => pair.Value.ExpiresAt <= EditorApplication.timeSinceStartup).Select(pair => pair.Key).ToArray())
                Previews.Remove(expired);
            if (Previews.Count >= 32)
                throw new ArgumentException("Structural preview capacity reached; wait for expiry.");
            string token = Guid.NewGuid().ToString("N");
            Previews.Add(
                token,
                new Plan
                {
                    Root = root,
                    Scene = root.scene,
                    Hash = hash,
                    Operations = prepared,
                    Parents = parents,
                    ExpiresAt = EditorApplication.timeSinceStartup + Lifetime,
                }
            );
            return new SuccessResponse(
                "Structural preview prepared; no scene objects changed.",
                new
                {
                    preview_id = token,
                    expires_in_seconds = Lifetime,
                    hierarchy_hash = hash,
                    scope_id = root.GetInstanceIDCompat(),
                    operations = prepared,
                    impact = prepared.DeepClone(),
                    atomicity = "recorded_plain_scene_hierarchy",
                    reparent_transform = "preserve_local",
                }
            );
        }

        internal static object Apply(JObject request)
        {
            Keys(request, "action", "preview_id", "undo_name");
            string token = Text(request, "preview_id");
            if (!Previews.TryGetValue(token, out Plan plan))
                throw new ArgumentException("Unknown or consumed structural preview_id. Preview again.");
            Previews.Remove(token);
            if (EditorApplication.timeSinceStartup >= plan.ExpiresAt)
                throw new ArgumentException("Structural preview expired. Preview again.");
            Verify(plan);
            string undoName = request["undo_name"] == null ? "MCP Scene Structure" : Text(request, "undo_name");
            if (undoName.Length > 80)
                throw new ArgumentException("undo_name must contain 1-80 characters.");
            var objects = Objects(plan.Root).ToDictionary(node => Key(node.gameObject), node => node.gameObject, StringComparer.Ordinal);
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName(undoName);
            try
            {
                Undo.RegisterFullObjectHierarchyUndo(plan.Root, undoName);
                foreach (JObject operation in plan.Operations)
                {
                    switch (operation.Value<string>("op"))
                    {
                        case "create":
                            var created = new GameObject(operation.Value<string>("name"));
                            // Register before parenting so compensation also owns a partially created object.
                            Undo.RegisterCreatedObjectUndo(created, undoName);
                            if (created.scene != plan.Scene)
                                SceneManager.MoveGameObjectToScene(created, plan.Scene);
                            created.transform.SetParent(objects[operation["parent"].ToString()].transform, false);
                            objects.Add(operation.Value<string>("temp_id"), created);
                            break;
                        case "reparent":
                            Transform transform = objects[operation["target"].ToString()].transform;
                            Vector3 position = transform.localPosition;
                            Quaternion rotation = transform.localRotation;
                            Vector3 scale = transform.localScale;
                            Undo.SetTransformParent(transform, objects[operation["parent"].ToString()].transform, undoName);
                            transform.localPosition = position;
                            transform.localRotation = rotation;
                            transform.localScale = scale;
                            break;
                        case "delete":
                            Undo.DestroyObjectImmediate(objects[operation["target"].ToString()]);
                            break;
                        default:
                            throw new InvalidOperationException("Stored structural operation is unsupported.");
                    }
                }
                foreach (var parent in plan.Parents)
                {
                    GameObject node = objects[parent.Key];
                    if (node == null || node.transform.parent != (parent.Value == null ? null : objects[parent.Value].transform))
                        throw new InvalidOperationException("Applied hierarchy does not match the structural plan.");
                }
                EditorSceneManager.MarkSceneDirty(plan.Scene);
                Undo.CollapseUndoOperations(group);
                var temporary = new JObject();
                foreach (var pair in objects.Where(pair => pair.Key.StartsWith("$", StringComparison.Ordinal) && pair.Value != null))
                    temporary[pair.Key] = pair.Value.GetInstanceIDCompat();
                return new SuccessResponse(
                    "Structural edits applied in one Undo group.",
                    new
                    {
                        undo_name = undoName,
                        undo_group = group,
                        operations = plan.Operations,
                        temporary_ids = temporary,
                        atomicity = "recorded_plain_scene_hierarchy",
                    }
                );
            }
            catch (Exception exception)
            {
                bool restored = false;
                string rollbackError = null;
                try
                {
                    Undo.RevertAllDownToGroup(group);
                    Verify(plan);
                    restored = true;
                }
                catch (Exception rollback)
                {
                    rollbackError = rollback.Message;
                }
                return new ErrorResponse(
                    "Structural apply failed: " + exception.Message,
                    new
                    {
                        rollback_verified = restored,
                        rollback_error = rollbackError,
                        atomicity = "recorded_plain_scene_hierarchy",
                    }
                );
            }
            finally
            {
                Undo.IncrementCurrentGroup();
            }
        }

        private static void Verify(Plan plan)
        {
            if (plan.Root == null || plan.Root.scene != plan.Scene || Snapshot(plan.Root) != plan.Hash)
                throw new InvalidOperationException("Structural preview hierarchy or object state drifted. Preview again.");
        }

        private static string Snapshot(GameObject root)
        {
            if (
                root == null
                || root.transform.parent != null
                || EditorUtility.IsPersistent(root)
                || !root.scene.IsValid()
                || !root.scene.isLoaded
                || EditorSceneManager.IsPreviewScene(root.scene)
            )
                throw new ArgumentException("Structural scope must be a root GameObject in a loaded ordinary scene.");
            var nodes = new JArray();
            foreach (Transform transform in Objects(root))
            {
                GameObject go = transform.gameObject;
                if (PrefabUtility.IsPartOfPrefabInstance(go) || go.GetComponents<Component>().Length != 1 || transform.GetType() != typeof(Transform))
                    throw new ArgumentException(
                        "Structural plans require Transform-only subtrees; prefab instances, native/custom components and RequireComponent dependencies are excluded."
                    );
                nodes.Add(
                    new JObject
                    {
                        ["id"] = Key(go),
                        ["parent"] = transform.parent == null ? null : Key(transform.parent.gameObject),
                        ["sibling"] = transform.GetSiblingIndex(),
                        ["name"] = go.name,
                        ["active"] = go.activeSelf,
                        ["layer"] = go.layer,
                        ["tag"] = go.tag,
                        ["flags"] = (int)go.hideFlags,
                        ["position"] = new JArray(transform.localPosition.x, transform.localPosition.y, transform.localPosition.z),
                        ["rotation"] = new JArray(transform.localRotation.x, transform.localRotation.y, transform.localRotation.z, transform.localRotation.w),
                        ["scale"] = new JArray(transform.localScale.x, transform.localScale.y, transform.localScale.z),
                    }
                );
            }
            string state = new JObject { ["scene_path"] = root.scene.path, ["nodes"] = nodes }.ToString(Formatting.None);
            if (state.Length > 262144)
                throw new ArgumentException("Structural scope state exceeds 262144 characters.");
            return Hash128.Compute(state).ToString();
        }

        private static List<Transform> Objects(GameObject root)
        {
            var nodes = new List<Transform>();
            var pending = new Stack<Transform>();
            pending.Push(root.transform);
            while (pending.Count > 0)
            {
                Transform node = pending.Pop();
                nodes.Add(node);
                if (nodes.Count > MaxNodes || pending.Count + node.childCount > MaxNodes)
                    throw new ArgumentException($"Structural scope may contain at most {MaxNodes} objects.");
                for (int i = node.childCount - 1; i >= 0; i--)
                    pending.Push(node.GetChild(i));
            }
            return nodes;
        }

        private static bool DescendsFrom(string key, string ancestor, Dictionary<string, string> parents)
        {
            while (key != null)
            {
                if (key == ancestor)
                    return true;
                key = parents[key];
            }
            return false;
        }

        private static string Reference(JObject operation, string field, Dictionary<string, string> parents)
        {
            JToken token = operation[field];
            if (token == null || (token.Type != JTokenType.Integer && token.Type != JTokenType.String))
                throw new ArgumentException(field + " must be an instance ID or earlier temporary ID.");
            string key = token.ToString();
            if (!parents.ContainsKey(key))
                throw new ArgumentException(field + " is outside the scope, deleted, or references a future temporary ID.");
            return key;
        }

        private static string Key(GameObject go) => go.GetInstanceIDCompat().ToString(CultureInfo.InvariantCulture);

        private static string Text(JObject request, string field)
        {
            if (request[field]?.Type != JTokenType.String || string.IsNullOrWhiteSpace(request[field].Value<string>()))
                throw new ArgumentException(field + " must be a nonempty string.");
            return request[field].Value<string>();
        }

        private static void Keys(JObject request, params string[] allowed)
        {
            if (request.Properties().Any(property => !allowed.Contains(property.Name)))
                throw new ArgumentException("Unsupported structural plan field.");
        }
    }
}
