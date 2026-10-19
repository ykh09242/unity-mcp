using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.Netcode
{
    internal static class NetcodePreflight
    {
        internal static object Inspect(JObject p)
        {
            Type managerType = NetcodeAccess.Type("NetworkManager");
            if (managerType == null)
                return new ErrorResponse("netcode_package_unavailable: com.unity.netcode.gameobjects is required. No package was installed implicitly.");
            Scene scene = NetcodeAccess.Scene(p);
            int maxPrefabs = NetcodeAccess.Limit(p);
            JObject expected = NetcodeAccess.Expected(p);
            var managers = new List<Component>();
            var pending = new Stack<Transform>();
            foreach (GameObject root in scene.GetRootGameObjects())
                pending.Push(root.transform);
            int visited = 0;
            while (pending.Count > 0)
            {
                if (++visited > 10000)
                    throw new InvalidOperationException("preflight_budget_exceeded: more than 10000 scene objects.");
                Transform current = pending.Pop();
                managers.AddRange(current.GetComponents(managerType));
                if (managers.Count > 16)
                    throw new InvalidOperationException("preflight_budget_exceeded: more than 16 NetworkManagers.");
                for (int i = 0; i < current.childCount; i++)
                    pending.Push(current.GetChild(i));
            }
            var diagnostics = new JArray();
            if (managers.Count == 0)
                NetcodeAccess.Issue(diagnostics, "manager_missing", "The selected scene contains no NetworkManager, including inactive objects.");
            if (managers.Count > 1)
                NetcodeAccess.Issue(
                    diagnostics,
                    "multiple_managers",
                    "The selected scene contains multiple NetworkManagers; choose a single local configuration."
                );
            var rows = new JArray();
            JObject baseline = null;
            int registrations = 0;
            foreach (Component manager in managers)
            {
                JObject row = NetcodeAccess.Identity(manager);
                var config = NetcodeAccess.Get(manager, "NetworkConfig");
                if (config == null)
                {
                    NetcodeAccess.Issue(diagnostics, "configuration_missing", manager.name + " has no NetworkConfig.");
                    rows.Add(row);
                    continue;
                }
                if (!(manager is Behaviour behaviour) || !behaviour.isActiveAndEnabled)
                    NetcodeAccess.Issue(diagnostics, "manager_inactive", manager.name + " is disabled or inactive.");
                bool listening = (bool)NetcodeAccess.Get(manager, "IsListening");
                row["is_listening"] = listening;
                if (listening)
                    NetcodeAccess.Issue(diagnostics, "session_active", manager.name + " is already listening; runtime readiness is outside static preflight.");
                var settings = new JObject();
                foreach (string field in NetcodeAccess.Settings)
                {
                    JToken actual = JToken.FromObject(NetcodeAccess.Get(config, field));
                    settings[field] = actual;
                    JToken wanted = expected[field] ?? baseline?[field];
                    if (wanted != null && !JToken.DeepEquals(wanted, actual))
                        NetcodeAccess.Issue(diagnostics, "settings_mismatch", manager.name + "." + field + ": actual=" + actual + ", expected=" + wanted + ".");
                }
                baseline ??= settings;
                row["settings"] = settings;
                if (settings["TickRate"].Value<long>() == 0)
                    NetcodeAccess.Issue(diagnostics, "tick_rate_invalid", manager.name + " TickRate must be greater than zero.");
                Component transport = NetcodeAccess.Get(config, "NetworkTransport") as Component;
                if (transport == null)
                    NetcodeAccess.Issue(diagnostics, "transport_missing", manager.name + " has no NetworkTransport.");
                else
                {
                    row["transport"] = NetcodeAccess.Identity(transport);
                    Type transportType = NetcodeAccess.Type("NetworkTransport");
                    if (transportType == null || !transportType.IsInstanceOfType(transport))
                        NetcodeAccess.Issue(diagnostics, "transport_invalid", manager.name + " transport does not derive from NetworkTransport.");
                    if (transport.gameObject.scene != scene || EditorUtility.IsPersistent(transport))
                        NetcodeAccess.Issue(diagnostics, "transport_scene_mismatch", manager.name + " transport must belong to the selected scene.");
                    if (!(transport is Behaviour activeTransport) || !activeTransport.isActiveAndEnabled)
                        NetcodeAccess.Issue(diagnostics, "transport_inactive", manager.name + " transport is disabled or inactive.");
                }
                row["prefabs"] = NetcodePrefabs.Inspect(config, diagnostics, ref registrations, maxPrefabs);
                rows.Add(row);
            }
            return new SuccessResponse(
                "Local Netcode configuration inspected without starting a network session.",
                new JObject
                {
                    ["ready"] = diagnostics.Count == 0,
                    ["read_only"] = true,
                    ["complete"] = true,
                    ["scene_handle"] = NetcodeAccess.Handle(scene),
                    ["capabilities"] = NetcodeAccess.Capabilities(),
                    ["manager_count"] = managers.Count,
                    ["managers"] = rows,
                    ["diagnostics"] = diagnostics,
                    ["prefab_registration_count"] = registrations,
                    ["settings_comparison"] =
                        "Explicit expected settings, otherwise first manager in this scene. This does not compare remote peers or builds.",
                }
            );
        }
    }
}
