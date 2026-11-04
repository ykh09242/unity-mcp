using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Clients.Configurators;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Helpers
{
    public class ClaudeHttpAuthTests
    {
        [TestCase(0, false)]
        [TestCase(1, false)]
        [TestCase(2, false)]
        [TestCase(0, true)]
        [TestCase(1, true)]
        [TestCase(2, true)]
        public void StdioRegistrationThroughWindowsShimPreservesLiteralPaths(int captureMode, bool fail)
        {
            if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                Assert.Ignore("Windows npm-shim regression.");
            string root = Path.Combine(Path.GetTempPath(), "mcp-stdio-process-" + Guid.NewGuid().ToString("N"));
            string project = Path.Combine(root, "project");
            string literalSegment = "%MCP_SYNTHETIC_UNDEFINED_" + Guid.NewGuid().ToString("N") + "%";
            string shimDirectory = Path.Combine(root, "Program Files (synthetic cli)");
            string source = Path.Combine(root, "source " + literalSegment + " ! & (editable) ^");
            Directory.CreateDirectory(project);
            Directory.CreateDirectory(shimDirectory);
            Directory.CreateDirectory(source);
            File.WriteAllText(Path.Combine(source, "pyproject.toml"), "# synthetic source only\n");
            string executable = Path.Combine(shimDirectory, "claude.cmd");
            string uvx = Path.Combine(shimDirectory, "uvx " + literalSegment + " ! & (executable) ^.exe");
            File.WriteAllText(
                Path.Combine(shimDirectory, "capture.ps1"),
                "$ErrorActionPreference = 'Stop'\r\n"
                    + "if ($args[1] -eq 'add-json') {\r\n"
                    + "  if ($args.Count -ne 6) { throw 'Unexpected JSON argument count' }\r\n"
                    + "  $entry = $args[5] | ConvertFrom-Json\r\n"
                    + "} elseif ($args[1] -eq 'add' -and $args.Count -gt 9) {\r\n"
                    + "  $entry = @{ type = 'stdio'; command = $args[8]; args = @($args[9..($args.Count - 1)]) }\r\n"
                    + "} else { throw 'Unexpected registration command' }\r\n"
                    + "@{ Prefix = @($args[0..4]); Entry = $entry } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'captured.json') -Encoding UTF8\r\n"
                    + (fail ? "exit 23\r\n" : "exit 0\r\n")
            );
            File.WriteAllText(
                executable,
                "@echo off\r\nsetlocal EnableDelayedExpansion\r\npowershell.exe -NoProfile -NonInteractive -File \"%~dp0capture.ps1\" %*\r\nexit /b %errorlevel%\r\n"
            );
            string profileKey = StartupToolProfile.PreferenceKey(ProjectIdentityUtility.GetProjectHash());
            var stringPreferences = new Dictionary<string, (bool Exists, string Value)>();
            foreach (string key in new[] { EditorPrefKeys.ClientProjectDirOverride, EditorPrefKeys.GitUrlOverride, profileKey })
                stringPreferences[key] = (EditorPrefs.HasKey(key), EditorPrefs.GetString(key, ""));
            var boolPreferences = new Dictionary<string, (bool Exists, bool Value)>();
            foreach (string key in new[] { EditorPrefKeys.UseHttpTransport, EditorPrefKeys.DevModeForceServerRefresh })
                boolPreferences[key] = (EditorPrefs.HasKey(key), EditorPrefs.GetBool(key, false));
            var originalPaths = MCPServiceLocator.Paths;
            try
            {
                MCPServiceLocator.Register<IPathResolverService>(new SyntheticClientPaths { UvxPath = uvx, ClaudePath = executable });
                EditorPrefs.SetString(EditorPrefKeys.ClientProjectDirOverride, project);
                EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, source);
                EditorPrefs.SetString(profileKey, "core");
                EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, false);
                EditorPrefs.SetBool(EditorPrefKeys.DevModeForceServerRefresh, true);
                EditorConfigurationCache.Instance.Refresh();
                var client = new ClaudeCodeConfigurator().CreateOperationClient(Path.Combine(root, "synthetic-user-config.json"));
                Action configure = () =>
                {
                    if (captureMode == 0)
                        client.Configure();
                    else
                    {
                        // Reflection keeps the real-process regression runnable against the
                        // pre-fix API as a negative control; current code uses structured argv.
                        var method = typeof(ClaudeCliMcpConfigurator).GetMethod("ConfigureWithCapturedValues");
                        bool structured = method.GetParameters().Length == 14;
                        var captured = new List<object>
                        {
                            project,
                            executable,
                            null,
                            false,
                            null,
                            uvx,
                            captureMode == 2 || !structured ? AssetPathUtility.GetBetaServerFromArgs(quoteFromPath: true) : null,
                            "mcp-for-unity",
                            "--no-cache --refresh ",
                            null,
                            ConfiguredTransport.Stdio,
                            false,
                            "core",
                        };
                        if (structured)
                            captured.Add(captureMode == 1 ? AssetPathUtility.GetBetaServerFromArgsList() : null);
                        try
                        {
                            method.Invoke(client, captured.ToArray());
                        }
                        catch (TargetInvocationException error) when (error.InnerException != null)
                        {
                            System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(error.InnerException).Throw();
                        }
                    }
                };
                if (fail)
                    Assert.Throws<InvalidOperationException>(() => configure());
                else
                    configure();
                var result = JObject.Parse(File.ReadAllText(Path.Combine(shimDirectory, "captured.json")));
                Assert.AreEqual("stdio", (string)result["Entry"]["type"]);
                Assert.AreEqual(uvx, (string)result["Entry"]["command"]);
                CollectionAssert.AreEqual(
                    new[]
                    {
                        "--no-cache",
                        "--refresh",
                        "--python",
                        ">=3.11",
                        "--from",
                        source,
                        "--with-editable",
                        source,
                        "mcp-for-unity",
                        "--tool-profile",
                        "core",
                    },
                    result["Entry"]["args"].ToObject<string[]>()
                );
                Assert.IsFalse(File.Exists(Path.Combine(root, "synthetic-user-config.json")), "The fake CLI records argv without registering anything.");
            }
            finally
            {
                MCPServiceLocator.Register<IPathResolverService>(originalPaths);
                foreach (var pair in stringPreferences)
                    if (pair.Value.Exists)
                        EditorPrefs.SetString(pair.Key, pair.Value.Value);
                    else
                        EditorPrefs.DeleteKey(pair.Key);
                foreach (var pair in boolPreferences)
                    if (pair.Value.Exists)
                        EditorPrefs.SetBool(pair.Key, pair.Value.Value);
                    else
                        EditorPrefs.DeleteKey(pair.Key);
                EditorConfigurationCache.Instance.Refresh();
                Directory.Delete(root, true);
            }
        }

        [TestCase("C:\\literal %PATH% ! ^ & (source)\\", false)]
        [TestCase("C:\\literal %PATH% ! ^ & (source)\\", true)]
        [TestCase("/tmp/literal \"quoted\" source/", false)]
        [TestCase("/tmp/literal \"quoted\" source/", true)]
        public void LegacyStdioSourceDecodingPreservesGeneratedLiteralData(string source, bool editable)
        {
            string formatted = "--python \">=3.11\" --from \"" + source + "\"";
            var expected = new List<string> { "--python", ">=3.11", "--from", source };
            if (editable)
            {
                formatted += " --with-editable \"" + source + "\"";
                expected.AddRange(new[] { "--with-editable", source });
            }
            var method = typeof(ClaudeCliMcpConfigurator).GetMethod("DecodeLegacyFromArguments", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method, "Generated legacy arguments must be decoded before safe JSON registration.");
            CollectionAssert.AreEqual(expected, (IList<string>)method.Invoke(null, new object[] { formatted }));
        }

        [Test]
        public void UnsupportedLegacyStdioArgumentsFailBeforeRunningCli()
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp-stdio-legacy-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                var client = new ClaudeCodeConfigurator().CreateOperationClient(Path.Combine(root, "synthetic-user-config.json"));
                Assert.Throws<ArgumentException>(() =>
                    client.ConfigureWithCapturedValues(
                        root,
                        "must-never-run",
                        null,
                        false,
                        null,
                        "synthetic-uvx",
                        "--from custom",
                        "mcp-for-unity",
                        "",
                        null,
                        ConfiguredTransport.Stdio,
                        false
                    )
                );
                Assert.IsEmpty(Directory.GetFiles(root));
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

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
            return (string)method.Invoke(client, new object[] { project, executable, url, true, apiKey, null });
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
