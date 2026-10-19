using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.Localization
{
    // The optional package is accessed only through these fixed, public API members.
    // Keeping the adapter in the core assembly avoids a mandatory package dependency.
    [McpForUnityTool("manage_localization", AutoRegister = false, Group = "core")]
    public static class ManageLocalization
    {
        private const int MaxKeys = 4096;
        private const int MaxBytes = 1048576;

        private static Type Kind(string name) =>
            AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType("UnityEngine.Localization." + name)).FirstOrDefault(t => t != null);

        private static object GetNullable(object target, string name)
        {
            PropertyInfo property = target.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
            if (property?.GetMethod == null || !property.GetMethod.IsPublic)
                throw new InvalidOperationException("unsupported_package_api: missing readable " + name);
            return property.GetValue(target);
        }

        private static object Get(object target, string name) =>
            GetNullable(target, name) ?? throw new InvalidOperationException("invalid_localization_data: required " + name + " is null.");

        private static void Set(object target, string name, object value)
        {
            PropertyInfo property = target.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
            if (property?.SetMethod == null || !property.SetMethod.IsPublic)
                throw new InvalidOperationException("unsupported_package_api: unwritable " + name);
            property.SetValue(target, value);
        }

        private static object Call(object target, string name, Type[] signature, params object[] args)
        {
            MethodInfo method = target.GetType().GetMethod(name, signature);
            if (method == null)
                throw new InvalidOperationException("unsupported_package_api: missing " + name);
            return method.Invoke(target, args);
        }

        private static string Text(JObject p, string name, int maximum = 512)
        {
            JToken token = p[name];
            if (token?.Type != JTokenType.String || token.Value<string>().Length > maximum)
                throw new ArgumentException(name + " must be a bounded string.");
            return token.Value<string>();
        }

        private static string PathValue(string path)
        {
            if (
                path == null
                || path.Length > 512
                || !path.StartsWith("Assets/", StringComparison.Ordinal)
                || path.Contains("\\")
                || path.Split('/').Any(s => s == "" || s == "." || s == "..")
                || path.Any(c => c < 32 || c == ':')
            )
                throw new ArgumentException("Asset paths must be canonical paths under Assets/.");
            // Reuse the repository's containment validation before touching source bytes.
            AssetPathUtility.GetFullAssetPath(path);
            return path;
        }

        private static string[] Paths(JObject p, string name, int min, int max)
        {
            if (!(p[name] is JArray array) || array.Count < min || array.Count > max || array.Any(t => t.Type != JTokenType.String))
                throw new ArgumentException(name + " must be an explicit bounded array of asset paths.");
            string[] paths = array.Select(t => PathValue(t.Value<string>())).ToArray();
            if (paths.Distinct(StringComparer.Ordinal).Count() != paths.Length)
                throw new ArgumentException(name + " must not contain duplicate paths.");
            return paths;
        }

        private static Object Load(string path, string kind)
        {
            Type type = Kind(kind) ?? throw new InvalidOperationException("localization_package_unavailable: com.unity.localization is required.");
            Object obj = AssetDatabase.LoadAssetAtPath(PathValue(path), type);
            if (obj == null || AssetDatabase.GetAssetPath(obj) != path || AssetDatabase.IsSubAsset(obj))
                throw new ArgumentException("Expected one persistent main " + kind + " asset at " + path);
            return obj;
        }

        private static Object Table(string path)
        {
            Object obj = Load(path, "Tables.LocalizationTable");
            if (!Kind("Tables.StringTable").IsInstanceOfType(obj) && !Kind("Tables.AssetTable").IsInstanceOfType(obj))
                throw new ArgumentException("Only StringTable and AssetTable are supported.");
            if (Convert.ToInt32(Get(obj, "Count"), CultureInfo.InvariantCulture) > MaxKeys)
                throw new ArgumentException("table_limit: table exceeds 4096 entries.");
            SharedEntries(obj);
            return obj;
        }

        private static object[] SharedEntries(Object table)
        {
            Object shared = (Object)Get(table, "SharedData");
            if (!EditorUtility.IsPersistent(shared))
                throw new ArgumentException("SharedTableData must be a persistent asset.");
            object[] entries = ((IEnumerable)Get(shared, "Entries")).Cast<object>().Take(MaxKeys + 1).ToArray();
            if (entries.Length > MaxKeys)
                throw new ArgumentException("table_limit: shared data exceeds 4096 keys.");
            return entries;
        }

        private static string Code(Object obj, bool locale) => (string)Get(Get(obj, locale ? "Identifier" : "LocaleIdentifier"), "Code");

        private static string Serialize(Object obj)
        {
            if (obj is ISerializationCallbackReceiver callback)
                callback.OnBeforeSerialize();
            string json = EditorJsonUtility.ToJson(obj);
            if (Encoding.UTF8.GetByteCount(json) > MaxBytes)
                throw new ArgumentException("asset_limit: serialized asset exceeds 1 MiB.");
            return json;
        }

        private static string Revision(Object obj, Object shared = null)
        {
            var input = new StringBuilder();
            foreach (Object asset in shared == null ? new[] { obj } : new[] { obj, shared })
            {
                string path = AssetDatabase.GetAssetPath(asset);
                string full = AssetPathUtility.GetFullAssetPath(PathValue(path));
                if (!File.Exists(full) || new FileInfo(full).Length > MaxBytes)
                    throw new ArgumentException("asset_limit: source asset must exist and be at most 1 MiB.");
                input.Append(AssetDatabase.AssetPathToGUID(path)).Append('\n').Append(Serialize(asset)).Append('\n');
                using (var stream = new FileStream(full, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    if (stream.Length > MaxBytes)
                        throw new ArgumentException("asset_limit: source asset exceeds 1 MiB.");
                    var bytes = new byte[(int)stream.Length];
                    int read = 0;
                    while (read < bytes.Length)
                    {
                        int count = stream.Read(bytes, read, bytes.Length - read);
                        if (count == 0)
                            throw new IOException("source_changed: source was truncated while reading.");
                        read += count;
                    }
                    if (stream.ReadByte() != -1)
                        throw new IOException("source_changed: source grew while reading.");
                    input.Append(Convert.ToBase64String(bytes)).Append('\n');
                }
            }
            using (var hash = SHA256.Create())
                return string.Concat(hash.ComputeHash(Encoding.UTF8.GetBytes(input.ToString())).Select(b => b.ToString("x2", CultureInfo.InvariantCulture)));
        }

        private static void Expected(JObject p, Object obj, Object shared = null)
        {
            string expected = Text(p, "expected_revision", 64);
            if (expected.Length != 64 || expected.Any(c => !(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f')))
                throw new ArgumentException("expected_revision must be a lowercase SHA256 revision from inspection.");
            if (!string.Equals(expected, Revision(obj, shared), StringComparison.Ordinal))
                throw new InvalidOperationException("stale_revision: asset or shared data changed; inspect again.");
            if (EditorUtility.IsDirty(obj) || (shared != null && EditorUtility.IsDirty(shared)))
                throw new InvalidOperationException("dirty_asset: save or discard existing Inspector edits before a correction.");
        }

        private static int Page(JObject p, string name, int fallback, int min, int max)
        {
            if (p[name] == null)
                return fallback;
            if (p[name].Type != JTokenType.Integer || !long.TryParse(p[name].ToString(), out long value) || value < min || value > max)
                throw new ArgumentException(name + " is outside its integer bounds.");
            return (int)value;
        }

        private static void Fields(JObject p, string action)
        {
            string names;
            switch (action)
            {
                case "capabilities":
                    names = "";
                    break;
                case "list_locales":
                case "list_tables":
                    names = "paths offset limit";
                    break;
                case "inspect":
                    names = "table_paths argument_names";
                    break;
                case "set_string_entry":
                    names = "table_path key_id value is_smart expected_revision";
                    break;
                case "set_asset_entry":
                    names = "table_path key_id asset_path expected_revision";
                    break;
                case "set_fallbacks":
                    names = "locale_path fallback_locale_paths expected_revision";
                    break;
                default:
                    throw new ArgumentException("Unknown Localization action.");
            }
            var allowed = new HashSet<string>(names.Split(' ')) { "action" };
            if (p.Properties().Any(prop => !allowed.Contains(prop.Name) || prop.Value.Type == JTokenType.Null))
                throw new ArgumentException("Unsupported or null field for Localization action.");
        }

        public static object HandleCommand(JObject p)
        {
            try
            {
                if (p == null)
                    throw new ArgumentException("Localization parameters are required.");
                string action = Text(p, "action", 64);
                Fields(p, action);
                Type locale = Kind("Locale");
                if (action == "capabilities")
                    return new SuccessResponse(
                        "Localization capabilities.",
                        new
                        {
                            package_available = locale != null,
                            package_name = "com.unity.localization",
                            package_version = locale == null ? null : UnityEditor.PackageManager.PackageInfo.FindForAssembly(locale.Assembly)?.version,
                            asset_entry_authoring_supported = false,
                            smart_validation = "Default package parser and root selectors only; custom sources, formatters and runtime arguments are not evaluated.",
                            persistence = "Exact selected asset saved with native Undo; Undo persistence across reimport or Editor restart is not promised.",
                            concurrency = "Revision checks loaded serialization and source bytes. External process races are not excluded.",
                        }
                    );
                if (locale == null)
                    throw new InvalidOperationException(
                        "localization_package_unavailable: com.unity.localization is required; no package installed implicitly."
                    );
                switch (action)
                {
                    case "list_locales":
                        return List(p, true);
                    case "list_tables":
                        return List(p, false);
                    case "inspect":
                        return Inspect(p);
                    case "set_string_entry":
                        return SetString(p);
                    case "set_fallbacks":
                        return SetFallbacks(p);
                    case "set_asset_entry":
                        throw new InvalidOperationException(
                            "asset_authoring_requires_addressables: Localization's supported AssetTableCollection API also updates Addressables groups/labels and shared metadata. This bounded tool does not perform those changes."
                        );
                    default:
                        throw new ArgumentException("Unknown Localization action.");
                }
            }
            catch (Exception error)
            {
                while (error is TargetInvocationException && error.InnerException != null)
                    error = error.InnerException;
                return new ErrorResponse(error.Message);
            }
        }

        private static object List(JObject p, bool locales)
        {
            string[] paths;
            if (p["paths"] != null)
                paths = Paths(p, "paths", 1, 16);
            else
            {
                string[] ids = AssetDatabase.FindAssets(locales ? "t:Locale" : "t:LocalizationTable", new[] { "Assets" });
                if (ids.Length > MaxKeys)
                    throw new ArgumentException("discovery_limit: more than 4096 matching assets; supply exact paths.");
                paths = ids.Select(AssetDatabase.GUIDToAssetPath).OrderBy(s => s, StringComparer.Ordinal).ToArray();
            }
            int offset = Page(p, "offset", 0, 0, MaxKeys);
            int limit = Page(p, "limit", 50, 1, 100);
            var rows = new JArray();
            foreach (string path in paths.Skip(offset).Take(limit))
            {
                Object asset = locales ? Load(path, "Locale") : Table(path);
                JObject row = locales ? LocaleRow(asset) : TableRow(asset);
                rows.Add(row);
            }
            return new SuccessResponse(
                "Localization asset page.",
                new JObject
                {
                    [locales ? "locales" : "tables"] = rows,
                    ["total"] = paths.Length,
                    ["offset"] = offset,
                    ["next_offset"] = offset + rows.Count < paths.Length ? new JValue(offset + rows.Count) : JValue.CreateNull(),
                    ["scope"] = "Assets only; package or runtime availability is not inferred from asset discovery.",
                }
            );
        }

        private static object[] Fallbacks(Object locale) =>
            ((IEnumerable)Get(Get(locale, "Metadata"), "MetadataEntries"))
                .Cast<object>()
                .Where(m => m != null && m.GetType() == Kind("Metadata.FallbackLocale"))
                .Take(33)
                .ToArray();

        private static Object FallbackTarget(object metadata) => (Object)metadata.GetType().GetProperty("Locale").GetValue(metadata);

        private static JObject LocaleRow(Object locale)
        {
            object[] fallbacks = Fallbacks(locale);
            if (fallbacks.Length > 32)
                throw new ArgumentException("fallback_limit: more than 32 fallback metadata entries.");
            var findings = new JArray();
            foreach (object fallback in fallbacks)
                if (FallbackTarget(fallback) == null)
                    findings.Add(new JObject { ["code"] = "missing_fallback_reference" });
            if (HasCycle(locale, locale, new HashSet<int>(), 0, false))
                findings.Add(new JObject { ["code"] = "fallback_cycle" });
            return new JObject
            {
                ["path"] = AssetDatabase.GetAssetPath(locale),
                ["guid"] = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(locale)),
                ["code"] = Code(locale, true),
                ["revision"] = Revision(locale),
                ["fallback_locale_paths"] = new JArray(fallbacks.Select(f => FallbackTarget(f) == null ? null : AssetDatabase.GetAssetPath(FallbackTarget(f)))),
                ["findings"] = findings,
            };
        }

        private static JObject TableRow(Object table)
        {
            Object shared = (Object)Get(table, "SharedData");
            return new JObject
            {
                ["path"] = AssetDatabase.GetAssetPath(table),
                ["guid"] = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(table)),
                ["kind"] = Kind("Tables.StringTable").IsInstanceOfType(table) ? "string" : "asset",
                ["locale"] = Code(table, false),
                ["shared_data_path"] = AssetDatabase.GetAssetPath(shared),
                ["shared_data_guid"] = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(shared)),
                ["revision"] = Revision(table, shared),
            };
        }

        private static object Inspect(JObject p)
        {
            string[] paths = Paths(p, "table_paths", 1, 16);
            string[] arguments = null;
            if (p["argument_names"] != null)
            {
                if (
                    !(p["argument_names"] is JArray args)
                    || args.Count > 128
                    || args.Any(t => t.Type != JTokenType.String || t.Value<string>().Length < 1 || t.Value<string>().Length > 128)
                )
                    throw new ArgumentException("argument_names must contain at most 128 nonempty strings of at most 128 characters.");
                arguments = args.Values<string>().ToArray();
            }
            var tables = new JArray();
            var findings = new JArray();
            var localeIdentities = new HashSet<string>(StringComparer.Ordinal);
            int outputCount = 0;
            Action<string, string, string> finding = (code, path, key) =>
            {
                if (++outputCount > MaxKeys)
                    throw new ArgumentException("output_limit: more than 4096 findings; inspect fewer tables.");
                findings.Add(
                    new JObject
                    {
                        ["code"] = code,
                        ["path"] = path,
                        ["key_id"] = key,
                    }
                );
            };
            foreach (string path in paths)
            {
                Object table = Table(path);
                JObject row = TableRow(table);
                string identity = row.Value<string>("shared_data_guid") + "/" + row.Value<string>("locale") + "/" + row.Value<string>("kind");
                if (!localeIdentities.Add(identity))
                    finding("duplicate_locale_table", path, null);
                object[] keys = SharedEntries(table);
                foreach (var group in keys.GroupBy(k => Get(k, "Id").ToString()).Where(g => g.Count() > 1))
                    finding("duplicate_key_id", path, group.Key);
                foreach (var group in keys.GroupBy(k => (string)Get(k, "Key"), StringComparer.Ordinal).Where(g => g.Count() > 1))
                    finding("duplicate_key_name", path, Get(group.First(), "Id").ToString());
                var keyRows = new JArray();
                foreach (object key in keys)
                {
                    long id = (long)Get(key, "Id");
                    object entry = Call(table, "GetEntry", new[] { typeof(long) }, id);
                    string value = entry == null ? null : (string)GetNullable(entry, "LocalizedValue");
                    if (value != null && value.Length > 8192)
                        throw new ArgumentException("entry_limit: string value exceeds 8192 characters.");
                    if (++outputCount > MaxKeys)
                        throw new ArgumentException("output_limit: more than 4096 rows/findings; inspect fewer tables.");
                    var keyRow = new JObject
                    {
                        ["key_id"] = id.ToString(CultureInfo.InvariantCulture),
                        ["key"] = (string)Get(key, "Key"),
                        ["value"] = value == null ? JValue.CreateNull() : new JValue(value),
                    };
                    if (entry == null || string.IsNullOrEmpty(value))
                        finding("missing_translation", path, keyRow.Value<string>("key_id"));
                    else if (row.Value<string>("kind") == "string")
                    {
                        bool smart = (bool)Get(entry, "IsSmart");
                        keyRow["is_smart"] = smart;
                        if (smart)
                        {
                            try
                            {
                                string[] selectors = Selectors(value);
                                keyRow["root_selectors"] = new JArray(selectors);
                                keyRow["argument_validation"] = arguments == null ? "runtime_arguments_unverified" : "caller_inventory_only";
                                if (arguments != null && selectors.Any(s => !arguments.Contains(s, StringComparer.Ordinal)))
                                    finding("unresolved_argument", path, keyRow.Value<string>("key_id"));
                            }
                            catch (TargetInvocationException)
                            {
                                finding("smart_parse_error", path, keyRow.Value<string>("key_id"));
                            }
                        }
                    }
                    else
                    {
                        string guid = (string)Get(entry, "Guid");
                        string target = AssetDatabase.GUIDToAssetPath(guid);
                        keyRow["asset_guid"] = guid;
                        keyRow["asset_path"] = target;
                        keyRow["runtime_load_verified"] = false;
                        if (string.IsNullOrEmpty(target))
                            finding("missing_asset_reference", path, keyRow.Value<string>("key_id"));
                        if ((bool)Get(entry, "IsSubAsset"))
                            keyRow["subasset_validation"] = "not_evaluated";
                    }
                    keyRows.Add(keyRow);
                }
                var ids = new HashSet<long>(keys.Select(k => (long)Get(k, "Id")));
                foreach (object entry in (IEnumerable)Get(table, "Values"))
                    if (!ids.Contains((long)Get(entry, "KeyId")))
                        finding("orphan_entry", path, Get(entry, "KeyId").ToString());
                row["keys"] = keyRows;
                tables.Add(row);
            }
            return new SuccessResponse(
                "Localization table inspection.",
                new JObject
                {
                    ["tables"] = tables,
                    ["findings"] = findings,
                    ["smart_validation"] =
                        "Default package parser only. Root selectors are caller-supplied argument requirements; custom/global variables and formatter behavior remain unverified.",
                    ["asset_validation"] = "Local GUID existence only; Addressables labels, build inclusion, subassets and runtime load are not proven.",
                }
            );
        }

        private static string[] Selectors(string value)
        {
            // A fresh formatter configures the package parser without accessing LocalizationSettings
            // or executing any source/formatter extension or user-supplied runtime code.
            Type smart = Kind("SmartFormat.Smart");
            object formatter = smart.GetMethod("CreateDefaultSmartFormat", BindingFlags.Public | BindingFlags.Static).Invoke(null, null);
            object names = Call(formatter, "GetNotEmptyFormatterExtensionNames", Type.EmptyTypes);
            object parser = Get(formatter, "Parser");
            object parsed = Call(parser, "ParseFormat", new[] { typeof(string), typeof(IList<string>) }, value, names);
            var selectors = new HashSet<string>(StringComparer.Ordinal);
            try
            {
                CollectSelectors(parsed, selectors, 0);
                return selectors.OrderBy(s => s, StringComparer.Ordinal).ToArray();
            }
            finally
            {
                Call(parsed, "ReleaseToPool", Type.EmptyTypes);
            }
        }

        private static void CollectSelectors(object format, HashSet<string> selectors, int depth)
        {
            if (depth > 16)
                throw new ArgumentException("smart_limit: nested format depth exceeds 16.");
            foreach (object item in (IEnumerable)Get(format, "Items"))
            {
                if (item.GetType().Name != "Placeholder")
                    continue;
                object first = ((IEnumerable)Get(item, "Selectors")).Cast<object>().FirstOrDefault();
                if (first != null && depth == 0)
                    selectors.Add((string)Get(first, "RawText"));
                if (selectors.Count > 128)
                    throw new ArgumentException("smart_limit: more than 128 root selectors.");
                object nested = item.GetType().GetProperty("Format").GetValue(item);
                if (nested != null)
                    CollectSelectors(nested, selectors, depth + 1);
            }
        }

        private static object SetString(JObject p)
        {
            Object table = Table(Text(p, "table_path"));
            if (!Kind("Tables.StringTable").IsInstanceOfType(table))
                throw new ArgumentException("set_string_entry requires a StringTable.");
            Object shared = (Object)Get(table, "SharedData");
            string idText = Text(p, "key_id", 19);
            if (
                !long.TryParse(idText, NumberStyles.None, CultureInfo.InvariantCulture, out long id)
                || id <= 0
                || id.ToString(CultureInfo.InvariantCulture) != idText
            )
                throw new ArgumentException("key_id must be a positive Int64 decimal string.");
            object[] keys = SharedEntries(table);
            if (
                keys.Count(k => (long)Get(k, "Id") == id) != 1
                || keys.GroupBy(k => Get(k, "Id")).Any(g => g.Count() != 1)
                || keys.GroupBy(k => (string)Get(k, "Key"), StringComparer.Ordinal).Any(g => g.Count() != 1)
            )
                throw new ArgumentException("ambiguous_shared_key: corrections require existing unique shared IDs and names.");
            string value = Text(p, "value", 8192);
            if (p["is_smart"] != null && p["is_smart"].Type != JTokenType.Boolean)
                throw new ArgumentException("is_smart must be a JSON boolean when supplied.");
            object entry = Call(table, "GetEntry", new[] { typeof(long) }, id);
            bool isSmart = p["is_smart"] == null ? entry != null && (bool)Get(entry, "IsSmart") : p.Value<bool>("is_smart");
            if (isSmart)
                Selectors(value);
            Expected(p, table, shared);
            return Persist(
                table,
                () =>
                {
                    if (entry == null)
                        entry = Call(table, "AddEntry", new[] { typeof(long), typeof(string) }, id, value);
                    else
                        Set(entry, "Value", value);
                    if (p["is_smart"] != null)
                        Set(entry, "IsSmart", isSmart);
                },
                () => TableRow(table)
            );
        }

        private static bool HasCycle(Object current, Object target, HashSet<int> visited, int depth, bool followSelf)
        {
            return VisitFallback(current, target, visited, new HashSet<int>(), depth, followSelf);
        }

        private static bool VisitFallback(Object current, Object target, HashSet<int> active, HashSet<int> completed, int depth, bool followSelf)
        {
            if (current == target && followSelf)
                return true;
            if (depth > 32 || completed.Count + active.Count >= 256)
                throw new ArgumentException("fallback_limit: graph exceeds bounded 32 depth/256 locales.");
            int instance = current.GetInstanceIDCompat();
            if (completed.Contains(instance))
                return false;
            if (!active.Add(instance))
                return true;
            object[] links = Fallbacks(current);
            if (links.Length > 32)
                throw new ArgumentException("fallback_limit: too many fallback metadata entries.");
            foreach (object link in links)
            {
                Object next = FallbackTarget(link);
                if (next != null && VisitFallback(next, target, active, completed, depth + 1, true))
                    return true;
            }
            active.Remove(instance);
            completed.Add(instance);
            return false;
        }

        private static object SetFallbacks(JObject p)
        {
            Object locale = Load(Text(p, "locale_path"), "Locale");
            Object[] targets = Paths(p, "fallback_locale_paths", 0, 8).Select(path => Load(path, "Locale")).ToArray();
            foreach (Object target in targets)
                if (HasCycle(target, locale, new HashSet<int>(), 0, true))
                    throw new ArgumentException("fallback_cycle: requested fallbacks return to the selected locale.");
            object[] prior = Fallbacks(locale);
            if (prior.Length > 32)
                throw new ArgumentException("fallback_limit: too many existing fallback metadata entries.");
            // Construct and validate through the package's public constructor before changing metadata.
            Type fallbackType = Kind("Metadata.FallbackLocale");
            // Explicit argument array avoids binding Activator's (Type, bool) overload
            // through UnityEngine.Object's implicit bool conversion.
            object[] replacements = targets.Select(t => Activator.CreateInstance(fallbackType, new object[] { t })).ToArray();
            for (int index = 0; index < replacements.Length; index++)
                if (FallbackTarget(replacements[index]) != targets[index])
                    throw new InvalidOperationException("fallback_constructor_refused: package did not retain the requested fallback; no metadata changed.");
            Expected(p, locale);
            return Persist(
                locale,
                () =>
                {
                    var entries = (IList)Get(Get(locale, "Metadata"), "MetadataEntries");
                    foreach (object existing in prior)
                        entries.Remove(existing);
                    foreach (object replacement in replacements)
                        entries.Add(replacement);
                },
                () => LocaleRow(locale)
            );
        }

        private static object Persist(Object asset, Action apply, Func<JObject> readback)
        {
            string before = Serialize(asset);
            string path = AssetDatabase.GetAssetPath(asset);
            string guid = AssetDatabase.AssetPathToGUID(path);
            Undo.RegisterCompleteObjectUndo(asset, "Correct Localization asset");
            bool saveAttempted = false;
            try
            {
                apply();
                EditorUtility.SetDirty(asset);
                saveAttempted = true;
                AssetDatabase.SaveAssetIfDirty(asset);
                if (EditorUtility.IsDirty(asset) || AssetDatabase.AssetPathToGUID(path) != guid)
                    throw new InvalidOperationException("Selected asset save/identity verification failed.");
                return new SuccessResponse(
                    "Localization correction persisted.",
                    new JObject
                    {
                        ["persisted"] = true,
                        ["asset"] = readback(),
                        ["undo"] = "Native asset Undo recorded; disk persistence is not automatically undone.",
                        ["addressables_changed"] = false,
                    }
                );
            }
            catch (Exception error)
            {
                bool restored = false;
                string restoreError = null;
                try
                {
                    EditorJsonUtility.FromJsonOverwrite(before, asset);
                    if (asset is ISerializationCallbackReceiver callback)
                        callback.OnAfterDeserialize();
                    EditorUtility.SetDirty(asset);
                    if (saveAttempted)
                        AssetDatabase.SaveAssetIfDirty(asset);
                    restored = Serialize(asset) == before;
                }
                catch (Exception recovery)
                {
                    restoreError = recovery.Message;
                }
                return new ErrorResponse(
                    "Localization correction failed: " + (error.InnerException?.Message ?? error.Message),
                    new
                    {
                        restored_loaded_state = restored,
                        persistence_may_have_changed = saveAttempted,
                        recovery_error = restoreError,
                        detail = "No transaction or external-writer exclusion is promised. Inspect the selected asset before retrying.",
                    }
                );
            }
        }
    }
}
