using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Resources.MenuItems;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ExecuteMenuItemTests
    {
        private const string OwnedMenuPath = "Tools/MCPForUnityTests/Owned Menu 80f1a802b581420493c68b31d1b43d8c";
        private static bool _ownedMenuInvoked;
        private const string DisabledMenuPath = "Tools/MCPForUnityTests/Disabled DEV11 9a438d";

        [MenuItem(DisabledMenuPath)]
        private static void InvokeDisabledMenu() => _ownedMenuInvoked = true;

        [MenuItem(DisabledMenuPath, true)]
        private static bool ValidateDisabledMenu() => false;

        [MenuItem("CONTEXT/Transform/Owned DEV11 9a438d")]
        private static void InvokeContextMenu(MenuCommand command) => _ownedMenuInvoked = true;

        [MenuItem(OwnedMenuPath)]
        private static void InvokeOwnedMenu() => _ownedMenuInvoked = true;

        [SetUp]
        public void SetUp() => _ownedMenuInvoked = false;

        [TearDown]
        public void TearDown()
        {
            typeof(ExecuteMenuItem).GetMethod("InterruptObservations", BindingFlags.Static | BindingFlags.NonPublic)?.Invoke(null, null);
            foreach (string name in new[] { "_existsOverride", "_enabledOverride", "_executeOverride", "_consoleOpenOverride", "_clockOverride" })
                typeof(ExecuteMenuItem).GetField(name, BindingFlags.Static | BindingFlags.NonPublic)?.SetValue(null, null);
            _ownedMenuInvoked = false;
        }

        private static JObject ToJO(object o) => JObject.FromObject(o);

        private static void Seam(string name, object value)
        {
            var field = typeof(ExecuteMenuItem).GetField(name, BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(field, "Expected bounded lifecycle seam " + name);
            field.SetValue(null, value);
        }

        private static JObject Request(string action = "execute", string path = OwnedMenuPath, string condition = null)
        {
            var request = new JObject { ["menuPath"] = path, ["action"] = action };
            if (condition != null)
                request["wait_for"] = condition;
            return request;
        }

        private static Task<object> Dispatch(JObject request, CancellationToken token = default)
        {
            CommandRegistry.Initialize();
            return CommandRegistry.InvokeCommandAsync("execute_menu_item", request, token);
        }

        [TestCase("inspect")]
        [TestCase("exists")]
        public void Inspect_ReportsEvidenceWithoutInvoking(string action)
        {
            var result = ToJO(ExecuteMenuItem.HandleCommand(Request(action)));
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.IsFalse(_ownedMenuInvoked);
            Assert.IsTrue((bool)result["data"]["existence"]["value"]);
            Assert.IsNotEmpty((string)result["data"]["existence"]["source"]);
            Assert.IsFalse((bool)result["data"]["invocation"]["attempted"]);
        }

        [Test]
        public void Execute_ReportsAcceptedWithoutClaimingCompletion()
        {
            var result = ToJO(ExecuteMenuItem.HandleCommand(Request()));
            Assert.IsTrue((bool)result["success"]);
            Assert.IsTrue(_ownedMenuInvoked);
            Assert.IsTrue((bool)result["data"]["invocation"]["accepted"]);
            Assert.AreEqual("not_observed", (string)result["data"]["completion"]["status"]);
            Assert.AreEqual(JTokenType.Null, result["data"]["completion"]["observed"].Type);
        }

        [TestCase(DisabledMenuPath, "disabled")]
        [TestCase("CONTEXT/Transform/Owned DEV11 9a438d", "context_required")]
        [TestCase("fIlE/qUiT", "blocked")]
        public void Execute_RefusesUnavailablePolicyWithoutInvocation(string path, string reason)
        {
            var result = ToJO(ExecuteMenuItem.HandleCommand(Request(path: path)));
            Assert.IsFalse((bool)result["success"], result.ToString());
            Assert.AreEqual(reason, (string)result["data"]["reason"]);
            Assert.IsFalse((bool)result["data"]["invocation"]["attempted"]);
            Assert.IsFalse(_ownedMenuInvoked);
        }

        [Test]
        public void Inspect_UnavailableRegistryDoesNotTurnMissingManagedDeclarationIntoFalse()
        {
            Seam("_existsOverride", (Func<string, bool?>)(_ => null));
            Seam("_enabledOverride", (Func<string, bool>)(_ => false));
            var result = ToJO(ExecuteMenuItem.HandleCommand(Request("inspect", "Fixture/DEV11 missing 98cd20")));
            Assert.IsTrue((bool)result["success"]);
            Assert.AreEqual(JTokenType.Null, result["data"]["existence"]["value"].Type);
            Assert.AreEqual("unknown", (string)result["data"]["availability"]["status"]);
            Assert.AreEqual(JTokenType.Null, result["data"]["availability"]["enabled"].Type);
        }

        [Test]
        public void Execute_UnknownNativeRefusalIsNotProvenMissing()
        {
            Seam("_existsOverride", (Func<string, bool?>)(_ => null));
            Seam("_enabledOverride", (Func<string, bool>)(_ => false));
            int invocations = 0;
            Seam(
                "_executeOverride",
                (Func<string, bool>)(
                    _ =>
                    {
                        invocations++;
                        return false;
                    }
                )
            );
            var result = ToJO(ExecuteMenuItem.HandleCommand(Request(path: "Fixture/DEV11 missing 98cd20")));
            Assert.IsFalse((bool)result["success"]);
            Assert.AreEqual(1, invocations);
            Assert.AreEqual("invocation_refused", (string)result["data"]["reason"]);
            Assert.AreEqual(JTokenType.Null, result["data"]["existence"]["value"].Type);
        }

        [Test]
        public void CompletionUnavailableAndAdmissionRefuseBeforeInvocation()
        {
            int invocations = 0;
            Seam("_existsOverride", (Func<string, bool?>)(_ => true));
            Seam("_enabledOverride", (Func<string, bool>)(_ => true));
            Seam(
                "_executeOverride",
                (Func<string, bool>)(
                    _ =>
                    {
                        invocations++;
                        return true;
                    }
                )
            );
            Seam("_consoleOpenOverride", (Func<bool?>)(() => null));
            var request = Request(path: "Window/General/Console", condition: "console_window_open");
            var unsupported = ToJO(ExecuteMenuItem.HandleCommand(request));
            Assert.AreEqual("unsupported_completion", (string)unsupported["data"]["reason"]);
            Seam("_consoleOpenOverride", (Func<bool?>)(() => false));
            var count = typeof(ExecuteMenuItem).GetField("_activeObserverCount", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(count);
            int previous = (int)count.GetValue(null);
            try
            {
                count.SetValue(null, 8);
                var refused = ToJO(ExecuteMenuItem.HandleCommand(request));
                Assert.AreEqual("observation_capacity", (string)refused["data"]["reason"]);
            }
            finally
            {
                count.SetValue(null, previous);
            }
            Assert.AreEqual(0, invocations);
        }

        [Test]
        public void Dispatcher_PreCancelledDoesNotInvoke()
        {
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            Assert.Throws<OperationCanceledException>(() => Dispatch(Request(), cancellation.Token));
            Assert.IsFalse(_ownedMenuInvoked);
        }

        [TestCase("{\"action\":\"other\"}")]
        [TestCase("{\"action\":false}")]
        [TestCase("{\"wait_for\":\"arbitrary_expression\"}")]
        [TestCase("{\"wait_for\":\"console_window_open\"}")]
        [TestCase("{\"timeout_ms\":true}")]
        [TestCase("{\"timeout_ms\":\"20\"}")]
        [TestCase("{\"timeout_ms\":0}")]
        [TestCase("{\"timeout_ms\":10001}")]
        [TestCase("{\"timeout_ms\":2.5}")]
        public void InvalidObservationInputsRefuseBeforeInvocation(string additions)
        {
            var request = Request();
            request.Merge(JObject.Parse(additions));
            var result = ToJO(ExecuteMenuItem.HandleCommand(request));
            Assert.IsFalse((bool)result["success"], result.ToString());
            Assert.IsFalse(_ownedMenuInvoked);
        }

        [TestCase(1025, false)]
        [TestCase(1024, true)]
        public void PathBoundsRejectBeforeInvocation(int length, bool admitted)
        {
            string path = new string('x', length);
            var result = ToJO(ExecuteMenuItem.HandleCommand(Request("inspect", path)));
            Assert.AreEqual(admitted, (bool)result["success"], result.ToString());
        }

        [UnityEngine.TestTools.UnityTest]
        public IEnumerator Dispatcher_InjectedTimeoutUnwrapsAndCleansObserver()
        {
            Seam("_existsOverride", (Func<string, bool?>)(_ => true));
            Seam("_enabledOverride", (Func<string, bool>)(_ => true));
            Seam("_executeOverride", (Func<string, bool>)(_ => true));
            Seam("_consoleOpenOverride", (Func<bool?>)(() => false));
            double now = 0;
            Seam("_clockOverride", (Func<double>)(() => now));
            var task = Dispatch(Request(path: "Window/General/Console", condition: "console_window_open"));
            Assert.IsFalse(task.IsCompleted, "The actual dispatcher must await the observation.");
            now = 3;
            var waiting = System.Diagnostics.Stopwatch.StartNew();
            while (!task.IsCompleted && waiting.Elapsed.TotalSeconds < 5)
            {
                EditorApplication.QueuePlayerLoopUpdate();
                yield return null;
            }
            Assert.IsTrue(
                task.IsCompleted,
                "Dispatcher task="
                    + task.Status
                    + "; active observers="
                    + typeof(ExecuteMenuItem).GetField("_activeObserverCount", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null)
            );
            var result = ToJO(task.GetAwaiter().GetResult());
            Assert.AreEqual("postcondition_timeout", (string)result["data"]["reason"]);
            Assert.IsTrue((bool)result["data"]["invocation"]["accepted"]);
            Assert.AreEqual(0, (int)typeof(ExecuteMenuItem).GetField("_activeObserverCount", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null));
        }

        [UnityEngine.TestTools.UnityTest]
        public IEnumerator Dispatcher_FirstPositiveObservationAfterDeadlineDoesNotClaimTimelyCompletion()
        {
            Seam("_existsOverride", (Func<string, bool?>)(_ => true));
            Seam("_enabledOverride", (Func<string, bool>)(_ => true));
            Seam("_executeOverride", (Func<string, bool>)(_ => true));
            bool open = false;
            Seam("_consoleOpenOverride", (Func<bool?>)(() => open));
            double now = 0;
            Seam("_clockOverride", (Func<double>)(() => now));
            var task = Dispatch(Request(path: "Window/General/Console", condition: "console_window_open"));
            Assert.IsFalse(task.IsCompleted);
            now = 3;
            open = true;
            var waiting = System.Diagnostics.Stopwatch.StartNew();
            while (!task.IsCompleted && waiting.Elapsed.TotalSeconds < 5)
            {
                EditorApplication.QueuePlayerLoopUpdate();
                yield return null;
            }
            Assert.IsTrue(
                task.IsCompleted,
                "Dispatcher task="
                    + task.Status
                    + "; active observers="
                    + typeof(ExecuteMenuItem).GetField("_activeObserverCount", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null)
            );
            var result = ToJO(task.GetAwaiter().GetResult());
            Assert.AreEqual("postcondition_timeout", (string)result["data"]["reason"], result.ToString());
            Assert.IsTrue((bool)result["data"]["invocation"]["accepted"]);
            Assert.AreEqual(0, (int)typeof(ExecuteMenuItem).GetField("_activeObserverCount", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null));
        }

        [UnityEngine.TestTools.UnityTest]
        public IEnumerator Dispatcher_InjectedCancellationNeverClaimsExecutionWasUndone()
        {
            Seam("_existsOverride", (Func<string, bool?>)(_ => true));
            Seam("_enabledOverride", (Func<string, bool>)(_ => true));
            int invocations = 0;
            Seam(
                "_executeOverride",
                (Func<string, bool>)(
                    _ =>
                    {
                        invocations++;
                        return true;
                    }
                )
            );
            Seam("_consoleOpenOverride", (Func<bool?>)(() => false));
            using var cancellation = new CancellationTokenSource();
            var task = Dispatch(Request(path: "Window/General/Console", condition: "console_window_open"), cancellation.Token);
            cancellation.Cancel();
            var waiting = System.Diagnostics.Stopwatch.StartNew();
            while (!task.IsCompleted && waiting.Elapsed.TotalSeconds < 5)
            {
                EditorApplication.QueuePlayerLoopUpdate();
                yield return null;
            }
            Assert.IsTrue(
                task.IsCompleted,
                "Dispatcher task="
                    + task.Status
                    + "; active observers="
                    + typeof(ExecuteMenuItem).GetField("_activeObserverCount", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null)
            );
            var result = ToJO(task.GetAwaiter().GetResult());
            Assert.AreEqual("observation_cancelled", (string)result["data"]["reason"]);
            Assert.IsTrue((bool)result["data"]["invocation"]["accepted"]);
            Assert.AreEqual(1, invocations);
            Assert.AreEqual(0, (int)typeof(ExecuteMenuItem).GetField("_activeObserverCount", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null));
        }

        [UnityEngine.TestTools.UnityTest]
        public IEnumerator ActualConsolePresenceIsObservedWithoutGeneralCompletionClaim()
        {
            var type = typeof(EditorWindow).Assembly.GetType("UnityEditor.ConsoleWindow");
            Assert.IsNotNull(type);
            bool wasOpen = UnityEngine.Resources.FindObjectsOfTypeAll(type).Length > 0;
            try
            {
                var task = Dispatch(Request(path: "Window/General/Console", condition: "console_window_open"));
                var waiting = System.Diagnostics.Stopwatch.StartNew();
                while (!task.IsCompleted && waiting.Elapsed.TotalSeconds < 5)
                {
                    EditorApplication.QueuePlayerLoopUpdate();
                    yield return null;
                }
                Assert.IsTrue(task.IsCompleted, "Dispatcher task=" + task.Status);
                var result = ToJO(task.GetAwaiter().GetResult());
                Assert.IsTrue((bool)result["success"], result.ToString());
                Assert.IsTrue((bool)result["data"]["completion"]["observed"]);
                Assert.Contains((string)result["data"]["completion"]["status"], new[] { "observed", "already_satisfied" });
                Assert.IsTrue(UnityEngine.Resources.FindObjectsOfTypeAll(type).Length > 0);
                TestContext.WriteLine("DEV11_CONSOLE_PRESENCE=" + result.ToString(Newtonsoft.Json.Formatting.None));
            }
            finally
            {
                if (!wasOpen)
                    foreach (var window in UnityEngine.Resources.FindObjectsOfTypeAll(type))
                        ((EditorWindow)window).Close();
            }
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(33)]
        public void InspectSelectionContextPreservesCompatIdsCountAndBound(int selectedCount)
        {
            var original = Selection.objects;
            var owned = new UnityEngine.GameObject[selectedCount];
            try
            {
                for (int i = 0; i < owned.Length; i++)
                    owned[i] = new UnityEngine.GameObject("DEV11 selection compatibility " + i);
                Selection.objects = owned;
                var selected = Selection.objects;
                Assert.AreEqual(selectedCount, selected.Length, "All owned objects must be selected before inspection.");
                Seam("_existsOverride", (Func<string, bool?>)(_ => true));
                Seam("_enabledOverride", (Func<string, bool>)(_ => true));
                var result = ToJO(ExecuteMenuItem.HandleCommand(Request("inspect")));
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                var context = result["data"]["context"];
                Assert.AreEqual(selectedCount, (int)context["selected_count"]);
                Assert.AreEqual(selectedCount > 32, (bool)context["selection_truncated"]);
                CollectionAssert.AreEqual(
                    selected.Take(32).Select(item => item.GetInstanceIDCompat()).ToArray(),
                    context["selected_ids"].Values<int>().ToArray()
                );
                Assert.IsFalse(_ownedMenuInvoked);
            }
            finally
            {
                Selection.objects = original;
                foreach (var item in owned)
                    if (item != null)
                        UnityEngine.Object.DestroyImmediate(item);
            }
        }

        [Test]
        public void Execute_MissingParam_ReturnsError()
        {
            var res = ExecuteMenuItem.HandleCommand(new JObject());
            var jo = ToJO(res);
            Assert.IsFalse((bool)jo["success"], "Expected success false");
            StringAssert.Contains("Required parameter", (string)jo["error"]);
        }

        [Test]
        public void Execute_Blacklisted_ReturnsError()
        {
            var res = ExecuteMenuItem.HandleCommand(new JObject { ["menuPath"] = "File/Quit" });
            var jo = ToJO(res);
            Assert.IsFalse((bool)jo["success"], "Expected success false for blacklisted menu");
            StringAssert.Contains("blocked for safety", (string)jo["error"], "Expected blacklist message");
        }

        [Test]
        public void Execute_OwnedHarmlessMenu_InvokesOnlyManagedCallback()
        {
            try
            {
                var jo = ToJO(ExecuteMenuItem.HandleCommand(new JObject { ["menuPath"] = OwnedMenuPath }));
                Assert.IsTrue((bool)jo["success"], jo.ToString());
                Assert.IsTrue(_ownedMenuInvoked, "The exact owned callback must execute synchronously.");
            }
            finally
            {
                _ownedMenuInvoked = false;
            }
        }

        [Test]
        public void Execute_NullParameters_ReturnsError()
        {
            Assert.IsFalse(ToJO(ExecuteMenuItem.HandleCommand(null)).Value<bool>("success"));
            Assert.IsFalse(_ownedMenuInvoked);
        }

        [TestCase("{}")]
        [TestCase("{\"menu_path\":null,\"menuPath\":\"Fixture/A\"}")]
        [TestCase("{\"menuPath\":\"\"}")]
        [TestCase("{\"menu_path\":\"   \"}")]
        [TestCase("{\"menu_path\":42}")]
        [TestCase("{\"menuPath\":false}")]
        [TestCase("{\"menu_path\":{\"path\":\"Fixture/A\"}}")]
        [TestCase("{\"menuPath\":[\"Fixture/A\"]}")]
        public void PathParserRejectsInvalidInputsWithoutMenuExecution(string json)
        {
            var parameters = JObject.Parse(json);
            string before = parameters.ToString();
            var method = typeof(ExecuteMenuItem).GetMethod("TryGetMenuPath", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            object[] arguments = { parameters, null, null };
            Assert.IsFalse((bool)method.Invoke(null, arguments));
            Assert.IsFalse(ToJO(arguments[2]).Value<bool>("success"));
            StringAssert.Contains("menu_path", ToJO(arguments[2]).Value<string>("error"));
            Assert.AreEqual(before, parameters.ToString());
            Assert.IsFalse(_ownedMenuInvoked);
        }

        [TestCase("{\"menu_path\":\"Fixture/A\"}", "Fixture/A")]
        [TestCase("{\"menuPath\":\"Fixture/Z\"}", "Fixture/Z")]
        [TestCase("{\"menu_path\":\"Fixture/A\",\"menuPath\":\"Fixture/Z\"}", "Fixture/A")]
        [TestCase("{\"menu_path\":\" Fixture/A \"}", " Fixture/A ")]
        public void PathParserPreservesStringsAndPrecedence(string json, string expected)
        {
            var parameters = JObject.Parse(json);
            string before = parameters.ToString();
            var method = typeof(ExecuteMenuItem).GetMethod("TryGetMenuPath", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            object[] arguments = { parameters, null, null };
            Assert.IsTrue((bool)method.Invoke(null, arguments));
            Assert.AreEqual(expected, arguments[1]);
            Assert.IsNull(arguments[2]);
            Assert.AreEqual(before, parameters.ToString());
            Assert.IsFalse(_ownedMenuInvoked);
        }

        [TestCase("{}", false)]
        [TestCase("{\"refresh\":null}", false)]
        [TestCase("{\"refresh\":false}", false)]
        [TestCase("{\"refresh\":0}", false)]
        [TestCase("{\"refresh\":\"false\"}", false)]
        [TestCase("{\"refresh\":true}", true)]
        [TestCase("{\"refresh\":1}", true)]
        [TestCase("{\"refresh\":\"true\"}", true)]
        public void RefreshParserPreservesDefaultsAndCoercions(string json, bool expected)
        {
            var parameters = JObject.Parse(json);
            string before = parameters.ToString();
            var method = typeof(GetMenuItems).GetMethod("TryReadRefresh", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            object[] arguments = { parameters, false, null };
            Assert.IsTrue((bool)method.Invoke(null, arguments));
            Assert.AreEqual(expected, arguments[1]);
            Assert.IsNull(arguments[2]);
            Assert.AreEqual(before, parameters.ToString());
        }

        [TestCase("{\"refresh\":\"invalid\"}")]
        [TestCase("{\"refresh\":{}}")]
        [TestCase("{\"refresh\":[]}")]
        public void RefreshParserRejectsMalformedValuesWithoutDiscovery(string json)
        {
            var parameters = JObject.Parse(json);
            string before = parameters.ToString();
            var method = typeof(GetMenuItems).GetMethod("TryReadRefresh", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            object[] arguments = { parameters, false, null };
            Assert.IsFalse((bool)method.Invoke(null, arguments));
            Assert.IsFalse(ToJO(arguments[2]).Value<bool>("success"));
            StringAssert.Contains("refresh", ToJO(arguments[2]).Value<string>("error"));
            Assert.AreEqual(before, parameters.ToString());
        }
    }

    [Parallelizable(ParallelScope.None)]
    public class MenuAliasContractTests
    {
        private static readonly FieldInfo ToolService = typeof(MCPServiceLocator).GetField(
            "_toolDiscoveryService",
            BindingFlags.NonPublic | BindingFlags.Static
        );
        private static readonly FieldInfo ResourceService = typeof(MCPServiceLocator).GetField(
            "_resourceDiscoveryService",
            BindingFlags.NonPublic | BindingFlags.Static
        );
        private readonly AliasPolicy _policy = new();
        private readonly Dictionary<string, object> _previousSeams = new();
        private object _previousTools;
        private object _previousResources;
        private int _lookups;
        private int _executions;

        [SetUp]
        public void SetUp()
        {
            _previousTools = ToolService.GetValue(null);
            _previousResources = ResourceService.GetValue(null);
            MCPServiceLocator.Register<IToolDiscoveryService>(_policy);
            MCPServiceLocator.Register<IResourceDiscoveryService>(new NoResources());
            _policy.ParentEnabled = _policy.AliasEnabled = true;
            _lookups = _executions = 0;
            foreach (string name in new[] { "_existsOverride", "_enabledOverride", "_executeOverride", "_consoleOpenOverride", "_clockOverride" })
                _previousSeams[name] = Seam(name).GetValue(null);
            Seam("_existsOverride")
                .SetValue(
                    null,
                    (Func<string, bool?>)(
                        _ =>
                        {
                            _lookups++;
                            return true;
                        }
                    )
                );
            Seam("_enabledOverride").SetValue(null, (Func<string, bool>)(_ => true));
            Seam("_executeOverride")
                .SetValue(
                    null,
                    (Func<string, bool>)(
                        _ =>
                        {
                            _executions++;
                            return true;
                        }
                    )
                );
            Seam("_consoleOpenOverride").SetValue(null, (Func<bool?>)(() => true));
        }

        [TearDown]
        public void TearDown()
        {
            typeof(ExecuteMenuItem).GetMethod("InterruptObservations", BindingFlags.NonPublic | BindingFlags.Static)?.Invoke(null, null);
            foreach (var previous in _previousSeams)
                Seam(previous.Key).SetValue(null, previous.Value);
            _previousSeams.Clear();
            ToolService.SetValue(null, _previousTools);
            ResourceService.SetValue(null, _previousResources);
        }

        private static FieldInfo Seam(string name) => typeof(ExecuteMenuItem).GetField(name, BindingFlags.NonPublic | BindingFlags.Static);

        [TestCase("InspectMenuItem", "inspect_menu_item")]
        [TestCase("ExecuteMenuItemObserved", "execute_menu_item_observed")]
        public void AliasHasRealConservativeMetadataWithoutSecondPublicRegistration(string className, string command)
        {
            var type = typeof(ExecuteMenuItem).Assembly.GetType("MCPForUnity.Editor.Tools." + className);
            Assert.IsNotNull(type, "A distinct native route must exist before forwarding new semantics.");
            var attribute = type.GetCustomAttribute<McpForUnityToolAttribute>();
            Assert.IsNotNull(attribute);
            var extract = typeof(ToolDiscoveryService).GetMethod("ExtractToolMetadata", BindingFlags.NonPublic | BindingFlags.Instance);
            var metadata = (ToolMetadata)extract.Invoke(new ToolDiscoveryService(), new object[] { type, attribute });
            Assert.AreEqual(command, metadata.Name);
            Assert.IsFalse(metadata.AutoRegister);
            Assert.IsTrue(metadata.IsBuiltIn);
            Assert.IsTrue(metadata.RequiresExplicitConsent);
            Assert.AreEqual("core", metadata.Group);
            Assert.IsFalse(metadata.RequiresPolling);
            CommandRegistry.Initialize();
            Assert.IsTrue(CommandRegistry.HasHandler(command));
        }

        [Test]
        public void InspectAliasCannotBeTurnedIntoExecuteAndDoesNotMutateInput()
        {
            CommandRegistry.Initialize();
            var request = new JObject { ["menuPath"] = "Window/General/Console", ["action"] = "execute" };
            string before = request.ToString();
            var result = JObject.FromObject(CommandRegistry.GetHandler("inspect_menu_item")(request));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("inspect", (string)result["data"]["action"]);
            Assert.IsFalse((bool)result["data"]["invocation"]["attempted"]);
            Assert.AreEqual(0, _executions);
            Assert.AreEqual(before, request.ToString());
        }

        [TestCase("{\"action\":\"inspect\",\"wait_for\":\"console_window_open\"}")]
        [TestCase("{\"action\":false,\"wait_for\":\"console_window_open\"}")]
        [TestCase("{\"wait_for\":\"none\"}")]
        [TestCase("{}")]
        public void ObservedAliasRejectsMalformedIntentBeforeLookupOrExecution(string options)
        {
            CommandRegistry.Initialize();
            var request = new JObject { ["menuPath"] = "Window/General/Console" };
            request.Merge(JObject.Parse(options));
            var pending = CommandRegistry.InvokeCommandAsync("execute_menu_item_observed", request);
            Assert.IsTrue(pending.IsCompleted, "Malformed observed requests must refuse synchronously.");
            Assert.IsFalse(JObject.FromObject(pending.GetAwaiter().GetResult()).Value<bool>("success"));
            Assert.AreEqual(0, _lookups);
            Assert.AreEqual(0, _executions);
        }

        [TestCase("inspect_menu_item", true, true)]
        [TestCase("inspect_menu_item", false, true)]
        [TestCase("inspect_menu_item", true, false)]
        [TestCase("execute_menu_item_observed", true, true)]
        [TestCase("execute_menu_item_observed", false, true)]
        [TestCase("execute_menu_item_observed", true, false)]
        public void ActualDispatcherPreservesAliasAndParentPolicy(string command, bool parentEnabled, bool aliasEnabled)
        {
            _policy.ParentEnabled = parentEnabled;
            _policy.AliasEnabled = aliasEnabled;
            var request = new JObject { ["menuPath"] = "Window/General/Console", ["action"] = "execute" };
            if (command == "execute_menu_item_observed")
                request["wait_for"] = "console_window_open";
            using var cancellation = new CancellationTokenSource();
            var pending = TransportCommandDispatcher.ExecuteCommandJsonAsync(
                new JObject { ["type"] = command, ["params"] = request }.ToString(),
                cancellation.Token
            );
            try
            {
                Assert.IsTrue(pending.IsCompleted, "Inspection, refusal and already-satisfied observation settle during the real main-thread dispatcher pump.");
                var envelope = JObject.Parse(pending.GetAwaiter().GetResult());
                var response = envelope["result"] as JObject ?? envelope;
                bool allowed = parentEnabled && aliasEnabled;
                Assert.AreEqual(allowed, response.Value<bool?>("success") ?? false, envelope.ToString());
                Assert.AreEqual(allowed ? 1 : 0, _lookups);
                Assert.AreEqual(allowed && command == "execute_menu_item_observed" ? 1 : 0, _executions);
                if (allowed && command == "inspect_menu_item")
                    Assert.IsFalse((bool)response["data"]["invocation"]["attempted"]);
            }
            finally
            {
                cancellation.Cancel();
            }
        }

        private sealed class AliasPolicy : IToolDiscoveryService
        {
            internal bool ParentEnabled = true;
            internal bool AliasEnabled = true;

            public bool IsToolEnabled(string name) =>
                name == "execute_menu_item" ? ParentEnabled : (name == "inspect_menu_item" || name == "execute_menu_item_observed") && AliasEnabled;

            public ToolMetadata GetToolMetadata(string name) =>
                name == "execute_menu_item" || name == "inspect_menu_item" || name == "execute_menu_item_observed"
                    ? new ToolMetadata
                    {
                        Name = name,
                        AutoRegister = false,
                        RequiresExplicitConsent = true,
                        Group = "core",
                    }
                    : null;

            public List<ToolMetadata> DiscoverAllTools() => new();

            public List<ToolMetadata> GetEnabledTools() => new();

            public void SetToolEnabled(string name, bool enabled) => throw new InvalidOperationException("Tests cannot write preferences.");

            public void InvalidateCache() { }
        }

        private sealed class NoResources : IResourceDiscoveryService
        {
            public ResourceMetadata GetResourceMetadata(string name) => null;

            public bool IsResourceEnabled(string name) => false;

            public List<ResourceMetadata> DiscoverAllResources() => new();

            public List<ResourceMetadata> GetEnabledResources() => new();

            public void SetResourceEnabled(string name, bool enabled) => throw new InvalidOperationException("Tests cannot write preferences.");

            public void InvalidateCache() { }
        }
    }

    // Opt-in only: the ordinary fixture never requests a reload.
    [TestFixture]
    public class ExecuteMenuItemReloadProbeTests
    {
        private const string Prefix = "MCPForUnity.DEV11.MenuReload.";
        private const string Active = Prefix + "active";
        private const string Domain = Prefix + "domain";
        private const string Witness = Prefix + "witness";
        private const string Accepted = Prefix + "accepted";
        private const string Callback = Prefix + "callback";
        private const string CallbackCount = Prefix + "callback_count";
        private const string Cleaned = Prefix + "cleaned";
        private const string Interrupted = Prefix + "interrupted";
        private const string Diagnostic = Prefix + "diagnostic";
        private const string OwnedReloadPath = "Tools/MCPForUnityTests/Owned DEV11 Reload 2cf38f";
        private static readonly string DomainNonce = Guid.NewGuid().ToString("N");
        private static Task<object> _pendingObservation = null;

        [MenuItem(OwnedReloadPath)]
        private static void InvokeOwnedReload()
        {
            // Even a manual menu click cannot reload an unopted-in project.
            if (!SessionState.GetBool(Active, false) || !IsOwnedProject())
                return;
            SessionState.SetBool(Callback, true);
            SessionState.SetInt(CallbackCount, SessionState.GetInt(CallbackCount, 0) + 1);
            EditorUtility.RequestScriptReload();
        }

        [UnityEngine.TestTools.UnityTest]
        [Explicit("Only the serial QA owner may run this in the exact opted-in disposable project.")]
        public IEnumerator ActualReloadMenu_InvokesOnceAndInterruptsOwnedObservation()
        {
            try
            {
                PrepareProbe();
                yield return new UnityEngine.TestTools.WaitForDomainReload();
                VerifyProbe();
            }
            finally
            {
                CleanupProbe();
            }
        }

        private static bool IsOwnedProject()
        {
            string expected = Environment.GetEnvironmentVariable("DEV11_RELOAD_PROBE_PROJECT");
            if (string.IsNullOrEmpty(expected))
                return false;
            string actual = System.IO.Directory.GetParent(UnityEngine.Application.dataPath).FullName;
            return string.Equals(
                System.IO.Path.GetFullPath(expected).TrimEnd('/', '\\'),
                System.IO.Path.GetFullPath(actual).TrimEnd('/', '\\'),
                StringComparison.OrdinalIgnoreCase
            );
        }

        private static void SetSeam(string name, object value)
        {
            var field = typeof(ExecuteMenuItem).GetField(name, BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(field, name);
            field.SetValue(null, value);
        }

        private static void PrepareProbe()
        {
            Assert.IsTrue(IsOwnedProject(), "DEV11_RELOAD_PROBE_PROJECT must equal this disposable project path.");
            Assert.IsFalse(SessionState.GetBool(Active, false), "Another owned probe is already active.");
            SessionState.SetBool(Active, true);
            SessionState.SetString(Domain, DomainNonce);
            SessionState.SetString(Witness, "");
            SessionState.SetString(Diagnostic, "");
            SessionState.SetInt(CallbackCount, 0);
            foreach (string key in new[] { Accepted, Callback, Cleaned, Interrupted })
                SessionState.SetBool(key, false);

            SetSeam("_existsOverride", (Func<string, bool?>)(_ => true));
            SetSeam("_enabledOverride", (Func<string, bool>)(_ => true));
            SetSeam("_executeOverride", (Func<string, bool>)(_ => true));
            SetSeam("_consoleOpenOverride", (Func<bool?>)(() => false));
            // The raw cooperative task is inspected before the reload event returns;
            // ordinary tests separately prove the registry's async unwrapping.
            _pendingObservation = ExecuteMenuItem.HandleCommand(
                new JObject
                {
                    ["menuPath"] = "Window/General/Console",
                    ["wait_for"] = "console_window_open",
                    ["timeout_ms"] = 10000,
                },
                CancellationToken.None
            );
            Assert.IsFalse(_pendingObservation.IsCompleted);
            AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;

            SetSeam("_executeOverride", null);
            var response = JObject.FromObject(ExecuteMenuItem.HandleCommand(new JObject { ["menuPath"] = OwnedReloadPath }));
            Assert.IsTrue((bool)response["success"], response.ToString());
            Assert.IsTrue((bool)response["data"]["invocation"]["accepted"]);
            Assert.AreEqual("not_observed", (string)response["data"]["completion"]["status"]);
            SessionState.SetBool(Accepted, true);
        }

        private static void BeforeReload()
        {
            if (!SessionState.GetBool(Active, false))
                return;
            SessionState.SetString(Witness, DomainNonce);
            try
            {
                if (_pendingObservation != null && _pendingObservation.IsCompleted)
                {
                    var response = JObject.FromObject(_pendingObservation.GetAwaiter().GetResult());
                    SessionState.SetBool(Interrupted, (string)response["data"]?["reason"] == "observation_interrupted");
                }
                var field = typeof(ExecuteMenuItem).GetField("_activeObserverCount", BindingFlags.NonPublic | BindingFlags.Static);
                SessionState.SetBool(Cleaned, field?.GetValue(null) is int count && count == 0);
            }
            catch (Exception e)
            {
                SessionState.SetString(Diagnostic, e.Message.Length <= 512 ? e.Message : e.Message.Substring(0, 512));
            }
        }

        private static void VerifyProbe()
        {
            // No iterator locals or pre-reload task/static objects are read here.
            Assert.IsTrue(SessionState.GetBool(Active, false));
            Assert.AreEqual(SessionState.GetString(Domain, ""), SessionState.GetString(Witness, ""));
            Assert.AreNotEqual(SessionState.GetString(Domain, ""), DomainNonce);
            Assert.IsTrue(SessionState.GetBool(Callback, false), "The actual menu callback must request the reload.");
            Assert.AreEqual(1, SessionState.GetInt(CallbackCount, 0));
            Assert.IsTrue(SessionState.GetBool(Accepted, false));
            Assert.IsTrue(SessionState.GetBool(Interrupted, false), SessionState.GetString(Diagnostic, ""));
            Assert.IsTrue(SessionState.GetBool(Cleaned, false), SessionState.GetString(Diagnostic, ""));
            TestContext.WriteLine(
                "DEV11_ACTUAL_RELOAD="
                    + new JObject
                    {
                        ["fresh_domain"] = true,
                        ["before_reload_witness"] = true,
                        ["actual_menu_callback"] = true,
                        ["actual_menu_callback_count"] = 1,
                        ["invocation_accepted_completion_not_observed"] = true,
                        ["pending_injected_observation_interrupted"] = true,
                        ["before_reload_cleanup"] = true,
                        ["network_response_survival_proven"] = false,
                    }.ToString(Newtonsoft.Json.Formatting.None)
            );
        }

        private static void CleanupProbe()
        {
            if (!IsOwnedProject())
                return;
            AssemblyReloadEvents.beforeAssemblyReload -= BeforeReload;
            typeof(ExecuteMenuItem).GetMethod("InterruptObservations", BindingFlags.NonPublic | BindingFlags.Static)?.Invoke(null, null);
            foreach (string name in new[] { "_existsOverride", "_enabledOverride", "_executeOverride", "_consoleOpenOverride", "_clockOverride" })
                typeof(ExecuteMenuItem).GetField(name, BindingFlags.NonPublic | BindingFlags.Static)?.SetValue(null, null);
            _pendingObservation = null;
            SessionState.EraseInt(CallbackCount);
            foreach (string key in new[] { Domain, Witness, Diagnostic })
                SessionState.EraseString(key);
            foreach (string key in new[] { Active, Accepted, Callback, Cleaned, Interrupted })
                SessionState.EraseBool(key);
        }
    }
}
