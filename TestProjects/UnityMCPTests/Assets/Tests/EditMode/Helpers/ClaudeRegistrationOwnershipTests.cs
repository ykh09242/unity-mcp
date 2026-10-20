using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Models;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Helpers
{
    public class ClaudeRegistrationOwnershipTests
    {
        private string root,
            project,
            configPath,
            originalConfigDir;
        private FakeConfigurator configurator;

        private sealed class FakeConfigurator : ClaudeCliMcpConfigurator
        {
            internal readonly List<string> Calls = new();
            internal Func<string, bool> Run;

            internal FakeConfigurator()
                : base(new McpClient { name = "Synthetic Claude" }) { }

            protected override bool RunCli(
                string executable,
                string arguments,
                string directory,
                out string stdout,
                out string stderr,
                int timeout,
                string pathPrepend
            )
            {
                Calls.Add(arguments);
                stdout = stderr = "synthetic result";
                return Run(arguments);
            }
        }

        private static JObject Servers(string command) =>
            new JObject
            {
                ["UnityMCP"] = new JObject { ["type"] = "stdio", ["command"] = command },
            };

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "mcp-claude-ownership-" + Guid.NewGuid().ToString("N"));
            project = Path.Combine(root, "project");
            Directory.CreateDirectory(project);
            originalConfigDir = Environment.GetEnvironmentVariable("CLAUDE_CONFIG_DIR");
            Environment.SetEnvironmentVariable("CLAUDE_CONFIG_DIR", root);
            configPath = Path.Combine(root, ".claude.json");
            File.WriteAllText(
                configPath,
                new JObject
                {
                    ["projects"] = new JObject
                    {
                        [project] = new JObject { ["mcpServers"] = Servers("local") },
                        [root] = new JObject { ["mcpServers"] = Servers("parent") },
                    },
                    ["mcpServers"] = Servers("user"),
                }.ToString()
            );
            File.WriteAllText(Path.Combine(project, ".mcp.json"), new JObject { ["mcpServers"] = Servers("project") }.ToString());
            configurator = new FakeConfigurator { Run = RemoveLocal };
        }

        [TearDown]
        public void TearDown()
        {
            Environment.SetEnvironmentVariable("CLAUDE_CONFIG_DIR", originalConfigDir);
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        private bool RemoveLocal(string arguments)
        {
            if (arguments.StartsWith("mcp remove --scope local ", StringComparison.Ordinal))
            {
                var config = JObject.Parse(File.ReadAllText(configPath));
                ((JObject)config["projects"][project]["mcpServers"]).Remove(arguments.Substring(arguments.LastIndexOf(' ') + 1));
                config["concurrentOtherSetting"] = "preserve";
                File.WriteAllText(configPath, config.ToString());
            }
            return true;
        }

        private void Unregister() =>
            configurator.ConfigureWithCapturedValues(
                project,
                "synthetic",
                null,
                false,
                null,
                "synthetic",
                "",
                "mcp-for-unity",
                "",
                null,
                ConfiguredTransport.Stdio,
                true
            );

        [Test]
        public void UnregisterPreservesParentUserProjectAndConcurrentConfiguration()
        {
            string shared = File.ReadAllText(Path.Combine(project, ".mcp.json"));
            Unregister();
            Assert.That(configurator.Calls, Has.Count.EqualTo(1));
            Assert.That(configurator.Calls[0], Is.EqualTo("mcp remove --scope local UnityMCP"));
            var config = JObject.Parse(File.ReadAllText(configPath));
            Assert.That((string)config["projects"][root]["mcpServers"]["UnityMCP"]["command"], Is.EqualTo("parent"));
            Assert.That((string)config["mcpServers"]["UnityMCP"]["command"], Is.EqualTo("user"));
            Assert.That((string)config["concurrentOtherSetting"], Is.EqualTo("preserve"));
            Assert.That(File.ReadAllText(Path.Combine(project, ".mcp.json")), Is.EqualTo(shared));
            Assert.That(configurator.Status, Is.EqualTo(McpStatus.Configured), "Shared registration remains visible.");
            Assert.That(configurator.ShouldUnregister, Is.False);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FailedOrIneffectiveCliRemovalIsReported(bool cliSuccess)
        {
            string before = File.ReadAllText(configPath);
            configurator.Run = _ => cliSuccess;
            Assert.Throws<InvalidOperationException>(Unregister);
            Assert.That(configurator.Status, Is.EqualTo(McpStatus.Error));
            Assert.That(File.ReadAllText(configPath), Is.EqualTo(before));
        }

        [Test]
        public void AlreadyAbsentLocalEntryDoesNotRemoveSharedRegistrations()
        {
            var config = JObject.Parse(File.ReadAllText(configPath));
            ((JObject)config["projects"]).Remove(project);
            string before = config.ToString();
            File.WriteAllText(configPath, before);
            Unregister();
            Assert.That(configurator.Calls, Is.Empty);
            Assert.That(File.ReadAllText(configPath), Is.EqualTo(before));
            Assert.That(configurator.Status, Is.EqualTo(McpStatus.Configured));
        }
    }
}
