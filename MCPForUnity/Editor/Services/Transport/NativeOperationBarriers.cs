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
        internal string OwnedCompilation;
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

        private static NativeBarrierState Capture() =>
            new NativeBarrierState
            {
                Compiling = EditorStateCache.GetActualIsCompiling(),
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
                RecoveryRequired =
                    TestJobManager.NativeRecoveryRequired
                    || BuildRunner.NativeRecoveryRequired
                    || TestRunnerService.HasRetiredRun && !TestJobManager.NativeAdmissionBlocked,
                OwnedCompilation = CompilationJobTracker.Reservation.Blocker(EditorApplication.timeSinceStartup),
                PackageMutation = ManagePackages.HasNativeMutation,
                PackageQuery = ManagePackages.HasNativeQuery,
                PackageMutationRecovery = ManagePackages.MutationRecoveryRequired,
                PackageQueryRecovery = ManagePackages.QueryRecoveryRequired,
            };

        internal static bool Evaluate(string commandName, JObject parameters, NativeBarrierState state, out NativeAdmissionBlocker blocker)
        {
            bool packageCandidate = StartsPackageClient(commandName, parameters);
            string reason =
                state.RecoveryRequired
                || state.OwnedCompilation == "recovery_required"
                || state.PackageMutationRecovery
                || packageCandidate && state.PackageQueryRecovery
                    ? "recovery_required"
                : state.Compiling ? "editor_compiling"
                : state.Importing ? "editor_importing"
                : state.Transitioning || state.OwnedJob || state.OwnedCompilation != null || state.PackageMutation || packageCandidate && state.PackageQuery
                    ? "native_job_active"
                : null;
            blocker = new NativeAdmissionBlocker(reason, reason != "recovery_required");
            return reason != null;
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
