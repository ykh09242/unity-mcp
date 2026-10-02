using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools.Profiler;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
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
            if (!ownsRoot) return;
            foreach (string file in ownedFiles)
                if (File.Exists(file)) File.Delete(file);
            if (!Directory.Exists(ownedRoot)) return;
            Assert.AreEqual(0, Directory.GetFileSystemEntries(ownedRoot).Length,
                "Unexpected artifacts retained in the exact owned temporary root: " + ownedRoot);
            Directory.Delete(ownedRoot, false);
        }

        [TestCase("false", false)]
        [TestCase("\"false\"", false)]
        [TestCase("0", false)]
        [TestCase("\"off\"", false)]
        [TestCase("true", true)]
        [TestCase("null", true)]
        [TestCase("\"\"", true)]
        [TestCase("\"bad\"", true)]
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
                if (value != "omitted") request["enable_callstacks"] = JToken.Parse(value);
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

        [TestCase("OptionalEnum", 3u)]
        [TestCase("OptionalUInt", 7u)]
        [TestCase("RequiredEnum", 0u)]
        [TestCase("RequiredUInt", 0u)]
        public void SelectedSignatureUsesDeclaredDefaultOrExistingRequiredFallback(string methodName, uint expected)
        {
            var parameter = typeof(ProfilerSessionSnapshotIntegrityTests).GetMethod(methodName,
                BindingFlags.NonPublic | BindingFlags.Static).GetParameters()[0];
            object value = CaptureFlagsDefault(parameter);
            Assert.AreEqual(expected, Convert.ToUInt32(value));
            Assert.AreEqual(parameter.ParameterType, value.GetType());
        }

        [Test]
        public void InstalledSnapshotOverloadsRetainTheirDeclaredDefaultsWithoutInvocation()
        {
            Type memoryType = Type.GetType("Unity.Profiling.Memory.MemoryProfiler, UnityEngine.CoreModule")
                ?? Type.GetType("UnityEngine.Profiling.Memory.Experimental.MemoryProfiler, UnityEngine.CoreModule");
            if (memoryType == null) Assert.Ignore("No supported built-in snapshot API is available.");
            int checkedMethods = 0;
            foreach (var method in memoryType.GetMethods(BindingFlags.Public | BindingFlags.Static))
            {
                if (method.Name != "TakeSnapshot") continue;
                var parameters = method.GetParameters();
                if (parameters.Length != 3 && parameters.Length != 4) continue;
                var flags = parameters[parameters.Length - 1];
                if (!flags.ParameterType.IsEnum && flags.ParameterType != typeof(uint)) continue;
                if (!flags.HasDefaultValue) continue;
                Assert.AreEqual(flags.DefaultValue, CaptureFlagsDefault(flags));
                checkedMethods++;
            }
            Assert.Greater(checkedMethods, 0, "No declared-default snapshot overload was found.");
        }

        private enum TestFlags : uint { Managed = 1, Native = 2 }
        private static void OptionalEnum(TestFlags flags = TestFlags.Managed | TestFlags.Native) { }
        private static void OptionalUInt(uint flags = 7u) { }
        private static void RequiredEnum(TestFlags flags) { }
        private static void RequiredUInt(uint flags) { }
        private static TaskCompletionSource<object> NewCompletion() =>
            new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);

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
