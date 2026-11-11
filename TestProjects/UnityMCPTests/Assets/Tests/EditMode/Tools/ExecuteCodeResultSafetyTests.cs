using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.Serialization;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools
{
    public class ExecuteCodeResultSafetyTests
    {
        private const BindingFlags Flags = BindingFlags.NonPublic | BindingFlags.Static;

        [Test]
        public void UnfinishedTasksAreRejectedWithoutWaitingEvenWhenNested()
        {
            foreach (int shape in new[] { 0, 1, 2, 3, 4 })
            {
                var completion = new TaskCompletionSource<int>();
                object value = completion.Task;
                if (shape == 1)
                    value = new { nested = new object[] { completion.Task } };
                if (shape == 2)
                    value = new Dictionary<string, object> { ["task"] = completion.Task };
                if (shape == 3)
                    value = new HandledErrorResult { Value = completion.Task };
                if (shape == 4)
                    value = new { nested = MakeValueTask(completion.Task) };
                AssertRejectedBeforeCompletion(value, completion);
            }
        }

        [Test]
        public void CompletedTasksAndValueTasksAreStillUnsupported()
        {
            foreach (object value in new object[] { Task.FromResult(42), Task.CompletedTask, MakeValueTask(Task.FromResult(42)), MakeValueTask(null) })
                StringAssert.Contains("Task", SerializeFailure(value).Message);
        }

        [Test]
        public void NodeBudgetStopsAndDisposesTheOriginalEnumerable()
        {
            var values = new CountingValues(1000001);
            Exception error = SerializeFailure(values);
            Assert.AreEqual("ResponseLimitException", error.GetType().Name);
            Assert.LessOrEqual(values.Reads, 1000000, "The first conversion must stop before materializing the full source.");
            Assert.IsTrue(values.Disposed);
            Assert.AreEqual(0, values.ToStringCalls, "Limit refusal must not fall back to ToString.");
        }

        [Test]
        public void ScalarAndDepthLimitsCannotBecomeStringFallbacks()
        {
            string oversized = new string('x', 64 * 1024 * 1024 + 1);
            Assert.AreEqual("ResponseLimitException", SerializeFailure(oversized).GetType().Name);
            object deep = 42;
            for (int i = 0; i < 70; i++)
                deep = new object[] { deep };
            Assert.AreEqual("ResponseLimitException", SerializeFailure(deep).GetType().Name);
            Assert.AreEqual("ResponseLimitException", SerializeFailure(new HandledErrorResult { Value = deep }).GetType().Name);
        }

        [Test]
        public void OrdinaryResultsRetainUnityConvertersAndSerializationFallback()
        {
            var result = JToken.FromObject(Serialize(new { position = new UnityEngine.Vector3(1, 2, 3), values = new[] { 1, 2 } }));
            Assert.AreEqual(3, (int)result["position"]["z"]);
            Assert.AreEqual(2, (int)result["values"][1]);
            Assert.AreEqual("fallback", JToken.FromObject(Serialize(new BrokenGetter())).Value<string>());
        }

        [Test]
        public void UnsupportedAndOversizeResultsAreReturnedAndRecordedAsErrors()
        {
            ExecuteCode.HandleCommand(new JObject { ["action"] = "clear_history" });
            foreach (
                string code in new[]
                {
                    "return System.Threading.Tasks.Task.FromResult(42);",
                    "object value = 42; for (int i = 0; i < 70; i++) value = new object[] { value }; return value;",
                }
            )
            {
                var response = ExecuteCode.HandleCommand(new JObject { ["action"] = "execute", ["code"] = code });
                Assert.IsInstanceOf<ErrorResponse>(response);
                StringAssert.Contains("Result serialization failed", ((ErrorResponse)response).Error);
            }
            JObject history = JObject.FromObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "get_history" }));
            Assert.AreEqual(2, (int)history["data"]["total"]);
            foreach (JToken entry in history["data"]["entries"])
                Assert.IsFalse((bool)entry["success"]);
        }

        [Test]
        public void HistoryPreviewStopsWritingAtItsOwnSmallLimit()
        {
            ExecuteCode.HandleCommand(new JObject { ["action"] = "clear_history" });
            var values = new CountingValues(1000001);
            typeof(ExecuteCode)
                .GetMethod("AddToHistory", Flags)
                .Invoke(null, new object[] { "return values;", new SuccessResponse("ok", new { result = values }), 0d, true, "auto" });
            JObject history = JObject.FromObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "get_history" }));
            string preview = (string)history["data"]["entries"][0]["resultPreview"];
            Assert.That(preview.Length, Is.LessThanOrEqualTo(203));
            StringAssert.EndsWith("...", preview);
            Assert.That(values.Reads, Is.InRange(1, 200));
            Assert.IsTrue(values.Disposed);
            Assert.AreEqual(0, values.ToStringCalls);
        }

        [Test]
        public void HistoryPreviewBoundsStringsBeforeLateEscapeAllocation()
        {
            MethodInfo allocationMethod = typeof(GC).GetMethod("GetAllocatedBytesForCurrentThread", BindingFlags.Static | BindingFlags.Public);
            Assert.IsNotNull(allocationMethod, "This regression requires managed allocation accounting.");
            var allocated = (Func<long>)Delegate.CreateDelegate(typeof(Func<long>), allocationMethod);
            MethodInfo previewMethod = typeof(ExecuteCode).GetMethod("CreateResultPreview", Flags);
            string lateEscape = new string('x', 16 * 1024 * 1024) + "\n";
            var token = new JValue(lateEscape);
            var property = new JProperty(lateEscape, 1);
            foreach (
                object value in new object[]
                {
                    new { result = (JToken)token, compiler = "auto" },
                    new { result = (JToken)new JObject(property), compiler = "auto" },
                    lateEscape,
                }
            )
            {
                previewMethod.Invoke(null, new[] { value }); // Warm contracts and reflection before measuring.
                long before = allocated();
                string preview = (string)previewMethod.Invoke(null, new[] { value });
                long bytes = allocated() - before;
                Console.WriteLine("OBSERVATION: late-escape history preview allocated " + bytes + " bytes for " + preview.Length + " characters.");
                Assert.Less(bytes, 1024 * 1024, "A tiny preview must not allocate a buffer proportional to the late-escaped input.");
                Assert.LessOrEqual(preview.Length, 203);
                StringAssert.EndsWith("...", preview);
            }
            Assert.AreSame(lateEscape, token.Value, "Previewing must preserve the original token value.");
            Assert.AreEqual(lateEscape, property.Name, "Previewing must preserve the original property name.");
        }

        [Test]
        public void HistoryPreviewPreservesSmallJsonIncludingEscapes()
        {
            object value = new
            {
                text = "line one\n\"line two\"",
                values = new[] { 1, 2 },
                empty = (string)null,
            };
            string preview = (string)typeof(ExecuteCode).GetMethod("CreateResultPreview", Flags).Invoke(null, new[] { value });
            Assert.AreEqual(Newtonsoft.Json.JsonConvert.SerializeObject(value), preview);
        }

        private static object MakeValueTask(Task<int> task)
        {
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type type = assembly.GetType(task == null ? "System.Threading.Tasks.ValueTask" : "System.Threading.Tasks.ValueTask`1");
                if (type != null)
                    return task == null ? Activator.CreateInstance(type) : Activator.CreateInstance(type.MakeGenericType(typeof(int)), new object[] { task });
            }
            Assert.Fail("The selected runtime must provide ValueTask for this regression.");
            return null;
        }

        private static void AssertRejectedBeforeCompletion(object value, TaskCompletionSource<int> completion)
        {
            Exception error = null;
            bool returned = false;
            var thread = new Thread(() =>
            {
                try
                {
                    Serialize(value);
                }
                catch (Exception exception)
                {
                    error = exception;
                }
            })
            {
                IsBackground = true,
            };
            try
            {
                thread.Start();
                returned = thread.Join(1000);
            }
            finally
            {
                completion.TrySetResult(42);
                Assert.IsTrue(thread.Join(5000), "The probe thread must finish after cleanup.");
            }
            Assert.IsTrue(returned, "Serialization waited for Task.Result instead of rejecting the awaitable.");
            Assert.IsNotNull(error, "Awaitables must be rejected, not silently serialized.");
            StringAssert.Contains("Task", error.Message);
        }

        private static object Serialize(object value)
        {
            try
            {
                return typeof(ExecuteCode).GetMethod("SerializeResult", Flags).Invoke(null, new[] { value });
            }
            catch (TargetInvocationException exception)
            {
                throw exception.InnerException ?? exception;
            }
        }

        private static Exception SerializeFailure(object value)
        {
            Exception error = null;
            try
            {
                Serialize(value);
            }
            catch (Exception exception)
            {
                error = exception;
            }
            Assert.IsNotNull(error, "Unsafe result serialization unexpectedly succeeded.");
            return error;
        }

        private sealed class HandledErrorResult
        {
            public object Value { get; set; }

            [OnError]
            internal void IgnoreError(StreamingContext context, ErrorContext error) => error.Handled = true;
        }

        private sealed class BrokenGetter
        {
            public int Value => throw new InvalidOperationException("unavailable");

            public override string ToString() => "fallback";
        }

        private sealed class CountingValues : IEnumerable<int>
        {
            private readonly int count;
            public int Reads;
            public int ToStringCalls;
            public bool Disposed;

            public CountingValues(int count) => this.count = count;

            public IEnumerator<int> GetEnumerator()
            {
                try
                {
                    for (int i = 0; i < count; i++)
                    {
                        Reads++;
                        yield return i;
                    }
                }
                finally
                {
                    Disposed = true;
                }
            }

            IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();

            public override string ToString()
            {
                ToStringCalls++;
                return "values";
            }
        }
    }
}
