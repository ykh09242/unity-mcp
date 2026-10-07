using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Clients.Configurators;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Helpers
{
    public class ConfigJsonIntegrityTests
    {
        [TestCase("{\"X-API-Key\":\"synthetic-current\"}", true)]
        [TestCase("{\"x-api-key\":\"synthetic-current\"}", true)]
        [TestCase("{\"X-API-Key\":\"synthetic-stale\"}", false)]
        [TestCase("{\"X-API-Key\":123}", false)]
        [TestCase("{\"X-API-Key\":\"synthetic-current\",\"x-api-key\":\"duplicate\"}", false)]
        [TestCase("{}", false)]
        [TestCase("[]", false)]
        public void ManagedHttpHeadersAreComparedWithoutLeakingCredentials(string json, bool expected)
        {
            var headers = new Dictionary<string, string> { ["X-API-Key"] = "synthetic-current" };
            Assert.AreEqual(expected, ConfigJsonBuilder.TryValidateAuthHeaders(JToken.Parse(json), headers, out string reason));
            StringAssert.DoesNotContain("synthetic-current", reason ?? string.Empty);
            StringAssert.DoesNotContain("synthetic-stale", reason ?? string.Empty);
        }

        [TestCase("headersHelper")]
        [TestCase("http_headers_helper")]
        public void CustomAuthenticationProviderIsNotOverwritten(string field)
        {
            var root = JObject.Parse("{\"mcpServers\":{\"unityMCP\":{\"" + field + "\":\"custom-provider\"}}}");
            var original = root.DeepClone();
            Assert.Throws<InvalidOperationException>(() => ConfigJsonBuilder.ApplyUnityServerToExistingConfig(root, null, new McpClient()));
            Assert.IsTrue(JToken.DeepEquals(original, root));
        }

        [TestCase("{broken")]
        [TestCase("null")]
        [TestCase("[]")]
        [TestCase("{\"mcp\":[]}")]
        public void OpenCodePreservesInvalidExistingConfiguration(string json)
        {
            WithOwnedRoot(root =>
            {
                string path = Owned(root, "opencode.json");
                File.WriteAllText(path, json);
                var configurator = new OwnedOpenCode(path);
                configurator.Configure();
                Assert.AreEqual(McpStatus.Error, configurator.Status);
                Assert.AreEqual(json, File.ReadAllText(path));
            });
        }

        private sealed class OwnedOpenCode : OpenCodeConfigurator
        {
            private readonly string path;

            public OwnedOpenCode(string path)
            {
                this.path = path;
            }

            public override string GetConfigPath() => path;
        }

        [TestCase("mcpServers", "[]")]
        [TestCase("mcpServers", "null")]
        [TestCase("mcpServers", "false")]
        [TestCase("mcpServers", "\"keep\"")]
        [TestCase("servers", "[]")]
        [TestCase("servers", "null")]
        [TestCase("mcp", "[]")]
        [TestCase("mcp", "null")]
        public void InvalidExistingContainerIsRejectedBeforeTransportOrDocumentChanges(string key, string value)
        {
            var root = JObject.Parse("{\"unrelated\":{\"zero\":0,\"false\":false},\"" + key + "\":" + value + "}");
            var original = root.DeepClone();
            var unrelated = root["unrelated"];
            var client = new McpClient
            {
                IsVsCodeLayout = key == "servers",
                ServerContainerKey = key,
                SchemaUrl = "https://example.invalid/synthetic-schema",
            };

            Assert.Throws<FormatException>(() => ConfigJsonBuilder.ApplyUnityServerToExistingConfig(root, null, client));
            Assert.IsTrue(JToken.DeepEquals(original, root));
            Assert.AreSame(unrelated, root["unrelated"]);
            Assert.IsNull(root["$schema"]);
        }

        [TestCase("[]")]
        [TestCase("null")]
        [TestCase("false")]
        [TestCase("\"keep\"")]
        public void InvalidExistingUnityEntryIsRejectedBeforeTransportOrSiblingChanges(string value)
        {
            var root = JObject.Parse("{\"mcpServers\":{\"other\":{\"zero\":0},\"unityMCP\":" + value + "}}");
            var original = root.DeepClone();
            var other = root["mcpServers"]["other"];

            Assert.Throws<FormatException>(() =>
                ConfigJsonBuilder.ApplyUnityServerToExistingConfig(root, null, new McpClient { SchemaUrl = "https://example.invalid/synthetic-schema" })
            );
            Assert.IsTrue(JToken.DeepEquals(original, root));
            Assert.AreSame(other, root["mcpServers"]["other"]);
            Assert.IsNull(root["$schema"]);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void AtomicWriterPreservesUnrelatedSidecarsForExistingOrNewTarget(bool existing)
        {
            WithOwnedRoot(root =>
            {
                string path = Owned(root, "synthetic.json");
                if (existing)
                    File.WriteAllText(path, "original");
                File.WriteAllText(Owned(root, "synthetic.json.tmp"), "unrelated temp");
                File.WriteAllText(Owned(root, "synthetic.json.backup"), "unrelated backup");

                McpConfigurationHelper.WriteAtomicFile(path, "updated");

                Assert.AreEqual("updated", File.ReadAllText(path));
                Assert.AreEqual("unrelated temp", File.ReadAllText(Owned(root, "synthetic.json.tmp")));
                Assert.AreEqual("unrelated backup", File.ReadAllText(Owned(root, "synthetic.json.backup")));
                Assert.AreEqual(3, Directory.GetFiles(root).Length, "Successful attempt removes only its own sidecars.");
            });
        }

        [Test]
        public void MissingParentWriteFailureDoesNotCreateTargetOrParent()
        {
            WithOwnedRoot(root =>
            {
                string absentParent = Owned(root, "missing-parent");
                string path = Owned(root, "missing-parent/synthetic.json");
                Assert.IsFalse(Directory.Exists(absentParent));
                // Without a parent, temp creation fails before any target mutation.
                Assert.Throws<Exception>(() => McpConfigurationHelper.WriteAtomicFile(path, "updated"));
                Assert.IsFalse(File.Exists(path));
                Assert.IsFalse(Directory.Exists(absentParent));
                Assert.AreEqual(0, Directory.GetFiles(root).Length);
            });
        }

        private static void WithOwnedRoot(Action<string> test)
        {
            string temp = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string root = Path.GetFullPath(Path.Combine(temp, "UnityMCPConfigIntegrity_" + Guid.NewGuid().ToString("N")));
            Assert.IsTrue(root.StartsWith(temp + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
            Directory.CreateDirectory(root);
            try
            {
                test(root);
            }
            finally
            {
                string resolved = Path.GetFullPath(root);
                Assert.IsTrue(resolved.StartsWith(temp + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
                Assert.IsTrue(Path.GetFileName(resolved).StartsWith("UnityMCPConfigIntegrity_", StringComparison.Ordinal));
                if (Directory.Exists(resolved))
                    Directory.Delete(resolved, true);
            }
        }

        private static string Owned(string root, string relative)
        {
            string path = Path.GetFullPath(Path.Combine(root, relative));
            Assert.IsTrue(path.StartsWith(Path.GetFullPath(root) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
            return path;
        }
    }
}
