using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Addressables
{
    internal static class AddressablesPreflight
    {
        private static readonly Regex Tokens = new Regex(@"\[([^\[\]]+)\]");
        private static readonly HashSet<string> Deferred = new HashSet<string>(StringComparer.Ordinal)
        {
            "UnityEditor.EditorUserBuildSettings.activeBuildTarget",
            "UnityEngine.AddressableAssets.Addressables.BuildPath",
            "UnityEngine.AddressableAssets.Addressables.RuntimePath",
        };

        internal static JObject ObserveEdit(AddressablesState state, JObject request)
        {
            const string scope = "affected_addressables_entry_or_profile";
            var findings = new JArray();
            var observed = new JObject { ["revision"] = state.Revision };
            bool complete = true;
            try
            {
                string guid = request.Value<string>("entry_guid");
                if (guid != null)
                {
                    JObject entry = ((JArray)state.Document["entries"]).OfType<JObject>().Single(e => e.Value<string>("guid") == guid);
                    observed["entry"] = entry.DeepClone();
                    if (entry.Value<bool>("folder"))
                        complete = false;
                    else
                    {
                        UnityEngine.Object main = AssetDatabase.LoadMainAssetAtPath(entry.Value<string>("path"));
                        if (main == null)
                            Add(findings, "missing_entry_asset", new JObject { ["entry_guid"] = guid });
                        else
                            ObserveReferences(main, entry, findings, ref complete);
                    }
                    string groupGuid = entry.Value<string>("group_guid");
                    JObject group = ((JArray)state.Document["groups"]).OfType<JObject>().SingleOrDefault(g => g.Value<string>("guid") == groupGuid);
                    if (group == null)
                        Add(findings, "missing_entry_group", new JObject { ["group_guid"] = groupGuid });
                    else
                    {
                        var variables = ((JArray)state.Document["variables"]).Select(v => v.Value<string>("id")).ToHashSet();
                        foreach (JObject schema in (JArray)group["schemas"])
                        foreach (string field in new[] { "build_path_id", "load_path_id" })
                            if (schema.Property(field) != null && !variables.Contains(schema.Value<string>(field)))
                                Add(findings, "missing_group_profile_variable", new JObject { ["field"] = field, ["group_guid"] = groupGuid });
                    }
                }
                else
                {
                    string profile = request.Value<string>("profile_id"),
                        name = request.Value<string>("variable_name");
                    var variables = ((JArray)state.Document["variables"])
                        .OfType<JObject>()
                        .ToDictionary(v => v.Value<string>("name"), v => v.Value<string>("id"));
                    observed["profile_id"] = profile;
                    observed["variable_name"] = name;
                    observed["value"] = state.ProfileValue(profile, variables[name]);
                    int work = 0;
                    VisitValue(name, new HashSet<string>(), 0);
                    void VisitValue(string variable, HashSet<string> active, int depth)
                    {
                        if (++work > 128 || depth > 16)
                        {
                            complete = false;
                            return;
                        }
                        if (!active.Add(variable))
                        {
                            Add(findings, "profile_cycle", new JObject { ["variable_name"] = variable });
                            return;
                        }
                        string raw = state.ProfileValue(profile, variables[variable]);
                        if (raw == null)
                            Add(findings, "missing_profile_value", new JObject { ["variable_name"] = variable });
                        else
                        {
                            int tokens = 0;
                            foreach (Match match in Tokens.Matches(raw))
                            {
                                if (++tokens > 64 || findings.Count >= 32)
                                {
                                    complete = false;
                                    break;
                                }
                                string token = match.Groups[1].Value;
                                if (variables.ContainsKey(token))
                                    VisitValue(token, active, depth + 1);
                                else if (!Deferred.Contains(token))
                                    Add(findings, "unknown_profile_token", new JObject { ["token"] = token });
                            }
                            if (raw.Count(c => c == '[') != raw.Count(c => c == ']'))
                                Add(findings, "malformed_profile_token", new JObject());
                        }
                        active.Remove(variable);
                    }
                }
            }
            catch (Exception error)
            {
                complete = false;
                observed["observation_error"] = error.GetType().Name;
            }
            return PostEditVerification.Summary(scope, observed, findings, complete);
        }

        private static void ObserveReferences(UnityEngine.Object main, JObject entry, JArray findings, ref bool complete)
        {
            var objects = new List<UnityEngine.Object> { main };
            if (main is GameObject root)
            {
                var queue = new Queue<Transform>();
                queue.Enqueue(root.transform);
                int nodes = 0;
                while (queue.Count > 0 && nodes < 32 && objects.Count < 64)
                {
                    Transform node = queue.Dequeue();
                    nodes++;
                    var components = PostEditVerification.Components(node.gameObject, out bool all);
                    complete &= all;
                    foreach (Component component in components)
                    {
                        if (objects.Count >= 64)
                        {
                            complete = false;
                            break;
                        }
                        objects.Add(component);
                    }
                    for (int i = 0; i < node.childCount; i++)
                    {
                        if (nodes + queue.Count >= 32)
                        {
                            complete = false;
                            break;
                        }
                        queue.Enqueue(node.GetChild(i));
                    }
                }
                complete &= queue.Count == 0;
            }
            int properties = 0;
            foreach (UnityEngine.Object target in objects)
            {
                if (findings.Count >= 32)
                {
                    complete = false;
                    break;
                }
                if (target == null)
                {
                    Add(findings, "missing_script", Location(entry, ""));
                    continue;
                }
                if (target is Texture || target is Mesh || target is AudioClip)
                    continue;
                using var serialized = new SerializedObject(target);
                SerializedProperty property = serialized.GetIterator();
                bool enter = true;
                while (property.Next(enter))
                {
                    if (++properties > 1024 || findings.Count >= 32)
                    {
                        complete = false;
                        return;
                    }
                    enter = !(property.isArray && property.arraySize > 64);
                    if (!enter)
                        complete = false;
                    if (MissingObjectReference(property))
                        Add(findings, "missing_object_reference", Location(entry, property.propertyPath));
                    if (property.propertyType == SerializedPropertyType.String && property.name == "m_AssetGUID")
                    {
                        int dot = property.propertyPath.LastIndexOf('.');
                        // A top-level field belongs to the Unity object, not an inline AssetReference.
                        if (dot < 0)
                            continue;
                        bool? referenceType = IsAssetReference(serialized.FindProperty(property.propertyPath.Substring(0, dot)));
                        if (!referenceType.HasValue)
                            complete = false;
                        if (referenceType != true)
                            continue;
                        string reference = property.stringValue;
                        if (!string.IsNullOrEmpty(reference) && string.IsNullOrEmpty(AssetDatabase.GUIDToAssetPath(reference)))
                            Add(findings, "missing_asset_reference", Location(entry, property.propertyPath));
                    }
                }
            }
        }

        private static bool? IsAssetReference(SerializedProperty property)
        {
            Type baseType = Type.GetType("UnityEngine.AddressableAssets.AssetReference, Unity.Addressables");
            if (property == null || baseType == null)
                return null;
            if (property.propertyType == SerializedPropertyType.ManagedReference)
            {
                string name = property.managedReferenceFullTypename;
                int split = name.IndexOf(' ');
                Type actual = split < 0 ? null : Type.GetType(name.Substring(split + 1) + ", " + name.Substring(0, split));
                return actual == null ? (bool?)null : baseType.IsAssignableFrom(actual);
            }
            // Inline serialization exposes only a short name. Even a name matching a package
            // class may belong to an unrelated user type; only managed references prove identity.
            return null;
        }

        internal static JArray Read(AddressablesState state)
        {
            const int arrayElementLimit = 256;
            int skippedArrays = 0;
            var entries = ((JArray)state.Document["entries"]).OfType<JObject>().Where(e => !e.Value<bool>("builtin")).ToArray();
            if (entries.Length > 256)
                AddressablesState.Fail("addressables_budget_exceeded", "Preflight is limited to256 explicit entries.");
            var issues = new JArray();
            foreach (
                var duplicate in entries
                    .Where(e => !string.IsNullOrEmpty(e.Value<string>("address")))
                    .GroupBy(e => e.Value<string>("address"), StringComparer.Ordinal)
                    .Where(g => g.Count() > 1)
            )
                Add(
                    issues,
                    "duplicate_address",
                    new JObject { ["address"] = duplicate.Key, ["entry_guids"] = new JArray(duplicate.Select(e => e.Value<string>("guid"))) }
                );
            int properties = 0,
                objects = 0;
            foreach (JObject entry in entries)
            {
                string path = entry.Value<string>("path");
                if (string.IsNullOrEmpty(path))
                {
                    Add(issues, "missing_entry_asset", new JObject { ["entry_guid"] = entry["guid"] });
                    continue;
                }
                if (entry.Value<bool>("folder"))
                    continue;
                UnityEngine.Object main = AssetDatabase.LoadMainAssetAtPath(path);
                if (main == null)
                {
                    Add(issues, "missing_entry_asset", new JObject { ["entry_guid"] = entry["guid"] });
                    continue;
                }
                IEnumerable<UnityEngine.Object> scan = new[] { main };
                if (main is GameObject go)
                {
                    Component[] components = go.GetComponentsInChildren<Component>(true);
                    if (components.Length > 256)
                        AddressablesState.Fail("addressables_budget_exceeded", "A prefab exceeds256 components.");
                    scan = scan.Concat(components);
                }
                foreach (UnityEngine.Object value in scan)
                {
                    if (++objects > 1024)
                        AddressablesState.Fail("addressables_budget_exceeded", "Preflight exceeds1024 loaded objects.");
                    if (value == null)
                    {
                        Add(issues, "missing_script", new JObject { ["entry_guid"] = entry["guid"] });
                        continue;
                    }
                    // Texture/Mesh bulk payloads are not AssetReference-bearing managed fields.
                    if (value is Texture || value is Mesh)
                        continue;
                    using (var serialized = new SerializedObject(value))
                    {
                        var property = serialized.GetIterator();
                        bool enter = true;
                        while (property.Next(enter))
                        {
                            enter = true;
                            if (++properties > 32768)
                                AddressablesState.Fail("addressables_budget_exceeded", "Preflight exceeds32768 serialized properties.");
                            if (property.isArray && property.arraySize > arrayElementLimit)
                            {
                                enter = false;
                                skippedArrays++;
                                JObject skipped = Location(entry, property.propertyPath);
                                skipped["object_identity"] = AddressablesState.Identity(value);
                                skipped["array_size"] = property.arraySize;
                                skipped["array_element_limit"] = arrayElementLimit;
                                Add(issues, "reference_scan_array_skipped", skipped);
                            }
                            if (MissingObjectReference(property))
                                Add(issues, "missing_object_reference", Location(entry, property.propertyPath));
                            if (property.propertyType != SerializedPropertyType.String || property.name != "m_AssetGUID")
                                continue;
                            int dot = property.propertyPath.LastIndexOf('.');
                            if (dot < 0)
                                continue;
                            var parent = serialized.FindProperty(property.propertyPath.Substring(0, dot));
                            string type = parent?.type ?? "";
                            if (parent != null && parent.propertyType == SerializedPropertyType.ManagedReference)
                                type = parent.managedReferenceFullTypename;
                            Type baseType = Type.GetType("UnityEngine.AddressableAssets.AssetReference, Unity.Addressables");
                            bool typed = false;
                            if (parent != null && parent.propertyType == SerializedPropertyType.ManagedReference)
                            {
                                int split = type.IndexOf(' ');
                                Type actual = split < 0 ? null : Type.GetType(type.Substring(split + 1) + ", " + type.Substring(0, split));
                                typed = actual != null && baseType != null && baseType.IsAssignableFrom(actual);
                            }
                            else if (baseType != null)
                            {
                                Type actual = baseType.Assembly.GetType("UnityEngine.AddressableAssets." + type);
                                typed = actual != null && baseType.IsAssignableFrom(actual);
                            }
                            if (!typed)
                                continue;
                            string guid = property.stringValue;
                            if (string.IsNullOrEmpty(guid))
                                continue;
                            JObject location = Location(entry, property.propertyPath);
                            location["reference_guid"] = guid;
                            if (!Regex.IsMatch(guid, @"\A[0-9a-fA-F]{32}\z") || string.IsNullOrEmpty(AssetDatabase.GUIDToAssetPath(guid)))
                                Add(issues, "missing_asset_reference", location);
                            else if (!state.Entries.ContainsKey(guid))
                                Add(issues, "asset_reference_not_explicit", location);
                        }
                    }
                }
            }
            foreach (JObject profile in (JArray)state.Document["profiles"])
                ProfileIssues(state, profile.Value<string>("id"), issues);
            string active = state.Document.Value<string>("active_profile_id");
            if (!((JArray)state.Document["profiles"]).Any(p => p.Value<string>("id") == active))
                Add(issues, "missing_active_profile", new JObject { ["profile_id"] = active });
            var variables = ((JArray)state.Document["variables"]).Select(v => v.Value<string>("id")).ToHashSet(StringComparer.Ordinal);
            foreach (JObject group in (JArray)state.Document["groups"])
            foreach (JObject schema in (JArray)group["schemas"])
            foreach (string field in new[] { "build_path_id", "load_path_id" })
                if (schema.Property(field) != null && !variables.Contains(schema.Value<string>(field)))
                    Add(
                        issues,
                        "missing_group_profile_variable",
                        new JObject
                        {
                            ["group_guid"] = group["guid"],
                            ["field"] = field,
                            ["variable_id"] = schema[field],
                        }
                    );
            var sorted = new JArray(
                issues.OrderBy(x => x.Value<string>("code"), StringComparer.Ordinal).ThenBy(x => x.ToString(Formatting.None), StringComparer.Ordinal)
            );
            state.PreflightCoverage = new JObject
            {
                ["scope"] = "serialized_arrays_in_explicit_entry_main_assets_and_prefab_components",
                ["complete"] = skippedArrays == 0,
                ["skipped_array_count"] = skippedArrays,
                ["array_element_limit"] = arrayElementLimit,
                ["skipped_array_diagnostic_code"] = "reference_scan_array_skipped",
            };
            state.Revision = AddressablesState.Hash(
                state.Revision + "|" + sorted.ToString(Formatting.None) + "|" + state.PreflightCoverage.ToString(Formatting.None),
                "addr1:"
            );
            return sorted;
        }

        private static bool MissingObjectReference(SerializedProperty property)
        {
            if (property.propertyType != SerializedPropertyType.ObjectReference || property.objectReferenceValue != null)
                return false;
#if UNITY_6000_4_OR_NEWER
            return EntityId.ToULong(property.objectReferenceEntityIdValue) != 0;
#else
            return property.objectReferenceInstanceIDValue != 0;
#endif
        }

        private static JObject Location(JObject entry, string path) =>
            new JObject
            {
                ["entry_guid"] = entry["guid"],
                ["path"] = entry["path"],
                ["property_path"] = path,
            };

        private static void Add(JArray rows, string code, JObject detail)
        {
            if (rows.Count >= 8192)
                AddressablesState.Fail("addressables_budget_exceeded", "Preflight exceeds8192 diagnostic rows.");
            detail["code"] = code;
            rows.Add(detail);
        }

        internal static void ProfileIssues(AddressablesState state, string profileId, JArray rows)
        {
            var variables = ((JArray)state.Document["variables"])
                .OfType<JObject>()
                .ToDictionary(v => v.Value<string>("name"), v => v.Value<string>("id"), StringComparer.Ordinal);
            foreach (string name in variables.Keys.OrderBy(v => v, StringComparer.Ordinal))
                Visit(state, profileId, name, variables, new HashSet<string>(StringComparer.Ordinal), rows, 0);
        }

        private static void Visit(
            AddressablesState state,
            string profile,
            string name,
            Dictionary<string, string> variables,
            HashSet<string> chain,
            JArray rows,
            int depth
        )
        {
            if (++state.ProfileTokenWork > 32768)
                AddressablesState.Fail("addressables_budget_exceeded", "Profile token work exceeds32768 visits.");
            if (depth > 16)
                AddressablesState.Fail("addressables_budget_exceeded", "Profile token recursion exceeds16.");
            if (!chain.Add(name))
            {
                Add(rows, "profile_cycle", new JObject { ["profile_id"] = profile, ["variable_name"] = name });
                return;
            }
            string raw = state.ProfileValue(profile, variables[name]);
            if (raw == null)
                Add(rows, "missing_profile_value", new JObject { ["profile_id"] = profile, ["variable_name"] = name });
            else
            {
                foreach (Match token in Tokens.Matches(raw))
                {
                    string value = token.Groups[1].Value;
                    if (variables.ContainsKey(value))
                        Visit(state, profile, value, variables, chain, rows, depth + 1);
                    else if (!Deferred.Contains(value))
                        Add(
                            rows,
                            "unknown_profile_token",
                            new JObject
                            {
                                ["profile_id"] = profile,
                                ["variable_name"] = name,
                                ["token"] = value,
                            }
                        );
                }
                if (raw.Count(c => c == '[') != raw.Count(c => c == ']'))
                    Add(rows, "malformed_profile_token", new JObject { ["profile_id"] = profile, ["variable_name"] = name });
            }
            chain.Remove(name);
        }
    }
}
