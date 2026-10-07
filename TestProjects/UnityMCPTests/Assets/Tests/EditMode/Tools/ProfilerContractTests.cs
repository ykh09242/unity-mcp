using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Profiler;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.Profiling;
using UnityEngine.TestTools;
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
                var result = JObject.FromObject(
                    ManageProfiler
                        .HandleCommand(
                            new JObject
                            {
                                ["action"] = "profiler_start",
                                ["enable_callstacks"] = true,
                                ["log_file"] = Path.Combine(Path.GetTempPath(), "MissingProfilerContract_" + Guid.NewGuid().ToString("N"), "capture.raw"),
                            }
                        )
                        .GetAwaiter()
                        .GetResult()
                );
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual(initiallyEnabled, UProfiler.enabled);
                Assert.AreEqual(previousRecording, UProfiler.enableBinaryLog);
                Assert.AreEqual(previousCallstacks, UProfiler.enableAllocationCallstacks);
                Assert.AreEqual(previousLogFile, UProfiler.logFile);
            }
            finally
            {
                UProfiler.enabled = previousEnabled;
            }
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
                if (invalid == "unknown")
                    areas["NotAProfilerArea"] = true;
                else if (invalid == "value")
                    areas["Memory"] = "bad";
                else if (invalid == "null")
                    areas["Memory"] = JValue.CreateNull();
                else
                    areas["12345"] = true;
                var result = JObject.FromObject(
                    ManageProfiler.HandleCommand(new JObject { ["action"] = "profiler_set_areas", ["areas"] = areas }).GetAwaiter().GetResult()
                );
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual(previousCpu, UProfiler.GetAreaEnabled(ProfilerArea.CPU));
            }
            finally
            {
                UProfiler.SetAreaEnabled(ProfilerArea.CPU, previousCpu);
            }
        }

        [Test]
        public void ValidAreas_PreserveCaseInsensitiveAndFalseValues()
        {
            bool previousCpu = UProfiler.GetAreaEnabled(ProfilerArea.CPU);
            bool previousMemory = UProfiler.GetAreaEnabled(ProfilerArea.Memory);
            try
            {
                var result = JObject.FromObject(
                    ManageProfiler
                        .HandleCommand(
                            new JObject
                            {
                                ["action"] = "profiler_set_areas",
                                ["areas"] = new JObject { ["cpu"] = true, ["Memory"] = false },
                            }
                        )
                        .GetAwaiter()
                        .GetResult()
                );
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
            var result = JObject.FromObject(
                ManageProfiler.HandleCommand(new JObject { ["action"] = "profiler_set_areas", ["areas"] = new JObject() }).GetAwaiter().GetResult()
            );
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

        [TestCase("42")]
        [TestCase("true")]
        [TestCase("{}")]
        [TestCase("[42]")]
        [TestCase("[true]")]
        [TestCase("[{}]")]
        [TestCase("[null]")]
        [TestCase("[\"Known\",42]")]
        [TestCase("[[\"Known\"],\"Other\"]")]
        [TestCase("\"[42]\"")]
        [TestCase("[\"[42]\"]")]
        [TestCase("[[42]]")]
        public void MalformedCounterSelector_IsRejectedInsteadOfBroadeningSelection(string selector)
        {
            // Given an explicit selector that cannot name a set of counters.
            var parameters = new ToolParams(new JObject { ["counters"] = JToken.Parse(selector) });
            var select = typeof(CounterOps).GetMethod("GetRequestedCounters", BindingFlags.NonPublic | BindingFlags.Static);

            // When selection runs, then malformed input must fail before discovery or recorder creation.
            var error = Assert.Throws<TargetInvocationException>(() =>
                select.Invoke(null, new object[] { parameters, Unity.Profiling.ProfilerCategory.Render })
            );
            Assert.That(error.InnerException, Is.InstanceOf<ArgumentException>());
        }

        [UnityTest]
        public IEnumerator MalformedCounterSelector_RejectsBeforeWaitingForFrames()
        {
            // Given a malformed selector at the public tool boundary.
            var parameters = new JObject
            {
                ["action"] = "get_counters",
                ["category"] = "Render",
                ["counters"] = new JObject(),
            };

            // When the tool runs, admission must settle without starting frame-driven native work.
            var task = ManageProfiler.HandleCommand(parameters);
            bool completedAtAdmission = task.IsCompleted;
            // Drain the original implementation's two-frame capture during a RED run too.
            for (int frame = 0; frame < 10 && !task.IsCompleted; frame++)
                yield return null;

            // Then the caller receives an immediate error instead of an unfiltered capture.
            Assert.IsTrue(task.IsCompleted, "The owned recorder capture must settle before this test exits.");
            var result = JObject.FromObject(task.GetAwaiter().GetResult());
            Assert.IsTrue(completedAtAdmission, "Invalid selectors must be rejected before awaiting a capture frame.");
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [TestCase("Owned", "Owned_valid", "Owned_valid")]
        [TestCase("Owned_valid", "Owned", "Owned_valid")]
        [TestCase("Owned", "Owned_unit", "Owned_unit")]
        [TestCase("Owned_unit", "Owned", "Owned_unit")]
        public void CollidingCounterMetadataKeys_AreRejectedBeforeWaitingForFrames(string first, string second, string collision)
        {
            var task = ManageProfiler.HandleCommand(
                new JObject
                {
                    ["action"] = "get_counters",
                    ["category"] = "Render",
                    ["counters"] = new JArray(first, second),
                }
            );

            Assert.IsTrue(task.IsCompleted, "Collision preflight must finish before starting recorders or waiting for a frame.");
            var result = JObject.FromObject(task.GetAwaiter().GetResult());
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains(collision, result.Value<string>("error"));
            StringAssert.Contains("metadata key", result.Value<string>("error"));
        }

        [TestCase("Owned_valid", "Owned_unit")]
        [TestCase("Owned", "owned_valid")]
        [TestCase("Owned ", "Owned_valid")]
        [TestCase("Owned", "Owned")]
        public void NoncollidingSuffixNames_PreserveOrdinalNamesAndDuplicateSelection(string first, string second)
        {
            var parameters = new ToolParams(new JObject { ["counters"] = new JArray(first, second) });
            var select = typeof(CounterOps).GetMethod("GetRequestedCounters", BindingFlags.NonPublic | BindingFlags.Static);
            var validate = typeof(CounterOps).GetMethod("GetCounterKeyCollision", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(select);
            Assert.IsNotNull(validate);
            var names = (List<string>)select.Invoke(null, new object[] { parameters, Unity.Profiling.ProfilerCategory.Render });

            Assert.IsNull(validate.Invoke(null, new object[] { names }));
            CollectionAssert.AreEqual(first == second ? new[] { first } : new[] { first, second }, names);
        }
    }
}
