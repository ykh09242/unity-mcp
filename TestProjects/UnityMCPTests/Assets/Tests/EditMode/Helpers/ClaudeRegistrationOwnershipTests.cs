using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Helpers;
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

        private void Register() =>
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
                false
            );

        private JObject LocalServers() => (JObject)JObject.Parse(File.ReadAllText(configPath))["projects"][project]["mcpServers"];

        private void WriteLocal(string name, JToken entry)
        {
            var config = JObject.Parse(File.ReadAllText(configPath));
            ((JObject)config["projects"][project]["mcpServers"])[name] = entry;
            File.WriteAllText(configPath, config.ToString());
        }

        private bool Restore(string arguments, JObject previous)
        {
            foreach (var entry in previous.Properties())
            {
                string expected = ClaudeHttpAuth.BuildRegistrationArguments((JObject)entry.Value).Replace(" local UnityMCP ", " local " + entry.Name + " ");
                if (arguments != expected)
                    continue;
                WriteLocal(entry.Name, entry.Value.DeepClone());
                return true;
            }
            return false;
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FailedReplacementRestoresExactEntriesAndKeepsOtherScopes(bool partialRemoval)
        {
            var entry = (JObject)LocalServers()["UnityMCP"];
            entry["args"] = new JArray("quoted \\\"value\\\"", "Unicode 한글", "%PATH% ! &");
            entry["env"] = new JObject { ["SYNTHETIC_VALUE"] = "preserve" };
            WriteLocal("UnityMCP", entry);
            if (partialRemoval)
                WriteLocal("unitymcp", new JObject { ["type"] = "stdio", ["command"] = "variant" });
            JObject previous = LocalServers();
            string shared = File.ReadAllText(Path.Combine(project, ".mcp.json"));
            configurator.Run = arguments =>
            {
                if (arguments.StartsWith("mcp remove ", StringComparison.Ordinal))
                    return arguments.EndsWith(" unitymcp", StringComparison.Ordinal) ? false : RemoveLocal(arguments);
                return Restore(arguments, previous);
            };
            var error = Assert.Throws<InvalidOperationException>(Register);
            Assert.IsTrue(JToken.DeepEquals(previous, LocalServers()));
            StringAssert.Contains("restored", error.Message);
            Assert.That(configurator.Status, Is.EqualTo(McpStatus.Error));
            var config = JObject.Parse(File.ReadAllText(configPath));
            Assert.That((string)config["projects"][root]["mcpServers"]["UnityMCP"]["command"], Is.EqualTo("parent"));
            Assert.That((string)config["mcpServers"]["UnityMCP"]["command"], Is.EqualTo("user"));
            Assert.That((string)config["concurrentOtherSetting"], Is.EqualTo("preserve"));
            Assert.That(File.ReadAllText(Path.Combine(project, ".mcp.json")), Is.EqualTo(shared));
        }

        [TestCase("UnityMCP")]
        [TestCase("unitymcp")]
        public void FailedAddThatWroteAnEntryPreservesObservedReplacement(string name)
        {
            configurator.Run = arguments =>
            {
                if (arguments.StartsWith("mcp remove ", StringComparison.Ordinal))
                    return RemoveLocal(arguments);
                WriteLocal(name, new JObject { ["type"] = "stdio", ["command"] = "concurrent-or-completed-add" });
                return false;
            };
            var error = Assert.Throws<InvalidOperationException>(Register);
            Assert.That(configurator.Calls, Has.Count.EqualTo(2));
            Assert.That((string)LocalServers()[name]["command"], Is.EqualTo("concurrent-or-completed-add"));
            StringAssert.Contains("changed", error.Message);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RecoveryOutcomeIsVerifiedEvenWhenCliReportsFailure(bool actuallyRestored)
        {
            JObject previous = LocalServers();
            configurator.Run = arguments =>
            {
                if (arguments.StartsWith("mcp remove ", StringComparison.Ordinal))
                    return RemoveLocal(arguments);
                if (actuallyRestored)
                    Restore(arguments, previous);
                return false;
            };
            var error = Assert.Throws<InvalidOperationException>(Register);
            Assert.That(configurator.Calls, Has.Count.EqualTo(3));
            StringAssert.Contains(actuallyRestored ? "restored" : "could not restore", error.Message);
            Assert.That(LocalServers().HasValues, Is.EqualTo(actuallyRestored));
        }

        [Test]
        public void UncertainCliExitDoesNotRaceAutomaticRecovery()
        {
            configurator.Run = arguments =>
            {
                if (arguments.StartsWith("mcp remove ", StringComparison.Ordinal))
                    return RemoveLocal(arguments);
                throw new TimeoutException("synthetic uncertainty");
            };
            var error = Assert.Throws<InvalidOperationException>(Register);
            Assert.That(configurator.Calls, Has.Count.EqualTo(2));
            StringAssert.Contains("still be running", error.Message);
            StringAssert.Contains("not attempted", error.Message);
        }

        [Test]
        public void ConcurrentChangeBetweenRemovalsIsNotDeletedOrOverwritten()
        {
            WriteLocal("unitymcp", new JObject { ["type"] = "stdio", ["command"] = "second" });
            configurator.Run = arguments =>
            {
                RemoveLocal(arguments);
                WriteLocal("unitymcp", new JObject { ["type"] = "stdio", ["command"] = "concurrent" });
                return true;
            };
            var error = Assert.Throws<InvalidOperationException>(Register);
            Assert.That(configurator.Calls, Has.Count.EqualTo(1));
            Assert.That((string)LocalServers()["unitymcp"]["command"], Is.EqualTo("concurrent"));
            StringAssert.Contains("changed", error.Message);
        }

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
