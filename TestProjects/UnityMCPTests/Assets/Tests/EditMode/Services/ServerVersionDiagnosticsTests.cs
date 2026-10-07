using System;
using System.Collections;
using System.Threading;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    public class ServerVersionDiagnosticsTests
    {
        private static readonly DateTime Now = new DateTime(2026, 10, 7, 0, 0, 0, DateTimeKind.Utc);
        private static readonly string ExpectedCommit = new string('a', 40);
        private static readonly string SelectedSource = "https://github.com/example/repo/archive/" + ExpectedCommit + ".zip#subdirectory=Server";

        [SetUp]
        public void SetUp() => ServerVersionDiagnostics.ResetForTests();

        [TearDown]
        public void TearDown() => ServerVersionDiagnostics.ResetForTests();

        [Test]
        public void DifferentServerAndEditorVersionNumbersDoNotCreateAFalseMismatch()
        {
            ServerVersionDiagnostics.Observe(Info("1.2.0", ExpectedCommit, new string('b', 32)), TransportMode.Stdio, Now);
            string description = ServerVersionDiagnostics.Describe(SelectedSource, Now);
            StringAssert.Contains("Python v1.2.0", description);
            StringAssert.Contains("matches selected build", description);
            StringAssert.DoesNotContain("build mismatch", description);
        }

        [Test]
        public void MismatchIsVisibleWithRestartGuidanceAndManualPinPreservation()
        {
            ServerVersionDiagnostics.Observe(Info("1.1.0", new string('c', 40), new string('b', 32)), TransportMode.Stdio, Now);
            string description = ServerVersionDiagnostics.Describe(SelectedSource, Now);
            StringAssert.Contains("build mismatch with selected source", description);
            StringAssert.Contains("restart the affected client's MCP connection", description);
            StringAssert.Contains("preserve intentional manual pins", description);
        }

        [Test]
        public void LegacyMissingAndHttpMetadataCannotMasqueradeAsObservedStdio()
        {
            ServerVersionDiagnostics.Observe(null, TransportMode.Stdio, Now);
            ServerVersionDiagnostics.Observe(Info("1.1.0", ExpectedCommit, new string('b', 32)), TransportMode.Http, Now);
            StringAssert.Contains("no build metadata received", ServerVersionDiagnostics.Describe(SelectedSource, Now));
        }

        [Test]
        public void MissingComparableBuildAndOldObservationsRemainExplicitlyUnknown()
        {
            ServerVersionDiagnostics.Observe(Info("1.2.0", null, new string('b', 32)), TransportMode.Stdio, Now);
            string description = ServerVersionDiagnostics.Describe("file:../custom-source", Now.AddMinutes(3));
            StringAssert.Contains("build comparison unavailable", description);
            StringAssert.Contains("stale observation; current process state unknown", description);
            StringAssert.DoesNotContain("build mismatch", description);
        }

        [Test]
        public void MalformedOrOversizedMetadataDoesNotCreateAnObservation()
        {
            ServerVersionDiagnostics.Observe(Info(new string('x', 81), ExpectedCommit, new string('b', 32)), TransportMode.Stdio, Now);
            ServerVersionDiagnostics.Observe(Info("1.2.0\nforged", ExpectedCommit, new string('b', 32)), TransportMode.Stdio, Now);
            var withSecret = Info("1.2.0", ExpectedCommit, new string('b', 32));
            withSecret["token"] = "synthetic-secret";
            ServerVersionDiagnostics.Observe(withSecret, TransportMode.Stdio, Now);
            StringAssert.Contains("no build metadata received", ServerVersionDiagnostics.Describe(SelectedSource, Now));
        }

        [Test]
        public void MultipleServersStayBoundedAndKeepSeparateBuildObservations()
        {
            for (int index = 0; index < 9; index++)
                ServerVersionDiagnostics.Observe(Info("1.0." + index, ExpectedCommit, index.ToString("x32")), TransportMode.Stdio, Now.AddSeconds(index));
            string description = ServerVersionDiagnostics.Describe(SelectedSource, Now.AddSeconds(10));
            StringAssert.DoesNotContain("Python v1.0.0,", description);
            for (int index = 1; index < 9; index++)
                StringAssert.Contains("Python v1.0." + index + ",", description);
        }

        [UnityTest]
        public IEnumerator ActualCommandDispatcherAcceptsLegacyAndRecordsOnlyStdioOrigin()
        {
            var legacy = JsonConvert.DeserializeObject<Command>("{\"type\":\"ping\",\"params\":{}}");
            var operation = TransportCommandDispatcher.ExecuteCommandAsync(legacy, CancellationToken.None, TransportMode.Stdio);
            for (int frame = 0; frame < 100 && !operation.Response.IsCompleted; frame++)
                yield return null;
            Assert.IsTrue(operation.Response.IsCompleted);
            Assert.AreEqual("pong", (string)JObject.Parse(operation.Response.Result.ToJson())["result"]["message"]);
            StringAssert.Contains("no build metadata received", ServerVersionDiagnostics.Describe(SelectedSource, DateTime.UtcNow));
            var command = new Command { type = "ping", server_info = Info("1.2.0", ExpectedCommit, new string('b', 32)) };
            operation = TransportCommandDispatcher.ExecuteCommandAsync(command, CancellationToken.None, TransportMode.Http);
            for (int frame = 0; frame < 100 && !operation.Response.IsCompleted; frame++)
                yield return null;
            Assert.IsTrue(operation.Response.IsCompleted);
            StringAssert.Contains("no build metadata received", ServerVersionDiagnostics.Describe(SelectedSource, DateTime.UtcNow));
            operation = TransportCommandDispatcher.ExecuteCommandAsync(command, CancellationToken.None, TransportMode.Stdio);
            for (int frame = 0; frame < 100 && !operation.Response.IsCompleted; frame++)
                yield return null;
            Assert.IsTrue(operation.Response.IsCompleted);
            StringAssert.Contains("Python v1.2.0", ServerVersionDiagnostics.Describe(SelectedSource, DateTime.UtcNow));
        }

        private static JObject Info(string version, string commit, string serverId) =>
            new JObject
            {
                ["version"] = version,
                ["source_commit"] = commit == null ? JValue.CreateNull() : new JValue(commit),
                ["server_id"] = serverId,
            };
    }
}
