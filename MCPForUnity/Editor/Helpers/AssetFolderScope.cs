using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Registers output folders and rolls back only this operation's empty folders on failure.</summary>
    internal sealed class AssetFolderScope : IDisposable
    {
        private readonly List<(string path, string guid)> createdFolders = new();
        private bool completed;
        private bool disposed;

        public void EnsureParentDirectory(string assetPath)
        {
            string path = AssetPathUtility.GetContainedAssetPath(assetPath);
            string parent = Path.GetDirectoryName(path)?.Replace('\\', '/');
            if (!string.IsNullOrEmpty(parent))
                EnsureFolder(parent);
        }

        public void EnsureFolder(string folderPath)
        {
            if (disposed)
                throw new ObjectDisposedException(nameof(AssetFolderScope));
            string path = AssetPathUtility.GetContainedAssetPath(folderPath);
            if (path == "Assets")
                return;

            string fullPath = AssetPathUtility.GetFullAssetPath(path);
            if (File.Exists(fullPath))
                throw new IOException($"The output folder '{path}' is occupied by a file.");
            if (AssetDatabase.IsValidFolder(path) && Directory.Exists(fullPath))
                return;

            string parent = Path.GetDirectoryName(path)?.Replace('\\', '/');
            EnsureFolder(parent);
            fullPath = AssetPathUtility.GetFullAssetPath(path);
            if (Directory.Exists(fullPath))
            {
                // A folder made outside Unity is pre-existing, even when Unity has not indexed it.
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                if (!AssetDatabase.IsValidFolder(path))
                    throw new IOException($"Unity could not register the output folder '{path}'.");
                return;
            }

            string guid = AssetDatabase.CreateFolder(parent, Path.GetFileName(path));
            if (string.IsNullOrEmpty(guid))
                throw new IOException($"Unity could not create the output folder '{path}'.");

            string createdPath = AssetPathUtility.GetContainedAssetPath(AssetDatabase.GUIDToAssetPath(guid));
            createdFolders.Add((createdPath, guid));
            var comparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            if (!string.Equals(createdPath, path, comparison))
                throw new IOException($"Unity created a different output folder instead of '{path}'.");
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
                    string fullPath = AssetPathUtility.GetFullAssetPath(folder.path);
                    if (!Directory.Exists(fullPath) || AssetDatabase.AssetPathToGUID(folder.path, AssetPathToGUIDOptions.OnlyExistingAssets) != folder.guid)
                        continue;
                    using (var entries = Directory.EnumerateFileSystemEntries(fullPath).GetEnumerator())
                        if (entries.MoveNext())
                            continue;

                    // Never pass a nonempty or replaced folder to Unity's deletion API.
                    if (!AssetDatabase.DeleteAsset(folder.path))
                        McpLog.Warn($"Could not remove empty output folder '{folder.path}'.");
                }
                catch (Exception ex)
                {
                    // Cleanup must not hide the operation's original failure.
                    McpLog.Warn($"Could not remove empty output folder '{folder.path}' ({ex.GetType().Name}).");
                }
            }
        }
    }
}
