using System;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Services.AssetGen.Http
{
    /// <summary>
    /// Downloads artifacts using validated IP literals, with the original Host for TLS/SNI.
    /// UnityWebRequest cannot pin DNS resolution, so it must not handle provider result URLs.
    /// </summary>
    internal sealed class AssetDownloadTransport
    {
        internal const int MaxRedirects = 5;
        internal const int MaxDownloadBytes = 512 * 1024 * 1024;
        private readonly Func<string, Task<IPAddress[]>> _resolve;
        private readonly Func<Uri, IPAddress, CancellationToken, Task<HttpResult>> _send;

        internal AssetDownloadTransport(
            Func<string, Task<IPAddress[]>> resolve = null,
            Func<Uri, IPAddress, CancellationToken, Task<HttpResult>> send = null)
        {
            _resolve = resolve ?? Dns.GetHostAddressesAsync;
            _send = send ?? SendPinnedAsync;
        }

        internal async Task<HttpResult> DownloadAsync(string provider, string url, CancellationToken ct)
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeout.CancelAfter(TimeSpan.FromMinutes(2));
            CancellationToken token = timeout.Token;
            Uri uri = AssetDownloadPolicy.RequireAllowedUrl(provider, url);
            for (int redirects = 0; ; redirects++)
            {
                token.ThrowIfCancellationRequested();
                IPAddress[] addresses = await ResolveAsync(uri.IdnHost, token).ConfigureAwait(false);
                AssetDownloadPolicy.RequirePublicAddresses(addresses);
                HttpResult result = null;
                for (int i = 0; i < addresses.Length; i++)
                {
                    token.ThrowIfCancellationRequested();
                    try
                    {
                        result = await _send(uri, addresses[i], token).ConfigureAwait(false);
                        break;
                    }
                    catch (WebException e) when (!token.IsCancellationRequested && i + 1 < addresses.Length
                        && (e.Status == WebExceptionStatus.ConnectFailure || e.Status == WebExceptionStatus.Timeout))
                    {
                        // Retry only the already validated DNS snapshot, never resolve again here.
                    }
                }
                if (result == null) throw new IOException("Provider download returned no response.");
                if (!IsRedirect(result.Status)) return result;
                if (redirects >= MaxRedirects)
                    throw new InvalidOperationException("Provider download exceeded the redirect limit.");
                if (string.IsNullOrWhiteSpace(result.RedirectLocation)
                    || !Uri.TryCreate(uri, result.RedirectLocation, out Uri target))
                    throw new InvalidOperationException("Provider download returned an invalid redirect.");
                uri = AssetDownloadPolicy.RequireAllowedUrl(provider, target.AbsoluteUri);
            }
        }

        private async Task<IPAddress[]> ResolveAsync(string host, CancellationToken ct)
        {
            var canceled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using (ct.Register(() => canceled.TrySetCanceled()))
            {
                Task<IPAddress[]> lookup = _resolve(host);
                await Task.WhenAny(lookup, canceled.Task).ConfigureAwait(false);
                ct.ThrowIfCancellationRequested();
                return await lookup.ConfigureAwait(false);
            }
        }

        private static bool IsRedirect(int status)
            => status == 301 || status == 302 || status == 303 || status == 307 || status == 308;

        internal static HttpWebRequest CreatePinnedRequest(Uri uri, IPAddress address)
        {
            AssetDownloadPolicy.RequirePublicAddresses(new[] { address });
            if (address.IsIPv4MappedToIPv6) address = address.MapToIPv4();
            // Preserve the escaped path/query of signed CDN URLs. No second hostname lookup.
            string host = address.AddressFamily == System.Net.Sockets.AddressFamily.InterNetworkV6
                ? "[" + address + "]" : address.ToString();
            var request = (HttpWebRequest)WebRequest.Create("https://" + host + uri.PathAndQuery);
            request.Host = uri.IdnHost;
            request.Method = "GET";
            request.AllowAutoRedirect = false;
            request.Proxy = null;
            request.KeepAlive = false;
            // KeepAlive=false closes this connection after use, but can still borrow a pooled one.
            // Isolate the connection so a different TLS hostname on the same IP cannot be reused.
            request.ConnectionGroupName = Guid.NewGuid().ToString("N");
            request.UseDefaultCredentials = false;
            request.Credentials = null;
            request.CookieContainer = null;
            // Mono's TLS implementation authenticates request.Host, not the pinned IP literal.
            // Require normal hostname/chain checks even if an application set a global callback.
            request.ServerCertificateValidationCallback = (_, __, ___, errors) => errors == SslPolicyErrors.None;
            return request;
        }

        private static async Task<HttpResult> SendPinnedAsync(Uri uri, IPAddress address, CancellationToken ct)
        {
            HttpWebRequest request = CreatePinnedRequest(uri, address);
            using (ct.Register(request.Abort))
            {
                try
                {
                    HttpWebResponse response;
                    try
                    {
                        response = (HttpWebResponse)await request.GetResponseAsync().ConfigureAwait(false);
                    }
                    catch (WebException e) when (e.Response is HttpWebResponse)
                    {
                        response = (HttpWebResponse)e.Response;
                    }
                    using (response)
                    {
                        var result = new HttpResult
                        {
                            Status = (int)response.StatusCode,
                            RedirectLocation = response.Headers[HttpResponseHeader.Location],
                            IsSuccess = (int)response.StatusCode >= 200 && (int)response.StatusCode < 300
                        };
                        // Redirect/error bodies are unnecessary and must not be staged as assets.
                        if (!result.IsSuccess) return result;
                        using Stream body = response.GetResponseStream();
                        result.Body = await ReadLimitedAsync(body, response.ContentLength, MaxDownloadBytes, ct).ConfigureAwait(false);
                        return result;
                    }
                }
                catch (Exception) when (ct.IsCancellationRequested)
                {
                    throw new OperationCanceledException(ct);
                }
                finally
                {
                    request.ServicePoint.CloseConnectionGroup(request.ConnectionGroupName);
                }
            }
        }

        internal static async Task<byte[]> ReadLimitedAsync(Stream body, long contentLength, int maxBytes, CancellationToken ct)
        {
            if (contentLength > maxBytes)
                throw new IOException("Provider download exceeds the byte limit.");
            using var output = new MemoryStream();
            var buffer = new byte[81920];
            int read;
            while ((read = await body.ReadAsync(buffer, 0, buffer.Length, ct).ConfigureAwait(false)) != 0)
            {
                if (read > maxBytes - output.Length)
                    throw new IOException("Provider download exceeds the byte limit.");
                output.Write(buffer, 0, read);
            }
            return output.ToArray();
        }
    }
}
