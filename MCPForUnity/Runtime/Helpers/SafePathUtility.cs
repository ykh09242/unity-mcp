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
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
                if (current.Equals(fullRoot, comparison)) break;
                current = Path.GetDirectoryName(current);
            }
            return fullPath;
        }
    }
}
