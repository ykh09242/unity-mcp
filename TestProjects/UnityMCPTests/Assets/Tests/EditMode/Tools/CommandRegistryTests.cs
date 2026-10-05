using System;
using System.Collections;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Resources;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using MCPForUnity.Editor.Tools;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class CommandRegistryTests
    {
        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            // Ensure CommandRegistry is initialized before tests run
            CommandRegistry.Initialize();
        }

        [Test]
        public void GetHandler_ThrowsException_ForUnknownCommand()
        {
            var unknown = "nonexistent_command_that_should_not_exist";

            Assert.Throws<InvalidOperationException>(() =>
            {
                CommandRegistry.GetHandler(unknown);
            }, "Should throw InvalidOperationException for unknown handler");
        }

        [TestCase(false, false, false)]
        [TestCase(false, false, true)]
        [TestCase(false, true, false)]
        [TestCase(false, true, true)]
        [TestCase(true, false, false)]
        [TestCase(true, false, true)]
        [TestCase(true, true, false)]
        [TestCase(true, true, true)]
        public void Registration_DuplicateWinnerMatchesMetadataOrder(bool resource, bool fullNameTie, bool reverse)
        {
            string command = "registry_order_test_" + Guid.NewGuid().ToString("N");
            string assemblyPrefix = "RegistryOrder" + Guid.NewGuid().ToString("N");
            var first = EmitHandler(assemblyPrefix + "A", "RegistryOrder.AHandler", command, resource, "sync");
            var last = EmitHandler(assemblyPrefix + "B", fullNameTie ? first.FullName : "RegistryOrder.BHandler",
                command, resource, "sync");
            var input = reverse ? new[] { last, first } : new[] { first, last };
            try
            {
                var registerTypes = typeof(CommandRegistry).GetMethod("RegisterCommandTypes",
                    BindingFlags.Static | BindingFlags.NonPublic);
                Assert.IsNotNull(registerTypes, "Registration must expose the production enumeration path for this regression.");
                Assert.AreEqual(2, registerTypes.Invoke(null, new object[] { input, resource }));
                var metadataOrder = ToolDiscoveryService.InRegistrationOrder(input).ToArray();
                Assert.AreSame(last, metadataOrder.Last());
                object metadata;
                if (resource)
                {
                    var method = typeof(ResourceDiscoveryService).GetMethod("ExtractResourceMetadata",
                        BindingFlags.Instance | BindingFlags.NonPublic);
                    metadata = method.Invoke(new ResourceDiscoveryService(), new object[]
                    {
                        metadataOrder.Last(), metadataOrder.Last().GetCustomAttribute<McpForUnityResourceAttribute>()
                    });
                }
                else
                {
                    var method = typeof(ToolDiscoveryService).GetMethod("ExtractToolMetadata",
                        BindingFlags.Instance | BindingFlags.NonPublic);
                    metadata = method.Invoke(new ToolDiscoveryService(), new object[]
                    {
                        metadataOrder.Last(), metadataOrder.Last().GetCustomAttribute<McpForUnityToolAttribute>()
                    });
                }
                string metadataAssembly = (string)metadata.GetType().GetProperty("AssemblyName").GetValue(metadata);
                Assert.AreEqual(metadataAssembly, CommandRegistry.GetHandler(command)(new JObject()));
            }
            finally { RemoveTestHandler(command); }
        }

        [TestCase(false, "ambiguous")]
        [TestCase(true, "ambiguous")]
        [TestCase(false, "genericAsync")]
        [TestCase(true, "genericAsync")]
        [TestCase(false, "genericSync")]
        [TestCase(true, "genericSync")]
        [TestCase(false, "wrongParameter")]
        [TestCase(true, "wrongParameter")]
        [TestCase(false, "instance")]
        [TestCase(true, "instance")]
        [TestCase(false, "missing")]
        [TestCase(true, "missing")]
        [TestCase(false, "valueReturn")]
        [TestCase(true, "valueReturn")]
        [TestCase(false, "voidReturn")]
        [TestCase(true, "voidReturn")]
        public void Registration_InvalidHandlerDoesNotBlockValidNeighbors(bool resource, string signature)
        {
            string prefix = "registry_invalid_test_" + Guid.NewGuid().ToString("N");
            string assemblyName = "RegistryInvalid" + Guid.NewGuid().ToString("N");
            Type invalid = EmitHandler(assemblyName, "RegistryInvalid.AInvalid", prefix, resource, signature);
            Type sync = EmitHandler(assemblyName + "Sync", "RegistryInvalid.YSync", prefix + "_sync", resource, "sync");
            Type async = EmitHandler(assemblyName + "Async", "RegistryInvalid.ZAsync", prefix + "_async", resource, "asyncObject");
            try
            {
                if (signature == "ambiguous")
                    LogAssert.Expect(LogType.Error, new Regex("Failed to register .*AInvalid:"));
                Assert.IsFalse(RegisterTestHandler(invalid, resource));
                Assert.IsTrue(RegisterTestHandler(sync, resource));
                Assert.IsTrue(RegisterTestHandler(async, resource));
                Assert.Throws<InvalidOperationException>(() => CommandRegistry.GetHandler(prefix));
                Assert.AreEqual(sync.Assembly.GetName().Name, CommandRegistry.GetHandler(prefix + "_sync")(new JObject()));
                Assert.AreEqual(async.Assembly.GetName().Name,
                    CommandRegistry.InvokeCommandAsync(prefix + "_async", new JObject()).GetAwaiter().GetResult());
            }
            finally
            {
                RemoveTestHandler(prefix);
                RemoveTestHandler(prefix + "_sync");
                RemoveTestHandler(prefix + "_async");
            }
        }

        [TestCase(false, "sync")]
        [TestCase(true, "sync")]
        [TestCase(false, "syncReference")]
        [TestCase(true, "syncReference")]
        [TestCase(false, "asyncObject")]
        [TestCase(true, "asyncObject")]
        [TestCase(false, "asyncReference")]
        [TestCase(true, "asyncReference")]
        [TestCase(false, "asyncTask")]
        [TestCase(true, "asyncTask")]
        [TestCase(false, "asyncCompleted")]
        [TestCase(true, "asyncCompleted")]
        [TestCase(false, "asyncStateMachine")]
        [TestCase(true, "asyncStateMachine")]
        [TestCase(false, "asyncBaseWithValue")]
        [TestCase(true, "asyncBaseWithValue")]
        [TestCase(false, "asyncDerived")]
        [TestCase(true, "asyncDerived")]
        public void Registration_PreservesSupportedSyncAndAsyncSignatures(bool resource, string signature)
        {
            string command = "registry_signature_test_" + Guid.NewGuid().ToString("N");
            Type type = EmitHandler("RegistrySignature" + Guid.NewGuid().ToString("N"),
                "RegistrySignature.Handler", command, resource, signature);
            try
            {
                Assert.IsTrue(RegisterTestHandler(type, resource));
                object result = CommandRegistry.InvokeCommandAsync(command, null).GetAwaiter().GetResult();
                bool noValue = signature is "asyncTask" or "asyncCompleted" or "asyncStateMachine" or "asyncBaseWithValue";
                Assert.AreEqual(noValue ? null : type.Assembly.GetName().Name, result);
                if (signature.StartsWith("async", StringComparison.Ordinal))
                    Assert.Throws<InvalidOperationException>(() => CommandRegistry.GetHandler(command));
                else
                    Assert.IsNotNull(CommandRegistry.GetHandler(command));
            }
            finally { RemoveTestHandler(command); }
        }

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        [TestCase(true, true)]
        public void Registration_AsyncFailureAndCancellationRemainErrors(bool resource, bool canceled)
        {
            string command = "registry_failure_test_" + Guid.NewGuid().ToString("N");
            Type type = EmitHandler("RegistryFailure" + Guid.NewGuid().ToString("N"),
                "RegistryFailure.Handler", command, resource, canceled ? "asyncCanceled" : "asyncFault");
            try
            {
                Assert.IsTrue(RegisterTestHandler(type, resource));
                if (canceled)
                    Assert.Throws<TaskCanceledException>(() => CommandRegistry.InvokeCommandAsync(command, null).GetAwaiter().GetResult());
                else
                    Assert.Throws<InvalidOperationException>(() => CommandRegistry.InvokeCommandAsync(command, null).GetAwaiter().GetResult());
                LogAssert.Expect(LogType.Error, new Regex("Error in async command '" + command + "':"));
                var completion = new TaskCompletionSource<string>();
                Assert.IsNull(CommandRegistry.ExecuteCommand(command, new JObject(), completion));
                Assert.IsTrue(completion.Task.IsCompleted);
                Assert.AreEqual("error", JObject.Parse(completion.Task.Result)["status"].Value<string>());
            }
            finally { RemoveTestHandler(command); }
        }

        private static Type EmitHandler(string assemblyName, string fullName, string command, bool resource, string signature)
        {
            var assembly = AssemblyBuilder.DefineDynamicAssembly(new AssemblyName(assemblyName), AssemblyBuilderAccess.Run);
            var type = assembly.DefineDynamicModule(assemblyName).DefineType(fullName, TypeAttributes.Public);
            Type attribute = resource ? typeof(McpForUnityResourceAttribute) : typeof(McpForUnityToolAttribute);
            type.SetCustomAttribute(new CustomAttributeBuilder(attribute.GetConstructor(new[] { typeof(string) }),
                new object[] { command }));
            if (signature != "missing")
            {
                Type returnType = signature is "asyncTask" or "asyncCompleted" or "asyncStateMachine" or "asyncBaseWithValue" ? typeof(Task) :
                    signature == "asyncDerived" ? typeof(DerivedResultTask) :
                    signature == "asyncReference" ? typeof(Task<string>) :
                    signature is "asyncObject" or "genericAsync" or "asyncFault" or "asyncCanceled" ? typeof(Task<object>) :
                    signature == "syncReference" ? typeof(string) :
                    signature == "valueReturn" ? typeof(int) :
                    signature == "voidReturn" ? typeof(void) : typeof(object);
                DefineHandlerMethod(type, returnType, assemblyName, signature,
                    signature is "genericAsync" or "genericSync");
                if (signature == "ambiguous")
                    DefineHandlerMethod(type, returnType, assemblyName, signature, true);
            }
            return type.CreateType();
        }

        private static void DefineHandlerMethod(TypeBuilder type, Type returnType, string result, string signature, bool generic)
        {
            var flags = MethodAttributes.Public;
            if (signature != "instance") flags |= MethodAttributes.Static;
            var method = type.DefineMethod("HandleCommand", flags, returnType,
                new[] { signature == "wrongParameter" ? typeof(string) : typeof(JObject) });
            if (generic) method.DefineGenericParameters("T");
            var il = method.GetILGenerator();
            if (returnType == typeof(Task))
            {
                if (signature == "asyncCompleted")
                    il.Emit(OpCodes.Call, typeof(Task).GetProperty("CompletedTask").GetGetMethod());
                else if (signature == "asyncStateMachine")
                    il.Emit(OpCodes.Call, typeof(CommandRegistryTests).GetMethod(nameof(CreateCompletedAsyncTask)));
                else if (signature == "asyncBaseWithValue")
                {
                    il.Emit(OpCodes.Ldstr, result);
                    il.Emit(OpCodes.Call, typeof(CommandRegistryTests).GetMethod(nameof(CreateBaseTaskWithValue)));
                }
                else
                    il.Emit(OpCodes.Call, typeof(CommandRegistryTests).GetMethod(nameof(CreateCompletedNonGenericTask)));
            }
            else if (returnType == typeof(int))
                il.Emit(OpCodes.Ldc_I4_1);
            else if (returnType != typeof(void))
            {
                il.Emit(OpCodes.Ldstr, result);
                if (signature is "asyncFault" or "asyncCanceled" or "asyncDerived")
                {
                    string factory = signature == "asyncFault" ? nameof(CreateFaultedTask) :
                        signature == "asyncCanceled" ? nameof(CreateCanceledTask) : nameof(CreateDerivedResultTask);
                    il.Emit(OpCodes.Call, typeof(CommandRegistryTests).GetMethod(factory));
                }
                else if (typeof(Task).IsAssignableFrom(returnType))
                {
                    var fromResult = typeof(Task).GetMethods().Single(methodInfo => methodInfo.Name == "FromResult");
                    il.Emit(OpCodes.Call, fromResult.MakeGenericMethod(returnType.GetGenericArguments()[0]));
                }
            }
            il.Emit(OpCodes.Ret);
        }

        public static Task CreateCompletedNonGenericTask()
        {
            var task = new Task(() => { });
            task.RunSynchronously();
            return task;
        }

        public static async Task CreateCompletedAsyncTask() { await Task.CompletedTask; }
        public static Task CreateBaseTaskWithValue(string value) => Task.FromResult(value);
        public static Task<object> CreateFaultedTask(string message) =>
            Task.FromException<object>(new InvalidOperationException(message));
        public static Task<object> CreateCanceledTask(string unused) =>
            Task.FromCanceled<object>(new CancellationToken(true));

        public class DerivedResultTask : Task<string>
        {
            public DerivedResultTask(string value) : base(() => value) { }
        }

        public static DerivedResultTask CreateDerivedResultTask(string value)
        {
            var task = new DerivedResultTask(value);
            task.RunSynchronously();
            return task;
        }

        private static bool RegisterTestHandler(Type type, bool resource)
        {
            var method = typeof(CommandRegistry).GetMethod("RegisterCommandType", BindingFlags.Static | BindingFlags.NonPublic);
            try { return (bool)method.Invoke(null, new object[] { type, resource }); }
            catch (TargetInvocationException ex) { throw ex.InnerException ?? ex; }
        }

        private static void RemoveTestHandler(string command)
        {
            var field = typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic);
            ((IDictionary)field.GetValue(null)).Remove(command);
        }

        [Test]
        public void AutoDiscovery_RegistersAllBuiltInTools()
        {
            // Verify that all expected built-in tools are registered by trying to get their handlers
            var expectedTools = new[]
            {
                "manage_asset",
                "manage_editor",
                "manage_gameobject",
                "manage_scene",
                "manage_script",
                "manage_shader",
                "manage_ugui",
                "read_console",
                "execute_menu_item",
                "manage_prefabs"
            };

            foreach (var toolName in expectedTools)
            {
                var handler = CommandRegistry.GetHandler(toolName);
                Assert.IsNotNull(handler, $"Handler for '{toolName}' should not be null");

                // Verify the handler is actually callable (returns a result, not throws)
                var emptyParams = new Newtonsoft.Json.Linq.JObject();
                var result = handler(emptyParams);
                Assert.IsNotNull(result, $"Handler for '{toolName}' should return a result even for empty params");
            }
        }

        [UnityTest]
        public IEnumerator SyncHandlerThatReturnsATask_IsAwaitedOnBothEntryPoints()
        {
            // manage_scene's play-mode screenshot returns a Task from its synchronous handler,
            // so the capture can wait for the end of the frame. The dispatcher (ExecuteCommand)
            // and batch_execute (InvokeCommandAsync) must both wait for that Task instead of
            // answering with the Task object itself.
            const string name = "__test_sync_handler_returns_task";
            var handlers = (IDictionary)typeof(CommandRegistry)
                .GetField("_handlers", BindingFlags.NonPublic | BindingFlags.Static)
                .GetValue(null);
            var pending = new TaskCompletionSource<object>();
            handlers[name] = new HandlerInfo(name, _ => pending.Task, null);
            try
            {
                var tcs = new TaskCompletionSource<string>();
                Assert.IsNull(CommandRegistry.ExecuteCommand(name, new JObject(), tcs),
                    "the answer must come through the completion source, after the task");
                Assert.AreSame(pending.Task, CommandRegistry.InvokeCommandAsync(name, new JObject()));
                Assert.IsFalse(tcs.Task.IsCompleted, "the command answered before its task finished");

                pending.SetResult("captured");
                float deadline = Time.realtimeSinceStartup + 5f;
                while (!tcs.Task.IsCompleted && Time.realtimeSinceStartup < deadline)
                    yield return null;

                Assert.IsTrue(tcs.Task.IsCompleted, "the command never answered");
                StringAssert.Contains("\"result\":\"captured\"", tcs.Task.Result);
            }
            finally
            {
                handlers.Remove(name);
            }
        }
    }
}
