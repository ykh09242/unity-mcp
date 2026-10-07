using System;
using System.Collections.Generic;
using System.IO;

namespace MCPForUnity.Runtime.Helpers
{
    /// <summary>Rolls back only newly created, empty output directories after a failed write.</summary>
    public sealed class OutputFolderScope : IDisposable
    {
        private readonly string root;
        private readonly List<string> createdFolders = new();
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
            Directory.CreateDirectory(path);
            createdFolders.Add(path);
            SafePathUtility.ResolveWithinRoot(root, path);
        }

        public void Complete() => completed = true;

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            if (completed)
                return;

            for (int i = createdFolders.Count - 1; i >= 0; i--)
            {
                var folder = createdFolders[i];
                try
                {
                    string path = SafePathUtility.ResolveWithinRoot(root, folder);
                    if (!Directory.Exists(path))
                        continue;
                    using (var entries = Directory.EnumerateFileSystemEntries(path).GetEnumerator())
                        if (entries.MoveNext())
                            continue;
                    Directory.Delete(path, false);
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
                catch (InvalidOperationException) { }
            }
        }
    }
}
