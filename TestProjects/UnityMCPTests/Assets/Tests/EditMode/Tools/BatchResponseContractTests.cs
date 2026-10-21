using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Reflection.Emit;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
    public class BatchResponseContractTests
    {
        private MCPForUnityTests.Editor.Services.ScopedConsentTestState scopedConsent;

        [SetUp]
        public void GrantBatchScope() => scopedConsent = new MCPForUnityTests.Editor.Services.ScopedConsentTestState("batch_execute");

        [TearDown]
        public void RestoreBatchScope() => scopedConsent?.Dispose();

        private static bool Classify(object result) =>
            (bool)typeof(BatchExecute).GetMethod("DetermineCallSucceeded", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new[] { result });

        private class ExpensivePayload
        {
            public object Data => throw new InvalidOperationException("Do not serialize status payload.");
        }

        [Test]
        public void LargeConfiguredBatchPreservesEveryIndexedOutcome()
        {
            const string key = EditorPrefKeys.BatchExecuteMaxCommands;
            bool existed = EditorPrefs.HasKey(key);
            int original = EditorPrefs.GetInt(key);
            try
            {
                EditorPrefs.SetInt(key, 500);
                var commands = new JArray();
                for (int index = 0; index < 300; index++)
                    commands.Add(new JObject { ["tool"] = "" });
                var result = JObject.FromObject(BatchExecute.HandleCommand(new JObject { ["commands"] = commands }).GetAwaiter().GetResult());
                Assert.AreEqual(300, ((JArray)result.SelectToken("data.results")).Count, result.ToString());
                Assert.AreEqual(300, result.SelectToken("data.callFailureCount").Value<int>());
            }
            finally
            {
                if (existed)
                    EditorPrefs.SetInt(key, original);
                else
                    EditorPrefs.DeleteKey(key);
            }
        }

        [Test]
        public void InvalidToolNameRejectsLargeParametersWithoutCloningThem()
        {
            JObject Request(int valueCount)
            {
                var values = new JArray();
                for (int index = 0; index < valueCount; index++)
                    values.Add(index);
                return new JObject
                {
                    ["commands"] = new JArray(
                        new JObject
                        {
                            ["tool"] = "",
                            ["params"] = new JObject { ["value"] = values },
                        }
                    ),
                };
            }
            var small = Request(0);
            var large = Request(50000);
            BatchExecute.HandleCommand(small).GetAwaiter().GetResult();
            BatchExecute.HandleCommand(large).GetAwaiter().GetResult();

            long Allocations(JObject request)
            {
                long before = GC.GetAllocatedBytesForCurrentThread();
                for (int index = 0; index < 3; index++)
                    BatchExecute.HandleCommand(request).GetAwaiter().GetResult();
                return (GC.GetAllocatedBytesForCurrentThread() - before) / 3;
            }

            long extraBytes = Allocations(large) - Allocations(small);
            Assert.Less(extraBytes, 262144L, "Rejected params should not allocate a clone proportional to their size.");
            var response = JObject.FromObject(BatchExecute.HandleCommand(large).GetAwaiter().GetResult());
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual(1, response.SelectToken("data.callFailureCount").Value<int>());
            StringAssert.Contains("non-empty", response.SelectToken("data.results[0].error").ToString());
            Assert.AreEqual(50000, ((JArray)large.SelectToken("commands[0].params.value")).Count);
        }

        [Test]
        public void NormalizationPreservesNestedKeysAndIsolatesOriginalInput()
        {
            var source = new JObject
            {
                ["search_method"] = "by_name",
                ["value"] = new JObject { ["m_PersistentCalls"] = new JArray(1, 2) },
            };
            var before = source.DeepClone();
            var normalized = (JObject)
                typeof(BatchExecute).GetMethod("NormalizeParameterKeys", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { source });
            Assert.AreEqual("by_name", normalized.Value<string>("searchMethod"));
            Assert.IsNull(normalized["search_method"]);
            Assert.IsNotNull(normalized.SelectToken("value.m_PersistentCalls"));
            Assert.IsTrue(JToken.DeepEquals(source["value"], normalized["value"]));
            ((JArray)normalized.SelectToken("value.m_PersistentCalls")).Add(3);
            Assert.IsTrue(JToken.DeepEquals(before, source), "Mutating forwarded params must not mutate the original batch input.");
        }

        [Test]
        public void ExplicitBooleanStatusAndRawPayloadsAreClassifiedWithoutSerialization()
        {
            Assert.IsFalse(Classify(new { success = false, message = "failed" }));
            Assert.IsFalse(Classify(new Dictionary<string, object> { ["success"] = false }));
            Assert.IsFalse(Classify(new ErrorResponse("failed")));
            Assert.IsFalse(Classify(new JObject { ["success"] = false }));
            Assert.IsTrue(Classify(new { success = true, data = new ExpensivePayload() }));
            Assert.IsTrue(Classify(new Dictionary<string, bool> { ["success"] = true }));
            Assert.IsTrue(Classify(new SuccessResponse("done")));
            Assert.IsTrue(Classify(new JObject { ["success"] = true }));
            Assert.IsTrue(Classify(null));
            Assert.IsTrue(Classify(new { data = new ExpensivePayload() }));
            Assert.IsTrue(Classify(new { success = "false" }));
            Assert.IsTrue(Classify(new Dictionary<string, object> { ["success"] = "false" }));
            Assert.IsTrue(Classify(new JObject()));
            Assert.IsTrue(Classify(new JArray(1, 2)));
            Assert.IsTrue(Classify(new JValue(42)));
            Assert.IsTrue(Classify(JValue.CreateNull()));
        }

        [TestCase("manage_animation", true)]
        [TestCase("manage_animation", false)]
        [TestCase("manage_vfx", true)]
        [TestCase("manage_vfx", false)]
        public async Task FailedDomainCommandRetainsDiagnosticsAndHonorsFailFast(string command, bool failFast)
        {
            CommandRegistry.Initialize();
            var discovery = MCPServiceLocator.ToolDiscovery;
            bool enabled = discovery.IsToolEnabled(command);
            bool vfxEnabled = discovery.IsToolEnabled("manage_vfx");
            discovery.SetToolEnabled(command, true);
            discovery.SetToolEnabled("manage_vfx", true);
            try
            {
                var result = JObject.FromObject(
                    await BatchExecute.HandleCommand(
                        new JObject
                        {
                            ["failFast"] = failFast,
                            ["commands"] = new JArray(
                                new JObject
                                {
                                    ["tool"] = command,
                                    ["params"] = new JObject { ["action"] = "invalid" },
                                },
                                new JObject
                                {
                                    ["tool"] = "manage_vfx",
                                    ["params"] = new JObject { ["action"] = "ping" },
                                }
                            ),
                        }
                    )
                );
                Assert.IsFalse(result.Value<bool>("success"));
                Assert.AreEqual(1, result.SelectToken("data.callFailureCount").Value<int>());
                Assert.AreEqual(failFast ? 0 : 1, result.SelectToken("data.callSuccessCount").Value<int>());
                Assert.AreEqual(failFast ? 1 : 2, ((JArray)result.SelectToken("data.results")).Count);
                Assert.IsFalse(result.SelectToken("data.results[0].callSucceeded").Value<bool>());
                StringAssert.Contains("Unknown action", result.SelectToken("data.results[0].result.message").ToString());
                if (!failFast)
                    Assert.AreEqual("manage_vfx", result.SelectToken("data.results[1].result.tool").ToString());
            }
            finally
            {
                discovery.SetToolEnabled(command, enabled);
                discovery.SetToolEnabled("manage_vfx", vfxEnabled);
            }
        }

        [TestCase("domain", false)]
        [TestCase("domain", true)]
        [TestCase("exception", false)]
        [TestCase("exception", true)]
        [TestCase("tool_disabled", false)]
        [TestCase("tool_disabled", true)]
        [TestCase("resource_disabled", false)]
        [TestCase("resource_disabled", true)]
        public async Task Progress_CountsSettledFailuresButNeverFailFastSuffix(string failure, bool failFast)
        {
            const string first = "__core06_prefix";
            const string middle = "__core06_failure";
            const string last = "__core06_suffix";
            using var handlers = new MCPForUnityTests.Editor.Services.ProgressHandlerScope();
            using var progress = new MCPForUnityTests.Editor.Services.NativeProgressProbe();
            using var settings = MCPForUnityTests.Editor.Services.NativeProgressProbe.EnableTools("batch_execute", "manage_gameobject");
            var resources = MCPServiceLocator.ResourceDiscovery;
            int prefixCalls = 0,
                failureCalls = 0,
                suffixCalls = 0;
            handlers.Set(
                first,
                new HandlerInfo(
                    first,
                    _ =>
                    {
                        prefixCalls++;
                        return new SuccessResponse("settled");
                    },
                    null
                )
            );
            handlers.Set(
                last,
                new HandlerInfo(
                    last,
                    _ =>
                    {
                        suffixCalls++;
                        return new SuccessResponse("settled");
                    },
                    null
                )
            );
            handlers.Set(
                middle,
                new HandlerInfo(
                    middle,
                    _ =>
                    {
                        failureCalls++;
                        if (failure == "exception")
                            throw new InvalidOperationException("owned CORE06 child failure");
                        return new ErrorResponse("owned CORE06 domain failure");
                    },
                    null
                )
            );
            string failedTool =
                failure == "tool_disabled" ? "manage_gameobject"
                : failure == "resource_disabled" ? "get_project_info"
                : middle;
            if (failure == "tool_disabled" || failure == "resource_disabled")
                handlers.Set(
                    failedTool,
                    new HandlerInfo(
                        failedTool,
                        _ =>
                        {
                            failureCalls++;
                            return new SuccessResponse("must not run");
                        },
                        null
                    )
                );
            if (failure == "tool_disabled")
                MCPServiceLocator.ToolDiscovery.SetToolEnabled("manage_gameobject", false);
            if (failure == "resource_disabled")
                resources.SetResourceEnabled("get_project_info", false);
            var request = new JObject
            {
                ["failFast"] = failFast,
                ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = failedTool }, new JObject { ["tool"] = last }),
            };
            var before = request.DeepClone();
            var response = JObject.FromObject(
                await MCPForUnityTests.Editor.Services.NativeProgressProbe.InvokeBatch(request, System.Threading.CancellationToken.None, progress)
            );
            int attempted = failFast ? 2 : 3;
            Assert.IsTrue(JToken.DeepEquals(before, request), "Adding progress must not rewrite batch business arguments.");
            Assert.AreEqual(attempted, response.SelectToken("data.attemptedCount").Value<int>());
            Assert.AreEqual(failFast ? 1 : 0, response.SelectToken("data.notRunCount").Value<int>());
            Assert.AreEqual("failed", response.SelectToken("data.outcomes[1].state").Value<string>());
            Assert.AreEqual(
                failure == "domain" ? "handler_failed"
                    : failure == "exception" ? "handler_exception"
                    : failure,
                response.SelectToken("data.outcomes[1].reason").Value<string>()
            );
            Assert.AreEqual(failFast ? "not_run" : "succeeded", response.SelectToken("data.outcomes[2].state").Value<string>());
            Assert.IsTrue(progress.TryTake(out var update), "Error and disabled branches must report their actual settled attempt.");
            Assert.AreEqual(attempted, update.Completed);
            Assert.AreEqual(3, update.Total, "Fail-fast must not renormalize its admitted total to the executed prefix.");
            Assert.IsFalse(progress.TryTake(out _));
            Assert.AreEqual(1, prefixCalls);
            Assert.AreEqual(failure.EndsWith("_disabled", StringComparison.Ordinal) ? 0 : 1, failureCalls);
            Assert.AreEqual(failFast ? 0 : 1, suffixCalls);
        }

        [Test]
        public async Task Progress_WholeEnvelopeRejectionDoesNotInvokeOrReport()
        {
            const string first = "__core06_rejected_prefix";
            using var handlers = new MCPForUnityTests.Editor.Services.ProgressHandlerScope();
            using var progress = new MCPForUnityTests.Editor.Services.NativeProgressProbe();
            int calls = 0;
            handlers.Set(
                first,
                new HandlerInfo(
                    first,
                    _ =>
                    {
                        calls++;
                        return new SuccessResponse("must not run");
                    },
                    null
                )
            );
            var response = JObject.FromObject(
                await MCPForUnityTests.Editor.Services.NativeProgressProbe.InvokeBatch(
                    new JObject { ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = "batch_validate" }) },
                    System.Threading.CancellationToken.None,
                    progress
                )
            );
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual(0, response.SelectToken("data.attemptedCount").Value<int>());
            Assert.AreEqual(0, calls);
            Assert.IsFalse(progress.TryTake(out _));
        }

        private static JObject Preview(JObject request)
        {
            var method = typeof(BatchExecute).GetMethod("ValidateCommand", BindingFlags.Public | BindingFlags.Static);
            Assert.IsNotNull(method, "Native preview capability must exist; never execute a legacy batch as fallback.");
            return JObject.FromObject(method.Invoke(null, new object[] { request }));
        }

        private static IDisposable EnableTools(params string[] names) => new ToolState(names);

        private sealed class ToolState : IDisposable
        {
            private readonly Dictionary<string, bool?> values = new();

            public ToolState(string[] names)
            {
                foreach (string name in names)
                {
                    foreach (string key in new[] { "MCPForUnity.ToolEnabled." + name, "MCPForUnity.ToolEnabled.ExplicitConsent." + name })
                        values[key] = EditorPrefs.HasKey(key) ? EditorPrefs.GetBool(key) : null;
                    MCPServiceLocator.ToolDiscovery.SetToolEnabled(name, true);
                }
            }

            public void Dispose()
            {
                foreach (var pair in values)
                    if (pair.Value.HasValue)
                        EditorPrefs.SetBool(pair.Key, pair.Value.Value);
                    else
                        EditorPrefs.DeleteKey(pair.Key);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task LongToolNameKeepsLegacyFieldsAndBoundsOnlyNewReceipts(bool registeredCustom)
        {
            CommandRegistry.Initialize();
            using var state = EnableTools("batch_execute");
            string name = new string('x', 129);
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            object previous = handlers[name];
            if (registeredCustom)
                handlers[name] = new HandlerInfo(name, _ => new SuccessResponse("controlled"), null);
            else
                handlers.Remove(name);
            try
            {
                var response = JObject.FromObject(await BatchExecute.HandleCommand(new JObject { ["commands"] = new JArray(new JObject { ["tool"] = name }) }));
                Assert.AreEqual(name, response.SelectToken("data.results[0].tool").Value<string>());
                Assert.AreEqual(registeredCustom, response.SelectToken("data.results[0].callSucceeded").Value<bool>());
                Assert.AreEqual(name.Substring(0, 128), response.SelectToken("data.outcomes[0].tool").Value<string>());
                Assert.IsTrue(response.SelectToken("data.outcomes[0].toolTruncated").Value<bool>());
                var preview = Preview(new JObject { ["commands"] = new JArray(new JObject { ["tool"] = name }) });
                Assert.AreEqual(name.Substring(0, 128), preview.SelectToken("data.validation.items[0].tool").Value<string>());
                Assert.IsTrue(preview.SelectToken("data.validation.items[0].toolTruncated").Value<bool>());

                var invalid = JObject.FromObject(
                    await BatchExecute.HandleCommand(new JObject { ["commands"] = new JArray(new JObject { ["tool"] = name, ["params"] = new JArray() }) })
                );
                Assert.AreEqual(name, invalid.SelectToken("data.results[0].tool").Value<string>());
                Assert.AreEqual(name.Substring(0, 128), invalid.SelectToken("data.outcomes[0].tool").Value<string>());
                Assert.IsTrue(invalid.SelectToken("data.outcomes[0].toolTruncated").Value<bool>());
                Assert.AreEqual("invalid_batch", invalid.SelectToken("data.outcomes[0].reason").Value<string>());
                Assert.AreEqual(0, invalid.SelectToken("data.attemptedCount").Value<int>());
            }
            finally
            {
                if (previous == null)
                    handlers.Remove(name);
                else
                    handlers[name] = previous;
            }
        }

        [TestCase(true)]
        [TestCase(false)]
        public async Task IndexedOutcomesPreserveExecutedPrefixAndFailFastSuffix(bool failFast)
        {
            CommandRegistry.Initialize();
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            const string first = "core11_controlled_first",
                last = "core11_controlled_last";
            object oldFirst = handlers[first],
                oldLast = handlers[last];
            int firstCalls = 0,
                lastCalls = 0;
            handlers[first] = new HandlerInfo(
                first,
                _ =>
                {
                    firstCalls++;
                    return new SuccessResponse("changed");
                },
                null
            );
            handlers[last] = new HandlerInfo(
                last,
                _ =>
                {
                    lastCalls++;
                    return new SuccessResponse("changed");
                },
                null
            );
            try
            {
                var request = new JObject
                {
                    ["failFast"] = failFast,
                    ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = "core11_missing" }, new JObject { ["tool"] = last }),
                };
                var before = request.DeepClone();
                var response = JObject.FromObject(await BatchExecute.HandleCommand(request));
                Assert.IsTrue(JToken.DeepEquals(before, request), "Batch input must remain unchanged.");
                Assert.AreEqual(1, firstCalls);
                Assert.AreEqual(failFast ? 0 : 1, lastCalls);
                Assert.AreEqual(failFast ? 2 : 3, ((JArray)response.SelectToken("data.results")).Count);
                var outcomes = response.SelectToken("data.outcomes") as JArray;
                Assert.IsNotNull(outcomes, "An ordinary old batch lacks the full indexed outcome contract.");
                Assert.AreEqual(3, outcomes.Count);
                Assert.AreEqual("succeeded", outcomes[0].Value<string>("state"));
                Assert.AreEqual("failed", outcomes[1].Value<string>("state"));
                Assert.AreEqual(failFast ? "not_run" : "succeeded", outcomes[2].Value<string>("state"));
                Assert.AreEqual(failFast ? 2 : 3, response.SelectToken("data.attemptedCount").Value<int>());
                Assert.AreEqual(failFast ? 1 : 0, response.SelectToken("data.notRunCount").Value<int>());
                Assert.IsFalse(response.SelectToken("data.rollbackPerformed").Value<bool>());
                for (int i = 0; i < 3; i++)
                    Assert.AreEqual(i, outcomes[i].Value<int>("index"));
            }
            finally
            {
                if (oldFirst == null)
                    handlers.Remove(first);
                else
                    handlers[first] = oldFirst;
                if (oldLast == null)
                    handlers.Remove(last);
                else
                    handlers[last] = oldLast;
            }
        }

        [TestCase("manage_gameobject", "CREATE")]
        [TestCase("manage_components", "add")]
        [TestCase("manage_scene", " GET_ACTIVE ")]
        public void PreviewUsesSharedActionCatalogWithoutExecutingChildren(string tool, string action)
        {
            CommandRegistry.Initialize();
            using var state = EnableTools("batch_execute", tool);
            var request = new JObject
            {
                ["commands"] = new JArray(
                    new JObject
                    {
                        ["tool"] = tool,
                        ["params"] = new JObject
                        {
                            ["action"] = action,
                            ["name"] = "Core11MustNotExist",
                            ["componentType"] = "Rigidbody",
                        },
                    }
                ),
            };
            var before = request.DeepClone();
            Assert.IsNull(UnityEngine.GameObject.Find("Core11MustNotExist"));
            var response = Preview(request);
            Assert.IsTrue(response.Value<bool>("success"));
            Assert.IsTrue(response.SelectToken("data.validation.valid").Value<bool>());
            Assert.AreEqual("valid", response.SelectToken("data.validation.items[0].status").Value<string>());
            Assert.IsTrue(JToken.DeepEquals(before, request));
            Assert.IsNull(UnityEngine.GameObject.Find("Core11MustNotExist"), "Preview cannot create an object or invoke a child.");
        }

        [TestCase("manage_gameobject", " CREATE ", "unsupported_action")]
        [TestCase("manage_components", " ADD ", "unsupported_action")]
        [TestCase("manage_scene", " GET_ACTIVE ", "ok")]
        [TestCase("manage_scene", "modify_build_settings", "action_moved")]
        [TestCase("manage_scene", null, "missing_action")]
        public void PreviewPreservesEachActionNormalizationPath(string tool, string action, string reason)
        {
            CommandRegistry.Initialize();
            using var state = EnableTools("batch_execute", tool);
            var response = Preview(
                new JObject
                {
                    ["commands"] = new JArray(
                        new JObject
                        {
                            ["tool"] = tool,
                            ["params"] = new JObject { ["action"] = action },
                        }
                    ),
                }
            );
            Assert.AreEqual(reason, response.SelectToken("data.validation.items[0].reason").Value<string>());
        }

        [Test]
        public void PrivateAliasUsesExistingBatchConsentAndEnabledGate()
        {
            CommandRegistry.Initialize();
            using var state = EnableTools("batch_execute", "manage_gameobject");
            var admission = typeof(CommandRegistry).GetMethod("GetAdmissionName", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.AreEqual("batch_execute", admission.Invoke(null, new object[] { "batch_validate" }));
            Assert.IsNull(MCPServiceLocator.ToolDiscovery.GetToolMetadata("batch_validate"), "Internal alias must not publish a second tool.");
            MCPServiceLocator.ToolDiscovery.SetToolEnabled("batch_execute", false);
            var response = Preview(
                new JObject
                {
                    ["commands"] = new JArray(
                        new JObject
                        {
                            ["tool"] = "manage_gameobject",
                            ["params"] = new JObject { ["action"] = "create" },
                        }
                    ),
                }
            );
            Assert.AreEqual("batch_disabled", response.SelectToken("data.validation.items[0].reason").Value<string>());
            Assert.IsFalse(response.Value<bool>("success"));
        }

        [Test]
        public async Task ExecutionRechecksPolicyAfterSuccessfulPreview()
        {
            CommandRegistry.Initialize();
            using var state = EnableTools("batch_execute", "manage_gameobject");
            var request = new JObject
            {
                ["commands"] = new JArray(
                    new JObject
                    {
                        ["tool"] = "manage_gameobject",
                        ["params"] = new JObject { ["action"] = "create", ["name"] = "Core11PolicyMustNotExist" },
                    }
                ),
            };
            Assert.IsTrue(Preview(request).Value<bool>("success"));
            MCPServiceLocator.ToolDiscovery.SetToolEnabled("manage_gameobject", false);
            var response = JObject.FromObject(await BatchExecute.HandleCommand(request));
            Assert.AreEqual("tool_disabled", response.SelectToken("data.outcomes[0].reason").Value<string>());
            Assert.AreEqual(1, response.SelectToken("data.attemptedCount").Value<int>());
            Assert.IsNull(UnityEngine.GameObject.Find("Core11PolicyMustNotExist"));
        }

        [TestCase("batch_execute")]
        [TestCase("BATCH_EXECUTE")]
        [TestCase("batch_validate")]
        [TestCase("BATCH_VALIDATE")]
        public async Task EitherNestedBatchNameRejectsWholeBoundedEnvelope(string name)
        {
            var request = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = "core11_missing" }, new JObject { ["tool"] = name }) };
            var response = JObject.FromObject(await BatchExecute.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual(0, response.SelectToken("data.attemptedCount").Value<int>());
            Assert.AreEqual(2, ((JArray)response.SelectToken("data.outcomes")).Count);
            foreach (JObject row in (JArray)response.SelectToken("data.outcomes"))
            {
                Assert.AreEqual("not_run", row.Value<string>("state"));
                Assert.AreEqual("invalid_batch", row.Value<string>("reason"));
            }
        }

        [Test]
        public void PreviewRechecksDisabledToolAndResourceAndUnknownAction()
        {
            CommandRegistry.Initialize();
            using var state = EnableTools("batch_execute", "manage_gameobject");
            var discovery = MCPServiceLocator.ToolDiscovery;
            var resources = MCPServiceLocator.ResourceDiscovery;
            string resourceKey = MCPForUnity.Editor.Constants.EditorPrefKeys.ResourceEnabledPrefix + "get_project_info";
            bool resourceKeyExisted = EditorPrefs.HasKey(resourceKey);
            bool resourceValue = resourceKeyExisted && EditorPrefs.GetBool(resourceKey);
            try
            {
                discovery.SetToolEnabled("manage_gameobject", false);
                resources.SetResourceEnabled("get_project_info", false);
                var response = Preview(
                    new JObject
                    {
                        ["commands"] = new JArray(
                            new JObject
                            {
                                ["tool"] = "manage_gameobject",
                                ["params"] = new JObject { ["action"] = "create" },
                            },
                            new JObject { ["tool"] = "get_project_info" }
                        ),
                    }
                );
                Assert.AreEqual("tool_disabled", response.SelectToken("data.validation.items[0].reason").Value<string>());
                Assert.AreEqual("resource_disabled", response.SelectToken("data.validation.items[1].reason").Value<string>());
                discovery.SetToolEnabled("manage_gameobject", true);
                var invalid = Preview(
                    new JObject
                    {
                        ["commands"] = new JArray(
                            new JObject
                            {
                                ["tool"] = "manage_gameobject",
                                ["params"] = new JObject { ["action"] = "unknown" },
                            }
                        ),
                    }
                );
                Assert.AreEqual("unsupported_action", invalid.SelectToken("data.validation.items[0].reason").Value<string>());
            }
            finally
            {
                if (resourceKeyExisted)
                    EditorPrefs.SetBool(resourceKey, resourceValue);
                else
                    EditorPrefs.DeleteKey(resourceKey);
            }
        }

        private static Type DynamicTool(string commandName)
        {
            var assembly = AssemblyBuilder.DefineDynamicAssembly(
                new AssemblyName("Core11Registration" + Guid.NewGuid().ToString("N")),
                AssemblyBuilderAccess.Run
            );
            var type = assembly.DefineDynamicModule("test").DefineType("Core11Registration", TypeAttributes.Public);
            var attribute = typeof(McpForUnityToolAttribute).GetConstructor(new[] { typeof(string) });
            type.SetCustomAttribute(new CustomAttributeBuilder(attribute, new object[] { commandName }));
            var method = type.DefineMethod("HandleCommand", MethodAttributes.Public | MethodAttributes.Static, typeof(object), new[] { typeof(JObject) });
            var il = method.GetILGenerator();
            il.Emit(OpCodes.Ldnull);
            il.Emit(OpCodes.Ret);
            return type.CreateType();
        }

        [TestCase("manage_components", "add")]
        [TestCase("scene_inspect", "get_active")]
        public void ReplacingBatchOwnerInvalidatesItsAliasAndDoesNotInheritValidator(string tool, string action)
        {
            CommandRegistry.Initialize();
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            var register = typeof(CommandRegistry).GetMethod("RegisterCommandType", BindingFlags.NonPublic | BindingFlags.Static);
            object owner = handlers["batch_execute"],
                alias = handlers["batch_validate"],
                component = handlers[tool];
            using var state = EnableTools("batch_execute", tool);
            try
            {
                Assert.IsTrue((bool)register.Invoke(null, new object[] { DynamicTool(tool), false }));
                var response = Preview(
                    new JObject
                    {
                        ["commands"] = new JArray(
                            new JObject
                            {
                                ["tool"] = tool,
                                ["params"] = new JObject { ["action"] = action },
                            }
                        ),
                    }
                );
                Assert.AreEqual("incomplete", response.SelectToken("data.validation.items[0].status").Value<string>());
                Assert.IsTrue((bool)register.Invoke(null, new object[] { DynamicTool("batch_execute"), false }));
                Assert.IsFalse(CommandRegistry.HasHandler("batch_validate"));
                Assert.IsNull(handlers["batch_validate"]);
            }
            finally
            {
                handlers["batch_execute"] = owner;
                handlers["batch_validate"] = alias;
                handlers[tool] = component;
            }
        }

        [Test]
        public void CustomHandlersCannotBecomePreviewCallbacksOrReplaceReservedAlias()
        {
            CommandRegistry.Initialize();
            using var state = EnableTools("batch_execute");
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            const string name = "core11_unverified_custom";
            object previous = handlers[name];
            int calls = 0;
            handlers[name] = new HandlerInfo(
                name,
                _ =>
                {
                    calls++;
                    return new SuccessResponse("must not execute");
                },
                null
            );
            try
            {
                var response = Preview(new JObject { ["commands"] = new JArray(new JObject { ["tool"] = name }) });
                Assert.IsFalse(response.SelectToken("data.validation.complete").Value<bool>());
                Assert.IsFalse(response.SelectToken("data.validation.valid").Value<bool>());
                Assert.AreEqual("action_validation_unavailable", response.SelectToken("data.validation.items[0].reason").Value<string>());
                Assert.AreEqual(0, calls);
                object alias = handlers["batch_validate"];
                Type custom = DynamicTool("batch_validate");
                var register = typeof(CommandRegistry).GetMethod("RegisterCommandType", BindingFlags.NonPublic | BindingFlags.Static);
                Assert.IsFalse((bool)register.Invoke(null, new object[] { custom, false }));
                Assert.AreSame(alias, handlers["batch_validate"]);
            }
            finally
            {
                if (previous == null)
                    handlers.Remove(name);
                else
                    handlers[name] = previous;
            }
        }
    }
}
