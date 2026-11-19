using System;
using System.Collections;
using System.Linq;
using System.Reflection;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnity.Editor.Tools.Netcode
{
    internal static class NetcodeAccess
    {
        internal const string PackageName = "com.unity.netcode.gameobjects";
        internal static readonly string[] Settings = { "ProtocolVersion", "TickRate", "EnableSceneManagement", "ForceSamePrefabs", "ConnectionApproval" };

        internal static Type Type(string name) => System.Type.GetType("Unity.Netcode." + name + ", Unity.Netcode.Runtime", false);

        internal static bool TryGet(object target, string name, out object value)
        {
            if (target == null)
                throw new InvalidOperationException("netcode_api_unsupported: null owner for " + name + ".");
            var field = target.GetType().GetField(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            if (field != null && (field.IsPublic || field.IsDefined(typeof(SerializeField), false)))
            {
                value = field.GetValue(target);
                return true;
            }
            var property = target.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
            if (property?.GetGetMethod() != null)
            {
                value = property.GetValue(target);
                return true;
            }
            value = null;
            return false;
        }

        internal static object Get(object target, string name) =>
            TryGet(target, name, out object value)
                ? value
                : throw new InvalidOperationException("netcode_api_unsupported: missing readable member " + name + ".");

        internal static IEnumerable Sequence(object target, string name) =>
            Get(target, name) as IEnumerable ?? throw new InvalidOperationException("netcode_api_unsupported: " + name + " is not a collection.");

        internal static JObject Capabilities()
        {
            Type manager = Type("NetworkManager");
            PackageInfo package = PackageInfo.GetAllRegisteredPackages().FirstOrDefault(p => p.name == PackageName);
            return new JObject
            {
                ["package_name"] = PackageName,
                ["package_available"] = manager != null,
                ["package_installed"] = package != null,
                ["package_version"] = package?.version,
                ["read_only"] = true,
                ["actions"] = new JArray("capabilities", "preflight"),
                ["settings"] = new JArray(Settings),
                ["scope"] = "Explicit loaded main-stage scene, at most 16 managers, 10000 scene objects and 1000 total prefab registrations.",
                ["limitations"] =
                    "Static local configuration inspection only. Does not establish connectivity, peer compatibility, runtime prefab registrations, callbacks, hosting or deployment readiness. No network session is started.",
            };
        }

        internal static JValue Handle(Scene scene)
        {
#if UNITY_6000_4_OR_NEWER
            return new JValue(scene.handle.GetRawData());
#else
            return new JValue((int)scene.handle);
#endif
        }

        internal static Scene Scene(JObject p)
        {
            if (p["scene_handle"]?.Type != JTokenType.Integer)
                throw new ArgumentException("scene_handle must identify an explicitly selected loaded scene.");
            string identity = p["scene_handle"].ToString(Newtonsoft.Json.Formatting.None);
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene selected = SceneManager.GetSceneAt(i);
                if (selected.IsValid() && selected.isLoaded && Handle(selected).ToString(Newtonsoft.Json.Formatting.None) == identity)
                {
                    if (UnityEditor.SceneManagement.EditorSceneManager.IsPreviewScene(selected))
                        throw new ArgumentException("Preview and prefab stage scenes are unsupported preflight scopes.");
                    return selected;
                }
            }
            throw new ArgumentException("scene_handle is stale or does not identify a loaded scene.");
        }

        internal static int Limit(JObject p)
        {
            if (p["max_prefabs"] == null)
                return 1000;
            if (p["max_prefabs"].Type != JTokenType.Integer || !int.TryParse(p["max_prefabs"].ToString(), out int value) || value < 1 || value > 1000)
                throw new ArgumentException("max_prefabs must be an integer in 1..1000.");
            return value;
        }

        internal static JObject Expected(JObject p)
        {
            if (p["expected_settings"] == null)
                return new JObject();
            if (!(p["expected_settings"] is JObject settings))
                throw new ArgumentException("expected_settings must be an object of supported settings.");
            foreach (JProperty setting in settings.Properties())
            {
                if (!Settings.Contains(setting.Name))
                    throw new ArgumentException("Unsupported expected setting: " + setting.Name + ".");
                bool integer = setting.Name == "ProtocolVersion" || setting.Name == "TickRate";
                if (integer)
                {
                    long upper = setting.Name == "ProtocolVersion" ? ushort.MaxValue : uint.MaxValue;
                    long lower = setting.Name == "TickRate" ? 1 : 0;
                    if (
                        setting.Value.Type != JTokenType.Integer
                        || !long.TryParse(setting.Value.ToString(), out long number)
                        || number < lower
                        || number > upper
                    )
                        throw new ArgumentException(setting.Name + " has an invalid integer value.");
                }
                else if (setting.Value.Type != JTokenType.Boolean)
                    throw new ArgumentException(setting.Name + " must be a boolean.");
            }
            return settings;
        }

        internal static JObject Identity(Component component) =>
            new JObject
            {
                ["instance_id"] = component.GetInstanceIDCompat(),
                ["name"] = component.name,
                ["type"] = component.GetType().FullName,
            };

        internal static void Issue(JArray issues, string code, string detail)
        {
            if (issues.Count >= 200)
                throw new InvalidOperationException("preflight_budget_exceeded: more than 200 diagnostics; narrow or repair the selected scene.");
            issues.Add(
                new JObject
                {
                    ["code"] = code,
                    ["severity"] = "error",
                    ["detail"] = detail,
                }
            );
        }
    }
}
