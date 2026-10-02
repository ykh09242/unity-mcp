using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.Profiling;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Profiler;
using UProfiler = UnityEngine.Profiling.Profiler;

namespace MCPForUnityTests.Editor.Tools
{
    public class ProfilerContractTests
    {
        [TestCase(false)]
        [TestCase(true)]
        public void InvalidRecordingDirectory_PreservesProfilerState(bool initiallyEnabled)
        {
            bool previousEnabled = UProfiler.enabled;
            bool previousRecording = UProfiler.enableBinaryLog;
            bool previousCallstacks = UProfiler.enableAllocationCallstacks;
            string previousLogFile = UProfiler.logFile;
            try
            {
                UProfiler.enabled = initiallyEnabled;
                var result = JObject.FromObject(ManageProfiler.HandleCommand(new JObject
                {
                    ["action"] = "profiler_start", ["enable_callstacks"] = true,
                    ["log_file"] = Path.Combine(Path.GetTempPath(), "MissingProfilerContract_" + Guid.NewGuid().ToString("N"), "capture.raw")
                }).GetAwaiter().GetResult());
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual(initiallyEnabled, UProfiler.enabled);
                Assert.AreEqual(previousRecording, UProfiler.enableBinaryLog);
                Assert.AreEqual(previousCallstacks, UProfiler.enableAllocationCallstacks);
                Assert.AreEqual(previousLogFile, UProfiler.logFile);
            }
            finally { UProfiler.enabled = previousEnabled; }
        }

        [TestCase("unknown")]
        [TestCase("value")]
        [TestCase("null")]
        [TestCase("numeric")]
        public void InvalidAreas_DoNotChangeEarlierValidArea(string invalid)
        {
            bool previousCpu = UProfiler.GetAreaEnabled(ProfilerArea.CPU);
            try
            {
                var areas = new JObject { ["CPU"] = !previousCpu };
                if (invalid == "unknown") areas["NotAProfilerArea"] = true;
                else if (invalid == "value") areas["Memory"] = "bad";
                else if (invalid == "null") areas["Memory"] = JValue.CreateNull();
                else areas["12345"] = true;
                var result = JObject.FromObject(ManageProfiler.HandleCommand(new JObject
                {
                    ["action"] = "profiler_set_areas", ["areas"] = areas
                }).GetAwaiter().GetResult());
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual(previousCpu, UProfiler.GetAreaEnabled(ProfilerArea.CPU));
            }
            finally { UProfiler.SetAreaEnabled(ProfilerArea.CPU, previousCpu); }
        }

        [Test]
        public void ValidAreas_PreserveCaseInsensitiveAndFalseValues()
        {
            bool previousCpu = UProfiler.GetAreaEnabled(ProfilerArea.CPU);
            bool previousMemory = UProfiler.GetAreaEnabled(ProfilerArea.Memory);
            try
            {
                var result = JObject.FromObject(ManageProfiler.HandleCommand(new JObject
                {
                    ["action"] = "profiler_set_areas",
                    ["areas"] = new JObject { ["cpu"] = true, ["Memory"] = false }
                }).GetAwaiter().GetResult());
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.IsTrue(UProfiler.GetAreaEnabled(ProfilerArea.CPU));
                Assert.IsFalse(UProfiler.GetAreaEnabled(ProfilerArea.Memory));
            }
            finally
            {
                UProfiler.SetAreaEnabled(ProfilerArea.CPU, previousCpu);
                UProfiler.SetAreaEnabled(ProfilerArea.Memory, previousMemory);
            }
        }

        [Test]
        public void EmptyAreas_RemainSuccessfulNoOp()
        {
            var result = JObject.FromObject(ManageProfiler.HandleCommand(new JObject
            {
                ["action"] = "profiler_set_areas", ["areas"] = new JObject()
            }).GetAwaiter().GetResult());
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(0, ((JObject)result["data"]["areas"]).Count);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DuplicateExplicitCounters_AreUniqueWithCaseAndOrderPreserved(bool encoded)
        {
            var names = new JArray("first", "first", "First", "last", "first");
            var parameters = new JObject { ["counters"] = encoded ? (JToken)new JValue(names.ToString()) : names };
            var method = typeof(CounterOps).GetMethod("GetRequestedCounters", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            // Explicit counter selection is tested without starting native recorders.
            var result = (List<string>)method.Invoke(null, new object[] { new ToolParams(parameters), Unity.Profiling.ProfilerCategory.Render });
            CollectionAssert.AreEqual(new[] { "first", "First", "last" }, result);
        }
    }
}
