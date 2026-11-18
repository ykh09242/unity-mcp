using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class VerificationConsoleWindowTests
    {
        private VerificationConsoleWindow window;
        private double now;
        private const string Job = "job";

        [SetUp]
        public void SetUp()
        {
            now = 0;
            window = new VerificationConsoleWindow(() => now);
        }

        [TearDown]
        public void TearDown() => window.Stop("discarded");

        private JObject Read() => (JObject)JObject.FromObject(window.Read(window.Id, Job))["data"];

        [Test]
        public void CapturesExpectedApplicationErrorBeforeBindingAndStopsAtSettlement()
        {
            const string marker = "Verification console checkpoint regression";
            LogAssert.Expect(LogType.Error, marker);
            Debug.LogError(marker);
            Assert.IsTrue(window.Bind(window.Id, Job, 1000));
            window.Record("normal output", "", LogType.Log);
            window.Record("[MCPForUnity] Internal test error", "", LogType.Error);
            window.Stop("settled");
            window.Record("after settlement", "", LogType.Exception);
            var data = Read();
            Assert.AreEqual("settled", (string)data["end"]);
            Assert.AreEqual(1, (int)data["observed_error_count"]);
            Assert.AreEqual(marker, (string)data["findings"][0]["message"]);
            Assert.AreEqual(VerificationConsoleWindow.Generation, (string)data["generation"]);
            Assert.AreEqual(Job, (string)data["job_id"]);
            Assert.IsFalse((bool)JObject.FromObject(window.Read(window.Id, "another-job"))["success"]);
            Assert.IsFalse((bool)JObject.FromObject(window.Read("another-checkpoint", Job))["success"]);
        }

        [Test]
        public void FindingsAndMultibytePreviewsAreBoundedWithoutHidingErrorCount()
        {
            Assert.IsTrue(window.Bind(window.Id, Job, 1000));
            string text = new string('\u4e00', 400);
            for (int i = 0; i < VerificationConsoleWindow.FindingLimit + 1; i++)
                window.Record(text, "", LogType.Assert);
            window.Stop("settled");
            var data = Read();
            Assert.AreEqual(VerificationConsoleWindow.FindingLimit + 1, (int)data["observed_error_count"]);
            var rows = (JArray)data["findings"];
            Assert.AreEqual(VerificationConsoleWindow.FindingLimit, rows.Count);
            Assert.IsTrue((bool)data["messages_truncated"]);
            Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount((string)rows[0]["message"]), VerificationConsoleWindow.MessageLimit);
        }

        [Test]
        public void ExpiredOrUnclaimedWindowsCannotSealACleanInterval()
        {
            Assert.IsFalse(window.Bind("foreign-window", Job, 1000));
            Assert.IsFalse(window.Bind(window.Id, Job, 1000));
            window = new VerificationConsoleWindow(() => now);
            Assert.IsTrue(window.Bind(window.Id, Job, 1000));
            now = 32;
            window.Stop("settled");
            Assert.AreEqual("expired", (string)Read()["end"]);
        }
    }
}
