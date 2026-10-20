using System;
using System.Buffers;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Services.Transport
{
    /// <summary>One receive loop owns the reusable buffers until that connection settles.</summary>
    internal sealed class WebSocketMessageReader : IDisposable
    {
        // The server admits at most 4 MiB of command parameters. Leave envelope headroom.
        internal const int MaxMessageBytes = 8 * 1024 * 1024;
        private const int RetainedCapacity = 64 * 1024;
        private byte[] buffer = ArrayPool<byte>.Shared.Rent(8192);
        private MemoryStream stream = new(8192);
        internal string CloseReason { get; private set; }

        internal async Task<string> ReadAsync(WebSocket socket, CancellationToken token)
        {
            if (buffer == null)
                throw new ObjectDisposedException(nameof(WebSocketMessageReader));
            if (socket == null)
                throw new ArgumentNullException(nameof(socket));
            CloseReason = null;
            try
            {
                while (true)
                {
                    token.ThrowIfCancellationRequested();
                    var result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer, 0, 8192), token).ConfigureAwait(false);
                    token.ThrowIfCancellationRequested();
                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        CloseReason = result.CloseStatusDescription ?? "Server closed connection";
                        return null;
                    }
                    if (result.MessageType != WebSocketMessageType.Text)
                        throw new InvalidOperationException("Server messages must use WebSocket text frames.");
                    if (result.Count > MaxMessageBytes - stream.Length)
                        throw new InvalidOperationException("Server message exceeds the receive byte limit.");
                    if (result.Count > 0)
                        stream.Write(buffer, 0, result.Count);
                    if (result.EndOfMessage)
                        return Encoding.UTF8.GetString(stream.GetBuffer(), 0, (int)stream.Length);
                }
            }
            finally
            {
                stream.SetLength(0);
                if (stream.Capacity > RetainedCapacity)
                {
                    stream.Dispose();
                    stream = new MemoryStream(8192);
                }
            }
        }

        public void Dispose()
        {
            var owned = buffer;
            if (owned == null)
                return;
            buffer = null;
            stream.Dispose();
            ArrayPool<byte>.Shared.Return(owned);
        }
    }
}
