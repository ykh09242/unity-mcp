using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using UnityEditor;
using UnityEngine;
using UnityEngine.Networking;

namespace MCPForUnity.Editor.Setup
{
    public static class RoslynInstaller
    {
        private const string PluginsRelPath = "Plugins/Roslyn";

        private static readonly (string packageId, string version, string dllPath, string dllName)[] NuGetEntries =
        {
            ("microsoft.codeanalysis.common", "4.12.0", "lib/netstandard2.0/Microsoft.CodeAnalysis.dll", "Microsoft.CodeAnalysis.dll"),
            ("microsoft.codeanalysis.csharp", "4.12.0", "lib/netstandard2.0/Microsoft.CodeAnalysis.CSharp.dll", "Microsoft.CodeAnalysis.CSharp.dll"),
            ("system.collections.immutable", "8.0.0", "lib/netstandard2.0/System.Collections.Immutable.dll", "System.Collections.Immutable.dll"),
            ("system.reflection.metadata", "8.0.0", "lib/netstandard2.0/System.Reflection.Metadata.dll", "System.Reflection.Metadata.dll"),
            // Transitive dep of Microsoft.CodeAnalysis.* on netstandard2.0. Without it, Roslyn's StringTable
            // static cctor throws FileNotFoundException for v6.0.0.0 and every Roslyn entry point fails to
            // initialize. Unity ships a v4.x of this assembly which does NOT satisfy the v6 reference.
            (
                "system.runtime.compilerservices.unsafe",
                "6.0.0",
                "lib/netstandard2.0/System.Runtime.CompilerServices.Unsafe.dll",
                "System.Runtime.CompilerServices.Unsafe.dll"
            ),
        };

        public static bool IsInstalled()
        {
            string folder = Path.Combine(Application.dataPath, PluginsRelPath);
            foreach (var entry in NuGetEntries)
            {
                string path = Path.Combine(folder, entry.dllName);
                if (!File.Exists(path))
                    return false;

                // Defense-in-depth: a stale DLL whose assembly version is older than what
                // Roslyn references (e.g. a v4.x System.Runtime.CompilerServices.Unsafe
                // shadowing the v6 we actually need) would still satisfy file-existence but
                // leave Roslyn unable to load. Compare the on-disk assembly version against
                // each entry's declared NuGet version, treating "older or unreadable" as not
                // installed so InstallAsync() can rewrite it.
                if (Version.TryParse(entry.version, out var requiredVersion))
                {
                    try
                    {
                        var actual = AssemblyName.GetAssemblyName(path).Version;
                        if (actual == null || actual < requiredVersion)
                            return false;
                    }
                    catch
                    {
                        return false;
                    }
                }
            }
            return true;
        }

        public static async Task InstallAsync(bool interactive = true, CancellationToken cancellationToken = default)
        {
            if (
                IsInstalled()
                && interactive
                && !EditorUtility.DisplayDialog(
                    "Roslyn Already Installed",
                    $"Roslyn DLLs are already present in Assets/{PluginsRelPath}.\nReinstall?",
                    "Reinstall",
                    "Cancel"
                )
            )
                return;
            string folder = Path.Combine(Application.dataPath, PluginsRelPath);
            int count = 0;
            try
            {
                await InstallFromPackagesAsync(
                    folder,
                    async url =>
                    {
                        cancellationToken.ThrowIfCancellationRequested();
                        if (interactive)
                            EditorUtility.DisplayProgressBar(
                                "Installing Roslyn",
                                "Downloading and validating dependencies...",
                                (float)count++ / NuGetEntries.Length
                            );
                        using var request = UnityWebRequest.Get(url);
                        request.timeout = 30;
                        var operation = request.SendWebRequest();
                        var completion = new TaskCompletionSource<bool>();
                        if (operation.isDone)
                            completion.TrySetResult(true);
                        else
                            operation.completed += _ => completion.TrySetResult(true);
                        await completion.Task;
                        cancellationToken.ThrowIfCancellationRequested();
                        if (request.result != UnityWebRequest.Result.Success)
                            throw new IOException($"Failed to download a Roslyn dependency: {request.error}");
                        return request.downloadHandler.data;
                    },
                    ValidateAssembly,
                    cancellationToken,
                    publish =>
                    {
                        EditorApplication.LockReloadAssemblies();
                        AssetDatabase.DisallowAutoRefresh();
                        try
                        {
                            publish();
                        }
                        finally
                        {
                            AssetDatabase.AllowAutoRefresh();
                            EditorApplication.UnlockReloadAssemblies();
                        }
                    },
                    () => AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport)
                );
                if (!IsInstalled())
                    throw new IOException("The published Roslyn assembly set could not be validated.");
                if (interactive)
                    EditorUtility.DisplayDialog("Roslyn Installed", $"Validated Roslyn DLLs and dependencies installed to Assets/{PluginsRelPath}/.", "OK");
                Debug.Log($"[MCP] Roslyn installation complete ({NuGetEntries.Length} validated DLLs).");
            }
            catch (Exception ex)
            {
                Debug.LogError($"[MCP] Failed to install Roslyn: {ex.Message}");
                if (interactive)
                    EditorUtility.DisplayDialog("Installation Failed", ex.Message, "OK");
                throw;
            }
            finally
            {
                if (interactive)
                    EditorUtility.ClearProgressBar();
            }
        }

        internal static async Task InstallFromPackagesAsync(
            string folder,
            Func<string, Task<byte[]>> download,
            Action<string, string, string> validate = null,
            CancellationToken cancellationToken = default,
            Action<Action> publication = null,
            Action refresh = null
        )
        {
            cancellationToken.ThrowIfCancellationRequested();
            string[] names = NuGetEntries.Select(entry => entry.dllName).ToArray();
            using var files = new RoslynFileSet(folder, names);
            foreach (var entry in NuGetEntries)
            {
                cancellationToken.ThrowIfCancellationRequested();
                byte[] package = await download(
                    $"https://api.nuget.org/v3-flatcontainer/{entry.packageId}/{entry.version}/{entry.packageId}.{entry.version}.nupkg"
                );
                cancellationToken.ThrowIfCancellationRequested();
                byte[] dll = ExtractFileFromZip(package, entry.dllPath);
                string staged = files.StagePath(entry.dllName);
                File.WriteAllBytes(staged, dll);
                (validate ?? ValidateAssembly)(staged, entry.dllName, entry.version);
            }
            cancellationToken.ThrowIfCancellationRequested();
            var missingMetadata = names.Where(name => !File.Exists(files.PathFor(name + ".meta"))).ToArray();
            if (publication == null)
                files.Install(names);
            else
                publication(() => files.Install(names));
            if (refresh != null)
            {
                refresh();
                files.RecordCreatedMetadata(missingMetadata);
            }
        }

        internal static int UninstallOwned(string folder)
        {
            MCPForUnity.Editor.Helpers.GuardedFileSystem.CheckPath(folder);
            if (!Directory.Exists(folder))
                return 0;
            using var files = new RoslynFileSet(folder, NuGetEntries.Select(entry => entry.dllName));
            return files.Uninstall(NuGetEntries.Select(entry => entry.dllName).ToArray());
        }

        private static void ValidateAssembly(string path, string name, string version)
        {
            var assembly = AssemblyName.GetAssemblyName(path);
            if (assembly.Name != Path.GetFileNameWithoutExtension(name) || assembly.Version == null || assembly.Version < Version.Parse(version))
                throw new IOException($"Unexpected assembly identity or version: {name}");
        }

        private static byte[] ExtractFileFromZip(byte[] zipBytes, string entryPath)
        {
            const int maxDllBytes = 64 * 1024 * 1024;
            if (zipBytes == null || zipBytes.Length > 128 * 1024 * 1024)
                throw new IOException("Invalid Roslyn dependency archive size.");
            using var stream = new MemoryStream(zipBytes);
            using var archive = new ZipArchive(stream, ZipArchiveMode.Read);
            var matches = archive.Entries.Where(entry => entry.FullName.Replace('\\', '/').Equals(entryPath, StringComparison.OrdinalIgnoreCase)).ToArray();
            if (matches.Length != 1 || matches[0].Length == 0 || matches[0].Length > maxDllBytes)
                throw new IOException($"Missing, duplicate or invalid Roslyn archive entry: {entryPath}");
            using var input = matches[0].Open();
            using var output = new MemoryStream();
            var buffer = new byte[81920];
            int read;
            while ((read = input.Read(buffer, 0, buffer.Length)) != 0)
            {
                if (output.Length + read > maxDllBytes)
                    throw new IOException($"Roslyn DLL exceeds the size limit: {entryPath}");
                output.Write(buffer, 0, read);
            }
            return output.ToArray();
        }
    }
}
