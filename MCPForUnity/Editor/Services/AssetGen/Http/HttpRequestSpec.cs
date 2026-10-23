using System.Collections.Generic;

namespace MCPForUnity.Editor.Services.AssetGen.Http
{
    /// <summary>
    /// Transport-agnostic description of a single HTTP request. Provider adapters build one
    /// of these and hand it to an <see cref="IHttpTransport"/>; this keeps adapters free of
    /// any direct dependency on UnityWebRequest so they can be unit-tested without a network.
    /// </summary>
    public sealed class HttpRequestSpec
    {
        public string Method;
        public string Url;
        public Dictionary<string, string> Headers = new Dictionary<string, string>();
        public byte[] Body;
        public string ContentType;

        /// <summary>
        /// Optional finite request wait in seconds (greater than zero, at most 86400).
        /// Ordinary API reads default to 120 seconds. Managed jobs clamp API requests to their
        /// remaining execution budget; artifact downloads retain their separate streaming policy.
        /// </summary>
        public double? TimeoutSeconds;

        internal HttpRequestSpec WithTimeout(double seconds)
        {
            // Retain the existing payload and headers; large upload buffers are not copied.
            var request = (HttpRequestSpec)MemberwiseClone();
            request.TimeoutSeconds = seconds;
            return request;
        }

        /// <summary>Non-null for untrusted artifact downloads; selects the provider's outbound policy.</summary>
        public string DownloadProvider;
        internal AssetOperationProgress DownloadProgress;
    }
}
