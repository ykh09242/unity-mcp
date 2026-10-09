using System;
using System.Text;
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
        public Task<HttpResult> SendAsync(HttpRequestSpec spec, CancellationToken ct)
        {
            if (spec == null)
                throw new ArgumentNullException(nameof(spec));
            if (spec.DownloadProvider != null)
                return new AssetDownloadTransport().DownloadAsync(spec.DownloadProvider, spec.Url, ct);

            if (ct.IsCancellationRequested)
                return Task.FromCanceled<HttpResult>(ct);

            var tcs = new TaskCompletionSource<HttpResult>();
            var download = new BoundedDownloadHandler();
            UnityWebRequest request = null;
            UploadHandlerRaw upload = null;
            bool downloadAttached = false;
            bool uploadAttached = false;
            CancellationTokenRegistration ctReg = default;
            try
            {
                // Retain ownership until each handler has actually attached to the request.
                request = new UnityWebRequest(spec.Url, spec.Method ?? UnityWebRequest.kHttpVerbGET);
                request.downloadHandler = download;
                downloadAttached = true;
                request.timeout = 120;
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

                if (ct.CanBeCanceled)
                {
                    ctReg = ct.Register(() =>
                    {
                        try
                        {
                            request.Abort();
                        }
                        catch { /* ignore */ }
                        tcs.TrySetCanceled();
                    });
                }

                var op = request.SendWebRequest();
                op.completed += _ =>
                {
                    try
                    {
                        byte[] body = download.GetBody();
                        var result = new HttpResult
                        {
                            Status = (int)request.responseCode,
                            Body = body,
                            Text = Encoding.UTF8.GetString(body),
                            IsSuccess = request.result == UnityWebRequest.Result.Success,
                            RetryAfterSeconds = int.TryParse(request.GetResponseHeader("Retry-After"), out int retryAfter) ? (int?)retryAfter : null,
                        };
                        tcs.TrySetResult(result);
                    }
                    catch (Exception e)
                    {
                        tcs.TrySetException(e);
                    }
                    finally
                    {
                        ctReg.Dispose();
                        request.Dispose();
                    }
                };

                return tcs.Task;
            }
            catch
            {
                // No completion callback owns cleanup when configuration or sending fails synchronously.
                ctReg.Dispose();
                try
                {
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
