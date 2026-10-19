using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
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
    internal static class AddressablesLayout
    {
        private sealed class ObjectBytes
        {
            internal ulong Serialized;
            internal ulong Streamed;
            internal string Path;
        }

        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

        internal static object Read(JObject request)
        {
            AddressablesState.RequirePackage(out Type settingsType, out string version);
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
            using (var text = new StringReader(json))
            using (var reader = new JsonTextReader(text) { MaxDepth = 64 })
                header = JObject.Load(reader);
            // Native2.7.6 BuildLayoutGenerationTask declares both package identity and version.
            // Do not substitute the installed package version for missing or different report metadata.
            JToken declaration = header["PackageVersion"];
            if (declaration?.Type != JTokenType.String || declaration.Value<string>() != "com.unity.addressables: " + version)
                AddressablesState.Fail("addressables_layout_version_unsupported", "Only a native declared com.unity.addressables: 2.7.6 report is supported.");
            if (header.Property("DuplicatedAssets") == null || header.Property("Groups") == null)
                AddressablesState.Fail("addressables_layout_invalid", "Required native layout schema is missing.");
            Type layoutType = settingsType.Assembly.GetType("UnityEditor.AddressableAssets.Build.Layout.BuildLayout", true);
            object layout = Activator.CreateInstance(layoutType);
            // This is the exact native deserializer used by2.7.6 BuildLayout.ReadFull, applied to the bounded immutable bytes.
            // Avoid reopening a changing report or writing a temporary file during this read action.
            EditorJsonUtility.FromJsonOverwrite(json, layout);
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
