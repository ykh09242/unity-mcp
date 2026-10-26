using System;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen.Http;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.AssetGen.Providers
{
    /// <summary>
    /// Sketchfab marketplace provider. Search/preview are read-only GETs returning the raw API
    /// JSON to the caller; <see cref="ResolveDownloadUrlAsync"/> hits the model download endpoint
    /// and returns the signed glTF archive (.zip) URL the job manager will fetch. Auth uses the
    /// "Token &lt;key&gt;" scheme; the key is supplied per call and never logged.
    /// </summary>
    public sealed class SketchfabAdapter : IMarketplaceProviderAdapter
    {
        private const string SearchEndpoint = "https://api.sketchfab.com/v3/search";

        // Sketchfab model URLs use a 32-character hexadecimal UID. Normalize ASCII
        // uppercase before attaching a stored token or persisting a marketplace job.
        internal static bool TryNormalizeModelUid(string uid, out string canonicalUid)
        {
            canonicalUid = null;
            if (uid == null || uid.Length != 32)
                return false;
            foreach (char c in uid)
                if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')))
                    return false;
            canonicalUid = uid.ToLowerInvariant();
            return true;
        }

        private static string ModelUrl(string uid, bool download)
        {
            if (!TryNormalizeModelUid(uid, out string canonicalUid))
                throw new ArgumentException("'uid' must be a 32-character hexadecimal Sketchfab model ID.", nameof(uid));
            string expectedPath = "/v3/models/" + Uri.EscapeDataString(canonicalUid) + (download ? "/download" : string.Empty);
            var uri = new Uri("https://api.sketchfab.com" + expectedPath, UriKind.Absolute);
            if (
                uri.Scheme != Uri.UriSchemeHttps
                || uri.IdnHost != "api.sketchfab.com"
                || !uri.IsDefaultPort
                || uri.UserInfo.Length != 0
                || uri.AbsolutePath != expectedPath
                || uri.Query.Length != 0
                || uri.Fragment.Length != 0
            )
                throw new InvalidOperationException("Sketchfab model route is invalid.");
            return uri.AbsoluteUri;
        }

        public string Id => "sketchfab";

        public async Task<string> SearchAsync(
            string query,
            string categories,
            bool downloadable,
            int? count,
            string cursor,
            string apiKey,
            IHttpTransport http,
            CancellationToken ct
        )
        {
            if (http == null)
                throw new ArgumentNullException(nameof(http));
            string url =
                SearchEndpoint + "?type=models&downloadable=" + (downloadable ? "true" : "false") + "&q=" + Uri.EscapeDataString(query ?? string.Empty);
            if (!string.IsNullOrEmpty(categories))
                url += "&categories=" + Uri.EscapeDataString(categories);
            if (count.HasValue)
                url += "&count=" + count.Value;
            if (!string.IsNullOrEmpty(cursor))
                url += "&cursor=" + Uri.EscapeDataString(cursor);
            var spec = new HttpRequestSpec { Method = "GET", Url = url };
            spec.Headers["Authorization"] = "Token " + apiKey;
            HttpResult res = await http.SendAsync(spec, ct);
            // The raw response carries pagination (`cursors.next` / `next`) for the caller to page.
            return RawOk(res, apiKey, "search");
        }

        public async Task<string> PreviewAsync(string uid, string apiKey, IHttpTransport http, CancellationToken ct)
        {
            string url = ModelUrl(uid, false);
            if (http == null)
                throw new ArgumentNullException(nameof(http));
            var spec = new HttpRequestSpec { Method = "GET", Url = url };
            spec.Headers["Authorization"] = "Token " + apiKey;
            HttpResult res = await http.SendAsync(spec, ct);
            return RawOk(res, apiKey, "preview");
        }

        public async Task<string> ResolveDownloadUrlAsync(string uid, string apiKey, IHttpTransport http, CancellationToken ct)
        {
            string requestUrl = ModelUrl(uid, true);
            if (http == null)
                throw new ArgumentNullException(nameof(http));
            var spec = new HttpRequestSpec { Method = "GET", Url = requestUrl };
            spec.Headers["Authorization"] = "Token " + apiKey;

            HttpResult res = await http.SendAsync(spec, ct);
            JObject json = ParseOk(res, apiKey, "download");

            string url = json["gltf"]?["url"]?.ToString();
            if (string.IsNullOrEmpty(url))
            {
                throw new Exception(SecretRedactor.Scrub($"Sketchfab download returned no gltf url for '{uid}': {ProviderHttp.Truncate(res?.Text)}", apiKey));
            }
            return url;
        }

        private static string RawOk(HttpResult res, string apiKey, string phase)
        {
            string text = ProviderHttp.BodyText(res);

            bool ok = res?.Ok == true;
            if (!ok)
                throw new Exception(SecretRedactor.Scrub($"Sketchfab {phase} failed (status={res?.Status}): {ProviderHttp.Truncate(text)}", apiKey));
            return text ?? string.Empty;
        }

        private static JObject ParseOk(HttpResult res, string apiKey, string phase)
        {
            string text = ProviderHttp.BodyText(res);

            JObject json = null;
            if (!string.IsNullOrEmpty(text))
            {
                try
                {
                    json = BoundedJson.ParseObject(text);
                }
                catch (Newtonsoft.Json.JsonException) { /* non-JSON */ }
            }

            bool ok = res?.Ok == true;
            if (!ok)
            {
                string detail = json?["detail"]?.ToString() ?? json?["error"]?.ToString() ?? ProviderHttp.Truncate(text);
                throw new Exception(SecretRedactor.Scrub($"Sketchfab {phase} failed (status={res?.Status}): {detail}", apiKey));
            }
            return json ?? new JObject();
        }
    }
}
