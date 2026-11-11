using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.ScriptQuality
{
    internal static class AnalysisHostInstallation
    {
        private const string Schema = "unity-mcp/script-analysis-install@1";
        internal static readonly string[] SourceFiles =
        {
            "ScriptDiagnosticsHost.csproj",
            "packages.lock.json",
            "Program.cs",
            "Protocol.cs",
            "WireProtocol.cs",
            "AnalyzerRunner.cs",
            "AnalyzerConfiguration.cs",
            "AssemblyInfo.cs",
            "THIRD-PARTY-NOTICES.md",
        };

        internal static void RequireUnlinked(string path)
        {
            for (
                var item = new FileInfo(Path.GetFullPath(path)) as FileSystemInfo;
                item != null;
                item = item is FileInfo file ? file.Directory : ((DirectoryInfo)item).Parent
            )
            {
                // Walk canonical paths: Unity Mono's Parent repeats the extended drive root.
                try
                {
                    if ((File.GetAttributes(IoPath(item.FullName)) & FileAttributes.ReparsePoint) != 0)
                        throw new IOException("linked_host_path");
                }
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
            }
        }

        internal static JObject Inspect(string root, out string reason)
        {
            reason = "analyzer_unavailable";
            try
            {
                RequireUnlinked(root);
                string path = Path.Combine(root, "current.json");
                if (!File.Exists(path))
                    return null;
                RequireUnlinked(path);
                if (new FileInfo(path).Length > 128 * 1024)
                    throw new IOException("installation_manifest_limit");
                var manifest = JObject.Parse(File.ReadAllText(path));
                string id = (string)manifest["directory"];
                if ((string)manifest["schema"] != Schema || id == null || id.Length != 32 || !id.All(Uri.IsHexDigit))
                    throw new IOException("unowned_host_manifest");
                string host = Path.Combine(root, id, "publish", "ScriptDiagnosticsHost.dll");
                RequireUnlinked(host);
                if (!File.Exists(host))
                    throw new IOException("host_files_missing");
                reason = null;
                return manifest;
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException || error is JsonException)
            {
                reason = error.Message;
                return null;
            }
        }

        internal static string ResolveVerifiedHost(string root)
        {
            JObject manifest = Inspect(root, out string reason) ?? throw new IOException(reason);
            string publish = Path.GetFullPath(Path.Combine(root, (string)manifest["directory"], "publish"));
            var hashes = manifest["hashes"] as JObject ?? throw new IOException("host_integrity_unavailable");
            if (hashes.Count == 0 || hashes.Count > 512)
                throw new IOException("host_integrity_limit");
            long bytes = 0;
            foreach (var entry in hashes.Properties())
            {
                string path = Path.GetFullPath(Path.Combine(publish, entry.Name));
                if (!path.StartsWith(publish + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                    throw new IOException("invalid_host_manifest_path");
                RequireUnlinked(path);
                bytes += new FileInfo(IoPath(path)).Length;
                if (bytes > 128L * 1024 * 1024 || Hash(path) != (string)entry.Value)
                    throw new IOException("host_integrity_changed");
            }
            var actual = Directory
                .GetFiles(publish, "*", SearchOption.AllDirectories)
                .Select(path => Relative(publish, path))
                .OrderBy(p => p, StringComparer.Ordinal);
            if (!actual.SequenceEqual(hashes.Properties().Select(p => p.Name).OrderBy(p => p, StringComparer.Ordinal)))
                throw new IOException("host_inventory_changed");
            return Path.Combine(publish, "ScriptDiagnosticsHost.dll");
        }

        internal static async Task<JObject> InstallAsync(string source, string root, AnalysisHostProcess owner, CancellationToken cancellation)
        {
            RequireUnlinked(source);
            RequireUnlinked(root);
            string pointer = Path.Combine(root, "current.json");
            string expected = File.Exists(pointer) ? Hash(pointer) : null;
            if (expected != null && Inspect(root, out _) == null)
                throw new IOException("unowned_host_manifest");
            string id = Guid.NewGuid().ToString("N");
            string stage = Path.Combine(root, id);
            Directory.CreateDirectory(stage);
            // Only this unique stage is written. Never remove or overwrite a prior install or unknown file.
            foreach (string name in SourceFiles)
            {
                string input = Path.Combine(source, name);
                RequireUnlinked(input);
                if (new FileInfo(input).Length > 2 * 1024 * 1024)
                    throw new IOException("host_source_limit");
                File.Copy(input, Path.Combine(stage, name), false);
            }
            File.WriteAllText(
                Path.Combine(stage, "NuGet.Config"),
                "<configuration><packageSources><clear/><add key=\"nuget.org\" value=\"https://api.nuget.org/v3/index.json\" /></packageSources></configuration>",
                new UTF8Encoding(false)
            );
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
            deadline.CancelAfter(TimeSpan.FromMinutes(10));
            string sdk = Encoding.UTF8.GetString(await owner.RunAsync("dotnet", new[] { "--version" }, stage, null, false, deadline.Token)).Trim();
            if (!Version.TryParse(sdk, out var version) || version.Major < 10)
                throw new IOException("dotnet_sdk_10_required");
            // Freeze the selected SDK for this stage; target projects/global imports are never built.
            File.WriteAllText(
                Path.Combine(stage, "global.json"),
                new JObject
                {
                    ["sdk"] = new JObject { ["version"] = sdk, ["rollForward"] = "disable" },
                }.ToString()
            );
            await owner.RunAsync(
                "dotnet",
                new[]
                {
                    "publish",
                    Path.Combine(stage, "ScriptDiagnosticsHost.csproj"),
                    "--configuration",
                    "Release",
                    "--output",
                    Path.Combine(stage, "publish"),
                    "--disable-build-servers",
                    "--nologo",
                    "--verbosity",
                    "quiet",
                    "-nodeReuse:false",
                    "-maxcpucount:1",
                    "-p:RestoreLockedMode=true",
                    "-p:UseSharedCompilation=false",
                    "-p:ImportDirectoryBuildProps=false",
                    "-p:ImportDirectoryBuildTargets=false",
                    "-p:ImportDirectoryPackagesProps=false",
                    "-p:RestoreConfigFile=" + Path.Combine(stage, "NuGet.Config"),
                },
                stage,
                null,
                false,
                deadline.Token
            );
            string publish = Path.Combine(stage, "publish");
            byte[] capabilities = await owner.RunAsync(
                "dotnet",
                new[] { Path.Combine(publish, "ScriptDiagnosticsHost.dll"), "--capabilities" },
                stage,
                null,
                true,
                deadline.Token
            );
            var result = JObject.Parse(Encoding.UTF8.GetString(capabilities));
            if ((int?)result["protocol"] != 1 || (bool?)result["complete"] != true)
                throw new IOException("host_protocol_mismatch");
            File.Copy(Path.Combine(stage, "THIRD-PARTY-NOTICES.md"), Path.Combine(publish, "THIRD-PARTY-NOTICES.md"), false);
            var hashes = new JObject();
            foreach (string file in Directory.GetFiles(publish, "*", SearchOption.AllDirectories).OrderBy(p => p, StringComparer.Ordinal))
                hashes[Relative(publish, file)] = Hash(file);
            var manifest = new JObject
            {
                ["schema"] = Schema,
                ["directory"] = id,
                ["sdk"] = sdk,
                ["runtime"] = result["providers"]?["runtime"],
                ["verified_at_unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                ["providers"] = result["providers"],
                ["hashes"] = hashes,
            };
            string pending = Path.Combine(stage, "current.json");
            File.WriteAllText(pending, manifest.ToString(Formatting.None), new UTF8Encoding(false));
            deadline.Token.ThrowIfCancellationRequested();
            RequireUnlinked(root);
            if ((File.Exists(pointer) ? Hash(pointer) : null) != expected)
                throw new IOException("host_installation_changed");
            if (expected == null)
                File.Move(pending, pointer);
            else
                File.Replace(pending, pointer, null);
            return manifest;
        }

        private static string Relative(string root, string path) => path.Substring(root.Length + 1).Replace('\\', '/');

        private static string Hash(string path)
        {
            using var stream = File.OpenRead(IoPath(path));
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }

        private static string IoPath(string path)
        {
            string full = Path.GetFullPath(path);
            // Unity Mono cannot open the SDK's long satellite-resource paths without this prefix.
            if (Path.DirectorySeparatorChar != '\\' || full.StartsWith(@"\\?\", StringComparison.Ordinal))
                return full;
            return full.StartsWith(@"\\", StringComparison.Ordinal) ? @"\\?\UNC\" + full.Substring(2) : @"\\?\" + full;
        }
    }
}
