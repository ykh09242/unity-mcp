using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.External.Tommy;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Services
{
    internal sealed class ClientConfigurationInputs
    {
        internal readonly bool UseHttp,
            Remote,
            Locked;
        internal readonly string Endpoint,
            Command,
            SystemRoot,
            LocalHelperCommand;
        internal readonly string[] Args;
        internal readonly IDictionary<string, string> Headers;
        internal readonly bool? CodexHelperSupported;

        internal ClientConfigurationInputs(
            bool useHttp,
            string endpoint,
            string command,
            string[] args,
            IDictionary<string, string> headers,
            string systemRoot,
            bool remote,
            bool? codexHelperSupported,
            string localHelperCommand,
            bool locked
        )
        {
            UseHttp = useHttp;
            Endpoint = endpoint;
            Command = command;
            Args = args?.ToArray() ?? Array.Empty<string>();
            Headers = new SortedDictionary<string, string>(headers ?? new Dictionary<string, string>(), StringComparer.Ordinal);
            SystemRoot = systemRoot;
            Remote = remote;
            CodexHelperSupported = codexHelperSupported;
            LocalHelperCommand = localHelperCommand;
            Locked = locked;
        }

        internal string Fingerprint()
        {
            string serialized = JsonConvert.SerializeObject(
                new object[] { UseHttp, Endpoint, Command, Args, Headers, SystemRoot, Remote, CodexHelperSupported, LocalHelperCommand, Locked }
            );
            if (Encoding.UTF8.GetByteCount(serialized) > 65536)
                throw new ConfigurationLimitException();
            return ClientConfigurationChangeService.Hash(serialized);
        }
    }

    internal sealed class ClientConfigurationFileTarget
    {
        internal readonly object ClientIdentity;
        internal readonly string ClientId,
            Path,
            Format;
        internal readonly McpClient Descriptor;

        internal ClientConfigurationFileTarget(object clientIdentity, string clientId, string path, string format, McpClient descriptor)
        {
            ClientIdentity = clientIdentity;
            ClientId = clientId;
            Path = System.IO.Path.GetFullPath(path);
            Format = format;
            Descriptor = descriptor;
        }

        internal string Fingerprint() =>
            ClientConfigurationChangeService.Hash(
                JsonConvert.SerializeObject(
                    new object[]
                    {
                        ClientId,
                        Path,
                        Format,
                        Descriptor?.IsVsCodeLayout,
                        Descriptor?.ServerContainerKey,
                        Descriptor?.SupportsHttpTransport,
                        Descriptor?.HttpUrlProperty,
                        Descriptor?.HttpTypeValue,
                        Descriptor?.StdioTypeValue,
                        Descriptor?.EnsureEnvObject,
                        Descriptor?.StripEnvWhenNotRequired,
                        Descriptor?.DefaultUnityFields,
                    }
                )
            );
    }

    internal sealed class ConfigurationLimitException : Exception { }

    internal sealed class ConfigurationPathException : Exception { }

    internal sealed class ConfigurationLayoutException : FormatException { }

    public sealed class ClientConfigurationPreview
    {
        public string Code { get; internal set; }
        public bool CanApply => Code == "prepared";
        public string DisplayText { get; internal set; }
        public string Operation { get; internal set; }
        internal ClientConfigurationFileTarget Target;
        internal IMcpClientConfigurator Client;
        internal string TargetHash,
            ContextHash,
            Proposed,
            OriginalEntry;
        internal byte[] Original;
        internal bool Bom;
    }

    public sealed class ClientConfigurationReceipt
    {
        internal ClientConfigurationFileTarget Target;
        internal IMcpClientConfigurator Client;
        internal string TargetHash,
            WrittenEntry,
            OriginalEntry;
    }

    public sealed class ClientConfigurationChangeResult
    {
        public string Code { get; internal set; }
        public ClientConfigurationReceipt Receipt { get; internal set; }
    }

    /// <summary>One reviewed change and one entry-scoped restore, held only in this editor domain.</summary>
    public sealed class ClientConfigurationChangeService
    {
        public static ClientConfigurationChangeService Shared { get; } = new ClientConfigurationChangeService();
        private readonly Func<string, ClientConfigurationInputs> capture;
        private readonly Func<IMcpClientConfigurator, ClientConfigurationFileTarget> resolve;
        private ClientConfigurationPreview pending;
        private ClientConfigurationReceipt undo;
        private Action beforeCommitComparison = null;
        private Func<bool> readLock = null;
        private static readonly UTF8Encoding StrictUtf8 = new UTF8Encoding(false, true);
        private const int FileLimit = 1024 * 1024;

        public ClientConfigurationChangeService()
            : this(Capture, Resolve)
        {
            readLock = ReadLock;
        }

        internal ClientConfigurationChangeService(
            Func<string, ClientConfigurationInputs> capture,
            Func<IMcpClientConfigurator, ClientConfigurationFileTarget> resolver
        )
        {
            this.capture = capture;
            resolve = resolver;
            readLock = () => false;
        }

        public void CancelPreview() => pending = null;

        internal ClientConfigurationReceipt RestoreReceiptFor(IMcpClientConfigurator client) =>
            undo != null && ReferenceEquals(undo.Client, client) ? undo : null;

        public ClientConfigurationPreview Prepare(IMcpClientConfigurator client)
        {
            pending = null;
            try
            {
                var target = resolve(client);
                if (target == null)
                    return Refused("unsupported_client");
                byte[] bytes = Read(target.Path);
                string document = Decode(bytes, out bool bom);
                string original = Entry(document, target);
                ValidatePreflight(document, target);
                var inputs = CaptureInputs(target);
                if (inputs.Locked)
                    return Refused("locked");
                string context = inputs.Fingerprint();
                ValidateInputs(inputs);
                if (target.Format == "toml" && inputs.UseHttp && !inputs.Remote && inputs.CodexHelperSupported != true)
                    return Refused("capability_unavailable");
                string proposal;
                if (target.Format == "json")
                {
                    var root = ParseJson(document);
                    ConfigJsonBuilder.ApplyUnityServerToExistingConfig(root, null, target.Descriptor, inputs, false);
                    proposal = root.ToString(Formatting.Indented);
                }
                else if (target.Format == "toml")
                {
                    string generated = CodexConfigHelper.UpsertCodexServerBlock(document, null, inputs);
                    proposal = CodexConfigHelper.ReplaceOwnedEntry(document, CodexConfigHelper.OwnedEntry(generated));
                }
                else
                    return Refused("unsupported_client");
                if (Encoding.UTF8.GetByteCount(proposal) > FileLimit)
                    throw new ConfigurationLimitException();
                string proposedEntry = Entry(proposal, target);
                string operation =
                    SameEntry(original, proposedEntry, target.Format) ? "unchanged"
                    : original == null ? "create"
                    : "update";
                pending = new ClientConfigurationPreview
                {
                    Code = "prepared",
                    Target = target,
                    Client = client,
                    TargetHash = target.Fingerprint(),
                    ContextHash = context,
                    Original = bytes,
                    OriginalEntry = original,
                    Proposed = proposal,
                    Bom = bom,
                    Operation = operation,
                    DisplayText =
                        "Change: "
                        + operation
                        + ". Unity MCP entry only. Transport: "
                        + (inputs.UseHttp && target.Descriptor?.SupportsHttpTransport != false ? "HTTP" : "stdio")
                        + ". All values, paths, arguments, headers and user-defined names are redacted. Other settings are preserved.",
                };
                return pending;
            }
            catch (Exception error)
            {
                return Refused(Classify(error));
            }
        }

        public ClientConfigurationChangeResult Apply(ClientConfigurationPreview preview, IMcpClientConfigurator client)
        {
            if (preview == null || !ReferenceEquals(preview, pending) || !ReferenceEquals(preview.Client, client))
                return Result("stale_handle");
            try
            {
                var target = resolve(client);
                if (!SameTarget(preview.Target, preview.TargetHash, target))
                    return Result("stale_target");
                var inputs = CaptureInputs(target);
                if (inputs.Locked)
                    return Result("locked");
                string currentContext = inputs.Fingerprint();
                ValidateInputs(inputs);
                if (currentContext != preview.ContextHash)
                    return Result("stale_context");
                byte[] current = Read(target.Path);
                if (!SameBytes(current, preview.Original))
                    return Result("stale_file");
                string writtenEntry = Entry(preview.Proposed, target);
                if (SameEntry(writtenEntry, preview.OriginalEntry, target.Format))
                {
                    pending = null;
                    return Result("unchanged");
                }
                Commit(target.Path, preview.Proposed, preview.Bom, current);
                undo = new ClientConfigurationReceipt
                {
                    Target = target,
                    Client = client,
                    TargetHash = preview.TargetHash,
                    OriginalEntry = preview.OriginalEntry,
                    WrittenEntry = writtenEntry,
                };
                pending = null;
                return new ClientConfigurationChangeResult { Code = "applied", Receipt = undo };
            }
            catch (Exception error)
            {
                return Result(Classify(error));
            }
        }

        public ClientConfigurationChangeResult Restore(ClientConfigurationReceipt receipt, IMcpClientConfigurator client)
        {
            if (receipt == null || !ReferenceEquals(receipt, undo) || !ReferenceEquals(receipt.Client, client))
                return Result("stale_handle");
            try
            {
                var target = resolve(client);
                if (!SameTarget(receipt.Target, receipt.TargetHash, target))
                    return Result("stale_target");
                if (readLock())
                    return Result("locked");
                byte[] bytes = Read(target.Path);
                string document = Decode(bytes, out bool bom);
                if (!SameEntry(Entry(document, target), receipt.WrittenEntry, target.Format))
                    return Result("owned_entry_changed");
                string restored;
                if (target.Format == "json")
                {
                    var root = ParseJson(document);
                    var container = JsonContainer(root, target.Descriptor, false);
                    if (receipt.OriginalEntry == null)
                        container?.Remove("unityMCP");
                    else
                        container["unityMCP"] = JObject.Parse(receipt.OriginalEntry);
                    restored = root.ToString(Formatting.Indented);
                }
                else
                    restored = CodexConfigHelper.ReplaceOwnedEntry(
                        document,
                        receipt.OriginalEntry == null ? null : CodexConfigHelper.ParseConfiguration(receipt.OriginalEntry)
                    );
                Commit(target.Path, restored, bom, bytes);
                undo = null;
                pending = null;
                return Result("restored");
            }
            catch (Exception error)
            {
                return Result(Classify(error));
            }
        }

        private void Commit(string path, string text, bool bom, byte[] expected)
        {
            if (Encoding.UTF8.GetByteCount(text) + (bom ? 3 : 0) > FileLimit)
                throw new ConfigurationLimitException();
            ValidatePath(path);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            McpConfigurationHelper.WriteGuardedConfiguration(
                path,
                (bom ? "\uFEFF" : "") + text,
                expected,
                () =>
                {
                    beforeCommitComparison?.Invoke();
                    ValidatePath(path);
                }
            );
        }

        private static ClientConfigurationPreview Refused(string code) =>
            new ClientConfigurationPreview { Code = code, DisplayText = "Preview unavailable: " + code + ". No configuration was written." };

        private static ClientConfigurationChangeResult Result(string code) => new ClientConfigurationChangeResult { Code = code };

        private static bool SameBytes(byte[] left, byte[] right) => left == null ? right == null : right != null && left.SequenceEqual(right);

        private static bool SameTarget(ClientConfigurationFileTarget original, string hash, ClientConfigurationFileTarget current) =>
            current != null && ReferenceEquals(original.ClientIdentity, current.ClientIdentity) && hash == current.Fingerprint();

        internal static string Hash(string text)
        {
            using var sha = SHA256.Create();
            return Convert.ToBase64String(sha.ComputeHash(Encoding.UTF8.GetBytes(text)));
        }

        private static string Classify(Exception error) =>
            error is ConfigurationLimitException ? "input_limit"
            : error is ConfigurationPathException ? "unsupported_path"
            : error is McpConfigurationHelper.ConfigurationConflictException ? "stale_file"
            : error is ConfigurationLayoutException ? "unsupported_layout"
            : error is FormatException || error is JsonException ? "invalid_configuration"
            : "write_failed";

        private static void ValidatePath(string path)
        {
            if (Directory.Exists(path))
                throw new ConfigurationPathException();
            for (string current = path; !string.IsNullOrEmpty(current); current = Path.GetDirectoryName(current))
                if ((File.Exists(current) || Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new ConfigurationPathException();
        }

        private static byte[] Read(string path)
        {
            ValidatePath(path);
            if (!File.Exists(path))
                return null;
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            if (stream.Length > FileLimit)
                throw new ConfigurationLimitException();
            using var output = new MemoryStream();
            var buffer = new byte[8192];
            int count;
            while ((count = stream.Read(buffer, 0, buffer.Length)) != 0)
            {
                if (output.Length + count > FileLimit)
                    throw new ConfigurationLimitException();
                output.Write(buffer, 0, count);
            }
            return output.ToArray();
        }

        private static string Decode(byte[] bytes, out bool bom)
        {
            bom = bytes != null && bytes.Length >= 3 && bytes[0] == 239 && bytes[1] == 187 && bytes[2] == 191;
            try
            {
                return bytes == null ? "" : StrictUtf8.GetString(bytes, bom ? 3 : 0, bytes.Length - (bom ? 3 : 0));
            }
            catch (DecoderFallbackException)
            {
                throw new FormatException();
            }
        }

        private static JObject ParseJson(string document) =>
            string.IsNullOrWhiteSpace(document)
                ? new JObject()
                : JObject.Parse(document, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });

        private static bool SameEntry(string left, string right, string format)
        {
            if (left == null || right == null)
                return left == right;
            return format == "json"
                ? JToken.DeepEquals(JObject.Parse(left), JObject.Parse(right))
                : CodexConfigHelper.SameToml(CodexConfigHelper.ParseConfiguration(left), CodexConfigHelper.ParseConfiguration(right));
        }

        private static JObject JsonContainer(JObject root, McpClient descriptor, bool create)
        {
            string key =
                descriptor?.IsVsCodeLayout == true ? "servers"
                : string.IsNullOrEmpty(descriptor?.ServerContainerKey) ? "mcpServers"
                : descriptor.ServerContainerKey;
            foreach (string alias in new[] { "mcpServers", "servers", "mcp_servers", "mcp" })
                if (alias != key && root[alias] is JObject other && other["unityMCP"] != null)
                    throw new FormatException();
            if (root[key] != null && root[key] is not JObject)
                throw new FormatException();
            if (root[key] == null && create)
                root[key] = new JObject();
            return root[key] as JObject;
        }

        private static string Entry(string document, ClientConfigurationFileTarget target)
        {
            string entry;
            if (target.Format == "json")
            {
                var container = JsonContainer(ParseJson(document), target.Descriptor, false);
                var owned = container?["unityMCP"];
                if (owned != null && owned is not JObject)
                    throw new FormatException();
                if (owned is JObject ownedObject && (ownedObject["headersHelper"] != null || ownedObject["http_headers_helper"] != null))
                    throw new FormatException();
                entry = owned?.ToString(Formatting.None);
            }
            else if (target.Format == "toml")
            {
                var owned = CodexConfigHelper.OwnedEntry(document);
                if (owned == null)
                    entry = null;
                else
                {
                    using var writer = new StringWriter();
                    owned.WriteTo(writer);
                    entry = writer.ToString();
                }
            }
            else
                throw new FormatException();
            if (entry != null && Encoding.UTF8.GetByteCount(entry) > 262144)
                throw new ConfigurationLimitException();
            return entry;
        }

        private static ClientConfigurationFileTarget Resolve(IMcpClientConfigurator client)
        {
            if (
                client is not McpClientConfiguratorBase known
                || client.GetType().Assembly != typeof(ClientConfigurationChangeService).Assembly
                || !client.SupportsAutoConfigure
            )
                return null;
            var declaration = client.GetType().GetMethod("Configure")?.DeclaringType;
            string format =
                declaration == typeof(JsonFileMcpConfigurator) ? "json"
                : declaration == typeof(CodexMcpConfigurator) ? "toml"
                : null;
            return format == null ? null : new ClientConfigurationFileTarget(client, client.Id, client.GetConfigPath(), format, known.Client);
        }

        private static void ValidatePreflight(string document, ClientConfigurationFileTarget target)
        {
            try
            {
                if (target.Format == "json")
                    ConfigJsonBuilder.ValidatePreviewConfiguration(ParseJson(document), target.Descriptor);
                else
                    CodexConfigHelper.ValidatePreviewConfiguration(document);
            }
            catch (InvalidOperationException)
            {
                throw new FormatException();
            }
        }

        private static bool ReadLock() => EditorPrefs.GetBool(EditorPrefKeys.LockCursorConfig, false);

        private static void ValidateInputs(ClientConfigurationInputs inputs)
        {
            if (inputs.UseHttp)
            {
                if (!Uri.TryCreate(inputs.Endpoint, UriKind.Absolute, out var endpoint) || (endpoint.Scheme != "http" && endpoint.Scheme != "https"))
                    throw new FormatException();
            }
            else if (string.IsNullOrWhiteSpace(inputs.Command) || inputs.Args.Any(arg => arg == null))
                throw new FormatException();
        }

        private ClientConfigurationInputs CaptureInputs(ClientConfigurationFileTarget target) =>
            capture(target.Format == "json" && target.Descriptor?.SupportsHttpTransport == false ? "stdio" : target.Format);

        private static ClientConfigurationInputs Capture(string format)
        {
            bool http = format != "stdio" && EditorConfigurationCache.Instance.UseHttpTransport;
            string endpoint = http ? HttpEndpointUtility.GetMcpRpcUrl() : null;
            string command = null;
            string[] args = Array.Empty<string>();
            if (!http)
            {
                var parts = AssetPathUtility.GetUvxCommandParts();
                command = parts.Item1;
                args = ConfigJsonBuilder.BuildUvxArgs(parts.Item2, parts.Item3).ToArray();
            }
            bool remote = http && HttpEndpointUtility.IsRemoteScope();
            var headers = http && (format != "toml" || remote) ? HttpEndpointUtility.GetAuthHeaders() : new Dictionary<string, string>();
            return new ClientConfigurationInputs(
                http,
                endpoint,
                command,
                args,
                headers,
                !http ? Environment.GetEnvironmentVariable("SystemRoot") : null,
                remote,
                http && format == "toml" && !remote ? CodexHttpAuth.CachedHeadersHelper : null,
                http && format == "toml" && !remote ? LocalHttpAuth.CommandForEndpoint(endpoint) : null,
                ReadLock()
            );
        }
    }
}
