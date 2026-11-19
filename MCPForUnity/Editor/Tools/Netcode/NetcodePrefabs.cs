using System;
using System.Collections;
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

        private static IEnumerable<(IEnumerable entries, string location, string path)> Lists(object config, JArray issues)
        {
            if (!NetcodeAccess.TryGet(config, "Prefabs", out object prefabs))
            {
                yield return (NetcodeAccess.Sequence(config, "NetworkPrefabs"), "NetworkPrefabs", null);
                yield break;
            }

            var listIds = new HashSet<int>();
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
                yield return (NetcodeAccess.Sequence(list, "PrefabList"), "NetworkPrefabsLists[" + (listIndex - 1) + "]", AssetDatabase.GetAssetPath(asset));
            }

            // NGO can retain legacy entries until it initializes or migrates an upgraded configuration.
            if (NetcodeAccess.TryGet(config, "OldPrefabList", out object pending) && pending != null)
                yield return (
                    pending as IEnumerable ?? throw new InvalidOperationException("netcode_api_unsupported: OldPrefabList is not a collection."),
                    "OldPrefabList",
                    null
                );
        }

        private static uint SourceHash(object entry, GameObject source)
        {
            // Read the effective source directly, including when NGO would fill SourcePrefabToOverride during validation.
            if (!NetcodeAccess.TryGet(source.GetComponent(NetcodeAccess.Type("NetworkObject")), "GlobalObjectIdHash", out object value))
                value = NetcodeAccess.Get(entry, "SourcePrefabGlobalObjectIdHash");
            return value is uint hash ? hash : throw new InvalidOperationException("netcode_api_unsupported: expected a uint prefab source hash.");
        }

        internal static JArray Inspect(object config, JArray issues, ref int total, int limit)
        {
            var rows = new JArray();
            var sources = new HashSet<uint>();
            GameObject player = NetcodeAccess.Get(config, "PlayerPrefab") as GameObject;
            if (player != null)
                Prefab(player, issues, "PlayerPrefab");
            foreach (var list in Lists(config, issues))
            {
                int index = 0;
                foreach (object entry in list.entries)
                {
                    if (++total > limit)
                        throw new InvalidOperationException("preflight_budget_exceeded: prefab registrations exceed max_prefabs; inspection is incomplete.");
                    string location = list.location + "[" + index++ + "]";
                    var row = new JObject { ["location"] = location, ["list_path"] = list.path };
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
                            hash = SourceHash(entry, source);
                            break;
                        case "Prefab":
                            source = NetcodeAccess.Get(entry, "SourcePrefabToOverride") as GameObject;
                            if (source == null && entry.GetType().GetMethod("Validate", new[] { typeof(int) })?.ReturnType == typeof(bool))
                                source = NetcodeAccess.Get(entry, "Prefab") as GameObject;
                            if (
                                !Prefab(source, issues, location + " source")
                                || !Prefab(NetcodeAccess.Get(entry, "OverridingTargetPrefab") as GameObject, issues, location + " target")
                            )
                                continue;
                            hash = SourceHash(entry, source);
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
