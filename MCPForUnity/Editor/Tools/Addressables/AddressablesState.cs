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
        private static Type checkedSettings;
        private static Func<object, object> readLabels;

        internal static void RequireSnapshot(Type settings, string action)
        {
            if (settings == checkedSettings && settings != null)
                return;
            const string ns = "UnityEditor.AddressableAssets.Settings.";
            Type Native(string name) =>
                settings.Assembly.GetType(ns + name) ?? throw new AddressablesException("addressables_api_unsupported", action + " requires " + name + ".");
            Type group = Native("AddressableAssetGroup"),
                entry = Native("AddressableAssetEntry"),
                schema = Native("AddressableAssetGroupSchema");
            Type profiles = Native("AddressableAssetProfileSettings"),
                profile = Native("AddressableAssetProfileSettings+BuildProfile"),
                variable = Native("AddressableAssetProfileSettings+ProfileIdData"),
                value = Native("AddressableAssetProfileSettings+BuildProfile+ProfileEntry");
            void Stored(Type owner, string name, Type expected) => RequireField(owner, action, name, expected, true);
            void Property(Type owner, string name, Type expected) => RequireProperty(owner, action, name, expected);
            Stored(settings, "m_ActiveProfileId", typeof(string));
            Stored(settings, "m_ProfileSettings", profiles);
            Property(settings, "groups", typeof(IEnumerable<>).MakeGenericType(group));
            Stored(group, "m_GUID", typeof(string));
            Stored(group, "m_GroupName", typeof(string));
            Stored(group, "m_Settings", settings);
            Property(group, "Schemas", typeof(IEnumerable<>).MakeGenericType(schema));
            Property(group, "ReadOnly", typeof(bool));
            Property(group, "entries", typeof(IEnumerable<>).MakeGenericType(entry));
            Property(entry, "parentGroup", group);
            Property(entry, "guid", typeof(string));
            Property(entry, "address", typeof(string));
            Property(entry, "ReadOnly", typeof(bool));
            Property(entry, "labels", typeof(IEnumerable<string>));
            Stored(profiles, "m_ProfileVersion", typeof(int));
            Stored(profiles, "m_Profiles", typeof(List<>).MakeGenericType(profile));
            Stored(profiles, "m_ProfileEntryNames", typeof(List<>).MakeGenericType(variable));
            Stored(profile, "m_Id", typeof(string));
            Stored(profile, "m_ProfileName", typeof(string));
            Stored(profile, "m_InheritedParent", typeof(string));
            Stored(profile, "m_Values", typeof(List<>).MakeGenericType(value));
            Stored(variable, "m_Id", typeof(string));
            Stored(variable, "m_Name", typeof(string));
            Stored(value, "m_Id", typeof(string));
            Stored(value, "m_Value", typeof(string));
            readLabels = LabelReader(settings, action);
            checkedSettings = settings;
        }

        internal static void RequireProperty(Type owner, string action, string name, Type expected)
        {
            PropertyInfo property = owner.GetProperty(name, BindingFlags.Instance | BindingFlags.Public);
            if (
                expected == null
                || property == null
                || !expected.IsAssignableFrom(property.PropertyType)
                || property.GetGetMethod() == null
                || property.GetIndexParameters().Length != 0
            )
                throw new AddressablesException("addressables_api_unsupported", action + " requires the native property: " + name + ".");
        }

        internal static MethodInfo RequireMethod(object target, string action, string name, Type result, params Type[] parameters)
        {
            Type owner = target as Type ?? target?.GetType();
            MethodInfo[] methods = owner
                ?.GetMethods(BindingFlags.Instance | BindingFlags.Public)
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

        private static Func<object, object> LabelReader(Type settings, string action)
        {
            if (settings.GetMethods(BindingFlags.Instance | BindingFlags.Public).Any(m => m.Name == "GetLabels"))
            {
                var getter = RequireMethod(settings, action, "GetLabels", typeof(List<string>));
                return owner => getter.Invoke(owner, null);
            }
            Type table = settings.Assembly.GetType("UnityEditor.AddressableAssets.Settings.LabelTable");
            var stored = RequireField(settings, action, "m_LabelTable", table, true);
            var names = RequireField(table, action, "m_LabelNames", typeof(List<string>), true);
            return owner =>
                names.GetValue(
                    stored.GetValue(owner) ?? throw new AddressablesException("addressables_settings_invalid", "Stored Addressables labels are missing.")
                );
        }

        internal static object Labels(object settings, string action)
        {
            var reader = settings.GetType() == checkedSettings ? readLabels : LabelReader(settings.GetType(), action);
            return reader(settings);
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
        internal JObject PreflightCoverage;

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
            if (request.Property("include_preflight") != null)
            {
                if (
                    request["include_preflight"].Type != JTokenType.Boolean
                    || (action != "set_address" && action != "move_entry" && action != "set_profile_value")
                )
                    Fail("invalid_addressables_request", "include_preflight is a boolean for metadata edits only.");
                allowed.Add("include_preflight");
            }
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
            var provider = UnityEditor.PackageManager.PackageInfo.FindForAssembly(type.Assembly);
            if (provider?.name != "com.unity.addressables" || !typeof(ScriptableObject).IsAssignableFrom(type))
                Fail("addressables_api_unsupported", "The settings type must be provided by com.unity.addressables.");
            version = provider.version;
        }

        internal static AddressablesState Capture(JObject request)
        {
            string action = request.Value<string>("action") ?? "inspect";
            try
            {
                return CaptureCore(request, action);
            }
            catch (AddressablesException error) when (error.Code == "addressables_api_unsupported" || error.Code == "addressables_profile_schema_unsupported")
            {
                throw new AddressablesException(
                    error.Code,
                    error.Message.StartsWith(action + " ", StringComparison.Ordinal) ? error.Message : action + ": " + error.Message
                );
            }
        }

        private static AddressablesState CaptureCore(JObject request, string action)
        {
            if (Encoding.UTF8.GetByteCount(request.ToString(Formatting.None)) > 262144)
                Fail("addressables_budget_exceeded", "Request exceeds256KiB.");
            RequirePackage(out Type type, out string version);
            AddressablesCapabilities.Require(type, action);
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
                if (Get(group, "m_Settings") as UnityEngine.Object != settings)
                    Fail("addressables_settings_invalid", "A listed group does not belong to the selected settings.");
                // The public Guid and Name getters can write defaults into persisted groups.
                string groupId = state.String(Get(group, "m_GUID"), 64);
                if (groupId.Length == 0)
                    Fail("addressables_settings_invalid", "A listed group has no stored identity.");
                string groupName = state.String(Get(group, "m_GroupName"), 512);
                if (state.Groups.ContainsKey(groupId))
                    Fail("addressables_settings_invalid", "Duplicate native group identity.");
                state.Groups.Add(groupId, group);
                var schemas = new JArray();
                foreach (object schema in List(Get(group, "Schemas"), 32).Where(s => s != null))
                {
                    var row = new JObject { ["type"] = schema.GetType().FullName, ["asset"] = Identity(schema as UnityEngine.Object) };
                    if (schema.GetType() == type.Assembly.GetType("UnityEditor.AddressableAssets.Settings.GroupSchemas.BundledAssetGroupSchema"))
                    {
                        Type reference = type.Assembly.GetType("UnityEditor.AddressableAssets.Settings.ProfileValueReference");
                        AddressablesCompatibility.RequireProperty(schema.GetType(), action, "BuildPath", reference);
                        AddressablesCompatibility.RequireProperty(schema.GetType(), action, "LoadPath", reference);
                        AddressablesCompatibility.RequireProperty(reference, action, "Id", typeof(string));
                        row["build_path_id"] = state.String(Get(Get(schema, "BuildPath"), "Id"), 64);
                        row["load_path_id"] = state.String(Get(Get(schema, "LoadPath"), "Id"), 64);
                    }
                    schemas.Add(row);
                }
                bool builtin = schemas.Any(s => s.Value<string>("type") == "UnityEditor.AddressableAssets.Settings.GroupSchemas.PlayerDataGroupSchema");
                var groupRow = new JObject
                {
                    ["guid"] = groupId,
                    ["name"] = groupName.Length == 0 ? groupId : groupName,
                    ["asset"] = Identity(group as UnityEngine.Object),
                    ["read_only"] = (bool)Get(group, "ReadOnly"),
                    ["schemas"] = schemas,
                    ["entry_guids"] = new JArray(),
                };
                ((JArray)state.Document["groups"]).Add(groupRow);
                foreach (object entry in List(Get(group, "entries"), 4096))
                {
                    if (!ReferenceEquals(Get(entry, "parentGroup"), group))
                        Fail("addressables_settings_invalid", "An explicit entry does not belong to its listed group.");
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
                List(AddressablesCompatibility.Labels(settings, action), 256).Select(x => state.String(x, 128)).OrderBy(x => x, StringComparer.Ordinal)
            );
            state.ProfilesObject = Get(settings, "m_ProfileSettings");
            // Public profile getters can create defaults or migrate data. Compatible snapshots require stored schema 1.
            if (state.ProfilesObject == null)
                Fail("addressables_profile_schema_unsupported", "Stored profile settings are missing; no defaults were created.");
            FieldInfo profileVersion = AddressablesCompatibility.RequireField(state.ProfilesObject.GetType(), action, "m_ProfileVersion", typeof(int), true);
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
            if (PreflightCoverage != null)
                data["reference_scan_coverage"] = PreflightCoverage.DeepClone();
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
