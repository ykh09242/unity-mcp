using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Helpers
{
    public sealed class BufferedLogWriterTests
    {
        private string root;
        private object writer;
        private const BindingFlags Methods = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

        [SetUp]
        public void SetUp() => root = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpLogTest-" + Guid.NewGuid().ToString("N")));

        [TearDown]
        public void TearDown()
        {
            (writer as IDisposable)?.Dispose();
            writer = null;
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        private void Create(int capacity = 8, long maxBytes = 256, Action<string, bool> sink = null)
        {
            Type type = typeof(McpLogRecord).Assembly.GetType("MCPForUnity.Editor.Helpers.BufferedLogWriter");
            Assert.IsNotNull(type, "Logging needs an instance-owned bounded writer.");
            writer = Activator.CreateInstance(type, Methods, null, new object[] { root, capacity, maxBytes, sink }, null);
        }

        private T Call<T>(string name, params object[] args) => (T)writer.GetType().GetMethod(name, Methods).Invoke(writer, args);

        private long Count(string name) => (long)writer.GetType().GetProperty(name, Methods).GetValue(writer);

        [Test]
        public void ProducerNeverWaitsForDiskAndOverflowIsCounted()
        {
            using var entered = new ManualResetEventSlim();
            using var release = new ManualResetEventSlim();
            var rows = new List<string>();
            Create(
                2,
                256,
                (line, error) =>
                {
                    entered.Set();
                    release.Wait(TimeSpan.FromSeconds(5));
                    lock (rows)
                        rows.Add(line);
                }
            );
            try
            {
                Assert.IsTrue(Call<bool>("Enqueue", "first", false));
                Assert.IsTrue(entered.Wait(TimeSpan.FromSeconds(5)));
                var watch = System.Diagnostics.Stopwatch.StartNew();
                Assert.IsTrue(Call<bool>("Enqueue", "second", false));
                Assert.IsTrue(Call<bool>("Enqueue", "third", true));
                Assert.IsFalse(Call<bool>("Enqueue", "overflow", false));
                watch.Stop();
                Assert.Less(watch.ElapsedMilliseconds, 250);
                Assert.AreEqual(1, Count("DroppedCount"));
            }
            finally
            {
                release.Set();
            }
            Assert.IsTrue(Call<bool>("Flush", 5000));
            Assert.AreEqual(3, rows.Count);
        }

        [Test]
        public void ShutdownFlushesAndRotationPreservesUnrelatedFiles()
        {
            Directory.CreateDirectory(root);
            string sentinel = Path.Combine(root, "unrelated.txt");
            File.WriteAllText(sentinel, "keep");
            Create(128, 256);
            for (int i = 0; i < 20; i++)
                Assert.IsTrue(Call<bool>("Enqueue", "{\"row\":" + i + ",\"data\":\"" + new string('x', 80) + "\"}", true));
            ((IDisposable)writer).Dispose();
            Assert.AreEqual("keep", File.ReadAllText(sentinel));
            foreach (string name in new[] { "mcp.log", "mcpError.log" })
            {
                Assert.IsTrue(File.Exists(Path.Combine(root, name + ".1")));
                StringAssert.Contains("\"row\":19", File.ReadAllText(Path.Combine(root, name)));
                Assert.LessOrEqual(new FileInfo(Path.Combine(root, name)).Length, 256);
            }
        }

        [Test]
        public void FailedWritesAreObservableAndDoNotStopTheWorker()
        {
            int calls = 0;
            Create(
                sink: (line, error) =>
                {
                    if (Interlocked.Increment(ref calls) == 1)
                        throw new IOException("test");
                }
            );
            Assert.IsTrue(Call<bool>("Enqueue", "first", false));
            Assert.IsFalse(Call<bool>("Flush", 5000));
            Assert.IsTrue(Call<bool>("Enqueue", "second", false));
            Assert.IsFalse(Call<bool>("Flush", 5000));
            Assert.AreEqual(2, calls);
            Assert.AreEqual(1, Count("FailureCount"));
        }
    }
}
