using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Physical path checks and staged replacements for package-owned persistence.</summary>
    internal static class GuardedFileSystem
    {
        internal static string CheckPath(string path)
        {
            string full = Path.GetFullPath(path);
            return SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(full), full);
        }

        internal static void CheckTree(string root)
        {
            var pending = new Stack<string>();
            pending.Push(CheckPath(root));
            while (pending.Count > 0)
            {
                string directory = CheckPath(pending.Pop());
                foreach (string entry in Directory.EnumerateFileSystemEntries(directory))
                {
                    CheckPath(entry);
                    if ((File.GetAttributes(entry) & FileAttributes.Directory) != 0)
                        pending.Push(entry);
                }
            }
        }

        internal static void CopyDirectory(string source, string destination)
        {
            CheckTree(source);
            CheckPath(destination);
            Directory.CreateDirectory(destination);
            foreach (string entry in Directory.EnumerateFileSystemEntries(source))
            {
                string from = CheckPath(entry);
                string to = CheckPath(Path.Combine(destination, Path.GetFileName(entry)));
                if ((File.GetAttributes(from) & FileAttributes.Directory) != 0)
                    CopyDirectory(from, to);
                else
                    File.Copy(from, to, false);
            }
        }

        internal static void ReplaceDirectory(string target, Action<string> prepare)
        {
            target = CheckPath(target);
            if (Directory.Exists(target))
                CheckTree(target);
            string parent = Path.GetDirectoryName(target);
            string nonce = Guid.NewGuid().ToString("N");
            string stage = CheckPath(Path.Combine(parent, ".mcp-stage-" + nonce));
            string previous = CheckPath(Path.Combine(parent, ".mcp-previous-" + nonce));
            bool moved = false;
            bool committed = false;
            Directory.CreateDirectory(stage);
            try
            {
                prepare(stage);
                CheckTree(stage);
                CheckPath(target);
                if (Directory.Exists(target))
                {
                    CheckTree(target);
                    Directory.Move(target, previous);
                    moved = true;
                }
                try
                {
                    Directory.Move(stage, target);
                    committed = true;
                }
                catch
                {
                    if (moved && !Directory.Exists(CheckPath(target)))
                        Directory.Move(CheckPath(previous), target);
                    throw;
                }
            }
            finally
            {
                // A failed rollback keeps the original directory for explicit recovery.
                CleanupOwnedDirectory(stage);
                if (committed && moved)
                    CleanupOwnedDirectory(previous);
            }
        }

        private static void CleanupOwnedDirectory(string path)
        {
            try
            {
                if (!Directory.Exists(CheckPath(path)))
                    return;
                CheckTree(path);
                Directory.Delete(path, true);
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Replacement files retained at {path}: {ex.Message}");
            }
        }

        internal static void WriteAllTextAtomic(string path, string content)
        {
            path = CheckPath(path);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            CheckPath(path);
            string stage = CheckPath(path + "." + Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                using (var stream = new FileStream(stage, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                using (var writer = new StreamWriter(stream, new UTF8Encoding(false)))
                    writer.Write(content);
                CheckPath(stage);
                CheckPath(path);
                if (File.Exists(path))
                    File.Replace(stage, path, null);
                else
                    File.Move(stage, path);
            }
            finally
            {
                if (File.Exists(CheckPath(stage)))
                    File.Delete(stage);
            }
        }
    }
}
