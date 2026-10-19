#if MCP_FOR_UNITY_AI_MODULE
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.AI;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.Navigation
{
    internal static class NavigationObjects
    {
        private static readonly Dictionary<string, string[]> Fields = new Dictionary<string, string[]>
        {
            ["surface"] = new[]
            {
                "enabled",
                "agentTypeID",
                "collectObjects",
                "useGeometry",
                "layerMask",
                "defaultArea",
                "center",
                "size",
                "overrideTileSize",
                "tileSize",
                "overrideVoxelSize",
                "voxelSize",
                "buildHeightMesh",
            },
            ["agent"] = new[]
            {
                "enabled",
                "agentTypeID",
                "areaMask",
                "speed",
                "angularSpeed",
                "acceleration",
                "stoppingDistance",
                "radius",
                "height",
                "baseOffset",
                "autoBraking",
                "autoRepath",
                "autoTraverseOffMeshLink",
                "avoidancePriority",
                "obstacleAvoidanceType",
                "updatePosition",
                "updateRotation",
                "updateUpAxis",
            },
            ["obstacle"] = new[]
            {
                "enabled",
                "shape",
                "center",
                "size",
                "radius",
                "height",
                "carving",
                "carveOnlyStationary",
                "carvingMoveThreshold",
                "carvingTimeToStationary",
            },
            ["link"] = new[] { "enabled", "agentTypeID", "area", "startPoint", "endPoint", "width", "bidirectional", "costModifier", "autoUpdate" },
        };

        internal static Component[] InScene(Scene scene, Type type) =>
            scene.GetRootGameObjects().SelectMany(g => g.GetComponentsInChildren(type, true)).OrderBy(c => c.GetInstanceIDCompat()).ToArray();

        internal static object List(JObject p)
        {
            Scene scene = NavigationAccess.Scene(p);
            Type type = NavigationAccess.Kind((string)p["kind"]);
            int offset = NavigationAccess.Integer(p, "offset", 0);
            int limit = NavigationAccess.Integer(p, "limit", 50);
            if (offset < 0 || limit < 1 || limit > 100)
                throw new ArgumentException("offset must be nonnegative and limit must be 1..100.");
            Component[] all = InScene(scene, type);
            return new SuccessResponse(
                "Navigation components in selected scene.",
                new
                {
                    scene_handle = NavigationAccess.SceneHandle(scene),
                    scene_path = scene.path,
                    total = all.Length,
                    items = all.Skip(offset).Take(limit).Select(Read).ToArray(),
                    next_offset = (long)offset + limit < all.Length ? (int?)(offset + limit) : null,
                }
            );
        }

        internal static JObject Read(Component component)
        {
            string kind =
                component is NavMeshAgent ? "agent"
                : component is NavMeshObstacle ? "obstacle"
                : component.GetType().Name == "NavMeshSurface" ? "surface"
                : "link";
            JObject properties = new JObject();
            foreach (string field in Fields[kind])
            {
                object value = NavigationAccess.Get(component, field);
                if (value == null)
                    continue;
                properties[field] =
                    value is Vector3 vector ? NavigationAccess.Vector(vector)
                    : value is LayerMask mask ? new JValue(mask.value)
                    : value.GetType().IsEnum ? new JValue(value.ToString())
                    : JToken.FromObject(value);
            }
            JObject result = new JObject
            {
                ["component_id"] = component.GetInstanceIDCompat(),
                ["game_object_id"] = component.gameObject.GetInstanceIDCompat(),
                ["name"] = component.name,
                ["kind"] = kind,
                ["scene_handle"] = NavigationAccess.SceneHandle(component.gameObject.scene),
                ["scene_path"] = component.gameObject.scene.path,
                ["scene_dirty"] = component.gameObject.scene.isDirty,
                ["active_in_hierarchy"] = component.gameObject.activeInHierarchy,
                ["properties"] = properties,
            };
            if (kind == "surface")
            {
                NavMeshData data = NavigationAccess.Get(component, "navMeshData") as NavMeshData;
                result["has_baked_data"] = data != null;
                result["data_asset_path"] = data == null ? null : AssetDatabase.GetAssetPath(data);
                // NavMeshData exposes no public baked agent-type getter in supported Unity versions.
                result["data_agent_type_id"] = null;
                result["data_agent_type_available"] = false;
                result["data_agent_type_limitation"] =
                    "Surface properties.agentTypeID is current authoring configuration, not verified provenance of existing baked data.";
            }
            if (component is NavMeshAgent agent)
            {
                result["is_on_nav_mesh"] = agent.isOnNavMesh;
                result["path_pending"] = agent.isActiveAndEnabled && agent.isOnNavMesh ? new JValue(agent.pathPending) : null;
                result["path_status"] = agent.isActiveAndEnabled && agent.isOnNavMesh ? new JValue(agent.pathStatus.ToString()) : null;
            }
            return result;
        }

        internal static object Add(JObject p)
        {
            RequireEditMode();
            Scene scene = NavigationAccess.Scene(p);
            Type type = NavigationAccess.Kind((string)p["kind"]);
            GameObject go = UnityObjectIdCompat.InstanceIDToObjectCompat(NavigationAccess.Integer(p, "target")) as GameObject;
            if (go == null || go.scene != scene || EditorUtility.IsPersistent(go))
                throw new ArgumentException("add requires a GameObject instance ID in the selected scene.");
            if (go.GetComponents(type).Length != 0)
                throw new ArgumentException("The target already has this navigation component.");
            if (p["properties"] != null)
                throw new ArgumentException("Add a component first, then explicitly configure its returned component_id.");
            Component component = Undo.AddComponent(go, type);
            EditorSceneManager.MarkSceneDirty(scene);
            return new SuccessResponse("Navigation component added; save scene explicitly.", Read(component));
        }

        internal static object Configure(JObject p)
        {
            RequireEditMode();
            Component component = NavigationAccess.Component(p);
            if (NavigationBakeJobs.Busy(component))
                throw new InvalidOperationException("A bake is pending for this surface; configuration is refused until it finishes or is cancelled.");
            string kind = (string)p["kind"];
            if (!(p["properties"] is JObject patch) || patch.Count == 0 || patch.Count > 24)
                throw new ArgumentException("properties must contain 1..24 explicit supported settings.");
            var updates = new List<KeyValuePair<PropertyInfo, object>>();
            foreach (JProperty item in patch.Properties())
            {
                if (!Fields[kind].Contains(item.Name))
                    throw new ArgumentException("Unsupported " + kind + " setting: " + item.Name);
                PropertyInfo field = component.GetType().GetProperty(item.Name);
                if (field == null || !field.CanWrite)
                    throw new ArgumentException("Setting is unavailable in this installed version: " + item.Name);
                object value = ConvertValue(field, item.Value);
                updates.Add(new KeyValuePair<PropertyInfo, object>(field, value));
            }
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("Configure Navigation " + kind);
            Undo.RecordObject(component, "Configure Navigation " + kind);
            try
            {
                foreach (var update in updates)
                    update.Key.SetValue(component, update.Value);
                if (kind == "link")
                    component.GetType().GetMethod("UpdateLink", Type.EmptyTypes)?.Invoke(component, null);
                Undo.FlushUndoRecordObjects();
                PrefabUtility.RecordPrefabInstancePropertyModifications(component);
                EditorUtility.SetDirty(component);
                EditorSceneManager.MarkSceneDirty(component.gameObject.scene);
                Undo.CollapseUndoOperations(group);
                return new SuccessResponse("Navigation configuration applied; save scene explicitly.", Read(component));
            }
            catch
            {
                Undo.FlushUndoRecordObjects();
                Undo.RevertAllDownToGroup(group);
                if (kind == "link")
                    component.GetType().GetMethod("UpdateLink", Type.EmptyTypes)?.Invoke(component, null);
                throw;
            }
        }

        private static object ConvertValue(PropertyInfo property, JToken token)
        {
            string name = property.Name;
            Type type = property.PropertyType;
            if (type == typeof(bool))
            {
                if (token.Type != JTokenType.Boolean)
                    throw new ArgumentException(name + " must be boolean.");
                return token.Value<bool>();
            }
            if (type == typeof(Vector3))
            {
                Vector3 value = NavigationAccess.Vector(token, name);
                if (name == "size" && (value.x <= 0 || value.y <= 0 || value.z <= 0))
                    throw new ArgumentException("size components must be positive.");
                return value;
            }
            if (type.IsEnum)
            {
                if (token.Type != JTokenType.String || !Enum.IsDefined(type, token.ToString()))
                    throw new ArgumentException(name + " must be a supported named enum value.");
                return Enum.Parse(type, token.ToString());
            }
            if (type == typeof(int) || type == typeof(LayerMask))
            {
                int value = NavigationAccess.Integer(new JObject { [name] = token }, name);
                if (name == "agentTypeID")
                    NavigationAccess.AgentType(value);
                if (name == "area" || name == "defaultArea")
                    NavigationAccess.Area(value);
                if (name == "avoidancePriority" && (value < 0 || value > 99))
                    throw new ArgumentException("avoidancePriority must be 0..99.");
                if (name == "tileSize" && (value < 16 || value > 1024))
                    throw new ArgumentException("tileSize must be 16..1024.");
                if (type == typeof(LayerMask))
                    return (LayerMask)value;
                return value;
            }
            if (type == typeof(float))
            {
                float value = NavigationAccess.Number(token, name);
                if (name != "baseOffset" && name != "costModifier" && value < 0)
                    throw new ArgumentException(name + " must be nonnegative.");
                if ((name == "height" || name == "radius" || name == "voxelSize") && value <= 0)
                    throw new ArgumentException(name + " must be positive.");
                if (name == "costModifier" && value < 0 && value != -1)
                    throw new ArgumentException("costModifier must be -1 (area default) or nonnegative.");
                return value;
            }
            throw new ArgumentException("Unsupported installed property type: " + name);
        }

        internal static void RequireEditMode()
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode)
                throw new InvalidOperationException("Navigation authoring requires stable Edit mode.");
        }
    }
}
#endif
