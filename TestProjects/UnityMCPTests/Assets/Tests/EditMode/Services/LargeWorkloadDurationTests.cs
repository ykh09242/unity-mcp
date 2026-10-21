using System;
using System.Reflection;
using MCPForUnity.Editor.Services;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class LargeWorkloadDurationTests
    {
        [TestCase("timeout_seconds", 7200, 7200000)]
        [TestCase("timeoutSeconds", 7200, 7200000)]
        [TestCase("timeout_seconds", 1000000000, 86400000)]
        [TestCase("timeout_seconds", 5, 300000)]
        public void StdioWaitHonorsLongOperationPolicy(string key, int seconds, int expected)
        {
            Type type = typeof(MCPServiceLocator).Assembly.GetType("MCPForUnity.Editor.Services.Transport.Transports.StdioBridgeHost");
            Assert.NotNull(type);
            MethodInfo resolve = type.GetMethod("ResolveCommandTimeoutMs", BindingFlags.Static | BindingFlags.NonPublic);
            string payload = "{\"type\":\"manage_scene\",\"params\":{\"" + key + "\":" + seconds + "}}";
            Assert.AreEqual(expected, resolve.Invoke(null, new object[] { payload, 300000 }));
        }
    }
}
