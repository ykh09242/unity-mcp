using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool("execute_menu_item", AutoRegister = false, RequiresExplicitConsent = true)]
    /// <summary>
    /// Tool to execute a Unity Editor menu item by its path.
    /// </summary>
    public static class ExecuteMenuItem
    {
        // Basic blacklist to prevent execution of disruptive menu items.
        private static readonly HashSet<string> _menuPathBlacklist = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "File/Quit" };
        private const string ConsolePath = "Window/General/Console";
        private static readonly HashSet<Observation> Observations = new();
        private static int _activeObserverCount;

        // Narrow injectable API boundaries; null always selects the actual Editor API.
        private static Func<string, bool?> _existsOverride = null;
        private static Func<string, bool> _enabledOverride = null;
        private static Func<string, bool> _executeOverride = null;
        private static Func<bool?> _consoleOpenOverride = null;
        private static Func<double> _clockOverride = null;

        internal static object HandleInspection(JObject parameters)
        {
            var policyError = ParentPolicyError();
            if (policyError != null)
                return policyError;
            var read = parameters == null ? new JObject() : (JObject)parameters.DeepClone();
            read["action"] = "inspect";
            return Begin(read, CancellationToken.None);
        }

        internal static Task<object> HandleObserved(JObject parameters, CancellationToken cancellationToken)
        {
            if (cancellationToken.IsCancellationRequested)
                return Task.FromCanceled<object>(cancellationToken);
            var policyError = ParentPolicyError();
            if (policyError != null)
                return Task.FromResult<object>(policyError);
            JToken action = parameters?["action"];
            JToken condition = parameters?["wait_for"];
            if (
                (action != null && (action.Type != JTokenType.String || (string)action != "execute"))
                || condition?.Type != JTokenType.String
                || (string)condition != "console_window_open"
            )
                return Task.FromResult<object>(
                    new ErrorResponse(
                        "Observed menu execution requires action execute (or omitted) and wait_for console_window_open.",
                        new { reason = "invalid_observed_request" }
                    )
                );
            return HandleCommand(parameters, cancellationToken);
        }

        private static ErrorResponse ParentPolicyError()
        {
            try
            {
                return MCPServiceLocator.ToolDiscovery.IsToolEnabled("execute_menu_item")
                    ? null
                    : new ErrorResponse(
                        "Tool 'execute_menu_item' is disabled in the Unity Editor.",
                        new { reason = "tool_disabled", tool = "execute_menu_item" }
                    );
            }
            catch (Exception e)
            {
                return new ErrorResponse(
                    "The original menu capability's policy could not be established; no menu was inspected or invoked.",
                    new { reason = "policy_unavailable", diagnostic = Bounded(e.GetBaseException().Message) }
                );
            }
        }

        public static object HandleCommand(JObject @params) => Begin(@params, CancellationToken.None);

        public static Task<object> HandleCommand(JObject @params, CancellationToken cancellationToken)
        {
            if (cancellationToken.IsCancellationRequested)
                return Task.FromCanceled<object>(cancellationToken);
            object result = Begin(@params, cancellationToken);
            return result is Task<object> pending ? pending : Task.FromResult(result);
        }

        private static object Begin(JObject @params, CancellationToken cancellationToken)
        {
            if (!TryGetMenuPath(@params, out string menuPath, out ErrorResponse inputError))
                return inputError;
            if (!TryOptions(@params, menuPath, out string action, out string condition, out int timeout, out inputError))
                return inputError;

            var data = Describe(menuPath, action, condition);
            if (action == "inspect")
                return new SuccessResponse("Menu evidence inspected; no execution callback invoked.", data);
            if ((bool)data["policy"]["blocked"])
                return Refuse(data, "blocked", $"Execution of menu item '{menuPath}' is blocked for safety reasons.");
            if (!(bool)data["policy"]["context_supported"])
                return Refuse(data, "context_required", "CONTEXT menu execution requires an explicit context and is unsupported by this tool.");
            if (data["existence"]["value"].Type == JTokenType.Boolean && !(bool)data["existence"]["value"])
                return Refuse(data, "not_found", "The native menu registry reports this exact path is absent.");
            if ((string)data["availability"]["status"] == "disabled")
                return Refuse(data, "disabled", "The menu item is currently disabled.");

            bool? before = null;
            if (condition != "none")
            {
                try
                {
                    before = ConsoleOpen();
                }
                catch (Exception e)
                {
                    data["diagnostic"] = Bounded(e.GetBaseException().Message);
                }
                if (!before.HasValue)
                    return Refuse(data, "unsupported_completion", "Console-window presence cannot be observed in this Editor.");
                if (_activeObserverCount >= 8)
                    return Refuse(data, "observation_capacity", "Eight menu observations are already active; invocation was not attempted.");
            }

            Observation observation = null;
            try
            {
                // Reserve and subscribe before invocation so admission failure cannot follow a side effect.
                if (condition != "none")
                    observation = new Observation(data, before.Value, timeout, cancellationToken);
                cancellationToken.ThrowIfCancellationRequested();
                data["invocation"]["attempted"] = true;
                bool accepted = (_executeOverride ?? EditorApplication.ExecuteMenuItem)(menuPath);
                data["invocation"]["accepted"] = accepted;
                if (!accepted)
                {
                    observation?.Dispose();
                    return Refuse(data, "invocation_refused", "Unity refused the menu invocation; absence, disabled state or context may be responsible.");
                }
                if (observation == null)
                    return new SuccessResponse("Menu invocation accepted; downstream completion was not observed.", data);
                return observation.Start();
            }
            catch (Exception e)
            {
                observation?.Dispose();
                if (e is OperationCanceledException && !(bool)data["invocation"]["attempted"])
                    return Refuse(data, "cancelled_before_invocation", "Observation cancelled before menu invocation.");
                data["diagnostic"] = Bounded(e.GetBaseException().Message);
                return Refuse(data, "invocation_outcome_unknown", "The menu API failed; its downstream outcome is unknown.");
            }
        }

        private static bool TryOptions(JObject parameters, string path, out string action, out string condition, out int timeout, out ErrorResponse error)
        {
            action = "execute";
            condition = "none";
            timeout = 2000;
            error = null;
            JToken suppliedAction = parameters?["action"];
            if (suppliedAction != null)
            {
                if (suppliedAction.Type != JTokenType.String || !new[] { "execute", "inspect", "exists" }.Contains((string)suppliedAction))
                {
                    error = new ErrorResponse("Invalid action: expected execute, inspect or exists.");
                    return false;
                }
                action = (string)suppliedAction == "exists" ? "inspect" : (string)suppliedAction;
            }
            JToken suppliedCondition = parameters?["wait_for"];
            if (suppliedCondition != null)
            {
                if (suppliedCondition.Type != JTokenType.String || !new[] { "none", "console_window_open" }.Contains((string)suppliedCondition))
                {
                    error = new ErrorResponse("Invalid wait_for: expected none or console_window_open.");
                    return false;
                }
                condition = (string)suppliedCondition;
            }
            if (condition != "none" && (action != "execute" || path != ConsolePath))
            {
                error = new ErrorResponse("Unsupported completion: console_window_open is allowed only for Window/General/Console execution.");
                return false;
            }
            JToken suppliedTimeout = parameters?["timeout_ms"];
            if (
                suppliedTimeout != null
                && (
                    condition == "none"
                    || suppliedTimeout.Type != JTokenType.Integer
                    || !int.TryParse(suppliedTimeout.ToString(), NumberStyles.None, CultureInfo.InvariantCulture, out timeout)
                    || timeout < 1
                    || timeout > 10000
                )
            )
            {
                error = new ErrorResponse("Invalid timeout_ms: integer 1..10000 is permitted only with an allowlisted wait_for.");
                return false;
            }
            return true;
        }

        private static JObject Describe(string path, string action, string condition)
        {
            bool blocked = _menuPathBlacklist.Contains(path);
            bool context = path.StartsWith("CONTEXT/", StringComparison.OrdinalIgnoreCase);
            bool? exists = null;
            bool? enabled = null;
            string source = "unavailable";
            string availability =
                blocked ? "blocked"
                : context ? "context_required"
                : "unknown";
            string diagnostic = null;
            if (!blocked)
            {
                try
                {
                    var method = typeof(Menu).GetMethod("MenuItemExists", BindingFlags.NonPublic | BindingFlags.Static, null, new[] { typeof(string) }, null);
                    exists =
                        context ? null
                        : _existsOverride != null ? _existsOverride(path)
                        : method?.ReturnType == typeof(bool) ? (bool?)method.Invoke(null, new object[] { path })
                        : null;
                    if (exists.HasValue)
                        source = "native_registry";
                }
                catch (Exception e)
                {
                    diagnostic = Bounded(e.GetBaseException().Message);
                }
                if (!exists.HasValue)
                {
                    try
                    {
                        bool declared = TypeCache
                            .GetMethodsWithAttribute<MenuItem>()
                            .Any(method =>
                                method
                                    .GetCustomAttributes(typeof(MenuItem), false)
                                    .Cast<MenuItem>()
                                    .Any(attribute => !attribute.validate && attribute.menuItem == path)
                            );
                        if (declared)
                        {
                            exists = true;
                            source = "managed_declaration";
                        }
                    }
                    catch (Exception e)
                    {
                        diagnostic = Bounded(e.GetBaseException().Message);
                    }
                }
                if (!context && exists != false)
                {
                    try
                    {
                        bool rawEnabled = (_enabledOverride ?? Menu.GetEnabled)(path);
                        if (rawEnabled && !exists.HasValue)
                        {
                            exists = true;
                            source = "enabled_native_observation";
                        }
                        if (exists == true)
                        {
                            enabled = rawEnabled;
                            availability = rawEnabled ? "enabled" : "disabled";
                        }
                    }
                    catch (Exception e)
                    {
                        diagnostic = Bounded(e.GetBaseException().Message);
                    }
                }
                else if (!context && exists == false)
                    availability = "not_found";
            }
            var selected = Selection.objects;
            var data = new JObject
            {
                ["menu_path"] = path,
                ["action"] = action,
                ["existence"] = new JObject
                {
                    ["value"] = exists,
                    ["source"] = source,
                    ["status"] = exists.HasValue ? "known" : "unknown",
                },
                ["availability"] = new JObject
                {
                    ["enabled"] = enabled,
                    ["status"] = availability,
                    ["source"] = enabled.HasValue ? "Menu.GetEnabled" : "unavailable",
                },
                ["policy"] = new JObject { ["blocked"] = blocked, ["context_supported"] = !context },
                ["invocation"] = new JObject { ["attempted"] = false, ["accepted"] = JValue.CreateNull() },
                ["completion"] = new JObject
                {
                    ["condition"] = condition,
                    ["status"] = "not_observed",
                    ["observed"] = JValue.CreateNull(),
                },
                ["context"] = new JObject
                {
                    ["selected_ids"] = new JArray(selected.Take(32).Select(item => item.GetInstanceIDCompat())),
                    ["selected_count"] = selected.Length,
                    ["selection_truncated"] = selected.Length > 32,
                    ["focused_window_type"] = Bounded(EditorWindow.focusedWindow?.GetType().FullName),
                    ["observed_at_unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                },
            };
            if (diagnostic != null)
                data["diagnostic"] = diagnostic;
            return data;
        }

        private static object Refuse(JObject data, string reason, string message)
        {
            data["reason"] = reason;
            return new ErrorResponse(message, data);
        }

        private static string Bounded(string value) => value == null || value.Length <= 512 ? value : value.Substring(0, 512);

        private static double Now() => _clockOverride?.Invoke() ?? EditorApplication.timeSinceStartup;

        private static bool? ConsoleOpen()
        {
            if (_consoleOpenOverride != null)
                return _consoleOpenOverride();
            var type = typeof(EditorWindow).Assembly.GetType("UnityEditor.ConsoleWindow");
            return type == null ? null : UnityEngine.Resources.FindObjectsOfTypeAll(type).Length > 0;
        }

        private static void InterruptObservations()
        {
            foreach (var observation in Observations.ToArray())
                observation.Interrupt();
        }

        private sealed class Observation : IDisposable
        {
            private readonly JObject _data;
            private readonly bool _before;
            private readonly double _started;
            private readonly double _deadline;
            private readonly CancellationToken _token;
            private readonly TaskCompletionSource<object> _completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
            private bool _disposed;

            internal Observation(JObject data, bool before, int timeout, CancellationToken token)
            {
                _data = data;
                _before = before;
                _token = token;
                _started = Now();
                _deadline = _started + timeout / 1000d;
                Observations.Add(this);
                _activeObserverCount++;
                EditorApplication.update += Tick;
                AssemblyReloadEvents.beforeAssemblyReload += Interrupt;
                EditorApplication.quitting += Interrupt;
            }

            internal Task<object> Start()
            {
                Tick();
                return _completion.Task;
            }

            private void Finish(bool success, string status, bool? observed, string reason = null)
            {
                var completion = (JObject)_data["completion"];
                completion["status"] = status;
                completion["observed"] = observed;
                completion["before"] = _before;
                completion["elapsed_ms"] = Math.Max(0, (Now() - _started) * 1000);
                Dispose();
                _completion.TrySetResult(
                    success
                        ? new SuccessResponse("The allowlisted Console-window presence condition is satisfied.", _data)
                        : Refuse(_data, reason, "Menu invocation was attempted; observation ended without proving completion and execution was not undone.")
                );
            }

            private void Tick()
            {
                if (_disposed)
                    return;
                if (_token.IsCancellationRequested)
                {
                    Finish(false, "cancelled", null, "observation_cancelled");
                    return;
                }
                if (Now() >= _deadline)
                {
                    Finish(false, "timeout", false, "postcondition_timeout");
                    return;
                }
                try
                {
                    bool? open = ConsoleOpen();
                    if (!open.HasValue)
                        Finish(false, "unavailable", null, "observation_unavailable");
                    else if (open.Value)
                        Finish(true, _before ? "already_satisfied" : "observed", true);
                }
                catch (Exception e)
                {
                    _data["diagnostic"] = Bounded(e.GetBaseException().Message);
                    Finish(false, "unavailable", null, "observation_unavailable");
                }
            }

            internal void Interrupt()
            {
                if (!_disposed)
                    Finish(false, "interrupted", null, "observation_interrupted");
            }

            public void Dispose()
            {
                if (_disposed)
                    return;
                _disposed = true;
                EditorApplication.update -= Tick;
                AssemblyReloadEvents.beforeAssemblyReload -= Interrupt;
                EditorApplication.quitting -= Interrupt;
                Observations.Remove(this);
                _activeObserverCount--;
            }
        }

        private static bool TryGetMenuPath(JObject @params, out string menuPath, out ErrorResponse error)
        {
            menuPath = null;
            error = null;
            JToken path = @params?["menu_path"] ?? @params?["menuPath"];
            if (path != null && path.Type != JTokenType.String && path.Type != JTokenType.Null)
            {
                error = new ErrorResponse("Parameter 'menu_path' or 'menuPath' must be a string.");
                return false;
            }

            menuPath = path?.Value<string>();
            if (string.IsNullOrWhiteSpace(menuPath))
            {
                error = new ErrorResponse("Required parameter 'menu_path' or 'menuPath' is missing or empty.");
                return false;
            }
            if (menuPath.Length > 1024 || Encoding.UTF8.GetByteCount(menuPath) > 4096 || menuPath.IndexOfAny(new[] { '\0', '\r', '\n' }) >= 0)
            {
                error = new ErrorResponse("Invalid menu_path: maximum 1024 characters/4096 UTF-8 bytes, without NUL or newlines.");
                return false;
            }
            return true;
        }
    }

    // Distinct native routes prevent old plugins from ignoring new options and executing.
    // They are built-in backend commands, not additional public Python tools.
    [McpForUnityTool("inspect_menu_item", AutoRegister = false, RequiresExplicitConsent = true, Group = "core")]
    public static class InspectMenuItem
    {
        public static object HandleCommand(JObject parameters) => ExecuteMenuItem.HandleInspection(parameters);
    }

    [McpForUnityTool("execute_menu_item_observed", AutoRegister = false, RequiresExplicitConsent = true, Group = "core")]
    public static class ExecuteMenuItemObserved
    {
        public static Task<object> HandleCommand(JObject parameters, CancellationToken cancellationToken) =>
            ExecuteMenuItem.HandleObserved(parameters, cancellationToken);
    }
}
