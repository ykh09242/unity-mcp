using System;
using System.Collections.Generic;
using System.IO;

namespace MCPForUnity.Runtime.Helpers
{
    /// <summary>Rolls back only newly created, empty output directories after a failed write.</summary>
    public sealed class OutputFolderScope : IDisposable
    {
        private readonly string root;
        private readonly List<(string Path, string Marker)> createdFolders = new();
        private bool completed;
        private bool disposed;

        public OutputFolderScope(string root)
        {
            string fullRoot = Path.GetFullPath(root);
            this.root = SafePathUtility.ResolveWithinRoot(fullRoot, fullRoot);
        }

        public void EnsureParentDirectory(string fullPath)
        {
            if (disposed)
                throw new ObjectDisposedException(nameof(OutputFolderScope));
            string containedPath = SafePathUtility.ResolveWithinRoot(root, fullPath);
            string parent = Path.GetDirectoryName(containedPath);
            if (!string.IsNullOrEmpty(parent))
                EnsureDirectory(parent);
        }

        private void EnsureDirectory(string path)
        {
            path = SafePathUtility.ResolveWithinRoot(root, path);
            if (Directory.Exists(path))
                return;
            if (File.Exists(path))
                throw new IOException($"The output folder '{path}' is occupied by a file.");

            string parent = Path.GetDirectoryName(path);
            if (string.IsNullOrEmpty(parent))
                throw new IOException("The output directory has no existing parent.");
            EnsureDirectory(parent);
            path = SafePathUtility.ResolveWithinRoot(root, path);
            if (Directory.Exists(path))
                return;
            // Publish the directory with its private marker already inside it. A later
            // directory at the same path does not inherit this cleanup authority.
            string marker = ".mcp-output-" + Guid.NewGuid().ToString("N");
            string staging = SafePathUtility.ResolveWithinRoot(root, Path.Combine(parent, marker));
            Directory.CreateDirectory(staging);
            using (File.Open(Path.Combine(staging, marker), FileMode.CreateNew, FileAccess.Write, FileShare.None)) { }
            createdFolders.Add((staging, marker));
            Directory.Move(staging, path);
            createdFolders[createdFolders.Count - 1] = (path, marker);
            SafePathUtility.ResolveWithinRoot(root, path);
        }

        public void Complete() => completed = true;

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            for (int i = createdFolders.Count - 1; i >= 0; i--)
            {
                var folder = createdFolders[i];
                try
                {
                    string path = SafePathUtility.ResolveWithinRoot(root, folder.Path);
                    if (!Directory.Exists(path))
                        continue;
                    string marker = SafePathUtility.ResolveWithinRoot(root, Path.Combine(path, folder.Marker));
                    if (!File.Exists(marker))
                        continue;
                    using (var proof = File.Open(marker, FileMode.Open, FileAccess.Read, FileShare.Read))
                        if (proof.Length != 0)
                            continue;
                    bool empty = true;
                    foreach (string entry in Directory.EnumerateFileSystemEntries(path))
                        if (Path.GetFileName(entry) != folder.Marker)
                        {
                            empty = false;
                            break;
                        }
                    File.Delete(SafePathUtility.ResolveWithinRoot(root, marker));
                    // Nonrecursive deletion preserves new contents. Portable path APIs do
                    // not make the ownership observation and deletion atomic against writers.
                    if (!completed && empty)
                        Directory.Delete(SafePathUtility.ResolveWithinRoot(root, path), false);
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
                catch (InvalidOperationException) { }
            }
        }
    }
}
