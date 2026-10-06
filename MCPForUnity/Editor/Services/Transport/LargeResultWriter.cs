using System;
using System.Globalization;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Services.Transport
{
    /// <summary>Transfers one JSON command-result envelope in independently locked messages.</summary>
    public static class LargeResultWriter
    {
        public const string Capability = "large_result_v1";
        public const int ThresholdBytes = 256 * 1024;
        public const int MaxResultBytes = 32 * 1024 * 1024;
        public const int MaxFrameBytes = 64 * 1024;
        public const int HeaderBytes = 44;
        public const int ChunkPayloadBytes = MaxFrameBytes - HeaderBytes;

        /// <summary>
        /// sendFrame must send a complete WebSocket message and acquire/release the connection's
        /// send lock for that message only. It must reject replaced connection ownership.
        /// The supplied segment is reused after the returned task completes; do not retain it.
        /// </summary>
        public static async Task SendAsync(string commandId, byte[] responseBytes, bool negotiated,
            Func<ArraySegment<byte>, WebSocketMessageType, CancellationToken, Task> sendFrame,
            CancellationToken token)
        {
            if (responseBytes == null) throw new ArgumentNullException(nameof(responseBytes));
            if (sendFrame == null) throw new ArgumentNullException(nameof(sendFrame));
            if (responseBytes.Length > MaxResultBytes)
                throw new InvalidOperationException("Command result exceeds the transport byte limit");
            token.ThrowIfCancellationRequested();
            if (!negotiated || responseBytes.Length < ThresholdBytes)
            {
                await sendFrame(new ArraySegment<byte>(responseBytes), WebSocketMessageType.Text, token)
                    .ConfigureAwait(false);
                return;
            }
            if (!Guid.TryParseExact(commandId, "D", out Guid parsedId) ||
                !string.Equals(parsedId.ToString("D"), commandId, StringComparison.Ordinal))
                throw new ArgumentException("Large result ID must be a canonical lowercase UUID", nameof(commandId));

            int count = (responseBytes.Length + ChunkPayloadBytes - 1) / ChunkPayloadBytes;
            string start = "{\"type\":\"result_start\",\"id\":\"" + commandId + "\",\"total_bytes\":" +
                responseBytes.Length.ToString(CultureInfo.InvariantCulture) + ",\"chunk_count\":" +
                count.ToString(CultureInfo.InvariantCulture) + "}";
            await sendFrame(new ArraySegment<byte>(Encoding.UTF8.GetBytes(start)), WebSocketMessageType.Text, token)
                .ConfigureAwait(false);

            byte[] frame = new byte[MaxFrameBytes];
            Encoding.ASCII.GetBytes("ULR1" + commandId, 0, 40, frame, 0);
            for (int offset = 0; offset < responseBytes.Length; offset += ChunkPayloadBytes)
            {
                token.ThrowIfCancellationRequested();
                frame[40] = (byte)(offset >> 24);
                frame[41] = (byte)(offset >> 16);
                frame[42] = (byte)(offset >> 8);
                frame[43] = (byte)offset;
                int length = Math.Min(ChunkPayloadBytes, responseBytes.Length - offset);
                Buffer.BlockCopy(responseBytes, offset, frame, HeaderBytes, length);
                await sendFrame(new ArraySegment<byte>(frame, 0, HeaderBytes + length),
                    WebSocketMessageType.Binary, token).ConfigureAwait(false);
                // Every chunk is a complete message. Let queued pong/state/control sends run.
                await Task.Yield();
            }
        }
    }
}
