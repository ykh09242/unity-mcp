using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Helpers
{
    public class CommandLogSecurityTests
    {
        [Test]
        public void PhaseRecordAcceptsOnlyFixedMetadataWithoutChangingPreferences()
        {
            JObject row = null;
            var timing = new MCPForUnity.Editor.Services.Transport.CommandPhaseTiming(
                () => 0,
                1000,
                value => row = value,
                MCPForUnity.Editor.Services.Transport.CommandPhaseTiming.Transport.Direct
            );
            timing.ExecutionDone();
            timing.DeliveryDone(MCPForUnity.Editor.Services.Transport.CommandPhaseTiming.Delivery.Unobserved);
            CollectionAssert.AreEquivalent(
                new[]
                {
                    "event",
                    "sample_every",
                    "correlation",
                    "transport",
                    "delivery",
                    "execution_settled",
                    "execution_settled_after_delivery",
                    "total_ms",
                    "incomplete_stages",
                },
                System.Linq.Enumerable.Select(row.Properties(), property => property.Name)
            );
            Assert.IsTrue(Guid.TryParseExact(row.Value<string>("correlation"), "N", out _));
            Assert.AreEqual(64, row.Value<int>("sample_every"));
        }

        [Test]
        public void RecorderExcludesPayloadActionAndErrorText()
        {
            string sentinel = "secret-probe-" + Guid.NewGuid().ToString("N");
            var enabled = typeof(McpLogRecord).GetField("_isEnabledCached", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
            bool wasEnabled = (bool)enabled.GetValue(null);
            try
            {
                enabled.SetValue(null, true);
                McpLogRecord.Log(
                    "manage_script",
                    new JObject
                    {
                        ["action"] = sentinel,
                        ["api_key"] = sentinel,
                        ["contents"] = sentinel,
                        ["nested"] = new JObject { ["url"] = sentinel },
                    },
                    "tool",
                    "ERROR",
                    10,
                    sentinel
                );
                Assert.IsTrue(McpLogRecord.Flush(5000));
                string logDir = Path.Combine(Application.dataPath, "..", "Library", "MCPForUnity", "Logs");
                foreach (string name in new[] { "mcp.log", "mcpError.log" })
                {
                    using var stream = new FileStream(Path.Combine(logDir, name), FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
                    using var reader = new StreamReader(stream);
                    string text = reader.ReadToEnd();
                    StringAssert.DoesNotContain(sentinel, text);
                    StringAssert.Contains("manage_script", text);
                    StringAssert.Contains("ERROR", text);
                    StringAssert.DoesNotContain("\"params\"", text);
                    StringAssert.DoesNotContain("\"error\"", text);
                }
            }
            finally
            {
                enabled.SetValue(null, wasEnabled);
            }
        }
    }
}
