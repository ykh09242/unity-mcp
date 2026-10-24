using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Runtime.Helpers;
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
            try
            {
                var provided = RoslynAssemblyCompatibility.GetProvided(folder, LoadedAssemblies());
                foreach (var entry in NuGetEntries)
                {
                    if (!provided.Contains(entry.dllName))
                        ValidateAssembly(Path.Combine(folder, entry.dllName), entry.dllName, entry.version);
                }
                return true;
            }
            catch
            {
                return false;
            }
        }

        /// <summary>Install missing dependencies; an optional total deadline applies to each dependency including its retries.</summary>
        public static Task InstallAsync(bool interactive = true, CancellationToken cancellationToken = default) =>
            InstallAsync(interactive, cancellationToken, null);

        public static async Task InstallAsync(bool interactive, CancellationToken cancellationToken, TimeSpan? totalDownloadTimeout)
        {
            if (
                IsInstalled()
                && interactive
                && !EditorUtility.DisplayDialog(
                    "Roslyn Already Installed",
                    "Compatible Roslyn DLLs are already available from this project.\nReinstall MCP-owned dependencies?",
                    "Reinstall",
                    "Cancel"
                )
            )
                return;
            string folder = Path.Combine(Application.dataPath, PluginsRelPath);
            int count = 0;
            using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            try
            {
                await InstallFromPackagesAsync(
                    folder,
                    async url =>
                    {
                        byte[] package = await RoslynDownload.FetchAsync(
                            () => new UnityTransfer(url),
                            cancellation.Token,
                            (bytes, progress, attempt) =>
                            {
                                if (
                                    interactive
                                    && EditorUtility.DisplayCancelableProgressBar(
                                        "Installing Roslyn",
                                        $"{Path.GetFileName(new Uri(url).AbsolutePath)}: {bytes / 1024} KiB (attempt {attempt}/3)",
                                        (count + Math.Max(0, progress)) / NuGetEntries.Length
                                    )
                                )
                                    cancellation.Cancel();
                            },
                            new RoslynDownload.Policy { TotalTimeout = totalDownloadTimeout }
                        );
                        count++;
                        return package;
                    },
                    ValidateAssembly,
                    cancellation.Token,
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
                    EditorUtility.DisplayDialog(
                        "Roslyn Installed",
                        $"Compatible Roslyn dependencies are available. Missing DLLs were validated and installed to Assets/{PluginsRelPath}/; existing packages were reused.",
                        "OK"
                    );
                Debug.Log($"[MCP] Roslyn installation complete ({NuGetEntries.Length} compatible dependencies available).");
            }
            catch (OperationCanceledException)
            {
                Debug.Log("[MCP] Roslyn installation cancelled before publication.");
                throw;
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
            Action refresh = null,
            Func<IEnumerable<(AssemblyName identity, string location)>> assemblyInventory = null
        )
        {
            cancellationToken.ThrowIfCancellationRequested();
            assemblyInventory ??= LoadedAssemblies;
            var provided = RoslynAssemblyCompatibility.GetProvided(folder, assemblyInventory());
            var missing = NuGetEntries.Where(entry => !provided.Contains(entry.dllName)).ToArray();
            if (missing.Length == 0)
                return;
            string[] names = missing.Select(entry => entry.dllName).ToArray();
            using var files = new RoslynFileSet(folder, names);
            foreach (var entry in missing)
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
            void Publish()
            {
                cancellationToken.ThrowIfCancellationRequested();
                var current = RoslynAssemblyCompatibility.GetProvided(folder, assemblyInventory());
                if (!provided.SetEquals(current))
                    throw new IOException("Loaded Roslyn dependencies changed while downloading. Retry installation using the current package set.");
                files.Install(names);
            }
            if (publication == null)
                Publish();
            else
                publication(Publish);
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
            if (assembly.Name != Path.GetFileNameWithoutExtension(name) || !RoslynAssemblyCompatibility.IsCompatible(assembly))
                throw new IOException($"Unexpected assembly identity or version: {name}");
        }

        private static IEnumerable<(AssemblyName identity, string location)> LoadedAssemblies()
        {
            foreach (var assembly in UnityAssembliesCompat.GetLoadedAssemblies())
            {
                string location;
                try
                {
                    location = UnityAssembliesCompat.GetAssemblyPath(assembly);
                }
                catch (NotSupportedException)
                {
                    location = null;
                }
                yield return (assembly.GetName(), location);
            }
        }

        internal sealed class UnityTransfer : RoslynDownload.ITransfer
        {
            private readonly UnityWebRequest request;

            internal UnityTransfer(string url)
            {
                request = UnityWebRequest.Get(url);
                // The watchdog uses progress, rather than timing out a healthy slow body.
                request.timeout = 0;
            }

            public void Start() => request.SendWebRequest();

            public bool IsDone => request.isDone;
            public ulong DownloadedBytes => request.downloadedBytes;
            public float Progress => request.downloadProgress;
            public bool Succeeded => request.result == UnityWebRequest.Result.Success;
            public bool Retryable =>
                request.result == UnityWebRequest.Result.ConnectionError
                || request.responseCode == 408
                || request.responseCode == 429
                || request.responseCode == 500
                || request.responseCode == 502
                || request.responseCode == 503
                || request.responseCode == 504;
            public string Error => request.error;
            public byte[] Data => request.downloadHandler.data;

            public void Abort() => request.Abort();

            public void Dispose() => request.Dispose();
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
