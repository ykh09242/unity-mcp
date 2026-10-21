using System;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnity.Editor.Services.AssetGen.Http
{
    /// <summary>
    /// Downloads artifacts using validated IP literals, with the original Host for TLS/SNI.
    /// UnityWebRequest cannot pin DNS resolution, so it must not handle provider result URLs.
    /// </summary>
    internal sealed class AssetDownloadTransport
    {
        internal const int MaxRedirects = 5;
        internal const long MaxDownloadBytes = 8L * 1024 * 1024 * 1024;
        internal static readonly TimeSpan DownloadTimeout = TimeSpan.FromHours(1);
        private readonly string _projectRoot;
        private readonly Func<string, Task<IPAddress[]>> _resolve;
        private readonly Func<Uri, IPAddress, CancellationToken, Task<HttpResult>> _send;

        internal AssetDownloadTransport(
            Func<string, Task<IPAddress[]>> resolve = null,
            Func<Uri, IPAddress, CancellationToken, Task<HttpResult>> send = null,
            string projectRoot = null
        )
        {
            _projectRoot = projectRoot;
            _resolve = resolve ?? Dns.GetHostAddressesAsync;
            _send = send ?? SendPinnedAsync;
        }

        internal async Task<HttpResult> DownloadAsync(string provider, string url, CancellationToken ct)
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeout.CancelAfter(DownloadTimeout);
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
                    catch (WebException e)
                        when (!token.IsCancellationRequested
                            && i + 1 < addresses.Length
                            && (e.Status == WebExceptionStatus.ConnectFailure || e.Status == WebExceptionStatus.Timeout)
                        )
                    {
                        // Retry only the already validated DNS snapshot, never resolve again here.
                    }
                }
                if (result == null)
                    throw new IOException("Provider download returned no response.");
                bool transferred = false;
                try
                {
                    token.ThrowIfCancellationRequested();
                    if (!IsRedirect(result.Status))
                    {
                        transferred = true;
                        return result;
                    }
                    if (redirects >= MaxRedirects)
                        throw new InvalidOperationException("Provider download exceeded the redirect limit.");
                    if (string.IsNullOrWhiteSpace(result.RedirectLocation) || !Uri.TryCreate(uri, result.RedirectLocation, out Uri target))
                        throw new InvalidOperationException("Provider download returned an invalid redirect.");
                    uri = AssetDownloadPolicy.RequireAllowedUrl(provider, target.AbsoluteUri);
                }
                finally
                {
                    if (!transferred)
                        result.Dispose();
                }
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

        private static bool IsRedirect(int status) => status == 301 || status == 302 || status == 303 || status == 307 || status == 308;

        internal static HttpWebRequest CreatePinnedRequest(Uri uri, IPAddress address)
        {
            AssetDownloadPolicy.RequirePublicAddresses(new[] { address });
            if (address.IsIPv4MappedToIPv6)
                address = address.MapToIPv4();
            // Preserve the escaped path/query of signed CDN URLs. No second hostname lookup.
            string host = address.AddressFamily == System.Net.Sockets.AddressFamily.InterNetworkV6 ? "[" + address + "]" : address.ToString();
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

        private async Task<HttpResult> SendPinnedAsync(Uri uri, IPAddress address, CancellationToken ct)
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
                            IsSuccess = (int)response.StatusCode >= 200 && (int)response.StatusCode < 300,
                        };
                        // Redirect/error bodies are unnecessary and must not be staged as assets.
                        if (!result.IsSuccess)
                            return result;
                        using Stream body = response.GetResponseStream();
                        result.DownloadedFile = await DownloadedArtifact
                            .ReadAsync(body, response.ContentLength, MaxDownloadBytes, _projectRoot, ct)
                            .ConfigureAwait(false);
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
    }

    // Own the file handle, not just its name: cancellation/reload releases it and deletes the
    // staging file. No Unity APIs run here; the main-thread transport captures the project root.
    internal sealed class DownloadedArtifact : IDisposable
    {
        private FileStream stream;
        internal string Path { get; }
        internal long Length { get; }
        internal string Sha256 { get; }

        private DownloadedArtifact(FileStream stream, string path, string hash)
        {
            this.stream = stream;
            Path = path;
            Length = stream.Length;
            Sha256 = hash;
        }

        internal Stream Rewind()
        {
            FileStream current = stream ?? throw new ObjectDisposedException(nameof(DownloadedArtifact));
            current.Position = 0;
            return current;
        }

        public void Dispose() => Interlocked.Exchange(ref stream, null)?.Dispose();

        internal static async Task<DownloadedArtifact> ReadAsync(Stream body, long contentLength, long maxBytes, string projectRoot, CancellationToken ct)
        {
            ct.ThrowIfCancellationRequested();
            if (body == null || maxBytes <= 0 || contentLength > maxBytes || contentLength < -1)
                throw new IOException("Provider download exceeds the byte limit or has an invalid body length.");
            if (string.IsNullOrWhiteSpace(projectRoot) || !System.IO.Path.IsPathRooted(projectRoot))
                throw new InvalidOperationException("A project root is required for artifact staging.");
            string directory = SafePathUtility.ResolveWithinRoot(projectRoot, "Temp/McpAssetDownloads");
            Directory.CreateDirectory(directory);
            string path = SafePathUtility.ResolveWithinRoot(projectRoot, System.IO.Path.Combine(directory, Guid.NewGuid().ToString("N") + ".part"));
            FileStream output = null;
            try
            {
                output = new FileStream(
                    path,
                    FileMode.CreateNew,
                    FileAccess.ReadWrite,
                    FileShare.None,
                    64 * 1024,
                    FileOptions.Asynchronous | FileOptions.DeleteOnClose
                );
                SafePathUtility.ResolveWithinRoot(projectRoot, path);
                using var sha = SHA256.Create();
                var buffer = new byte[64 * 1024];
                long length = 0;
                int read;
                while ((read = await body.ReadAsync(buffer, 0, buffer.Length, ct).ConfigureAwait(false)) != 0)
                {
                    ct.ThrowIfCancellationRequested();
                    if (read > maxBytes - length || (contentLength >= 0 && read > contentLength - length))
                        throw new IOException("Provider download exceeds its declared length or byte limit.");
                    await output.WriteAsync(buffer, 0, read, ct).ConfigureAwait(false);
                    sha.TransformBlock(buffer, 0, read, null, 0);
                    length += read;
                }
                if (contentLength >= 0 && length != contentLength)
                    throw new IOException("Provider download ended before its declared length.");
                await output.FlushAsync(ct).ConfigureAwait(false);
                ct.ThrowIfCancellationRequested();
                sha.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                var artifact = new DownloadedArtifact(output, path, BitConverter.ToString(sha.Hash).Replace("-", "").ToLowerInvariant());
                output = null;
                return artifact;
            }
            finally
            {
                output?.Dispose();
            }
        }
    }
}
