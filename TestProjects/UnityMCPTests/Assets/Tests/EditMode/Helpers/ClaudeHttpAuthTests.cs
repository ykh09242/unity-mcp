using System;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Helpers
{
    public class ClaudeHttpAuthTests
    {
        [TestCase("2.1.193 (Claude Code)", true)]
        [TestCase("2.1.292 (Claude Code)\n", true)]
        [TestCase("2.1.192 (Claude Code)", false)]
        [TestCase("1.0.119 (Claude Code)", false)]
        [TestCase("2.1.193-beta (Claude Code)", false)]
        [TestCase("2.1.292 (Other CLI)", false)]
        [TestCase(null, false)]
        public void VersionGateRequiresDocumentedAuthenticationRefresh(string output, bool supported)
        {
            Assert.AreEqual(supported, ClaudeHttpAuth.RecognizesVersion(output));
        }

        [Test]
        public void LocalEntryKeepsCustomHeadersWithoutCopyingLaunchToken()
        {
            var originalProbe = ClaudeHttpAuth.SupportsHeadersHelper;
            try
            {
                ClaudeHttpAuth.SupportsHeadersHelper = _ => true;
                var existing = JObject.Parse("{\"headers\":{\"X-Unity-MCP-Token\":\"synthetic-old\",\"X-Custom\":\"keep\"}}");
                var snapshot = existing.DeepClone();
                var entry = ClaudeHttpAuth.BuildLocalEntry("http://127.0.0.1:8080/mcp", "synthetic-cli", existing);
                Assert.IsTrue(ClaudeHttpAuth.IsManagedHelper(entry));
                Assert.IsTrue(ClaudeHttpAuth.TryValidateLocalEntry(entry, "synthetic-cli", out string reason), reason);
                Assert.AreEqual("keep", (string)entry["headers"]["X-Custom"]);
                StringAssert.DoesNotContain("synthetic-old", entry.ToString());
                Assert.IsTrue(JToken.DeepEquals(snapshot, existing));
            }
            finally
            {
                ClaudeHttpAuth.SupportsHeadersHelper = originalProbe;
            }
        }

        [Test]
        public void UnknownCliLeavesExistingEntryUntouchedAndSuggestsStdio()
        {
            var originalProbe = ClaudeHttpAuth.SupportsHeadersHelper;
            try
            {
                ClaudeHttpAuth.SupportsHeadersHelper = _ => false;
                var existing = JObject.Parse("{\"type\":\"stdio\",\"command\":\"keep\"}");
                var snapshot = existing.DeepClone();
                var error = Assert.Throws<InvalidOperationException>(() =>
                    ClaudeHttpAuth.BuildLocalEntry("http://localhost:8080/mcp", "synthetic-cli", existing));
                StringAssert.Contains("stdio", error.Message);
                Assert.IsTrue(JToken.DeepEquals(snapshot, existing));
            }
            finally
            {
                ClaudeHttpAuth.SupportsHeadersHelper = originalProbe;
            }
        }

        [TestCase("{\"headersHelper\":\"custom-command\"}")]
        [TestCase("{\"oauth\":{\"clientId\":\"custom\"}}")]
        public void CustomAuthenticationIsPreservedForManualMigration(string json)
        {
            var existing = JObject.Parse(json);
            var snapshot = existing.DeepClone();
            Assert.Throws<InvalidOperationException>(() => ClaudeHttpAuth.EnsureManagedAuthentication(existing));
            Assert.IsTrue(JToken.DeepEquals(snapshot, existing));
        }

        [Test]
        public void RegistrationArgumentsEscapeWindowsShimMetacharacters()
        {
            var entry = new JObject { ["headersHelper"] = "synthetic %PATH% !BANG! ^ & | < > (helper)" };
            string arguments = ClaudeHttpAuth.BuildRegistrationArguments(entry);
            StringAssert.StartsWith("mcp add-json --scope local UnityMCP ", arguments);
            foreach (char character in "%!^&|<>()")
            {
                StringAssert.DoesNotContain(character.ToString(), arguments);
                StringAssert.Contains("\\u" + ((int)character).ToString("x4"), arguments);
            }
        }

        [Test]
        public void ConfigDirectoryOverrideAndScopePrecedenceMatchCli()
        {
            WithIsolatedConfig((directory, project) =>
            {
                string path = Path.Combine(directory, ".claude.json");
                Assert.AreEqual(path, ClaudeCliMcpConfigurator.GetUserConfigPath());
                var config = new JObject { ["mcpServers"] = Servers("user") };
                File.WriteAllText(path, config.ToString());
                Assert.AreEqual("user", ReadRegistration(project)["command"].Value<string>());

                File.WriteAllText(Path.Combine(project, ".mcp.json"), new JObject { ["mcpServers"] = Servers("project") }.ToString());
                Assert.AreEqual("project", ReadRegistration(project)["command"].Value<string>());

                config["projects"] = new JObject { [project] = new JObject { ["mcpServers"] = Servers("local") } };
                File.WriteAllText(path, config.ToString());
                Directory.CreateDirectory(Path.Combine(project, ".claude"));
                File.WriteAllText(Path.Combine(project, ".claude", "mcp.json"), new JObject { ["mcpServers"] = Servers("legacy") }.ToString());
                Assert.AreEqual("local", ReadRegistration(project)["command"].Value<string>());
            });
        }

        [Test]
        public void ShadowedUserAuthenticationIsNotRemovedByReconfiguration()
        {
            WithIsolatedConfig((directory, project) =>
            {
                var config = new JObject
                {
                    ["projects"] = new JObject { [project] = new JObject { ["mcpServers"] = Servers("local") } },
                    ["mcpServers"] = new JObject { ["UnityMCP"] = new JObject { ["headersHelper"] = "custom-helper" } }
                };
                string path = Path.Combine(directory, ".claude.json");
                string before = config.ToString();
                File.WriteAllText(path, before);
                var method = typeof(ClaudeCliMcpConfigurator).GetMethod("GetExistingConfigForRegistration", BindingFlags.Static | BindingFlags.NonPublic);
                var error = Assert.Throws<TargetInvocationException>(() => method.Invoke(null, new object[] { project }));
                Assert.IsInstanceOf<InvalidOperationException>(error.InnerException);
                Assert.AreEqual(before, File.ReadAllText(path));
            });
        }

        private static JObject Servers(string command) => new JObject
        {
            ["UnityMCP"] = new JObject { ["type"] = "stdio", ["command"] = command }
        };

        private static JObject ReadRegistration(string project)
        {
            var method = typeof(ClaudeCliMcpConfigurator).GetMethod("ReadClaudeCodeConfig", BindingFlags.Static | BindingFlags.NonPublic);
            var result = ((JObject serverConfig, string error))method.Invoke(null, new object[] { project });
            Assert.IsNull(result.error);
            return result.serverConfig;
        }

        private static void WithIsolatedConfig(Action<string, string> test)
        {
            string directory = Path.Combine(Path.GetTempPath(), "unity-mcp-claude-tests-" + Guid.NewGuid().ToString("N"));
            string original = Environment.GetEnvironmentVariable("CLAUDE_CONFIG_DIR");
            try
            {
                string project = Path.Combine(directory, "project");
                Directory.CreateDirectory(Path.Combine(project, ".git"));
                Environment.SetEnvironmentVariable("CLAUDE_CONFIG_DIR", directory);
                test(directory, project);
            }
            finally
            {
                Environment.SetEnvironmentVariable("CLAUDE_CONFIG_DIR", original);
                if (Directory.Exists(directory))
                {
                    Directory.Delete(directory, recursive: true);
                }
            }
        }
    }
}
