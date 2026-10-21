using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.SceneEdits;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Bounded native serialized scene edits. Compensation restores recorded component
    /// state only, not arbitrary Editor callbacks, dirty flags, filesystem or external effects.
    /// </summary>
    [McpForUnityTool("manage_scene_edits")]
    public static class ManageSceneEdits
    {
        private const int MaxEdits = 1000;
        private const int MaxPreviews = 32;
        private const double PreviewLifetimeSeconds = 600;
        private static readonly Dictionary<string, Plan> Previews = new Dictionary<string, Plan>(StringComparer.Ordinal);

        private sealed class Edit
        {
            internal Component Component;
            internal GameObject Target;
            internal Transform Parent;
            internal Scene Scene;
            internal JValue SceneHandle;
            internal string ScenePath;
            internal string TargetPath;
            internal int ComponentIndex;
            internal string Property;
            internal JToken Before;
            internal JToken After;

            internal JObject Describe() =>
                new JObject
                {
                    ["target_id"] = Target.GetInstanceIDCompat(),
                    ["target_path"] = TargetPath,
                    ["scene_path"] = ScenePath,
                    ["scene_handle"] = SceneHandle,
                    ["component_type"] = Component.GetType().FullName,
                    ["component_id"] = Component.GetInstanceIDCompat(),
                    ["component_index"] = ComponentIndex,
                    ["property"] = Property,
                    ["before"] = Before.DeepClone(),
                    ["after"] = After.DeepClone(),
                };
        }

        private sealed class Plan
        {
            internal List<Edit> Edits;
            internal double ExpiresAt;
        }

        public static object HandleCommand(JObject parameters)
        {
            try
            {
                EnsureEditMode();
                if (parameters == null)
                    throw new ArgumentException("Parameters are required.");
                if (parameters.ToString(Newtonsoft.Json.Formatting.None).Length > 4 * 1024 * 1024)
                    throw new ArgumentException("Scene edit request exceeds 4 Mi characters.");
                string action = RequiredString(parameters, "action");
                switch (action)
                {
                    case "preview_structure":
                        return SceneStructurePlans.Preview(parameters);
                    case "apply_structure":
                        return SceneStructurePlans.Apply(parameters);
                    case "preview":
                        if (parameters["preview_id"] != null)
                            throw new ArgumentException("preview_id is only valid for apply.");
                        return Preview(Prepare(parameters["edits"], false));
                    case "apply":
                        Plan plan;
                        if (parameters["preview_id"] != null)
                        {
                            if (parameters["edits"] != null)
                                throw new ArgumentException("Apply accepts either preview_id or edits, not both.");
                            string token = RequiredString(parameters, "preview_id");
                            if (!Previews.TryGetValue(token, out plan))
                                throw new ArgumentException("Unknown or already consumed preview_id. Preview again.");
                            Previews.Remove(token); // A token is one attempt, including drift/expiry failures.
                            if (EditorApplication.timeSinceStartup >= plan.ExpiresAt)
                                throw new ArgumentException("The preview has expired. Preview again.");
                        }
                        else
                            plan = Prepare(parameters["edits"], true);
                        string undoName = parameters["undo_name"] == null ? "MCP Scene Edits" : RequiredString(parameters, "undo_name");
                        if (undoName.Length > 80)
                            throw new ArgumentException("undo_name must be at most 80 characters.");
                        Verify(plan, false);
                        return Apply(plan, undoName);
                    default:
                        throw new ArgumentException("action must be preview or apply.");
                }
            }
            catch (Exception exception)
            {
                return new ErrorResponse(exception.Message);
            }
        }

        private static object Preview(Plan plan)
        {
            foreach (
                string expired in Previews.Where(entry => entry.Value.ExpiresAt <= EditorApplication.timeSinceStartup).Select(entry => entry.Key).ToArray()
            )
                Previews.Remove(expired);
            if (Previews.Count >= MaxPreviews)
                throw new ArgumentException("Preview capacity reached. Apply a preview or wait for expiry.");
            string token = Guid.NewGuid().ToString("N");
            Previews.Add(token, plan);
            return new SuccessResponse(
                "Preview prepared; no scene values were changed.",
                new
                {
                    preview_id = token,
                    expires_in_seconds = PreviewLifetimeSeconds,
                    edits = Describe(plan),
                }
            );
        }

        private static Plan Prepare(JToken token, bool requireExpected)
        {
            if (!(token is JArray edits) || edits.Count < 1 || edits.Count > MaxEdits)
                throw new ArgumentException($"edits must contain 1 to {MaxEdits} edit objects.");
            var prepared = new List<Edit>();
            var keys = new HashSet<string>(StringComparer.Ordinal);
            foreach (JToken entry in edits)
            {
                if (!(entry is JObject request))
                    throw new ArgumentException("Each edit must be an object.");
                GameObject target = ResolveTarget(request);
                string typeName = RequiredString(request, "component_type");
                if (typeName.StartsWith("UnityEngine.", StringComparison.Ordinal))
                    typeName = typeName.Substring("UnityEngine.".Length);
                if (!SceneEditValues.Types.TryGetValue(typeName, out Type type))
                    throw new ArgumentException(
                        "Supported component types: Transform, BoxCollider, SphereCollider, CapsuleCollider, Rigidbody. Custom components are excluded."
                    );
                Component[] components = target.GetComponents(type);
                int index = 0;
                if (request["component_index"] != null)
                {
                    if (request["component_index"].Type != JTokenType.Integer)
                        throw new ArgumentException("component_index must be a nonnegative integer.");
                    index = request["component_index"].Value<int>();
                }
                else if (components.Length > 1)
                    throw new ArgumentException("Ambiguous component; specify component_index.");
                if (index < 0 || index >= components.Length || components[index].GetType() != type)
                    throw new ArgumentException("An existing exact component and valid component_index are required.");
                Component component = components[index];
                string path = SceneEditValues.ResolvePath(type, RequiredString(request, "property"));
                if (!keys.Add(component.GetInstanceIDCompat() + ":" + path))
                    throw new ArgumentException("Duplicate edits to the same component property are not supported.");
                using (var serialized = new SerializedObject(component))
                {
                    SerializedProperty property = serialized.FindProperty(path);
                    if (property == null || !property.editable)
                        throw new ArgumentException($"Serialized property '{path}' is unavailable or read-only in this Unity version.");
                    JToken before = SceneEditValues.Read(property);
                    SceneEditValues.Normalize(property, before);
                    JToken after = SceneEditValues.Normalize(property, request["value"]);
                    if (requireExpected && request["expected_old"] == null)
                        throw new ArgumentException("Direct apply requires expected_old on every edit, or a preview_id.");
                    if (request["expected_old"] != null && !JToken.DeepEquals(before, SceneEditValues.Normalize(property, request["expected_old"])))
                        throw new ArgumentException($"expected_old drift for '{path}' on '{target.name}'.");
                    prepared.Add(
                        new Edit
                        {
                            Component = component,
                            Target = target,
                            Parent = target.transform.parent,
                            Scene = target.scene,
                            SceneHandle = DescribeSceneHandle(target.scene),
                            ScenePath = target.scene.path,
                            TargetPath = GameObjectLookup.GetGameObjectPath(target),
                            ComponentIndex = index,
                            Property = path,
                            Before = before,
                            After = after,
                        }
                    );
                }
            }
            return new Plan
            {
                Edits = prepared
                    .OrderBy(edit => edit.SceneHandle.Value<decimal>())
                    .ThenBy(edit => edit.TargetPath, StringComparer.Ordinal)
                    .ThenBy(edit => edit.Component.GetInstanceIDCompat())
                    .ThenBy(edit => edit.Property, StringComparer.Ordinal)
                    .ToList(),
                ExpiresAt = EditorApplication.timeSinceStartup + PreviewLifetimeSeconds,
            };
        }

        private static GameObject ResolveTarget(JObject request)
        {
            JToken target = request["target"];
            if (target == null || (target.Type != JTokenType.String && target.Type != JTokenType.Integer) || target.ToString().Length > 1024)
                throw new ArgumentException("target must be an instance ID or bounded name/path.");
            string method = request["search_method"] == null ? "by_id" : RequiredString(request, "search_method");
            if (method != "by_id" && method != "by_name" && method != "by_path")
                throw new ArgumentException("search_method must be by_id, by_name, or by_path.");
            List<int> matches = GameObjectLookup.SearchGameObjects(method, target.ToString(), true, 2);
            if (matches.Count != 1)
                throw new ArgumentException("Target must resolve to exactly one scene GameObject; use an instance ID or unique path.");
            GameObject go = GameObjectLookup.FindById(matches[0]);
            EnsureSceneTarget(go);
            if (GameObjectLookup.GetGameObjectPath(go).Length > 4096 || go.scene.path.Length > 1024)
                throw new ArgumentException("Resolved target identity exceeds preview output limits.");
            return go;
        }

        private static void EnsureEditMode()
        {
            if (
                EditorApplication.isPlayingOrWillChangePlaymode
                || EditorApplication.isCompiling
                || EditorApplication.isUpdating
                || PrefabStageUtility.GetCurrentPrefabStage() != null
            )
                throw new ArgumentException("Scene edits require a stable Edit-mode scene outside prefab stages.");
        }

        private static void EnsureSceneTarget(GameObject go)
        {
            if (go == null || EditorUtility.IsPersistent(go) || !go.scene.IsValid() || !go.scene.isLoaded || EditorSceneManager.IsPreviewScene(go.scene))
                throw new ArgumentException("Only GameObjects in loaded ordinary scenes are supported.");
        }

        private static void Verify(Plan plan, bool applied)
        {
            foreach (Edit edit in plan.Edits)
            {
                EnsureSceneTarget(edit.Target);
                if (
                    edit.Component == null
                    || edit.Component.gameObject != edit.Target
                    || edit.Target.scene != edit.Scene
                    || edit.Target.scene.path != edit.ScenePath
                    || edit.Target.transform.parent != edit.Parent
                    || GameObjectLookup.GetGameObjectPath(edit.Target) != edit.TargetPath
                )
                    throw new InvalidOperationException("Preview target identity, scene, or hierarchy drifted. Preview again.");
                Component[] components = edit.Target.GetComponents(edit.Component.GetType());
                if (edit.ComponentIndex >= components.Length || components[edit.ComponentIndex] != edit.Component)
                    throw new InvalidOperationException("Preview component selection drifted. Preview again.");
                using (var serialized = new SerializedObject(edit.Component))
                    if (!JToken.DeepEquals(SceneEditValues.Read(serialized.FindProperty(edit.Property)), applied ? edit.After : edit.Before))
                        throw new InvalidOperationException($"Serialized value drift for '{edit.Property}' on '{edit.TargetPath}'.");
            }
        }

        private static object Apply(Plan plan, string undoName)
        {
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName(undoName);
            try
            {
                UnityEngine.Object[] objects = plan.Edits.Select(edit => (UnityEngine.Object)edit.Component).Distinct().ToArray();
                Undo.RegisterCompleteObjectUndo(objects, undoName);
                foreach (Edit edit in plan.Edits)
                {
                    using (var serialized = new SerializedObject(edit.Component))
                    {
                        SceneEditValues.Write(serialized.FindProperty(edit.Property), edit.After);
                        serialized.ApplyModifiedPropertiesWithoutUndo();
                    }
                    if (PrefabUtility.IsPartOfPrefabInstance(edit.Component))
                        PrefabUtility.RecordPrefabInstancePropertyModifications(edit.Component);
                }
                Verify(plan, true);
                foreach (var scene in plan.Edits.Select(edit => edit.Target.scene).Distinct())
                    EditorSceneManager.MarkSceneDirty(scene);
                Undo.CollapseUndoOperations(group);
                return new SuccessResponse(
                    "Scene edits applied in one Undo group.",
                    new
                    {
                        undo_name = undoName,
                        undo_group = group,
                        edits = Describe(plan),
                        atomicity = "recorded_native_component_state",
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
                    Verify(plan, false);
                    restored = true;
                }
                catch (Exception rollbackException)
                {
                    rollbackError = rollbackException.Message;
                }
                return new ErrorResponse(
                    "Scene edit apply failed: " + exception.Message,
                    new
                    {
                        rollback_verified = restored,
                        rollback_error = rollbackError,
                        atomicity = "recorded_native_component_state",
                    }
                );
            }
            finally
            {
                Undo.IncrementCurrentGroup();
            }
        }

        private static JArray Describe(Plan plan) => new JArray(plan.Edits.Select(edit => edit.Describe()));

        private static JValue DescribeSceneHandle(Scene scene)
        {
#if UNITY_6000_4_OR_NEWER
            return new JValue(scene.handle.GetRawData());
#else
            return new JValue((int)scene.handle);
#endif
        }

        private static string RequiredString(JObject request, string key)
        {
            if (request[key]?.Type != JTokenType.String || string.IsNullOrWhiteSpace(request[key].Value<string>()))
                throw new ArgumentException($"'{key}' must be a nonempty string.");
            return request[key].Value<string>();
        }
    }
}
