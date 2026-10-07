using System;
using System.IO;
using System.Net.Http;
using System.Threading;
using Newtonsoft.Json;

namespace MCPForUnity.Editor.Setup
{
    /// <summary>Resource admission and bounded response reads for skill synchronization.</summary>
    internal static class SkillSyncDownload
    {
        internal const int MaxTreeBytes = 32 * 1024 * 1024;
        internal const int MaxBranchBytes = 1024 * 1024;
        internal const int MaxBlobBytes = 64 * 1024 * 1024;
        internal const long MaxStagedBytes = 256L * 1024 * 1024;
        internal const int MaxTreeEntries = 100000;
        internal const int MaxFiles = 4096;
        private const int BufferBytes = 64 * 1024;

        internal static byte[] ReadBytes(HttpClient client, string url, long maxBytes, CancellationToken cancellation = default)
        {
            if (maxBytes < 0 || maxBytes > MaxBlobBytes)
                throw new ArgumentOutOfRangeException(nameof(maxBytes));
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
            timeout.CancelAfter(TimeSpan.FromSeconds(60));
            var token = timeout.Token;
            using var response = client.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, token).GetAwaiter().GetResult();
            if (!response.IsSuccessStatusCode)
                throw new InvalidOperationException($"Skill sync request failed (HTTP {(int)response.StatusCode}).");
            long? declared = response.Content.Headers.ContentLength;
            if (declared.HasValue && (declared.Value < 0 || declared.Value > maxBytes))
                throw new IOException("Skill sync response exceeds its byte budget.");
            using var stream = response.Content.ReadAsStreamAsync().GetAwaiter().GetResult();
            return ReadBounded(stream, maxBytes, token);
        }

        internal static byte[] ReadBounded(Stream stream, long maxBytes, CancellationToken cancellation)
        {
            if (maxBytes < 0 || maxBytes > MaxBlobBytes)
                throw new ArgumentOutOfRangeException(nameof(maxBytes));
            using var output = new MemoryStream();
            byte[] buffer = new byte[BufferBytes];
            while (true)
            {
                cancellation.ThrowIfCancellationRequested();
                int count = (int)Math.Min(buffer.Length, maxBytes - output.Length + 1);
                int read = stream.ReadAsync(buffer, 0, count, cancellation).GetAwaiter().GetResult();
                if (read == 0)
                    break;
                long required = output.Length + read;
                if (required > maxBytes)
                    throw new IOException("Skill sync response exceeds its byte budget.");
                if (required > output.Capacity)
                    output.Capacity = (int)Math.Min(maxBytes, Math.Max(required, Math.Max(4096L, output.Capacity * 2L)));
                output.Write(buffer, 0, read);
            }
            return output.ToArray();
        }

        // Count the exact array consumed by JsonUtility before it allocates an entry array.
        internal static void ValidateTreeJson(string json, int entryLimit = MaxTreeEntries)
        {
            using var text = new StringReader(json);
            using var reader = new JsonTextReader(text) { MaxDepth = 16 };
            bool treeSeen = false;
            int entries = 0;
            while (reader.Read())
            {
                if (
                    reader.TokenType == JsonToken.PropertyName
                    && reader.Depth == 1
                    && string.Equals((string)reader.Value, "tree", StringComparison.OrdinalIgnoreCase)
                )
                {
                    if ((string)reader.Value != "tree")
                        throw new IOException("GitHub tree metadata uses a noncanonical tree property.");
                    if (treeSeen)
                        throw new IOException("GitHub tree metadata contains duplicate tree properties.");
                    treeSeen = true;
                }
                if (
                    reader.Depth == 2
                    && reader.Path.StartsWith("tree[", StringComparison.Ordinal)
                    && reader.TokenType != JsonToken.EndObject
                    && reader.TokenType != JsonToken.EndArray
                )
                {
                    if (++entries > entryLimit)
                        throw new IOException("GitHub tree exceeds the entry limit.");
                }
            }
        }

        internal static void RequireFileCount(long count)
        {
            if (count < 0 || count > MaxFiles)
                throw new IOException("Skill sync exceeds the 4096-file limit.");
        }

        internal sealed class ByteBudget
        {
            private long _used;
            internal long Remaining => MaxStagedBytes - _used;
            internal long NextBlobLimit => Math.Min(MaxBlobBytes, Remaining);

            internal void Admit(long bytes)
            {
                if (bytes < 0 || bytes > MaxBlobBytes || bytes > Remaining)
                    throw new IOException("Skill sync exceeds its file or aggregate byte budget.");
                _used += bytes;
            }
        }
    }
}
