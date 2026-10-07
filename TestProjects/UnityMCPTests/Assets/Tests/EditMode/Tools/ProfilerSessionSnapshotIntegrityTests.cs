using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools.Profiler;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using UProfiler = UnityEngine.Profiling.Profiler;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ProfilerSessionSnapshotIntegrityTests
    {
        private string ownedRoot;
        private bool ownsRoot;
        private readonly System.Collections.Generic.List<string> ownedFiles = new System.Collections.Generic.List<string>();

        [SetUp]
        public void SetUp()
        {
            ownsRoot = false;
            ownedFiles.Clear();
            ownedRoot = Path.Combine(Path.GetTempPath(), "McpProfilerSnapshotIntegrity_" + Guid.NewGuid().ToString("N"));
            Assert.IsFalse(Directory.Exists(ownedRoot));
            Assert.IsFalse(File.Exists(ownedRoot));
            Directory.CreateDirectory(ownedRoot);
            ownsRoot = true;
        }

        [TearDown]
        public void TearDown()
        {
            if (!ownsRoot)
                return;
            foreach (string file in ownedFiles)
                if (File.Exists(file))
                    File.Delete(file);
            if (!Directory.Exists(ownedRoot))
                return;
            Assert.AreEqual(
                0,
                Directory.GetFileSystemEntries(ownedRoot).Length,
                "Unexpected artifacts retained in the exact owned temporary root: " + ownedRoot
            );
            Directory.Delete(ownedRoot, false);
        }

        [TestCase("false", false)]
        [TestCase("\"false\"", false)]
        [TestCase("true", true)]
        [TestCase("null", true)]
        [TestCase("omitted", true)]
        public void StartHonorsExplicitCallstackValuesWithoutChangingOmittedDefaults(string value, bool expected)
        {
            // Never interrupt an active user profiler or recording when this authored fixture is later run.
            if (UProfiler.enabled || UProfiler.enableBinaryLog)
                Assert.Ignore("An existing profiler session/recording is active.");
            bool originalEnabled = UProfiler.enabled;
            bool originalCallstacks = UProfiler.enableAllocationCallstacks;
            bool originalRecording = UProfiler.enableBinaryLog;
            string originalLogFile = UProfiler.logFile;
            try
            {
                UProfiler.enableAllocationCallstacks = true;
                var request = new JObject { ["action"] = "profiler_start" };
                if (value != "omitted")
                    request["enable_callstacks"] = JToken.Parse(value);
                var response = JObject.FromObject(ManageProfiler.HandleCommand(request).GetAwaiter().GetResult());
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(expected, UProfiler.enableAllocationCallstacks);
                Assert.AreEqual(expected, response["data"].Value<bool>("allocation_callstacks"));
                Assert.AreEqual(originalRecording, UProfiler.enableBinaryLog);
                Assert.AreEqual(originalLogFile, UProfiler.logFile);
            }
            finally
            {
                UProfiler.enabled = originalEnabled;
                UProfiler.enableAllocationCallstacks = originalCallstacks;
            }
        }

        [TestCase("0")]
        [TestCase("\"off\"")]
        [TestCase("\"\"")]
        [TestCase("\"bad\"")]
        public void InvalidCallstackValuesAreRejectedWithoutChangingProfilerState(string value)
        {
            bool enabled = UProfiler.enabled;
            bool callstacks = UProfiler.enableAllocationCallstacks;
            bool recording = UProfiler.enableBinaryLog;
            string logFile = UProfiler.logFile;
            var response = ProfilerResponse(new JObject { ["action"] = "profiler_start", ["enable_callstacks"] = JToken.Parse(value) });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("enable_callstacks", response.Value<string>("error"));
            Assert.AreEqual(enabled, UProfiler.enabled);
            Assert.AreEqual(callstacks, UProfiler.enableAllocationCallstacks);
            Assert.AreEqual(recording, UProfiler.enableBinaryLog);
            Assert.AreEqual(logFile, UProfiler.logFile);
        }

        [UnityTest]
        public IEnumerator DelayedInvalidMetadataCompletesAsFailureWithoutCapture()
        {
            var completion = NewCompletion();
            yield return null;
            Complete(completion, "bad\0path", true);
            Assert.IsTrue(completion.Task.IsCompleted);
            var response = JObject.FromObject(completion.Task.Result);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("metadata", response.Value<string>("error"));
        }

        [Test]
        public void NullMetadataCompletesAsFailureWithoutCapture()
        {
            var completion = NewCompletion();
            Complete(completion, null, true);
            Assert.IsTrue(completion.Task.IsCompleted);
            Assert.IsFalse(JObject.FromObject(completion.Task.Result).Value<bool>("success"));
        }

        [Test]
        public void ExistingFileMetadataReturnsItsLengthWithoutCapture()
        {
            string path = Path.Combine(ownedRoot, "Fixture.snap");
            ownedFiles.Add(path);
            File.WriteAllBytes(path, new byte[] { 1, 2, 3, 4 });
            var completion = NewCompletion();
            Complete(completion, path, true);
            var response = JObject.FromObject(completion.Task.Result);
            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(path, response["data"].Value<string>("path"));
            Assert.AreEqual(4, response["data"].Value<long>("size_bytes"));
        }

        [Test]
        public void MissingFileSuccessRetainsZeroSizeCompatibility()
        {
            var completion = NewCompletion();
            Complete(completion, Path.Combine(ownedRoot, "Missing.snap"), true);
            var response = JObject.FromObject(completion.Task.Result);
            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(0, response["data"].Value<long>("size_bytes"));
        }

        [Test]
        public void DuplicateLateInvalidCallbackRetainsFirstResult()
        {
            var completion = NewCompletion();
            Complete(completion, Path.Combine(ownedRoot, "Missing.snap"), true);
            object first = completion.Task.Result;
            Complete(completion, "late\0invalid", true);
            Complete(completion, null, false);
            Assert.AreSame(first, completion.Task.Result);
        }

        [Test]
        public void FailedCaptureCompletesAsFailureWithoutMetadataRead()
        {
            var completion = NewCompletion();
            Complete(completion, "bad\0path", false);
            Assert.IsFalse(JObject.FromObject(completion.Task.Result).Value<bool>("success"));
        }

        [TestCase("profiler_start", "log_file")]
        [TestCase("memory_take_snapshot", "snapshot_path")]
        public void InvalidProfilerWritePathsFailBeforeNativeWork(string action, string key) => AssertInvalidProfilerPaths(action, key);

        [TestCase("memory_list_snapshots", "search_path")]
        [TestCase("memory_compare_snapshots", "snapshot_a")]
        [TestCase("memory_compare_snapshots", "snapshot_b")]
        public void InvalidProfilerReadPathsFailBeforeFileAccess(string action, string key) => AssertInvalidProfilerPaths(action, key);

        private void AssertInvalidProfilerPaths(string action, string key)
        {
            var invalidPaths = new System.Collections.Generic.List<JToken>
            {
                new JObject(),
                new JArray(),
                new JValue(true),
                new JValue(42),
                new JValue(" "),
                new JValue("bad\0path"),
                new JValue("../escape.snap"),
                new JValue("..\\escape.snap"),
                new JValue("file:stream.snap"),
                new JValue("C:relative.snap"),
                new JValue("bad?.snap"),
                new JValue(Path.Combine(ownedRoot, "external.snap")),
            };
            if (Application.platform == RuntimePlatform.WindowsEditor)
            {
                invalidPaths.Add(".. /escape.snap");
                invalidPaths.Add("trailing./capture.snap");
                invalidPaths.Add("NUL.snap");
                invalidPaths.Add("COM1.snap");
            }
            foreach (JToken invalid in invalidPaths)
            {
                bool enabled = UProfiler.enabled;
                bool recording = UProfiler.enableBinaryLog;
                bool callstacks = UProfiler.enableAllocationCallstacks;
                string logFile = UProfiler.logFile;
                var request = new JObject
                {
                    ["action"] = action,
                    ["snapshot_a"] = "missing-a.snap",
                    ["snapshot_b"] = "missing-b.snap",
                    [key] = invalid,
                };
                var task = ManageProfiler.HandleCommand(request);
                Assert.IsTrue(task.IsCompleted, "Invalid paths must fail synchronously before any native capture wait.");
                var response = JObject.FromObject(task.GetAwaiter().GetResult());
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                StringAssert.Contains(key, response.Value<string>("error"));
                Assert.AreEqual(enabled, UProfiler.enabled);
                Assert.AreEqual(recording, UProfiler.enableBinaryLog);
                Assert.AreEqual(callstacks, UProfiler.enableAllocationCallstacks);
                Assert.AreEqual(logFile, UProfiler.logFile);
            }
        }

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        public void SnapshotReadsAcceptContainedAbsoluteAndProjectRelativePaths(bool relative, bool cache)
        {
            string projectRoot = Path.GetDirectoryName(Application.dataPath);
            string root = Path.Combine(
                cache ? Application.temporaryCachePath : Path.Combine(projectRoot, "Temp"),
                "McpProfilerPaths_" + Guid.NewGuid().ToString("N")
            );
            string first = Path.Combine(root, "first.snap");
            string second = Path.Combine(root, "second.snap");
            Directory.CreateDirectory(root);
            try
            {
                File.WriteAllBytes(first, new byte[] { 1 });
                File.WriteAllBytes(second, new byte[] { 1, 2, 3 });
                string inputRoot = relative ? root.Substring(projectRoot.Length + 1) : root;
                var listed = ProfilerResponse(new JObject { ["action"] = "memory_list_snapshots", ["searchPath"] = inputRoot });
                Assert.IsTrue(listed.Value<bool>("success"), listed.ToString());
                Assert.AreEqual(2, ((JArray)listed["data"]["snapshots"]).Count);
                var compared = ProfilerResponse(
                    new JObject
                    {
                        ["action"] = "memory_compare_snapshots",
                        ["snapshotA"] = Path.Combine(inputRoot, "first.snap"),
                        ["snapshotB"] = Path.Combine(inputRoot, "second.snap"),
                    }
                );
                Assert.IsTrue(compared.Value<bool>("success"), compared.ToString());
                Assert.AreEqual(2, compared["data"]["delta"].Value<long>("size_delta_bytes"));
            }
            finally
            {
                File.Delete(first);
                File.Delete(second);
                Directory.Delete(root, false);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SnapshotReadsRejectLinkedDirectoriesAndFiles(bool directory)
        {
            string projectRoot = Path.GetDirectoryName(Application.dataPath);
            string root = Path.Combine(projectRoot, "Temp", "McpProfilerLinks_" + Guid.NewGuid().ToString("N"));
            string sentinel = Path.Combine(ownedRoot, "sentinel.snap");
            string link = Path.Combine(root, directory ? "Linked" : "linked.snap");
            ownedFiles.Add(sentinel);
            File.WriteAllText(sentinel, "owned sentinel");
            Directory.CreateDirectory(root);
            try
            {
                CreateOwnedLink(link, directory ? ownedRoot : sentinel, directory);
                var listed = ProfilerResponse(new JObject { ["action"] = "memory_list_snapshots", ["search_path"] = directory ? link : root });
                Assert.IsFalse(listed.Value<bool>("success"), listed.ToString());
                var compared = ProfilerResponse(
                    new JObject
                    {
                        ["action"] = "memory_compare_snapshots",
                        ["snapshot_a"] = directory ? Path.Combine(link, "sentinel.snap") : link,
                        ["snapshot_b"] = "missing.snap",
                    }
                );
                Assert.IsFalse(compared.Value<bool>("success"), compared.ToString());
                StringAssert.Contains("snapshot_a", compared.Value<string>("error"));
                Assert.AreEqual("owned sentinel", File.ReadAllText(sentinel));
            }
            finally
            {
                if (directory)
                {
                    try
                    {
                        Directory.Delete(link, false);
                    }
                    catch (DirectoryNotFoundException) { }
                }
                else
                    File.Delete(link);
                Directory.Delete(root, false);
            }
        }

        [Test]
        public void SnapshotListingRejectsAFileAsSearchDirectory()
        {
            string root = Path.Combine(Path.GetDirectoryName(Application.dataPath), "Temp", "McpProfilerDirectory_" + Guid.NewGuid().ToString("N"));
            string snapshot = Path.Combine(root, "owned.snap");
            Directory.CreateDirectory(root);
            try
            {
                File.WriteAllText(snapshot, "owned snapshot sentinel");
                var response = ProfilerResponse(new JObject { ["action"] = "memory_list_snapshots", ["search_path"] = snapshot });
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                StringAssert.Contains("search_path", response.Value<string>("error"));
                Assert.AreEqual("owned snapshot sentinel", File.ReadAllText(snapshot));
            }
            finally
            {
                File.Delete(snapshot);
                Directory.Delete(root, false);
            }
        }

        private static JObject ProfilerResponse(JObject request) => JObject.FromObject(ManageProfiler.HandleCommand(request).GetAwaiter().GetResult());

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.I1)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        private static void CreateOwnedLink(string link, string target, bool directory)
        {
            bool created =
                Application.platform == RuntimePlatform.WindowsEditor ? CreateSymbolicLinkW(link, target, (directory ? 1 : 0) | 2) : symlink(target, link) == 0;
            if (!created)
                Assert.Ignore("Owned profiler path link creation unavailable: " + Marshal.GetLastWin32Error());
        }

        [TestCase("OptionalEnum", 3u)]
        [TestCase("OptionalUInt", 7u)]
        [TestCase("RequiredEnum", 0u)]
        [TestCase("RequiredUInt", 0u)]
        public void SelectedSignatureUsesDeclaredDefaultOrExistingRequiredFallback(string methodName, uint expected)
        {
            var parameter = typeof(ProfilerSessionSnapshotIntegrityTests).GetMethod(methodName, BindingFlags.NonPublic | BindingFlags.Static).GetParameters()[
                0
            ];
            object value = CaptureFlagsDefault(parameter);
            Assert.AreEqual(expected, Convert.ToUInt32(value));
            Assert.AreEqual(parameter.ParameterType, value.GetType());
        }

        [Test]
        public void InstalledSnapshotOverloadsRetainTheirDeclaredDefaultsWithoutInvocation()
        {
            Type memoryType =
                Type.GetType("Unity.Profiling.Memory.MemoryProfiler, UnityEngine.CoreModule")
                ?? Type.GetType("UnityEngine.Profiling.Memory.Experimental.MemoryProfiler, UnityEngine.CoreModule");
            if (memoryType == null)
                Assert.Ignore("No supported built-in snapshot API is available.");
            int checkedMethods = 0;
            foreach (var method in memoryType.GetMethods(BindingFlags.Public | BindingFlags.Static))
            {
                if (method.Name != "TakeSnapshot")
                    continue;
                var parameters = method.GetParameters();
                if (parameters.Length != 3 && parameters.Length != 4)
                    continue;
                var flags = parameters[parameters.Length - 1];
                if (!flags.ParameterType.IsEnum && flags.ParameterType != typeof(uint))
                    continue;
                if (!flags.HasDefaultValue)
                    continue;
                Assert.AreEqual(flags.DefaultValue, CaptureFlagsDefault(flags));
                checkedMethods++;
            }
            Assert.Greater(checkedMethods, 0, "No declared-default snapshot overload was found.");
        }

        private enum TestFlags : uint
        {
            Managed = 1,
            Native = 2,
        }

        private static void OptionalEnum(TestFlags flags = TestFlags.Managed | TestFlags.Native) { }

        private static void OptionalUInt(uint flags = 7u) { }

        private static void RequiredEnum(TestFlags flags) { }

        private static void RequiredUInt(uint flags) { }

        private static TaskCompletionSource<object> NewCompletion() => new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);

        private static void Complete(TaskCompletionSource<object> completion, string path, bool result)
        {
            var method = typeof(MemorySnapshotOps).GetMethod("CompleteSnapshot", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            method.Invoke(null, new object[] { completion, path, result });
            Assert.IsTrue(completion.Task.IsCompleted, "Snapshot callback did not complete its task.");
        }

        private static object CaptureFlagsDefault(ParameterInfo parameter)
        {
            var method = typeof(MemorySnapshotOps).GetMethod("GetCaptureFlagsDefault", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            return method.Invoke(null, new object[] { parameter });
        }
    }
}
