#if MCP_FOR_UNITY_AI_MODULE
using System;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.AI;
using UnityEngine.SceneManagement;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnity.Editor.Tools.Navigation
{
    internal static class NavigationAccess
    {
        internal static Type OptionalType(string name) =>
            AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType("Unity.AI.Navigation." + name)).FirstOrDefault(t => t != null);

        internal static Type Kind(string kind)
        {
            switch (kind)
            {
                case "surface":
                    return OptionalType("NavMeshSurface")
                        ?? throw new InvalidOperationException(
                            "ai_navigation_package_unavailable: install com.unity.ai.navigation explicitly to author surfaces."
                        );
                case "link":
                    return OptionalType("NavMeshLink")
                        ?? throw new InvalidOperationException(
                            "ai_navigation_package_unavailable: install com.unity.ai.navigation explicitly to author links."
                        );
                case "agent":
                    return typeof(NavMeshAgent);
                case "obstacle":
                    return typeof(NavMeshObstacle);
                default:
                    throw new ArgumentException("kind must be surface, agent, obstacle or link.");
            }
        }

        internal static object Capabilities()
        {
            Type surface = OptionalType("NavMeshSurface");
            string version = surface == null ? null : PackageInfo.FindForAssembly(surface.Assembly)?.version;
            return new SuccessResponse(
                "Navigation capabilities.",
                new
                {
                    module_available = true,
                    package_available = surface != null,
                    package_name = "com.unity.ai.navigation",
                    package_version = version,
                    surface_bake_supported = surface?.GetMethod("BuildNavMesh", Type.EmptyTypes) != null,
                    native_bake_interruptible = false,
                    baked_agent_type_provenance_available = false,
                    bake_collection = "Children only; one explicit surface per request",
                    query_scope = "Unity native queries use the global NavMesh; known active foreign-scene surfaces cause refusal. Runtime-added NavMeshData without a surface cannot be scene-attributed.",
                    persistence = "Configuration uses scene Undo. Save the scene explicitly; generated bake data is not automatically saved as an asset.",
                }
            );
        }

        internal static JValue SceneHandle(Scene scene)
        {
#if UNITY_6000_4_OR_NEWER
            return new JValue(scene.handle.GetRawData());
#else
            // Unity 6000.3 exposes an opaque type through the legacy integer wire identity.
            return new JValue((int)scene.handle);
#endif
        }

        internal static Scene Scene(JObject p)
        {
            JToken requested = p["scene_handle"];
            if (requested?.Type != JTokenType.Integer)
                throw new ArgumentException("scene_handle must be the integer wire identity of an explicitly selected loaded scene.");
            // Compare with actual loaded identities instead of narrowing modern opaque handles to Int32.
            string identity = requested.ToString(Newtonsoft.Json.Formatting.None);
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                if (!scene.IsValid() || !scene.isLoaded || SceneHandle(scene).ToString(Newtonsoft.Json.Formatting.None) != identity)
                    continue;
                if (UnityEditor.SceneManagement.EditorSceneManager.IsPreviewScene(scene))
                    throw new ArgumentException("Preview and prefab stage scenes are unsupported navigation scopes.");
                return scene;
            }
            throw new ArgumentException("scene_handle must identify an explicitly selected loaded scene; no narrowing or truncation is performed.");
        }

        internal static Component Component(JObject p)
        {
            Scene scene = Scene(p);
            Type type = Kind((string)p["kind"]);
            UnityEngine.Object selected = UnityObjectIdCompat.InstanceIDToObjectCompat(Integer(p, "target"));
            if (selected is Component exact && exact.GetType() == type)
            {
                if (exact.gameObject.scene != scene)
                    throw new ArgumentException("Target component is outside the selected scene.");
                return exact;
            }
            if (!(selected is GameObject go) || go.scene != scene)
                throw new ArgumentException("target must identify a component or GameObject in the selected scene.");
            Component[] matches = go.GetComponents(type);
            if (matches.Length != 1)
                throw new ArgumentException("Target must contain exactly one matching component; use the exact component instance ID for ambiguity.");
            return matches[0];
        }

        internal static int Integer(JObject p, string name, int? fallback = null)
        {
            JToken token = p[name];
            if (token == null && fallback.HasValue)
                return fallback.Value;
            if (token?.Type != JTokenType.Integer || !long.TryParse(token.ToString(), out long value) || value < int.MinValue || value > int.MaxValue)
                throw new ArgumentException(name + " must be a 32-bit integer.");
            return (int)value;
        }

        internal static float Number(JToken token, string name)
        {
            if (token == null || (token.Type != JTokenType.Float && token.Type != JTokenType.Integer))
                throw new ArgumentException(name + " must be a finite number.");
            float value = token.Value<float>();
            if (float.IsNaN(value) || float.IsInfinity(value))
                throw new ArgumentException(name + " must be a finite number.");
            return value;
        }

        internal static Vector3 Vector(JToken token, string name)
        {
            if (!(token is JArray a) || a.Count != 3)
                throw new ArgumentException(name + " must contain three finite numbers.");
            return new Vector3(Number(a[0], name), Number(a[1], name), Number(a[2], name));
        }

        internal static JArray Vector(Vector3 v) => new JArray(v.x, v.y, v.z);

        internal static object Get(Component c, string name) => c.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance)?.GetValue(c);

        internal static void AgentType(int id)
        {
            for (int i = 0; i < NavMesh.GetSettingsCount(); i++)
                if (NavMesh.GetSettingsByIndex(i).agentTypeID == id)
                    return;
            throw new ArgumentException("unknown_agent_type: agent_type_id/agentTypeID is not a configured NavMesh agent type.");
        }

        internal static void Area(int area)
        {
#if UNITY_6000_0_OR_NEWER
            string[] areaNames = NavMesh.GetAreaNames();
#else
            string[] areaNames = GameObjectUtility.GetNavMeshAreaNames();
#endif
            if (area < 0 || area > 31 || !areaNames.Any(name => NavMesh.GetAreaFromName(name) == area))
                throw new ArgumentException("Area must be a configured NavMesh area index (0..31).");
        }
    }
}
#endif
