using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources.Editor;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnity.Editor.Tests.EditMode.Services
{
    [Parallelizable(ParallelScope.None)]
    public class ToolDiscoveryDiagnosticsTests
    {
        private static MethodInfo CandidateMethod =>
            typeof(ToolDiscoveryService).GetMethod("ApplyDiagnosticCandidate", BindingFlags.Instance | BindingFlags.NonPublic);

        private static JObject Snapshot(ToolDiscoveryService service)
        {
            var method = typeof(ToolDiscoveryService).GetMethod("GetDiagnostics");
            Assert.That(method, Is.Not.Null, "cached discovery diagnostics are not available");
            return (JObject)method.Invoke(service, new object[] { null, 64 });
        }

        private static ToolDiscoveryService OwnedService()
        {
            var service = new ToolDiscoveryService();
            typeof(ToolDiscoveryService)
                .GetField("_cachedTools", BindingFlags.Instance | BindingFlags.NonPublic)
                .SetValue(service, new Dictionary<string, ToolMetadata>());
            return service;
        }

        private static void Apply(ToolDiscoveryService service, Type type, McpForUnityToolAttribute attribute)
        {
            Assert.That(CandidateMethod, Is.Not.Null, "production candidate classification is not structured");
            CandidateMethod.Invoke(service, new object[] { type, attribute });
        }

        [Test]
        public void UninitializedSnapshotIsUnknownWithoutDiscovery()
        {
            var service = new ToolDiscoveryService();
            var snapshot = Snapshot(service);
            Assert.That((bool)snapshot["complete"], Is.False);
            Assert.That(snapshot["rows"], Is.Empty);
            Assert.That(typeof(ToolDiscoveryService).GetField("_cachedTools", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(service), Is.Null);
        }

        [Test]
        public void InvalidHandlerHasSafeReasonWithoutExecutingAnything()
        {
            var service = OwnedService();
            Apply(service, typeof(NoHandler), new McpForUnityToolAttribute("invalid_owned"));
            var row = (JObject)Snapshot(service)["rows"][0];
            Assert.That((string)row["reason"], Is.EqualTo("unsupported_handler"));
            Assert.That((string)row["next_action"], Is.EqualTo("fix_handler_signature"));
        }

        [Test]
        public void DuplicateRetainsActualLastWinnerAndRejectedLoser()
        {
            var service = OwnedService();
            Apply(service, typeof(First), new McpForUnityToolAttribute("same_owned"));
            Apply(service, typeof(Second), new McpForUnityToolAttribute("same_owned"));
            var rows = (JArray)Snapshot(service)["rows"];
            Assert.That((string)rows[0]["reason"], Is.EqualTo("duplicate_shadowed"));
            Assert.That((string)rows[1]["state"], Is.EqualTo("discovered"));
        }

        [Test]
        public void RichSchemaFailureKeepsSiblingAndSafeFailure()
        {
            var service = OwnedService();
            Apply(service, typeof(First), new McpForUnityToolAttribute("valid_owned"));
            var cache =
                (Dictionary<string, ToolMetadata>)
                    typeof(ToolDiscoveryService).GetField("_cachedTools", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(service);
            var valid = cache["valid_owned"];
            var rejected = (ToolMetadata)
                CandidateMethod.Invoke(
                    service,
                    new object[]
                    {
                        typeof(InvalidRich),
                        new McpForUnityToolAttribute("bad_owned") { SchemaVersion = 1 },
                    }
                );
            Assert.That(rejected.SchemaError, Is.Not.Null.And.Not.Empty);
            Assert.That(rejected.InputSchema, Is.Null);
            Assert.That(rejected.OutputDataSchema, Is.Null);
            Assert.That(cache["valid_owned"], Is.SameAs(valid));
            Assert.That(cache["bad_owned"], Is.SameAs(rejected));
            var rows = (JArray)Snapshot(service)["rows"];
            Assert.That((string)rows[0]["state"], Is.EqualTo("discovered"));
            Assert.That((string)rows[1]["state"], Is.EqualTo("rejected"));
            Assert.That((string)rows[1]["reason"], Is.EqualTo("schema_or_default_invalid"));
            Assert.That((string)rows[1]["next_action"], Is.EqualTo("review_schema_and_defaults"));
            StringAssert.DoesNotContain("Exception", rows.ToString());
        }

        [Test]
        public void EmptyRichDeclarationWithoutParametersIsAccepted()
        {
            var service = OwnedService();
            var accepted = (ToolMetadata)
                CandidateMethod.Invoke(
                    service,
                    new object[]
                    {
                        typeof(First),
                        new McpForUnityToolAttribute("empty_owned") { SchemaVersion = 1 },
                    }
                );
            Assert.That(string.IsNullOrEmpty(accepted.SchemaError), Is.True);
            Assert.That(accepted.InputSchema, Is.Not.Null);
            Assert.That((string)accepted.InputSchema["type"], Is.EqualTo("object"));
            Assert.That(accepted.InputSchema["properties"], Is.Empty);
            Assert.That((bool)accepted.InputSchema["additionalProperties"], Is.False);
            Assert.That(accepted.OutputDataSchema, Is.Null);
            var row = (JObject)Snapshot(service)["rows"][0];
            Assert.That((string)row["state"], Is.EqualTo("discovered"));
            Assert.That((string)row["reason"], Is.EqualTo("none"));
        }

        [Test]
        public void ClassificationMetadataFailureAndDuplicateEmitNoLogsOrPreferenceInitialization()
        {
            var service = OwnedService();
            int logs = 0;
            Application.LogCallback observe = (message, stack, type) => logs++;
            Application.logMessageReceived += observe;
            try
            {
                Apply(service, typeof(BrokenMetadata), new McpForUnityToolAttribute("metadata_owned"));
                Apply(service, typeof(First), new McpForUnityToolAttribute("duplicate_owned"));
                Apply(service, typeof(Second), new McpForUnityToolAttribute("duplicate_owned"));
                var rows = (JArray)Snapshot(service)["rows"];
                Assert.That((string)rows[0]["reason"], Is.EqualTo("metadata_invalid"));
                Assert.That((string)rows[1]["reason"], Is.EqualTo("duplicate_shadowed"));
                Assert.That(logs, Is.Zero);
                Assert.That((bool)Snapshot(service)["complete"], Is.False);
                TestContext.Out.WriteLine("DEV20_NATIVE_DIAGNOSTICS:" + Snapshot(service).ToString(Newtonsoft.Json.Formatting.None));
            }
            finally
            {
                Application.logMessageReceived -= observe;
            }
        }

        [Test]
        public void InvalidRichDefaultIsSafeWithoutParameterConstruction()
        {
            var service = OwnedService();
            Apply(service, typeof(InvalidDefault), new McpForUnityToolAttribute("default_owned") { SchemaVersion = 1 });
            var row = (JObject)Snapshot(service)["rows"][0];
            Assert.That((string)row["reason"], Is.EqualTo("schema_or_default_invalid"));
            StringAssert.DoesNotContain("private-default-text", row.ToString());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CompletedCacheUsesOwnedPolicyAndDiscardsInvalidatedRead(bool invalidate)
        {
            var service = OwnedService();
            Apply(service, typeof(First), new McpForUnityToolAttribute("completed_owned"));
            var store = typeof(ToolDiscoveryService).GetField("_diagnostics", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(service);
            store.GetType().GetMethod("Complete", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(store, null);
            var core = typeof(ToolDiscoveryService).GetMethod("GetDiagnosticsCore", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(core, Is.Not.Null, "completed diagnostics must share the public cache/epoch checks");
            int reads = 0;
            int logs = 0;
            Application.LogCallback observe = (message, stack, type) => logs++;
            Func<string, ToolMetadata, bool> ownedPolicy = (name, metadata) =>
            {
                reads++;
                Assert.That(name, Is.EqualTo("completed_owned"));
                Assert.That(metadata.Name, Is.EqualTo(name));
                if (invalidate)
                    service.InvalidateCache();
                return false;
            };
            Application.logMessageReceived += observe;
            try
            {
                var result = (JObject)core.Invoke(service, new object[] { null, 64, ownedPolicy });
                Assert.That(reads, Is.EqualTo(1));
                Assert.That(logs, Is.Zero);
                if (invalidate)
                {
                    Assert.That((bool)result["complete"], Is.False);
                    Assert.That(result["rows"], Is.Empty);
                    Assert.That(
                        typeof(ToolDiscoveryService).GetField("_cachedTools", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(service),
                        Is.Null
                    );
                }
                else
                {
                    Assert.That((bool)result["complete"], Is.True);
                    Assert.That((bool)result["rows"][0]["native_enabled"], Is.False);
                    Assert.That((string)result["rows"][0]["reason"], Is.EqualTo("native_disabled"));
                    Assert.That((string)result["rows"][0]["state"], Is.EqualTo("disabled"));
                }
            }
            finally
            {
                Application.logMessageReceived -= observe;
            }
        }

        [Test]
        public void SnapshotIsDetachedBoundedAndInvalidationDoesNotDiscover()
        {
            var service = OwnedService();
            for (int i = 0; i < 540; i++)
                Apply(service, typeof(First), new McpForUnityToolAttribute("owned_" + i));
            var snapshot = Snapshot(service);
            Assert.That(((JArray)snapshot["rows"]).Count, Is.LessThanOrEqualTo(64));
            Assert.That((bool)snapshot["truncated"], Is.True);
            var store = typeof(ToolDiscoveryService).GetField("_diagnostics", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(service);
            var completeSnapshot = (JObject)
                store.GetType().GetMethod("Snapshot", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(store, new object[] { null, 512 });
            Assert.That(System.Text.Encoding.UTF8.GetByteCount(completeSnapshot.ToString(Newtonsoft.Json.Formatting.None)), Is.LessThanOrEqualTo(128 * 1024));
            ((JObject)snapshot["rows"][0])["reason"] = "tampered";
            Assert.That((string)Snapshot(service)["rows"][0]["reason"], Is.EqualTo("none"));
            service.InvalidateCache();
            var invalidated = Snapshot(service);
            Assert.That((bool)invalidated["complete"], Is.False);
            Assert.That(invalidated["rows"], Is.Empty);
        }

        [TestCase("success")]
        [TestCase("throw")]
        [TestCase("bad_limit")]
        [TestCase("bad_flag")]
        [TestCase("bad_name")]
        public void ResourceDiagnosticsNeverEnterDiscoveryOrSettings(string mode)
        {
            var locator = typeof(MCPServiceLocator).GetField("_toolDiscoveryService", BindingFlags.Static | BindingFlags.NonPublic);
            var settingsField = typeof(ToolStates).GetField("_settingsStateOverride", BindingFlags.Static | BindingFlags.NonPublic);
            var previous = locator.GetValue(null);
            var previousSettings = settingsField.GetValue(null);
            var ownedSettings = new ToolSettingsState();
            var before = JObject.FromObject(ownedSettings.Status);
            var fake = new OwnedDiagnostics { Throw = mode == "throw" };
            int settingsChanges = 0;
            ownedSettings.StatusChanged += () => settingsChanges++;
            locator.SetValue(null, fake);
            settingsField.SetValue(null, ownedSettings);
            try
            {
                var query = new JObject { ["diagnostics"] = mode == "bad_flag" ? (JToken)"true" : true, ["limit"] = mode == "bad_limit" ? 0 : 16 };
                if (mode == "bad_name")
                    query["tool_name"] = "../private";
                var response = JObject.FromObject(ToolStates.HandleCommand(query));
                Assert.That((bool)response["success"], Is.EqualTo(mode == "success"));
                Assert.That(fake.OrdinaryCalls, Is.Zero);
                Assert.That(settingsChanges, Is.Zero);
                Assert.That(JToken.DeepEquals(before, JObject.FromObject(ownedSettings.Status)), Is.True);
                StringAssert.DoesNotContain("private-throwing-provider", response.ToString());
                if (mode == "success")
                    Assert.That((bool)response["data"]["rows"][0]["native_enabled"], Is.False);
            }
            finally
            {
                locator.SetValue(null, previous);
                settingsField.SetValue(null, previousSettings);
            }
        }

        private class ThrowingParameterAttribute : ToolParameterAttribute
        {
            public ThrowingParameterAttribute()
                : base("owned") => throw new InvalidOperationException("private-metadata-error");
        }

        private class BrokenMetadata
        {
            public static object HandleCommand(JObject args) => null;

            public class Parameters
            {
                [ThrowingParameter]
                public int count = 0;
            }
        }

        private class InvalidDefault
        {
            public static object HandleCommand(JObject args) => null;

            public class Parameters
            {
                [ToolParameter("owned", Required = false, DefaultValue = "private-default-text")]
                public int count = 0;
            }
        }

        private class OwnedDiagnostics : IToolDiscoveryService, IToolDiscoveryDiagnostics
        {
            public int OrdinaryCalls;
            public bool Throw;

            private Exception Forbidden()
            {
                OrdinaryCalls++;
                return new InvalidOperationException("ordinary path forbidden");
            }

            public List<ToolMetadata> DiscoverAllTools() => throw Forbidden();

            public ToolMetadata GetToolMetadata(string name) => throw Forbidden();

            public List<ToolMetadata> GetEnabledTools() => throw Forbidden();

            public bool IsToolEnabled(string name) => throw Forbidden();

            public void SetToolEnabled(string name, bool enabled) => throw Forbidden();

            public void InvalidateCache() => throw Forbidden();

            public JObject GetDiagnostics(string name = null, int limit = 64)
            {
                if (Throw)
                    throw new InvalidOperationException("private-throwing-provider");
                return new JObject
                {
                    ["schema_version"] = "unity-mcp/native-tool-diagnostics@1",
                    ["complete"] = true,
                    ["rows"] = new JArray(
                        new JObject
                        {
                            ["tool_name"] = "owned",
                            ["stage"] = "native_enablement",
                            ["state"] = "disabled",
                            ["reason"] = "native_disabled",
                            ["next_action"] = "review_tool_toggle",
                            ["native_enabled"] = false,
                        }
                    ),
                };
            }
        }

        private class NoHandler { }

        private class First
        {
            public static object HandleCommand(JObject args) => throw new InvalidOperationException("must not execute");
        }

        private class Second
        {
            public static object HandleCommand(JObject args) => throw new InvalidOperationException("must not execute");
        }

        private class InvalidRich
        {
            public static object HandleCommand(JObject args) => throw new InvalidOperationException("must not execute");

            public class Parameters
            {
                [ToolParameter("owned")]
                public Action callback = null;
            }
        }
    }
}
