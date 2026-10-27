using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.PackageManager;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Addressables
{
    internal sealed class AddressablesException : Exception
    {
        internal readonly string Code;

        internal AddressablesException(string code, string message)
            : base(message) => Code = code;
    }

    internal static class AddressablesCompatibility
    {
        // Audited stable families retain the stored profile schema and public authoring APIs.
        // Membership permits probing; it does not replace the per-action API and stored-schema checks.
        internal static bool SupportsVersion(string version)
        {
            if (version == null || version.Length > 64)
                return false;
            string[] parts = version.Split('.');
            if (parts.Length != 3)
                return false;
            var numbers = new int[3];
            for (int i = 0; i < parts.Length; i++)
                if (
                    parts[i].Length == 0
                    || (parts[i].Length > 1 && parts[i][0] == '0')
                    || !int.TryParse(parts[i], NumberStyles.None, CultureInfo.InvariantCulture, out numbers[i])
                )
                    return false;
            return (numbers[0] == 1 && numbers[1] >= 28)
                || numbers[0] == 2
                || (numbers[0] == 3 && numbers[1] >= 1)
                || (numbers[0] == 4 && (numbers[1] > 1 || (numbers[1] == 1 && numbers[2] >= 1)));
        }

        internal static bool RequiresLookupCacheReset(string version) => SupportsVersion(version) && !version.StartsWith("2.", StringComparison.Ordinal);

        internal static bool UsesContentDirectories(string version) =>
            SupportsVersion(version) && (version.StartsWith("3.", StringComparison.Ordinal) || version.StartsWith("4.", StringComparison.Ordinal));

        internal static MethodInfo RequireMethod(object target, string action, string name, Type result, params Type[] parameters)
        {
            MethodInfo[] methods = target
                ?.GetType()
                .GetMethods(BindingFlags.Instance | BindingFlags.Public)
                .Where(m =>
                    m.Name == name
                    && !m.ContainsGenericParameters
                    && m.ReturnType == result
                    && m.GetParameters().Select(p => p.ParameterType).SequenceEqual(parameters)
                )
                .ToArray();
            if (methods == null || methods.Length != 1)
                throw new AddressablesException(
                    "addressables_api_unsupported",
                    action + " requires the native public API signature: " + name + ". No edit was started."
                );
            return methods[0];
        }

        internal static FieldInfo RequireField(Type type, string action, string name, Type expected, bool nonPublic = false)
        {
            FieldInfo field = type?.GetField(name, BindingFlags.Instance | BindingFlags.Public | (nonPublic ? BindingFlags.NonPublic : 0));
            if (field == null || field.FieldType != expected)
                throw new AddressablesException("addressables_api_unsupported", action + " requires the native stored field: " + name + ".");
            return field;
        }
    }

    internal sealed class AddressablesState
    {
        internal UnityEngine.Object Settings;
        internal object ProfilesObject;
        internal string Version;
        internal string Revision;
        internal JObject Document;
        internal readonly Dictionary<string, object> Entries = new Dictionary<string, object>(StringComparer.Ordinal);
        internal readonly Dictionary<string, object> Groups = new Dictionary<string, object>(StringComparer.Ordinal);
        private int characters;
        internal int ProfileTokenWork;

        internal static void Fail(string code, string message) => throw new AddressablesException(code, message);

        internal static object Get(object value, string name)
        {
            if (value == null)
                return null;
            var type = value.GetType();
            var property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public);
            if (property != null)
                return property.GetValue(value);
            var field = type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (field != null)
                return field.GetValue(value);
            throw new AddressablesException("addressables_api_unsupported", "The declared package adapter lacks a required field: " + name);
        }

        internal static string Text(JToken token, string name, int max = 512, bool empty = false)
        {
            if (token?.Type != JTokenType.String || token.Value<string>().Length > max || (!empty && string.IsNullOrWhiteSpace(token.Value<string>())))
                throw new AddressablesException("invalid_addressables_request", name + " must be a bounded string.");
            return token.Value<string>();
        }

        internal static int Integer(JToken value, int fallback, int max)
        {
            if (value == null)
                return fallback;
            if (
                value.Type != JTokenType.Integer
                || !int.TryParse(value.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out int parsed)
                || parsed < 1
                || parsed > max
            )
                throw new AddressablesException("invalid_addressables_request", "Invalid strict page bound.");
            return parsed;
        }

        internal static List<object> List(object value, int max)
        {
            if (!(value is IEnumerable sequence))
                throw new AddressablesException("addressables_api_unsupported", "Expected a native collection.");
            var result = new List<object>();
            foreach (object item in sequence)
            {
                if (result.Count >= max)
                    Fail("addressables_budget_exceeded", "Native collection exceeds its bounded scan.");
                result.Add(item);
            }
            return result;
        }

        private string String(object value, int max = 2048)
        {
            string result = value?.ToString() ?? "";
            if (result.Length > max || (characters += result.Length) > 1048576)
                Fail("addressables_budget_exceeded", "Settings text exceeds the snapshot budget.");
            return result;
        }

        internal static JObject Identity(UnityEngine.Object value)
        {
            if (value == null)
                return new JObject
                {
                    ["guid"] = "",
                    ["local_id"] = "",
                    ["instance_id"] = 0,
                };
            AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long id);
            return new JObject
            {
                ["guid"] = guid ?? "",
                ["local_id"] = id.ToString(CultureInfo.InvariantCulture),
                ["instance_id"] = value.GetInstanceIDCompat(),
            };
        }

        internal static string Path(JToken token)
        {
            string path = Text(token, "settings_path");
            if (
                !path.StartsWith("Assets/", StringComparison.Ordinal)
                || path.Contains("\\")
                || path.Split('/').Any(p => p == "" || p == "." || p == "..")
                || !path.EndsWith(".asset", StringComparison.OrdinalIgnoreCase)
            )
                Fail("invalid_addressables_request", "An explicit contained existing project settings asset is required.");
            return path;
        }

        internal static void Validate(JObject request, string action)
        {
            var allowed = new List<string> { "action", "page_number", "page_size", "expected_revision" };
            Integer(request["page_number"], 1, 1000000);
            Integer(request["page_size"], 50, 100);
            if (request.Property("expected_revision") != null)
            {
                string revision = Text(request["expected_revision"], "expected_revision", 80);
                string prefix = action == "analyze_layout" ? "addr-layout1:" : "addr1:";
                if (!System.Text.RegularExpressions.Regex.IsMatch(revision, @"\A" + prefix + @"[0-9a-f]{64}\z"))
                    Fail("invalid_addressables_request", "Expected revision has an invalid format or scope.");
            }
            switch (action)
            {
                case "inspect":
                    allowed.AddRange(new[] { "settings_path", "section" });
                    break;
                case "preflight":
                    allowed.Add("settings_path");
                    break;
                case "set_address":
                    allowed.AddRange(new[] { "settings_path", "entry_guid", "address" });
                    break;
                case "move_entry":
                    allowed.AddRange(new[] { "settings_path", "entry_guid", "group_guid" });
                    break;
                case "set_profile_value":
                    allowed.AddRange(new[] { "settings_path", "profile_id", "variable_name", "value" });
                    break;
                case "analyze_layout":
                    allowed.Add("layout_path");
                    break;
                default:
                    Fail("invalid_addressables_action", "Unknown action; no content build action exists.");
                    break;
            }
            if (request.Properties().Any(p => !allowed.Contains(p.Name)))
                Fail("invalid_addressables_request", "A supplied field does not apply to this action.");
        }

        internal static void RequirePackage(out Type type, out string version)
        {
            type = Type.GetType("UnityEditor.AddressableAssets.Settings.AddressableAssetSettings, Unity.Addressables.Editor");
            if (type == null)
                throw new AddressablesException("addressables_unavailable", "Addressables package is not installed.");
            version = UnityEditor.PackageManager.PackageInfo.FindForAssembly(type.Assembly)?.version;
            if (!AddressablesCompatibility.SupportsVersion(version))
                Fail(
                    "addressables_version_unsupported",
                    "This adapter requires stable Addressables 1.28+, 2.x, 3.1+ or 4.1.1+, compatible action APIs and stored profile schema 1."
                );
        }

        internal static AddressablesState Capture(JObject request)
        {
            if (Encoding.UTF8.GetByteCount(request.ToString(Formatting.None)) > 262144)
                Fail("addressables_budget_exceeded", "Request exceeds256KiB.");
            RequirePackage(out Type type, out string version);
            string path = Path(request["settings_path"]);
            UnityEngine.Object settings = AssetDatabase.LoadAssetAtPath(path, type);
            if (settings == null)
                Fail("addressables_settings_not_found", "Explicit Addressables settings asset was not found; no defaults were created.");
            var state = new AddressablesState { Settings = settings, Version = version };
            state.Document = new JObject
            {
                ["settings"] = Identity(settings),
                ["package_version"] = version,
                ["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString(),
                ["active_profile_id"] = state.String(Get(settings, "m_ActiveProfileId"), 64),
                ["groups"] = new JArray(),
                ["entries"] = new JArray(),
                ["profiles"] = new JArray(),
                ["variables"] = new JArray(),
            };
            foreach (object group in List(Get(settings, "groups"), 128).Where(g => g != null))
            {
                string groupId = state.String(Get(group, "Guid"), 64);
                if (state.Groups.ContainsKey(groupId))
                    Fail("addressables_settings_invalid", "Duplicate native group identity.");
                state.Groups.Add(groupId, group);
                var schemas = new JArray();
                foreach (object schema in List(Get(group, "Schemas"), 32).Where(s => s != null))
                {
                    var row = new JObject { ["type"] = schema.GetType().FullName, ["asset"] = Identity(schema as UnityEngine.Object) };
                    if (schema.GetType().FullName == "UnityEditor.AddressableAssets.Settings.GroupSchemas.BundledAssetGroupSchema")
                    {
                        row["build_path_id"] = state.String(Get(Get(schema, "BuildPath"), "Id"), 64);
                        row["load_path_id"] = state.String(Get(Get(schema, "LoadPath"), "Id"), 64);
                    }
                    schemas.Add(row);
                }
                bool builtin = schemas.Any(s => s.Value<string>("type") == "UnityEditor.AddressableAssets.Settings.GroupSchemas.PlayerDataGroupSchema");
                var groupRow = new JObject
                {
                    ["guid"] = groupId,
                    ["name"] = state.String(Get(group, "Name"), 512),
                    ["asset"] = Identity(group as UnityEngine.Object),
                    ["read_only"] = (bool)Get(group, "ReadOnly"),
                    ["schemas"] = schemas,
                    ["entry_guids"] = new JArray(),
                };
                ((JArray)state.Document["groups"]).Add(groupRow);
                foreach (object entry in List(Get(group, "entries"), 4096))
                {
                    if (state.Entries.Count >= 4096)
                        Fail("addressables_budget_exceeded", "Settings exceed4096 explicit entries.");
                    string guid = state.String(Get(entry, "guid"), 64);
                    if (state.Entries.ContainsKey(guid))
                        Fail("addressables_settings_invalid", "Duplicate entry GUID identity.");
                    state.Entries.Add(guid, entry);
                    string assetPath = state.String(AssetDatabase.GUIDToAssetPath(guid), 512);
                    var labels = List(Get(entry, "labels"), 64).Select(x => state.String(x, 128)).OrderBy(x => x, StringComparer.Ordinal).ToArray();
                    ((JArray)state.Document["entries"]).Add(
                        new JObject
                        {
                            ["guid"] = guid,
                            ["group_guid"] = groupId,
                            ["address"] = state.String(Get(entry, "address"), 512),
                            ["path"] = assetPath,
                            ["labels"] = new JArray(labels),
                            ["read_only"] = (bool)Get(entry, "ReadOnly"),
                            ["builtin"] = builtin,
                            ["folder"] = !string.IsNullOrEmpty(assetPath) && AssetDatabase.IsValidFolder(assetPath),
                            ["dependency_hash"] = string.IsNullOrEmpty(assetPath) || builtin ? "" : AssetDatabase.GetAssetDependencyHash(assetPath).ToString(),
                        }
                    );
                    ((JArray)groupRow["entry_guids"]).Add(guid);
                }
                groupRow["entry_guids"] = new JArray(((JArray)groupRow["entry_guids"]).Values<string>().OrderBy(x => x, StringComparer.Ordinal));
            }
            state.Document["labels"] = new JArray(
                List(AddressablesCompatibility.RequireMethod(settings, "inspect", "GetLabels", typeof(List<string>)).Invoke(settings, null), 256)
                    .Select(x => state.String(x, 128))
                    .OrderBy(x => x, StringComparer.Ordinal)
            );
            state.ProfilesObject = Get(settings, "m_ProfileSettings");
            // Public profile getters can create defaults or migrate data. Both supported families store schema 1.
            if (state.ProfilesObject == null)
                Fail("addressables_profile_schema_unsupported", "Stored profile settings are missing; no defaults were created.");
            FieldInfo profileVersion = AddressablesCompatibility.RequireField(state.ProfilesObject.GetType(), "inspect", "m_ProfileVersion", typeof(int), true);
            if ((int)profileVersion.GetValue(state.ProfilesObject) != 1)
                Fail("addressables_profile_schema_unsupported", "Stored profiles need an explicit package migration outside this read operation.");
            foreach (object variable in List(Get(state.ProfilesObject, "m_ProfileEntryNames"), 64))
                ((JArray)state.Document["variables"]).Add(
                    new JObject { ["id"] = state.String(Get(variable, "m_Id"), 64), ["name"] = state.String(Get(variable, "m_Name"), 128) }
                );
            foreach (object profile in List(Get(state.ProfilesObject, "m_Profiles"), 32))
            {
                var values = new JObject();
                foreach (object item in List(Get(profile, "m_Values"), 64))
                {
                    string id = state.String(Get(item, "m_Id"), 64);
                    if (values.Property(id) != null)
                        Fail("addressables_settings_invalid", "Duplicate stored profile value identity.");
                    values[id] = state.String(Get(item, "m_Value"));
                }
                ((JArray)state.Document["profiles"]).Add(
                    new JObject
                    {
                        ["id"] = state.String(Get(profile, "m_Id"), 64),
                        ["name"] = state.String(Get(profile, "m_ProfileName"), 128),
                        ["parent"] = state.String(Get(profile, "m_InheritedParent"), 64),
                        ["values"] = values,
                    }
                );
            }
            foreach (string collection in new[] { "groups", "entries", "profiles", "variables" })
            {
                string key = collection == "profiles" || collection == "variables" ? "id" : "guid";
                state.Document[collection] = new JArray(((JArray)state.Document[collection]).OrderBy(t => t.Value<string>(key), StringComparer.Ordinal));
            }
            string json = state.Document.ToString(Formatting.None);
            if (Encoding.UTF8.GetByteCount(json) > 8388608)
                Fail("addressables_budget_exceeded", "Settings snapshot exceeds8MiB.");
            state.Revision = Hash(json, "addr1:");
            return state;
        }

        internal static string Hash(string value, string prefix)
        {
            using (var sha = SHA256.Create())
                return prefix + BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant();
        }

        internal string ProfileValue(string profileId, string variableId, HashSet<string> visited = null)
        {
            visited = visited ?? new HashSet<string>(StringComparer.Ordinal);
            if (!visited.Add(profileId) || visited.Count > 32)
                return null;
            JObject profile = ((JArray)Document["profiles"]).OfType<JObject>().SingleOrDefault(p => p.Value<string>("id") == profileId);
            if (profile == null)
                return null;
            var values = (JObject)profile["values"];
            if (values.Property(variableId) != null)
                return values.Value<string>(variableId);
            return ProfileValue(profile.Value<string>("parent"), variableId, visited);
        }

        internal JArray Rows(string section)
        {
            if (section == "groups" || section == "entries")
                return (JArray)Document[section].DeepClone();
            if (section != "profiles")
                throw new AddressablesException("invalid_addressables_request", "section must be groups, entries or profiles.");
            var rows = new JArray();
            foreach (JObject profile in (JArray)Document["profiles"])
            foreach (JObject variable in (JArray)Document["variables"])
                rows.Add(
                    new JObject
                    {
                        ["profile_id"] = profile["id"],
                        ["profile_name"] = profile["name"],
                        ["variable_id"] = variable["id"],
                        ["variable_name"] = variable["name"],
                        ["value"] = ProfileValue(profile.Value<string>("id"), variable.Value<string>("id")),
                        ["raw_value"] = profile["values"][variable.Value<string>("id")]?.DeepClone(),
                        ["inherited_profile_id"] = profile["parent"],
                    }
                );
            return rows;
        }

        internal object Page(JObject request, JArray rows)
        {
            string expected = request.Property("expected_revision") == null ? null : Text(request["expected_revision"], "expected_revision", 80);
            int page = Integer(request["page_number"], 1, 1000000),
                size = Integer(request["page_size"], 50, 100);
            if (page > 1 && expected == null)
                Fail("invalid_addressables_request", "Continuation pages require expected_revision.");
            if (expected != null && expected != Revision)
                Fail("stale_addressables_revision", "Settings changed; restart inspection.");
            long start = (long)(page - 1) * size;
            var data = new JObject
            {
                ["revision"] = Revision,
                ["package_version"] = Version,
                ["build_target"] = Document["build_target"],
                ["settings_identity"] = Document["settings"].DeepClone(),
                ["total"] = rows.Count,
                ["page_number"] = page,
                ["page_size"] = size,
                ["items"] = new JArray(rows.Skip((int)Math.Min(start, rows.Count)).Take(size)),
                ["has_more"] = start + size < rows.Count,
                ["content_build_started"] = false,
                ["limitations"] = new JArray(
                    "Explicit entries only; folder descendants are not expanded. Reference scanning is bounded to loaded main assets and prefab components.",
                    "Profile tokens are inspected syntactically; arbitrary static properties and runtime placeholders are not evaluated. No content build or remote deployment is performed."
                ),
            };
            return Bounded(new SuccessResponse("Addressables inspection", data));
        }

        internal static object Bounded(object value)
        {
            if (Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(value)) > 262144)
                Fail("addressables_budget_exceeded", "Response exceeds256KiB; request a smaller page.");
            return value;
        }
    }
}
