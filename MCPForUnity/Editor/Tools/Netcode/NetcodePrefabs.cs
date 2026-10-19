using System;
using System.Collections.Generic;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Netcode
{
    internal static class NetcodePrefabs
    {
        private static bool Prefab(GameObject go, JArray issues, string location)
        {
            if (go == null)
            {
                NetcodeAccess.Issue(issues, "prefab_missing", location + " references no prefab.");
                return false;
            }
            if (!EditorUtility.IsPersistent(go) || !PrefabUtility.IsPartOfPrefabAsset(go))
            {
                NetcodeAccess.Issue(issues, "prefab_asset_required", location + " must reference a prefab asset.");
                return false;
            }
            Type networkObject = NetcodeAccess.Type("NetworkObject");
            if (networkObject == null || go.GetComponent(networkObject) == null)
            {
                NetcodeAccess.Issue(issues, "network_object_missing", location + " requires a root NetworkObject.");
                return false;
            }
            return true;
        }

        internal static JArray Inspect(object config, JArray issues, ref int total, int limit)
        {
            var rows = new JArray();
            var sources = new HashSet<uint>();
            var listIds = new HashSet<int>();
            GameObject player = NetcodeAccess.Get(config, "PlayerPrefab") as GameObject;
            if (player != null)
                Prefab(player, issues, "PlayerPrefab");
            object prefabs = NetcodeAccess.Get(config, "Prefabs");
            int listIndex = 0;
            foreach (object list in NetcodeAccess.Sequence(prefabs, "NetworkPrefabsLists"))
            {
                if (++listIndex > 1000)
                    throw new InvalidOperationException("preflight_budget_exceeded: more than 1000 prefab list references.");
                if (!(list is UnityEngine.Object asset) || asset == null)
                {
                    NetcodeAccess.Issue(issues, "prefab_list_missing", "NetworkPrefabsLists contains a missing list reference.");
                    continue;
                }
                if (!listIds.Add(asset.GetInstanceIDCompat()))
                    NetcodeAccess.Issue(issues, "duplicate_prefab_list", "The same NetworkPrefabsList is registered more than once.");
                int index = 0;
                foreach (object entry in NetcodeAccess.Sequence(list, "PrefabList"))
                {
                    if (++total > limit)
                        throw new InvalidOperationException("preflight_budget_exceeded: prefab registrations exceed max_prefabs; inspection is incomplete.");
                    string location = "NetworkPrefabsLists[" + (listIndex - 1) + "][" + index++ + "]";
                    var row = new JObject { ["location"] = location, ["list_path"] = AssetDatabase.GetAssetPath(asset) };
                    rows.Add(row);
                    if (entry == null)
                    {
                        NetcodeAccess.Issue(issues, "prefab_entry_missing", location + " is null.");
                        continue;
                    }
                    string mode = NetcodeAccess.Get(entry, "Override").ToString();
                    row["override"] = mode;
                    GameObject source;
                    uint hash;
                    switch (mode)
                    {
                        case "None":
                            source = NetcodeAccess.Get(entry, "Prefab") as GameObject;
                            if (!Prefab(source, issues, location))
                                continue;
                            hash = Convert.ToUInt32(NetcodeAccess.Get(entry, "SourcePrefabGlobalObjectIdHash"));
                            break;
                        case "Prefab":
                            source = NetcodeAccess.Get(entry, "SourcePrefabToOverride") as GameObject;
                            if (
                                !Prefab(source, issues, location + " source")
                                || !Prefab(NetcodeAccess.Get(entry, "OverridingTargetPrefab") as GameObject, issues, location + " target")
                            )
                                continue;
                            hash = Convert.ToUInt32(NetcodeAccess.Get(entry, "SourcePrefabGlobalObjectIdHash"));
                            break;
                        case "Hash":
                            source = null;
                            hash = Convert.ToUInt32(NetcodeAccess.Get(entry, "SourceHashToOverride"));
                            if (!Prefab(NetcodeAccess.Get(entry, "OverridingTargetPrefab") as GameObject, issues, location + " target"))
                                continue;
                            break;
                        default:
                            NetcodeAccess.Issue(issues, "prefab_override_invalid", location + " has unsupported override mode " + mode + ".");
                            continue;
                    }
                    row["source_path"] = source == null ? null : AssetDatabase.GetAssetPath(source);
                    row["source_hash"] = hash;
                    if (hash == 0)
                        NetcodeAccess.Issue(issues, "prefab_hash_invalid", location + " source hash is zero.");
                    if (!sources.Add(hash))
                        NetcodeAccess.Issue(issues, "duplicate_prefab", location + " repeats source hash " + hash + ".");
                }
            }
            return rows;
        }
    }
}
