using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net.WebSockets;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources.Editor;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport.Transports;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    [Parallelizable(ParallelScope.None)]
    public class ToolSettingsWireTests
    {
        [TestCase(null)]
        [TestCase("")]
        [TestCase("  ")]
        [TestCase("core")]
        [TestCase("custom")]
        [TestCase(" Custom ")]
        public void ActualBuilderAndResource_UseSameIdentityAndRetainRichSchema(string group)
        {
            FieldInfo discoveryField = typeof(MCPServiceLocator).GetField("_toolDiscoveryService", BindingFlags.Static | BindingFlags.NonPublic);
            FieldInfo stateOverride = typeof(ToolStates).GetField("_settingsStateOverride", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(stateOverride, "The resource must support a task-owned state seam without changing preferences.");
            object previousDiscovery = discoveryField.GetValue(null);
            object previousState = stateOverride.GetValue(null);
            var discovery = new OwnedDiscovery();
            discovery.Items[0].Group = group;
            var state = new ToolSettingsState();
            discoveryField.SetValue(null, discovery);
            stateOverride.SetValue(null, state);
            try
            {
                var builder = typeof(WebSocketTransportClient).GetMethod("BuildToolsRegistration", BindingFlags.Static | BindingFlags.NonPublic);
                var built = (JObject)builder.Invoke(null, new object[] { discovery.Items });
                var sent = state.Capture((JArray)built["tools"], new JObject { ["owned_probe"] = true });
                var response = ToolStates.HandleCommand(null) as SuccessResponse;
                Assert.IsNotNull(response);
                var data = (JObject)response.Data;
                Assert.AreEqual(sent.Epoch, (string)data["settings_epoch"]);
                Assert.AreEqual(sent.Revision, (long)data["desired_revision"]);
                Assert.IsTrue((bool)data["tools"][0]["enabled"]);
                Assert.AreEqual(group ?? "core", (string)data["tools"][0]["group"], "The public resource group is unchanged.");
                Assert.AreEqual(string.IsNullOrWhiteSpace(group) ? "core" : group, (string)sent.Tools[0]["group"]);
                Assert.IsNotNull(data["tools"][0]["is_built_in"], "Public resource rows retain their established metadata.");
                Assert.IsTrue(JToken.DeepEquals(built["tools"][0]["input_schema"], data["tools"][0]["input_schema"]));
                discovery.Items[0].InputSchema["properties"]["payload"]["properties"]["value"]["type"] = "string";
                var changed = (JObject)((SuccessResponse)ToolStates.HandleCommand(null)).Data;
                Assert.AreEqual(sent.Revision + 1, (long)changed["desired_revision"]);
            }
            finally
            {
                discoveryField.SetValue(null, previousDiscovery);
                stateOverride.SetValue(null, previousState);
            }
        }

        [TestCase(true)]
        [TestCase(false)]
        public void ActualReceiptParser_RequiresTheCurrentConnectionToken(bool current)
        {
            using var client = new WebSocketTransportClient();
            using var owner = new CancellationTokenSource();
            using var obsolete = new CancellationTokenSource();
            var state = new ToolSettingsTestDriver();
            state.Owner = owner;
            object snapshot = state.Capture();
            state.Connect();
            state.Call("BeginAttempt", owner);
            Set(client, "_connectionCts", owner);
            Set(client, "_toolSettingsState", state.Value);
            Set(client, "_toolSettingsNegotiated", true);
            var parser = typeof(WebSocketTransportClient).GetMethod("HandleMessageAsync", BindingFlags.Instance | BindingFlags.NonPublic);
            Task parsed = (Task)parser.Invoke(client, new object[] { state.Receipt(snapshot).ToString(), current ? owner.Token : obsolete.Token });
            Assert.IsTrue(parsed.IsCompleted && !parsed.IsFaulted);
            Assert.AreEqual(current ? "applied" : "sending", state.Status<string>("Stage"));
        }

        [UnityTest]
        public IEnumerator RegisteredCapture_SeparatesConnectionIdentityFromLinkedWorkCancellation()
        {
            using var owner = new CancellationTokenSource();
            using var client = CreateRegisteredClient(owner);
            Task registration = (Task)
                typeof(WebSocketTransportClient).GetField("_registrationTask", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(client);
            Assert.IsNotNull(registration);
            yield return WaitForTask(registration);
            Assert.AreEqual(1, client.Status.DesiredRevision, "The real registered coordinator must reach capture with its distinct linked work token.");
            Assert.IsNull(client.Status.ConfirmedRevision, "This socket-free test does not establish a server receipt.");

            var capture = RegistrationCallback<Func<CancellationToken, Task<ToolSettingsSnapshot>>>(client, "_capture");
            using var work = CancellationTokenSource.CreateLinkedTokenSource(owner.Token);
            Assert.AreNotEqual(owner.Token, work.Token);
            work.Cancel();
            Task cancelled = capture(work.Token);
            yield return WaitForTask(cancelled);
            Assert.IsTrue(cancelled.IsCanceled, "Work cancellation must cancel queued capture without cancelling its connection owner.");
            Assert.IsFalse(owner.IsCancellationRequested);

            using var replacement = new CancellationTokenSource();
            using var liveWork = CancellationTokenSource.CreateLinkedTokenSource(owner.Token);
            Set(client, "_connectionCts", replacement);
            Task obsolete = capture(liveWork.Token);
            yield return WaitForTask(obsolete);
            Assert.IsTrue(obsolete.IsCanceled || obsolete.IsFaulted && obsolete.Exception.GetBaseException() is OperationCanceledException);
            Assert.AreEqual(1, client.Status.DesiredRevision, "An obsolete connection callback cannot capture a replacement's desired state.");
        }

        [UnityTest]
        public IEnumerator RegisteredSend_SeparatesConnectionIdentityFromLinkedWorkCancellation()
        {
            using var owner = new CancellationTokenSource();
            using var client = CreateRegisteredClient(owner);
            var send = RegistrationCallback<Func<JObject, CancellationToken, Task>>(client, "_send");
            using var work = CancellationTokenSource.CreateLinkedTokenSource(owner.Token);
            Assert.AreNotEqual(owner.Token, work.Token);
            var frame = new JObject { ["type"] = "register_tools", ["tools"] = new JArray() };
            Task missingSocket = send(frame, work.Token);
            yield return WaitForTask(missingSocket);
            Assert.IsTrue(missingSocket.IsFaulted, "The live owner must pass identity validation and reach the deliberate missing-socket refusal.");
            Assert.IsInstanceOf<InvalidOperationException>(missingSocket.Exception.GetBaseException());

            using var unopenedSocket = new ClientWebSocket();
            Set(client, "_socket", unopenedSocket);
            work.Cancel();
            Task cancelled = send(frame, work.Token);
            yield return WaitForTask(cancelled);
            Assert.IsTrue(cancelled.IsCanceled, "The work token must still cancel before attempting a frame send.");
            Assert.IsFalse(owner.IsCancellationRequested);

            using var replacement = new CancellationTokenSource();
            using var liveWork = CancellationTokenSource.CreateLinkedTokenSource(owner.Token);
            Set(client, "_connectionCts", replacement);
            Task obsolete = send(frame, liveWork.Token);
            yield return WaitForTask(obsolete);
            Assert.IsTrue(obsolete.IsCanceled || obsolete.IsFaulted && obsolete.Exception.GetBaseException() is OperationCanceledException);
            Assert.IsNull(client.Status.ConfirmedRevision);
        }

        private static WebSocketTransportClient CreateRegisteredClient(CancellationTokenSource owner)
        {
            var endpoint = new Uri("ws://127.0.0.1:1/hub/plugin");
            var options = new WebSocketTransportClient.OwnedConnectionOptions(endpoint, "owned-synthetic-token", "owned", "owned", "/owned", "6000.0");
            var client = new WebSocketTransportClient(new OwnedDiscovery(), options);
            try
            {
                Set(client, "_endpointUri", endpoint);
                Set(client, "_connectionCts", owner);
                var registered = typeof(WebSocketTransportClient).GetMethod("HandleRegisteredAsync", BindingFlags.Instance | BindingFlags.NonPublic);
                Task handled = (Task)
                    registered.Invoke(
                        client,
                        new object[]
                        {
                            new JObject { ["session_id"] = "owned-session", ["capabilities"] = new JArray(ToolSettingsState.Capability) },
                            owner.Token,
                        }
                    );
                Assert.IsTrue(handled.IsCompleted && !handled.IsFaulted);
                return client;
            }
            catch
            {
                client.Dispose();
                throw;
            }
        }

        private static T RegistrationCallback<T>(WebSocketTransportClient client, string field)
        {
            object helper = typeof(WebSocketTransportClient)
                .GetField("_toolSettingsRegistration", BindingFlags.Instance | BindingFlags.NonPublic)
                .GetValue(client);
            Assert.IsNotNull(helper);
            return (T)helper.GetType().GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(helper);
        }

        private static IEnumerator WaitForTask(Task task)
        {
            var elapsed = Stopwatch.StartNew();
            while (!task.IsCompleted && elapsed.Elapsed < TimeSpan.FromSeconds(5))
            {
                EditorApplication.QueuePlayerLoopUpdate();
                yield return null;
            }
            Assert.IsTrue(task.IsCompleted, "The owned callback must settle within the existing bounded native fixture wait.");
        }

        [TestCase("state")]
        [TestCase("websocket")]
        [TestCase("resource")]
        public void CaptureFailure_StatusEventsNeverExposeSensitiveExceptionMessages(string source)
        {
            var state = new ToolSettingsState();
            var messages = new List<string>();
            state.StatusChanged += () => messages.Add(state.Status.Message ?? "");
            if (source == "state")
                Assert.Throws<InvalidOperationException>(() => state.Capture(new ThrowingArray(), new JObject()));
            else if (source == "websocket")
            {
                using var owner = new CancellationTokenSource();
                using var client = new WebSocketTransportClient(new OwnedDiscovery { CaptureFailure = true });
                Set(client, "_connectionCts", owner);
                Set(client, "_toolSettingsState", state);
                state.SetConnection(owner, "owned-session", true, null);
                var capture = typeof(WebSocketTransportClient).GetMethod(
                    "CaptureToolSettingsOnMainThreadAsync",
                    BindingFlags.Instance | BindingFlags.NonPublic
                );
                var task = (Task)capture.Invoke(client, new object[] { owner.Token });
                Assert.IsTrue(task.IsCompleted && task.IsFaulted, "The actual main-thread capture must reach the controlled discovery failure.");
                Assert.IsInstanceOf<InvalidOperationException>(task.Exception.GetBaseException());
            }
            else
            {
                FieldInfo discovery = typeof(MCPServiceLocator).GetField("_toolDiscoveryService", BindingFlags.Static | BindingFlags.NonPublic);
                FieldInfo stateOverride = typeof(ToolStates).GetField("_settingsStateOverride", BindingFlags.Static | BindingFlags.NonPublic);
                object previousDiscovery = discovery.GetValue(null),
                    previousState = stateOverride.GetValue(null);
                try
                {
                    discovery.SetValue(null, new OwnedDiscovery { CaptureFailure = true });
                    stateOverride.SetValue(null, state);
                    Assert.IsInstanceOf<ErrorResponse>(ToolStates.HandleCommand(null));
                }
                finally
                {
                    discovery.SetValue(null, previousDiscovery);
                    stateOverride.SetValue(null, previousState);
                }
            }
            Assert.IsNotEmpty(messages, "Assert every synchronous intermediate event, not only the final state.");
            foreach (string message in messages)
                StringAssert.DoesNotContain("owned-sensitive-marker", message);
            Assert.AreEqual("preparation_failed", state.Status.Stage);
            StringAssert.Contains(nameof(InvalidOperationException), state.Status.Message);
        }

        private sealed class ThrowingArray : JArray
        {
            public override void WriteTo(JsonWriter writer, params JsonConverter[] converters) => throw new InvalidOperationException("owned-sensitive-marker");
        }

        private static void Set(object target, string name, object value)
        {
            var field = target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(field, "Missing native receipt field " + name);
            field.SetValue(target, value);
        }

        private sealed class OwnedDiscovery : IToolDiscoveryService
        {
            internal bool CaptureFailure;
            internal readonly List<ToolMetadata> Items = new()
            {
                new ToolMetadata
                {
                    Name = "owned_probe",
                    Description = "owned",
                    StructuredOutput = true,
                    IsBuiltIn = false,
                    SchemaVersion = 1,
                    InputSchema = JObject.Parse(
                        "{\"type\":\"object\",\"properties\":{\"payload\":{\"type\":\"object\",\"properties\":{\"value\":{\"type\":\"integer\"}}}}}"
                    ),
                },
            };

            public List<ToolMetadata> DiscoverAllTools() => CaptureFailure ? throw new InvalidOperationException("owned-sensitive-marker") : Items;

            public ToolMetadata GetToolMetadata(string name) => Items[0];

            public List<ToolMetadata> GetEnabledTools() => Items;

            public bool IsToolEnabled(string name) => true;

            public void SetToolEnabled(string name, bool enabled) => Assert.Fail("Preferences are not part of this wire fixture.");

            public void InvalidateCache() { }
        }
    }

    [Explicit("Requires the reviewed disposable-project receipt proof server and isolated Editor launcher.")]
    [Parallelizable(ParallelScope.None)]
    public class ToolSettingsActualReceiptProbeTests
    {
        [UnityTest]
        public IEnumerator ActualOwnedWebSocket_ReceiptsAndPublicSdkReadbackSurviveReregistration()
        {
            string project = Path.GetFullPath(Path.Combine(UnityEngine.Application.dataPath, ".."));
            string expected = Environment.GetEnvironmentVariable("DEV21_ACK_PROBE_PROJECT");
            Assert.IsFalse(string.IsNullOrWhiteSpace(expected));
            Assert.AreEqual(Path.GetFullPath(expected), project, "This probe requires the exact fresh owned project.");
            Assert.IsTrue(UnityEngine.Application.isBatchMode);
            Assert.IsTrue(string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("UNITY_MCP_ALLOW_BATCH")));
            string endpoint = Environment.GetEnvironmentVariable("DEV21_ACK_ENDPOINT");
            string token = Environment.GetEnvironmentVariable("DEV21_ACK_TOKEN");
            Assert.IsFalse(string.IsNullOrWhiteSpace(token), "A disposable local proof token is required.");
            var uri = new Uri(endpoint);
            Assert.IsTrue(uri.IsLoopback);
            string folder = Path.Combine(project, "Library", "Dev21ReceiptProof");
            Directory.CreateDirectory(folder);
            var discovery = new ProbeDiscovery();
            var options = new WebSocketTransportClient.OwnedConnectionOptions(
                uri,
                token,
                "DEV21 receipt proof",
                "dev21-owned-proof",
                project,
                UnityEngine.Application.unityVersion
            );
            using var client = new WebSocketTransportClient(discovery, options);
            try
            {
                Task<bool> start = client.StartAsync();
                yield return Wait(() => start.IsCompleted, "initial_start_completed", client, start);
                Assert.IsFalse(start.IsFaulted, "Owned connection failed before registration.");
                Assert.IsTrue(start.Result);
                var source = client as IToolSettingsStatusSource;
                Assert.IsNotNull(source);
                yield return Wait(() => source.Status.Stage == "applied", "initial_receipt_applied", client, start);
                string originalSession = source.Status.SessionId;
                WritePhase(folder, 1, source.Status, "first_value");
                yield return Wait(() => File.Exists(Path.Combine(folder, "readback-1.json")), "initial_sdk_readback", client);

                discovery.Parameter = "second_value";
                Task changed = client.ReregisterToolsAsync();
                yield return Wait(
                    () => changed.IsCompleted && source.Status.Stage == "applied" && source.Status.DesiredRevision > 1,
                    "changed_receipt_applied",
                    client,
                    changed
                );
                WritePhase(folder, 2, source.Status, "second_value");
                yield return Wait(() => File.Exists(Path.Combine(folder, "readback-2.json")), "changed_sdk_readback", client);

                Task stop = client.StopAsync();
                yield return Wait(() => stop.IsCompleted, "stop_completed", client, stop);
                Task<bool> reconnect = client.StartAsync();
                yield return Wait(() => reconnect.IsCompleted && source.Status.Stage == "applied", "reconnected_receipt_applied", client, reconnect);
                Assert.IsFalse(reconnect.IsFaulted);
                Assert.IsTrue(reconnect.Result);
                Assert.AreNotEqual(originalSession, source.Status.SessionId);
                WritePhase(folder, 3, source.Status, "second_value");
                yield return Wait(() => File.Exists(Path.Combine(folder, "readback-3.json")), "reconnected_sdk_readback", client);
                TestContext.WriteLine(
                    "DEV21_ACTUAL_RECEIPTS "
                        + new JObject
                        {
                            ["native_websocket"] = true,
                            ["production_hub"] = true,
                            ["owned_fake_discovery"] = true,
                            ["sdk_readback_phases"] = 3,
                            ["native_command_execution"] = false,
                        }
                );
            }
            finally
            {
                client.ForceStop();
                File.WriteAllText(Path.Combine(folder, "done.json"), "{\"closed\":true}");
            }
        }

        private static IEnumerator Wait(Func<bool> condition, string phase, WebSocketTransportClient client, Task task = null)
        {
            var elapsed = Stopwatch.StartNew();
            while (!condition() && elapsed.Elapsed < TimeSpan.FromSeconds(40))
            {
                EditorApplication.QueuePlayerLoopUpdate();
                yield return null;
            }
            bool settled = condition();
            ToolSettingsStatus status = client.Status;
            TestContext.WriteLine(
                "DEV21_PREREQUISITE "
                    + new JObject
                    {
                        ["phase"] = phase,
                        ["settled"] = settled,
                        ["elapsed_ms"] = elapsed.ElapsedMilliseconds,
                        ["task_status"] = task?.Status.ToString(),
                        ["fault_type"] = task?.Exception?.GetBaseException().GetType().Name,
                        ["transport_connected"] = client.IsConnected,
                        ["settings_stage"] = status.Stage,
                        ["settings_reason"] = status.Reason,
                        ["capture_failure_type"] = status.Reason == "capture_failed" ? CaptureFailureType(status.Message) : null,
                        ["session_present"] = !string.IsNullOrEmpty(status.SessionId),
                        ["desired_revision"] = status.DesiredRevision,
                        ["confirmed_revision"] = status.ConfirmedRevision,
                        ["server_catalog_revision"] = status.ServerCatalogRevision,
                        ["registration_task"] = InternalTaskState(client, "_registrationTask"),
                        ["receive_task"] = InternalTaskState(client, "_receiveTask"),
                        ["keepalive_task"] = InternalTaskState(client, "_keepAliveTask"),
                    }
            );
            Assert.IsTrue(settled, "The owned receipt/readback prerequisite did not settle within forty seconds: " + phase);
        }

        private static string InternalTaskState(WebSocketTransportClient client, string field) =>
            (typeof(WebSocketTransportClient).GetField(field, BindingFlags.Instance | BindingFlags.NonPublic)?.GetValue(client) as Task)?.Status.ToString();

        private static string CaptureFailureType(string message)
        {
            const string prefix = "Catalog preparation failed (";
            if (message == null || !message.StartsWith(prefix, StringComparison.Ordinal) || !message.EndsWith(").", StringComparison.Ordinal))
                return "other";
            string type = message.Substring(prefix.Length, message.Length - prefix.Length - 2);
            if (type.Length == 0 || type.Length > 128)
                return "other";
            foreach (char value in type)
                if (!(value >= 'A' && value <= 'Z' || value >= 'a' && value <= 'z' || value >= '0' && value <= '9' || value == '_' || value == '`'))
                    return "other";
            return type;
        }

        private static void WritePhase(string folder, int phase, ToolSettingsStatus status, string parameter)
        {
            var record = new JObject
            {
                ["phase"] = phase,
                ["status"] = status.Stage,
                ["session_id"] = status.SessionId,
                ["settings_epoch"] = status.Epoch,
                ["desired_revision"] = status.DesiredRevision,
                ["catalog_revision"] = status.ServerCatalogRevision,
                ["tool_name"] = "dev21_native_catalog_probe",
                ["parameter"] = parameter,
            };
            File.WriteAllText(Path.Combine(folder, "phase-" + phase + ".json"), record.ToString());
        }

        private sealed class ProbeDiscovery : IToolDiscoveryService
        {
            internal string Parameter = "first_value";

            public List<ToolMetadata> DiscoverAllTools() =>
                new()
                {
                    new ToolMetadata
                    {
                        Name = "dev21_native_catalog_probe",
                        Description = "Owned catalog-only proof; never invoked.",
                        StructuredOutput = true,
                        IsBuiltIn = false,
                        Parameters = new List<ParameterMetadata>
                        {
                            new()
                            {
                                Name = Parameter,
                                Type = "string",
                                Required = true,
                            },
                        },
                    },
                };

            public List<ToolMetadata> GetEnabledTools() => DiscoverAllTools();

            public ToolMetadata GetToolMetadata(string name) => DiscoverAllTools()[0];

            public bool IsToolEnabled(string name) => true;

            public void SetToolEnabled(string name, bool enabled) => Assert.Fail("No preferences may be written by the owned catalog probe.");

            public void InvalidateCache() { }
        }
    }
}
