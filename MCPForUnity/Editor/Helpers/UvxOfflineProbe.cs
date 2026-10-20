using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Helpers
{
    internal sealed class UvxOfflineProbe
    {
        private sealed class Entry
        {
            internal Task<bool> Work;
            internal DateTime Expires;
        }

        private readonly object gate = new();
        private readonly Dictionary<string, Entry> entries = new();
        private readonly Func<string, string, bool> run;
        private readonly Func<DateTime> clock;

        internal UvxOfflineProbe(Func<string, string, bool> run, Func<DateTime> clock = null)
        {
            this.run = run;
            this.clock = clock ?? (() => DateTime.UtcNow);
        }

        private static string Identity(string executable, string arguments)
        {
            var info = new FileInfo(executable);
            return info.FullName + "|" + info.LastWriteTimeUtc.Ticks + "|" + (info.Exists ? info.Length : -1) + "|" + arguments;
        }

        private Task<bool> GetOrStart(string executable, string arguments)
        {
            if (string.IsNullOrWhiteSpace(executable))
                return Task.FromResult(false);
            string key;
            try
            {
                key = Identity(executable, arguments);
            }
            catch
            {
                return Task.FromResult(false);
            }
            lock (gate)
            {
                if (entries.TryGetValue(key, out var entry) && (!entry.Work.IsCompleted || clock() < entry.Expires))
                    return entry.Work;
                foreach (
                    string expired in entries.Where(pair => pair.Value.Work.IsCompleted && clock() >= pair.Value.Expires).Select(pair => pair.Key).ToArray()
                )
                    entries.Remove(expired);
                if (entries.Count >= 16)
                {
                    string victim = entries
                        .Where(pair => pair.Value.Work.IsCompleted)
                        .OrderBy(pair => pair.Value.Expires)
                        .Select(pair => pair.Key)
                        .FirstOrDefault();
                    if (victim == null)
                        return Task.FromResult(false);
                    entries.Remove(victim);
                }
                var work = Task.Run(() =>
                {
                    try
                    {
                        return run(executable, "--offline " + arguments + " mcp-for-unity --help");
                    }
                    catch
                    {
                        return false;
                    }
                });
                entries[key] = new Entry { Work = work, Expires = clock().AddSeconds(30) };
                return work;
            }
        }

        internal bool GetCachedOrSchedule(string executable, string arguments)
        {
            var task = GetOrStart(executable, arguments);
            return task.Status == TaskStatus.RanToCompletion && task.Result;
        }

        internal async Task<bool> ProbeAsync(string executable, string arguments, CancellationToken ct)
        {
            ct.ThrowIfCancellationRequested();
            var work = GetOrStart(executable, arguments);
            var canceled = new TaskCompletionSource<bool>();
            using (ct.Register(() => canceled.TrySetResult(true)))
            {
                await Task.WhenAny(work, canceled.Task);
                ct.ThrowIfCancellationRequested();
                return await work;
            }
        }
    }
}
