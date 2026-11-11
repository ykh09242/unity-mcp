using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor.PackageManager;

namespace MCPForUnity.Editor.Services.ScriptQuality
{
    internal sealed class ScriptSelection
    {
        internal sealed class Source
        {
            internal string Path,
                Physical,
                Text,
                Hash,
                Guid;
        }

        internal Source[] Files { get; private set; }
        internal string[] Paths => Files.Select(file => file.Path).ToArray();
        internal const int MaxFileBytes = 2 * 1024 * 1024;
        internal static readonly UTF8Encoding Utf8 = new(false, true);

        internal static string Canonical(string path)
        {
            if (path == null || path.Length > 4096)
                throw new ArgumentException("invalid_script_path");
            path = path.Replace('\\', '/');
            string[] parts = path.Split('/');
            if (
                (parts[0] != "Assets" && parts[0] != "Packages")
                || !path.EndsWith(".cs", StringComparison.OrdinalIgnoreCase)
                || parts.Any(part => part.Length == 0 || part.StartsWith(".", StringComparison.Ordinal) || part.EndsWith("~", StringComparison.Ordinal))
                || path.IndexOfAny(new[] { ':', '*', '?', '"', '<', '>', '|', '\0', '\r', '\n' }) >= 0
                || path.IndexOf("/Resources/GameData/", StringComparison.OrdinalIgnoreCase) >= 0
            )
                throw new ArgumentException("invalid_script_path");
            return path;
        }

        internal static string Physical(string path)
        {
            if (path.StartsWith("Packages/", StringComparison.Ordinal))
            {
                var package = PackageInfo.FindForAssetPath(path);
                if (package == null || package.source == PackageSource.BuiltIn)
                    throw new InvalidOperationException("source_package_unavailable");
                return AssetPathUtility.ResolvePackageFilePath(path, package.assetPath, package.resolvedPath);
            }
            return SafePathUtility.ResolveWithinRoot(ScriptAnalysisHost.ProjectRoot, path);
        }

        internal static ScriptSelection Resolve(JObject options)
        {
            bool explicitPaths = options["paths"] != null;
            if (explicitPaths == (options["change_set_id"] != null))
                throw new ArgumentException("exactly_one_script_selection_required");
            Dictionary<string, string> expected = null;
            string[] paths;
            if (explicitPaths)
            {
                if (options["paths"] is not JArray list || list.Count < 1 || list.Count > 256 || list.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("script_selection_limit");
                paths = list.Values<string>().Select(Canonical).ToArray();
            }
            else
            {
                if (options["change_set_id"].Type != JTokenType.String)
                    throw new ArgumentException("invalid_change_set_id");
                expected = ScriptChangeSet.ReadSelection((string)options["change_set_id"]);
                paths = expected.Keys.Select(Canonical).ToArray();
            }
            if (paths.Distinct(StringComparer.OrdinalIgnoreCase).Count() != paths.Length)
                throw new ArgumentException("duplicate_script_path");
            var files = new List<Source>();
            long total = 0;
            foreach (string path in paths)
            {
                string physical = Physical(path);
                byte[] bytes = Read(physical, MaxFileBytes);
                total += bytes.Length;
                if (total > 16L * 1024 * 1024)
                    throw new InvalidDataException("script_selection_bytes_limit");
                string hash = Digest(bytes);
                if (expected != null && expected[path] != hash)
                    throw new InvalidOperationException("change_set_source_changed");
                string guid = null;
                string meta = SafePathUtility.ResolveWithinRoot(Path.GetDirectoryName(physical), physical + ".meta");
                if (File.Exists(meta))
                {
                    string contents = Utf8.GetString(Read(meta, 65536));
                    var match = Regex.Match(contents, "(?m)^guid: ([a-f0-9]{32})\\r?$", RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(100));
                    if (match.Success)
                        guid = match.Groups[1].Value;
                }
                files.Add(
                    new Source
                    {
                        Path = path,
                        Physical = physical,
                        Text = Text(bytes),
                        Hash = hash,
                        Guid = guid,
                    }
                );
            }
            return new ScriptSelection { Files = files.ToArray() };
        }

        internal static byte[] Read(string path, int maximum)
        {
            using var stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (stream.Length > maximum)
                throw new InvalidDataException("analysis_file_bytes_limit");
            byte[] bytes = new byte[(int)stream.Length];
            int offset = 0;
            while (offset < bytes.Length)
            {
                int count = stream.Read(bytes, offset, bytes.Length - offset);
                if (count == 0)
                    throw new EndOfStreamException();
                offset += count;
            }
            if (stream.ReadByte() != -1)
                throw new InvalidDataException("source_changed_during_read");
            return bytes;
        }

        internal static string Text(byte[] bytes) => Utf8.GetString(bytes, HasBom(bytes) ? 3 : 0, bytes.Length - (HasBom(bytes) ? 3 : 0));

        private static bool HasBom(byte[] bytes) => bytes.Length >= 3 && bytes[0] == 0xef && bytes[1] == 0xbb && bytes[2] == 0xbf;

        internal static string Digest(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return SourceFreshness.SourceSnapshot.Hex(sha.ComputeHash(bytes));
        }
    }
}
