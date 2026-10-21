using System;
using System.Text;

namespace MCPForUnity.Editor.Services.AssetGen.Http
{
    /// <summary>
    /// Transport-agnostic result of an <see cref="HttpRequestSpec"/>. <see cref="Status"/> is
    /// the numeric HTTP status code; <see cref="IsSuccess"/> reflects the transport's own view
    /// of success (e.g. UnityWebRequest.Result.Success), which adapters combine with their own
    /// body-level checks.
    /// </summary>
    public sealed class HttpResult : IDisposable
    {
        public int Status;
        internal DownloadedArtifact DownloadedFile;

        public void Dispose()
        {
            DownloadedFile?.Dispose();
            DownloadedFile = null;
            body = null;
            buffer = default;
            text = null;
        }

        private byte[] body;
        private ArraySegment<byte> buffer;
        private string text;

        // JSON adapters read Text; binary adapters read Body. Materialize only the requested form.
        public byte[] Body
        {
            get
            {
                if (body == null && buffer.Array != null)
                {
                    if (buffer.Offset == 0 && buffer.Count == buffer.Array.Length)
                        body = buffer.Array;
                    else
                    {
                        body = new byte[buffer.Count];
                        Buffer.BlockCopy(buffer.Array, buffer.Offset, body, 0, buffer.Count);
                    }
                    buffer = default;
                }
                return body;
            }
            set
            {
                body = value;
                buffer = default;
                text = null;
            }
        }

        public string Text
        {
            get
            {
                if (text == null)
                {
                    if (buffer.Array != null)
                        text = Encoding.UTF8.GetString(buffer.Array, buffer.Offset, buffer.Count);
                    else if (body != null)
                        text = Encoding.UTF8.GetString(body);
                }
                return text;
            }
            set => text = value;
        }

        internal void SetBody(ArraySegment<byte> value)
        {
            buffer = value;
            body = null;
            text = null;
        }

        public bool IsSuccess;
        public string RedirectLocation;
        public int? RetryAfterSeconds;

        /// <summary>True when the transport reports success or the status code is 2xx.</summary>
        public bool Ok => IsSuccess || (Status >= 200 && Status < 300);
    }
}
