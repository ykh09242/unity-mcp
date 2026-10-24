using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.Animation
{
    internal static class AnimationAssetAccess
    {
        internal static void Stable()
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
                throw new ArgumentException("Asset round trips require stable Edit mode.");
        }

        internal static string Path(JObject request, string field)
        {
            string path = AssetPathUtility.GetContainedAssetPath(Text(request, field));
            if (string.IsNullOrEmpty(path))
                throw new ArgumentException("A contained Assets path is required.");
            AssetPathUtility.GetFullAssetPath(path);
            return path;
        }

        internal static string Id(Object asset)
        {
            if (asset == null || !AssetDatabase.TryGetGUIDAndLocalFileIdentifier(asset, out string _, out long id) || id == 0)
                throw new ArgumentException("Persistent saved asset identity is required. Save and read again.");
            return id.ToString(CultureInfo.InvariantCulture);
        }

        internal static JToken Reference(Object asset)
        {
            if (asset == null)
                return JValue.CreateNull();
            if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(asset, out string guid, out long id) || id == 0)
                throw new ArgumentException("Object reference is not a persistent asset.");
            return new JObject
            {
                ["guid"] = guid,
                ["file_id"] = id.ToString(CultureInfo.InvariantCulture),
                ["path"] = AssetDatabase.GetAssetPath(asset),
                ["type"] = asset.GetType().FullName,
                ["name"] = asset.name,
            };
        }

        internal static Object ResolveReference(JToken token)
        {
            if (token?.Type == JTokenType.Null)
                return null;
            if (!(token is JObject reference))
                throw new ArgumentException("Object values require null or an exact asset {guid,file_id} reference.");
            string guid = Text(reference, "guid");
            string path = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrEmpty(path) || AssetPathUtility.GetContainedAssetPath(path) == null)
                throw new ArgumentException("Object GUID does not resolve to a contained asset.");
            AssetPathUtility.GetFullAssetPath(path);
            string id = reference["fileId"]?.ToString() ?? reference["file_id"]?.ToString();
            if (!long.TryParse(id, NumberStyles.Integer, CultureInfo.InvariantCulture, out long fileId) || fileId == 0)
                throw new ArgumentException("An exact nonzero file_id is required.");
            Object[] assets = AssetDatabase.LoadAllAssetsAtPath(path);
            if (assets.Length > 4096)
                throw new ArgumentException("Reference asset exceeds 4096 subassets.");
            Object result = assets.SingleOrDefault(asset => Id(asset) == fileId.ToString(CultureInfo.InvariantCulture));
            if (result == null)
                throw new ArgumentException("Object file_id is stale or does not belong to the GUID.");
            return result;
        }

        internal static string Revision(string path, Object[] assets)
        {
            string full = AssetPathUtility.GetFullAssetPath(path);
            if (new FileInfo(full).Length > 16777216)
                throw new ArgumentException("Round-trip source exceeds 16 MiB.");
            using (var sha = SHA256.Create())
            {
                var state = new StringBuilder(AssetDatabase.AssetPathToGUID(path) + ":" + Convert.ToBase64String(sha.ComputeHash(File.ReadAllBytes(full))));
                foreach (Object asset in assets.OrderBy(Id, StringComparer.Ordinal))
                {
                    state.Append('\n').Append(Id(asset)).Append(':').Append(EditorJsonUtility.ToJson(asset));
                    if (state.Length > 2097152)
                        throw new ArgumentException("Round-trip serialized state exceeds 2 MiB.");
                }
                return Convert.ToBase64String(sha.ComputeHash(Encoding.UTF8.GetBytes(state.ToString())));
            }
        }

        internal static void Expected(JObject request, string revision)
        {
            if (Text(request, "expectedRevision") != revision)
                throw new ArgumentException("Asset revision drifted. Read again before editing.");
        }

        internal static string Text(JObject request, string field)
        {
            if (
                request[field]?.Type != JTokenType.String
                || string.IsNullOrWhiteSpace(request[field].Value<string>())
                || request[field].Value<string>().Length > 1024
            )
                throw new ArgumentException(field + " must be a nonempty string of at most 1024 characters.");
            return request[field].Value<string>();
        }

        internal static int Integer(JObject request, string field, int fallback, int minimum, int maximum)
        {
            if (request[field] == null)
                return fallback;
            if (request[field].Type != JTokenType.Integer || request[field].Value<long>() < minimum || request[field].Value<long>() > maximum)
                throw new ArgumentException(field + " is outside its supported integer range.");
            return request[field].Value<int>();
        }

        internal static float Number(JToken token, bool tangent = false)
        {
            if (tangent && token?.Type == JTokenType.String && token.Value<string>() == "Infinity")
                return float.PositiveInfinity;
            if (tangent && token?.Type == JTokenType.String && token.Value<string>() == "-Infinity")
                return float.NegativeInfinity;
            if (token == null || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float))
                throw new ArgumentException("A finite numeric value is required.");
            float value = token.Value<float>();
            if (float.IsInfinity(value) || float.IsNaN(value))
                throw new ArgumentException("A finite float value is required.");
            return value;
        }

        internal static bool Boolean(JToken token)
        {
            if (token?.Type != JTokenType.Boolean)
                throw new ArgumentException("A JSON boolean is required.");
            return token.Value<bool>();
        }

        internal static void Keys(JObject request, params string[] allowed)
        {
            if (request.Properties().Any(property => !allowed.Contains(property.Name)))
                throw new ArgumentException("Unsupported round-trip change field.");
        }

        internal static object Page(JArray rows, JObject request, string revision, string field) =>
            Page(rows.Count, index => rows[index], request, revision, field);

        internal static object Page(int total, Func<int, JToken> describe, JObject request, string revision, string field)
        {
            int offset = Integer(request, "offset", 0, 0, total);
            int size = Integer(request, "pageSize", 50, 1, 100);
            if (offset > 0 && request["expectedRevision"] == null)
                throw new ArgumentException("Continuation pages require expected_revision from the first page.");
            if (request["expectedRevision"] != null)
                Expected(request, revision);
            var data = new JObject
            {
                ["revision"] = revision,
                ["total"] = total,
                ["offset"] = offset,
                ["next_offset"] = offset + size < total ? new JValue(offset + size) : JValue.CreateNull(),
                [field] = new JArray(Enumerable.Range(offset, Math.Min(size, total - offset)).Select(describe)),
            };
            if (data.ToString(Formatting.None).Length > 262144)
                throw new ArgumentException("Page exceeds output limit; use a smaller page_size.");
            return new SuccessResponse("Animation asset page read without mutation.", data);
        }
    }
}
