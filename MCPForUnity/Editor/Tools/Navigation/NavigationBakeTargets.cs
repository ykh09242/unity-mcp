#if MCP_FOR_UNITY_AI_MODULE
using System;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.AI;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.Navigation
{
    internal static class NavigationBakeTargets
    {
        internal static JObject Capture(Component surface)
        {
            Scene scene = surface.gameObject.scene;
            if (string.IsNullOrEmpty(scene.path) || scene.isDirty)
                throw new InvalidOperationException(
                    "Reload continuation requires a saved, clean scene. Save the scene and explicitly request another bake if tracking is interrupted."
                );
            string sceneGuid = AssetDatabase.AssetPathToGUID(scene.path);
            string target = StableId(surface);
            return new JObject
            {
                ["version"] = 1,
                ["target"] = target,
                ["scene_guid"] = sceneGuid,
                ["scene_hash"] = AssetDatabase.GetAssetDependencyHash(scene.path).ToString(),
                ["configuration_hash"] = Configuration(surface),
                ["backend"] = Backend(surface),
            };
        }

        internal static Component Resolve(JObject checkpoint)
        {
            string path = AssetDatabase.GUIDToAssetPath((string)checkpoint["scene_guid"]);
            if (string.IsNullOrEmpty(path))
                throw new InvalidOperationException("Queued bake scene is missing. Restore the scene and request another bake explicitly.");
            Scene scene = SceneManager.GetSceneByPath(path);
            if (!scene.IsValid() || !scene.isLoaded || scene.isDirty || UnityEditor.SceneManagement.EditorSceneManager.IsPreviewScene(scene))
                throw new InvalidOperationException("Queued bake requires its original loaded, clean scene; no scene is opened or saved implicitly.");
            if (!GlobalObjectId.TryParse((string)checkpoint["target"], out var id))
                throw new InvalidOperationException("Queued bake target identity is invalid.");
            Component surface = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(id) as Component;
            if (surface == null || surface.gameObject.scene != scene || surface.GetType() != NavigationAccess.Kind("surface"))
                throw new InvalidOperationException(
                    "Queued surface is missing or its backend changed. Inspect the target and request another bake explicitly."
                );
            JObject current = Capture(surface);
            if (!JToken.DeepEquals(current, checkpoint))
                throw new InvalidOperationException(
                    "Queued bake scene, source assets, surface configuration, agent settings or backend changed; no bake performed. Inspect and request another bake explicitly."
                );
            return surface;
        }

        private static string Backend(Component surface) =>
            surface.GetType().AssemblyQualifiedName
            + ":"
            + surface.GetType().Assembly.ManifestModule.ModuleVersionId
            + ":"
            + UnityEditor.PackageManager.PackageInfo.FindForAssembly(surface.GetType().Assembly)?.version;

        private static string Configuration(Component surface)
        {
            var source = new JArray();
            // Children-only collection is validated by the caller. Fingerprint live source
            // configuration as well as saved asset dependencies, including stable references.
            foreach (Component component in surface.GetComponentsInChildren<Component>(true))
            {
                if (component == null)
                    throw new InvalidOperationException("Missing source scripts prevent safe bake continuation.");
                JObject serialized = JObject.Parse(EditorJsonUtility.ToJson(component));
                foreach (JObject reference in serialized.Descendants().OfType<JObject>().Where(j => j["instanceID"] != null).ToArray())
                {
                    int instanceId = (int)reference["instanceID"];
                    UnityEngine.Object value = instanceId == 0 ? null : UnityObjectIdCompat.InstanceIDToObjectCompat(instanceId);
                    if (instanceId != 0 && value == null)
                        throw new InvalidOperationException("An unresolved source reference prevents safe bake continuation.");
                    reference.RemoveAll();
                    reference["global_id"] = value == null ? null : StableId(value);
                }
                source.Add(
                    new JObject
                    {
                        ["id"] = StableId(component),
                        ["type"] = component.GetType().AssemblyQualifiedName,
                        ["data"] = serialized,
                    }
                );
            }
            int agent = (int)NavigationAccess.Get(surface, "agentTypeID");
            source.Add(JsonUtility.ToJson(NavMesh.GetSettingsByID(agent)));
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(source.ToString(Formatting.None)))).Replace("-", "");
        }

        private static string StableId(UnityEngine.Object value)
        {
            GlobalObjectId id = GlobalObjectId.GetGlobalObjectIdSlow(value);
            if (id.identifierType == 0 || id.targetObjectId == 0 || string.IsNullOrEmpty(AssetDatabase.GUIDToAssetPath(id.assetGUID.ToString())))
                throw new InvalidOperationException(
                    "An unsaved target/source reference prevents safe reload continuation. Save scene/assets before requesting another bake."
                );
            return id.ToString();
        }
    }
}
#endif
