using System;
using System.IO;
using System.Text;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Explicit regular files only. Final output never replaces existing evidence.</summary>
    public static class PlayScenarioPlayerFiles
    {
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

        public static string CheckedAbsolute(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path))
                throw new ArgumentException("An absolute Player file path is required.");
            string full = Path.GetFullPath(path);
            for (string current = full; !string.IsNullOrEmpty(current); current = Path.GetDirectoryName(current))
                if ((File.Exists(current) || Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("Player paths cannot traverse symbolic links or reparse points.");
            return full;
        }

        public static string Read(string path, int limit)
        {
            path = CheckedAbsolute(path);
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (stream.Length <= 0 || stream.Length > limit)
                    throw new IOException("Player file exceeds its bounded size.");
                using (var reader = new StreamReader(stream, Utf8, false))
                    return reader.ReadToEnd();
            }
        }

        public static void WriteNew(string path, string json, int limit)
        {
            path = CheckedAbsolute(path);
            byte[] bytes = Utf8.GetBytes(json);
            if (bytes.Length == 0 || bytes.Length > limit)
                throw new IOException("Player output exceeds its bounded size.");
            string temporary = CheckedAbsolute(path + ".tmp");
            if (File.Exists(path) || File.Exists(temporary))
                throw new IOException("Player output already exists; use a fresh run directory.");
            try
            {
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
                File.Move(temporary, path);
            }
            catch
            {
                if (File.Exists(temporary))
                    File.Delete(temporary);
                throw;
            }
        }
    }
}
