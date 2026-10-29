using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;

namespace MCPForUnity.Editor.Clients
{
    /// <summary>Optional manual-configuration capabilities, independent of a file destination.</summary>
    public interface IManualConfigurationProvider
    {
        bool IsCliManaged { get; }
        ManualConfigurationResult GenerateManualConfiguration();
    }

    public sealed class ManualConfigurationResult
    {
        public string Snippet { get; }
        public string Error { get; }
        public bool Success => Error == null && !string.IsNullOrWhiteSpace(Snippet);

        private ManualConfigurationResult(string snippet, string error)
        {
            Snippet = snippet;
            Error = error;
        }

        public static ManualConfigurationResult Generate(Func<string> generate)
        {
            try
            {
                return new ManualConfigurationResult(generate(), null);
            }
            catch (Exception ex)
            {
                return new ManualConfigurationResult(null, ex.Message);
            }
        }
    }

    /// <summary>Shared base class for MCP configurators.</summary>
    public abstract class McpClientConfiguratorBase : IMcpClientConfigurator, IManualConfigurationProvider
    {
        protected readonly McpClient client;

        protected McpClientConfiguratorBase(McpClient client)
        {
            this.client = client;
        }

        internal McpClient Client => client;

        public string Id => client.name.Replace(" ", "").ToLowerInvariant();
        public virtual string DisplayName => client.name;
        public McpStatus Status => client.status;
        public ConfiguredTransport ConfiguredTransport => client.configuredTransport;
        public virtual bool SupportsAutoConfigure => true;

        // Default to a filesystem check on the configured path. Concrete configurators
        // whose presence isn't path-based (CLI binaries, etc.) override this. This makes
        // any future configurator that forgets to override fail-closed rather than be
        // treated as "detected" by ConfigureAllDetectedClients.
        public virtual bool IsInstalled => ParentDirectoryExists(GetConfigPath());
        private static readonly ConfiguredTransport[] DefaultTransports = { ConfiguredTransport.Stdio, ConfiguredTransport.Http };
        public virtual IReadOnlyList<ConfiguredTransport> SupportedTransports => DefaultTransports;
        public virtual bool SupportsSkills => false;

        public virtual string GetConfigureActionLabel() => "Configure";

        public virtual string GetSkillInstallPath() => null;

        public abstract string GetConfigPath();
        public abstract McpStatus CheckStatus(bool attemptAutoRewrite = true);
        public abstract void Configure();

        /// <summary>Default Unregister is a no-op. Override in JsonFileMcpConfigurator /
        /// ClaudeCliMcpConfigurator etc. where removal has a concrete implementation.</summary>
        public virtual void Unregister() { }

        public virtual bool IsCliManaged => false;

        public virtual ManualConfigurationResult GenerateManualConfiguration() => ManualConfigurationResult.Generate(GetManualSnippet);

        public abstract string GetManualSnippet();
        public abstract IList<string> GetInstallationSteps();

        protected string GetUvxPathOrError()
        {
            string uvx = MCPServiceLocator.Paths.GetUvxPath();
            if (string.IsNullOrEmpty(uvx))
            {
                throw new InvalidOperationException("uvx not found. Install uv/uvx or set the override in Advanced Settings.");
            }
            return uvx;
        }

        protected string CurrentOsPath()
        {
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                return client.windowsConfigPath;
            if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
                return client.macConfigPath;
            return client.linuxConfigPath;
        }

        protected static bool ParentDirectoryExists(string configPath)
        {
            try
            {
                if (string.IsNullOrEmpty(configPath))
                    return false;
                string parent = Path.GetDirectoryName(configPath);
                return !string.IsNullOrEmpty(parent) && Directory.Exists(parent);
            }
            catch
            {
                return false;
            }
        }

        protected bool UrlsEqual(string a, string b)
        {
            if (string.IsNullOrWhiteSpace(a) || string.IsNullOrWhiteSpace(b))
            {
                return false;
            }

            if (Uri.TryCreate(a.Trim(), UriKind.Absolute, out var uriA) && Uri.TryCreate(b.Trim(), UriKind.Absolute, out var uriB))
            {
                // Uri canonicalizes the scheme, host and default port. Paths and query
                // values can identify different endpoints when only their case differs.
                string CanonicalRequestUrl(Uri uri)
                {
                    char[] value = uri.GetComponents(UriComponents.HttpRequestUrl, UriFormat.SafeUnescaped).ToCharArray();
                    // Escaped reserved characters stay escaped, but their hex digits
                    // represent the same byte regardless of letter case.
                    for (int i = 0; i + 2 < value.Length; i++)
                    {
                        if (value[i] != '%' || !Uri.IsHexDigit(value[i + 1]) || !Uri.IsHexDigit(value[i + 2]))
                            continue;
                        value[i + 1] = char.ToUpperInvariant(value[i + 1]);
                        value[i + 2] = char.ToUpperInvariant(value[i + 2]);
                        i += 2;
                    }
                    return new string(value);
                }
                return string.Equals(CanonicalRequestUrl(uriA), CanonicalRequestUrl(uriB), StringComparison.Ordinal);
            }

            string Normalize(string value) => value.Trim().TrimEnd('/');
            return string.Equals(Normalize(a), Normalize(b), StringComparison.Ordinal);
        }

        /// <summary>
        /// Gets the expected package source for validation based on the installed package version.
        /// This should match what Configure() would actually use for the --from argument.
        /// MUST be called from the main thread due to EditorPrefs access.
        /// </summary>
        protected static string GetExpectedPackageSourceForValidation()
        {
            // Includes the canonical explicit override or the package's pinned Git source.
            return AssetPathUtility.GetMcpServerPackageSource();
        }

        /// <summary>
        /// Checks if a package source string represents a beta/prerelease version.
        /// Beta versions include:
        /// - PyPI beta: "mcpforunityserver==9.4.0b20250203..." (contains 'b' before timestamp)
        /// - PyPI prerelease range: "mcpforunityserver>=0.0.0a0" (used for prerelease package builds)
        /// - Git beta branch: contains "@beta" or "-beta"
        /// </summary>
        protected static bool IsBetaPackageSource(string packageSource)
        {
            if (string.IsNullOrEmpty(packageSource))
                return false;

            // PyPI beta format: mcpforunityserver==X.Y.Zb<timestamp>
            // The 'b' suffix before numbers indicates a PEP 440 beta version
            if (System.Text.RegularExpressions.Regex.IsMatch(packageSource, @"==\d+\.\d+\.\d+b\d+"))
                return true;

            // PyPI prerelease range: >=0.0.0a0 (used for prerelease package builds)
            if (packageSource.Contains(">=0.0.0a0", StringComparison.OrdinalIgnoreCase))
                return true;

            // Git-based beta references
            if (packageSource.Contains("@beta", StringComparison.OrdinalIgnoreCase))
                return true;

            if (packageSource.Contains("-beta", StringComparison.OrdinalIgnoreCase))
                return true;

            return false;
        }
    }
}
