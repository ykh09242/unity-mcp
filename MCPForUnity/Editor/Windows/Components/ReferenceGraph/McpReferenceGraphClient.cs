using System;
using System.Linq;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Windows.Components.ReferenceGraph
{
    internal static class McpReferenceGraphClient
    {
        internal static string Identity()
        {
            using var sha = SHA256.Create();
            string material =
                HttpEndpointUtility.GetBaseUrl()
                + "\n"
                + HttpEndpointUtility.IsRemoteScope()
                + "\n"
                + EditorConfigurationCache.Instance.UseHttpTransport
                + "\n"
                + ReferenceGraphJournal.InstanceId
                + "\n"
                + ProjectIdentityUtility.GetProjectHash()
                + "\n"
                + string.Join("\n", HttpEndpointUtility.GetAuthHeaders().OrderBy(p => p.Key).Select(p => p.Key + ":" + p.Value));
            return Convert.ToBase64String(sha.ComputeHash(Encoding.UTF8.GetBytes(material)));
        }

        internal static UnauthorizedAccessException AuthorizationFailure(int statusCode) =>
            new UnauthorizedAccessException(
                statusCode == 401
                    ? "Authentication is required. Check the account or local server connection in Connect."
                    : "This account cannot perform the action or read that source. Check account permissions and the server's external-folder grant."
            );

        internal static async Task<JObject> Send(string action, JObject options, CancellationToken cancellation)
        {
            if (!EditorConfigurationCache.Instance.UseHttpTransport)
                throw new InvalidOperationException("Use an HTTP connection in Connect to open the server-owned graph panel.");
            string endpoint = HttpEndpointUtility.GetBaseUrl();
            bool remote = HttpEndpointUtility.IsRemoteScope();
            string error;
            bool allowed = remote
                ? HttpEndpointUtility.IsRemoteUrlAllowed(endpoint, out error)
                : HttpEndpointUtility.IsHttpLocalUrlAllowedForLaunch(endpoint, out error);
            if (!allowed)
                throw new InvalidOperationException("The configured server endpoint is not allowed. Check Connect settings.");
            var uri = new Uri(endpoint.TrimEnd('/') + "/api/reference-graph");
            if (!string.IsNullOrEmpty(uri.UserInfo) || !string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment))
                throw new InvalidOperationException("The graph endpoint must not contain credentials, query parameters or fragments.");
            string identity = Identity();
            var body = new JObject
            {
                ["action"] = action,
                ["options"] = options,
                ["instance"] = ProjectIdentityUtility.GetProjectHash(),
                ["editor_id"] = ReferenceGraphJournal.InstanceId,
                ["project_id"] = ProjectIdentityUtility.GetProjectHash(),
            };
            using var handler = new HttpClientHandler { AllowAutoRedirect = false, UseProxy = remote };
            using var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
            using var request = new HttpRequestMessage(HttpMethod.Post, uri)
            {
                Content = new StringContent(body.ToString(Formatting.None), Encoding.UTF8, "application/json"),
            };
            foreach (var header in HttpEndpointUtility.GetAuthHeaders())
                request.Headers.Add(header.Key, header.Value);
            using var response = await client.SendAsync(request, HttpCompletionOption.ResponseContentRead, cancellation);
            cancellation.ThrowIfCancellationRequested();
            if (Identity() != identity)
                throw new OperationCanceledException("Connection identity changed.");
            if ((int)response.StatusCode == 401 || (int)response.StatusCode == 403)
                throw AuthorizationFailure((int)response.StatusCode);
            if (!response.IsSuccessStatusCode)
                throw new InvalidOperationException(
                    (int)response.StatusCode == 409 ? "The Editor or destination version changed. Refresh and reload the item before applying it."
                    : (int)response.StatusCode == 400
                        ? "Check the configuration JSON and destination version. External sources require explicit read-access approval."
                    : "The graph service is unavailable (HTTP " + (int)response.StatusCode + "). Check the server and Editor connection, then refresh."
                );
            JObject result = JObject.Parse(await response.Content.ReadAsStringAsync());
            if (result.Value<bool?>("success") != true)
                throw new InvalidOperationException("The reference graph is unavailable. Check the server connection and retry.");
            return (JObject)result["data"];
        }
    }
}
