using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnity.Editor.Tools.Build
{
    /// <summary>Bounded cleanup of explicit build outputs, never project source directories.</summary>
    internal static class BuildOutputCleaner
    {
        internal const int MaxEntries = 5000;
        private const int MaxDepth = 64;
        private const int MaxPreviewEntries = 100;
        private const int MaxScanMilliseconds = 2000;

        internal static object Clean(string projectRoot, string outputPath, bool dryRun, bool buildActive = false)
        {
            if (buildActive)
                return new ErrorResponse("Cannot clean build outputs while a build is pending or running.");
            if (string.IsNullOrWhiteSpace(outputPath))
                return new ErrorResponse("'output_path' is required for clean_output; choose an explicit descendant of Builds/.");

            try
            {
                string root = Path.GetFullPath(projectRoot);
                if (root.Length > Path.GetPathRoot(root).Length)
                    root = root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                string builds = Path.Combine(root, "Builds");
                string requested = outputPath.Replace('\\', Path.DirectorySeparatorChar).Replace('/', Path.DirectorySeparatorChar);
                // Reject ambiguous components before normalization, including Windows ADS and
                // trailing-dot/space aliases. Traversal is unnecessary for an explicit output.
                string components = Path.IsPathRooted(requested) ? requested.Substring(Path.GetPathRoot(requested).Length) : requested;
                foreach (string part in components.Split(Path.DirectorySeparatorChar))
                {
                    if (
                        part == "."
                        || part == ".."
                        || part.EndsWith(".", StringComparison.Ordinal)
                        || part.EndsWith(" ", StringComparison.Ordinal)
                        || part.IndexOfAny(new[] { ':', '*', '?', '\0' }) >= 0
                    )
                        throw new InvalidOperationException("'output_path' contains an unsafe or ambiguous path component.");
                }

                string target = SafePathUtility
                    .ResolveWithinRoot(builds, Path.GetFullPath(Path.Combine(root, requested)))
                    .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                var comparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
                if (target.Equals(builds, comparison))
                    throw new InvalidOperationException("Choose a descendant of Builds/; the Builds root itself cannot be cleaned.");
                // Also inspect ancestors of the permitted root, so a linked project or Builds
                // parent cannot make a lexically contained selection escape its real location.
                SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(root), target);

                var files = new List<string>();
                var directories = new List<string>();
                var preview = new List<string>();
                var pending = new Stack<(string path, int depth)>();
                pending.Push((target, 0));
                long bytes = 0;
                int entries = 0;
                var timer = Stopwatch.StartNew();
                bool exists = File.Exists(target) || Directory.Exists(target);
                if (exists)
                {
                    while (pending.Count > 0)
                    {
                        if (timer.ElapsedMilliseconds > MaxScanMilliseconds)
                            throw new InvalidOperationException("Build-output scan exceeded the 2-second budget; select a smaller output subtree.");
                        var current = pending.Pop();
                        if (current.depth > MaxDepth || ++entries > MaxEntries)
                            throw new InvalidOperationException(
                                $"Build-output cleanup is limited to {MaxEntries} entries and {MaxDepth} directory levels; select a smaller output subtree."
                            );
                        SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(root), current.path);
                        var attributes = File.GetAttributes(current.path);
                        if ((attributes & FileAttributes.ReparsePoint) != 0)
                            throw new InvalidOperationException("Build outputs containing symbolic links or junctions cannot be cleaned.");
                        if (preview.Count < MaxPreviewEntries)
                            preview.Add(Relative(root, current.path));
                        if ((attributes & FileAttributes.Directory) != 0)
                        {
                            directories.Add(current.path);
                            foreach (string child in Directory.EnumerateFileSystemEntries(current.path))
                            {
                                // Bound even a single very wide directory before materializing it.
                                if (pending.Count + entries >= MaxEntries || timer.ElapsedMilliseconds > MaxScanMilliseconds)
                                    throw new InvalidOperationException("Build-output scan limit reached; select a smaller output subtree.");
                                pending.Push((child, current.depth + 1));
                            }
                        }
                        else
                        {
                            if (!dryRun && (attributes & FileAttributes.ReadOnly) != 0)
                                throw new InvalidOperationException("Build output contains read-only files; no files were deleted.");
                            files.Add(current.path);
                            bytes += new FileInfo(current.path).Length;
                        }
                    }
                }

                int deleted = 0;
                if (!dryRun)
                {
                    // Revalidate immediately before each operation. Never use recursive delete:
                    // newly added entries fail an empty-directory delete instead of being swept up.
                    try
                    {
                        foreach (string file in files)
                        {
                            SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(root), file);
                            File.Delete(file);
                            deleted++;
                        }
                        for (int i = directories.Count - 1; i >= 0; i--)
                        {
                            SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(root), directories[i]);
                            Directory.Delete(directories[i], false);
                            deleted++;
                        }
                    }
                    catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is InvalidOperationException)
                    {
                        return new ErrorResponse(
                            $"Build-output cleanup stopped: {ex.Message}",
                            new
                            {
                                output_path = Relative(root, target),
                                dry_run = false,
                                deleted_entries = deleted,
                                partial = deleted > 0,
                            }
                        );
                    }
                }

                return new SuccessResponse(
                    dryRun ? "Build-output cleanup preview; no files deleted." : "Build-output cleanup completed.",
                    new
                    {
                        output_path = Relative(root, target),
                        dry_run = dryRun,
                        exists,
                        file_count = files.Count,
                        directory_count = directories.Count,
                        total_bytes = bytes,
                        entries = preview,
                        entries_truncated = entries > preview.Count,
                        deleted_entries = deleted,
                    }
                );
            }
            catch (Exception ex)
                when (ex is IOException
                    || ex is UnauthorizedAccessException
                    || ex is InvalidOperationException
                    || ex is ArgumentException
                    || ex is NotSupportedException
                )
            {
                return new ErrorResponse($"Build-output cleanup refused: {ex.Message}");
            }
        }

        private static string Relative(string root, string path) =>
            path.Substring(root.EndsWith(Path.DirectorySeparatorChar.ToString(), StringComparison.Ordinal) ? root.Length : root.Length + 1).Replace('\\', '/');
    }
}
