using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>A bounded, instance-owned queue. Only its worker opens, flushes or rotates files.</summary>
    internal sealed class BufferedLogWriter : IDisposable
    {
        private readonly object gate = new object();
        private readonly Queue<(long sequence, string line, bool error)> queue = new();
        private readonly string root;
        private readonly int capacity;
        private readonly long maxBytes;
        private readonly Action<string, bool> sink;
        private readonly Thread worker;
        private long accepted,
            completed,
            flushRequested,
            dropped,
            failures;
        private bool stopping;
        private OwnedFile normal,
            errors;
        internal long DroppedCount => Interlocked.Read(ref dropped);
        internal long FailureCount => Interlocked.Read(ref failures);
        internal long PendingCount
        {
            get
            {
                lock (gate)
                    return accepted - completed;
            }
        }
        internal bool ShutdownCompleted { get; private set; }

        internal BufferedLogWriter(string root, int capacity, long maxBytes, Action<string, bool> sink = null)
        {
            if (capacity < 1 || maxBytes < 128)
                throw new ArgumentOutOfRangeException();
            this.root = Path.GetFullPath(root);
            this.capacity = capacity;
            this.maxBytes = maxBytes;
            this.sink = sink;
            worker = new Thread(Run) { IsBackground = true, Name = "Unity MCP log writer" };
            worker.Start();
        }

        internal bool Enqueue(string line, bool error)
        {
            lock (gate)
            {
                if (stopping || line == null || line.Length > 16384 || queue.Count >= capacity)
                {
                    Interlocked.Increment(ref dropped);
                    return false;
                }
                queue.Enqueue((++accepted, line, error));
                Monitor.PulseAll(gate);
                return true;
            }
        }

        internal bool Flush(int timeoutMs)
        {
            var watch = Stopwatch.StartNew();
            lock (gate)
            {
                long target = accepted;
                flushRequested = Math.Max(flushRequested, target);
                Monitor.PulseAll(gate);
                while (completed < target)
                {
                    int remaining = timeoutMs - (int)watch.ElapsedMilliseconds;
                    if (remaining <= 0)
                        return false;
                    Monitor.Wait(gate, remaining);
                }
                return FailureCount == 0;
            }
        }

        private void Run()
        {
            var batch = new List<(long sequence, string line, bool error)>(64);
            try
            {
                while (true)
                {
                    lock (gate)
                    {
                        while (queue.Count == 0 && !stopping)
                            Monitor.Wait(gate);
                        if (queue.Count == 0)
                            return;
                        // Coalesce low-rate traffic for at most 100ms; explicit flush/quit bypasses the delay.
                        long until = Stopwatch.GetTimestamp() + Stopwatch.Frequency / 10;
                        while (!stopping && queue.Count < 64 && flushRequested <= completed)
                        {
                            int remaining = (int)((until - Stopwatch.GetTimestamp()) * 1000 / Stopwatch.Frequency);
                            if (remaining <= 0)
                                break;
                            Monitor.Wait(gate, remaining);
                        }
                        while (queue.Count > 0 && batch.Count < 64)
                            batch.Add(queue.Dequeue());
                    }
                    try
                    {
                        if (sink == null)
                        {
                            SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(root), root);
                            Directory.CreateDirectory(root);
                            normal ??= new OwnedFile(root, "mcp.log", maxBytes);
                            errors ??= new OwnedFile(root, "mcpError.log", maxBytes);
                            normal.Validate();
                            errors.Validate();
                        }
                        foreach (var row in batch)
                        {
                            if (sink != null)
                                sink(row.line, row.error);
                            else
                            {
                                normal.Append(row.line);
                                if (row.error)
                                    errors.Append(row.line);
                            }
                        }
                        normal?.Flush();
                        errors?.Flush();
                    }
                    catch
                    {
                        // Disk errors are diagnostics, never tool failures or recursive Unity logs.
                        Interlocked.Add(ref failures, batch.Count);
                        normal?.Dispose();
                        errors?.Dispose();
                        normal = errors = null;
                    }
                    finally
                    {
                        lock (gate)
                        {
                            completed = batch[batch.Count - 1].sequence;
                            Monitor.PulseAll(gate);
                        }
                        batch.Clear();
                    }
                }
            }
            finally
            {
                normal?.Dispose();
                errors?.Dispose();
            }
        }

        public void Dispose()
        {
            lock (gate)
            {
                stopping = true;
                Monitor.PulseAll(gate);
            }
            if (Thread.CurrentThread != worker)
                ShutdownCompleted = worker.Join(2000);
        }

        private sealed class OwnedFile : IDisposable
        {
            private static readonly Encoding Utf8 = new UTF8Encoding(false);
            private readonly string root,
                name;
            private readonly long maximum;
            private StreamWriter writer;
            private long bytes;

            internal OwnedFile(string root, string name, long maximum)
            {
                this.root = root;
                this.name = name;
                this.maximum = maximum;
            }

            internal void Validate()
            {
                SafePathUtility.ResolveWithinRoot(root, name);
                SafePathUtility.ResolveWithinRoot(root, name + ".1");
            }

            internal void Append(string line)
            {
                long size = Utf8.GetByteCount(line) + Utf8.GetByteCount(Environment.NewLine);
                if (writer == null)
                    Open();
                if (bytes > 0 && bytes + size > maximum)
                {
                    Dispose();
                    Validate();
                    string path = Path.Combine(root, name),
                        previous = path + ".1";
                    if (File.Exists(previous))
                        File.Delete(previous);
                    File.Move(path, previous);
                    Open();
                }
                writer.WriteLine(line);
                bytes += size;
            }

            private void Open()
            {
                Validate();
                var stream = new FileStream(Path.Combine(root, name), FileMode.Append, FileAccess.Write, FileShare.Read);
                bytes = stream.Length;
                writer = new StreamWriter(stream, Utf8, 8192);
            }

            internal void Flush() => writer?.Flush();

            public void Dispose()
            {
                try
                {
                    writer?.Dispose();
                }
                catch { }
                finally
                {
                    writer = null;
                }
            }
        }
    }
}
