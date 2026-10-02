using System;
using System.IO;

namespace MCPForUnity.Runtime.Helpers
{
    /// <summary>Checks filesystem boundaries before tools read or write caller-selected paths.</summary>
    public static class SafePathUtility
    {
        public static string ResolveWithinRoot(string root, string path)
        {
            string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string fullPath = Path.GetFullPath(Path.Combine(fullRoot, path));
            var comparison = Path.DirectorySeparatorChar == '\\'
                ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            if (!fullPath.Equals(fullRoot, comparison) &&
                !fullPath.StartsWith(fullRoot + Path.DirectorySeparatorChar, comparison))
                throw new InvalidOperationException("Path must stay inside the permitted directory.");

            // A lexically contained path can still escape through a symlink or junction.
            string current = fullPath;
            while (current != null)
            {
                try
                {
                    if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                        throw new InvalidOperationException("Paths through symbolic links or junctions are not permitted.");
                }
                catch (FileNotFoundException) { RejectUnresolvedEntry(current, comparison); }
                catch (DirectoryNotFoundException) { RejectUnresolvedEntry(current, comparison); }
                if (current.Equals(fullRoot, comparison)) break;
                current = Path.GetDirectoryName(current);
            }
            return fullPath;
        }

        private static void RejectUnresolvedEntry(string path, StringComparison comparison)
        {
            // Some Unity/Mono versions report dangling links as missing targets.
            // Only a genuinely absent directory entry may be created later.
            string parent = Path.GetDirectoryName(path);
            if (parent == null || !Directory.Exists(parent)) return;
            foreach (string entry in Directory.EnumerateFileSystemEntries(parent, Path.GetFileName(path)))
                if (string.Equals(Path.GetFileName(entry), Path.GetFileName(path), comparison))
                    throw new InvalidOperationException("An existing path could not be safely inspected.");
        }
    }
}
