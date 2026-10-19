using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Shared admission and streaming observation hashes for read-only reference diagnostics.</summary>
    internal static class ReferenceDiagnosticUtility
    {
        internal static int Integer(JObject parameters, string name, int fallback, int maximum)
        {
            if (!PaginationBounds.TryRead(parameters[name], fallback, 1, maximum, name, out int result, out string error))
                throw new ArgumentException(error);
            return result;
        }

        internal static void Allowed(JObject parameters, params string[] names)
        {
            var allowed = new HashSet<string>(names, StringComparer.Ordinal);
            if (parameters.Properties().Any(property => !allowed.Contains(property.Name)))
                throw new ArgumentException("Unsupported option for this reference diagnostic scope.");
        }

        internal static bool Boolean(JObject parameters, string name)
        {
            var token = parameters[name];
            if (token == null)
                return false;
            if (token.Type != JTokenType.Boolean)
                throw new ArgumentException(name + " must be a boolean.");
            return token.Value<bool>();
        }

        internal static string Text(JObject parameters, string name, string fallback = null)
        {
            var token = parameters[name];
            if (token == null)
                return fallback;
            if (token.Type != JTokenType.String)
                throw new ArgumentException(name + " must be a string.");
            string value = token.Value<string>();
            if (string.IsNullOrEmpty(value) || value.Length > 2048 || value != value.Trim() || value.Any(char.IsControl))
                throw new ArgumentException(name + " requires a nonempty bounded string without controls/edge whitespace.");
            return value;
        }

        internal static string Path(JObject parameters, string name, bool folder = false, string fallback = null)
        {
            string input = Text(parameters, name, fallback);
            if (input == null || !(input == "Assets" || input.StartsWith("Assets/", StringComparison.Ordinal)) || input.IndexOf('\\') >= 0)
                throw new ArgumentException(name + " requires an explicit contained Assets path under Assets.");
            string path = AssetPathUtility.GetContainedAssetPath(input);
            if (!folder && (path == "Assets" || path.EndsWith(".meta", StringComparison.OrdinalIgnoreCase)))
                throw new ArgumentException(name + " must identify an asset file.");
            return path;
        }

        internal static string ExpectedRevision(JObject parameters)
        {
            string revision = Text(parameters, "referenceRevision");
            if (revision != null && !Regex.IsMatch(revision, "\\Amcp-ref-v1_[0-9a-f]{64}\\z", RegexOptions.CultureInvariant))
                throw new ArgumentException("referenceRevision must be an observed diagnostic revision.");
            return revision;
        }

        internal static void PageBytes(JObject data)
        {
            if (Encoding.UTF8.GetByteCount(data.ToString(Formatting.None)) > 512 * 1024)
                throw new ArgumentException("Reference diagnostic page exceeds 512 KiB; reduce pageSize.");
        }

        internal sealed class Observation : IDisposable
        {
            private readonly SHA256 hash = SHA256.Create();

            internal void Add(string text)
            {
                byte[] bytes = Encoding.UTF8.GetBytes(text ?? "<null>");
                byte[] length = Encoding.ASCII.GetBytes(bytes.Length.ToString(CultureInfo.InvariantCulture) + ":");
                hash.TransformBlock(length, 0, length.Length, length, 0);
                hash.TransformBlock(bytes, 0, bytes.Length, bytes, 0);
            }

            internal string Finish()
            {
                hash.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                return "mcp-ref-v1_" + BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant();
            }

            public void Dispose() => hash.Dispose();
        }
    }
}
