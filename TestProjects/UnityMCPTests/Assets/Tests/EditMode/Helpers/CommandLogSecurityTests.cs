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
        public void RecorderExcludesPayloadActionAndErrorText()
        {
            string sentinel = "secret-probe-" + Guid.NewGuid().ToString("N");
            bool wasEnabled = McpLogRecord.IsEnabled;
            try
            {
                McpLogRecord.IsEnabled = true;
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
                string logDir = Path.Combine(Application.dataPath, "..", "Library", "MCPForUnity", "Logs");
                foreach (string name in new[] { "mcp.log", "mcpError.log" })
                {
                    string text = File.ReadAllText(Path.Combine(logDir, name));
                    StringAssert.DoesNotContain(sentinel, text);
                    StringAssert.Contains("manage_script", text);
                    StringAssert.Contains("ERROR", text);
                    StringAssert.DoesNotContain("\"params\"", text);
                    StringAssert.DoesNotContain("\"error\"", text);
                }
            }
            finally
            {
                McpLogRecord.IsEnabled = wasEnabled;
            }
        }
    }
}
