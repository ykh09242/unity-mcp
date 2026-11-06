using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    /// <summary>
    /// Owns a private, same-project filesystem snapshot until main-thread publication.
    /// Prepare uses no Unity APIs. Its caller must await the worker before disposing this owner.
    /// </summary>
    internal sealed class LocalModelPreparation : IDisposable
    {
        private static readonly ArtifactPreparationQueue Workers = new(1);
        private readonly BundlePlan plan;
        private readonly CancellationToken token;
        private readonly string temporaryRoot;
        private readonly List<string> directories = new();
        private LocalAssetCopy copying;
        private AssetFolderScope folders;
        private bool committed;
        private bool disposed;

        private LocalModelPreparation(BundlePlan plan, string projectRoot, CancellationToken token)
        {
            this.plan = plan;
            this.token = token;
            temporaryRoot = SafePathUtility.ResolveWithinRoot(projectRoot, "Temp/MCPForUnity/LocalModels/" + Guid.NewGuid().ToString("N"));
        }

        internal static async Task<LocalModelPreparation> PrepareAsync(string source, string assetsRoot, CancellationToken token)
        {
            // Bundle size is unknown until inspection; bound metadata scans as well as copies.
            using (await Workers.AcquireAsync(long.MaxValue, token).ConfigureAwait(false))
                return await Task.Run(() => Prepare(source, assetsRoot, token), token).ConfigureAwait(false);
        }

        internal static LocalModelPreparation Prepare(string source, string assetsRoot, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            string extension = Path.GetExtension(source).ToLowerInvariant();
            BundlePlan plan;
            if (extension == ".obj" || extension == ".gltf" || extension == ".glb")
                plan = ModelDependencyBundle.Plan(source, cancellationToken: token, assetsRoot: assetsRoot);
            else
            {
                long length = new FileInfo(source).Length;
                if (length <= 0 || length > AssetDownloadTransport.MaxDownloadBytes)
                    throw new IOException("Local model exceeds the 8 GiB artifact limit or is empty.");
                plan = new BundlePlan
                {
                    AssetsRoot = assetsRoot,
                    SourceRoot = Path.GetDirectoryName(source),
                    Bytes = length,
                    Limits = new BundleLimits(),
                };
                plan.Entries.Add(
                    new BundleEntry
                    {
                        RelativePath = Path.GetFileName(source),
                        AbsolutePath = source,
                        Length = length,
                    }
                );
                plan.Evidence["inspection"] = extension == ".zip" ? "archive_managed" : "not_scanned";
                plan.Evidence.Remove("inspection_scope");
                plan.Evidence["planned_file_count"] = 1;
            }

            var owner = new LocalModelPreparation(plan, Path.GetDirectoryName(assetsRoot), token);
            try
            {
                owner.copying = new LocalAssetCopy(Path.GetDirectoryName(assetsRoot), plan.Bytes, token);
                owner.EnsurePrivateDirectory(owner.temporaryRoot);
                for (int ordinal = 0; ordinal < plan.Entries.Count; ordinal++)
                {
                    token.ThrowIfCancellationRequested();
                    BundleEntry entry = plan.Entries[(ordinal + 1) % plan.Entries.Count]; // main last
                    string input = ModelDependencyBundle.ResolveSource(plan, entry.RelativePath);
                    string destination = SafePathUtility.ResolveWithinRoot(owner.temporaryRoot, entry.RelativePath);
                    owner.EnsurePrivateDirectory(Path.GetDirectoryName(destination));
                    owner.copying.Copy(input, destination, entry.Length, entry.MetadataHash);
                }
                token.ThrowIfCancellationRequested();
                // The worker has closed every stream. Publication retains byte credit, but
                // must not hold the copy slot while awaiting an Editor-thread continuation.
                owner.copying.CompleteTransfer();
                return owner;
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested)
            {
                owner.Dispose();
                throw;
            }
            catch (Exception e)
            {
                owner.Dispose();
                string code =
                    e is BundleImportException known ? (string)known.Evidence["error_code"]
                    : e is IOException
                    && (e.Message.StartsWith("Local asset", StringComparison.Ordinal) || e.Message.StartsWith("Local model", StringComparison.Ordinal))
                        ? "dependency_source_changed"
                    : "dependency_stage_failed";
                throw new BundleImportException(
                    code,
                    "Dependency preparation failed: " + e.Message,
                    plan.Evidence,
                    e is BundleImportException failure ? (string)failure.Evidence["reference"] : null
                );
            }
        }

        // The unique private root is ours. Shared Temp ancestors are never claimed or removed.
        private void EnsurePrivateDirectory(string path)
        {
            if (Directory.Exists(path))
                return;
            if (path != temporaryRoot)
                EnsurePrivateDirectory(Path.GetDirectoryName(path));
            Directory.CreateDirectory(path);
            directories.Add(path);
        }

        // Must run on the Editor thread. No byte copying or metadata parsing occurs here.
        internal BundleStageResult Publish(string outputFolder, string requestedName)
        {
            token.ThrowIfCancellationRequested();
            string root = AssetGenPaths.ResolveOutputFolder(outputFolder, "Imported").TrimEnd('/');
            string name = string.IsNullOrWhiteSpace(requestedName) ? Path.GetFileNameWithoutExtension(plan.Entries[0].RelativePath) : requestedName;
            foreach (char c in Path.GetInvalidFileNameChars())
                name = name.Replace(c, '_');
            name = name.Trim();
            ModelDependencyBundle.ValidatePath(plan, name);
            bool bundle = plan.Entries.Count > 1;
            string selected = null;
            for (int i = 0; i < plan.Limits.MaxNameAttempts; i++)
            {
                token.ThrowIfCancellationRequested();
                string candidate = root + "/" + name + (i == 0 ? "" : "_" + i) + (bundle ? "" : Path.GetExtension(plan.Entries[0].RelativePath));
                if (Available(candidate))
                {
                    selected = candidate;
                    break;
                }
            }
            if (selected == null)
                throw new BundleImportException(
                    "dependency_limit_exceeded",
                    "Could not allocate a fresh destination within the collision limit.",
                    plan.Evidence
                );
            foreach (BundleEntry entry in plan.Entries)
            {
                token.ThrowIfCancellationRequested();
                if (!Available(bundle ? selected + "/" + entry.RelativePath : selected))
                    throw new BundleImportException(
                        "dependency_stage_failed",
                        "Destination identity appeared before publication; existing assets are preserved.",
                        plan.Evidence
                    );
            }

            folders = new AssetFolderScope();
            folders.EnsureFolder(root);
            token.ThrowIfCancellationRequested();
            if (!Available(selected))
                throw new BundleImportException(
                    "dependency_stage_failed",
                    "Destination identity appeared before publication; existing assets are preserved.",
                    plan.Evidence
                );
            string absolute = AssetGenPaths.ToAbsolute(selected);
            if (bundle)
            {
                var relocated = new List<string>(directories.Count);
                foreach (string directory in directories)
                    relocated.Add(absolute + directory.Substring(temporaryRoot.Length));
                copying.MoveDirectory(temporaryRoot, absolute);
                directories.Clear();
                directories.AddRange(relocated);
            }
            else
                copying.MoveFile(SafePathUtility.ResolveWithinRoot(temporaryRoot, plan.Entries[0].RelativePath), absolute);
            token.ThrowIfCancellationRequested();

            string main = bundle ? selected + "/" + plan.Entries[0].RelativePath : selected;
            var evidence = (JObject)plan.Evidence.DeepClone();
            evidence["stage"] = "staged";
            evidence["main_asset_path"] = main;
            evidence["staged_file_count"] = plan.Entries.Count;
            evidence["staged_bytes"] = plan.Bytes;
            if (bundle)
                evidence["root_path"] = selected;
            return new BundleStageResult
            {
                MainAssetPath = main,
                BundleRoot = bundle ? selected : null,
                Evidence = evidence,
            };
        }

        private static bool Available(string path)
        {
            string absolute = AssetGenPaths.ToAbsolute(path);
            string metadata = AssetGenPaths.ToAbsolute(path + ".meta");
            return !File.Exists(absolute)
                && !Directory.Exists(absolute)
                && !File.Exists(metadata)
                && !Directory.Exists(metadata)
                && string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets));
        }

        internal void Commit()
        {
            token.ThrowIfCancellationRequested();
            copying.Complete();
            folders.Complete();
            committed = true;
        }

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            copying?.Dispose();
            for (int i = directories.Count - 1; i >= 0; i--)
            {
                if (committed && !directories[i].StartsWith(temporaryRoot, StringComparison.Ordinal))
                    continue;
                try
                {
                    Directory.Delete(directories[i], false);
                }
                catch (IOException) { /* Never remove unowned contents or release retained file credit. */ }
                catch (UnauthorizedAccessException) { /* Preserve surviving bytes and their reservation. */ }
            }
            folders?.Dispose();
        }
    }
}
