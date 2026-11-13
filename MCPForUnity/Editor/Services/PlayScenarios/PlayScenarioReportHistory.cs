using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Retire a report ID durably before its full report can be removed.</summary>
    internal sealed class PlayScenarioReportHistory
    {
        internal const int RetiredLimit = 256;
        internal const int ReportLimit = 20;
        private const int IdBytes = 32;
        private readonly string root;
        private readonly string directory;

        internal PlayScenarioReportHistory(string root, string directory)
        {
            this.root = root;
            this.directory = directory;
        }

        private string Checked(string path) => SafePathUtility.ResolveWithinRoot(root, path);

        private List<string> ReadRetired()
        {
            FileStream stream;
            try
            {
                stream = new FileStream(Checked(directory + "/retired.ids"), FileMode.Open, FileAccess.Read, FileShare.Read);
            }
            catch (IOException exception) when (exception is FileNotFoundException || exception is DirectoryNotFoundException)
            {
                return new List<string>();
            }
            using (stream)
            using (var reader = new BinaryReader(stream))
            {
                long length = stream.Length;
                if (length > RetiredLimit * IdBytes || length % IdBytes != 0 || length == 0)
                    throw new InvalidDataException("Invalid retired report ID history length.");
                var ids = new List<string>((int)length / IdBytes);
                for (int index = 0; index < length / IdBytes; index++)
                {
                    string id = Encoding.ASCII.GetString(reader.ReadBytes(IdBytes));
                    PlayScenarioSuiteStore.ValidateId(id);
                    ids.Add(id);
                }
                if (ids.Distinct(StringComparer.Ordinal).Count() != ids.Count)
                    throw new InvalidDataException("Duplicate retired report ID.");
                return ids;
            }
        }

        internal T FindForStart<T>(string id, Func<string, T> getReport, string field)
            where T : class
        {
            try
            {
                return getReport(id);
            }
            catch (IOException exception) when (exception is FileNotFoundException || exception is DirectoryNotFoundException)
            {
                if (ReadRetired().Contains(id))
                    throw new InvalidOperationException(field + " was already used and its full report has expired.");
                return null;
            }
        }

        private void Retire(string id)
        {
            PlayScenarioSuiteStore.ValidateId(id);
            var ids = ReadRetired();
            if (ids.Contains(id))
                return;
            if (ids.Count == RetiredLimit)
                ids.RemoveAt(0);
            ids.Add(id);
            byte[] bytes = Encoding.ASCII.GetBytes(string.Concat(ids));
            string path = Checked(directory + "/retired.ids");
            string stage = Checked(directory + "/" + Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                AtomicFilePublication.StageNew(stage, stream => stream.Write(bytes, 0, bytes.Length));
                Checked(stage);
                Checked(path);
                if (!File.Exists(path))
                    AtomicFilePublication.Create(stage, path);
                else if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                    File.Replace(stage, path, null);
                else
                    AtomicFilePublication.Exchange(stage, path);
            }
            finally
            {
                if (File.Exists(stage))
                    File.Delete(Checked(stage));
            }
        }

        // Called under the store gate, including across store instances. A crash after retirement
        // can leave both records; the next save finishes the eviction and the full report wins on replay.
        internal void Trim(string[] files, string currentId, Action<string> delete)
        {
            if (files.Length <= ReportLimit)
                return;
            string oldest = files
                .Where(file => Path.GetFileNameWithoutExtension(file) != currentId)
                .OrderBy(File.GetLastWriteTimeUtc)
                .ThenByDescending(file => file, StringComparer.Ordinal)
                .First();
            Retire(Path.GetFileNameWithoutExtension(oldest));
            delete(oldest);
        }
    }
}
