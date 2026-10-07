using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.External.Tommy;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Helpers
{
    public class CodexConfigHelperTests : TransportPreferenceTestBase
    {
        /// <summary>
        /// Validates that a TOML args array contains the expected uvx structure:
        /// --from, the pinned fork reference, and the stable mcp-for-unity executable name.
        /// </summary>
        private static void AssertValidUvxArgs(TomlArray args)
        {
            var argValues = new List<string>();
            foreach (TomlNode child in args.Children)
                argValues.Add((child as TomlString).Value);

            Assert.IsTrue(argValues.Contains("--from"), "Args should contain --from");
            Assert.IsTrue(argValues.Contains(AssetPathUtility.GetMcpServerPackageSource()), "Args should contain the selected pinned source");
            Assert.IsTrue(argValues.Contains("mcp-for-unity"), "Args should contain package name");

            Assert.IsFalse(argValues.Contains("--prerelease"), "Pinned Git sources do not use a prerelease channel");
        }

        /// <summary>
        /// Mock platform service for testing
        /// </summary>
        private class MockPlatformService : IPlatformService
        {
            private readonly bool _isWindows;
            private readonly string _systemRoot;

            public MockPlatformService(bool isWindows, string systemRoot = "C:\\Windows")
            {
                _isWindows = isWindows;
                _systemRoot = systemRoot;
            }

            public bool IsWindows() => _isWindows;

            public string GetSystemRoot() => _isWindows ? _systemRoot : null;
        }

        private bool _hadGitOverride;
        private string _originalGitOverride;
        private bool _hadHttpTransport;
        private bool _originalHttpTransport;
        private bool _hadDevForceRefresh;
        private bool _originalDevForceRefresh;
        private IPlatformService _originalPlatformService;
        private System.Func<bool> _originalCodexCapabilityProbe;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _originalCodexCapabilityProbe = CodexHttpAuth.SupportsHeadersHelper;
            CodexHttpAuth.SupportsHeadersHelper = () => true;
            _hadGitOverride = EditorPrefs.HasKey(EditorPrefKeys.GitUrlOverride);
            _originalGitOverride = EditorPrefs.GetString(EditorPrefKeys.GitUrlOverride, string.Empty);
            _hadHttpTransport = EditorPrefs.HasKey(EditorPrefKeys.UseHttpTransport);
            _originalHttpTransport = EditorPrefs.GetBool(EditorPrefKeys.UseHttpTransport, true);
            _hadDevForceRefresh = EditorPrefs.HasKey(EditorPrefKeys.DevModeForceServerRefresh);
            _originalDevForceRefresh = EditorPrefs.GetBool(EditorPrefKeys.DevModeForceServerRefresh, false);
            _originalPlatformService = MCPServiceLocator.Platform;
        }

        [SetUp]
        public void SetUp()
        {
            // Ensure per-test deterministic Git URL (ignore developer overrides)
            EditorPrefs.DeleteKey(EditorPrefKeys.GitUrlOverride);
            // Default to stdio mode for existing tests unless specified otherwise
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, false);
            // Ensure deterministic uvx args ordering for these tests regardless of editor settings
            // (dev-mode inserts --no-cache/--refresh, which changes the first args).
            EditorPrefs.SetBool(EditorPrefKeys.DevModeForceServerRefresh, false);
            // Refresh the cache so it picks up the test's pref values
            EditorConfigurationCache.Instance.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            // IMPORTANT:
            // These tests can be executed while an MCP session is active (e.g., when running tests via MCP).
            // MCPServiceLocator.Reset() disposes the bridge + transport manager, which can kill the MCP connection
            // mid-run. Instead, restore only what this fixture mutates.
            // To avoid leaking global state to other tests/fixtures, restore the original platform service
            // instance captured before this fixture started running.
            if (_originalPlatformService != null)
            {
                MCPServiceLocator.Register<IPlatformService>(_originalPlatformService);
            }
            else
            {
                MCPServiceLocator.Register<IPlatformService>(new PlatformService());
            }
        }

        [OneTimeTearDown]
        public void OneTimeTearDown()
        {
            CodexHttpAuth.SupportsHeadersHelper = _originalCodexCapabilityProbe;
            if (_hadGitOverride)
            {
                EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, _originalGitOverride);
            }
            else
            {
                EditorPrefs.DeleteKey(EditorPrefKeys.GitUrlOverride);
            }

            if (_hadHttpTransport)
            {
                EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, _originalHttpTransport);
            }
            else
            {
                EditorPrefs.DeleteKey(EditorPrefKeys.UseHttpTransport);
            }

            if (_hadDevForceRefresh)
            {
                EditorPrefs.SetBool(EditorPrefKeys.DevModeForceServerRefresh, _originalDevForceRefresh);
            }
            else
            {
                EditorPrefs.DeleteKey(EditorPrefKeys.DevModeForceServerRefresh);
            }
            EditorConfigurationCache.Instance.Refresh();
        }

        [Test]
        public void UpsertCodexServerBlock_StdioPreservesCustomSettings()
        {
            string result = CodexConfigHelper.UpsertCodexServerBlock(
                "model = \"synthetic-model\"\n[mcp_servers.other]\ncommand = \"other\"\n"
                    + "[mcp_servers.unityMCP]\ncommand = \"old\"\nargs = []\n"
                    + "enabled = false\nstartup_timeout_sec = 0\n"
                    + "env = { CUSTOM = \"retained\" }\n",
                null
            );
            using var reader = new StringReader(result);
            var root = TOML.Parse(reader);
            var servers = (TomlTable)root["mcp_servers"];
            var unity = (TomlTable)servers["unityMCP"];
            Assert.AreEqual("synthetic-model", ((TomlString)root["model"]).Value);
            Assert.AreEqual("other", ((TomlString)((TomlTable)servers["other"])["command"]).Value);
            Assert.IsFalse(((TomlBoolean)unity["enabled"]).Value);
            Assert.AreEqual(0, ((TomlInteger)unity["startup_timeout_sec"]).Value);
            Assert.AreEqual("retained", ((TomlString)((TomlTable)unity["env"])["CUSTOM"]).Value);
        }

        [Test]
        public void UpsertCodexServerBlock_StdioRefusesCustomHttpHeaders()
        {
            var error = Assert.Throws<System.FormatException>(() =>
                CodexConfigHelper.UpsertCodexServerBlock(
                    "[mcp_servers.unityMCP]\nurl = \"https://synthetic.invalid/mcp\"\n" + "http_headers = { Custom = \"synthetic-private-value\" }",
                    null
                )
            );
            StringAssert.Contains("http_headers", error.Message);
            StringAssert.Contains("stdio", error.Message);
            StringAssert.DoesNotContain("synthetic-private-value", error.Message);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void HttpUpsertPreservesExistingLegacyFeatureChoice(bool enabled)
        {
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorConfigurationCache.Instance.Refresh();
            string result = CodexConfigHelper.UpsertCodexServerBlock(
                "[features]\nrmcp_client = " + enabled.ToString().ToLowerInvariant() + "\ncustom_feature = true\n[mcp_servers.other]\ncommand = \"other\"",
                null
            );
            using var reader = new StringReader(result);
            var root = TOML.Parse(reader);
            Assert.AreEqual(enabled, ((TomlBoolean)root["features"]["rmcp_client"]).Value);
            Assert.IsTrue(((TomlBoolean)root["features"]["custom_feature"]).Value);
            Assert.AreEqual("other", ((TomlString)root["mcp_servers"]["other"]["command"]).Value);
        }

        [Test]
        public void TransportRoundTripRemovesOnlyManagedTransportFields()
        {
            MCPServiceLocator.Register<IPlatformService>(new MockPlatformService(isWindows: true));
            string stdio = CodexConfigHelper.UpsertCodexServerBlock("[mcp_servers.unityMCP]\ntool_timeout_sec = 17\n", null);
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorConfigurationCache.Instance.Refresh();
            string http = CodexConfigHelper.UpsertCodexServerBlock(stdio, null);
            using (var reader = new StringReader(http))
            {
                var server = (TomlTable)TOML.Parse(reader)["mcp_servers"]["unityMCP"];
                Assert.IsTrue(server.TryGetNode("url", out _));
                foreach (string key in new[] { "command", "args", "env" })
                    Assert.IsFalse(server.TryGetNode(key, out _), key);
                Assert.AreEqual(17, ((TomlInteger)server["tool_timeout_sec"]).Value);
            }
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, false);
            EditorConfigurationCache.Instance.Refresh();
            using var backReader = new StringReader(CodexConfigHelper.UpsertCodexServerBlock(http, null));
            var back = (TomlTable)TOML.Parse(backReader)["mcp_servers"]["unityMCP"];
            Assert.IsTrue(back.TryGetNode("command", out _));
            Assert.IsTrue(back.TryGetNode("args", out _));
            Assert.IsFalse(back.TryGetNode("url", out _));
            Assert.IsFalse(back.TryGetNode("http_headers", out _));
            Assert.IsFalse(back.TryGetNode("http_headers_helper", out _));
            Assert.AreEqual(17, ((TomlInteger)back["tool_timeout_sec"]).Value);
        }

        [TestCase("", false)]
        [TestCase("http_headers = { 'X-Unity-MCP-Token' = 'synthetic-current' }", true)]
        [TestCase("http_headers = { 'x-unity-mcp-token' = 'synthetic-current', Custom = 'retained' }", true)]
        [TestCase("http_headers = { 'X-Unity-MCP-Token' = 'synthetic-stale' }", false)]
        [TestCase("http_headers = { 'X-Unity-MCP-Token' = 7 }", false)]
        [TestCase("http_headers = { 'X-Unity-MCP-Token' = 'synthetic-current', 'x-unity-mcp-token' = 'synthetic-current' }", false)]
        [TestCase("http_headers = { 'X-Unity-MCP-Token' = 'synthetic-current', 'X-API-Key' = 'synthetic-stale' }", false)]
        [TestCase("enabled = false\nhttp_headers = { 'X-Unity-MCP-Token' = 'synthetic-current' }", false)]
        [TestCase("command = 'old-uvx'\nhttp_headers = { 'X-Unity-MCP-Token' = 'synthetic-current' }", false)]
        [TestCase("http_headers = 'synthetic-private-value'", false)]
        [TestCase("env_http_headers = { 'X-Unity-MCP-Token' = 'SYNTHETIC_ENV' }", false)]
        [TestCase("http_headers_helper = ['synthetic-helper']", false)]
        public void HttpValidationChecksManagedCredentialsWithoutLeakingValues(string settings, bool expected)
        {
            var headers = new Dictionary<string, string> { ["X-Unity-MCP-Token"] = "synthetic-current" };
            Assert.AreEqual(
                expected,
                CodexConfigHelper.TryValidateHttpServer("[mcp_servers.unityMCP]\nurl = 'http://127.0.0.1:8080/mcp'\n" + settings, headers, out var reason)
            );
            if (expected)
                Assert.IsNull(reason);
            else
            {
                Assert.IsNotEmpty(reason);
                StringAssert.DoesNotContain("synthetic-", reason);
            }
        }

        [TestCase("http_headers_helper = ['synthetic-helper']")]
        [TestCase("env_http_headers = { 'x-unity-mcp-token' = 'SYNTHETIC_ENV' }")]
        public void HttpUpsertRefusesToConflictWithCustomAuthentication(string settings)
        {
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorConfigurationCache.Instance.Refresh();
            var error = Assert.Throws<System.FormatException>(() =>
                CodexConfigHelper.UpsertCodexServerBlock("[mcp_servers.unityMCP]\nurl = 'http://127.0.0.1:8080/mcp'\n" + settings, null)
            );
            StringAssert.Contains("preserved", error.Message);
            StringAssert.DoesNotContain("synthetic-", error.Message);
        }

        [TestCase("{}", false)]
        [TestCase("not-json", false)]
        [TestCase("{\"transport\":{\"type\":\"streamable_http\"}}", false)]
        [TestCase("{\"transport\":{\"type\":\"streamable_http\",\"http_headers_helper\":null}}", false)]
        [TestCase("{\"transport\":{\"type\":\"streamable_http\",\"http_headers_helper\":\"<redacted>\"}}", true)]
        public void HelperCapabilityRequiresRecognizedField(string output, bool supported) =>
            Assert.AreEqual(supported, CodexHttpAuth.RecognizesHeadersHelper(output));

        [Test]
        public void LocalHttpWithoutHelperSupportRefusesToWriteStaticToken()
        {
            bool hadScope = EditorPrefs.HasKey(EditorPrefKeys.HttpTransportScope);
            string scope = EditorPrefs.GetString(EditorPrefKeys.HttpTransportScope);
            try
            {
                EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
                EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "local");
                EditorConfigurationCache.Instance.Refresh();
                CodexHttpAuth.SupportsHeadersHelper = () => false;
                var error = Assert.Throws<System.InvalidOperationException>(() => CodexConfigHelper.BuildCodexServerBlock(null));
                StringAssert.Contains("stdio", error.Message);
            }
            finally
            {
                CodexHttpAuth.SupportsHeadersHelper = () => true;
                if (hadScope)
                    EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, scope);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.HttpTransportScope);
                EditorConfigurationCache.Instance.Refresh();
            }
        }

        [Test]
        public void WindowsTokenHelperQuotesPathsWithoutEmbeddingCredentials()
        {
            string path = "C:\\synthetic dir\\O'Brien\\token-8080";
            string command = LocalHttpAuth.BuildCommand(path, true);
            string script = System.Text.Encoding.Unicode.GetString(System.Convert.FromBase64String(command.Substring(command.LastIndexOf(' ') + 1)));
            StringAssert.Contains("O''Brien", script);
            StringAssert.Contains("ReadAllText", script);
            StringAssert.Contains("ConvertTo-Json", script);
            StringAssert.DoesNotContain("ExecutionPolicy Bypass", command);
        }

        [TestCase("https://synthetic.example.test/mcp")]
        [TestCase("file:///synthetic/path")]
        public void TokenHelperNeverTargetsArbitraryRemoteEndpoints(string url) =>
            Assert.Throws<System.InvalidOperationException>(() => LocalHttpAuth.CommandForEndpoint(url));

        [Test]
        public void TryParseCodexServer_SingleLineArgs_ParsesSuccessfully()
        {
            string toml = string.Join(
                "\n",
                new[]
                {
                    "[mcp_servers.unityMCP]",
                    "command = \"uvx --from git+https://github.com/CoplayDev/unity-mcp@v6.3.0#subdirectory=Server\"",
                    "args = [\"mcp-for-unity\"]",
                }
            );

            bool result = CodexConfigHelper.TryParseCodexServer(toml, out string command, out string[] args);

            Assert.IsTrue(result, "Parser should detect server definition");
            Assert.AreEqual("uvx --from git+https://github.com/CoplayDev/unity-mcp@v6.3.0#subdirectory=Server", command);
            CollectionAssert.AreEqual(new[] { "mcp-for-unity" }, args);
        }

        [Test]
        public void TryParseCodexServer_MultiLineArgsWithTrailingComma_ParsesSuccessfully()
        {
            string toml = string.Join("\n", new[] { "[mcp_servers.unityMCP]", "command = \"uvx\"", "args = [", "  \"mcp-for-unity\",", "]" });

            bool result = CodexConfigHelper.TryParseCodexServer(toml, out string command, out string[] args);

            Assert.IsTrue(result, "Parser should handle multi-line arrays with trailing comma");
            Assert.AreEqual("uvx", command);
            CollectionAssert.AreEqual(new[] { "mcp-for-unity" }, args);
        }

        [Test]
        public void TryParseCodexServer_MultiLineArgsWithComments_IgnoresComments()
        {
            string toml = string.Join("\n", new[] { "[mcp_servers.unityMCP]", "command = \"uvx\"", "args = [", "  \"mcp-for-unity\", # package name", "]" });

            bool result = CodexConfigHelper.TryParseCodexServer(toml, out string command, out string[] args);

            Assert.IsTrue(result, "Parser should tolerate comments within the array block");
            Assert.AreEqual("uvx", command);
            CollectionAssert.AreEqual(new[] { "mcp-for-unity" }, args);
        }

        [Test]
        public void TryParseCodexServer_HeaderWithComment_StillDetected()
        {
            string toml = string.Join("\n", new[] { "[mcp_servers.unityMCP] # annotated header", "command = \"uvx\"", "args = [\"mcp-for-unity\"]" });

            bool result = CodexConfigHelper.TryParseCodexServer(toml, out string command, out string[] args);

            Assert.IsTrue(result, "Parser should recognize section headers even with inline comments");
            Assert.AreEqual("uvx", command);
            CollectionAssert.AreEqual(new[] { "mcp-for-unity" }, args);
        }

        [Test]
        public void TryParseCodexServer_SingleQuotedArgsWithApostrophes_ParsesSuccessfully()
        {
            string toml = string.Join("\n", new[] { "[mcp_servers.unityMCP]", "command = 'uvx'", "args = ['mcp-for-unity']" });

            bool result = CodexConfigHelper.TryParseCodexServer(toml, out string command, out string[] args);

            Assert.IsTrue(result, "Parser should accept single-quoted arrays with escaped apostrophes");
            Assert.AreEqual("uvx", command);
            CollectionAssert.AreEqual(new[] { "mcp-for-unity" }, args);
        }

        [Test]
        public void BuildCodexServerBlock_OnWindows_IncludesSystemRootEnv()
        {
            // This test verifies that Windows-specific environment configuration is included in stdio mode

            // Force stdio mode
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, false);

            // Mock Windows platform
            MCPServiceLocator.Register<IPlatformService>(new MockPlatformService(isWindows: true));

            string uvPath = "C:\\Program Files\\uv\\uv.exe";

            string result = CodexConfigHelper.BuildCodexServerBlock(uvPath);

            Assert.IsNotNull(result, "BuildCodexServerBlock should return a valid TOML string");

            // Parse the generated TOML to validate structure
            TomlTable parsed;
            using (var reader = new StringReader(result))
            {
                parsed = TOML.Parse(reader);
            }

            // Verify basic structure
            Assert.IsTrue(parsed.TryGetNode("mcp_servers", out var mcpServersNode), "TOML should contain mcp_servers");
            Assert.IsInstanceOf<TomlTable>(mcpServersNode, "mcp_servers should be a table");

            var mcpServers = mcpServersNode as TomlTable;
            Assert.IsTrue(mcpServers.TryGetNode("unityMCP", out var unityMcpNode), "mcp_servers should contain unityMCP");
            Assert.IsInstanceOf<TomlTable>(unityMcpNode, "unityMCP should be a table");

            var unityMcp = unityMcpNode as TomlTable;
            Assert.IsTrue(unityMcp.TryGetNode("command", out var commandNode), "unityMCP should contain command");
            Assert.IsTrue(unityMcp.TryGetNode("args", out var argsNode), "unityMCP should contain args");

            // Verify command contains uvx
            var command = (commandNode as TomlString).Value;
            Assert.IsTrue(command.Contains("uvx"), "Command should contain uvx");

            // Verify args contains the proper uvx command structure
            var args = argsNode as TomlArray;
            AssertValidUvxArgs(args);

            // Verify env.SystemRoot is present on Windows
            bool hasEnv = unityMcp.TryGetNode("env", out var envNode);
            Assert.IsTrue(hasEnv, "Windows config should contain env table");
            Assert.IsInstanceOf<TomlTable>(envNode, "env should be a table");

            var env = envNode as TomlTable;
            Assert.IsTrue(env.TryGetNode("SystemRoot", out var systemRootNode), "env should contain SystemRoot");
            Assert.IsInstanceOf<TomlString>(systemRootNode, "SystemRoot should be a string");

            var systemRoot = (systemRootNode as TomlString).Value;
            Assert.AreEqual("C:\\Windows", systemRoot, "SystemRoot should be C:\\Windows");
        }

        [Test]
        public void BuildCodexServerBlock_OnNonWindows_ExcludesEnv()
        {
            // This test verifies that non-Windows platforms don't include env configuration in stdio mode

            // Force stdio mode
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, false);

            // Mock non-Windows platform (e.g., macOS/Linux)
            MCPServiceLocator.Register<IPlatformService>(new MockPlatformService(isWindows: false));

            string uvPath = "/usr/local/bin/uv";

            string result = CodexConfigHelper.BuildCodexServerBlock(uvPath);

            Assert.IsNotNull(result, "BuildCodexServerBlock should return a valid TOML string");

            // Parse the generated TOML to validate structure
            TomlTable parsed;
            using (var reader = new StringReader(result))
            {
                parsed = TOML.Parse(reader);
            }

            // Verify basic structure
            Assert.IsTrue(parsed.TryGetNode("mcp_servers", out var mcpServersNode), "TOML should contain mcp_servers");
            Assert.IsInstanceOf<TomlTable>(mcpServersNode, "mcp_servers should be a table");

            var mcpServers = mcpServersNode as TomlTable;
            Assert.IsTrue(mcpServers.TryGetNode("unityMCP", out var unityMcpNode), "mcp_servers should contain unityMCP");
            Assert.IsInstanceOf<TomlTable>(unityMcpNode, "unityMCP should be a table");

            var unityMcp = unityMcpNode as TomlTable;
            Assert.IsTrue(unityMcp.TryGetNode("command", out var commandNode), "unityMCP should contain command");
            Assert.IsTrue(unityMcp.TryGetNode("args", out var argsNode), "unityMCP should contain args");

            // Verify command contains uvx
            var command = (commandNode as TomlString).Value;
            Assert.IsTrue(command.Contains("uvx"), "Command should contain uvx");

            // Verify args contains the proper uvx command structure
            var args = argsNode as TomlArray;
            AssertValidUvxArgs(args);

            // Verify env is NOT present on non-Windows platforms
            bool hasEnv = unityMcp.TryGetNode("env", out _);
            Assert.IsFalse(hasEnv, "Non-Windows config should not contain env table");
        }

        [Test]
        public void UpsertCodexServerBlock_OnWindows_IncludesSystemRootEnv()
        {
            // This test verifies the fix for https://github.com/CoplayDev/unity-mcp/issues/315
            // Ensures that upsert operations also include Windows-specific env configuration in stdio mode

            // Force stdio mode
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, false);

            // Mock Windows platform
            MCPServiceLocator.Register<IPlatformService>(new MockPlatformService(isWindows: true, systemRoot: "C:\\Windows"));

            string existingToml = string.Join("\n", new[] { "[other_section]", "key = \"value\"" });

            string uvPath = "C:\\path\\to\\uv.exe";

            string result = CodexConfigHelper.UpsertCodexServerBlock(existingToml, uvPath);

            Assert.IsNotNull(result, "UpsertCodexServerBlock should return a valid TOML string");

            // Parse the generated TOML to validate structure
            TomlTable parsed;
            using (var reader = new StringReader(result))
            {
                parsed = TOML.Parse(reader);
            }

            // Verify existing sections are preserved
            Assert.IsTrue(parsed.TryGetNode("other_section", out _), "TOML should preserve existing sections");

            // Verify mcp_servers structure
            Assert.IsTrue(parsed.TryGetNode("mcp_servers", out var mcpServersNode), "TOML should contain mcp_servers");
            Assert.IsInstanceOf<TomlTable>(mcpServersNode, "mcp_servers should be a table");

            var mcpServers = mcpServersNode as TomlTable;
            Assert.IsTrue(mcpServers.TryGetNode("unityMCP", out var unityMcpNode), "mcp_servers should contain unityMCP");
            Assert.IsInstanceOf<TomlTable>(unityMcpNode, "unityMCP should be a table");

            var unityMcp = unityMcpNode as TomlTable;
            Assert.IsTrue(unityMcp.TryGetNode("command", out var commandNode), "unityMCP should contain command");
            Assert.IsTrue(unityMcp.TryGetNode("args", out var argsNode), "unityMCP should contain args");

            // Verify command contains uvx
            var command = (commandNode as TomlString).Value;
            Assert.IsTrue(command.Contains("uvx"), "Command should contain uvx");

            // Verify args contains the proper uvx command structure
            var args = argsNode as TomlArray;
            AssertValidUvxArgs(args);

            // Verify env.SystemRoot is present on Windows
            bool hasEnv = unityMcp.TryGetNode("env", out var envNode);
            Assert.IsTrue(hasEnv, "Windows config should contain env table");
            Assert.IsInstanceOf<TomlTable>(envNode, "env should be a table");

            var env = envNode as TomlTable;
            Assert.IsTrue(env.TryGetNode("SystemRoot", out var systemRootNode), "env should contain SystemRoot");
            Assert.IsInstanceOf<TomlString>(systemRootNode, "SystemRoot should be a string");

            var systemRoot = (systemRootNode as TomlString).Value;
            Assert.AreEqual("C:\\Windows", systemRoot, "SystemRoot should be C:\\Windows");
        }

        [Test]
        public void UpsertCodexServerBlock_OnNonWindows_ExcludesEnv()
        {
            // This test verifies that upsert operations on non-Windows platforms don't include env configuration in stdio mode

            // Force stdio mode
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, false);

            // Mock non-Windows platform (e.g., macOS/Linux)
            MCPServiceLocator.Register<IPlatformService>(new MockPlatformService(isWindows: false));

            string existingToml = string.Join("\n", new[] { "[other_section]", "key = \"value\"" });

            string uvPath = "/usr/local/bin/uv";

            string result = CodexConfigHelper.UpsertCodexServerBlock(existingToml, uvPath);

            Assert.IsNotNull(result, "UpsertCodexServerBlock should return a valid TOML string");

            // Parse the generated TOML to validate structure
            TomlTable parsed;
            using (var reader = new StringReader(result))
            {
                parsed = TOML.Parse(reader);
            }

            // Verify existing sections are preserved
            Assert.IsTrue(parsed.TryGetNode("other_section", out _), "TOML should preserve existing sections");

            // Verify mcp_servers structure
            Assert.IsTrue(parsed.TryGetNode("mcp_servers", out var mcpServersNode), "TOML should contain mcp_servers");
            Assert.IsInstanceOf<TomlTable>(mcpServersNode, "mcp_servers should be a table");

            var mcpServers = mcpServersNode as TomlTable;
            Assert.IsTrue(mcpServers.TryGetNode("unityMCP", out var unityMcpNode), "mcp_servers should contain unityMCP");
            Assert.IsInstanceOf<TomlTable>(unityMcpNode, "unityMCP should be a table");

            var unityMcp = unityMcpNode as TomlTable;
            Assert.IsTrue(unityMcp.TryGetNode("command", out var commandNode), "unityMCP should contain command");
            Assert.IsTrue(unityMcp.TryGetNode("args", out var argsNode), "unityMCP should contain args");

            // Verify command contains uvx
            var command = (commandNode as TomlString).Value;
            Assert.IsTrue(command.Contains("uvx"), "Command should contain uvx");

            // Verify args contains the proper uvx command structure
            var args = argsNode as TomlArray;
            AssertValidUvxArgs(args);

            // Verify env is NOT present on non-Windows platforms
            bool hasEnv = unityMcp.TryGetNode("env", out _);
            Assert.IsFalse(hasEnv, "Non-Windows config should not contain env table");
        }

        [Test]
        public void BuildCodexServerBlock_HttpMode_GeneratesUrlField()
        {
            // This test verifies HTTP transport mode generates url field instead of command/args

            // Force HTTP mode
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorConfigurationCache.Instance.Refresh();

            string uvPath = "C:\\Program Files\\uv\\uv.exe";

            string result = CodexConfigHelper.BuildCodexServerBlock(uvPath);

            Assert.IsNotNull(result, "BuildCodexServerBlock should return a valid TOML string");

            // Parse the generated TOML to validate structure
            TomlTable parsed;
            using (var reader = new StringReader(result))
            {
                parsed = TOML.Parse(reader);
            }

            // Verify basic structure
            Assert.IsTrue(parsed.TryGetNode("mcp_servers", out var mcpServersNode), "TOML should contain mcp_servers");
            Assert.IsInstanceOf<TomlTable>(mcpServersNode, "mcp_servers should be a table");

            var mcpServers = mcpServersNode as TomlTable;
            Assert.IsTrue(mcpServers.TryGetNode("unityMCP", out var unityMcpNode), "mcp_servers should contain unityMCP");
            Assert.IsInstanceOf<TomlTable>(unityMcpNode, "unityMCP should be a table");

            var unityMcp = unityMcpNode as TomlTable;

            Assert.IsFalse(parsed.TryGetNode("features", out _), "HTTP must not force obsolete global feature flags");

            // Verify url field is present
            Assert.IsTrue(unityMcp.TryGetNode("url", out var urlNode), "unityMCP should contain url in HTTP mode");
            Assert.IsInstanceOf<TomlString>(urlNode, "url should be a string");

            var url = (urlNode as TomlString).Value;
            Assert.IsTrue(url.Contains("http"), "URL should be an HTTP endpoint");
            Assert.IsTrue(url.Contains("/mcp"), "URL should contain /mcp path");

            // Verify command and args are NOT present in HTTP mode
            Assert.IsFalse(unityMcp.TryGetNode("command", out _), "HTTP mode should not contain command field");
            Assert.IsFalse(unityMcp.TryGetNode("args", out _), "HTTP mode should not contain args field");
            Assert.IsFalse(unityMcp.TryGetNode("env", out _), "HTTP mode should not contain env field");
        }

        [Test]
        public void TryParseCodexServer_HttpMode_ParsesUrlSuccessfully()
        {
            // This test verifies HTTP mode parsing with url field

            string toml = string.Join("\n", new[] { "[mcp_servers.unityMCP]", "url = \"http://localhost:8080/mcp/v1/rpc\"" });

            bool result = CodexConfigHelper.TryParseCodexServer(toml, out string command, out string[] args, out string url);

            Assert.IsTrue(result, "Parser should accept HTTP mode with url field");
            Assert.IsNull(command, "Command should be null in HTTP mode");
            Assert.IsNull(args, "Args should be null in HTTP mode");
            Assert.AreEqual("http://localhost:8080/mcp/v1/rpc", url, "URL should be parsed correctly");
        }

        [Test]
        public void UpsertCodexServerBlock_HttpMode_GeneratesUrlField()
        {
            // This test verifies HTTP mode upsert generates url field

            // Force HTTP mode
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorConfigurationCache.Instance.Refresh();

            string existingToml = string.Join("\n", new[] { "[other_section]", "key = \"value\"" });

            string uvPath = "C:\\path\\to\\uv.exe";

            string result = CodexConfigHelper.UpsertCodexServerBlock(existingToml, uvPath);

            Assert.IsNotNull(result, "UpsertCodexServerBlock should return a valid TOML string");

            // Parse the generated TOML to validate structure
            TomlTable parsed;
            using (var reader = new StringReader(result))
            {
                parsed = TOML.Parse(reader);
            }

            // Verify existing sections are preserved
            Assert.IsTrue(parsed.TryGetNode("other_section", out _), "TOML should preserve existing sections");

            // Verify mcp_servers structure
            Assert.IsTrue(parsed.TryGetNode("mcp_servers", out var mcpServersNode), "TOML should contain mcp_servers");
            Assert.IsInstanceOf<TomlTable>(mcpServersNode, "mcp_servers should be a table");

            var mcpServers = mcpServersNode as TomlTable;
            Assert.IsTrue(mcpServers.TryGetNode("unityMCP", out var unityMcpNode), "mcp_servers should contain unityMCP");
            Assert.IsInstanceOf<TomlTable>(unityMcpNode, "unityMCP should be a table");

            var unityMcp = unityMcpNode as TomlTable;

            Assert.IsFalse(parsed.TryGetNode("features", out _), "HTTP must not force obsolete global feature flags");

            // Verify url field is present
            Assert.IsTrue(unityMcp.TryGetNode("url", out var urlNode), "unityMCP should contain url in HTTP mode");
            Assert.IsInstanceOf<TomlString>(urlNode, "url should be a string");

            var url = (urlNode as TomlString).Value;
            Assert.IsTrue(url.Contains("http"), "URL should be an HTTP endpoint");

            // Verify command and args are NOT present in HTTP mode
            Assert.IsFalse(unityMcp.TryGetNode("command", out _), "HTTP mode should not contain command field");
            Assert.IsFalse(unityMcp.TryGetNode("args", out _), "HTTP mode should not contain args field");
        }
    }
}
