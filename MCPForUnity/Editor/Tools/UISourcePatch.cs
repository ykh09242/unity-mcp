using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    // Edits existing lexical value spans, never serializes the source document.
    internal static class UISourcePatch
    {
        private const int MaxBytes = 1048576;
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        private static readonly TimeSpan RegexTimeout = TimeSpan.FromSeconds(1);

        private static MatchCollection Matches(string source, string pattern) => Regex.Matches(source, pattern, RegexOptions.CultureInvariant, RegexTimeout);

        private static bool IsMatch(string source, string pattern) => Regex.IsMatch(source, pattern, RegexOptions.CultureInvariant, RegexTimeout);

        private sealed class Edit
        {
            public int Offset;
            public string Before;
            public string After;
            public string Property;
        }

        internal static object Execute(JObject request, string path)
        {
            if (request["preview"] != null && request["preview"].Type != JTokenType.Boolean)
                return new ErrorResponse("preview must be a Boolean.");
            bool preview = request.Value<bool?>("preview") ?? true;
            if (request["source_selector"]?.Type != JTokenType.String || request["source_changes"] is not JObject changes)
                return new ErrorResponse("source_selector and source_changes are required.");
            string selector = request.Value<string>("source_selector");
            if (string.IsNullOrEmpty(selector) || selector.Length > 256 || changes.Count < 1 || changes.Count > 32)
                return new ErrorResponse("Source patch requires a selector up to 256 characters and 1..32 changes.");
            foreach (var property in changes.Properties())
                if (property.Value.Type != JTokenType.String || property.Value.Value<string>().Length > 4096 || property.Name.Length > 128)
                    return new ErrorResponse("Source changes require string values up to 4096 characters and keys up to 128 characters.");
            string expected = request.Value<string>("expected_source_hash");
            if (!preview && (expected == null || !IsMatch(expected, "\\A[0-9a-fA-F]{64}\\z")))
                return new ErrorResponse("Apply requires expected_source_hash from preview.");

            string full = AssetPathUtility.GetFullAssetPath(path);
            if (!File.Exists(full))
                return new ErrorResponse("Source file does not exist.");
            byte[] original;
            byte[] updated;
            List<Edit> edits;
            string sourceHash;
            // .NET sharing coordinates cooperating file users from the hash check through writes.
            // This is not a cross-platform filesystem CAS or protection from noncooperating writers.
            using (
                var stream = new FileStream(full, FileMode.Open, preview ? FileAccess.Read : FileAccess.ReadWrite, preview ? FileShare.Read : FileShare.None)
            )
            {
                if (stream.Length > MaxBytes)
                    return new ErrorResponse("Source exceeds the 1 MiB patch limit.");
                original = new byte[(int)stream.Length];
                int read = 0;
                while (read < original.Length)
                {
                    int count = stream.Read(original, read, original.Length - read);
                    if (count == 0)
                        throw new IOException("Source read was incomplete.");
                    read += count;
                }
                sourceHash = Hash(original);
                if (expected != null && !string.Equals(expected, sourceHash, StringComparison.OrdinalIgnoreCase))
                    return new ErrorResponse("source_hash_conflict", new { source_hash = sourceHash });
                int bom = original.Length >= 3 && original[0] == 239 && original[1] == 187 && original[2] == 191 ? 3 : 0;
                string source = Utf8.GetString(original, bom, original.Length - bom);
                edits = path.EndsWith(".uxml", StringComparison.OrdinalIgnoreCase) ? Uxml(source, selector, changes) : Uss(source, selector, changes);
                var builder = new StringBuilder(source);
                foreach (var edit in edits.OrderByDescending(e => e.Offset))
                    builder.Remove(edit.Offset, edit.Before.Length).Insert(edit.Offset, edit.After);
                byte[] body = Utf8.GetBytes(builder.ToString());
                if (body.Length + bom > MaxBytes)
                    return new ErrorResponse("Patched source exceeds the 1 MiB patch limit.");
                updated = new byte[body.Length + bom];
                Array.Copy(original, updated, bom);
                Array.Copy(body, 0, updated, bom, body.Length);
                if (!preview)
                {
                    try
                    {
                        stream.Position = 0;
                        stream.Write(updated, 0, updated.Length);
                        stream.SetLength(updated.Length);
                        stream.Flush(true);
                    }
                    catch (IOException writeError)
                    {
                        try
                        {
                            stream.Position = 0;
                            stream.Write(original, 0, original.Length);
                            stream.SetLength(original.Length);
                            stream.Flush(true);
                        }
                        catch (IOException restoreError)
                        {
                            return new ErrorResponse(
                                "source_write_failed_restore_failed",
                                new
                                {
                                    path,
                                    source_hash = sourceHash,
                                    source_state = "unknown",
                                    write_error = writeError.Message,
                                    restore_error = restoreError.Message,
                                }
                            );
                        }
                        return new ErrorResponse("source_write_failed_original_restored", new { path, source_hash = sourceHash });
                    }
                }
            }
            if (!preview)
            {
                try
                {
                    AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                }
                catch (Exception importError)
                {
                    return new ErrorResponse(
                        "source_persisted_import_failed",
                        new
                        {
                            path,
                            source_hash = sourceHash,
                            result_hash = Hash(updated),
                            source_state = "persisted",
                            import_error = importError.Message,
                        }
                    );
                }
            }
            return new SuccessResponse(
                preview ? "Source patch preview; no file written." : "Source patch persisted and reimported; live UI refresh is separate.",
                new
                {
                    path,
                    preview,
                    source_hash = sourceHash,
                    result_hash = Hash(updated),
                    persistence = "source",
                    offset_unit = "utf16_code_units_without_bom",
                    reimport_requested = !preview,
                    live_view_updated = false,
                    changes = edits
                        .Select(e => new
                        {
                            property = e.Property,
                            offset = e.Offset,
                            before = e.Before,
                            after = e.After,
                        })
                        .ToArray(),
                }
            );
        }

        private static string Hash(byte[] bytes)
        {
            using var hash = SHA256.Create();
            return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private static List<Edit> Uxml(string source, string selector, JObject changes)
        {
            if (!IsMatch(selector, "\\A#[A-Za-z_][A-Za-z0-9_-]*\\z"))
                throw new ArgumentException("UXML source_selector supports only #name.");
            var document = new XmlDocument { XmlResolver = null };
            using (var input = XmlReader.Create(new StringReader(source), new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null }))
                document.Load(input);
            var elements = document.SelectNodes("//*").Cast<XmlElement>().ToList();
            var selected = elements.Where(e => e.GetAttribute("name") == selector.Substring(1)).ToList();
            if (selected.Count != 1)
                throw new ArgumentException("Source selector must match exactly one owned element.");
            XmlElement element = selected[0];
            for (XmlNode node = element; node is XmlElement; node = node.ParentNode)
                if (node.LocalName == "Instance" || node.LocalName == "Template")
                    throw new ArgumentException("Template-owned source is unsupported; patch its explicit source asset instead.");
            if (element.NamespaceURI != "UnityEngine.UIElements")
                throw new ArgumentException("Only UnityEngine.UIElements source elements are supported.");
            // Full XML validation above establishes well-formedness. This lexer retains original
            // attribute quotes/spacing/comments and maps opening tags to DOM document order.
            const string tokens =
                "<!--[\\s\\S]*?-->|<!\\[CDATA\\[[\\s\\S]*?\\]\\]>|<\\?[\\s\\S]*?\\?>|</?[^<>\\\"']*(?:(?:\\\"[^\\\"]*\\\"|'[^']*')[^<>\\\"']*)*>";
            var tags = Matches(source, tokens)
                .Cast<Match>()
                .Where(m => m.Value.Length > 1 && m.Value[1] != '!' && m.Value[1] != '?' && m.Value[1] != '/')
                .ToList();
            if (tags.Count != elements.Count)
                throw new ArgumentException("Unsupported XML source structure.");
            Match tag = tags[elements.IndexOf(element)];
            var attributes = Matches(tag.Value, "(?<name>[A-Za-z_][A-Za-z0-9_.:-]*)\\s*=\\s*(?<quote>[\\\"'])(?<value>[\\s\\S]*?)\\k<quote>")
                .Cast<Match>()
                .ToList();
            var edits = new List<Edit>();
            foreach (var property in changes.Properties())
            {
                if (property.Name != "text" && property.Name != "tooltip" && property.Name != "class")
                    throw new ArgumentException("UXML patch supports existing text, tooltip and class attributes only.");
                var attribute = attributes.SingleOrDefault(m => m.Groups["name"].Value == property.Name);
                if (attribute == null)
                    throw new ArgumentException("Attribute does not exist in the selected source element: " + property.Name);
                string value = property.Value.Value<string>();
                XmlConvert.VerifyXmlChars(value);
                string escaped = value
                    .Replace("&", "&amp;")
                    .Replace("<", "&lt;")
                    .Replace(">", "&gt;")
                    .Replace("\r", "&#13;")
                    .Replace("\n", "&#10;")
                    .Replace("\t", "&#9;");
                escaped = attribute.Groups["quote"].Value == "'" ? escaped.Replace("'", "&apos;") : escaped.Replace("\"", "&quot;");
                Group span = attribute.Groups["value"];
                edits.Add(
                    new Edit
                    {
                        Offset = tag.Index + span.Index,
                        Before = span.Value,
                        After = escaped,
                        Property = property.Name,
                    }
                );
            }
            return edits;
        }

        private static List<Edit> Uss(string source, string selector, JObject changes)
        {
            if (!IsMatch(selector, "\\A[.#]?[A-Za-z_][A-Za-z0-9_-]*\\z"))
                throw new ArgumentException("USS source_selector supports one exact simple type, .class or #name rule.");
            string masked = MaskCommentsAndStrings(source);
            var rules = new List<(int start, int end)>();
            int cursor = 0;
            while (cursor < masked.Length)
            {
                int open = masked.IndexOf('{', cursor);
                if (open < 0)
                {
                    if (!string.IsNullOrWhiteSpace(masked.Substring(cursor)))
                        throw new ArgumentException("Unsupported USS trailing source.");
                    break;
                }
                int close = masked.IndexOf('}', open + 1);
                if (close < 0 || masked.IndexOf('{', open + 1, close - open - 1) >= 0)
                    throw new ArgumentException("Nested or incomplete USS rules are unsupported.");
                string rule = masked.Substring(cursor, open - cursor).Trim();
                if (rule == selector)
                    rules.Add((open + 1, close));
                cursor = close + 1;
            }
            if (rules.Count != 1)
                throw new ArgumentException("USS selector must match exactly one rule.");
            var (start, end) = rules[0];
            string body = masked.Substring(start, end - start);
            var declarations = new Dictionary<string, (int start, int length)>(StringComparer.Ordinal);
            int part = 0;
            int parentheses = 0;
            for (int i = 0; i < body.Length; i++)
            {
                if (body[i] == '(')
                    parentheses++;
                if (body[i] == ')')
                    parentheses--;
                if (parentheses < 0)
                    throw new ArgumentException("Unbalanced USS value.");
                if (body[i] != ';' || parentheses != 0)
                    continue;
                string declaration = body.Substring(part, i - part);
                int colon = declaration.IndexOf(':');
                if (colon < 0)
                    throw new ArgumentException("Unsupported USS declaration.");
                string key = declaration.Substring(0, colon).Trim();
                if (!IsMatch(key, "\\A-?[A-Za-z_][A-Za-z0-9_-]*\\z") || declarations.ContainsKey(key))
                    throw new ArgumentException("Invalid or duplicate USS declaration.");
                int valueStart = part + colon + 1;
                while (valueStart < i && char.IsWhiteSpace(body[valueStart]))
                    valueStart++;
                int valueEnd = i;
                while (valueEnd > valueStart && char.IsWhiteSpace(body[valueEnd - 1]))
                    valueEnd--;
                declarations.Add(key, (start + valueStart, valueEnd - valueStart));
                part = i + 1;
            }
            if (parentheses != 0 || !string.IsNullOrWhiteSpace(body.Substring(part)))
                throw new ArgumentException("USS declarations must be complete and end with semicolons.");
            var edits = new List<Edit>();
            foreach (var property in changes.Properties())
            {
                if (!declarations.TryGetValue(property.Name, out var span))
                    throw new ArgumentException("USS declaration does not exist: " + property.Name);
                string before = source.Substring(span.start, span.length);
                // Keep comments around values as well as around rules. An embedded comment in a
                // value is refused because replacing it would silently remove authored source.
                if (before.Contains("/*"))
                    throw new ArgumentException("Comments inside a declaration value are unsupported.");
                string value = property.Value.Value<string>();
                if (
                    string.IsNullOrWhiteSpace(value)
                    || value.IndexOfAny(new[] { '{', '}', ';', '\r', '\n', '\0' }) >= 0
                    || value.Contains("/*")
                    || value.Contains("*/")
                )
                    throw new ArgumentException("USS replacement must be one nonempty declaration value.");
                string replacementMask = MaskCommentsAndStrings(value);
                int depth = 0;
                foreach (char c in replacementMask)
                {
                    if (c == '(')
                        depth++;
                    if (c == ')')
                        depth--;
                    if (depth < 0)
                        throw new ArgumentException("Unbalanced USS replacement.");
                }
                if (depth != 0)
                    throw new ArgumentException("Unbalanced USS replacement.");
                edits.Add(
                    new Edit
                    {
                        Offset = span.start,
                        Before = before,
                        After = value,
                        Property = property.Name,
                    }
                );
            }
            return edits;
        }

        private static string MaskCommentsAndStrings(string source)
        {
            char[] masked = source.ToCharArray();
            for (int i = 0; i < source.Length; i++)
            {
                if (source[i] == '/' && i + 1 < source.Length && source[i + 1] == '*')
                {
                    int end = source.IndexOf("*/", i + 2, StringComparison.Ordinal);
                    if (end < 0)
                        throw new ArgumentException("Unclosed USS comment.");
                    while (i < end + 2)
                        masked[i++] = ' ';
                    i--;
                }
                else if (source[i] == '\'' || source[i] == '"')
                {
                    char quote = source[i];
                    // Keep a non-whitespace placeholder at each quoted-string position so value
                    // boundaries include quotes and quoted spaces.
                    masked[i] = 'x';
                    bool closed = false;
                    while (++i < source.Length)
                    {
                        masked[i] = 'x';
                        if (source[i] == '\\' && i + 1 < source.Length)
                        {
                            masked[++i] = 'x';
                            continue;
                        }
                        if (source[i] == quote)
                        {
                            closed = true;
                            break;
                        }
                    }
                    if (!closed)
                        throw new ArgumentException("Unclosed USS string.");
                }
                else if (source[i] == '\\')
                    throw new ArgumentException("USS escapes outside strings are unsupported.");
            }
            return new string(masked);
        }
    }
}
