using System;
using System.Globalization;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
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
        public const string CompressionCapability = "large_result_gzip_v1";
        public const int CompressionThresholdBytes = 1024 * 1024;
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
        public static Task SendAsync(string commandId, byte[] responseBytes, bool negotiated,
            Func<ArraySegment<byte>, WebSocketMessageType, CancellationToken, Task> sendFrame,
            CancellationToken token, bool compressionNegotiated = false, bool allowCompression = false)
        {
            if (responseBytes == null) throw new ArgumentNullException(nameof(responseBytes));
            return SendCoreAsync(commandId, new ArraySource(responseBytes), negotiated, sendFrame, token,
                compressionNegotiated, allowCompression);
        }

        /// <summary>Preserves Encoding.UTF8 bytes without a full UTF-8 array for large negotiated peers.</summary>
        public static Task SendJsonAsync(string commandId, string responseJson, bool negotiated,
            Func<ArraySegment<byte>, WebSocketMessageType, CancellationToken, Task> sendFrame,
            CancellationToken token, bool compressionNegotiated = false, bool allowCompression = false)
        {
            if (responseJson == null) throw new ArgumentNullException(nameof(responseJson));
            return SendPreparedJsonAsync(commandId, new PreparedJson(responseJson), negotiated, sendFrame, token,
                compressionNegotiated, allowCompression);
        }

        // The immutable text owns its exact Encoding.UTF8 count; callers cannot supply a false size.
        internal readonly struct PreparedJson
        {
            internal string Text { get; }
            internal int ByteCount { get; }
            internal PreparedJson(string text)
            {
                Text = text ?? throw new ArgumentNullException(nameof(text));
                ByteCount = Encoding.UTF8.GetByteCount(text);
            }
        }

        internal static Task SendPreparedJsonAsync(string commandId, PreparedJson responseJson, bool negotiated,
            Func<ArraySegment<byte>, WebSocketMessageType, CancellationToken, Task> sendFrame,
            CancellationToken token, bool compressionNegotiated = false, bool allowCompression = false)
        {
            if (responseJson.Text == null) throw new ArgumentNullException(nameof(responseJson));
            return SendCoreAsync(commandId, new StringSource(responseJson), negotiated, sendFrame, token,
                compressionNegotiated, allowCompression);
        }

        private static async Task SendCoreAsync(string commandId, ByteSource source, bool negotiated,
            Func<ArraySegment<byte>, WebSocketMessageType, CancellationToken, Task> sendFrame,
            CancellationToken token, bool compressionNegotiated, bool allowCompression)
        {
            if (sendFrame == null) throw new ArgumentNullException(nameof(sendFrame));
            if (source.Length > MaxResultBytes)
                throw new InvalidOperationException("Command result exceeds the transport byte limit");
            token.ThrowIfCancellationRequested();
            if (!negotiated || source.Length < ThresholdBytes)
            {
                await sendFrame(new ArraySegment<byte>(source.GetTextBytes()), WebSocketMessageType.Text, token)
                    .ConfigureAwait(false);
                return;
            }
            if (!Guid.TryParseExact(commandId, "D", out Guid parsedId) ||
                !string.Equals(parsedId.ToString("D"), commandId, StringComparison.Ordinal))
                throw new ArgumentException("Large result ID must be a canonical lowercase UUID", nameof(commandId));

            BoundedGzipBuffer compressed = null;
            try
            {
                if (compressionNegotiated && allowCompression && source.Length >= CompressionThresholdBytes)
                    compressed = await CompressAsync(source, token).ConfigureAwait(false);
                ByteSource wireSource = compressed == null ? source : new GzipSource(compressed);
                await SendChunksAsync(commandId, wireSource, compressed == null ? 0 : source.Length,
                    sendFrame, token).ConfigureAwait(false);
            }
            finally { compressed?.Dispose(); }
        }

        private static async Task SendChunksAsync(string commandId, ByteSource source, int decodedBytes,
            Func<ArraySegment<byte>, WebSocketMessageType, CancellationToken, Task> sendFrame, CancellationToken token)
        {
            int count = (source.Length + ChunkPayloadBytes - 1) / ChunkPayloadBytes;
            string start = "{\"type\":\"result_start\",\"id\":\"" + commandId + "\",\"total_bytes\":" +
                source.Length.ToString(CultureInfo.InvariantCulture) + ",\"chunk_count\":" +
                count.ToString(CultureInfo.InvariantCulture);
            if (decodedBytes > 0)
                start += ",\"encoding\":\"gzip\",\"decoded_bytes\":" + decodedBytes.ToString(CultureInfo.InvariantCulture);
            start += "}";
            await sendFrame(new ArraySegment<byte>(Encoding.UTF8.GetBytes(start)), WebSocketMessageType.Text, token)
                .ConfigureAwait(false);

            byte[] frame = new byte[MaxFrameBytes];
            Encoding.ASCII.GetBytes("ULR1" + commandId, 0, 40, frame, 0);
            for (int offset = 0; offset < source.Length; offset += ChunkPayloadBytes)
            {
                token.ThrowIfCancellationRequested();
                frame[40] = (byte)(offset >> 24);
                frame[41] = (byte)(offset >> 16);
                frame[42] = (byte)(offset >> 8);
                frame[43] = (byte)offset;
                int length = Math.Min(ChunkPayloadBytes, source.Length - offset);
                source.CopyTo(offset, frame, HeaderBytes, length);
                await sendFrame(new ArraySegment<byte>(frame, 0, HeaderBytes + length),
                    WebSocketMessageType.Binary, token).ConfigureAwait(false);
                // Every chunk is a complete message. Let queued pong/state/control sends run.
                await Task.Yield();
            }
        }

        private static async Task<BoundedGzipBuffer> CompressAsync(ByteSource source, CancellationToken token)
        {
            byte[] staging = new byte[ChunkPayloadBytes];
            // A bounded prefix avoids a full expensive pass over incompressible preview data.
            using (var sample = new BoundedGzipBuffer(staging.Length * 9 / 10))
            {
                try
                {
                    token.ThrowIfCancellationRequested();
                    source.CopyTo(0, staging, 0, staging.Length);
                    using (var gzip = new GZipStream(sample, CompressionLevel.Fastest, true))
                        gzip.Write(staging, 0, staging.Length);
                }
                catch (CompressionLimitException) { return null; }
            }
            // Never retain more than 90% of the original bytes. Abort a low-value attempt.
            var output = new BoundedGzipBuffer(source.Length * 9 / 10);
            try
            {
                using (var gzip = new GZipStream(output, CompressionLevel.Fastest, true))
                {
                    for (int offset = 0; offset < source.Length; offset += staging.Length)
                    {
                        token.ThrowIfCancellationRequested();
                        int count = Math.Min(staging.Length, source.Length - offset);
                        source.CopyTo(offset, staging, 0, count);
                        gzip.Write(staging, 0, count);
                        await Task.Yield();
                    }
                }
                token.ThrowIfCancellationRequested();
                return output;
            }
            catch (CompressionLimitException) { output.Dispose(); return null; }
            catch { output.Dispose(); throw; }
        }

        private abstract class ByteSource
        {
            public abstract int Length { get; }
            public abstract void CopyTo(int offset, byte[] destination, int start, int count);
            public abstract byte[] GetTextBytes();
        }

        private sealed class ArraySource : ByteSource
        {
            private readonly byte[] _bytes;
            public ArraySource(byte[] bytes) { _bytes = bytes; }
            public override int Length => _bytes.Length;
            public override void CopyTo(int offset, byte[] destination, int start, int count) =>
                Buffer.BlockCopy(_bytes, offset, destination, start, count);
            public override byte[] GetTextBytes() => _bytes;
        }

        private sealed class StringSource : ByteSource
        {
            private readonly string _text;
            private readonly int _length;
            private byte[] _staging;
            private int _characters, _read, _available;
            public StringSource(PreparedJson json) { _text = json.Text; _length = json.ByteCount; }
            public override int Length => _length;
            public override byte[] GetTextBytes() => Encoding.UTF8.GetBytes(_text);
            public override void CopyTo(int offset, byte[] destination, int start, int count)
            {
                if (offset == 0) { _characters = _read = _available = 0; }
                if (_staging == null) _staging = new byte[MaxFrameBytes];
                while (count > 0)
                {
                    if (_read == _available)
                    {
                        int characters = Math.Min(MaxFrameBytes / 4, _text.Length - _characters);
                        // Keep a valid UTF-16 pair together; standalone surrogates retain Encoding.UTF8 replacement semantics.
                        if (characters > 0 && _characters + characters < _text.Length &&
                            char.IsHighSurrogate(_text[_characters + characters - 1]) &&
                            char.IsLowSurrogate(_text[_characters + characters])) characters--;
                        _available = Encoding.UTF8.GetBytes(_text, _characters, characters, _staging, 0);
                        _characters += characters;
                        _read = 0;
                    }
                    int part = Math.Min(count, _available - _read);
                    Buffer.BlockCopy(_staging, _read, destination, start, part);
                    _read += part;
                    start += part;
                    count -= part;
                }
            }
        }

        private sealed class GzipSource : ByteSource
        {
            private readonly BoundedGzipBuffer _buffer;
            public GzipSource(BoundedGzipBuffer buffer) { _buffer = buffer; }
            public override int Length => (int)_buffer.Length;
            public override void CopyTo(int offset, byte[] destination, int start, int count) =>
                _buffer.CopyTo(offset, destination, start, count);
            public override byte[] GetTextBytes() => throw new NotSupportedException();
        }

        private sealed class CompressionLimitException : IOException { }

        private sealed class BoundedGzipBuffer : Stream
        {
            private readonly int _limit;
            private readonly List<byte[]> _segments = new List<byte[]>();
            private int _length;
            public BoundedGzipBuffer(int limit) { _limit = limit; }
            public override bool CanRead => false;
            public override bool CanSeek => false;
            public override bool CanWrite => true;
            public override long Length => _length;
            public override long Position { get => _length; set => throw new NotSupportedException(); }
            public override void Flush() { }
            public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
            public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
            public override void SetLength(long value) => throw new NotSupportedException();
            public override void Write(byte[] buffer, int offset, int count)
            {
                if (count > _limit - _length) throw new CompressionLimitException();
                while (count > 0)
                {
                    int index = _length / ChunkPayloadBytes;
                    int start = _length % ChunkPayloadBytes;
                    if (index == _segments.Count)
                        _segments.Add(new byte[Math.Min(ChunkPayloadBytes, _limit - _length)]);
                    int part = Math.Min(count, _segments[index].Length - start);
                    Buffer.BlockCopy(buffer, offset, _segments[index], start, part);
                    _length += part;
                    offset += part;
                    count -= part;
                }
            }
            public void CopyTo(int offset, byte[] destination, int start, int count)
            {
                while (count > 0)
                {
                    int index = offset / ChunkPayloadBytes;
                    int within = offset % ChunkPayloadBytes;
                    int part = Math.Min(count, _segments[index].Length - within);
                    Buffer.BlockCopy(_segments[index], within, destination, start, part);
                    offset += part;
                    start += part;
                    count -= part;
                }
            }
            protected override void Dispose(bool disposing)
            {
                if (disposing) _segments.Clear();
                base.Dispose(disposing);
            }
        }
    }
}
