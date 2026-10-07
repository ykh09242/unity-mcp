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
        private readonly MemoryStream _body = new MemoryStream();
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

        internal byte[] GetBody()
        {
            if (LimitExceeded)
                throw new IOException("Provider API response exceeds the 32 MiB limit.");
            return _body.ToArray();
        }

        public override void Dispose()
        {
            _body.Dispose();
            base.Dispose();
        }
    }
}
