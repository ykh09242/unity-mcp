using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.Transport.Transports
{
    internal sealed class StdioAuthenticatedSession
    {
        internal string ServerGeneration { get; }
        internal string SessionId { get; }
        internal IReadOnlyCollection<string> Capabilities { get; }
        internal string SessionGeneration => ServerGeneration + ":" + SessionId;

        internal StdioAuthenticatedSession(string generation, string session, bool progress, bool arbitration = false, bool resume = false, bool origin = false)
        {
            ServerGeneration = generation;
            SessionId = session;
            var capabilities = new List<string>();
            if (progress)
                capabilities.Add(CommandProgressBuffer.Capability);
            if (arbitration)
                capabilities.Add(NativeCommandArbitrator.Capability);
            if (resume)
                capabilities.Add(CommandResumeJournal.Capability);
            if (resume && arbitration && origin)
                capabilities.Add(CommandResumeJournal.OriginCapability);
            Capabilities = capabilities.AsReadOnly();
        }
    }

    /// <summary>Mutual, bounded launch authentication before command/client admission.</summary>
    internal sealed class StdioBridgeAuthentication : IDisposable
    {
        internal readonly string Generation = Guid.NewGuid().ToString("N");
        private readonly string token;
        private readonly StdioLaunchCredential credential;
        private const int MaxAuthFrame = 1024;

        internal StdioBridgeAuthentication(string token, bool publish)
        {
            this.token = token ?? RandomHex(32);
            if (this.token.Length < 32 || this.token.Length > 256)
                throw new ArgumentException("Stdio launch token must contain 32 to 256 characters");
            if (publish)
                credential = new StdioLaunchCredential(Generation, this.token);
        }

        private static string RandomHex(int length)
        {
            byte[] bytes = new byte[length];
            using (var random = RandomNumberGenerator.Create())
                random.GetBytes(bytes);
            return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
        }

        internal static string Proof(string token, params string[] fields)
        {
            string transcript = "unity-mcp-stdio-v2\n" + string.Join("\n", fields);
            using var mac = new HMACSHA256(Encoding.UTF8.GetBytes(token));
            return BitConverter.ToString(mac.ComputeHash(Encoding.ASCII.GetBytes(transcript))).Replace("-", "").ToLowerInvariant();
        }

        private static bool EqualProof(string actual, string expected)
        {
            if (actual == null || actual.Length != expected.Length)
                return false;
            int difference = 0;
            for (int i = 0; i < expected.Length; i++)
                difference |= actual[i] ^ expected[i];
            return difference == 0;
        }

        private static async Task<byte[]> ReadExactAsync(NetworkStream stream, int count, CancellationToken cancel)
        {
            byte[] bytes = new byte[count];
            int offset = 0;
            while (offset < count)
            {
                int read = await stream.ReadAsync(bytes, offset, count - offset, cancel).ConfigureAwait(false);
                if (read == 0)
                    throw new IOException("Stdio authentication peer closed");
                offset += read;
            }
            return bytes;
        }

        internal async Task<string> AuthenticateAsync(NetworkStream stream, CancellationToken cancel)
        {
            var session = await AuthenticateSessionAsync(stream, cancel).ConfigureAwait(false);
            // Keep the native legacy wrapper's exact session-only return contract.
            return session?.SessionId;
        }

        private static bool OffersCapability(JToken offered, string requested)
        {
            if (!(offered is JArray values) || values.Count > 16)
                return false;
            bool supported = false;
            foreach (JToken value in values)
            {
                if (value.Type != JTokenType.String)
                    return false;
                string capability = (string)value;
                if (capability.Length > 64 || Encoding.UTF8.GetByteCount(capability) > 64)
                    return false;
                supported |= string.Equals(capability, requested, StringComparison.Ordinal);
            }
            return supported;
        }

        internal async Task<StdioAuthenticatedSession> AuthenticateSessionAsync(
            NetworkStream stream,
            CancellationToken cancel,
            Func<StdioAuthenticatedSession, bool> admit = null
        )
        {
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancel);
            deadline.CancelAfter(TimeSpan.FromSeconds(OperationTimeouts.EnvironmentSeconds("UNITY_MCP_HANDSHAKE_TIMEOUT", 30, 300)));
            // Older Mono NetworkStream reads may not abort on token cancellation.
            using var abortRead = deadline.Token.Register(() => stream.Close());
            string challenge = RandomHex(32);
            string banner = $"WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 SERVER={Generation} CHALLENGE={challenge}\n";
            byte[] greeting = Encoding.ASCII.GetBytes(banner);
            await stream.WriteAsync(greeting, 0, greeting.Length, deadline.Token).ConfigureAwait(false);
            byte[] header = await ReadExactAsync(stream, 8, deadline.Token).ConfigureAwait(false);
            ulong length = 0;
            foreach (byte part in header)
                length = (length << 8) | part;
            if (length == 0 || length > MaxAuthFrame)
                return null;
            byte[] payload = await ReadExactAsync(stream, (int)length, deadline.Token).ConfigureAwait(false);
            JObject request;
            try
            {
                request = JObject.Parse(new UTF8Encoding(false, true).GetString(payload));
            }
            catch (JsonException)
            {
                return null;
            }
            catch (DecoderFallbackException)
            {
                return null;
            }
            if (
                request["type"]?.Type != JTokenType.String
                || (string)request["type"] != "authenticate"
                || request["version"]?.Type != JTokenType.Integer
                || request["version"].ToString(Formatting.None) != "2"
                || request["client_nonce"]?.Type != JTokenType.String
                || request["proof"]?.Type != JTokenType.String
            )
                return null;
            string nonce = (string)request["client_nonce"];
            string proof = (string)request["proof"];
            if (
                !Regex.IsMatch(nonce, "\\A[0-9a-f]{64}\\z")
                || !Regex.IsMatch(proof, "\\A[0-9a-f]{64}\\z")
                || !EqualProof(proof, Proof(token, "client", Generation, challenge, nonce))
            )
                return null;
            string session = Guid.NewGuid().ToString("N");
            bool progress = OffersCapability(request["capabilities"], CommandProgressBuffer.Capability);
            bool arbitration = OffersCapability(request["capabilities"], NativeCommandArbitrator.Capability);
            bool resume = OffersCapability(request["capabilities"], CommandResumeJournal.Capability);
            bool origin = OffersCapability(request["capabilities"], CommandResumeJournal.OriginCapability);
            var acknowledgement = new JObject
            {
                ["type"] = "authenticated",
                ["version"] = 2,
                ["session_id"] = session,
                ["proof"] = Proof(token, "server", Generation, challenge, nonce, session),
            };
            var negotiated = new StdioAuthenticatedSession(Generation, session, progress, arbitration, resume, origin);
            deadline.Token.ThrowIfCancellationRequested();
            if (admit != null && !admit(negotiated))
            {
                await WriteAcknowledgementAsync(
                        stream,
                        new JObject
                        {
                            ["type"] = "authentication_refused",
                            ["version"] = 2,
                            ["code"] = "client_admission_refused",
                        },
                        deadline.Token
                    )
                    .ConfigureAwait(false);
                return null;
            }
            if (negotiated.Capabilities.Count > 0)
                acknowledgement["capabilities"] = new JArray(negotiated.Capabilities);
            if (arbitration)
                acknowledgement["arbitration_generation"] = NativeCommandArbitrator.Shared.Generation;
            if (resume)
                acknowledgement["resume_generation"] = CommandResumeState.SessionId;
            await WriteAcknowledgementAsync(stream, acknowledgement, deadline.Token).ConfigureAwait(false);
            return negotiated;
        }

        private static async Task WriteAcknowledgementAsync(NetworkStream stream, JObject acknowledgement, CancellationToken cancel)
        {
            byte[] header = new byte[8];
            byte[] reply = Encoding.ASCII.GetBytes(acknowledgement.ToString(Formatting.None));
            ulong remaining = (ulong)reply.Length;
            for (int i = 7; i >= 0; i--)
            {
                header[i] = (byte)(remaining & 255);
                remaining >>= 8;
            }
            await stream.WriteAsync(header, 0, header.Length, cancel).ConfigureAwait(false);
            await stream.WriteAsync(reply, 0, reply.Length, cancel).ConfigureAwait(false);
        }

        public void Dispose() => credential?.Dispose();
    }
}
