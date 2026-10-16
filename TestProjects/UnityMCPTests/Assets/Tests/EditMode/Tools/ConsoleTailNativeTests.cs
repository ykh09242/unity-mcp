using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    // Sole-QA disposable project only: emits synthetic logs; saves/restores UI and services.
    [Parallelizable(ParallelScope.None)]
    public class ConsoleTailNativeTests
    {
        private const BindingFlags Flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
        private static Type Entries => typeof(EditorApplication).Assembly.GetType("UnityEditor.LogEntries");
        private static FieldInfo ToolService => typeof(MCPServiceLocator).GetField("_toolDiscoveryService", Flags);
        private static FieldInfo ResourceService => typeof(MCPServiceLocator).GetField("_resourceDiscoveryService", Flags);

        private sealed class Policy : IToolDiscoveryService, IResourceDiscoveryService
        {
            internal bool Parent = true;
            internal bool Alias = true;

            public bool IsToolEnabled(string name) => name == "read_console" ? Parent : Alias;

            public ToolMetadata GetToolMetadata(string name) => new ToolMetadata { Name = name, AutoRegister = false };

            public List<ToolMetadata> DiscoverAllTools() => new List<ToolMetadata>();

            public List<ToolMetadata> GetEnabledTools() => new List<ToolMetadata>();

            public void SetToolEnabled(string name, bool enabled) => throw new NotSupportedException();

            public void InvalidateCache() { }

            public ResourceMetadata GetResourceMetadata(string name) => null;

            public bool IsResourceEnabled(string name) => true;

            public List<ResourceMetadata> DiscoverAllResources() => new List<ResourceMetadata>();

            public List<ResourceMetadata> GetEnabledResources() => new List<ResourceMetadata>();

            public void SetResourceEnabled(string name, bool enabled) => throw new NotSupportedException();
        }

        private static JObject Request(string token, string marker = "") =>
            new JObject
            {
                ["tail_cursor"] = token,
                ["filterText"] = marker,
                ["types"] = new JArray("all"),
                ["includeMcpLogs"] = true,
            };

        private static JObject Tail(ConsoleTailCursor state, string token, string marker)
        {
            using var snapshot = ReadConsole.OpenTailSnapshot();
            return JObject.FromObject(state.Read(ConsoleTailQuery.Parse(Request(token, marker)), snapshot));
        }

        [TestCase(false, true)]
        [TestCase(true, false)]
        public void ActualRegistryAndTransportCannotBypassDisabledParentOrAlias(bool parent, bool alias)
        {
            // Given isolated policy services; malformed input would fail differently after policy.
            object previousTools = ToolService.GetValue(null);
            object previousResources = ResourceService.GetValue(null);
            var policy = new Policy { Parent = parent, Alias = alias };
            var dispatcher = typeof(TransportCommandDispatcher);
            var thread = dispatcher.GetField("_mainThreadId", Flags);
            object previousThread = thread.GetValue(null);
            MCPServiceLocator.Register<IToolDiscoveryService>(policy);
            MCPServiceLocator.Register<IResourceDiscoveryService>(policy);
            thread.SetValue(null, Thread.CurrentThread.ManagedThreadId);
            try
            {
                CommandRegistry.Initialize();
                // When the actual registered handler and transport are called.
                JObject direct = JObject.FromObject(CommandRegistry.GetHandler("tail_console")(new JObject()));
                var pending = TransportCommandDispatcher.ExecuteCommandJsonAsync("{\"type\":\"tail_console\",\"params\":{}}", CancellationToken.None);
                Assert.IsTrue(pending.IsCompleted, "The main-thread blocked policy response is synchronous.");
                JObject wire = JObject.Parse(pending.GetAwaiter().GetResult());
                // Then parent/alias disablement wins before parsing or native console reads.
                Assert.IsFalse((bool)direct["success"]);
                Assert.AreEqual("tool_disabled", (string)direct["data"]["reason"]);
                StringAssert.Contains("disabled", wire.ToString().ToLowerInvariant());
                Assert.IsFalse(typeof(TailConsole).GetCustomAttribute<McpForUnityToolAttribute>().AutoRegister);
            }
            finally
            {
                thread.SetValue(null, previousThread);
                ToolService.SetValue(null, previousTools);
                ResourceService.SetValue(null, previousResources);
            }
        }

        [TestCase(2000)]
        [TestCase(20000)]
        public void ActualNativeThreeAppendsHaveConstantFiveRowReadsAndNoDuplicateOrOmission(int prefix)
        {
            // Given a synthetic prefix in the disposable console and Collapse disabled for baseline.
            string marker = "Play04_" + Guid.NewGuid().ToString("N");
            PropertyInfo flags = Entries.GetProperty("consoleFlags", Flags);
            int savedFlags = (int)flags.GetValue(null);
            flags.SetValue(null, savedFlags & ~1);
            try
            {
                for (int i = 0; i < prefix; i++)
                    Debug.Log(marker + " prefix " + i);
                var state = new ConsoleTailCursor();
                JObject begin = Tail(state, "begin", marker);
                string token = (string)begin["data"]["next_cursor"];
                // When exactly three new synthetic rows are appended and consumed.
                for (int i = 0; i < 3; i++)
                    Debug.Log(marker + " new " + i);
                JObject page = Tail(state, token, marker);
                // Then actual native entry calls, not formatted-item counts, stay constant.
                Assert.IsTrue((bool)page["success"], page.ToString());
                Assert.AreEqual(5, (int)page["data"]["native_entry_reads"]);
                var items = (JArray)page["data"]["items"];
                Assert.AreEqual(3, items.Count);
                for (int i = 0; i < 3; i++)
                    StringAssert.Contains(marker + " new " + i, (string)items[i]["entry"]["message"]);
                Assert.AreEqual(3, items.Select(t => (long)t["sequence"]).Distinct().Count());
                var metrics = new ConsoleReadMetrics();
                JObject legacy = JObject.FromObject(
                    ReadConsole.GetConsoleEntries(new List<string> { "error", "warning", "log" }, null, 3, prefix, marker, "json", false, null, true, metrics)
                );
                Assert.IsTrue((bool)legacy["success"]);
                Assert.AreEqual(3, ((JArray)legacy["data"]["items"]).Count);
                Assert.GreaterOrEqual(metrics.EntryReads, prefix + 3, "The legacy baseline reads the prefix at the actual native call boundary.");
                JObject empty = Tail(state, (string)page["data"]["next_cursor"], marker);
                Assert.AreEqual(0, ((JArray)empty["data"]["items"]).Count);
                Assert.IsTrue((bool)empty["data"]["loss_unknown"]);
                TestContext.Out.WriteLine(
                    "PLAY04_METRICS prefix=" + prefix + " legacy_reads=" + metrics.EntryReads + " tail_reads=" + page["data"]["native_entry_reads"]
                );
            }
            finally
            {
                flags.SetValue(null, savedFlags);
            }
        }

        [Test]
        public void ActualDuplicateRowsSurviveCollapseAndUiFiltersAreRestored()
        {
            // Given hidden severities, search and Collapse enabled in this owned console.
            PropertyInfo flags = Entries.GetProperty("consoleFlags", Flags);
            MethodInfo getSearch = Entries.GetMethod("GetFilteringText", Flags);
            MethodInfo setSearch = Entries.GetMethod("SetFilteringText", Flags);
            int savedFlags = (int)flags.GetValue(null);
            string savedSearch = (string)getSearch.Invoke(null, null);
            int hidden = (savedFlags | 1) & ~((1 << 7) | (1 << 8) | (1 << 9));
            string marker = "Play04_Duplicate_" + Guid.NewGuid().ToString("N");
            try
            {
                flags.SetValue(null, hidden);
                setSearch.Invoke(null, new object[] { "Unrelated synthetic search" });
                var state = new ConsoleTailCursor();
                JObject begin = Tail(state, "begin", marker);
                Debug.Log(marker);
                Debug.Log(marker);
                // When tail temporarily neutralizes all native view filters.
                JObject page = Tail(state, (string)begin["data"]["next_cursor"], marker);
                // Then identical native rows remain two events, and exact state is restored.
                Assert.AreEqual(2, ((JArray)page["data"]["items"]).Count);
                Assert.AreEqual(hidden, (int)flags.GetValue(null));
                Assert.AreEqual("Unrelated synthetic search", (string)getSearch.Invoke(null, null));
            }
            finally
            {
                setSearch.Invoke(null, new object[] { savedSearch });
                flags.SetValue(null, savedFlags);
            }
        }

        [Test]
        public void OwnedClearInvalidatesExistingTailWithoutChangingClearEnvelope()
        {
            // Given a cursor from the production tail state (disposable project only).
            JObject begin = Tail(TailConsole.Cursors, "begin", "");
            // When the existing clear handler succeeds.
            JObject cleared = JObject.FromObject(ReadConsole.HandleCommand(new JObject { ["action"] = "clear" }));
            // Then the old cursor fails stale instead of silently restarting.
            Assert.IsTrue((bool)cleared["success"]);
            Assert.AreEqual("Console cleared successfully.", (string)cleared["message"]);
            JObject stale = Tail(TailConsole.Cursors, (string)begin["data"]["next_cursor"], "");
            Assert.IsFalse((bool)stale["success"]);
            Assert.AreEqual("generation_changed", (string)stale["data"]["reason"]);
        }

        private static object SyntheticStartFailure() => throw new InvalidOperationException("Synthetic snapshot failure");

        [Test]
        public void SnapshotConstructionFailureRestoresActualUiState()
        {
            // Given actual scoped UI state and a synthetic failure at the native start boundary.
            PropertyInfo flags = Entries.GetProperty("consoleFlags", Flags);
            MethodInfo getSearch = Entries.GetMethod("GetFilteringText", Flags);
            MethodInfo setSearch = Entries.GetMethod("SetFilteringText", Flags);
            int savedFlags = (int)flags.GetValue(null);
            string savedSearch = (string)getSearch.Invoke(null, null);
            FieldInfo start = typeof(ReadConsole).GetField("_startGettingEntriesMethod", Flags);
            object original = start.GetValue(null);
            try
            {
                setSearch.Invoke(null, new object[] { "Synthetic failure search" });
                start.SetValue(null, typeof(ConsoleTailNativeTests).GetMethod(nameof(SyntheticStartFailure), Flags));
                // When the production snapshot constructor fails after UI neutralization.
                Assert.Throws<TargetInvocationException>(() => ReadConsole.OpenTailSnapshot());
                // Then exact flags/search survive the error path without logging native payload.
                Assert.AreEqual(savedFlags, (int)flags.GetValue(null));
                Assert.AreEqual("Synthetic failure search", (string)getSearch.Invoke(null, null));
            }
            finally
            {
                start.SetValue(null, original);
                setSearch.Invoke(null, new object[] { savedSearch });
                flags.SetValue(null, savedFlags);
            }
        }
    }
}
