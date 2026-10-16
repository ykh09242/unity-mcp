using System;
using System.Collections;
using System.Diagnostics;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Setup;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Setup
{
    [Parallelizable(ParallelScope.None)]
    public class SkillSyncDispatchTests
    {
        private const BindingFlags Internal = BindingFlags.Static | BindingFlags.NonPublic;
        private static readonly string A = new('a', 40);
        private static readonly string B = new('b', 40);
        private FieldInfo _reader;
        private object _previous;
        private int _mainThread;

        [SetUp]
        public void SetUp()
        {
            _mainThread = Thread.CurrentThread.ManagedThreadId;
            _reader = typeof(SkillSyncService).GetField("InstalledSelectionReader", Internal);
            _previous = _reader?.GetValue(null);
        }

        [TearDown]
        public void TearDown() => _reader?.SetValue(null, _previous);

        private static SkillSyncSource Package(string revision) =>
            SkillSyncSource.FromPackage(
                new JObject
                {
                    ["name"] = "com.ykh09242.unity-mcp",
                    ["version"] = "1.2.1",
                    ["mcpSkillRevision"] = revision,
                },
                "isolated-dispatch-fixture"
            );

        private void SetReader(Func<SkillSyncSource> reader)
        {
            Assert.IsNotNull(_reader, "The production dispatcher needs an isolated reader boundary for native characterization.");
            _reader.SetValue(null, reader);
        }

        private static Task<SkillSyncSource> ReadOnWorker() =>
            Task.Run(() =>
            {
                var method = typeof(SkillSyncService).GetMethod("ReadInstalledOnMainThread", Internal);
                Assert.IsNotNull(method);
                try
                {
                    return (SkillSyncSource)method.Invoke(null, null);
                }
                catch (TargetInvocationException error)
                {
                    throw error.InnerException ?? error;
                }
            });

        private static IEnumerator Wait(Task task)
        {
            var elapsed = Stopwatch.StartNew();
            while (!task.IsCompleted)
            {
                Assert.Less(elapsed.Elapsed.TotalSeconds, 15, "The production dispatcher did not complete.");
                yield return null;
            }
        }

        [UnityTest]
        public IEnumerator ProductionDispatcherReadsOnEditorThreadAndObservesSameVersionPinChanges()
        {
            string revision = A;
            int calls = 0;
            SetReader(() =>
            {
                Assert.AreEqual(_mainThread, Thread.CurrentThread.ManagedThreadId);
                calls++;
                return Package(revision);
            });
            var first = ReadOnWorker();
            yield return Wait(first);
            Assert.AreEqual(A, first.GetAwaiter().GetResult().Revision);
            revision = B;
            var second = ReadOnWorker();
            yield return Wait(second);
            Assert.AreEqual(B, second.GetAwaiter().GetResult().Revision);
            Assert.AreEqual(2, calls, "Every boundary must re-read rather than cache package selection.");
        }

        [UnityTest]
        public IEnumerator ProductionDispatcherPropagatesReaderFailureWithoutNetworkOrInstall()
        {
            SetReader(() =>
            {
                Assert.AreEqual(_mainThread, Thread.CurrentThread.ManagedThreadId);
                throw new InvalidOperationException("fixture installed pin unavailable");
            });
            var read = ReadOnWorker();
            yield return Wait(read);
            StringAssert.Contains("fixture installed pin unavailable", Assert.Throws<InvalidOperationException>(() => read.GetAwaiter().GetResult()).Message);
        }

        [Test]
        public void CaptureInstalledUsesActualResolvedPackageMetadataReadOnly()
        {
            var metadata = AssetPathUtility.GetPackageJson();
            Assert.IsNotNull(metadata, "The isolated package must be discoverable through the production package locator.");
            var pin = metadata["mcpSkillRevision"];
            if (pin?.Type != JTokenType.String || !SkillSyncSource.IsCommit(pin.Value<string>()))
            {
                StringAssert.Contains("mcpSkillRevision", Assert.Throws<InvalidOperationException>(() => SkillSyncSource.CaptureInstalled()).Message);
                return;
            }
            var selected = SkillSyncSource.CaptureInstalled();
            Assert.AreEqual(metadata.Value<string>("name"), selected.PackageName);
            Assert.AreEqual(metadata.Value<string>("version"), selected.PackageVersion);
            Assert.AreEqual(pin.Value<string>().ToLowerInvariant(), selected.Revision);
            Assert.IsNotEmpty(selected.PackageIdentity);
        }
    }
}
