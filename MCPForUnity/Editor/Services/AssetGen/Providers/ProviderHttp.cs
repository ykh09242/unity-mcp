using System;
using System.IO;
using System.Text;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen.Http;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.AssetGen.Providers
{
    /// <summary>
    /// Shared HTTP-response helpers for provider adapters: read the response text (falling back to
    /// a UTF-8 decode of the raw body) and truncate long bodies for inclusion in error messages.
    /// </summary>
    internal static class ProviderHttp
    {
        internal const int MaxRequestBytes = 48 * 1024 * 1024;

        /// <summary>Count compact JSON UTF-8 bytes before allocating its string or request body.</summary>
        public static byte[] SerializeRequest(JObject body)
        {
            // Json.NET may create an escape buffer for an individual string. Bound those inputs
            // before writing, then measure escaped text, Unicode and the complete JSON envelope.
            foreach (JToken token in body.Descendants())
                if (token.Type == JTokenType.String && ((string)token).Length > MaxRequestBytes)
                    throw new IOException("Provider request exceeds the 48 MiB input limit.");
            long byteCount;
            using (var counter = new RequestByteCounter())
            {
                WriteJson(body, counter);
                byteCount = counter.Length;
            }
            byte[] bytes = new byte[(int)byteCount]; // The counter bounds the conversion and allocation.
            using (var stream = new MemoryStream(bytes, 0, bytes.Length, true, false))
            {
                WriteJson(body, stream);
                if (stream.Position != byteCount)
                    throw new IOException("Provider request changed during serialization.");
            }
            return bytes;
        }

        private static void WriteJson(JObject body, Stream stream)
        {
            using var text = new StreamWriter(stream, new UTF8Encoding(false), 1024, true);
            using var json = new JsonTextWriter(text) { Formatting = Formatting.None };
            body.WriteTo(json);
            json.Flush();
            text.Flush();
        }

        private sealed class RequestByteCounter : Stream
        {
            private long _length;
            public override bool CanRead => false;
            public override bool CanSeek => false;
            public override bool CanWrite => true;
            public override long Length => _length;
            public override long Position
            {
                get => _length;
                set => throw new NotSupportedException();
            }

            public override void Flush() { }

            public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();

            public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();

            public override void SetLength(long value) => throw new NotSupportedException();

            public override void Write(byte[] buffer, int offset, int count)
            {
                if (count > MaxRequestBytes - _length)
                    throw new IOException("Provider request exceeds the 48 MiB input limit.");
                _length += count;
            }

            public override void WriteByte(byte value)
            {
                if (_length == MaxRequestBytes)
                    throw new IOException("Provider request exceeds the 48 MiB input limit.");
                _length++;
            }
        }

        /// <summary>
        /// Throw unless <paramref name="url"/> is an absolute https URL whose host is exactly
        /// <paramref name="allowedHost"/>. Adapters route every auth-bearing request URL through
        /// this so a malicious/MITM'd provider response (e.g. a rogue response_url) can't redirect
        /// the API key to an attacker host. The error is scrubbed of the key.
        /// </summary>
        public static void RequireHost(string url, string allowedHost, string apiKey, string context)
        {
            if (
                !Uri.TryCreate(url, UriKind.Absolute, out Uri u)
                || u.Scheme != Uri.UriSchemeHttps
                || !string.Equals(u.Host, allowedHost, StringComparison.OrdinalIgnoreCase)
            )
            {
                throw new Exception(
                    SecretRedactor.Scrub(
                        $"{context}: refusing to send credentials to an unexpected host in URL '{url}' (expected https://{allowedHost}).",
                        apiKey
                    )
                );
            }
        }

        /// <summary>Response text, falling back to a UTF-8 decode of the raw body when Text is empty.</summary>
        public static string BodyText(HttpResult res)
        {
            string text = res?.Text;
            if (string.IsNullOrEmpty(text) && res?.Body != null)
                text = Encoding.UTF8.GetString(res.Body);
            return text;
        }

        /// <summary>Cap a (possibly null) string at 500 chars for inclusion in an error message.</summary>
        public static string Truncate(string s)
        {
            if (string.IsNullOrEmpty(s))
                return string.Empty;
            return s.Length <= 500 ? s : s.Substring(0, 500) + "…";
        }
    }
}
