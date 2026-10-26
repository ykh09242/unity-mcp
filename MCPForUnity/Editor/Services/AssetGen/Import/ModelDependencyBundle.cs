using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    internal sealed class BundleLimits
    {
        internal int MaxFiles = 8192;
        internal int MaxReferences = 65536;
        internal long MaxBytes = 16L * 1024 * 1024 * 1024;
        internal int MaxJsonBytes = 64 * 1024 * 1024;
        internal int MaxMtlBytes = 16 * 1024 * 1024;
        internal int MaxMetadataBytes = 128 * 1024 * 1024;
        internal int MaxJsonDepth = 64;
        internal int MaxLineBytes = 256 * 1024;
        internal int MaxPathCharacters = 1024;
        internal int MaxSegments = 32;
        internal int MaxSegmentBytes = 255;
        internal int MaxNameAttempts = 1000;
    }

    internal sealed class BundleEntry
    {
        internal string RelativePath;
        internal string AbsolutePath;
        internal long Length;
        internal byte[] MetadataHash;
    }

    internal sealed class BundlePlan
    {
        internal string SourceRoot;
        internal readonly List<BundleEntry> Entries = new();
        internal readonly Dictionary<string, BundleEntry> EntriesByPath = new(StringComparer.Ordinal);
        internal readonly Dictionary<string, string> PortablePaths = new(StringComparer.OrdinalIgnoreCase);
        internal readonly Dictionary<string, string> PortableDirectories = new(StringComparer.OrdinalIgnoreCase);
        internal BundleLimits Limits;
        internal long Bytes;
        internal long EmbeddedBytes;
        internal int References;
        internal int MetadataBytes;
        internal readonly JObject Evidence = new()
        {
            ["inspection"] = "complete",
            ["inspection_scope"] = "supported_local_dependencies",
            ["stage"] = "not_staged",
            ["planned_file_count"] = 0,
            ["staged_file_count"] = 0,
            ["staged_bytes"] = 0L,
        };
    }

    internal sealed class BundleStageResult
    {
        internal string MainAssetPath;
        internal string BundleRoot;
        internal JObject Evidence;
    }

    internal sealed class BundleImportException : IOException
    {
        internal JObject Evidence { get; }

        internal BundleImportException(string code, string message, JObject evidence, string reference = null)
            : base(message)
        {
            Evidence = (JObject)evidence.DeepClone();
            Evidence["error_code"] = code;
            if (reference != null)
            {
                string scrubbed = SecretRedactor.Scrub(reference);
                Evidence["reference"] = scrubbed.Length <= 256 ? scrubbed : scrubbed.Substring(0, 256);
            }
        }
    }

    /// <summary>
    /// Plans only explicit, bounded local glTF/OBJ dependencies. Does not certify importer output,
    /// render quality, arbitrary exporter extensions, or filesystem safety against concurrent attackers.
    /// </summary>
    internal static class ModelDependencyBundle
    {
        private static readonly UTF8Encoding Utf8 = new(false, true);
        private static readonly HashSet<string> ObjStatements = new(StringComparer.Ordinal) { "v", "vt", "vn", "vp", "p", "l", "f", "o", "g", "s", "usemtl" };
        private static readonly HashSet<string> MtlValues = new(StringComparer.Ordinal)
        {
            "newmtl",
            "Ka",
            "Kd",
            "Ks",
            "Ke",
            "Tf",
            "Ns",
            "Ni",
            "d",
            "Tr",
            "illum",
            "sharpness",
        };
        private static readonly HashSet<string> MtlMaps = new(StringComparer.Ordinal)
        {
            "map_Ka",
            "map_Kd",
            "map_Ks",
            "map_Ns",
            "map_d",
            "bump",
            "map_bump",
            "disp",
            "decal",
        };

        internal static BundlePlan Plan(string sourceAbsolutePath, BundleLimits limits = null)
        {
            var plan = new BundlePlan { Limits = limits ?? new BundleLimits() };
            try
            {
                string source = AssetGenPaths.ToAbsolute(sourceAbsolutePath);
                plan.SourceRoot = Path.GetDirectoryName(source);
                BundleEntry main = AddFile(plan, Path.GetFileName(source));
                string extension = Path.GetExtension(source).ToLowerInvariant();
                if (extension == ".gltf" || extension == ".glb")
                    ReadGltf(plan, main, extension == ".glb");
                else if (extension == ".obj")
                {
                    ReadStatements(plan, main, false);
                    // OBJ only discovers same-directory MTLs; MTL only discovers terminal textures.
                    for (int i = 1; i < plan.Entries.Count; i++)
                        if (Path.GetExtension(plan.Entries[i].RelativePath).Equals(".mtl", StringComparison.OrdinalIgnoreCase))
                            ReadStatements(plan, plan.Entries[i], true);
                }
                else
                    throw Error(plan, "unsupported_dependency_form", "Dependency inspection supports local OBJ, glTF and core GLB resources only.");
                var imageAdmission = new ImageAdmission();
                foreach (BundleEntry entry in plan.Entries)
                    if (ImageAdmission.IsImage(Path.GetExtension(entry.RelativePath)))
                    {
                        using var imageStream = new FileStream(entry.AbsolutePath, FileMode.Open, FileAccess.Read, FileShare.Read);
                        imageAdmission.Inspect(imageStream, imageStream.Length, Path.GetExtension(entry.RelativePath));
                        imageStream.Position = 0;
                        entry.MetadataHash = HashBounded(plan, imageStream, entry);
                    }
                ModelMetadataAdmission.File(source, imageAdmission, externalImagesAlreadyAdmitted: true);
                return plan;
            }
            catch (BundleImportException)
            {
                throw;
            }
            catch (JsonReaderException e) when (e.Message.IndexOf("MaxDepth", StringComparison.Ordinal) >= 0)
            {
                throw Error(plan, "dependency_limit_exceeded", "glTF JSON exceeds the nesting limit.");
            }
            catch (InvalidDataException e)
                when (e.Message.IndexOf("budget", StringComparison.Ordinal) >= 0 || e.Message.IndexOf("limit", StringComparison.Ordinal) >= 0)
            {
                throw Error(plan, "dependency_limit_exceeded", e.Message);
            }
            catch (Exception e)
            {
                throw Error(plan, "unsupported_dependency_form", "Could not inspect model dependencies: " + e.Message);
            }
        }

        private static BundleImportException Error(BundlePlan plan, string code, string message, string reference = null) =>
            new(code, message, plan.Evidence, reference);

        private static BundleEntry AddFile(BundlePlan plan, string relative)
        {
            ValidatePath(plan, relative);
            for (int separator = relative.IndexOf('/'); separator >= 0; separator = relative.IndexOf('/', separator + 1))
            {
                string prefix = relative.Substring(0, separator);
                if (plan.PortableDirectories.TryGetValue(prefix, out string directorySpelling) && directorySpelling != prefix)
                    throw Error(plan, "unsafe_dependency_path", "Dependency directories must not alias by case or normalization.", relative);
                plan.PortableDirectories[prefix] = prefix;
            }
            if (plan.PortablePaths.TryGetValue(relative, out string spelling))
            {
                if (!string.Equals(spelling, relative, StringComparison.Ordinal))
                    throw Error(plan, "unsafe_dependency_path", "Dependency paths must not alias by case or normalization.", relative);
                return plan.EntriesByPath[relative];
            }
            if (plan.Entries.Count >= plan.Limits.MaxFiles)
                throw Error(plan, "dependency_limit_exceeded", "Dependency file count exceeds the local bundle limit.", relative);
            string absolute = ResolveSource(plan, relative);
            if (Directory.Exists(absolute))
                throw Error(plan, "unsafe_dependency_path", "A dependency must be a regular file.", relative);
            if (!File.Exists(absolute))
                throw Error(plan, "dependency_not_found", "Referenced dependency was not found: " + relative, relative);
            long length = new FileInfo(absolute).Length;
            if (length > plan.Limits.MaxBytes - plan.Bytes - plan.EmbeddedBytes)
                throw Error(plan, "dependency_limit_exceeded", "Dependency bytes exceed the local bundle limit.", relative);
            var entry = new BundleEntry
            {
                RelativePath = relative,
                AbsolutePath = absolute,
                Length = length,
            };
            plan.Bytes += length;
            plan.PortablePaths.Add(relative, relative);
            plan.Entries.Add(entry);
            plan.EntriesByPath.Add(relative, entry);
            plan.Evidence["planned_file_count"] = plan.Entries.Count;
            return entry;
        }

        private static string ResolveSource(BundlePlan plan, string relative)
        {
            try
            {
                return AssetGenPaths.ToAbsolute(SafePathUtility.ResolveWithinRoot(plan.SourceRoot, relative));
            }
            catch (Exception e)
            {
                throw Error(plan, "unsafe_dependency_path", "Dependency must remain below the source directory without links: " + e.Message, relative);
            }
        }

        private static void ValidatePath(BundlePlan plan, string relative)
        {
            if (string.IsNullOrEmpty(relative))
                throw Error(plan, "unsafe_dependency_path", "Dependency path must not be empty.", relative);
            if (relative.Length > plan.Limits.MaxPathCharacters)
                throw Error(plan, "dependency_limit_exceeded", "Dependency path exceeds the path length limit.", relative);
            if (relative != relative.Normalize(NormalizationForm.FormC))
                throw Error(plan, "unsafe_dependency_path", "Dependency paths must use unambiguous Unicode normalization.", relative);
            string[] segments = relative.Split('/');
            if (segments.Length > plan.Limits.MaxSegments)
                throw Error(plan, "dependency_limit_exceeded", "Dependency path exceeds the segment limit.", relative);
            foreach (string segment in segments)
            {
                if (
                    segment.Length == 0
                    || segment == "."
                    || segment == ".."
                    || segment.EndsWith(".", StringComparison.Ordinal)
                    || segment.EndsWith(" ", StringComparison.Ordinal)
                )
                    throw Error(
                        plan,
                        "unsafe_dependency_path",
                        "Dependency paths must be plain relative paths without traversal or ambiguous segments.",
                        relative
                    );
                if (Utf8.GetByteCount(segment) > plan.Limits.MaxSegmentBytes)
                    throw Error(plan, "dependency_limit_exceeded", "Dependency path segment exceeds the byte limit.", relative);
                foreach (char c in segment)
                    if (char.IsControl(c) || "\\:<>\"|?*#%".IndexOf(c) >= 0)
                        throw Error(plan, "unsafe_dependency_path", "Dependency URI/path syntax is not a permitted local relative path.", relative);
                string stem = segment.Split('.')[0].ToUpperInvariant();
                if (
                    stem == "CON"
                    || stem == "PRN"
                    || stem == "AUX"
                    || stem == "NUL"
                    || (
                        stem.Length == 4
                        && (stem.StartsWith("COM", StringComparison.Ordinal) || stem.StartsWith("LPT", StringComparison.Ordinal))
                        && stem[3] >= '1'
                        && stem[3] <= '9'
                    )
                )
                    throw Error(plan, "unsafe_dependency_path", "Device names are not permitted in dependency paths.", relative);
            }
        }

        private static string DecodeUri(BundlePlan plan, string uri)
        {
            if (uri.Length > plan.Limits.MaxPathCharacters)
                throw Error(plan, "dependency_limit_exceeded", "Dependency URI exceeds the path length limit.");
            for (int i = 0; i < uri.Length; i++)
                if (uri[i] == '%')
                {
                    if (
                        i + 2 >= uri.Length
                        || !byte.TryParse(
                            uri.Substring(i + 1, 2),
                            System.Globalization.NumberStyles.HexNumber,
                            System.Globalization.CultureInfo.InvariantCulture,
                            out byte value
                        )
                        || value == '/'
                        || value == '\\'
                        || value == '%'
                    )
                        throw Error(plan, "unsafe_dependency_path", "Malformed or ambiguous percent encoding in dependency URI.", uri);
                    i += 2;
                }
            string decoded = Uri.UnescapeDataString(uri);
            ValidatePath(plan, decoded);
            return decoded;
        }

        private static void Reference(BundlePlan plan)
        {
            if (++plan.References > plan.Limits.MaxReferences)
                throw Error(plan, "dependency_limit_exceeded", "Dependency reference count exceeds the local bundle limit.");
        }

        private static void ReadGltf(BundlePlan plan, BundleEntry main, bool glb)
        {
            JObject document;
            using (var stream = new FileStream(main.AbsolutePath, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                long length = stream.Length;
                if (length != main.Length)
                    throw Error(plan, "dependency_source_changed", "Model changed before metadata inspection.");
                int jsonLength;
                if (glb)
                {
                    plan.Evidence["inspection_scope"] = "glb_core_resources_only";
                    using var reader = new BinaryReader(stream, Utf8, true);
                    if (length < 20 || reader.ReadUInt32() != 0x46546C67 || reader.ReadUInt32() != 2 || reader.ReadUInt32() != length)
                        throw Error(plan, "unsupported_dependency_form", "GLB core resource inspection requires a valid GLB 2.0 header.");
                    uint chunkLength = reader.ReadUInt32();
                    uint chunkType = reader.ReadUInt32();
                    if (chunkType != 0x4E4F534A || chunkLength > length - 20 || chunkLength % 4 != 0)
                        throw Error(plan, "unsupported_dependency_form", "GLB must begin with a bounded JSON chunk.");
                    if (chunkLength > plan.Limits.MaxJsonBytes)
                        throw Error(plan, "dependency_limit_exceeded", "GLB JSON exceeds the metadata limit.");
                    jsonLength = (int)chunkLength;
                }
                else
                {
                    if (length > plan.Limits.MaxJsonBytes)
                        throw Error(plan, "dependency_limit_exceeded", "glTF JSON exceeds the metadata limit.");
                    jsonLength = (int)length;
                }
                ChargeMetadata(plan, jsonLength);
                byte[] json = new byte[jsonLength];
                int offset = 0;
                while (offset < json.Length)
                {
                    int count = stream.Read(json, offset, json.Length - offset);
                    if (count == 0)
                        throw Error(plan, "dependency_source_changed", "Model metadata changed while being inspected.");
                    offset += count;
                }
                document = BoundedJson.ParseObject(Utf8.GetString(json), BoundedJson.ModelLimits(plan.Limits.MaxJsonDepth));
                stream.Position = 0;
                main.MetadataHash = HashBounded(plan, stream, main);
            }

            bool opaque = false;
            foreach (JToken token in document.Descendants())
                if (
                    token is JProperty property
                    && (property.Name == "extensions" || property.Name == "extensionsUsed" || property.Name == "extensionsRequired")
                    && property.Value.Type != JTokenType.Null
                    && (property.Value.HasValues || !(property.Value is JContainer))
                )
                    opaque = true;
            if (opaque && !glb)
                throw Error(
                    plan,
                    "unsupported_dependency_form",
                    "Text glTF extensions are outside the supported dependency subset; export core glTF or a self-contained model."
                );
            if (opaque)
                plan.Evidence["inspection"] = "not_scanned";
            ReadResources(plan, document["buffers"], "buffer", glb);
            ReadResources(plan, document["images"], "image", glb);
        }

        private static void ReadResources(BundlePlan plan, JToken resources, string role, bool glb)
        {
            if (resources == null)
                return;
            if (!(resources is JArray array))
                throw Error(plan, "unsupported_dependency_form", "glTF " + role + " resources must be an array.");
            foreach (JToken resource in array)
            {
                if (!(resource is JObject item))
                    throw Error(plan, "unsupported_dependency_form", "glTF resource must be an object.");
                JToken uri = item["uri"];
                if (uri == null)
                {
                    if (role == "buffer" && !glb)
                        throw Error(plan, "unsupported_dependency_form", "Text glTF buffers require a supported URI.");
                    if (role == "image" && item["bufferView"] == null)
                        throw Error(plan, "unsupported_dependency_form", "glTF image requires a URI or embedded bufferView.");
                    continue;
                }
                Reference(plan);
                if (uri.Type != JTokenType.String || (role == "image" && item["bufferView"] != null))
                    throw Error(plan, "unsupported_dependency_form", "glTF resource URI must be a string with one resource location.");
                string value = (string)uri;
                if (value.StartsWith("data:", glb ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal))
                {
                    // GLB inspection only detects external core resources; preserve embedded exporter metadata.
                    if (!glb)
                        InspectDataUri(plan, value, role);
                    continue;
                }
                if (glb)
                    throw Error(
                        plan,
                        "unsupported_dependency_form",
                        "GLB references an external core resource. Export core text glTF with its local files or a self-contained GLB.",
                        value
                    );
                string relative = DecodeUri(plan, value);
                string extension = Path.GetExtension(relative).ToLowerInvariant();
                bool allowed = role == "buffer" ? extension == ".bin" || extension == ".glbin" || extension == ".glbuf" : IsImage(extension);
                if (!allowed)
                    throw Error(plan, "unsupported_dependency_form", "Dependency extension is outside the supported inert resource subset.", relative);
                AddFile(plan, relative);
            }
        }

        private static void InspectDataUri(BundlePlan plan, string uri, string role)
        {
            int comma = uri.IndexOf(',');
            string header = comma < 0 ? "" : uri.Substring(0, comma);
            bool allowed =
                role == "buffer"
                    ? header == "data:application/octet-stream;base64" || header == "data:application/gltf-buffer;base64"
                    : header == "data:image/png;base64" || header == "data:image/jpeg;base64";
            if (!allowed)
                throw Error(plan, "unsupported_dependency_form", "Only bounded base64 glTF buffer/PNG/JPEG data URIs are supported.");
            int length = uri.Length - comma - 1;
            if (length == 0 || length % 4 != 0)
                throw Error(plan, "unsupported_dependency_form", "Malformed base64 data URI.");
            int padding =
                uri.EndsWith("==", StringComparison.Ordinal) ? 2
                : uri.EndsWith("=", StringComparison.Ordinal) ? 1
                : 0;
            for (int i = comma + 1; i < uri.Length - padding; i++)
            {
                char c = uri[i];
                if (!(c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '+' || c == '/'))
                    throw Error(plan, "unsupported_dependency_form", "Malformed base64 data URI.");
            }
            long decoded = (long)length / 4 * 3 - padding;
            if (decoded > plan.Limits.MaxBytes - plan.Bytes - plan.EmbeddedBytes)
                throw Error(plan, "dependency_limit_exceeded", "Embedded data exceeds the bundle byte limit.");
            plan.EmbeddedBytes += decoded;
        }

        private static bool IsImage(string extension) => extension == ".png" || extension == ".jpg" || extension == ".jpeg";

        private static void ChargeMetadata(BundlePlan plan, int bytes)
        {
            if (bytes > plan.Limits.MaxMetadataBytes - plan.MetadataBytes)
                throw Error(plan, "dependency_limit_exceeded", "Model metadata exceeds the aggregate metadata limit.");
            plan.MetadataBytes += bytes;
        }

        private static void ReadStatements(BundlePlan plan, BundleEntry entry, bool mtl)
        {
            if (mtl)
            {
                if (entry.Length > plan.Limits.MaxMtlBytes)
                    throw Error(plan, "dependency_limit_exceeded", "Material library exceeds the metadata limit.", entry.RelativePath);
                ChargeMetadata(plan, (int)entry.Length);
            }
            using var input = new FileStream(ResolveSource(plan, entry.RelativePath), FileMode.Open, FileAccess.Read, FileShare.Read);
            using var hash = SHA256.Create();
            using var hashing = new CryptoStream(input, hash, CryptoStreamMode.Read);
            using var reader = new StreamReader(hashing, Utf8, true, 4096);
            var line = new StringBuilder();
            char[] buffer = new char[4096];
            int number = 1;
            int count;
            while ((count = reader.Read(buffer, 0, buffer.Length)) != 0)
            {
                if (input.Position > entry.Length)
                    throw Error(plan, "dependency_source_changed", "Model/material grew beyond its inspected byte budget.", entry.RelativePath);
                for (int i = 0; i < count; i++)
                {
                    if (buffer[i] == '\n')
                    {
                        Statement(plan, entry, line.ToString().TrimEnd('\r'), mtl, number++);
                        line.Clear();
                    }
                    else
                    {
                        if (line.Length >= plan.Limits.MaxLineBytes)
                            throw Error(plan, "dependency_limit_exceeded", "Model/material line exceeds the bounded line length.", entry.RelativePath);
                        line.Append(buffer[i]);
                    }
                }
            }
            if (input.Position != entry.Length)
                throw Error(plan, "dependency_source_changed", "Model/material changed while being inspected.", entry.RelativePath);
            if (line.Length != 0)
                Statement(plan, entry, line.ToString().TrimEnd('\r'), mtl, number);
            entry.MetadataHash = hash.Hash;
        }

        private static void Statement(BundlePlan plan, BundleEntry entry, string line, bool mtl, int number)
        {
            if (Utf8.GetByteCount(line) > plan.Limits.MaxLineBytes)
                throw Error(plan, "dependency_limit_exceeded", "Model/material line exceeds the byte limit.", entry.RelativePath);
            string location = entry.RelativePath + ":" + number;
            line = line.Trim();
            if (line.Length == 0 || line.StartsWith("#", StringComparison.Ordinal))
                return;
            if (line.IndexOf('\\') >= 0 || line.IndexOf('"') >= 0 || line.IndexOf('\0') >= 0)
                throw Error(
                    plan,
                    "unsupported_dependency_form",
                    "Quoted/escaped filenames and line continuations are outside the local dependency subset.",
                    location
                );
            int comment = line.IndexOf('#');
            if (comment >= 0)
            {
                if (!char.IsWhiteSpace(line[comment - 1]))
                    throw Error(plan, "unsafe_dependency_path", "Fragments are not supported in local dependency paths.", location);
                line = line.Substring(0, comment).TrimEnd();
            }
            string[] words = line.Split((char[])null, StringSplitOptions.RemoveEmptyEntries);
            string keyword = words[0];
            if (!mtl && keyword == "mtllib")
            {
                if (words.Length < 2)
                    throw Error(plan, "unsupported_dependency_form", "mtllib requires at least one same-directory material library.", location);
                for (int i = 1; i < words.Length; i++)
                {
                    Reference(plan);
                    if (words[i].IndexOf('/') >= 0 || !Path.GetExtension(words[i]).Equals(".mtl", StringComparison.OrdinalIgnoreCase))
                        throw Error(plan, "unsupported_dependency_form", "Only same-directory .mtl references are supported.", location);
                    AddFile(plan, words[i]);
                }
            }
            else if (mtl && MtlMaps.Contains(keyword))
            {
                if (words.Length != 2 || words[1].StartsWith("-", StringComparison.Ordinal))
                    throw Error(plan, "unsupported_dependency_form", "Texture map options and ambiguous filenames are unsupported.", location);
                Reference(plan);
                ValidatePath(plan, words[1]);
                if (!IsImage(Path.GetExtension(words[1]).ToLowerInvariant()))
                    throw Error(plan, "unsupported_dependency_form", "MTL textures must be PNG or JPEG files.", location);
                AddFile(plan, words[1]);
            }
            else if (mtl && MtlValues.Contains(keyword))
            {
                if (words.Length > 1 && words[1] == "spectral")
                    throw Error(plan, "unsupported_dependency_form", "External spectral material files are unsupported.", location);
            }
            else if (mtl || !ObjStatements.Contains(keyword))
                throw Error(plan, "unsupported_dependency_form", "Unsupported model/material statement: " + keyword, location);
        }

        internal static BundleStageResult Stage(
            BundlePlan plan,
            string outputFolder,
            string requestedName,
            Action<int, string> beforeCopy = null,
            CancellationToken cancellationToken = default
        )
        {
            var result = new BundleStageResult { Evidence = (JObject)plan.Evidence.DeepClone() };
            LocalAssetCopy copying = null;
            try
            {
                string root = AssetGenPaths.ResolveOutputFolder(outputFolder, "Imported").TrimEnd('/');
                string name = string.IsNullOrWhiteSpace(requestedName) ? Path.GetFileNameWithoutExtension(plan.Entries[0].RelativePath) : requestedName;
                foreach (char c in Path.GetInvalidFileNameChars())
                    name = name.Replace(c, '_');
                name = name.Trim();
                ValidatePath(plan, name);
                bool bundle = plan.Entries.Count > 1;
                string selected = null;
                for (int i = 0; i < plan.Limits.MaxNameAttempts; i++)
                {
                    string candidate = root + "/" + name + (i == 0 ? "" : "_" + i) + (bundle ? "" : Path.GetExtension(plan.Entries[0].RelativePath));
                    string absolute = AssetGenPaths.ToAbsolute(candidate);
                    string meta = AssetGenPaths.ToAbsolute(candidate + ".meta");
                    if (
                        !File.Exists(absolute)
                        && !Directory.Exists(absolute)
                        && !File.Exists(meta)
                        && !Directory.Exists(meta)
                        && string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(candidate, AssetPathToGUIDOptions.OnlyExistingAssets))
                    )
                    {
                        selected = candidate;
                        break;
                    }
                }
                if (selected == null)
                    throw new BundleImportException(
                        "dependency_limit_exceeded",
                        "Could not allocate a fresh destination within the collision limit.",
                        result.Evidence
                    );
                // Validate all destination paths before creating even the output folder.
                foreach (BundleEntry entry in plan.Entries)
                    AssetGenPaths.ToAbsolute(bundle ? selected + "/" + entry.RelativePath : selected);
                using var folders = new AssetFolderScope();
                using var copyScope = new LocalAssetCopy(Path.GetDirectoryName(Application.dataPath), plan.Bytes, cancellationToken);
                copying = copyScope;
                folders.EnsureFolder(root);
                if (bundle)
                {
                    folders.EnsureFolder(selected);
                    result.BundleRoot = selected;
                    result.Evidence["root_path"] = selected;
                }
                for (int ordinal = 0; ordinal < plan.Entries.Count; ordinal++)
                {
                    int index = (ordinal + 1) % plan.Entries.Count; // main last
                    BundleEntry entry = plan.Entries[index];
                    string destination = bundle ? selected + "/" + entry.RelativePath : selected;
                    beforeCopy?.Invoke(ordinal, destination);
                    string source = ResolveSource(plan, entry.RelativePath);
                    if (new FileInfo(source).Length != entry.Length)
                        throw new BundleImportException(
                            "dependency_source_changed",
                            "Source changed between dependency inspection and staging.",
                            result.Evidence,
                            entry.RelativePath
                        );
                    folders.EnsureParentDirectory(destination);
                    string absolute = AssetGenPaths.ToAbsolute(destination);
                    string metadata = AssetGenPaths.ToAbsolute(destination + ".meta");
                    if (
                        File.Exists(metadata)
                        || Directory.Exists(metadata)
                        || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(destination, AssetPathToGUIDOptions.OnlyExistingAssets))
                    )
                        throw new BundleImportException(
                            "dependency_stage_failed",
                            "Destination metadata or a registered asset appeared before copying; existing identity is preserved.",
                            result.Evidence,
                            entry.RelativePath
                        );
                    result.Evidence["stage"] = "partial";
                    if (index == 0)
                    {
                        result.MainAssetPath = destination;
                        result.Evidence["main_asset_path"] = destination;
                    }
                    try
                    {
                        copying.Copy(
                            source,
                            absolute,
                            entry.Length,
                            entry.MetadataHash,
                            count => result.Evidence["staged_bytes"] = (long)result.Evidence["staged_bytes"] + count
                        );
                    }
                    catch (IOException e)
                    {
                        throw new BundleImportException(
                            e.Message.StartsWith("Local asset", StringComparison.Ordinal) || e.Message.StartsWith("Local model", StringComparison.Ordinal)
                                ? "dependency_source_changed"
                                : "dependency_stage_failed",
                            e.Message,
                            result.Evidence,
                            entry.RelativePath
                        );
                    }
                    result.Evidence["staged_file_count"] = (int)result.Evidence["staged_file_count"] + 1;
                }
                result.Evidence["stage"] = "staged";
                folders.Complete();
                copying.Complete();
                return result;
            }
            catch (Exception e)
            {
                long actualBytes = 0;
                bool cleaned = true;
                if (copying != null)
                    foreach (string path in copying.CreatedFiles)
                    {
                        cleaned &= AssetDiskBudget.FileIsGone(path);
                        try
                        {
                            actualBytes += new FileInfo(path).Length;
                        }
                        catch { /* preserve the original failure */ }
                    }
                result.Evidence["staged_bytes"] = actualBytes;
                if (cleaned && (string)result.Evidence["stage"] == "partial")
                {
                    result.Evidence["stage"] = "rolled_back";
                    result.Evidence["staged_file_count"] = 0;
                    result.Evidence.Remove("main_asset_path");
                }
                string code = e is BundleImportException failure ? (string)failure.Evidence["error_code"] : "dependency_stage_failed";
                string reference = e is BundleImportException known ? (string)known.Evidence["reference"] : null;
                throw new BundleImportException(code, "Dependency staging failed: " + e.Message, result.Evidence, reference);
            }
        }

        private static byte[] HashBounded(BundlePlan plan, Stream stream, BundleEntry entry)
        {
            using var hash = SHA256.Create();
            byte[] buffer = new byte[81920];
            long total = 0;
            int count;
            while ((count = stream.Read(buffer, 0, buffer.Length)) != 0)
            {
                if (count > entry.Length - total)
                    throw Error(plan, "dependency_source_changed", "Model grew beyond its inspected byte budget.", entry.RelativePath);
                hash.TransformBlock(buffer, 0, count, buffer, 0);
                total += count;
            }
            if (total != entry.Length)
                throw Error(plan, "dependency_source_changed", "Model changed while being inspected.", entry.RelativePath);
            hash.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
            return hash.Hash;
        }
    }
}
