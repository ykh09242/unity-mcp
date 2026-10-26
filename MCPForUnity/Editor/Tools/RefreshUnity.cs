using System;
using System.Diagnostics;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Explicitly refreshes Unity's asset database and optionally requests a script compilation.
    /// This is side-effectful and should be treated as a tool.
    /// </summary>
    [McpForUnityTool("refresh_unity", AutoRegister = false)]
    public static class RefreshUnity
    {
        private const int DefaultWaitTimeoutSeconds = 300;
        internal static Action<ImportAssetOptions> RefreshAssets = AssetDatabase.Refresh;
        internal static Action RequestCompilation = CompilationPipeline.RequestScriptCompilation;
        internal static Func<bool> IsCompiling = EditorStateCache.GetActualIsCompiling;
        internal static CompilationLedger LedgerOverride;
        internal static OwnedCompilationReservation ReservationOverride;
        private static CompilationLedger Ledger => LedgerOverride ?? CompilationJobTracker.Ledger;
        private static OwnedCompilationReservation Reservation => ReservationOverride ?? CompilationJobTracker.Reservation;

        private static string ReadOption(JObject parameters, string name, string defaultValue, params string[] allowedValues)
        {
            var token = parameters?[name];
            if (token == null || token.Type == JTokenType.Null)
                return defaultValue;
            if (token.Type == JTokenType.String)
            {
                string value = token.Value<string>();
                foreach (string allowed in allowedValues)
                    if (string.Equals(value, allowed, StringComparison.OrdinalIgnoreCase))
                        return value;
            }
            throw new ArgumentException($"Invalid parameter '{name}': expected one of {string.Join(", ", allowedValues)}.", name);
        }

        public static async Task<object> HandleCommand(JObject @params)
        {
            string mode;
            string scope;
            string compile;
            bool waitForReady;
            double timeoutSeconds;
            try
            {
                timeoutSeconds = OperationTimeouts.ReadSeconds(@params, DefaultWaitTimeoutSeconds);
                mode = ReadOption(@params, "mode", "if_dirty", "if_dirty", "force");
                scope = ReadOption(@params, "scope", "all", "assets", "scripts", "all");
                compile = ReadOption(@params, "compile", "none", "none", "request");
                var snakeCaseWait = @params?["wait_for_ready"];
                var camelCaseWait = @params?["waitForReady"];
                // Validate both supplied aliases, even when one is shadowed by precedence.
                ParamCoercion.CoerceBool(snakeCaseWait, false);
                ParamCoercion.CoerceBool(camelCaseWait, false);
                waitForReady = ParamCoercion.CoerceBool(snakeCaseWait ?? camelCaseWait, false);
            }
            catch (ArgumentException error)
            {
                return new ErrorResponse(error.Message);
            }

            var elapsed = Stopwatch.StartNew();
            TimeSpan Remaining() => TimeSpan.FromSeconds(Math.Max(0, timeoutSeconds - elapsed.Elapsed.TotalSeconds));

            string compileId = null;
            if (string.Equals(compile, "request", StringComparison.OrdinalIgnoreCase))
            {
                var idToken = @params?["compile_id"];
                if (idToken != null && (idToken.Type != JTokenType.String || !CompilationLedger.ValidId(idToken.Value<string>())))
                    return new ErrorResponse("Invalid compile_id.");
                compileId = idToken?.Value<string>() ?? Guid.NewGuid().ToString("N");
            }

            if (TestRunStatus.IsRunning)
            {
                return new ErrorResponse("tests_running", new { reason = "tests_running", retry_after_ms = 5000 });
            }

            if (compileId != null && IsCompiling())
                return new ErrorResponse(
                    "compilation_in_progress",
                    new
                    {
                        reason = "preexisting_compile",
                        retryable = true,
                        refresh_triggered = false,
                        compile_requested = false,
                        compilation = Ledger.Query(compileId),
                        current_compilation = Ledger.Summary(),
                        hint = "Wait for the current compilation to finish, then explicitly refresh again to import pending source changes. No refresh or compilation was queued by this request.",
                    }
                );

            bool refreshTriggered = false;
            bool compileRequested = false;
            bool refreshCycleObserved = false;
            string compilationTrigger = null;
            string reservationGeneration = null;
            int compileCountBefore = EditorStateCache.CompileCount;

            if (compileId != null && !Ledger.Begin(compileId))
                return new SuccessResponse(
                    "Compilation request already tracked or tracking capacity reached; no work repeated.",
                    new
                    {
                        compilation = Ledger.Query(compileId),
                        refresh_triggered = false,
                        compile_requested = false,
                    }
                );

            try
            {
                if (compileId != null)
                    reservationGeneration = Reservation.Reserve(
                        compileId,
                        EditorApplication.timeSinceStartup + Remaining().TotalSeconds,
                        refreshing: !string.Equals(scope, "scripts", StringComparison.OrdinalIgnoreCase)
                    );
                // Best-effort semantics: if_dirty currently behaves like force unless future dirty signals are added.
                bool shouldRefresh =
                    string.Equals(mode, "force", StringComparison.OrdinalIgnoreCase) || string.Equals(mode, "if_dirty", StringComparison.OrdinalIgnoreCase);

                if (shouldRefresh)
                {
                    if (string.Equals(scope, "scripts", StringComparison.OrdinalIgnoreCase))
                    {
                        // For scripts, requesting compilation is usually the meaningful action.
                        // We avoid a heavyweight full refresh by default.
                    }
                    else
                    {
                        RefreshAssets(ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
                        refreshTriggered = true;
                    }
                }

                if (string.Equals(compile, "request", StringComparison.OrdinalIgnoreCase))
                {
                    refreshCycleObserved = Reservation.CompleteRefreshDecision(reservationGeneration);
                    if (refreshCycleObserved)
                    {
                        compilationTrigger = "refresh";
                        Ledger.AdoptOwnedRefreshCycle(compileId);
                    }
                    else
                    {
                        Ledger.ExplicitRequest(compileId);
                        RequestCompilation();
                        compileRequested = true;
                        compilationTrigger = "request";
                        Ledger.Dispatched(compileId);
                    }
                }

                if (string.Equals(scope, "all", StringComparison.OrdinalIgnoreCase) && !refreshTriggered)
                {
                    // If the caller asked for "all" and we skipped refresh above (e.g., scripts-only path),
                    // do a lightweight refresh now. Use ForceSynchronousImport to ensure the refresh
                    // completes before returning, preventing stalls when Unity is backgrounded.
                    RefreshAssets(ImportAssetOptions.ForceSynchronousImport);
                    refreshTriggered = true;
                }
            }
            catch (Exception ex)
            {
                if (reservationGeneration != null && !compileRequested)
                {
                    Reservation.AbortBeforeStart(reservationGeneration);
                    Reservation.CompleteRefreshDecision(reservationGeneration);
                }
                if (compileId != null)
                    Ledger.Reject(compileId);
                return new ErrorResponse($"refresh_failed: {ex.Message}", new { compilation = compileId == null ? null : Ledger.Query(compileId) });
            }

            // RequestScriptCompilation only queues; the pipeline starts on a later
            // editor tick. Sampling the state here therefore reported "idle" for a
            // compile that was about to run, and the caller's readiness poll — which
            // begins the moment this returns — saw a ready editor and returned
            // immediately, so wait_for_ready silently did nothing for exactly the call
            // it exists for (issue #814). Waiting for the start edge first makes
            // resulting_state, and every readiness decision downstream of it, truthful.
            //
            // Unlike WaitForUnityReadyAsync this cannot span a domain reload: it
            // resolves the moment compilation *starts*, long before assemblies swap.
            // That is why it is safe on Unity 6+ where waiting for readiness is not.
            //
            // Gated on wait_for_ready: that flag is documented as the non-blocking
            // switch, and this wait is a wait — cheap when the pipeline starts on the
            // next tick, but a full grace when it never does. A caller who opted out
            // of waiting gets the immediate return and the poll hint.
            //
            // An actual cycle observed during this owned refresh is already a start.
            // Otherwise compile_started stays null unless an explicit start was waited
            // for, so "not observed" never reads as "did not start".
            bool? compileStarted = refreshCycleObserved ? true : null;
            if (compileRequested && waitForReady)
            {
                compileStarted = await WaitForCompilationToStartAsync(compileCountBefore, Remaining()).ConfigureAwait(true);
            }

            // Unity 6+ fix: Skip wait_for_ready when compile was requested.
            // The EditorApplication.update polling in WaitForUnityReadyAsync doesn't survive
            // domain reloads properly in Unity 6+, causing infinite compilation loops.
            // When compilation is requested, return immediately and let client poll editor_state.
            // Earlier Unity versions retain the original behavior.
#if UNITY_6000_0_OR_NEWER
            bool shouldWaitForReady = waitForReady && !compileRequested && !refreshCycleObserved;
#else
            bool shouldWaitForReady = waitForReady;
#endif
            if (shouldWaitForReady)
            {
                try
                {
                    await WaitForUnityReadyAsync(Remaining()).ConfigureAwait(true);
                }
                catch (TimeoutException)
                {
                    return new ErrorResponse(
                        "refresh_timeout_waiting_for_ready",
                        new
                        {
                            compilation = compileId == null ? null : Ledger.Query(compileId),
                            refresh_triggered = refreshTriggered,
                            compile_requested = compileRequested,
                            compile_started = compileStarted,
                            resulting_state = "unknown",
                        }
                    );
                }
                catch (Exception ex)
                {
                    return new ErrorResponse($"refresh_wait_failed: {ex.Message}");
                }
            }

            string resultingState = EditorStateCache.GetActualIsCompiling() ? "compiling" : (EditorApplication.isUpdating ? "asset_import" : "idle");

            return new SuccessResponse(
                "Refresh requested.",
                new
                {
                    compilation = compileId == null ? null : Ledger.Query(compileId),
                    refresh_triggered = refreshTriggered,
                    compile_requested = compileRequested,
                    compilation_trigger = compilationTrigger,
                    compilation_goal_satisfied = refreshCycleObserved,
                    compile_started = compileStarted,
                    native_coordination = compileId == null ? null : CompilationJobTracker.CoordinationStatus(compileId),
                    resulting_state = resultingState,
                    hint = shouldWaitForReady
                        ? "Unity refresh completed; editor should be ready."
                        : "If Unity enters compilation/domain reload, poll the mcpforunity://editor/state resource until data.advice.ready_for_tools is true.",
                }
            );
        }

        /// <summary>
        /// Resolves <c>true</c> once a compilation is under way, or <c>false</c> once
        /// the grace elapsed without one. Two of the three exits are a start:
        /// <list type="bullet">
        /// <item>the pipeline is running;</item>
        /// <item><see cref="EditorStateCache.CompileCount"/> moved past
        /// <paramref name="compileCountBefore"/> — a short compile can begin and end
        /// inside AssetDatabase.Refresh, before this is even armed, and the counter is
        /// the only thing that still sees it;</item>
        /// <item>the grace elapsed with neither — the pipeline declined or deferred
        /// the request within its remaining observation budget.</item>
        /// </list>
        /// The first two are also tested synchronously on entry, so the case where a
        /// reload is already imminent never leaves this command queued as a
        /// continuation — see the note on the fast path below. The running case is
        /// observed from <see cref="CompilationPipeline.compilationStarted"/> and
        /// completed so that the caller's continuations run inline in that handler,
        /// for the reason given at the completion source.
        /// </summary>
        internal static Task<bool> WaitForCompilationToStartAsync(int compileCountBefore, TimeSpan grace)
        {
            // Synchronous fast path, and the reason it matters: the counter check is
            // there for a compile that began *and ended* inside AssetDatabase.Refresh
            // above, and in that state the domain reload is already imminent. Resolving
            // it from a later tick would hand the rest of this command to the
            // synchronization context as a queued continuation, which the reload
            // discards along with the rest of the domain — losing the response. An
            // already-completed task resumes the await inline instead, so nothing is
            // left queued.
            if (EditorStateCache.CompileCount != compileCountBefore || EditorStateCache.GetActualIsCompiling())
            {
                return Task.FromResult(true);
            }

            // Resolved from the compilationStarted event itself, not from a poll, and
            // deliberately *without* RunContinuationsAsynchronously. Both matter for
            // the same reason: the response has to be on the wire before the compile
            // finishes, because the domain reload follows compilationFinished directly
            // and a cached no-change compile lasts ~110 ms. Every await between here
            // and the socket captures Unity's synchronization context; a continuation
            // posted to it runs one editor frame later, and there are three of them
            // (this method's caller, the CommandRegistry async wrapper, its
            // AwaitHandler). Completing the task on the main thread with inlining
            // allowed lets the awaiter see the captured context as the current one and
            // run all three inline, inside this event handler, so the only hop left
            // is the dispatcher's thread-pool send. A poll would also quantise the
            // edge to the update tick, which in an unfocused Editor is most of that
            // window on its own.
            //
            // EditorStateCache subscribed to the same event at domain load, so its
            // handler has already flipped GetActualIsCompiling() by the time this one
            // runs; the caller reads resulting_state = "compiling" inline.
            var tcs = new TaskCompletionSource<bool>();
            var elapsed = Stopwatch.StartNew();
            Action<object> onStarted = null;
            EditorApplication.CallbackFunction tick = null;

            void Cleanup()
            {
                CompilationPipeline.compilationStarted -= onStarted;
                EditorApplication.update -= tick;
                AssemblyReloadEvents.beforeAssemblyReload -= Stop;
                EditorApplication.quitting -= Stop;
            }
            void Stop()
            {
                Cleanup();
                tcs.TrySetCanceled();
            }
            onStarted = _ =>
            {
                Cleanup();
                tcs.TrySetResult(true);
            };

            // The update hook only carries the grace: the pipeline declined or deferred
            // the request, so no reload is coming and inlining is harmless there too.
            tick = () =>
            {
                if (elapsed.Elapsed < grace)
                {
                    return;
                }

                Cleanup();
                tcs.TrySetResult(false);
            };

            CompilationPipeline.compilationStarted += onStarted;
            EditorApplication.update += tick;
            AssemblyReloadEvents.beforeAssemblyReload += Stop;
            EditorApplication.quitting += Stop;
            // Nudge Unity to pump once in case update is throttled.
            try
            {
                EditorApplication.QueuePlayerLoopUpdate();
            }
            catch { }
            return tcs.Task;
        }

        private static Task WaitForUnityReadyAsync(TimeSpan timeout)
        {
            var tcs = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var elapsed = Stopwatch.StartNew();

            void Cleanup()
            {
                EditorApplication.update -= Tick;
                AssemblyReloadEvents.beforeAssemblyReload -= Stop;
                EditorApplication.quitting -= Stop;
            }
            void Stop()
            {
                Cleanup();
                tcs.TrySetCanceled();
            }

            void Tick()
            {
                try
                {
                    if (tcs.Task.IsCompleted)
                    {
                        Cleanup();
                        return;
                    }

                    if (elapsed.Elapsed >= timeout)
                    {
                        Cleanup();
                        tcs.TrySetException(new TimeoutException());
                        return;
                    }

                    if (
                        !EditorStateCache.GetActualIsCompiling()
                        && !EditorApplication.isUpdating
                        && !TestRunStatus.IsRunning
                        && !EditorApplication.isPlayingOrWillChangePlaymode
                    )
                    {
                        Cleanup();
                        tcs.TrySetResult(true);
                    }
                }
                catch (Exception ex)
                {
                    Cleanup();
                    tcs.TrySetException(ex);
                }
            }

            EditorApplication.update += Tick;
            AssemblyReloadEvents.beforeAssemblyReload += Stop;
            EditorApplication.quitting += Stop;
            // Nudge Unity to pump once in case update is throttled.
            try
            {
                EditorApplication.QueuePlayerLoopUpdate();
            }
            catch { }
            return tcs.Task;
        }
    }
}
