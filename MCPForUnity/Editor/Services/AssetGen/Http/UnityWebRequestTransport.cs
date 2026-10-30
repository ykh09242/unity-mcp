using System;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine.Networking;

namespace MCPForUnity.Editor.Services.AssetGen.Http
{
    /// <summary>
    /// Production <see cref="IHttpTransport"/> backed by UnityWebRequest. Must be invoked on the
    /// Unity main thread (the asset-gen job manager guarantees this in Phase 3). The send is
    /// awaited via a <see cref="TaskCompletionSource{T}"/> wired to the async op's completed
    /// callback, so the call never blocks the editor loop.
    /// </summary>
    public sealed class UnityWebRequestTransport : IHttpTransport
    {
        internal static int ResolveTimeoutSeconds(double? requested)
        {
            double seconds = requested ?? 120;
            if (double.IsNaN(seconds) || double.IsInfinity(seconds) || seconds <= 0 || seconds > 86400)
                throw new ArgumentOutOfRangeException(nameof(requested), "HTTP request wait must be greater than zero and at most 86400 seconds.");
            // Unity uses whole seconds; rounding up avoids prematurely shortening the job budget.
            // The enclosing job still owns its precise deadline and cancellation.
            return (int)Math.Ceiling(seconds);
        }

        public Task<HttpResult> SendAsync(HttpRequestSpec spec, CancellationToken ct)
        {
            if (spec == null)
                throw new ArgumentNullException(nameof(spec));
            if (spec.DownloadProvider != null)
                return new AssetDownloadTransport(
                    projectRoot: System.IO.Path.GetDirectoryName(UnityEngine.Application.dataPath),
                    progress: spec.DownloadProgress
                ).DownloadAsync(spec.DownloadProvider, spec.Url, ct);

            if (ct.IsCancellationRequested)
                return Task.FromCanceled<HttpResult>(ct);

            int timeout = ResolveTimeoutSeconds(spec.TimeoutSeconds);
            EditorWebRequest.ThrowIfUnavailable();
            var tcs = new TaskCompletionSource<HttpResult>(TaskCreationOptions.RunContinuationsAsynchronously);
            var download = new BoundedDownloadHandler();
            UnityWebRequest request = null;
            UploadHandlerRaw upload = null;
            bool downloadAttached = false;
            bool uploadAttached = false;
            EditorWebRequest owner = null;
            try
            {
                // Retain ownership until each handler has actually attached to the request.
                request = new UnityWebRequest(spec.Url, spec.Method ?? UnityWebRequest.kHttpVerbGET);
                request.downloadHandler = download;
                downloadAttached = true;
                request.timeout = timeout;
                if (spec.Body != null)
                {
                    upload = new UploadHandlerRaw(spec.Body);
                    request.uploadHandler = upload;
                    uploadAttached = true;
                }
                if (!string.IsNullOrEmpty(spec.ContentType))
                {
                    request.SetRequestHeader("Content-Type", spec.ContentType);
                }
                if (spec.Headers != null)
                {
                    foreach (var kv in spec.Headers)
                    {
                        request.SetRequestHeader(kv.Key, kv.Value);
                    }
                }
                // Provider API calls never follow redirects. Artifact redirects are handled separately
                // by AssetDownloadTransport, which revalidates the URL and DNS at every hop.
                request.redirectLimit = 0;

                owner = new EditorWebRequest(request);
                owner.Send(
                    ct,
                    () =>
                    {
                        HttpResult result = null;
                        Exception failure = null;
                        try
                        {
                            var body = download.GetBody();
                            result = new HttpResult
                            {
                                Status = (int)request.responseCode,
                                IsSuccess = request.result == UnityWebRequest.Result.Success,
                                RetryAfterSeconds = int.TryParse(request.GetResponseHeader("Retry-After"), out int retryAfter) ? (int?)retryAfter : null,
                            };
                            result.SetBody(body);
                        }
                        catch (Exception e)
                        {
                            failure = e;
                        }
                        finally
                        {
                            owner.Dispose();
                        }
                        if (failure != null)
                            tcs.TrySetException(failure);
                        else
                            tcs.TrySetResult(result);
                    },
                    error =>
                    {
                        if (error is OperationCanceledException)
                            tcs.TrySetCanceled(ct);
                        else
                            tcs.TrySetException(error);
                    }
                );

                return tcs.Task;
            }
            catch
            {
                // No completion callback owns cleanup when configuration or sending fails synchronously.
                try
                {
                    if (owner != null)
                        owner.Dispose();
                    else
                        request?.Dispose();
                }
                finally
                {
                    if (!downloadAttached)
                        download.Dispose();
                    if (!uploadAttached)
                        upload?.Dispose();
                }
                throw;
            }
        }
    }
}
