using System;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Services.Transport
{
    internal struct NativeBarrierState
    {
        internal bool Compiling;
        internal bool Importing;
        internal bool Transitioning;
        internal bool OwnedJob;
        internal bool RecoveryRequired;
        internal NativeRecoveryDetails RecoveryDetails;
        internal string OwnedCompilation;
        internal NativeRecoveryDetails CompilationRecovery;
        internal bool PackageMutation;
        internal bool PackageQuery;
        internal bool PackageMutationRecovery;
        internal bool PackageQueryRecovery;
    }

    // Admission reads actual owners, never Activity rows or public job outcomes. All reads
    // happen on the Editor main thread immediately before invoking a non-control handler.
    internal static class NativeOperationBarriers
    {
        internal static bool TryGetBlocker(string commandName, JObject parameters, out NativeAdmissionBlocker blocker) =>
            Evaluate(commandName, parameters, Capture(), out blocker);

        // General readiness shares ordinary-read admission. Read-only UPM queries
        // still fence Client calls/execute_code, but do not block every tool.
        internal static NativeAdmissionBlocker ObserveReadiness(bool compiling)
        {
            Evaluate("read_console", null, Capture(compiling), out var blocker);
            return blocker;
        }

        private static NativeBarrierState Capture(bool? compiling = null)
        {
            var retired = TestRunnerService.RetiredRunRecoveryDetails;
            // The matching restored control can settle this marker independently of
            // callback reattachment. An unrelated run cannot clear its ownership.
            if (retired != null && TestJobManager.ControlFor(retired.JobId) is { Released: false })
                retired = null;
            var recovery = PreferRecovery(TestJobManager.NativeRecoveryDetails, retired);
            if (BuildRunner.NativeRecoveryRequired)
                recovery = PreferRecovery(
                    recovery,
                    new NativeRecoveryDetails(
                        "build",
                        "owner_lost",
                        "build_completion_observer_lost",
                        "restart_required",
                        "inspect_native_work_then_restart_editor",
                        "A build reservation survived a domain reload, but its managed completion observer did not. "
                            + "Unity being idle cannot prove whether that build finished. Inspect the build in Unity, then restart the Editor to reset this session's ownership."
                    )
                );
            return new NativeBarrierState
            {
                // Event snapshots already know this flag. Do not reenter the native
                // compilation pipeline from an AssemblyBuilder completion callback.
                Compiling = compiling ?? EditorStateCache.GetActualIsCompiling(),
                Importing = EditorApplication.isUpdating,
                Transitioning = EditorStateCache.IsDomainReloadPending || EditorStateCache.IsPlayModeTransition || PlayModeReadiness.HasPendingPlayRequest,
                OwnedJob =
                    TestJobManager.HasRunningJob
                    || TestJobManager.NativeAdmissionBlocked
                    || TestRunnerService.HasOwnedNativeRun
                    || BuildRunner.HasNativeOwner
                    || BuildPipeline.isBuildingPlayer
                    || PlayScenarioService.IsBusy
                    || PlayScenarioSuiteService.IsBusy,
                RecoveryRequired = RequiresRecovery(recovery),
                RecoveryDetails = recovery,
                OwnedCompilation = CompilationJobTracker.Reservation.Blocker(EditorApplication.timeSinceStartup),
                CompilationRecovery = CompilationJobTracker.Reservation.Recovery(EditorApplication.timeSinceStartup),
                PackageMutation = ManagePackages.HasNativeMutation,
                PackageQuery = ManagePackages.HasNativeQuery,
                PackageMutationRecovery = ManagePackages.MutationRecoveryRequired,
                PackageQueryRecovery = ManagePackages.QueryRecoveryRequired,
            };
        }

        internal static bool Evaluate(string commandName, JObject parameters, NativeBarrierState state, out NativeAdmissionBlocker blocker)
        {
            bool packageCandidate = StartsPackageClient(commandName, parameters);
            string reason =
                state.RecoveryRequired
                || RequiresRecovery(state.RecoveryDetails)
                || state.OwnedCompilation == "recovery_required"
                || state.PackageMutationRecovery
                || packageCandidate && state.PackageQueryRecovery
                    ? "recovery_required"
                : state.Compiling ? "editor_compiling"
                : state.Importing ? "editor_importing"
                : state.Transitioning
                || state.OwnedJob
                || state.RecoveryDetails != null
                || state.OwnedCompilation != null
                || state.PackageMutation
                || packageCandidate && state.PackageQuery
                    ? "native_job_active"
                : null;
            NativeRecoveryDetails recovery = null;
            if (reason == "recovery_required")
            {
                if (RequiresRecovery(state.RecoveryDetails))
                    recovery = state.RecoveryDetails;
                if (state.OwnedCompilation == "recovery_required")
                    recovery = PreferRecovery(recovery, state.CompilationRecovery);
                if (state.PackageMutationRecovery || packageCandidate && state.PackageQueryRecovery)
                    recovery = PreferRecovery(
                        recovery,
                        new NativeRecoveryDetails(
                            "package_manager",
                            "request_handle_lost",
                            "package_request_handle_lost",
                            "restart_required",
                            "inspect_native_work_then_restart_editor",
                            "A Package Manager request survived a domain reload without its Request handle. "
                                + "Installed package metadata cannot prove the request completed. Wait for Package Manager activity to settle, then restart the Editor to reset this session's ownership."
                        )
                    );
            }
            else if (reason != null)
            {
                recovery = PreferRecovery(state.RecoveryDetails, state.CompilationRecovery);
                if (recovery == null && (state.PackageMutation || packageCandidate && state.PackageQuery))
                    recovery = new NativeRecoveryDetails(
                        "package_manager",
                        "request_active",
                        "package_request_pending",
                        "recoverable",
                        "wait_for_native_work",
                        "The Package Manager Request handle is retained. Wait for its completion; restarting the Editor is not required."
                    );
            }
            blocker = new NativeAdmissionBlocker(reason, reason != "recovery_required", recovery);
            return reason != null;
        }

        private static bool RequiresRecovery(NativeRecoveryDetails recovery) => recovery != null && recovery.Mode != "recoverable";

        // An observable owner must not hide a different owner whose completion proof was lost.
        internal static NativeRecoveryDetails PreferRecovery(NativeRecoveryDetails first, NativeRecoveryDetails second)
        {
            int Rank(NativeRecoveryDetails value) =>
                value == null ? 0
                : value.Mode == "restart_required" ? 3
                : value.Mode == "unknown" ? 2
                : 1;
            return Rank(second) > Rank(first) ? second : first;
        }

        private static bool StartsPackageClient(string commandName, JObject parameters)
        {
            // Arbitrary code may call Client too. Ordinary scene/cached reads can continue
            // while a retained read-only UPM request runs; another Client method cannot.
            if (string.IsNullOrEmpty(commandName) || commandName == "execute_code")
                return true;
            if (!string.Equals(commandName, "manage_packages", StringComparison.Ordinal))
                return false;
            string action = parameters?["action"]?.Type == JTokenType.String ? ((string)parameters["action"]).ToLowerInvariant() : "";
            return action == "add_package"
                || action == "remove_package"
                || action == "embed_package"
                || action == "list_packages"
                || action == "search_packages"
                || action == "resolve_packages"
                || action == "add_registry"
                || action == "remove_registry";
        }
    }
}
