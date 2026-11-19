using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Addressables
{
    internal static class AddressablesLayout
    {
        private sealed class ObjectBytes
        {
            internal ulong Serialized;
            internal ulong Streamed;
            internal string Path;
        }

        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

        private static Type Nested(Type layout, string name)
        {
            Type type = layout?.GetNestedType(name);
            if (type == null)
                AddressablesState.Fail("addressables_api_unsupported", "analyze_layout requires the native layout type: " + name + ".");
            return type;
        }

        private static void Field(Type type, string name, Type expected) => AddressablesCompatibility.RequireField(type, "analyze_layout", name, expected);

        private static void ListField(Type type, string name, Type element) => Field(type, name, typeof(List<>).MakeGenericType(element));

        internal static void RequireSchema(Type layout)
        {
            // Check even empty reports: native constructors would otherwise hide missing fields behind empty defaults.
            Type asset = Nested(layout, "AssetDuplicationData"),
                duplicate = Nested(layout, "ObjectDuplicationData"),
                file = Nested(layout, "File"),
                bundle = Nested(layout, "Bundle"),
                implicitAsset = Nested(layout, "DataFromOtherAsset"),
                item = Nested(layout, "ObjectData");
            if (layout.GetConstructor(Type.EmptyTypes) == null)
                AddressablesState.Fail("addressables_api_unsupported", "analyze_layout requires a native layout constructor.");
            Field(layout, "PackageVersion", typeof(string));
            Field(layout, "BuildTarget", typeof(BuildTarget));
            Field(layout, "UnityVersion", typeof(string));
            Field(layout, "BuildError", typeof(string));
            ListField(layout, "Groups", Nested(layout, "Group"));
            ListField(layout, "DuplicatedAssets", asset);
            Field(asset, "AssetGuid", typeof(string));
            ListField(asset, "DuplicatedObjects", duplicate);
            Field(duplicate, "LocalIdentifierInFile", typeof(long));
            ListField(duplicate, "IncludedInBundleFiles", file);
            Field(file, "Bundle", bundle);
            Field(file, "Name", typeof(string));
            ListField(file, "OtherAssets", implicitAsset);
            Field(bundle, "Name", typeof(string));
            Field(implicitAsset, "AssetGuid", typeof(string));
            Field(implicitAsset, "AssetPath", typeof(string));
            ListField(implicitAsset, "Objects", item);
            Field(item, "LocalIdentifierInFile", typeof(long));
            Field(item, "SerializedSize", typeof(ulong));
            Field(item, "StreamedSize", typeof(ulong));
        }

        internal static bool UsesContentDirectories(Type layout)
        {
            Type group = Nested(layout, "Group");
            const BindingFlags fields = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
            if (
                layout.GetField("ContentDirectories", fields) == null
                && layout.GetNestedType("ContentDirectory", BindingFlags.Public | BindingFlags.NonPublic) == null
                && group.GetField("ContentDirectoryName", fields) == null
            )
                return false;
            // An optional extension is compatible only when its entire consumed schema is exact.
            RequireModernSchema(layout);
            return true;
        }

        internal static void RequireModernSchema(Type layout)
        {
            ListField(layout, "ContentDirectories", Nested(layout, "ContentDirectory"));
            Field(Nested(layout, "Group"), "ContentDirectoryName", typeof(string));
        }

        internal static void RequireSupportedReportShape(JObject report, bool modern)
        {
            if (!(report["DuplicatedAssets"] is JArray) || !(report["Groups"] is JArray))
                AddressablesState.Fail("addressables_layout_invalid", "Required native layout schema is missing.");
            JToken directories = report["ContentDirectories"];
            if ((modern || directories != null) && (!(directories is JArray items) || items.Count != 0))
                AddressablesState.Fail(
                    "addressables_layout_schema_unsupported",
                    "Content Directory layout analysis is unsupported; only an explicit empty ContentDirectories array is compatible."
                );
            // SerializeReference can place Group data in the references table.
            // Scan the already bounded JSON tree without materializing another copy.
            foreach (JProperty property in report.Descendants().OfType<JProperty>())
                if (
                    property.Name == "ContentDirectoryName"
                    && property.Value.Type != JTokenType.Null
                    && (property.Value.Type != JTokenType.String || property.Value.Value<string>().Length != 0)
                )
                    AddressablesState.Fail(
                        "addressables_layout_schema_unsupported",
                        "Content Directory group coverage is unsupported; ContentDirectoryName must be null or empty before legacy duplication analysis."
                    );
        }

        // EditorJsonUtility silently ignores unknown members and supplies defaults for missing ones.
        // Derive the wire fields from trusted empty native instances (Unity also omits unsupported CLR fields).
        // Inspect each managed-reference declaration and edge before giving the bytes to Unity.
        private sealed class WireSchema
        {
            private readonly Type layout;
            private readonly Dictionary<Type, JObject> samples = new Dictionary<Type, JObject>();
            private readonly Dictionary<long, (Type Type, JObject Data)> references = new Dictionary<long, (Type, JObject)>();
            private readonly HashSet<long> reached = new HashSet<long>();
            private readonly Queue<long> pending = new Queue<long>();
            private readonly Dictionary<(Type, string), FieldInfo> fields = new Dictionary<(Type, string), FieldInfo>();

            internal WireSchema(Type layout) => this.layout = layout;

            private static void Require(bool condition, string member)
            {
                if (!condition)
                    AddressablesState.Fail(
                        "addressables_layout_schema_unsupported",
                        "The report does not match the installed serialized layout at " + member + "."
                    );
            }

            private JObject Sample(Type type)
            {
                if (!samples.TryGetValue(type, out JObject sample))
                {
                    object instance =
                        type.IsValueType || type.GetConstructor(Type.EmptyTypes) != null
                            ? Activator.CreateInstance(type)
                            : FormatterServices.GetUninitializedObject(type);
                    sample = JObject.Parse(EditorJsonUtility.ToJson(instance));
                    samples.Add(type, sample);
                }
                return sample;
            }

            internal void Check(JObject report)
            {
                JToken nativeTable = Sample(layout)["references"];
                if (nativeTable != null || report["references"] != null)
                {
                    var table = report["references"] as JObject;
                    Require(
                        table?.Count == 2
                            && nativeTable?["version"]?.Type == JTokenType.Integer
                            && JToken.DeepEquals(nativeTable["version"], table["version"])
                            && table["RefIds"] is JArray,
                        "references"
                    );
                    var rows = (JArray)table["RefIds"];
                    var nativeTypes = layout.GetNestedTypes(BindingFlags.Public).ToDictionary(t => layout.Name + "/" + t.Name, StringComparer.Ordinal);
                    JToken nullRecord = ((JArray)nativeTable["RefIds"]).FirstOrDefault(r => r.Value<long>("rid") == -2);
                    bool hasNullRecord = false;
                    foreach (JToken row in rows)
                    {
                        Require(row is JObject, "references.RefIds");
                        long id = ReferenceId(row["rid"]);
                        if (id == -2 && nullRecord != null)
                        {
                            Require(!hasNullRecord && JToken.DeepEquals(row, nullRecord), "references.RefIds.null");
                            hasNullRecord = true;
                            continue;
                        }
                        Require(
                            row is JObject record
                                && record.Count == 3
                                && row["data"] is JObject
                                && row["type"] is JObject declaration
                                && declaration.Count == 3
                                && declaration["asm"]?.Type == JTokenType.String
                                && (string)declaration["asm"] == layout.Assembly.GetName().Name
                                && declaration["ns"]?.Type == JTokenType.String
                                && (string)declaration["ns"] == layout.Namespace
                                && declaration["class"]?.Type == JTokenType.String,
                            "references.RefIds.type"
                        );
                        Require(id >= 0 && !references.ContainsKey(id), "references.RefIds.rid");
                        Require(nativeTypes.TryGetValue((string)row["type"]["class"], out Type type) && type.IsSerializable, "references.RefIds.type.class");
                        references.Add(id, (type, (JObject)row["data"]));
                    }
                }
                CheckObject(report, layout);
                while (pending.Count != 0)
                {
                    var record = references[pending.Dequeue()];
                    CheckObject(record.Data, record.Type);
                }
                Require(reached.Count == references.Count, "unreachable references.RefIds");
            }

            private static long ReferenceId(JToken token)
            {
                if (token?.Type == JTokenType.Integer && long.TryParse(token.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out long id))
                    return id;
                throw new AddressablesException("addressables_layout_schema_unsupported", "The report contains an invalid managed reference ID.");
            }

            private void CheckObject(JObject data, Type type)
            {
                JObject sample = Sample(type);
                // A nested object serialized alone has its own reference table; its inline data has none.
                Require(data != null && data.Count == sample.Count - (type != layout && sample["references"] != null ? 1 : 0), type.Name);
                foreach (JProperty property in sample.Properties())
                {
                    if (property.Name == "references")
                        continue;
                    JToken value = data[property.Name];
                    // The package's pretty writer emits the private header's name for this field.
                    if (
                        value == null
                        && type == layout
                        && property.Name == "AddressablesBuildSessionGUID"
                        && layout.GetNestedType("BuildLayoutHeader", BindingFlags.NonPublic)?.GetField("BuildSessionGUID")?.FieldType == typeof(GUID)
                    )
                        value = data["BuildSessionGUID"];
                    Require(value != null, type.Name + "." + property.Name);
                    var key = (type, property.Name);
                    if (!fields.TryGetValue(key, out FieldInfo field))
                    {
                        field = type.GetField(property.Name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                        Require(field != null, property.Name);
                        fields.Add(key, field);
                    }
                    Type declared = field.FieldType;
                    if (
                        type.DeclaringType == layout
                        && type.Name == "Group"
                        && property.Name == "ContentDirectoryName"
                        && declared == typeof(string)
                        && value.Type == JTokenType.Null
                    )
                        continue;
                    bool managed = field.IsDefined(typeof(SerializeReference), false);
                    Type element =
                        declared.IsArray ? declared.GetElementType()
                        : declared.IsGenericType && declared.GetGenericTypeDefinition() == typeof(List<>) ? declared.GetGenericArguments()[0]
                        : null;
                    bool requiredReference =
                        property.Name == "IncludedInBundleFiles" || property.Name == "OtherAssets" || type.Name == "File" && property.Name == "Bundle";
                    if (element == null)
                        CheckValue(value, declared, managed, requiredReference, property.Value);
                    else
                    {
                        Require(value is JArray, property.Name);
                        foreach (JToken item in (JArray)value)
                            CheckValue(item, element, managed, requiredReference, null);
                    }
                }
            }

            private void CheckValue(JToken value, Type type, bool managed, bool requiredReference, JToken sample)
            {
                if (managed)
                {
                    Require(value is JObject link && link.Count == 1, type.Name);
                    long id = ReferenceId(value["rid"]);
                    if (id == -2)
                        Require(!requiredReference, type.Name);
                    else
                    {
                        Require(references.TryGetValue(id, out var record) && record.Type == type, type.Name);
                        if (reached.Add(id))
                            pending.Enqueue(id);
                    }
                }
                else if (type == typeof(string))
                    Require(value.Type == JTokenType.String, type.Name);
                else if (type == typeof(bool))
                    Require(value.Type == JTokenType.Boolean, type.Name);
                else if (type == typeof(float) || type == typeof(double))
                {
                    Require(value.Type == JTokenType.Float || value.Type == JTokenType.Integer, type.Name);
                    double number = (double)value;
                    Require(!double.IsNaN(number) && !double.IsInfinity(number) && (type != typeof(float) || Math.Abs(number) <= float.MaxValue), type.Name);
                }
                else if (type.IsEnum || type.IsPrimitive)
                {
                    Require(value.Type == JTokenType.Integer, type.Name);
                    try
                    {
                        Convert.ChangeType(value.ToString(), type.IsEnum ? Enum.GetUnderlyingType(type) : type, CultureInfo.InvariantCulture);
                    }
                    catch (OverflowException)
                    {
                        Require(false, type.Name);
                    }
                }
                else if (type == typeof(GUID))
                    Require(sample is JObject empty && empty.Count == 0 && JToken.DeepEquals(value, empty), type.Name);
                else if (type == typeof(Hash128))
                {
                    var hash = value as JObject;
                    Require(
                        sample is JObject example
                            && hash?.Count == example.Count
                            && JToken.DeepEquals(hash["serializedVersion"], example["serializedVersion"])
                            && hash["Hash"]?.Type == JTokenType.String
                            && Regex.IsMatch((string)hash["Hash"], @"\A[0-9a-fA-F]{32}\z"),
                        type.Name
                    );
                }
                else
                {
                    Require(type.Assembly == layout.Assembly && type.FullName.StartsWith(layout.FullName + "+", StringComparison.Ordinal), type.Name);
                    CheckObject(value as JObject, type);
                }
            }
        }

        internal static object Read(JObject request)
        {
            AddressablesState.RequirePackage(out Type settingsType, out string version);
            Type layoutType = settingsType.Assembly.GetType("UnityEditor.AddressableAssets.Build.Layout.BuildLayout");
            RequireSchema(layoutType);
            bool modern = UsesContentDirectories(layoutType);
            string relative = AddressablesState.Text(request["layout_path"], "layout_path");
            if (
                (
                    !relative.StartsWith("Library/com.unity.addressables/BuildReports/", StringComparison.Ordinal)
                    && !relative.StartsWith("Library/com.unity.addressables/buildReports/", StringComparison.Ordinal)
                    && !relative.StartsWith("Library/MCPForUnity/Addressables/", StringComparison.Ordinal)
                )
                || relative.Contains("\\")
                || relative.Split('/').Any(p => p == "" || p == "." || p == "..")
                || !relative.EndsWith(".json", StringComparison.OrdinalIgnoreCase)
            )
                AddressablesState.Fail("invalid_addressables_request", "Layout must be an explicit contained Addressables JSON report.");
            string path = SafePathUtility.ResolveWithinRoot(Path.GetDirectoryName(Application.dataPath), relative);
            if (!File.Exists(path))
                AddressablesState.Fail("addressables_layout_not_found", "Existing layout report was not found; no content build was started.");
            byte[] bytes;
            using (var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (file.Length < 2 || file.Length > 16 * 1024 * 1024)
                    AddressablesState.Fail("addressables_budget_exceeded", "Layout must fit16MiB.");
                using (var memory = new MemoryStream())
                {
                    var buffer = new byte[8192];
                    int count;
                    while ((count = file.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        if (memory.Length + count > 16 * 1024 * 1024)
                            AddressablesState.Fail("addressables_budget_exceeded", "Layout grew beyond16MiB.");
                        memory.Write(buffer, 0, count);
                    }
                    bytes = memory.ToArray();
                }
            }
            string json = Utf8.GetString(bytes).TrimStart('\uFEFF');
            JObject header;
            try
            {
                using (var text = new StringReader(json))
                using (var reader = new JsonTextReader(text) { MaxDepth = 64, DateParseHandling = DateParseHandling.None })
                {
                    header = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                    if (reader.Read())
                        AddressablesState.Fail("addressables_layout_invalid", "Unexpected trailing report data.");
                }
            }
            catch (JsonException)
            {
                throw new AddressablesException("addressables_layout_invalid", "The report must contain one unambiguous JSON object.");
            }
            const string producer = "com.unity.addressables: ";
            JToken declaration = header["PackageVersion"];
            string declared = declaration?.Type == JTokenType.String ? (string)declaration : null;
            if (
                declared == null
                || !declared.StartsWith(producer, StringComparison.Ordinal)
                || declared.Length > producer.Length + 128
                || !Regex.IsMatch(declared.Substring(producer.Length), @"\A[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?(?:\+[a-zA-Z0-9.-]+)?\z")
            )
                AddressablesState.Fail(
                    "addressables_layout_version_unsupported",
                    "The report must declare its com.unity.addressables producer and package version."
                );
            RequireSupportedReportShape(header, modern);
            new WireSchema(layoutType).Check(header);
            object layout = Activator.CreateInstance(layoutType);
            // Give the native reader exactly the checked tokens. The revision still identifies the original file bytes.
            EditorJsonUtility.FromJsonOverwrite(header.ToString(Formatting.None), layout);
            string revision;
            using (var sha = SHA256.Create())
                revision = "addr-layout1:" + BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            string expected =
                request.Property("expected_revision") == null ? null : AddressablesState.Text(request["expected_revision"], "expected_revision", 80);
            int page = AddressablesState.Integer(request["page_number"], 1, 1000000),
                size = AddressablesState.Integer(request["page_size"], 50, 100);
            if (page > 1 && expected == null)
                AddressablesState.Fail("invalid_addressables_request", "Layout continuation requires expected_revision.");
            if (expected != null && expected != revision)
                AddressablesState.Fail("stale_addressables_revision", "Layout content changed; restart analysis.");
            var rows = new JArray();
            var files = new Dictionary<object, Dictionary<string, ObjectBytes>>();
            int objectWork = 0,
                duplicateWork = 0,
                memberships = 0;
            foreach (object asset in AddressablesState.List(AddressablesState.Get(layout, "DuplicatedAssets"), 4096))
            {
                string guid = BoundedText(AddressablesState.Get(asset, "AssetGuid"), 64);
                foreach (object duplicate in AddressablesState.List(AddressablesState.Get(asset, "DuplicatedObjects"), 8192))
                {
                    if (++duplicateWork > 8192)
                        AddressablesState.Fail("addressables_budget_exceeded", "Duplicate object count exceeds8192.");
                    string localId = Convert
                        .ToInt64(AddressablesState.Get(duplicate, "LocalIdentifierInFile"), CultureInfo.InvariantCulture)
                        .ToString(CultureInfo.InvariantCulture);
                    var copies = new JArray();
                    var identities = new HashSet<string>(StringComparer.Ordinal);
                    var bundles = new HashSet<string>(StringComparer.Ordinal);
                    ulong serialized = 0,
                        streamed = 0,
                        largestSerialized = 0,
                        largestStreamed = 0;
                    bool sizes = true;
                    string assetPath = "";
                    foreach (object file in AddressablesState.List(AddressablesState.Get(duplicate, "IncludedInBundleFiles"), 128))
                    {
                        if (++memberships > 32768)
                            AddressablesState.Fail("addressables_budget_exceeded", "Duplicate memberships exceed32768.");
                        object bundle = AddressablesState.Get(file, "Bundle");
                        string bundleName = BoundedText(AddressablesState.Get(bundle, "Name"), 512);
                        string fileName = BoundedText(AddressablesState.Get(file, "Name"), 512);
                        if (!identities.Add(bundleName + "|" + fileName))
                            continue;
                        bundles.Add(bundleName);
                        if (!files.TryGetValue(file, out var index))
                        {
                            if (files.Count >= 2048)
                                AddressablesState.Fail("addressables_budget_exceeded", "Referenced layout files exceed2048.");
                            index = Index(file, ref objectWork);
                            files.Add(file, index);
                        }
                        var copy = new JObject { ["bundle"] = bundleName, ["file"] = fileName };
                        if (index.TryGetValue(guid + "|" + localId, out ObjectBytes data))
                        {
                            assetPath = data.Path;
                            copy["serialized_bytes"] = data.Serialized;
                            copy["streamed_bytes"] = data.Streamed;
                            checked
                            {
                                serialized += data.Serialized;
                                streamed += data.Streamed;
                            }
                            largestSerialized = Math.Max(largestSerialized, data.Serialized);
                            largestStreamed = Math.Max(largestStreamed, data.Streamed);
                        }
                        else
                        {
                            sizes = false;
                            copy["byte_evidence"] = "unavailable_for_this_object";
                        }
                        copies.Add(copy);
                    }
                    rows.Add(
                        new JObject
                        {
                            ["asset_guid"] = guid,
                            ["local_file_id"] = localId,
                            ["asset_path"] = assetPath,
                            ["file_count"] = copies.Count,
                            ["bundle_count"] = bundles.Count,
                            ["copies"] = copies,
                            ["byte_evidence_complete"] = sizes,
                            ["repeated_serialized_bytes"] = sizes ? new JValue(serialized - largestSerialized) : JValue.CreateNull(),
                            ["repeated_streamed_bytes"] = sizes ? new JValue(streamed - largestStreamed) : JValue.CreateNull(),
                        }
                    );
                }
            }
            var ordered = rows.OrderBy(r => r.Value<string>("asset_guid"), StringComparer.Ordinal)
                .ThenBy(r => r.Value<string>("local_file_id"), StringComparer.Ordinal)
                .ToArray();
            long start = (long)(page - 1) * size;
            return AddressablesState.Bounded(
                new SuccessResponse(
                    "Read existing native Addressables layout",
                    new JObject
                    {
                        ["revision"] = revision,
                        ["package_version"] = version,
                        ["report_package_version"] = declared.Substring(producer.Length),
                        ["build_target"] = AddressablesState.Get(layout, "BuildTarget").ToString(),
                        ["unity_version"] = BoundedText(AddressablesState.Get(layout, "UnityVersion"), 64),
                        ["analysis_complete"] = string.IsNullOrEmpty(AddressablesState.Get(layout, "BuildError") as string),
                        ["total"] = rows.Count,
                        ["page_number"] = page,
                        ["page_size"] = size,
                        ["items"] = new JArray(ordered.Skip((int)Math.Min(start, ordered.Length)).Take(size)),
                        ["has_more"] = start + size < ordered.Length,
                        ["content_build_started"] = false,
                        ["limitations"] = new JArray(
                            "Native reported implicit object duplication only; no invented duplicates from file names or total bundle sizes.",
                            "Repeated bytes sum matching native object copies minus one largest copy; they are serialized/streamed payload estimates, not compressed download savings or runtime memory.",
                            "Native deserialization is indivisible. Bounds:16MiB input,4096 duplicate assets,8192 objects,32768 memberships,2048 referenced files,262144 indexed objects."
                        ),
                    }
                )
            );
        }

        private static string BoundedText(object value, int max)
        {
            string text = value?.ToString() ?? "";
            if (text.Length > max)
                AddressablesState.Fail("addressables_budget_exceeded", "Layout text exceeds its field budget.");
            return text;
        }

        private static Dictionary<string, ObjectBytes> Index(object file, ref int work)
        {
            var index = new Dictionary<string, ObjectBytes>(StringComparer.Ordinal);
            foreach (object asset in AddressablesState.List(AddressablesState.Get(file, "OtherAssets"), 4096))
            {
                string guid = BoundedText(AddressablesState.Get(asset, "AssetGuid"), 64);
                string path = BoundedText(AddressablesState.Get(asset, "AssetPath"), 512);
                foreach (object item in AddressablesState.List(AddressablesState.Get(asset, "Objects"), 8192))
                {
                    if (++work > 262144)
                        AddressablesState.Fail("addressables_budget_exceeded", "Layout object indexing exceeds262144.");
                    string id = Convert
                        .ToInt64(AddressablesState.Get(item, "LocalIdentifierInFile"), CultureInfo.InvariantCulture)
                        .ToString(CultureInfo.InvariantCulture);
                    string key = guid + "|" + id;
                    if (index.ContainsKey(key))
                        AddressablesState.Fail("addressables_layout_invalid", "Duplicate native object identity inside one implicit asset/file.");
                    index.Add(
                        key,
                        new ObjectBytes
                        {
                            Serialized = Convert.ToUInt64(AddressablesState.Get(item, "SerializedSize"), CultureInfo.InvariantCulture),
                            Streamed = Convert.ToUInt64(AddressablesState.Get(item, "StreamedSize"), CultureInfo.InvariantCulture),
                            Path = path,
                        }
                    );
                }
            }
            return index;
        }
    }
}
