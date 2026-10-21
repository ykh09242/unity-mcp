using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    internal sealed class ArchiveSelectionException : ArgumentException
    {
        internal string ErrorCode { get; }

        internal ArchiveSelectionException(string code, string message)
            : base(message) => ErrorCode = code;
    }

    /// <summary>Bounded, metadata-only selection from the same archive that will be extracted.</summary>
    internal static class ModelArchiveSelection
    {
        internal const int MaxCandidates = 1024;
        internal const int MaxPathBytes = 512;
        internal const int MaxDepth = 32;
        private static readonly UTF8Encoding StrictUtf8 = new(false, true);

        internal static string NormalizeSelector(string value)
        {
            if (value == null)
                return null;
            string path = value.Replace('\\', '/');
            string[] segments = path.Split('/');
            if (string.IsNullOrWhiteSpace(path) || Unsafe(path) || segments.Any(s => s.Length == 0 || s == "." || s == "..") || !IsModel(path))
                throw InvalidSelector();
            try
            {
                if (StrictUtf8.GetByteCount(path) > MaxPathBytes || segments.Length > MaxDepth)
                    throw InvalidSelector();
            }
            catch (EncoderFallbackException)
            {
                throw InvalidSelector();
            }
            return path;
        }

        internal static string ReadSelector(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null)
                return null;
            if (token.Type != JTokenType.String)
                throw InvalidSelector();
            return NormalizeSelector((string)token);
        }

        private static ArchiveSelectionException InvalidSelector() =>
            new(
                "archive_model_path_invalid",
                "archive_model_path must be an exact relative .fbx/.obj/.glb/.gltf entry path without traversal, URLs or wildcards (at most 512 UTF-8 bytes and 32 segments)."
            );

        private static bool Unsafe(string path) =>
            path.StartsWith("/", StringComparison.Ordinal)
            || path.Any(char.IsControl)
            || path.Contains(":")
            || path.IndexOfAny(new[] { '*', '?', '"', '<', '>', '|' }) >= 0
            || Path.IsPathRooted(path);

        private static bool IsModel(string path)
        {
            string ext = Path.GetExtension(path).ToLowerInvariant();
            return ext == ".fbx" || ext == ".obj" || ext == ".gltf" || ext == ".glb";
        }

        private static int Group(string path)
        {
            string ext = Path.GetExtension(path).ToLowerInvariant();
            return ext == ".fbx" || ext == ".obj" ? 0 : 1;
        }

        internal static JObject Evidence(string archivePath, string requested) =>
            new()
            {
                ["archive_path"] = archivePath,
                ["requested_path"] = requested,
                ["selected_path"] = null,
                ["selected_asset_path"] = null,
                ["extraction_root"] = null,
                ["mode"] = requested == null ? "default" : "explicit",
                ["policy"] = "builtin_then_ordinal",
                ["candidates"] = new JArray(),
                ["candidate_count"] = 0,
                ["candidates_complete"] = false,
                ["extraction"] = "not_started",
            };

        internal static string Select(
            ZipArchive archive,
            ISet<string> allowedExtensions,
            string requestedPath,
            JObject evidence,
            int maxCandidates = MaxCandidates,
            int maxPathBytes = MaxPathBytes,
            int maxDepth = MaxDepth
        )
        {
            evidence["candidates"] = new JArray();
            evidence["candidate_count"] = 0;
            evidence["candidates_complete"] = false;
            string requested = NormalizeSelector(requestedPath);
            var destinations = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
            var candidates = new List<string>();
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                string raw = entry.FullName.Replace('\\', '/');
                if (raw.Length == 0)
                    continue;
                // Match the existing extractor's stricter traversal rule, including skipped entries.
                if (Unsafe(raw) || raw.Contains(".."))
                    throw Failure(evidence, "archive_extraction_failed", "Unsafe ZIP entry rejected.");
                string[] segments = raw.Split('/').Where(s => s.Length != 0 && s != ".").ToArray();
                string path = string.Join("/", segments);
                if (path.Length == 0)
                    throw Failure(evidence, "archive_extraction_failed", "ZIP entry cannot resolve to its extraction root.");
                int bytes;
                try
                {
                    bytes = StrictUtf8.GetByteCount(path);
                }
                catch (EncoderFallbackException)
                {
                    throw Failure(evidence, "archive_extraction_failed", "ZIP entry path is not valid Unicode.");
                }
                if (bytes > maxPathBytes || segments.Length > maxDepth)
                    throw Limit(evidence, "entry_path", "Archive entry exceeds the model-selection path limit.");
                bool directory = string.IsNullOrEmpty(entry.Name) || raw.EndsWith("/", StringComparison.Ordinal);
                if (
                    !directory
                    && allowedExtensions != null
                    && allowedExtensions.Count > 0
                    && !allowedExtensions.Contains(Path.GetExtension(path).ToLowerInvariant())
                )
                    continue;
                if (destinations.TryGetValue(path, out bool existingDirectory))
                {
                    if (!directory || !existingDirectory)
                        throw Failure(evidence, "archive_entry_conflict", "Archive contains duplicate or conflicting entry destinations.");
                }
                else
                    destinations.Add(path, directory);
                if (!directory && IsModel(path))
                {
                    if (candidates.Count == maxCandidates)
                        throw Limit(
                            evidence,
                            "candidate_count",
                            $"Archive exceeds the {maxCandidates}-model candidate limit; split the archive before importing."
                        );
                    candidates.Add(path);
                }
            }
            foreach (var destination in destinations)
            {
                string path = destination.Key;
                for (int slash = path.IndexOf('/'); slash >= 0; slash = path.IndexOf('/', slash + 1))
                    if (destinations.TryGetValue(path.Substring(0, slash), out bool parentDirectory) && !parentDirectory)
                        throw Failure(evidence, "archive_entry_conflict", "An archive file conflicts with another entry's parent directory.");
            }
            candidates.Sort((a, b) => Group(a) != Group(b) ? Group(a).CompareTo(Group(b)) : StringComparer.Ordinal.Compare(a, b));
            evidence["candidates"] = new JArray(candidates);
            evidence["candidate_count"] = candidates.Count;
            evidence["candidates_complete"] = true;
            if (candidates.Count == 0)
                throw Failure(evidence, "archive_no_model", "Archive contains no model file (.fbx/.obj/.glb/.gltf).");
            string selected = requested ?? candidates[0];
            if (!candidates.Contains(selected, StringComparer.Ordinal))
                throw Failure(evidence, "archive_model_not_found", "archive_model_path was not found. Choose an exact path from archive_selection.candidates.");
            evidence["selected_path"] = selected;
            return selected;
        }

        private static ArchiveSelectionException Limit(JObject evidence, string limit, string message)
        {
            evidence["limit"] = limit;
            evidence["candidates"] = new JArray();
            evidence["candidate_count"] = 0;
            evidence["candidates_complete"] = false;
            return Failure(evidence, "archive_selection_limit", message);
        }

        private static ArchiveSelectionException Failure(JObject evidence, string code, string message)
        {
            evidence["selection_error_code"] = code;
            return new ArchiveSelectionException(code, message);
        }
    }
}
