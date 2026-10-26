using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.AssetGen
{
    /// <summary>Admission before a JToken graph is allocated. Byte and depth limits alone do not bound a wide graph.</summary>
    internal static class BoundedJson
    {
        internal sealed class Limits
        {
            internal int MaxDepth = 64;
            internal int MaxTokens = 262144;
            internal int MaxProperties = 65536;
            internal int MaxContainerItems = 65536;
            internal int MaxCharacters = 64 * 1024 * 1024;
        }

        internal static Limits ModelLimits(int depth = 64)
        {
            // Editor process configuration only; provider responses and tool arguments cannot raise limits.
            int tokens = 2000000;
            if (
                int.TryParse(Environment.GetEnvironmentVariable("MCPFORUNITY_ASSETGEN_MODEL_JSON_TOKENS"), out int configured)
                && configured >= 10000
                && configured <= 16000000
            )
                tokens = configured;
            return new Limits
            {
                MaxDepth = depth,
                MaxTokens = tokens,
                MaxProperties = tokens / 2,
                MaxContainerItems = tokens / 2,
            };
        }

        internal static JObject ParseObject(string text, Limits limits = null)
        {
            limits ??= new Limits();
            Validate(text, limits);
            using var source = new StringReader(text);
            using var reader = new JsonTextReader(source) { MaxDepth = limits.MaxDepth, DateParseHandling = DateParseHandling.None };
            return JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
        }

        internal static void Validate(string text, Limits limits)
        {
            if (text == null || text.Length > limits.MaxCharacters)
                throw new InvalidDataException("Asset JSON exceeds the metadata character limit.");
            using var source = new StringReader(text);
            using var reader = new JsonTextReader(source) { MaxDepth = limits.MaxDepth, DateParseHandling = DateParseHandling.None };
            var containers = new Stack<(bool array, int items)>();
            int tokens = 0;
            int properties = 0;
            bool root = false;
            while (reader.Read())
            {
                if (++tokens > limits.MaxTokens)
                    throw new InvalidDataException("Asset JSON exceeds the token budget. Simplify metadata or adjust the Editor's model JSON token budget.");
                JsonToken token = reader.TokenType;
                if (token == JsonToken.Comment || token == JsonToken.Undefined || token == JsonToken.StartConstructor)
                    throw new InvalidDataException("Asset JSON contains unsupported non-JSON syntax.");
                if (!root)
                {
                    if (token != JsonToken.StartObject)
                        throw new InvalidDataException("Asset JSON must contain one object.");
                    root = true;
                }
                else if (containers.Count == 0)
                    throw new InvalidDataException("Unexpected content after asset JSON.");
                if (token == JsonToken.EndObject || token == JsonToken.EndArray)
                {
                    containers.Pop();
                    continue;
                }
                if (token == JsonToken.PropertyName && ++properties > limits.MaxProperties)
                    throw new InvalidDataException("Asset JSON exceeds the property budget.");
                if (containers.Count > 0)
                {
                    var parent = containers.Pop();
                    if ((parent.array || token == JsonToken.PropertyName) && ++parent.items > limits.MaxContainerItems)
                        throw new InvalidDataException("Asset JSON exceeds the per-container item budget.");
                    containers.Push(parent);
                }
                if (token == JsonToken.StartArray || token == JsonToken.StartObject)
                    containers.Push((token == JsonToken.StartArray, 0));
            }
            if (!root || containers.Count != 0)
                throw new InvalidDataException("Asset JSON is empty or truncated.");
        }
    }
}
