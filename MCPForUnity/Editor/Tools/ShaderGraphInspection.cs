using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Bounded structural inspection only; never instantiates serialized Shader Graph types.</summary>
    internal static class ShaderGraphInspection
    {
        private const int MaxBytes = 4 * 1024 * 1024;
        private const int MaxObjects = 16384;
        private const int MaxNodes = 4096;
        private const int MaxEdges = 8192;
        private const int MaxPageSize = 100;
        private static readonly Regex ObjectId = new Regex(@"\A[0-9a-fA-F]{32}\z", RegexOptions.CultureInvariant);

        internal static object Inspect(JObject parameters)
        {
            try
            {
                if (
                    !PaginationBounds.TryRead(parameters["pageSize"] ?? parameters["page_size"], 50, 1, MaxPageSize, "pageSize", out int size, out string error)
                    || !PaginationBounds.TryRead(
                        parameters["pageNumber"] ?? parameters["page_number"],
                        1,
                        1,
                        int.MaxValue,
                        "pageNumber",
                        out int page,
                        out error
                    )
                )
                    return new ErrorResponse(error);
                if (parameters["path"]?.Type != JTokenType.String)
                    return new ErrorResponse("inspect_graph requires a string .shadergraph asset path.");
                string path = AssetPathUtility.GetContainedAssetPath(parameters.Value<string>("path"));
                if (!string.Equals(Path.GetExtension(path), ".shadergraph", StringComparison.OrdinalIgnoreCase))
                    return new ErrorResponse("inspect_graph requires a project-owned .shadergraph file path inside Assets.");
                string fullPath = AssetPathUtility.GetFullAssetPath(path);
                string contents = ReadBounded(fullPath);
                var objects = new Dictionary<string, JObject>(StringComparer.Ordinal);
                JObject graph = null;
                using (var reader = new BudgetJsonReader(new StringReader(contents)))
                {
                    while (reader.Read())
                    {
                        if (reader.TokenType != JsonToken.StartObject)
                            throw new FormatException("Expected a MultiJSON object stream.");
                        if (objects.Count >= MaxObjects)
                            throw new FormatException($"Object budget exceeded ({MaxObjects}).");
                        var value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                        if (
                            graph == null
                            && (value["m_Type"]?.Type != JTokenType.String || value.Value<string>("m_Type") != "UnityEditor.ShaderGraph.GraphData")
                        )
                            return new ErrorResponse("Unsupported Shader Graph format: expected modern MultiJSON GraphData.");
                        string id = ReadId(value["m_ObjectId"]);
                        ReadText(value, "m_Type", true);
                        if (objects.ContainsKey(id))
                            throw new FormatException("Duplicate serialized object ID.");
                        objects.Add(id, value);
                        graph ??= value;
                    }
                }
                if (graph == null || ReadText(graph, "m_Type", true) != "UnityEditor.ShaderGraph.GraphData")
                    return new ErrorResponse("Unsupported Shader Graph format: expected modern MultiJSON GraphData.");
                if (
                    graph["m_SGVersion"]?.Type != JTokenType.Integer
                    || !PaginationBounds.TryRead(graph["m_SGVersion"], -1, 0, 3, "m_SGVersion", out int version, out _)
                    || version < 0
                )
                    return new ErrorResponse("Unsupported Shader Graph format: supported GraphData m_SGVersion values are 0 through 3.");
                var nodes = ReadReferences(graph, "m_Nodes", objects, true);
                var properties = ReadReferences(graph, "m_Properties", objects, false);
                var keywords = ReadReferences(graph, "m_Keywords", objects, false);
                var edges = graph["m_Edges"] as JArray ?? throw new FormatException("m_Edges must be an array.");
                if (edges.Count > MaxEdges)
                    throw new FormatException($"Edge budget exceeded ({MaxEdges}).");
                var nodeIds = new HashSet<string>(nodes.Select(node => ReadId(node["m_ObjectId"])), StringComparer.Ordinal);
                // Validate every edge, including those outside the requested page.
                foreach (JToken edge in edges)
                {
                    if (!(edge is JObject))
                        throw new FormatException("Each graph edge must be an object.");
                    ReadEndpoint(edge["m_OutputSlot"], nodeIds, out _, out _);
                    ReadEndpoint(edge["m_InputSlot"], nodeIds, out _, out _);
                }
                long start = PaginationBounds.StartIndex(page, size);
                int offset = start > int.MaxValue ? int.MaxValue : (int)start;
                var data = new
                {
                    path,
                    format = "multi_json",
                    graphVersion = version,
                    objectCount = objects.Count,
                    nodeCount = nodes.Count,
                    edgeCount = edges.Count,
                    propertyCount = properties.Count,
                    keywordCount = keywords.Count,
                    nodes = nodes.Skip(offset).Take(size).Select(Summarize).ToArray(),
                    edges = edges
                        .Skip(offset)
                        .Take(size)
                        .Select(edge => new
                        {
                            output = SummarizeEndpoint(edge["m_OutputSlot"], nodeIds),
                            input = SummarizeEndpoint(edge["m_InputSlot"], nodeIds),
                        })
                        .ToArray(),
                    properties = properties.Skip(offset).Take(size).Select(Summarize).ToArray(),
                    keywords = keywords.Skip(offset).Take(size).Select(Summarize).ToArray(),
                    pageSize = size,
                    pageNumber = page,
                    maxPageSize = MaxPageSize,
                    hasMore = new[] { nodes.Count, edges.Count, properties.Count, keywords.Count }.Any(count => start + size < count),
                };
                if (Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(data)) > 512 * 1024)
                    throw new FormatException("Result byte budget exceeded (512 KiB); request a smaller pageSize.");
                return new SuccessResponse("Inspected serialized Shader Graph structure; package runtime validation was not performed.", data);
            }
            catch (Exception ex)
                when (ex is IOException
                    || ex is UnauthorizedAccessException
                    || ex is ArgumentException
                    || ex is InvalidOperationException
                    || ex is FormatException
                    || ex is JsonException
                )
            {
                return new ErrorResponse($"Cannot inspect Shader Graph: {ex.Message}");
            }
        }

        private static string ReadBounded(string fullPath)
        {
            using var stream = new FileStream(fullPath, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (stream.Length > MaxBytes)
                throw new FormatException($"File size budget exceeded ({MaxBytes} bytes).");
            byte[] bytes = new byte[(int)stream.Length];
            int count = 0;
            while (count < bytes.Length)
            {
                int read = stream.Read(bytes, count, bytes.Length - count);
                if (read == 0)
                    throw new IOException("Shader Graph changed while reading.");
                count += read;
            }
            if (stream.ReadByte() != -1)
                throw new IOException("Shader Graph changed while reading.");
            return new UTF8Encoding(false, true).GetString(bytes).TrimStart('\ufeff');
        }

        private static List<JObject> ReadReferences(JObject graph, string key, Dictionary<string, JObject> objects, bool required)
        {
            var references = graph[key] as JArray;
            if (references == null)
            {
                if (!required && graph[key] == null)
                    return new List<JObject>();
                throw new FormatException($"{key} must be an array.");
            }
            if (references.Count > MaxNodes)
                throw new FormatException($"{key} reference budget exceeded ({MaxNodes}).");
            var results = new List<JObject>(references.Count);
            var ids = new HashSet<string>(StringComparer.Ordinal);
            foreach (JToken reference in references)
            {
                if (!(reference is JObject item))
                    throw new FormatException($"{key} requires object references.");
                string id = ReadId(item["m_Id"]);
                if (!ids.Add(id) || !objects.TryGetValue(id, out JObject value))
                    throw new FormatException($"{key} contains a duplicate or unresolved object reference.");
                ReadText(value, "m_Name", false);
                ReadText(value, "m_DefaultReferenceName", false);
                ReadText(value, "m_OverrideReferenceName", false);
                results.Add(value);
            }
            return results;
        }

        private static string ReadId(JToken token)
        {
            if (token?.Type != JTokenType.String || !ObjectId.IsMatch(token.Value<string>()))
                throw new FormatException("Expected a 32-character hexadecimal serialized object ID.");
            return token.Value<string>();
        }

        private static string ReadText(JObject value, string key, bool required)
        {
            JToken token = value[key];
            if (!required && (token == null || token.Type == JTokenType.Null))
                return null;
            if (token?.Type != JTokenType.String || token.Value<string>().Length > 512 || (required && string.IsNullOrWhiteSpace(token.Value<string>())))
                throw new FormatException($"{key} must be a string of at most 512 characters.");
            return token.Value<string>();
        }

        private static JObject Summarize(JObject value) =>
            new JObject
            {
                ["id"] = ReadId(value["m_ObjectId"]),
                ["type"] = ReadText(value, "m_Type", true),
                ["name"] = ReadText(value, "m_Name", false),
                ["referenceName"] = ReadText(value, "m_OverrideReferenceName", false) ?? ReadText(value, "m_DefaultReferenceName", false),
            };

        private static void ReadEndpoint(JToken token, HashSet<string> nodes, out string id, out int slotId)
        {
            if (!(token is JObject endpoint) || !(endpoint["m_Node"] is JObject node))
                throw new FormatException("Edge endpoint must contain an m_Node object reference.");
            id = ReadId(node["m_Id"]);
            if (!nodes.Contains(id))
                throw new FormatException("Edge endpoint refers to an unresolved graph node.");
            JToken slot = endpoint["m_SlotId"];
            if (slot?.Type != JTokenType.Integer || !PaginationBounds.TryRead(slot, -1, 0, int.MaxValue, "m_SlotId", out slotId, out _))
                throw new FormatException("Edge endpoint m_SlotId must be a nonnegative integer.");
        }

        private static JObject SummarizeEndpoint(JToken token, HashSet<string> nodes)
        {
            ReadEndpoint(token, nodes, out string id, out int slotId);
            return new JObject { ["nodeId"] = id, ["slotId"] = slotId };
        }

        private sealed class BudgetJsonReader : JsonTextReader
        {
            private int tokens;

            internal BudgetJsonReader(TextReader reader)
                : base(reader)
            {
                MaxDepth = 64;
                SupportMultipleContent = true;
                DateParseHandling = DateParseHandling.None;
            }

            public override bool Read()
            {
                bool result = base.Read();
                if (result && (++tokens > 262144 || TokenType == JsonToken.Comment))
                    throw new JsonReaderException("JSON token budget exceeded or unsupported JSON comments.");
                return result;
            }
        }
    }
}
