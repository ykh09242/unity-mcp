using System;
using MCPForUnity.Editor.Helpers;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Helpers
{
    public class CodexConfigIntegrityTests
    {
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
            Assert.IsTrue(CodexConfigHelper.TryParseCodexServer(
                "[" + table + ".unityMCP]\ncommand = \"synthetic-uvx\"\nargs = []",
                out var command, out var args, out var url));
            Assert.AreEqual("synthetic-uvx", command);
            Assert.IsEmpty(args);
            Assert.IsNull(url);
        }

        [Test]
        public void ReadParser_AcceptsInlineHttpTableWithoutEndpointLookup()
        {
            Assert.IsTrue(CodexConfigHelper.TryParseCodexServer(
                "mcp_servers = { unityMCP = { url = \"https://synthetic.invalid/mcp\", enabled = false } }",
                out var command, out var args, out var url));
            Assert.AreEqual("https://synthetic.invalid/mcp", url);
            Assert.IsNull(command);
            Assert.IsNull(args);
        }
    }
}
