using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Clients.Configurators;
using MCPForUnity.Editor.Constants;
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
                    ClaudeHttpAuth.BuildLocalEntry("http://localhost:8080/mcp", "synthetic-cli", existing)
                );
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

        [TestCase(false, null, ".cmd")]
        [TestCase(true, "synthetic %PATH% !BANG! ^ & | < > (header) \"quoted\" \\ tail", ".cmd")]
        [TestCase(true, "synthetic %PATH% !BANG! ^ & | < > (header) \"quoted\" \\ tail", ".bat")]
        public void RemoteRegistrationThroughWindowsShimPreservesUrlHeadersAndFailure(bool complexUrl, string apiKey, string extension)
        {
            if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                Assert.Ignore("Batch shim forwarding is Windows-only.");

            WithIsolatedConfig(
                (directory, project) =>
                {
                    // Given a command shim with npm-style argument forwarding and a failing CLI.
                    string shimDirectory = Path.Combine(directory, "Program Files Like");
                    Directory.CreateDirectory(shimDirectory);
                    string shim = Path.Combine(shimDirectory, "synthetic-claude" + extension);
                    string capture = Path.Combine(shimDirectory, "capture.ps1");
                    File.WriteAllText(
                        capture,
                        "$ErrorActionPreference = 'Stop'\r\n"
                            + "if ($args.Count -ne 6) { throw 'Unexpected argument count' }\r\n"
                            + "$entry = $args[5] | ConvertFrom-Json\r\n"
                            + "@{ Prefix = @($args[0..4]); Entry = $entry } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'captured.json') -Encoding UTF8\r\n"
                            + "Write-Output 'synthetic-cli-failed'\r\nexit 23\r\n"
                    );
                    File.WriteAllText(
                        shim,
                        "@echo off\r\nsetlocal EnableDelayedExpansion\r\npowershell.exe -NoProfile -NonInteractive -File \"%~dp0capture.ps1\" %*\r\nexit /b %errorlevel%\r\n"
                    );
                    string rawUrl = "https://example.invalid/proxy/mcp?region=one&echo";
                    if (complexUrl)
                        rawUrl += "&value=%PATH%!BANG!^|<>()&space=one two&quote=\"literal\"&slash=\\";
                    string url = new Uri(rawUrl).AbsoluteUri;
                    var originalProbe = ClaudeHttpAuth.SupportsHeadersHelper;
                    try
                    {
                        ClaudeHttpAuth.SupportsHeadersHelper = _ => throw new AssertionException("Remote registration must not require headersHelper support.");
                        string arguments = BuildRemoteRegistrationArguments(directory, project, shim, url, apiKey);

                        // When the real command execution adapter launches the shim.
                        bool ok = ExecPath.TryRun(shim, arguments, project, out string stdout, out string stderr, out bool running, 10000);

                        // Then the failed registration stays failed and its data reaches the CLI exactly.
                        Assert.IsFalse(ok, stdout + stderr);
                        Assert.IsFalse(running);
                        Assert.AreEqual("synthetic-cli-failed", stdout.Trim(), "A URL fragment must never become a second command.");
                        var result = JObject.Parse(File.ReadAllText(Path.Combine(shimDirectory, "captured.json")));
                        CollectionAssert.AreEqual(new[] { "mcp", "add-json", "--scope", "local", "UnityMCP" }, result["Prefix"].ToObject<string[]>());
                        Assert.AreEqual("http", (string)result["Entry"]["type"]);
                        Assert.AreEqual(url, (string)result["Entry"]["url"]);
                        Assert.IsNull(result["Entry"]["headersHelper"]);
                        if (apiKey == null)
                            Assert.IsNull(result["Entry"]["headers"]);
                        else
                            Assert.AreEqual(apiKey, (string)result["Entry"]["headers"][AuthConstants.ApiKeyHeader]);
                    }
                    finally
                    {
                        ClaudeHttpAuth.SupportsHeadersHelper = originalProbe;
                    }
                }
            );
        }

        private static string BuildRemoteRegistrationArguments(string directory, string project, string executable, string url, string apiKey)
        {
            var client = new ClaudeCodeConfigurator().CreateOperationClient(Path.Combine(directory, "synthetic-user-config.json"));
            var method = typeof(ClaudeCliMcpConfigurator).GetMethod("BuildHttpRegistrationArguments", BindingFlags.Instance | BindingFlags.NonPublic);
            return (string)method.Invoke(client, new object[] { project, executable, url, true, apiKey });
        }

        [Test]
        public void ConfigDirectoryOverrideAndScopePrecedenceMatchCli()
        {
            WithIsolatedConfig(
                (directory, project) =>
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
                }
            );
        }

        [Test]
        public void ShadowedUserAuthenticationIsNotRemovedByReconfiguration()
        {
            WithIsolatedConfig(
                (directory, project) =>
                {
                    var config = new JObject
                    {
                        ["projects"] = new JObject { [project] = new JObject { ["mcpServers"] = Servers("local") } },
                        ["mcpServers"] = new JObject { ["UnityMCP"] = new JObject { ["headersHelper"] = "custom-helper" } },
                    };
                    string path = Path.Combine(directory, ".claude.json");
                    string before = config.ToString();
                    File.WriteAllText(path, before);
                    var method = typeof(ClaudeCliMcpConfigurator).GetMethod("GetExistingConfigForRegistration", BindingFlags.Instance | BindingFlags.NonPublic);
                    var local = (JObject)method.Invoke(new ClaudeCodeConfigurator(), new object[] { project });
                    Assert.AreEqual("local", (string)local["command"]);
                    Assert.AreEqual(before, File.ReadAllText(path));
                }
            );
        }

        private static JObject Servers(string command) =>
            new JObject
            {
                ["UnityMCP"] = new JObject { ["type"] = "stdio", ["command"] = command },
            };

        private static JObject ReadRegistration(string project)
        {
            var method = typeof(ClaudeCliMcpConfigurator).GetMethod("ReadClaudeCodeConfig", BindingFlags.Instance | BindingFlags.NonPublic);
            var result = ((JObject serverConfig, string error))method.Invoke(new ClaudeCodeConfigurator(), new object[] { project });
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
