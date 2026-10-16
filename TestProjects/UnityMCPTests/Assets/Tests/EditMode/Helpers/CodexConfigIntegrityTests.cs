using System;
using System.IO;
using System.Text;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.External.Tommy;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Helpers
{
    public class CodexConfigIntegrityTests
    {
        private sealed class TemporaryCodex : CodexMcpConfigurator
        {
            private readonly string path;

            internal TemporaryCodex(string path)
                : base(new McpClient { name = "Temporary Codex" }) => this.path = path;

            public override string GetConfigPath() => path;
        }

        [TestCase('\'', 4)]
        [TestCase('\'', 5)]
        [TestCase('"', 4)]
        [TestCase('"', 5)]
        public void Unregister_MultilineClosingQuotesPreserveFollowingComments(char quote, int closingQuotes)
        {
            string directory = Path.Combine(Path.GetTempPath(), "unity-mcp-codex-quotes-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            string path = Path.Combine(directory, "config.toml");
            string neighbor = "# neighboring server\r\n[mcp_servers.neighbor]\r\ncommand = 'keep'\r\nargs = []\r\n";
            string comments = "# keep this neighboring comment\r\n\r\n# keep this final comment too\r\n";
            string document =
                neighbor + "[mcp_servers.unityMCP]\r\ncommand = " + new string(quote, 3) + "uvx" + new string(quote, closingQuotes) + "\r\n" + comments;
            try
            {
                File.WriteAllText(path, document, new UTF8Encoding(false));
                using (var original = new StringReader(document))
                    Assert.AreEqual("uvx" + new string(quote, closingQuotes - 3), TOML.Parse(original)["mcp_servers"]["unityMCP"]["command"].AsString.Value);
                new TemporaryCodex(path).Unregister();
                CollectionAssert.AreEqual(Encoding.UTF8.GetBytes(neighbor + comments), File.ReadAllBytes(path));
                using var remaining = new StringReader(File.ReadAllText(path));
                Assert.AreEqual("keep", TOML.Parse(remaining)["mcp_servers"]["neighbor"]["command"].AsString.Value);
            }
            finally
            {
                Directory.Delete(directory, true);
            }
        }

        [TestCase("[mcp_servers.unityMCP]\ncommand = 'custom-command'\nargs = ['custom']\n")]
        [TestCase("[\"mcp_servers\".'unityMCP']\nurl = 'https://custom.invalid/mcp'\nenabled = false\n")]
        [TestCase("mcp_servers.unityMCP.command = 'custom-command'\nmcp_servers.unityMCP.args = []\n")]
        [TestCase("[mcp_servers]\nunityMCP = { url = 'https://custom.invalid/mcp', enabled = false }\n")]
        [TestCase("[mcpServers.unityMCP]\ncommand = 'legacy'\nargs = []\n")]
        [TestCase(
            "[mcp_servers.unityMCP]\ncommand = 'uvx'\nargs = [\n  'first', # own array comment\n  '[mcp_servers.neighbor]'\n]\n[mcp_servers.unityMCP.env]\nCUSTOM = 'value'\n"
        )]
        public void Unregister_RemovesReservedEntryAndPreservesNeighborBytes(string owned)
        {
            string directory = Path.Combine(Path.GetTempPath(), "unity-mcp-codex-unregister-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            string path = Path.Combine(directory, "config.toml");
            string prefix = "# global comment\r\nmodel = 'chosen-model'\r\n\r\n";
            string suffix =
                "# keep this neighboring comment\r\n[mcp_servers.customName]\r\n"
                + "command = 'neighbor' # keep inline comment\r\nargs = []\r\n"
                + "description = '''\r\n[mcp_servers.unityMCP]\r\nthis is text\r\n'''\r\n"
                + "[features]\r\ncustom = true\r\n";
            string document = prefix + owned.Replace("\n", "\r\n") + suffix;
            try
            {
                File.WriteAllText(path, document, new UTF8Encoding(true));
                string token = Path.Combine(directory, "token");
                File.WriteAllText(token, "synthetic-shared-token");
                var configurator = new TemporaryCodex(path);
                configurator.Unregister();
                string expected = prefix + (owned.StartsWith("[mcp_servers]\n", StringComparison.Ordinal) ? "[mcp_servers]\r\n" : "") + suffix;
                CollectionAssert.AreEqual(new UTF8Encoding(true).GetPreamble(), new ArraySegment<byte>(File.ReadAllBytes(path), 0, 3));
                Assert.AreEqual(expected, File.ReadAllText(path));
                Assert.AreEqual("synthetic-shared-token", File.ReadAllText(token));
                Assert.AreEqual(McpStatus.NotConfigured, configurator.Status);
                Assert.AreEqual(ConfiguredTransport.Unknown, configurator.ConfiguredTransport);
                using var reader = new StringReader(File.ReadAllText(path));
                var parsed = TOML.Parse(reader);
                Assert.AreEqual("neighbor", parsed["mcp_servers"]["customName"]["command"].AsString.Value);
                Assert.IsFalse(CodexConfigHelper.TryParseCodexServer(File.ReadAllText(path), out _, out _, out _));
                byte[] once = File.ReadAllBytes(path);
                configurator.Unregister();
                CollectionAssert.AreEqual(once, File.ReadAllBytes(path));
            }
            finally
            {
                Directory.Delete(directory, true);
            }
        }

        [TestCase("model = 'custom'\n[mcp_servers.customUnity]\ncommand = 'uvx'\nargs = []\n", false)]
        [TestCase("model = 'unterminated", true)]
        [TestCase("[mcp_servers]\nunityMCP = false\n", true)]
        [TestCase("mcp_servers = { unityMCP = { url = 'https://custom.invalid' }, neighbor = { command = 'keep' } }\n", true)]
        public void Unregister_AbsentOrAmbiguousConfigurationPreservesOriginalBytes(string document, bool failure)
        {
            string directory = Path.Combine(Path.GetTempPath(), "unity-mcp-codex-unregister-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            string path = Path.Combine(directory, "config.toml");
            try
            {
                File.WriteAllText(path, document, new UTF8Encoding(false));
                byte[] original = File.ReadAllBytes(path);
                var configurator = new TemporaryCodex(path);
                if (failure)
                {
                    var error = Assert.Throws<InvalidOperationException>(() => configurator.Unregister());
                    StringAssert.Contains("configuration", error.Message.ToLowerInvariant());
                }
                else
                    configurator.Unregister();
                CollectionAssert.AreEqual(original, File.ReadAllBytes(path));
            }
            finally
            {
                Directory.Delete(directory, true);
            }
        }

        [Test]
        public void Unregister_FailedAtomicReplacementKeepsOriginalBytes()
        {
            if (Environment.OSVersion.Platform != PlatformID.Win32NT)
                Assert.Ignore("Windows file-sharing semantics are required to deny atomic replacement.");
            string directory = Path.Combine(Path.GetTempPath(), "unity-mcp-codex-unregister-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            string path = Path.Combine(directory, "config.toml");
            byte[] original = Encoding.UTF8.GetBytes("# preserve\n[mcp_servers.unityMCP]\ncommand = 'uvx'\nargs = []\n");
            try
            {
                File.WriteAllBytes(path, original);
                using (var held = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                    Assert.Throws<InvalidOperationException>(() => new TemporaryCodex(path).Unregister());
                CollectionAssert.AreEqual(original, File.ReadAllBytes(path));
                Assert.AreEqual(1, Directory.GetFiles(directory).Length, "Failed write must clean temporary/backup files.");
            }
            finally
            {
                Directory.Delete(directory, true);
            }
        }

        [Test]
        public void Unregister_MissingFileIsIdempotent()
        {
            string path = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"), "missing.toml");
            var configurator = new TemporaryCodex(path);
            configurator.Unregister();
            Assert.IsFalse(File.Exists(path));
            Assert.AreEqual(McpStatus.NotConfigured, configurator.Status);
        }

        // These failures are detected before reading preferences or resolving transport.
        [TestCase("model = \"unterminated")]
        [TestCase("[mcp_servers\n")]
        [TestCase("model = 1\nmodel = 2")]
        [TestCase("mcp_servers = 1")]
        [TestCase("mcp_servers = []")]
        [TestCase("mcp_servers = \"invalid\"")]
        [TestCase("[mcp_servers]\nunityMCP = false")]
        [TestCase("[mcp_servers]\nunityMCP = []")]
        [TestCase("[mcp_servers.unityMCP]\nenv = false")]
        [TestCase("[mcp_servers.unityMCP]\nhttp_headers = []")]
        public void Upsert_RejectsInvalidExistingConfiguration(string document)
        {
            Assert.Throws<FormatException>(() => CodexConfigHelper.UpsertCodexServerBlock(document, null));
        }

        [TestCase("mcp_servers")]
        [TestCase("mcp_servers.unityMCP")]
        public void Upsert_ErrorIdentifiesMalformedTableWithoutItsValue(string path)
        {
            string document = path + " = \"synthetic-private-value\"";
            var error = Assert.Throws<FormatException>(() => CodexConfigHelper.UpsertCodexServerBlock(document, null));
            StringAssert.Contains(path, error.Message);
            StringAssert.DoesNotContain("synthetic-private-value", error.Message);
        }

        [Test]
        public void ReadParser_StillReturnsFalseForInvalidToml()
        {
            Assert.IsFalse(CodexConfigHelper.TryParseCodexServer("model = \"", out var command, out var args, out var url));
            Assert.IsNull(command);
            Assert.IsNull(args);
            Assert.IsNull(url);
        }

        [TestCase("mcp_servers")]
        [TestCase("mcpServers")]
        public void ReadParser_PreservesCanonicalAndLegacyStdioCompatibility(string table)
        {
            Assert.IsTrue(
                CodexConfigHelper.TryParseCodexServer(
                    "[" + table + ".unityMCP]\ncommand = \"synthetic-uvx\"\nargs = []",
                    out var command,
                    out var args,
                    out var url
                )
            );
            Assert.AreEqual("synthetic-uvx", command);
            Assert.IsEmpty(args);
            Assert.IsNull(url);
        }

        [Test]
        public void ReadParser_AcceptsInlineHttpTableWithoutEndpointLookup()
        {
            Assert.IsTrue(
                CodexConfigHelper.TryParseCodexServer(
                    "mcp_servers = { unityMCP = { url = \"https://synthetic.invalid/mcp\", enabled = false } }",
                    out var command,
                    out var args,
                    out var url
                )
            );
            Assert.AreEqual("https://synthetic.invalid/mcp", url);
            Assert.IsNull(command);
            Assert.IsNull(args);
        }
    }
}
