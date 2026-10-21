using System;
using System.IO;
using UnityEngine.Networking;

namespace MCPForUnity.Editor.Services.AssetGen.Http
{
    /// <summary>Bounds provider API responses, including chunked and decompressed bodies.</summary>
    internal sealed class BoundedDownloadHandler : DownloadHandlerScript
    {
        internal const int MaxResponseBytes = 32 * 1024 * 1024;
        private readonly int _limit;
        private MemoryStream _body = new MemoryStream();
        internal bool LimitExceeded { get; private set; }

        internal BoundedDownloadHandler(int limit = MaxResponseBytes)
            : base(new byte[64 * 1024])
        {
            if (limit <= 0)
                throw new ArgumentOutOfRangeException(nameof(limit));
            _limit = limit;
        }

        protected override void ReceiveContentLengthHeader(ulong length)
        {
            if (length > (ulong)_limit)
                LimitExceeded = true;
        }

        protected override bool ReceiveData(byte[] data, int dataLength)
        {
            if (LimitExceeded || dataLength > _limit - _body.Length)
            {
                LimitExceeded = true;
                return false;
            }
            if (data != null && dataLength > 0)
                _body.Write(data, 0, dataLength);
            return true;
        }

        internal ArraySegment<byte> GetBody()
        {
            if (LimitExceeded)
                throw new IOException("Provider API response exceeds the 32 MiB limit.");
            using var completed = _body ?? throw new InvalidOperationException("The response body has already been transferred.");
            _body = null;
            // MemoryStream disposal leaves the transferred array valid; no handler retains it.
            return new ArraySegment<byte>(completed.GetBuffer(), 0, checked((int)completed.Length));
        }

        public override void Dispose()
        {
            _body?.Dispose();
            _body = null;
            base.Dispose();
        }
    }
}
