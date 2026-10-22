using System;
using System.Collections.Generic;
using System.Threading;

namespace MCPForUnity.Editor.Tools
{
    internal static class ReferenceAssetInventory
    {
        internal const int MaximumAssets = 250000;

        // FindAssets itself is indivisible and already includes child folders.
        // Apply the advertised budget to distinct eligible files, not raw GUIDs.
        internal static SortedSet<string> Collect(
            IEnumerable<string> guids,
            Func<string, string> pathForGuid,
            Func<string, bool> isFolder,
            int maximum,
            CancellationToken token,
            out int excluded
        )
        {
            if (maximum < 1 || maximum > MaximumAssets)
                throw new ArgumentOutOfRangeException(nameof(maximum));
            excluded = 0;
            var paths = new SortedSet<string>(StringComparer.Ordinal);
            foreach (string guid in guids)
            {
                token.ThrowIfCancellationRequested();
                string path = pathForGuid(guid);
                if (string.IsNullOrEmpty(path) || !path.StartsWith("Assets/", StringComparison.Ordinal) || path.Length > 2048)
                {
                    excluded++;
                    continue;
                }
                if (!isFolder(path))
                    paths.Add(path);
                if (paths.Count > maximum)
                    throw new ArgumentException(
                        $"Scope exceeds maxAssets={maximum}; narrow scanRoot or increase maxAssets (maximum {MaximumAssets}). Captured pages reuse a complete admitted scan; they do not truncate its inventory."
                    );
            }
            token.ThrowIfCancellationRequested();
            return paths;
        }
    }
}
