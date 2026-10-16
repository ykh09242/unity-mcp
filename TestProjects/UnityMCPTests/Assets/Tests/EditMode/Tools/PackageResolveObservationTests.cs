using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class PackageResolveObservationTests
    {
        private const BindingFlags Hidden = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
        private readonly Dictionary<FieldInfo, object> originals = new Dictionary<FieldInfo, object>();
        private Dictionary<string, PackageJob> jobs;
        private Dictionary<string, PackageJob> priorJobs;
        private string session;
        private int invocations;
        private bool captured;
        private long clock;

        [SetUp]
        public void SetUp()
        {
            captured = false;
            originals.Clear();
            // Validate every seam before changing anything; the old product fails here safely.
            foreach (string name in new[] { "ResolveInvoker", "ResolveSnapshotReader", "ResolveReadinessReader", "ResolveClock", "ResolveHasPendingRequests" })
                Capture(typeof(ManagePackages), name);
            Capture(typeof(PackageJobManager), "SessionReader");
            Capture(typeof(PackageJobManager), "SessionWriter");
            Capture(typeof(PackageJobManager), "ResolveRegistration");
            AssertReadPathHasNoDirectClientCalls();
            jobs = (Dictionary<string, PackageJob>)typeof(PackageJobManager).GetField("Jobs", Hidden).GetValue(null);
            priorJobs = new Dictionary<string, PackageJob>(jobs);
            jobs.Clear();
            captured = true;
            session = "";
            invocations = 0;
            clock = 1000;
            Set(typeof(ManagePackages), "ResolveInvoker", (Action)(() => invocations++));
            Set(typeof(ManagePackages), "ResolveSnapshotReader", (Func<JArray>)(() => Snapshot()));
            Set(typeof(ManagePackages), "ResolveReadinessReader", (Func<JObject>)(() => new JObject { ["is_compiling"] = false, ["is_updating"] = false }));
            Set(typeof(ManagePackages), "ResolveClock", (Func<long>)(() => clock));
            Set(typeof(ManagePackages), "ResolveHasPendingRequests", (Func<bool>)(() => false));
            Set(typeof(PackageJobManager), "SessionReader", (Func<string>)(() => session));
            Set(typeof(PackageJobManager), "SessionWriter", (Action<string>)(value => session = value));
        }

        [TearDown]
        public void TearDown()
        {
            if (!captured)
                return;
            foreach (var item in originals)
                item.Key.SetValue(null, item.Value);
            jobs.Clear();
            foreach (var item in priorJobs)
                jobs.Add(item.Key, item.Value);
        }

        private void Capture(Type type, string name)
        {
            var field = type.GetField(name, Hidden);
            Assert.IsNotNull(field, "Required explicit fake boundary: " + name);
            originals.Add(field, field.GetValue(null));
        }

        private static void Set(Type type, string name, object value) => type.GetField(name, Hidden).SetValue(null, value);

        private static JArray Snapshot() =>
            new JArray(
                new JObject
                {
                    ["name"] = "com.fixture.one",
                    ["version"] = "1.2.3",
                    ["source"] = "Registry",
                }
            );

        private static JObject Call(string action, string id = null) =>
            JObject.FromObject(ManagePackages.HandleCommand(new JObject { ["action"] = action, ["job_id"] = id }));

        private static JObject Evidence(JObject response) => (JObject)response["data"]["resolve"];

        [Test]
        public void TriggerAcknowledgesOnce_StatusNeverRepeatsOrClaimsCompletion()
        {
            var initial = Call("resolve_packages");
            Assert.IsTrue((bool)initial["success"]);
            Assert.AreEqual("unknown", (string)initial["data"]["status"]);
            Assert.AreEqual("triggered", (string)Evidence(initial)["trigger_state"]);
            Assert.AreEqual("unknown", (string)Evidence(initial)["completion"]);
            Assert.AreEqual("editor_session", (string)Evidence(initial)["persistence_scope"]);
            Assert.AreEqual(1, invocations);
            string id = (string)initial["data"]["job_id"];
            clock += 900000;
            var status = Call("status", id);
            Assert.AreEqual("unknown", (string)Evidence(status)["completion"]);
            Assert.AreEqual(1, invocations);
            Assert.AreEqual("observed", (string)status["data"]["observations"]["installed_snapshot"]["status"]);
            Assert.AreEqual("idle", (string)status["data"]["observations"]["readiness"]["status"]);
        }

        [Test]
        public void PendingOwnedRequestRefusesBeforeMutation()
        {
            Set(typeof(ManagePackages), "ResolveHasPendingRequests", (Func<bool>)(() => true));
            var result = Call("resolve_packages");
            Assert.IsFalse((bool)result["success"]);
            Assert.AreEqual("known_client_operation_pending", (string)result["data"]["reason"]);
            Assert.AreEqual(0, invocations);
            Assert.AreEqual(0, jobs.Count);
        }

        [Test]
        public void IntentWriteFailureInvokesNothing()
        {
            Set(typeof(PackageJobManager), "SessionWriter", (Action<string>)(_ => throw new InvalidOperationException("owned-store-failure")));
            var result = Call("resolve_packages");
            Assert.IsFalse((bool)result["success"]);
            Assert.AreEqual("persistence_failed", (string)result["data"]["reason"]);
            Assert.AreEqual(0, invocations);
            Assert.AreEqual(0, jobs.Count);
        }

        [Test]
        public void ThrowingInvocationDoesNotProveNativeWorkNeverStarted()
        {
            Set(
                typeof(ManagePackages),
                "ResolveInvoker",
                (Action)(
                    () =>
                    {
                        invocations++;
                        throw new Exception("private-url-token-canary");
                    }
                )
            );
            var result = Call("resolve_packages");
            Assert.IsFalse((bool)result["success"]);
            Assert.AreEqual("failed", (string)result["data"]["status"]);
            Assert.AreEqual("unknown", (string)Evidence(result)["completion"]);
            Assert.AreEqual(JTokenType.Null, Evidence(result)["triggered"].Type);
            Assert.IsFalse(result.ToString().Contains("private-url-token-canary"));
            Assert.AreEqual(1, invocations);
        }

        [TestCase("registering")]
        [TestCase("registered")]
        public void GlobalEventNeverCompletesResolve(string phase)
        {
            var initial = Call("resolve_packages");
            typeof(ManagePackages).GetMethod("RecordPackageRegistration", Hidden).Invoke(null, new object[] { phase });
            var status = Call("status", (string)initial["data"]["job_id"]);
            Assert.AreEqual("unknown", (string)Evidence(status)["completion"]);
            Assert.AreEqual("editor_global", (string)status["data"]["observations"]["scope"]);
            Assert.AreEqual(1, invocations);
        }

        [Test]
        public void SnapshotFailureLeavesAcknowledgmentUnknownAndRedacted()
        {
            Set(typeof(ManagePackages), "ResolveSnapshotReader", (Func<JArray>)(() => throw new Exception("private-path-canary")));
            var result = Call("resolve_packages");
            Assert.IsTrue((bool)result["success"]);
            Assert.AreEqual("unknown", (string)Evidence(result)["completion"]);
            Assert.AreEqual("unavailable", (string)result["data"]["observations"]["installed_snapshot"]["status"]);
            Assert.IsFalse(result.ToString().Contains("private-path-canary"));
        }

        [Test]
        public void SnapshotIsDeterministicAndDoesNotExposeExtraMetadata()
        {
            var one = Snapshot();
            one.Add(
                new JObject
                {
                    ["name"] = "com.fixture.two",
                    ["version"] = "2.0.0",
                    ["source"] = "Git",
                    ["packageId"] = "https://secret-canary",
                }
            );
            Set(typeof(ManagePackages), "ResolveSnapshotReader", (Func<JArray>)(() => one));
            var first = Call("resolve_packages");
            Set(typeof(ManagePackages), "ResolveSnapshotReader", (Func<JArray>)(() => new JArray(one[1].DeepClone(), one[0].DeepClone())));
            var second = Call("status", (string)first["data"]["job_id"]);
            Assert.AreEqual(
                (string)first["data"]["observations"]["installed_snapshot"]["digest"],
                (string)second["data"]["observations"]["installed_snapshot"]["digest"]
            );
            Assert.IsFalse(first.ToString().Contains("secret-canary"));
        }

        [TestCase("duplicate")]
        [TestCase("count")]
        [TestCase("bytes")]
        [TestCase("invalid")]
        public void InvalidOrOversizedSnapshotCannotProduceValidDigest(string kind)
        {
            var raw = Snapshot();
            if (kind == "duplicate")
                raw.Add(raw[0].DeepClone());
            if (kind == "count")
                for (int i = 1; i < 4097; i++)
                    raw.Add(
                        new JObject
                        {
                            ["name"] = "com.fixture.p" + i,
                            ["version"] = "1.0.0",
                            ["source"] = "Registry",
                        }
                    );
            if (kind == "bytes")
                for (int i = 1; i < 1500; i++)
                    raw.Add(
                        new JObject
                        {
                            ["name"] = "com.fixture." + new string('a', 190) + i,
                            ["version"] = "1." + new string('0', 77),
                            ["source"] = "Registry",
                        }
                    );
            if (kind == "invalid")
                raw[0]["name"] = "https://private-canary";
            Set(typeof(ManagePackages), "ResolveSnapshotReader", (Func<JArray>)(() => raw));
            var result = Call("resolve_packages");
            Assert.AreEqual("unavailable", (string)result["data"]["observations"]["installed_snapshot"]["status"]);
            Assert.IsNull(result["data"]["observations"]["installed_snapshot"]["digest"]);
            Assert.IsFalse(result.ToString().Contains("private-canary"));
        }

        [Test]
        public void SameSessionIntentRestoreIsUnknownAndMissingSessionNeverRepeats()
        {
            session = new JObject
            {
                ["jobs"] = new JArray(
                    new JObject
                    {
                        ["job_id"] = "interrupted",
                        ["status"] = "running",
                        ["operation"] = "resolve",
                        ["resolve"] = new JObject
                        {
                            ["trigger_state"] = "requested",
                            ["triggered"] = JValue.CreateNull(),
                            ["completion"] = "unknown",
                        },
                    }
                ),
            }.ToString();
            typeof(PackageJobManager).GetMethod("TryRestoreFromSessionState", Hidden).Invoke(null, null);
            var result = Call("status", "interrupted");
            Assert.AreEqual("unknown", (string)result["data"]["status"]);
            Assert.AreEqual("unknown", (string)Evidence(result)["trigger_state"]);
            Assert.AreEqual(0, invocations);
            session = "";
            jobs.Clear();
            var missing = Call("status", "interrupted");
            Assert.IsFalse((bool)missing["success"]);
            Assert.AreEqual(0, invocations);
        }

        [Test]
        public void IndependentRequestOutcomeSurvivesResolveObservation()
        {
            var resolve = Call("resolve_packages");
            string add = PackageJobManager.StartJob("add", "com.fixture.other");
            PackageJobManager.CompleteJob(add, true, version: "2.0.0", name: "com.fixture.other");
            var status = Call("status", (string)resolve["data"]["job_id"]);
            Assert.AreEqual("unknown", (string)Evidence(status)["completion"]);
            Assert.AreEqual(PackageJobStatus.Succeeded, PackageJobManager.GetJob(add).Status);
            Assert.AreEqual(1, invocations);
        }

        [TestCase(true, false, "blocked")]
        [TestCase(false, true, "blocked")]
        [TestCase(false, false, "idle")]
        public void ReadinessIsIndependentAndPingDoesNotTrigger(bool compiling, bool updating, string expected)
        {
            Set(
                typeof(ManagePackages),
                "ResolveReadinessReader",
                (Func<JObject>)(() => new JObject { ["is_compiling"] = compiling, ["is_updating"] = updating })
            );
            var initial = Call("resolve_packages");
            var ping = Call("ping");
            Assert.AreEqual(expected, (string)initial["data"]["observations"]["readiness"]["status"]);
            Assert.AreEqual("unknown", (string)Evidence(initial)["completion"]);
            Assert.IsTrue((bool)ping["success"]);
            Assert.AreEqual(1, invocations);
        }

        [Test]
        public void AcknowledgmentWriteFailureCannotPromoteRestoredIntentOrRepeat()
        {
            int writes = 0;
            Set(
                typeof(PackageJobManager),
                "SessionWriter",
                (Action<string>)(
                    value =>
                    {
                        if (++writes == 1)
                            session = value;
                        else
                            throw new Exception("owned-late-write-failure");
                    }
                )
            );
            var initial = Call("resolve_packages");
            Assert.IsTrue((bool)initial["success"]);
            Assert.AreEqual("triggered", (string)Evidence(initial)["trigger_state"]);
            jobs.Clear();
            typeof(PackageJobManager).GetMethod("TryRestoreFromSessionState", Hidden).Invoke(null, null);
            var restored = Call("status", (string)initial["data"]["job_id"]);
            Assert.AreEqual("unknown", (string)Evidence(restored)["trigger_state"]);
            Assert.AreEqual("unknown", (string)Evidence(restored)["completion"]);
            Assert.AreEqual(1, invocations);
        }

        private static void AssertReadPathHasNoDirectClientCalls()
        {
            var codes = typeof(OpCodes)
                .GetFields(BindingFlags.Public | BindingFlags.Static)
                .Where(field => field.FieldType == typeof(OpCode))
                .Select(field => (OpCode)field.GetValue(null))
                .GroupBy(code => (ushort)code.Value)
                .ToDictionary(group => group.Key, group => group.First());
            foreach (string name in new[] { "ResolvePackages", "ResolveJobData", "CaptureResolveObservations", "CaptureResolveSnapshot", "Ping", "GetStatus" })
            {
                var method = typeof(ManagePackages).GetMethod(name, Hidden);
                Assert.IsNotNull(method, name);
                byte[] il = method.GetMethodBody().GetILAsByteArray();
                for (int i = 0; i < il.Length; )
                {
                    ushort key = il[i++];
                    if (key == 0xfe)
                        key = (ushort)(0xfe00 | il[i++]);
                    var code = codes[key];
                    if (code.OperandType == OperandType.InlineMethod)
                    {
                        var called = method.Module.ResolveMethod(BitConverter.ToInt32(il, i));
                        Assert.AreNotEqual(
                            "UnityEditor.PackageManager.Client",
                            called.DeclaringType.FullName,
                            "An accidental direct Client call bypasses the explicit fake mutation boundary: " + name
                        );
                    }
                    int length = code.OperandType switch
                    {
                        OperandType.InlineNone => 0,
                        OperandType.ShortInlineBrTarget or OperandType.ShortInlineI or OperandType.ShortInlineVar => 1,
                        OperandType.InlineVar => 2,
                        OperandType.InlineI8 or OperandType.InlineR => 8,
                        OperandType.InlineSwitch => 4 + 4 * BitConverter.ToInt32(il, i),
                        _ => 4,
                    };
                    i += length;
                }
            }
        }

        [TestCase("PendingRequests")]
        [TestCase("PendingListRequests")]
        [TestCase("PendingSearchRequests")]
        public void ProductionOwnedRequestGuardRefusesWithoutAllocatingARequest(string field)
        {
            var pending = (IDictionary)typeof(ManagePackages).GetField(field, Hidden).GetValue(null);
            string owned = "dev05-guard-" + Guid.NewGuid().ToString("N");
            pending.Add(owned, null);
            try
            {
                Set(typeof(ManagePackages), "ResolveHasPendingRequests", originals[typeof(ManagePackages).GetField("ResolveHasPendingRequests", Hidden)]);
                Assert.IsFalse((bool)Call("resolve_packages")["success"]);
                Assert.AreEqual(0, invocations);
            }
            finally
            {
                pending.Remove(owned);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MalformedOptionalSessionMetadataDoesNotRecoverCompletion(bool malformed)
        {
            session = new JObject
            {
                ["resolve_registration"] = malformed
                    ? (JToken)"private-canary"
                    : new JObject { ["phase"] = "registered", ["observed_unix_ms"] = JToken.Parse("999999999999999999999999999999999") },
                ["jobs"] = new JArray(
                    new JObject
                    {
                        ["job_id"] = "stale",
                        ["status"] = "unknown",
                        ["operation"] = "resolve",
                        ["resolve"] = "private-canary",
                    }
                ),
            }.ToString();
            typeof(PackageJobManager).GetMethod("TryRestoreFromSessionState", Hidden).Invoke(null, null);
            var result = Call("status", "stale");
            Assert.AreEqual("unknown", (string)Evidence(result)["completion"]);
            Assert.IsFalse(result.ToString().Contains("private-canary"));
            Assert.AreEqual(0, invocations);
        }

        [Test]
        public void GenericRequestCompletionCannotPromoteResolve()
        {
            var initial = Call("resolve_packages");
            string id = (string)initial["data"]["job_id"];
            PackageJobManager.CompleteJob(id, true);
            Assert.AreEqual("unknown", (string)Call("status", id)["data"]["status"]);
            Assert.AreEqual(1, invocations);
        }

        [Test]
        [Explicit("Only the separately reviewed owned-project read-only metadata characterization.")]
        public void ActualRegisteredMetadataAndReadiness_ReadOnlyOwnedProject()
        {
            string expected = Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_PROJECT");
            string nonce = Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE");
            Assert.IsTrue(Application.isBatchMode);
            Assert.IsTrue(string.IsNullOrEmpty(Environment.GetEnvironmentVariable("UNITY_MCP_ALLOW_BATCH")));
            Assert.IsFalse(string.IsNullOrWhiteSpace(expected));
            Assert.IsTrue(Guid.TryParseExact(nonce, "N", out _));
            string actual = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            Assert.AreEqual(Path.GetFullPath(expected), actual, "Exact owned QA project required.");
            Assert.IsTrue(actual.Replace('\\', '/').Contains("/.unity-ci/CS-20261016-core/"));
            var readers = new[] { "ResolveSnapshotReader", "ResolveReadinessReader", "ResolveClock" }
                .Select(name => typeof(ManagePackages).GetField(name, Hidden))
                .ToArray();
            object[] fakes = readers.Select(field => field.GetValue(null)).ToArray();
            var invoker = typeof(ManagePackages).GetField("ResolveInvoker", Hidden);
            object fakeMutation = invoker.GetValue(null);
            var sessionReader = typeof(PackageJobManager).GetField("SessionReader", Hidden).GetValue(null);
            var sessionWriter = typeof(PackageJobManager).GetField("SessionWriter", Hidden).GetValue(null);
            try
            {
                Set(typeof(ManagePackages), "ResolveInvoker", (Action)(() => Assert.Fail("Read-only observation attempted a mutation.")));
                foreach (var reader in readers)
                    reader.SetValue(null, originals[reader]);
                var ping = Call("ping");
                Assert.IsTrue((bool)ping["success"]);
                var observations = (JObject)ping["data"]["observations"];
                Assert.AreEqual("editor_global", (string)observations["scope"]);
                Assert.AreEqual("none", (string)observations["completion_effect"]);
                Assert.AreEqual("observed", (string)observations["installed_snapshot"]["status"]);
                Assert.GreaterOrEqual((int)observations["installed_snapshot"]["count"], 1);
                Assert.IsTrue(System.Text.RegularExpressions.Regex.IsMatch((string)observations["installed_snapshot"]["digest"], @"\A[0-9a-f]{64}\z"));
                Assert.AreEqual(JTokenType.Boolean, observations["readiness"]["is_compiling"].Type);
                Assert.AreEqual(JTokenType.Boolean, observations["readiness"]["is_updating"].Type);
                Assert.IsTrue(new[] { "idle", "blocked" }.Contains((string)observations["readiness"]["status"]));
                Assert.AreEqual(0, invocations);
                Assert.AreEqual(0, jobs.Count);
                Assert.AreEqual("", session);
                Assert.AreSame(sessionReader, typeof(PackageJobManager).GetField("SessionReader", Hidden).GetValue(null));
                Assert.AreSame(sessionWriter, typeof(PackageJobManager).GetField("SessionWriter", Hidden).GetValue(null));
                TestContext.WriteLine("DEV05 actual read-only observation: " + observations.ToString(Newtonsoft.Json.Formatting.None));
            }
            finally
            {
                for (int i = 0; i < readers.Length; i++)
                    readers[i].SetValue(null, fakes[i]);
                invoker.SetValue(null, fakeMutation);
            }
        }
    }
}
