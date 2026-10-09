using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.Blender
{
    /// <summary>Where the BlenderMCP addon socket listens. Captured on the main thread so I/O can run elsewhere.</summary>
    public readonly struct BlenderEndpoint
    {
        public string Host { get; }
        public int Port { get; }

        public BlenderEndpoint(string host, int port)
        {
            Host = host;
            Port = port;
        }

        /// <summary>Formats the endpoint as host:port.</summary>
        public override string ToString() => $"{Host}:{Port}";
    }

    /// <summary>
    /// Minimal client for the BlenderMCP addon socket. Protocol: one JSON object
    /// {"type": ..., "params": {...}} per request and one JSON object
    /// {"status": "success"|"error", "result"|"message": ...} back, with no length framing —
    /// the addon parses whenever the accumulated bytes form valid JSON, so this does the same.
    /// A fresh connection is used per command so it never interleaves with the AI client's
    /// own BlenderMCP server talking to the same addon. The synchronous methods block and touch
    /// no Unity API, so callers run them through the *Async wrappers off the editor thread.
    /// </summary>
    public static class BlenderSocketClient
    {
        private const int ConnectTimeoutSeconds = 3;
        internal const int MaxResponseBytes = 16 * 1024 * 1024;

        /// <summary>Sends one command and blocks until the addon answers or the timeout elapses.</summary>
        public static JToken Send(
            BlenderEndpoint endpoint,
            string type,
            JObject @params = null,
            int timeoutSeconds = 60,
            CancellationToken cancellationToken = default
        )
        {
            cancellationToken.ThrowIfCancellationRequested();
            timeoutSeconds = Math.Max(1, Math.Min(3600, timeoutSeconds));
            var request = new JObject { ["type"] = type, ["params"] = @params ?? new JObject() };
            byte[] payload = Encoding.UTF8.GetBytes(request.ToString(Formatting.None));

            using var client = new TcpClient();
            using var cancellation = cancellationToken.Register(() => client.Close());
            IAsyncResult connect = client.BeginConnect(endpoint.Host, endpoint.Port, null, null);
            using var connectWait = connect.AsyncWaitHandle;
            bool connected = connectWait.WaitOne(TimeSpan.FromSeconds(ConnectTimeoutSeconds));
            cancellationToken.ThrowIfCancellationRequested();
            if (!connected || !client.Connected)
            {
                throw new BlenderUnavailableException(
                    $"Blender addon not reachable at {endpoint}. Start Blender and press " + "'Connect to MCP server' in the BlenderMCP sidebar (N panel)."
                );
            }
            client.EndConnect(connect);

            using NetworkStream stream = client.GetStream();
            var elapsed = Stopwatch.StartNew();
            stream.WriteTimeout = timeoutSeconds * 1000;
            stream.Write(payload, 0, payload.Length);
            stream.Flush();
            return ReadResponse(stream, type, timeoutSeconds, cancellationToken, elapsed);
        }

        internal static JToken ReadResponse(
            Stream stream,
            string type,
            int timeoutSeconds,
            CancellationToken cancellationToken = default,
            Stopwatch elapsedClock = null
        )
        {
            using var buffer = new MemoryStream();
            var chunk = new byte[65536];
            var boundary = new ResponseBoundary();
            var elapsed = elapsedClock ?? Stopwatch.StartNew();

            while (true)
            {
                cancellationToken.ThrowIfCancellationRequested();
                int remainingMs = timeoutSeconds * 1000 - (int)elapsed.ElapsedMilliseconds;
                if (remainingMs <= 0)
                    throw new TimeoutException($"Timed out after {timeoutSeconds}s waiting for Blender to answer '{type}'.");
                stream.ReadTimeout = remainingMs;
                int n;
                try
                {
                    n = stream.Read(chunk, 0, chunk.Length);
                }
                catch (IOException e)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    if (!(e.InnerException is SocketException socket) || socket.SocketErrorCode != SocketError.TimedOut)
                        throw;
                    throw new TimeoutException($"Timed out after {timeoutSeconds}s waiting for Blender to answer '{type}'.", e);
                }
                catch (ObjectDisposedException) when (cancellationToken.IsCancellationRequested)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    throw;
                }

                if (n <= 0)
                    break;
                if (buffer.Length + n > MaxResponseBytes)
                    throw new InvalidDataException($"Blender response exceeds the {MaxResponseBytes}-byte limit.");
                buffer.Write(chunk, 0, n);

                if (boundary.Append(chunk, n) && TryParseResponse(buffer.GetBuffer(), (int)buffer.Length, out JObject parsed))
                    return Unwrap(parsed, type);
            }

            if (TryParseResponse(buffer.GetBuffer(), (int)buffer.Length, out JObject final))
                return Unwrap(final, type);
            throw new IOException($"Blender closed the connection before a complete response to '{type}' arrived.");
        }

        // Scan each byte once, keeping only framing state across reads. JSON validation
        // remains with Json.NET, including its quoted/unquoted names, comments and constructors.
        private sealed class ResponseBoundary
        {
            private int depth;
            private byte quote;
            private bool escaped;
            private bool seenObject;
            private bool invalid;
            private bool slash;
            private int comment; // 1: line, 2: block
            private bool star;

            internal bool Append(byte[] bytes, int count)
            {
                for (int i = 0; i < count; i++)
                {
                    byte value = bytes[i];
                    if (comment == 1)
                    {
                        if (value == '\r' || value == '\n')
                            comment = 0;
                        continue;
                    }
                    if (comment == 2)
                    {
                        if (star && value == '/')
                        {
                            comment = 0;
                            star = false;
                        }
                        else
                            star = value == '*';
                        continue;
                    }
                    if (quote != 0)
                    {
                        if (escaped)
                            escaped = false;
                        else if (value == '\\')
                            escaped = true;
                        else if (value == quote)
                            quote = 0;
                        continue;
                    }
                    if (slash)
                    {
                        slash = false;
                        if (value == '/' || value == '*')
                        {
                            comment = value == '/' ? 1 : 2;
                            continue;
                        }
                        invalid = true;
                    }
                    if (value == '/')
                    {
                        slash = true;
                        continue;
                    }
                    // Non-ASCII bytes may encode Json.NET whitespace; final parsing validates them.
                    if (value <= 0x20 || value >= 0x80)
                        continue;
                    if (!seenObject)
                    {
                        if (value == '{')
                        {
                            seenObject = true;
                            depth = 1;
                        }
                        else
                            invalid = true;
                        continue;
                    }
                    if (depth == 0)
                    {
                        invalid = true;
                        continue;
                    }
                    if (value == '"' || value == '\'')
                        quote = value;
                    else if (value == '{' || value == '[' || value == '(')
                        depth++;
                    else if (value == '}' || value == ']' || value == ')')
                        depth--;
                }
                return seenObject && depth == 0 && quote == 0 && comment != 2 && !slash && !invalid;
            }
        }

        /// <summary>Runs <see cref="Send"/> on the thread pool so the editor stays responsive.</summary>
        public static Task<JToken> SendAsync(
            BlenderEndpoint endpoint,
            string type,
            JObject @params = null,
            int timeoutSeconds = 60,
            CancellationToken cancellationToken = default
        )
        {
            return Task.Run(() => Send(endpoint, type, @params, timeoutSeconds, cancellationToken), cancellationToken);
        }

        /// <summary>Runs Python inside Blender (blocking) and returns its captured stdout.</summary>
        public static string RunPython(BlenderEndpoint endpoint, string code, int timeoutSeconds = 120)
        {
            JToken result = Send(endpoint, "execute_code", new JObject { ["code"] = code }, timeoutSeconds);
            return result?["result"]?.ToString() ?? string.Empty;
        }

        /// <summary>Runs <see cref="RunPython"/> on the thread pool.</summary>
        public static Task<string> RunPythonAsync(BlenderEndpoint endpoint, string code, int timeoutSeconds = 120)
        {
            return Task.Run(() => RunPython(endpoint, code, timeoutSeconds));
        }

        /// <summary>Probes the addon with get_scene_info; returns whether it answered and the error text if not.</summary>
        public static (bool Ok, string Error) Probe(BlenderEndpoint endpoint, int timeoutSeconds = 10)
        {
            try
            {
                Send(endpoint, "get_scene_info", null, timeoutSeconds);
                return (true, null);
            }
            catch (Exception e)
            {
                return (false, e.Message);
            }
        }

        /// <summary>Runs <see cref="Probe"/> on the thread pool.</summary>
        public static Task<(bool Ok, string Error)> ProbeAsync(BlenderEndpoint endpoint, int timeoutSeconds = 10)
        {
            return Task.Run(() => Probe(endpoint, timeoutSeconds));
        }

        /// <summary>True once the bytes received so far form one complete JSON object.</summary>
        internal static bool TryParseResponse(byte[] buffer, int length, out JObject obj)
        {
            try
            {
                obj = JObject.Parse(Encoding.UTF8.GetString(buffer, 0, length));
                return true;
            }
            catch
            {
                obj = null;
                return false;
            }
        }

        /// <summary>
        /// Returns the addon's "result" payload for a success response, throws
        /// <see cref="BlenderCommandException"/> for an error response, and rejects anything else
        /// so a malformed reply never surfaces as a successful command with a null payload.
        /// </summary>
        internal static JToken Unwrap(JObject response, string type)
        {
            string status = (string)response?["status"];
            if (string.Equals(status, "success", StringComparison.OrdinalIgnoreCase))
            {
                if (response.Property("result") == null)
                    throw new InvalidDataException($"Blender omitted the result for '{type}'.");
                return response["result"];
            }
            if (string.Equals(status, "error", StringComparison.OrdinalIgnoreCase))
                throw new BlenderCommandException($"Blender '{type}' failed: {(string)response["message"] ?? "unknown error"}");
            throw new InvalidDataException($"Blender returned an invalid status '{status ?? "(none)"}' for '{type}'.");
        }
    }

    /// <summary>The addon socket could not be reached (Blender closed or the addon not connected).</summary>
    public class BlenderUnavailableException : Exception
    {
        public BlenderUnavailableException(string message)
            : base(message) { }
    }

    /// <summary>The addon accepted the command but reported an error while running it.</summary>
    public class BlenderCommandException : Exception
    {
        public BlenderCommandException(string message)
            : base(message) { }
    }
}
