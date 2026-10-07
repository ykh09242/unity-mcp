using System;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Read-only AssetDatabase bundle assignments, without loading or building bundles.</summary>
    internal static class AssetBundleInspection
    {
        private const int MaxEntries = 65536;
        private const int MaxPageSize = 100;

        internal static object Inspect(JObject parameters)
        {
            if (
                !PaginationBounds.TryRead(parameters["pageSize"] ?? parameters["page_size"], 50, 1, MaxPageSize, "pageSize", out int size, out string error)
                || !PaginationBounds.TryRead(parameters["pageNumber"] ?? parameters["page_number"], 1, 1, int.MaxValue, "pageNumber", out int page, out error)
            )
                return new ErrorResponse(error);

            string action = parameters.Value<string>("action")?.ToLowerInvariant();
            string bundle = parameters.Value<string>("bundleName") ?? parameters.Value<string>("bundle_name");
            string[] names = AssetDatabase.GetAllAssetBundleNames();
            if (names.Length > MaxEntries || names.Any(name => name == null || name.Length > 2048))
                return new ErrorResponse($"Bundle assignment inspection exceeds {MaxEntries} names.");
            string[] entries;
            bool recursive = false;
            if (action == "list_asset_bundles")
            {
                entries = names;
            }
            else
            {
                if (string.IsNullOrWhiteSpace(bundle) || bundle.Length > 512 || !names.Contains(bundle, StringComparer.Ordinal))
                    return new ErrorResponse("'bundleName' must name a registered AssetDatabase bundle (maximum 512 characters).");
                if (action == "get_bundle_assets")
                    entries = AssetDatabase.GetAssetPathsFromAssetBundle(bundle);
                else
                {
                    JToken recursiveToken = parameters["recursive"];
                    if (recursiveToken != null && recursiveToken.Type != JTokenType.Boolean)
                        return new ErrorResponse("'recursive' must be a boolean.");
                    recursive = recursiveToken?.Value<bool>() ?? false;
                    entries = AssetDatabase.GetAssetBundleDependencies(bundle, recursive);
                }
            }
            if (entries.Length > MaxEntries || entries.Any(entry => entry == null || entry.Length > 2048))
                return new ErrorResponse($"Bundle assignment inspection exceeds its {MaxEntries}-entry or 2048-character entry budget.");
            Array.Sort(entries, StringComparer.Ordinal);
            long start = PaginationBounds.StartIndex(page, size);
            string[] results = start >= entries.Length ? Array.Empty<string>() : entries.Skip((int)start).Take(size).ToArray();
            if (Encoding.UTF8.GetByteCount(Newtonsoft.Json.JsonConvert.SerializeObject(results)) > 512 * 1024)
                return new ErrorResponse("Result byte budget exceeded (512 KiB); request a smaller pageSize.");
            return new SuccessResponse(
                "Read AssetDatabase bundle assignment metadata.",
                new
                {
                    source = "AssetDatabase assignments (not built bundle contents)",
                    bundleName = action == "list_asset_bundles" ? null : bundle,
                    recursive,
                    entries = results,
                    totalEntries = entries.Length,
                    pageSize = size,
                    pageNumber = page,
                    maxPageSize = MaxPageSize,
                    hasMore = start + results.Length < entries.Length,
                }
            );
        }
    }
}
