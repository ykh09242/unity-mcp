using System;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class ToolSettingsStateTests
    {
        [Test]
        public void Capture_IsImmutableAndIdenticalContentDoesNotAdvanceRevision()
        {
            var state = new ToolSettingsTestDriver();
            var tools = ToolSettingsTestDriver.Tools();
            object first = state.Capture(tools);
            tools[0]["description"] = "mutated caller";
            Assert.AreEqual("original", ToolSettingsTestDriver.Get<JArray>(first, "Tools")[0]["description"].Value<string>());
            var exposed = ToolSettingsTestDriver.Get<JArray>(first, "Tools");
            exposed.Clear();
            Assert.AreEqual(1, ToolSettingsTestDriver.Get<JArray>(first, "Tools").Count);
            object identical = state.Capture();
            Assert.AreEqual(ToolSettingsTestDriver.Get<long>(first, "Revision"), ToolSettingsTestDriver.Get<long>(identical, "Revision"));
            Assert.That(ToolSettingsTestDriver.Get<string>(first, "Epoch"), Does.Match("^[0-9a-f]{32}$"));
        }

        [Test]
        public void ResourceOnlyStateFields_DoNotChangeTheEnabledWireIdentity()
        {
            var state = new ToolSettingsTestDriver();
            object wire = state.Capture();
            JArray resource = ToolSettingsTestDriver.Tools();
            resource[0]["enabled"] = true;
            resource[0]["auto_register"] = false;
            resource[0]["is_built_in"] = false;
            object readback = state.Capture(resource);
            Assert.AreEqual(ToolSettingsTestDriver.Get<long>(wire, "Revision"), ToolSettingsTestDriver.Get<long>(readback, "Revision"));
            Assert.IsNull(ToolSettingsTestDriver.Get<JArray>(readback, "Tools")[0]["enabled"]);
        }

        [Test]
        public void Capture_DescriptorOrEnablementChangeAdvancesDesiredRevision()
        {
            var state = new ToolSettingsTestDriver();
            object first = state.Capture();
            object second = state.Capture(ToolSettingsTestDriver.Tools("changed"));
            object third = state.Capture(ToolSettingsTestDriver.Tools("changed"), new JObject { ["owned_probe"] = false });
            Assert.AreEqual(ToolSettingsTestDriver.Get<long>(first, "Revision") + 1, ToolSettingsTestDriver.Get<long>(second, "Revision"));
            Assert.AreEqual(ToolSettingsTestDriver.Get<long>(second, "Revision") + 1, ToolSettingsTestDriver.Get<long>(third, "Revision"));
        }

        [TestCase(257, false)]
        [TestCase(1, true)]
        public void Capture_OverLimitCatalogPreservesPreviousDesired(int count, bool oversized)
        {
            var state = new ToolSettingsTestDriver();
            object prior = state.Capture();
            var tools = new JArray();
            for (int i = 0; i < count; i++)
                tools.Add(new JObject { ["name"] = "probe_" + i, ["description"] = oversized ? new string('x', 512 * 1024) : "small" });
            Assert.Throws<TargetInvocationException>(() => state.Capture(tools));
            Assert.AreEqual(ToolSettingsTestDriver.Get<long>(prior, "Revision"), state.Status<long>("DesiredRevision"));
            Assert.AreEqual("preparation_failed", state.Status<string>("Stage"));
        }

        [Test]
        public void BeginAttempt_CoalescesNewestDesiredAndSendAloneDoesNotApply()
        {
            var state = new ToolSettingsTestDriver();
            state.Capture();
            state.Connect();
            object first = state.Call("BeginAttempt", state.Owner);
            state.Call("MarkSent", state.Owner, first);
            Assert.AreEqual("awaiting_ack", state.Status<string>("Stage"));
            Assert.IsNull(state.Status<object>("ConfirmedRevision"));
            object newest = state.Capture(ToolSettingsTestDriver.Tools("newest"));
            Assert.IsNull(state.Call("BeginAttempt", state.Owner));
            Assert.IsTrue((bool)state.Call("AcceptReceipt", state.Owner, state.Receipt(first)));
            Assert.AreEqual("pending", state.Status<string>("Stage"));
            object next = state.Call("BeginAttempt", state.Owner);
            Assert.AreEqual(ToolSettingsTestDriver.Get<long>(newest, "Revision"), ToolSettingsTestDriver.Get<long>(next, "Revision"));
        }

        [TestCase("applied")]
        [TestCase("rejected")]
        [TestCase("unconfirmed")]
        public void LateR1Success_DoesNotRegressR2StageOrConfirmation(string latestStage)
        {
            var state = new ToolSettingsTestDriver();
            object first = state.Capture();
            state.Connect();
            state.Call("BeginAttempt", state.Owner);
            state.Call("AcceptReceipt", state.Owner, state.Receipt(first));
            object second = state.Capture(ToolSettingsTestDriver.Tools("R2"));
            state.Call("BeginAttempt", state.Owner);
            if (latestStage == "unconfirmed")
                state.Call("FailAttempt", state.Owner, second, "unconfirmed", "ack_timeout", "No receipt");
            else
                state.Call("AcceptReceipt", state.Owner, state.Receipt(second, latestStage, 2));
            object confirmed = state.Status<object>("ConfirmedRevision");
            state.Call("AcceptReceipt", state.Owner, state.Receipt(first));
            Assert.AreEqual(latestStage, state.Status<string>("Stage"));
            Assert.AreEqual(confirmed, state.Status<object>("ConfirmedRevision"));
            Assert.AreEqual(ToolSettingsTestDriver.Get<long>(second, "Revision"), state.Status<long>("DesiredRevision"));
        }

        [TestCase("session_id", "other-session")]
        [TestCase("settings_epoch", "00000000000000000000000000000000")]
        [TestCase("status", "invented")]
        [TestCase("desired_revision", "1")]
        public void MalformedOrUnownedReceipt_DoesNotApply(string field, string value)
        {
            var state = new ToolSettingsTestDriver();
            object snapshot = state.Capture();
            state.Connect();
            state.Call("BeginAttempt", state.Owner);
            JObject receipt = state.Receipt(snapshot);
            receipt[field] = value;
            Assert.IsFalse((bool)state.Call("AcceptReceipt", state.Owner, receipt));
            Assert.IsNull(state.Status<object>("ConfirmedRevision"));
            Assert.AreEqual("sending", state.Status<string>("Stage"));
        }

        [Test]
        public void Reconnect_IgnoresOldOwnerAndRequiresFreshReceipt()
        {
            var state = new ToolSettingsTestDriver();
            object snapshot = state.Capture();
            state.Connect();
            state.Call("BeginAttempt", state.Owner);
            state.Call("AcceptReceipt", state.Owner, state.Receipt(snapshot));
            object oldOwner = state.Owner;
            state.Owner = new object();
            state.Call("SetConnection", state.Owner, "replacement", true, null);
            Assert.IsNull(state.Status<object>("ConfirmedRevision"));
            Assert.IsFalse((bool)state.Call("AcceptReceipt", oldOwner, state.Receipt(snapshot)));
            state.Call("Disconnect", oldOwner);
            Assert.IsTrue(state.Status<bool>("IsConnected"));
            state.Call("BeginAttempt", state.Owner);
            JObject fresh = state.Receipt(snapshot);
            fresh["session_id"] = "replacement";
            Assert.IsTrue((bool)state.Call("AcceptReceipt", state.Owner, fresh));
            Assert.AreEqual("applied", state.Status<string>("Stage"));
        }

        [Test]
        public void UnsupportedPeerAndDisconnect_NeverConfirmSavedPreferences()
        {
            var state = new ToolSettingsTestDriver();
            state.Capture();
            state.Connect(false);
            Assert.AreEqual("unconfirmed", state.Status<string>("Stage"));
            Assert.IsNull(state.Call("BeginAttempt", state.Owner));
            state.Call("Disconnect", state.Owner);
            Assert.AreEqual("disconnected", state.Status<string>("Stage"));
            Assert.IsNull(state.Status<object>("ConfirmedRevision"));
        }

        [Test]
        public void Retry_CoalescesClicksAndDoesNotChangeSavedRevision()
        {
            var state = new ToolSettingsTestDriver();
            object saved = state.Capture();
            int calls = 0;
            var pending = new TaskCompletionSource<bool>();
            state.Call(
                "SetConnection",
                state.Owner,
                "session",
                true,
                (Func<Task>)(
                    () =>
                    {
                        calls++;
                        return pending.Task;
                    }
                )
            );
            Task first = (Task)state.Call("RetryToolSettingsAsync");
            Task second = (Task)state.Call("RetryToolSettingsAsync");
            Assert.AreSame(first, second);
            Assert.AreEqual(1, calls);
            Assert.AreEqual(ToolSettingsTestDriver.Get<long>(saved, "Revision"), state.Status<long>("DesiredRevision"));
            pending.SetResult(true);
        }

        [Test]
        public void RevisionOverflow_RotatesEpochAndClearsAppliedEvidence()
        {
            var state = new ToolSettingsTestDriver();
            object first = state.Capture();
            state.Value.GetType().GetField("_revision", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(state.Value, 9007199254740991L);
            object next = state.Capture(ToolSettingsTestDriver.Tools("changed"));
            Assert.AreEqual(1, ToolSettingsTestDriver.Get<long>(next, "Revision"));
            Assert.AreNotEqual(ToolSettingsTestDriver.Get<string>(first, "Epoch"), ToolSettingsTestDriver.Get<string>(next, "Epoch"));
            Assert.IsNull(state.Status<object>("ConfirmedRevision"));
        }
    }

    internal sealed class ToolSettingsTestDriver
    {
        internal readonly object Value;
        internal object Owner = new object();

        internal ToolSettingsTestDriver()
        {
            Type type = typeof(ToolMetadata).Assembly.GetType("MCPForUnity.Editor.Services.ToolSettingsState");
            Assert.IsNotNull(type, "The bounded desired/applied state facility must exist.");
            Value = Activator.CreateInstance(type);
        }

        internal object Call(string name, params object[] args) => Value.GetType().GetMethod(name).Invoke(Value, args);

        internal object Capture(JArray tools = null, JObject enabled = null) =>
            Call("Capture", tools ?? Tools(), enabled ?? new JObject { ["owned_probe"] = true });

        internal void Connect(bool supported = true) => Call("SetConnection", Owner, "session", supported, null);

        internal T Status<T>(string name) => Get<T>(Get<object>(Value, "Status"), name);

        internal static T Get<T>(object value, string name) => (T)value.GetType().GetProperty(name).GetValue(value);

        internal static JArray Tools(string description = "original") => new JArray(new JObject { ["name"] = "owned_probe", ["description"] = description });

        internal JObject Receipt(object snapshot, string status = "applied", long catalogRevision = 1) =>
            new JObject
            {
                ["type"] = "tool_settings_result",
                ["session_id"] = "session",
                ["settings_epoch"] = Get<string>(snapshot, "Epoch"),
                ["desired_revision"] = Get<long>(snapshot, "Revision"),
                ["status"] = status,
                ["catalog_revision"] = catalogRevision,
                ["enabled_tool_count"] = 1,
                ["reason"] = status == "rejected" ? "invalid_catalog" : null,
            };
    }
}
