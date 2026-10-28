using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools.Capabilities;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Holds information about a registered command handler.
    /// </summary>
    class HandlerInfo
    {
        public string CommandName { get; }
        public Func<JObject, object> SyncHandler { get; }
        public Func<JObject, Task<object>> AsyncHandler { get; }
        public Func<JObject, CancellationToken, Task<object>> CooperativeAsyncHandler { get; private set; }
        public Func<JObject, CancellationToken, ICommandProgress, Task<object>> ProgressiveAsyncHandler { get; private set; }

        internal Func<JObject, BatchActionValidation> BatchValidator { get; set; }
        internal HandlerInfo AliasOwner { get; set; }
        internal Type SourceType { get; set; }
        private Type registeredSource;
        private MethodInfo registeredMethod;
        private Func<JObject, object> registeredSync;
        private Func<JObject, Task<object>> registeredAsync;
        private Func<JObject, CancellationToken, Task<object>> registeredCooperative;
        private Func<JObject, CancellationToken, ICommandProgress, Task<object>> registeredProgressive;

        internal void BindRegistration(Type source, MethodInfo method)
        {
            SourceType = registeredSource = source;
            registeredMethod = method;
            registeredSync = SyncHandler;
            registeredAsync = AsyncHandler;
            registeredCooperative = CooperativeAsyncHandler;
            registeredProgressive = ProgressiveAsyncHandler;
        }

        internal bool HasTrustedRegistration =>
            registeredSource != null
            && SourceType == registeredSource
            && registeredMethod?.DeclaringType == registeredSource
            && registeredSource.Assembly == typeof(CommandRegistry).Assembly
            && registeredSource.Namespace != null
            && registeredSource.Namespace.StartsWith("MCPForUnity.Editor.", StringComparison.Ordinal)
            && ReferenceEquals(registeredSync, SyncHandler)
            && ReferenceEquals(registeredAsync, AsyncHandler)
            && ReferenceEquals(registeredCooperative, CooperativeAsyncHandler)
            && ReferenceEquals(registeredProgressive, ProgressiveAsyncHandler);
        private string previewIdentity;
        internal string PreviewIdentity => previewIdentity ??= Guid.NewGuid().ToString("N");

        public bool IsAsync => AsyncHandler != null || CooperativeAsyncHandler != null || ProgressiveAsyncHandler != null;

        internal static HandlerInfo Cooperative(string name, Func<JObject, CancellationToken, Task<object>> handler) =>
            new HandlerInfo(name, null, null) { CooperativeAsyncHandler = handler };

        internal static HandlerInfo Progressive(string name, Func<JObject, CancellationToken, ICommandProgress, Task<object>> handler) =>
            new HandlerInfo(name, null, null) { ProgressiveAsyncHandler = handler };

        public HandlerInfo(string commandName, Func<JObject, object> syncHandler, Func<JObject, Task<object>> asyncHandler)
        {
            CommandName = commandName;
            SyncHandler = syncHandler;
            AsyncHandler = asyncHandler;
        }
    }

    /// <summary>
    /// Registry for all MCP command handlers via reflection.
    /// Handles both MCP tools and resources.
    /// </summary>
    public static class CommandRegistry
    {
        private static readonly Dictionary<string, HandlerInfo> _handlers = new();
        private static bool _initialized = false;

        /// <summary>
        /// Initialize and auto-discover all tools and resources marked with
        /// [McpForUnityTool] or [McpForUnityResource]
        /// </summary>
        public static void Initialize()
        {
            if (_initialized)
                return;

            AutoDiscoverCommands();
            _initialized = true;
        }

        private static string ToSnakeCase(string name) => StringCaseUtility.ToSnakeCase(name);

        /// <summary>
        /// Auto-discover all types with [McpForUnityTool] or [McpForUnityResource] attributes
        /// </summary>
        private static void AutoDiscoverCommands()
        {
            // AssetImportWorker is a separate Editor subprocess. It doesn't host the MCP
            // transport so the registry is unused there, and Mono can hard-crash inside
            // GetCustomAttribute<T>() when scanning types whose owning assembly hasn't
            // finished domain-reload bookkeeping in the worker. Skip the scan there
            // entirely. See issue #1134.
            if (IsRunningInAssetImportWorker())
            {
                return;
            }

            try
            {
                // TypeCache is Unity's precomputed attribute index, rebuilt once per domain
                // reload. The previous scan materialised every type in every loaded assembly
                // and then called GetCustomAttribute on each one twice, which measured at
                // ~9s per reload on large projects (issue #1336) — work Unity had already
                // done. McpClientRegistry.BuildRegistry() sets the in-tree precedent.
                //
                // It also removes the GetCustomAttribute calls that made the AssetImportWorker
                // crash in issue #1134; the worker guard above stays regardless, since the
                // registry is unused there either way.
                int toolCount = RegisterCommandTypes(TypeCache.GetTypesWithAttribute<McpForUnityToolAttribute>(), isResource: false);
                int resourceCount = RegisterCommandTypes(TypeCache.GetTypesWithAttribute<McpForUnityResourceAttribute>(), isResource: true);

                McpLog.Info($"Auto-discovered {toolCount} tools and {resourceCount} resources ({_handlers.Count} total handlers)", false);
            }
            catch (Exception ex)
            {
                McpLog.Error($"Failed to auto-discover MCP commands: {ex.Message}");
            }
        }

        internal static int RegisterCommandTypes(IEnumerable<Type> types, bool isResource)
        {
            int count = 0;
            foreach (var type in ToolDiscoveryService.InRegistrationOrder(types))
            {
                if (RegisterCommandType(type, isResource))
                    count++;
            }
            return count;
        }

        internal static MethodInfo GetCommandMethod(Type type)
        {
            // Optional progress and cancellation are explicit async signatures. Preserve
            // legacy entry points and never infer an ambient reporter for nested calls.
            var progressive = type.GetMethod(
                "HandleCommand",
                BindingFlags.Public | BindingFlags.Static,
                null,
                new[] { typeof(JObject), typeof(CancellationToken), typeof(ICommandProgress) },
                null
            );
            if (progressive != null && !progressive.ContainsGenericParameters && typeof(Task).IsAssignableFrom(progressive.ReturnType))
                return progressive;
            var cooperative = type.GetMethod(
                "HandleCommand",
                BindingFlags.Public | BindingFlags.Static,
                null,
                new[] { typeof(JObject), typeof(CancellationToken) },
                null
            );
            if (cooperative != null && !cooperative.ContainsGenericParameters && typeof(Task).IsAssignableFrom(cooperative.ReturnType))
                return cooperative;
            var method = type.GetMethod("HandleCommand", BindingFlags.Public | BindingFlags.Static, null, new[] { typeof(JObject) }, null);
            if (method == null || method.ContainsGenericParameters)
                return null;

            // Sync delegates support reference returns; async handlers support Task and Task<T>.
            return typeof(Task).IsAssignableFrom(method.ReturnType) || (!method.ReturnType.IsValueType && typeof(object).IsAssignableFrom(method.ReturnType))
                ? method
                : null;
        }

        private static bool? _cachedIsAssetImportWorker;

        private static bool IsRunningInAssetImportWorker()
        {
            if (_cachedIsAssetImportWorker.HasValue)
                return _cachedIsAssetImportWorker.Value;

            bool result = false;
            try
            {
                // AssetDatabase.IsAssetImportWorkerProcess() exists on Unity 2020.2+ but the
                // visibility has shifted between versions. Look it up reflectively so we
                // tolerate either signature without conditional compilation.
                var method = typeof(UnityEditor.AssetDatabase).GetMethod(
                    "IsAssetImportWorkerProcess",
                    BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic
                );
                if (method != null && method.GetParameters().Length == 0)
                {
                    result = method.Invoke(null, null) is bool b && b;
                }
            }
            catch
            {
                // Reflection problems shouldn't break startup; fall through to the cmdline check.
            }

            if (!result)
            {
                try
                {
                    string cmd = Environment.CommandLine ?? string.Empty;
                    if (
                        cmd.IndexOf("-importWorker", StringComparison.OrdinalIgnoreCase) >= 0
                        || cmd.IndexOf("AssetImportWorker", StringComparison.OrdinalIgnoreCase) >= 0
                    )
                    {
                        result = true;
                    }
                }
                catch { }
            }

            _cachedIsAssetImportWorker = result;
            return result;
        }

        /// <summary>
        /// Register a command type (tool or resource) with the registry.
        /// Returns true if successfully registered, false otherwise.
        /// </summary>
        private static bool RegisterCommandType(Type type, bool isResource)
        {
            string typeLabel = isResource ? "resource" : "tool";
            try
            {
                string commandName;
                if (isResource)
                {
                    var resourceAttr = type.GetCustomAttribute<McpForUnityResourceAttribute>();
                    commandName = resourceAttr.ResourceName;
                }
                else
                {
                    var toolAttr = type.GetCustomAttribute<McpForUnityToolAttribute>();
                    commandName = toolAttr.CommandName;
                }

                if (string.IsNullOrEmpty(commandName))
                    commandName = ToSnakeCase(type.Name);

                // Reserved internal alias cannot be published or replaced by custom metadata.
                if (
                    string.Equals(commandName, "batch_validate", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(commandName, "batch_apply_preview", StringComparison.OrdinalIgnoreCase)
                )
                    return false;

                var method = GetCommandMethod(type);
                if (method == null)
                {
                    McpLog.Warn(
                        $"MCP {typeLabel} {type.Name} is marked with [McpForUnity{(isResource ? "Resource" : "Tool")}] "
                            + $"but has no supported closed public static HandleCommand(JObject) or explicit async cancellation/progress signature"
                    );
                    return false;
                }

                HandlerInfo handlerInfo;

                if (typeof(Task).IsAssignableFrom(method.ReturnType))
                {
                    if (method.GetParameters().Length == 3)
                        handlerInfo = HandlerInfo.Progressive(commandName, CreateProgressiveAsyncHandlerInvoker(method, commandName));
                    else if (method.GetParameters().Length == 2)
                        handlerInfo = HandlerInfo.Cooperative(commandName, CreateAsyncHandlerInvoker(method, commandName));
                    else
                        handlerInfo = new HandlerInfo(commandName, null, CreateAsyncHandlerDelegate(method, commandName));
                }
                else
                {
                    var handler = (Func<JObject, object>)Delegate.CreateDelegate(typeof(Func<JObject, object>), method);
                    handlerInfo = new HandlerInfo(commandName, handler, null);
                }

                if (_handlers.ContainsKey(commandName))
                {
                    McpLog.Warn($"Duplicate command name '{commandName}' detected. " + $"{typeLabel} {type.Name} will override previously registered handler.");
                }
                if (type == typeof(ManageGameObject))
                    handlerInfo.BatchValidator = ManageGameObject.ValidateBatchAction;
                else if (type == typeof(ManageComponents))
                    handlerInfo.BatchValidator = ManageComponents.ValidateBatchAction;
                else if (type == typeof(ManageScene))
                    handlerInfo.BatchValidator = ManageScene.ValidateBatchAction;
                else
                    handlerInfo.BatchValidator = ToolCapabilityCatalog.GetBatchValidator(type);

                // Replacing the owner never inherits its privileged alias or validator.
                if (commandName == "batch_execute")
                {
                    _handlers.Remove("batch_validate");
                    _handlers.Remove("batch_apply_preview");
                }
                handlerInfo.BindRegistration(type, method);
                _handlers[commandName] = handlerInfo;
                if (type == typeof(BatchExecute) && commandName == "batch_execute")
                {
                    var validate = HandlerInfo.Cooperative("batch_validate", BatchExecute.ValidateCommandAsync);
                    validate.AliasOwner = handlerInfo;
                    _handlers["batch_validate"] = validate;
                    var apply = HandlerInfo.Progressive("batch_apply_preview", BatchExecute.ApplyPreviewCommand);
                    apply.AliasOwner = handlerInfo;
                    _handlers["batch_apply_preview"] = apply;
                }
                return true;
            }
            catch (Exception ex)
            {
                McpLog.Error($"Failed to register {typeLabel} {type.Name}: {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// True when a tool or resource handler is registered under this name.
        /// </summary>
        public static bool HasHandler(string commandName) => _handlers.TryGetValue(commandName, out var handler) && IsCurrentAlias(handler);

        private static bool IsCurrentAlias(HandlerInfo handler) =>
            handler.AliasOwner == null
            || (
                (handler.CommandName == "batch_validate" || handler.CommandName == "batch_apply_preview")
                && _handlers.TryGetValue("batch_execute", out var owner)
                && ReferenceEquals(owner, handler.AliasOwner)
            );

        internal static bool IsTrustedBuiltInHandler(string commandName)
        {
            if (!_handlers.TryGetValue(commandName, out var handler) || !IsCurrentAlias(handler))
                return false;
            var owner = handler.AliasOwner ?? handler;
            return owner.HasTrustedRegistration && ToolOperationIdentity.IsCanonicalHandler(owner.CommandName, owner.SourceType);
        }

        internal static bool IsTrustedBatchHandler(string commandName) =>
            _handlers.TryGetValue(commandName, out var handler)
            && IsCurrentAlias(handler)
            && (handler.AliasOwner ?? handler).HasTrustedRegistration
            && (handler.AliasOwner ?? handler).SourceType == typeof(BatchExecute);

        internal static bool IsTrustedCompositionHandler(string commandName) =>
            (commandName != "batch_validate" && IsTrustedBatchHandler(commandName))
            || (
                commandName == "blender_bridge"
                && _handlers.TryGetValue(commandName, out var handler)
                && handler.HasTrustedRegistration
                && handler.SourceType == typeof(Blender.BlenderBridgeTool)
            );

        internal static string GetAdmissionName(string commandName) =>
            _handlers.TryGetValue(commandName, out var handler) && handler.AliasOwner != null && IsCurrentAlias(handler) ? "batch_execute" : commandName;

        internal static void RequireAvailable(string commandName)
        {
            // Discovery intentionally hides tool metadata off-thread. Treating that null as
            // an unlisted custom handler would turn a stored delegate into an availability bypass.
            if (!ToolConsentThread.IsMainThread)
                throw new InvalidOperationException("Commands must execute on the Unity Editor main thread.");
            string admissionName = GetAdmissionName(commandName);
            var resource = MCPServiceLocator.ResourceDiscovery.GetResourceMetadata(admissionName);
            if (resource != null && !MCPServiceLocator.ResourceDiscovery.IsResourceEnabled(admissionName))
                throw new InvalidOperationException($"Resource '{commandName}' is disabled in the Unity Editor.");
            var tool = MCPServiceLocator.ToolDiscovery.GetToolMetadata(admissionName);
            if (tool != null && !MCPServiceLocator.ToolDiscovery.IsToolEnabled(admissionName))
                throw new InvalidOperationException($"Tool '{commandName}' is disabled in the Unity Editor.");
        }

        internal static string GetBatchTargetIdentity(string name)
        {
            var handler = GetHandlerInfo(name);
            Type type = handler.SourceType;
            return
                type == typeof(ManageAsset)
                || type == typeof(AssetEdit)
                || type == typeof(ManageGameObject)
                || type == typeof(GameobjectEdit)
                || type == typeof(Prefabs.ManagePrefabs)
                || type == typeof(PrefabContents)
                ? handler.PreviewIdentity
                : null;
        }

        internal static BatchActionValidation ValidateBatchAction(string commandName, JObject parameters)
        {
            var handler = GetHandlerInfo(commandName);
            return handler.BatchValidator == null ? BatchActionValidation.Unavailable : handler.BatchValidator(parameters);
        }

        /// <summary>
        /// Get a command handler by name
        /// </summary>
        private static HandlerInfo GetHandlerInfo(string commandName)
        {
            if (!_handlers.TryGetValue(commandName, out var handler) || !IsCurrentAlias(handler))
            {
                throw new InvalidOperationException($"Unknown or unsupported command type: {commandName}");
            }
            return handler;
        }

        /// <summary>
        /// Get a synchronous command handler by name.
        /// Throws if the command is asynchronous.
        /// </summary>
        /// <param name="commandName"></param>
        /// <returns></returns>
        /// <exception cref="InvalidOperationException"></exception>
        public static Func<JObject, object> GetHandler(string commandName)
        {
            var handlerInfo = GetHandlerInfo(commandName);
            if (handlerInfo.IsAsync)
            {
                throw new InvalidOperationException($"Command '{commandName}' is asynchronous and must be executed via ExecuteCommand");
            }

            return parameters =>
            {
                // A retained delegate must observe later disablement or consent revocation.
                GetHandlerInfo(commandName);
                RequireAvailable(commandName);
                return handlerInfo.SyncHandler(parameters);
            };
        }

        /// <summary>
        /// Execute a command handler, supporting both synchronous and asynchronous (coroutine) handlers.
        /// If the handler returns an IEnumerator, it will be executed as a coroutine.
        /// </summary>
        /// <param name="commandName">The command name to execute</param>
        /// <param name="params">Command parameters</param>
        /// <param name="tcs">TaskCompletionSource to complete when async operation finishes</param>
        /// <returns>The result for synchronous commands, or null for async commands (TCS will be completed later)</returns>
        public static object ExecuteCommand(string commandName, JObject @params, TaskCompletionSource<string> tcs) =>
            ExecuteCommand(commandName, @params, tcs, CancellationToken.None);

        // Additive token overload: never replace actual handler settlement with a canceled wait.
        public static object ExecuteCommand(string commandName, JObject @params, TaskCompletionSource<string> tcs, CancellationToken cancellationToken) =>
            ExecuteCommand(commandName, @params, tcs, cancellationToken, null);

        public static object ExecuteCommand(
            string commandName,
            JObject @params,
            TaskCompletionSource<string> tcs,
            CancellationToken cancellationToken,
            ICommandProgress progress
        ) => ExecuteCommand(commandName, @params, tcs, cancellationToken, progress, null);

        internal static object ExecuteCommand(
            string commandName,
            JObject @params,
            TaskCompletionSource<string> tcs,
            CancellationToken cancellationToken,
            ICommandProgress progress,
            Action<CommandOutcome> outcome
        )
        {
            cancellationToken.ThrowIfCancellationRequested();
            var handlerInfo = GetHandlerInfo(commandName);
            RequireAvailable(commandName);

            if (handlerInfo.IsAsync)
            {
                ExecuteAsyncHandler(handlerInfo, @params, tcs, cancellationToken, progress, outcome);
                return null;
            }

            if (handlerInfo.SyncHandler == null)
            {
                throw new InvalidOperationException($"Handler for '{commandName}' does not provide a synchronous implementation");
            }

            object result = handlerInfo.SyncHandler(@params);
            // A synchronous handler may return a Task<object> to answer later, as manage_scene's
            // play-mode screenshot does. Only Task<object> is awaited here: a handler that needs
            // another Task type should be declared async, which registration handles.
            if (result is Task<object> returnedTask)
            {
                ExecuteAsyncHandler(new HandlerInfo(commandName, null, _ => returnedTask), @params, tcs, cancellationToken, progress, outcome);
                return null;
            }

            return result;
        }

        /// <summary>
        /// Execute a command handler and return its raw result, regardless of sync or async implementation.
        /// Used internally for features like batch execution where commands need to be composed.
        /// </summary>
        /// <param name="commandName">The registered command to execute.</param>
        /// <param name="params">Parameters to pass to the command (optional).</param>
        public static Task<object> InvokeCommandAsync(string commandName, JObject @params) => InvokeCommandAsync(commandName, @params, CancellationToken.None);

        public static Task<object> InvokeCommandAsync(string commandName, JObject @params, CancellationToken cancellationToken) =>
            InvokeCommandAsync(commandName, @params, cancellationToken, null);

        internal static Task<object> InvokeCommandAsync(
            string commandName,
            JObject @params,
            CancellationToken cancellationToken,
            NativeCommandArbitrator.Lease parent
        )
        {
            cancellationToken.ThrowIfCancellationRequested();
            GetHandlerInfo(commandName);
            RequireAvailable(commandName);
            bool trustedBuiltIn = IsTrustedBuiltInHandler(commandName);
            var policy = ToolOperationPolicy.Resolve(commandName, @params, trustedBuiltIn);
            if (
                !NativeCommandArbitrator.Shared.TryAcquire(
                    policy.Mode,
                    NativeCommandArbitrator.BarrierCandidate(commandName, policy.Mode, trustedBuiltIn),
                    @params,
                    parent,
                    out var lease,
                    out NativeAdmissionBlocker blocker
                )
            )
                throw new NativeCommandBlockedException(blocker);
            try
            {
                using var composition = NativeCommandArbitrator.EnterComposition(IsTrustedCompositionHandler(commandName) ? parent ?? lease : null);
                var task = InvokeCommandCoreAsync(commandName, @params, cancellationToken);
                if (task.IsCompleted)
                {
                    lease.Dispose();
                    return task;
                }
                return ReleaseAfterSettlementAsync(task, lease);
            }
            catch
            {
                lease.Dispose();
                throw;
            }
        }

        private static async Task<object> ReleaseAfterSettlementAsync(Task<object> task, NativeCommandArbitrator.Lease lease)
        {
            using (lease)
                return await task.ConfigureAwait(true);
        }

        private static Task<object> InvokeCommandCoreAsync(string commandName, JObject @params, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var handlerInfo = GetHandlerInfo(commandName);
            var payload = @params ?? new JObject();

            if (handlerInfo.IsAsync)
            {
                // Composed child invocations do not inherit the outer batch reporter.
                if (handlerInfo.ProgressiveAsyncHandler != null)
                    return handlerInfo.ProgressiveAsyncHandler(payload, cancellationToken, null);
                if (handlerInfo.CooperativeAsyncHandler != null)
                    return handlerInfo.CooperativeAsyncHandler(payload, cancellationToken);
                if (handlerInfo.AsyncHandler == null)
                {
                    throw new InvalidOperationException($"Async handler for '{commandName}' is not configured correctly");
                }

                return handlerInfo.AsyncHandler(payload);
            }

            if (handlerInfo.SyncHandler == null)
            {
                throw new InvalidOperationException($"Handler for '{commandName}' does not provide a synchronous implementation");
            }

            object result = handlerInfo.SyncHandler(payload);
            // Same contract as ExecuteCommand: a returned Task<object> is the answer to await.
            if (result is Task<object> returnedTask)
            {
                return returnedTask;
            }

            return Task.FromResult(result);
        }

        /// <summary>
        /// Create a delegate for an async handler method that returns Task or Task<T>.
        /// The delegate will invoke the method and await its completion, returning the result.
        /// </summary>
        /// <param name="method"></param>
        /// <param name="commandName"></param>
        /// <returns></returns>
        /// <exception cref="InvalidOperationException"></exception>
        private static Func<JObject, Task<object>> CreateAsyncHandlerDelegate(MethodInfo method, string commandName)
        {
            var invoke = CreateAsyncHandlerInvoker(method, commandName);
            return parameters => invoke(parameters, CancellationToken.None);
        }

        private static Func<JObject, CancellationToken, Task<object>> CreateAsyncHandlerInvoker(MethodInfo method, string commandName)
        {
            var invoke = CreateProgressiveAsyncHandlerInvoker(method, commandName);
            return (parameters, cancellationToken) => invoke(parameters, cancellationToken, null);
        }

        private static Func<JObject, CancellationToken, ICommandProgress, Task<object>> CreateProgressiveAsyncHandlerInvoker(
            MethodInfo method,
            string commandName
        )
        {
            // The declared Task contract determines whether a result exists; runtime
            // implementations of plain Task can be Task<VoidTaskResult> internally.
            Type resultTaskType = method.ReturnType;
            while (resultTaskType != null && (!resultTaskType.IsGenericType || resultTaskType.GetGenericTypeDefinition() != typeof(Task<>)))
                resultTaskType = resultTaskType.BaseType;
            var resultProperty = resultTaskType?.GetProperty("Result");

            int arity = method.GetParameters().Length;
            return async (JObject parameters, CancellationToken cancellationToken, ICommandProgress progress) =>
            {
                cancellationToken.ThrowIfCancellationRequested();
                object rawResult;

                try
                {
                    object[] arguments =
                        arity == 3 ? new object[] { parameters, cancellationToken, progress }
                        : arity == 2 ? new object[] { parameters, cancellationToken }
                        : new object[] { parameters };
                    rawResult = method.Invoke(null, arguments);
                }
                catch (TargetInvocationException ex)
                {
                    throw ex.InnerException ?? ex;
                }

                if (rawResult == null)
                {
                    return null;
                }

                if (rawResult is not Task task)
                {
                    throw new InvalidOperationException($"Async handler '{commandName}' returned an object that is not a Task");
                }

                await task.ConfigureAwait(true);

                return resultProperty?.GetValue(task);
            };
        }

        private static void ExecuteAsyncHandler(
            HandlerInfo handlerInfo,
            JObject parameters,
            TaskCompletionSource<string> tcs,
            CancellationToken cancellationToken,
            ICommandProgress progress,
            Action<CommandOutcome> outcome
        )
        {
            string commandName = handlerInfo.CommandName;
            if (!handlerInfo.IsAsync)
            {
                throw new InvalidOperationException($"Async handler for '{commandName}' is not configured correctly");
            }

            Task<object> handlerTask;
            void CloseTransportProgress() => (progress as CommandProgressBuffer)?.Close();

            try
            {
                handlerTask =
                    handlerInfo.ProgressiveAsyncHandler != null ? handlerInfo.ProgressiveAsyncHandler(parameters, cancellationToken, progress)
                    : handlerInfo.CooperativeAsyncHandler != null ? handlerInfo.CooperativeAsyncHandler(parameters, cancellationToken)
                    : handlerInfo.AsyncHandler(parameters);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                CloseTransportProgress();
                tcs.TrySetCanceled(cancellationToken);
                return;
            }
            catch (Exception ex)
            {
                CloseTransportProgress();
                ReportAsyncFailure(commandName, tcs, ex, outcome);
                return;
            }

            if (handlerTask == null)
            {
                CloseTransportProgress();
                CompleteAsyncCommand(commandName, tcs, null, outcome);
                return;
            }

            async void AwaitHandler()
            {
                try
                {
                    var finalResult = await handlerTask.ConfigureAwait(true);
                    CloseTransportProgress();
                    CompleteAsyncCommand(commandName, tcs, finalResult, outcome);
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    CloseTransportProgress();
                    tcs.TrySetCanceled(cancellationToken);
                }
                catch (Exception ex)
                {
                    CloseTransportProgress();
                    ReportAsyncFailure(commandName, tcs, ex, outcome);
                }
            }

            AwaitHandler();
        }

        /// <summary>
        /// Complete the TaskCompletionSource for an async command with a success result.
        /// </summary>
        /// <param name="commandName"></param>
        /// <param name="tcs"></param>
        /// <param name="result"></param>
        private static void CompleteAsyncCommand(string commandName, TaskCompletionSource<string> tcs, object result, Action<CommandOutcome> outcome)
        {
            try
            {
                var response = new { status = "success", result };
                string json = SerializeAsyncResponse(response);
                outcome?.Invoke(CommandOutcomeMetadata.FromResult(result));

                if (!tcs.TrySetResult(json))
                {
                    McpLog.Warn($"TCS for async command '{commandName}' was already completed");
                }
            }
            catch (Exception ex)
            {
                McpLog.Error($"Error completing async command '{commandName}': {ex.Message}\n{ex.StackTrace}");
                ReportAsyncFailure(commandName, tcs, ex, outcome);
            }
        }

        private static string SerializeAsyncResponse(object response)
        {
            try
            {
                return BoundedResponseJson.SerializeAdmitted(response);
            }
            catch (ResponseLimitException)
            {
                // This fixed envelope must remain useful even if custom serializers caused
                // the refusal or attempted to expand the handler's error response itself.
                return "{\"status\":\"error\",\"error\":\"Command result exceeds the transport size or complexity limit\"}";
            }
        }

        /// <summary>
        /// Report an error that occurred during async command execution.
        /// Completes the TaskCompletionSource with an error response.
        /// </summary>
        /// <param name="commandName"></param>
        /// <param name="tcs"></param>
        /// <param name="ex"></param>
        private static void ReportAsyncFailure(string commandName, TaskCompletionSource<string> tcs, Exception ex, Action<CommandOutcome> outcome)
        {
            McpLog.Error($"Error in async command '{commandName}': {ex.Message}\n{ex.StackTrace}");

            var errorResponse = new
            {
                status = "error",
                error = ex.Message,
                command = commandName,
                stackTrace = ex.StackTrace,
            };

            string json;
            try
            {
                json = SerializeAsyncResponse(errorResponse);
            }
            catch (Exception serializationEx)
            {
                McpLog.Error($"Failed to serialize error response for '{commandName}': {serializationEx.Message}");
                json = "{\"status\":\"error\",\"error\":\"Failed to complete command\"}";
            }

            outcome?.Invoke(CommandOutcome.Error);
            if (!tcs.TrySetResult(json))
            {
                McpLog.Warn($"TCS for async command '{commandName}' was already completed when trying to report error");
            }
        }
    }
}
