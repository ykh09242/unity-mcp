using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    public sealed class CommandLogOutcomeTests
    {
        [TestCase(false, 16)]
        [TestCase(true, 16)]
        [TestCase(false, 1048576)]
        [TestCase(true, 1048576)]
        public void ProfileResponseLogging(bool logging, int size)
        {
            CommandRegistry.Initialize();
            const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Static;
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", flags).GetValue(null);
            var enabled = typeof(McpLogRecord).GetField("_isEnabledCached", flags);
            bool wasEnabled = (bool)enabled.GetValue(null);
            object previous = handlers["manage_scene"];
            object value = new SuccessResponse("done", new { payload = new string('x', size) });
            try
            {
                enabled.SetValue(null, logging);
                handlers["manage_scene"] = new HandlerInfo("manage_scene", null, _ => Task.FromResult(value));
                TransportCommandDispatcher.ExecuteCommandJsonAsync("{\"type\":\"manage_scene\"}", CancellationToken.None).GetAwaiter().GetResult();
                long before = GC.GetAllocatedBytesForCurrentThread();
                var watch = System.Diagnostics.Stopwatch.StartNew();
                for (int i = 0; i < 5; i++)
                    TransportCommandDispatcher.ExecuteCommandJsonAsync("{\"type\":\"manage_scene\"}", CancellationToken.None).GetAwaiter().GetResult();
                watch.Stop();
                long bytes = GC.GetAllocatedBytesForCurrentThread() - before;
                TestContext.WriteLine($"async_log logging={logging} payload={size} repetitions=5 bytes={bytes} ms={watch.Elapsed.TotalMilliseconds:F3}");
            }
            finally
            {
                handlers["manage_scene"] = previous;
                enabled.SetValue(null, wasEnabled);
            }
        }

        [TestCase(false, "throw", "ERROR")]
        [TestCase(true, "throw", "ERROR")]
        [TestCase(false, "cancel", "CANCELED")]
        [TestCase(true, "cancel", "CANCELED")]
        [TestCase(false, "typed", "ERROR")]
        [TestCase(true, "typed", "ERROR")]
        [TestCase(false, "json", "ERROR")]
        [TestCase(true, "json", "ERROR")]
        [TestCase(false, "anonymous", "ERROR")]
        [TestCase(true, "anonymous", "ERROR")]
        [TestCase(true, "success", "SUCCESS")]
        public void DispatcherLogsNormalizedOutcomeWithoutRetainingPayload(bool async, string kind, string expected)
        {
            CommandRegistry.Initialize();
            const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Static;
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", flags).GetValue(null);
            var enabled = typeof(McpLogRecord).GetField("_isEnabledCached", flags);
            bool wasEnabled = (bool)enabled.GetValue(null);
            object previous = handlers["manage_scene"];
            string secret = "payload-secret-" + Guid.NewGuid().ToString("N");
            object value =
                kind == "typed" ? new ErrorResponse(secret)
                : kind == "json" ? new JObject { ["success"] = false, ["error"] = secret }
                : kind == "anonymous" ? (object)new { success = false, error = secret }
                : new SuccessResponse("done", new { payload = new string('x', 1024 * 1024) + secret });
            try
            {
                enabled.SetValue(null, true);
                using var cancellation = new CancellationTokenSource();
                object Handle(JObject ignored)
                {
                    if (kind == "throw")
                        throw new InvalidOperationException("log-outcome-test");
                    if (kind == "cancel")
                    {
                        cancellation.Cancel();
                        throw new OperationCanceledException(cancellation.Token);
                    }
                    return value;
                }
                if (kind == "throw")
                    LogAssert.Expect(LogType.Error, new Regex("Error (in async command|processing command)"));
                handlers["manage_scene"] = new HandlerInfo("manage_scene", async ? null : Handle, async ? input => Task.FromResult(Handle(input)) : null);
                Task<string> response = TransportCommandDispatcher.ExecuteCommandJsonAsync("{\"type\":\"manage_scene\"}", cancellation.Token);
                if (kind == "cancel")
                    Assert.IsTrue(response.IsCanceled);
                else
                    Assert.IsTrue(response.Wait(TimeSpan.FromSeconds(5)));
                var flush = typeof(McpLogRecord).GetMethod("Flush", flags);
                if (flush != null)
                    Assert.IsTrue((bool)flush.Invoke(null, new object[] { 5000 }));
                string path = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Library/MCPForUnity/Logs/mcp.log"));
                using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
                using var reader = new StreamReader(stream);
                string[] lines = reader.ReadToEnd().Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
                JObject last = lines.Select(JObject.Parse).Last(row => row.Value<string>("tool") == "manage_scene");
                Assert.AreEqual(expected, last.Value<string>("status"));
                StringAssert.DoesNotContain(secret, string.Join("\n", lines));
            }
            finally
            {
                handlers["manage_scene"] = previous;
                enabled.SetValue(null, wasEnabled);
            }
        }
    }
}
