using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    // A request-local, data-only map. It neither stores Unity objects nor calls providers.
    internal sealed class UISnapshotIdentityMap
    {
        private readonly Dictionary<string, string> ids = new(StringComparer.Ordinal);
        private readonly HashSet<string> visited = new(StringComparer.Ordinal);

        internal static UISnapshotIdentityMap Parse(JToken value, int maxNodes)
        {
            try
            {
                if (!(value is JArray entries) || entries.Count < 1 || entries.Count > 999 || entries.Count > maxNodes - 1)
                    throw new ArgumentException();
                var result = new UISnapshotIdentityMap();
                var uniqueIds = new HashSet<string>(StringComparer.Ordinal);
                int characters = 0;
                foreach (var item in entries)
                {
                    if (
                        !(item is JObject entry)
                        || entry.Count != 2
                        || entry["element_path"]?.Type != JTokenType.String
                        || entry["id"]?.Type != JTokenType.String
                    )
                        throw new ArgumentException();
                    string path = entry.Value<string>("element_path"),
                        id = entry.Value<string>("id");
                    if (string.IsNullOrEmpty(path) || path.Length > 512 || path[0] != '/' || string.IsNullOrEmpty(id) || id.Length > 128)
                        throw new ArgumentException();
                    characters += path.Length + id.Length;
                    if (characters > 65536)
                        throw new ArgumentException();
                    string[] pieces = path.Split('/');
                    if (pieces.Length > 128)
                        throw new ArgumentException();
                    for (int i = 1; i < pieces.Length; i++)
                    {
                        string part = pieces[i];
                        if (part.Length < 1 || part.Length > 3 || (part.Length > 1 && part[0] == '0'))
                            throw new ArgumentException();
                        foreach (char character in part)
                            if (character < '0' || character > '9')
                                throw new ArgumentException();
                    }
                    foreach (char character in id)
                        if (
                            !(
                                (character >= 'a' && character <= 'z')
                                || (character >= 'A' && character <= 'Z')
                                || (character >= '0' && character <= '9')
                                || character == '.'
                                || character == '_'
                                || character == ':'
                                || character == '-'
                            )
                        )
                            throw new ArgumentException();
                    if (!uniqueIds.Add(id) || result.ids.ContainsKey(path))
                        throw new ArgumentException();
                    result.ids.Add(path, id);
                }
                return result;
            }
            catch
            {
                throw new ArgumentException("identity_map is invalid or exceeds its path, ID, node or character bounds.");
            }
        }

        internal bool TryGetSegment(string currentPath, out string segment)
        {
            if (ids.TryGetValue(currentPath, out string id))
            {
                segment = "@id:" + Uri.EscapeDataString(id);
                return true;
            }
            segment = null;
            return false;
        }

        internal void MarkVisited(string currentPath)
        {
            if (ids.ContainsKey(currentPath))
                visited.Add(currentPath);
        }

        internal void EnsureAllVisited()
        {
            if (visited.Count != ids.Count)
                throw new ArgumentException("identity_map contains a path outside the captured hierarchy.");
        }
    }
}
