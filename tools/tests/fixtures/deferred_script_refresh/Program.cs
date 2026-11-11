using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using UnityEditor;

internal static class Program
{
    private static int failures;
    private static int cases;
    private const BindingFlags Flags = BindingFlags.NonPublic | BindingFlags.Static;

    private static int Main()
    {
        Check("queued refresh blocks admission before native compilation", () =>
        {
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            Require(Blocked(), "Pending script import was invisible to native admission.");
            Require(AssetDatabase.Imports == 0 && CompilationJobTracker.Reservation.State == null, "Native work started before the callback.");
        });
        Check("callback reserves before import and retains until matching completion", () =>
        {
            AssetDatabase.OnImport = () => Require(CompilationJobTracker.Reservation.State != null, "Import started without native ownership.");
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 1 && UnityEditor.Compilation.CompilationPipeline.Requests == 1, "Expected one import and one compile trigger.");
            Require(Blocked(), "Queued native request was released before its start event.");
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            Require(Blocked(), "Idle sample released queued native request.");
            var context = new object();
            CompilationJobTracker.Reservation.Started(context);
            CompilationJobTracker.Reservation.Finished(new object());
            Require(Blocked(), "Unrelated finish released ownership.");
            CompilationJobTracker.Reservation.Finished(context);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            Require(!Blocked(), "Completed refresh did not release admission.");
        });
        Check("foreign native work is rechecked before deferred invocation", () =>
        {
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            TestRunnerService.HasOwnedNativeRun = true;
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 0, "Deferred import overlapped a native test owner.");
            TestRunnerService.HasOwnedNativeRun = false;
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 1, "Deferred work self-blocked after foreign owner settled.");
        });
        Check("revoked consent cancels deferred native invocation", () =>
        {
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            ManageScript.Consent = false;
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 0 && !Blocked(), "Revoked callback ran or retained an impossible admission fence.");
        });
        Check("play suspends pending work and edit mode resumes it", () =>
        {
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            EditorApplication.isPlayingOrWillChangePlaymode = true;
            EditorApplication.ChangePlayMode(PlayModeStateChange.ExitingEditMode);
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 0 && !Blocked(), "Suspended refresh ran during Play or blocked Play observation.");
            EditorApplication.isPlayingOrWillChangePlaymode = false;
            EditorApplication.ChangePlayMode(PlayModeStateChange.EnteredEditMode);
            Field("_lastRequest").SetValue(null, DateTime.UtcNow.AddSeconds(-1));
            Require(Blocked(), "Resumed pending work was not published before its callback.");
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 1, "Pending saved source was lost on Play transition.");
        });
        Check("reload cancels uninvoked callbacks without creating native ownership", () =>
        {
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            AssemblyReloadEvents.Reload();
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 0 && !Blocked(), "A reload left late native work or a stale queued barrier.");
        });
        Check("native import failure retains explicit recovery instead of losing ownership", () =>
        {
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            AssetDatabase.OnImport = () => throw new InvalidOperationException("injected import failure");
            EditorApplication.Tick();
            Require(CompilationJobTracker.Reservation.Blocker(0) == "recovery_required", "Uncertain native import lost its ownership.");
            Require(UnityEditor.Compilation.CompilationPipeline.Requests == 0, "Compilation followed failed import.");
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 1, "Uncertain import was automatically replayed.");
        });
        Check("partial import failure retains uncertainty beyond an earlier running cycle", () =>
        {
            var context = new object();
            AssetDatabase.OnImport = () =>
            {
                if (AssetDatabase.Imports == 1)
                    CompilationJobTracker.Reservation.Started(context);
                else
                    throw new InvalidOperationException("second import failed");
            };
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            RefreshDebounce.Schedule("Assets/Two.cs", TimeSpan.Zero);
            EditorApplication.Tick();
            Require(CompilationJobTracker.Reservation.Blocker(0) == "recovery_required", "Later uncertain invocation was hidden by the earlier running cycle.");
            CompilationJobTracker.Reservation.Finished(context);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            Require(Blocked(), "Earlier cycle completion discharged a later uncertain invocation.");
            var later = new object();
            CompilationJobTracker.Reservation.Started(later);
            CompilationJobTracker.Reservation.Finished(later);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            Require(!Blocked(), "A subsequent cycle did not settle the partial batch uncertainty.");
        });
        Check("canceled stale delegate cannot run a replacement batch", () =>
        {
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            var stale = EditorApplication.delayCall;
            ManageScript.Consent = false;
            EditorApplication.Tick();
            ManageScript.Consent = true;
            RefreshDebounce.Schedule("Assets/Two.cs", TimeSpan.FromMinutes(1));
            stale();
            Require(AssetDatabase.Imports == 0, "A canceled callback invoked replacement work before its window.");
        });
        Check("import-triggered compilation does not issue a redundant request", () =>
        {
            AssetDatabase.OnImport = () => CompilationJobTracker.Reservation.Started(new object());
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            EditorApplication.Tick();
            Require(UnityEditor.Compilation.CompilationPipeline.Requests == 0, "Import-triggered cycle was followed by a second void request.");
            Require(Blocked(), "Actual compilation ownership was dropped.");
        });
        Check("native ownership survives reload with explicit recovery", () =>
        {
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            EditorApplication.Tick();
            Require(CompilationJobTracker.Reservation.State != null, "No native reservation to restore.");
            var restored = new OwnedCompilationReservation(CompilationJobTracker.Reservation.State);
            Require(restored.Blocker(0) == "recovery_required", "Reload lost unproven native work.");
        });
        Check("unknown trigger exposes recovery and reconciles a same-domain late cycle", () =>
        {
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            AssetDatabase.OnImport = () => throw new InvalidOperationException("native outcome unknown");
            EditorApplication.Tick();
            Require(NativeOperationBarriers.TryGetBlocker("manage_build", null, out var blocker), "Unknown native call became admissible.");
            Require(blocker.Recovery.Reason == "compilation_trigger_outcome_unknown", "Recovery reason is not observable through admission.");
            Require(blocker.Recovery.Action == "inspect_native_work_then_restart_editor", "Recovery does not provide the supported restart path.");
            var restored = new OwnedCompilationReservation(CompilationJobTracker.Reservation.State);
            var context = new object();
            CompilationJobTracker.Reservation.Started(context);
            CompilationJobTracker.Reservation.Finished(context);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            Require(!Blocked(), "Same-domain matching completion did not reconcile unknown trigger.");
            restored.Started(context);
            restored.Finished(context);
            restored.ObserveQuiescence(true);
            restored.ObserveQuiescence(true);
            Require(restored.Blocker(0) == "recovery_required", "Reloaded ownership adopted an unrelated pipeline context.");
            Require(new OwnedCompilationReservation().Blocker(0) == null, "Fresh session cannot clear a lost SessionState owner.");
        });
        Check("later failed import cannot reuse an earlier completed cycle", () =>
        {
            var earlier = new object();
            AssetDatabase.OnImport = () =>
            {
                if (AssetDatabase.Imports == 1)
                {
                    CompilationJobTracker.Reservation.Started(earlier);
                    CompilationJobTracker.Reservation.Finished(earlier);
                }
                else
                    throw new InvalidOperationException("later import queued work then failed");
            };
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            RefreshDebounce.Schedule("Assets/Two.cs", TimeSpan.Zero);
            EditorApplication.Tick();
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            Require(
                CompilationJobTracker.Reservation.Blocker(0) == "recovery_required",
                "An earlier completed import cycle released a later uncertain invocation."
            );
            var restored = new OwnedCompilationReservation(CompilationJobTracker.Reservation.State);
            restored.ObserveQuiescence(true);
            restored.ObserveQuiescence(true);
            Require(restored.Blocker(0) == "recovery_required", "Reload restored superseded completion proof.");
            var later = new object();
            CompilationJobTracker.Reservation.Started(later);
            CompilationJobTracker.Reservation.Finished(later);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            Require(!Blocked(), "Later actual cycle did not settle the uncertainty.");
        });
        Check("reentrant schedule remains admitted and processes on a later callback", () =>
        {
            AssetDatabase.OnImport = () =>
            {
                if (AssetDatabase.Imports == 1)
                    RefreshDebounce.Schedule("Assets/Two.cs", TimeSpan.Zero);
                var context = new object();
                CompilationJobTracker.Reservation.Started(context);
                CompilationJobTracker.Reservation.Finished(context);
            };
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            EditorApplication.Tick();
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            Require(Blocked(), "Reentrant saved source lost its queued admission marker.");
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 2, "Reentrant saved source was stranded without a callback.");
        });
        Check("reentrant schedule does not automatically retry a failed batch path", () =>
        {
            AssetDatabase.OnImport = () =>
            {
                if (AssetDatabase.Imports == 1)
                {
                    RefreshDebounce.Schedule("Assets/Two.cs", TimeSpan.Zero);
                    throw new InvalidOperationException("first import outcome unknown");
                }
            };
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            EditorApplication.Tick();
            var context = new object();
            CompilationJobTracker.Reservation.Started(context);
            CompilationJobTracker.Reservation.Finished(context);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 2, "New work was lost or the failed import was automatically replayed.");
            Require(AssetDatabase.LastPath == "Assets/Two.cs", "The scheduled callback retried the uncertain failed path.");
            var second = new object();
            CompilationJobTracker.Reservation.Started(second);
            CompilationJobTracker.Reservation.Finished(second);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            CompilationJobTracker.Reservation.ObserveQuiescence(true);
            EditorApplication.isPlayingOrWillChangePlaymode = true;
            EditorApplication.ChangePlayMode(PlayModeStateChange.ExitingEditMode);
            EditorApplication.isPlayingOrWillChangePlaymode = false;
            EditorApplication.ChangePlayMode(PlayModeStateChange.EnteredEditMode);
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 2, "Play cleanup automatically replayed a retained failed import.");
        });
        Check("play flag before lifecycle notification preserves pending paths", () =>
        {
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            EditorApplication.isPlayingOrWillChangePlaymode = true;
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 0, "Native import began after Play was requested.");
            EditorApplication.isPlayingOrWillChangePlaymode = false;
            EditorApplication.ChangePlayMode(PlayModeStateChange.EnteredEditMode);
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 1, "Play suspension discarded the pending scheduled path.");
        });
        foreach (bool compiling in new[] { false, true })
        {
            Check("between-import refusal preserves earlier successful ownership, compiling=" + compiling, () =>
            {
                var context = new object();
                AssetDatabase.OnImport = () =>
                {
                    CompilationJobTracker.Reservation.Started(context);
                    if (compiling)
                        EditorStateCache.ActualCompiling = true;
                    else
                        ManageScript.Consent = false;
                };
                RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
                RefreshDebounce.Schedule("Assets/Two.cs", TimeSpan.Zero);
                EditorApplication.Tick();
                Require(AssetDatabase.Imports == 1 && UnityEditor.Compilation.CompilationPipeline.Requests == 0, "The refused second native call still ran.");
                Require(!CompilationJobTracker.Reservation.State.InvocationUncertain, "A pre-invocation refusal was misreported as a native call failure.");
                CompilationJobTracker.Reservation.Finished(context);
                EditorStateCache.ActualCompiling = false;
                CompilationJobTracker.Reservation.ObserveQuiescence(true);
                CompilationJobTracker.Reservation.ObserveQuiescence(true);
                Require(!Blocked(), "The successfully invoked earlier cycle could not settle after the next call was refused.");
            });
        }
        Check("refusal before the first native call releases unused ownership", () =>
        {
            CompilationJobTracker.Reservation.Changed = () =>
            {
                if (CompilationJobTracker.Reservation.State?.Phase == "pending")
                    ManageScript.Consent = false;
            };
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 0 && CompilationJobTracker.Reservation.State == null, "An uninvoked callback retained native ownership.");
        });
        Check("actual compilation request exception retains invocation uncertainty", () =>
        {
            UnityEditor.Compilation.CompilationPipeline.OnRequest = () => throw new InvalidOperationException("request queued work then threw");
            RefreshDebounce.Schedule("Assets/One.cs", TimeSpan.Zero);
            EditorApplication.Tick();
            Require(AssetDatabase.Imports == 1 && UnityEditor.Compilation.CompilationPipeline.Requests == 1, "Expected one actual native compile request.");
            Require(CompilationJobTracker.Reservation.State.InvocationUncertain, "An actual native request exception lost its uncertainty fence.");
            Require(CompilationJobTracker.Reservation.Blocker(0) == "recovery_required", "Unknown native request became admissible.");
        });
        Console.WriteLine($"RESULT: {cases - failures}/{cases} passed; failures={failures}");
        return failures == 0 ? 0 : 1;
    }

    private static FieldInfo Field(string name) => typeof(RefreshDebounce).GetField(name, Flags);

    private static bool Blocked() => NativeOperationBarriers.TryGetBlocker("manage_build", null, out _);

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }

    private static void Check(string name, Action run)
    {
        cases++;
        EditorApplication.Clear();
        Field("_scheduled").SetValue(null, false);
        Field("_compilePending").SetValue(null, false);
        ((HashSet<string>)Field("_paths").GetValue(null)).Clear();
        (Field("_scheduledPaths")?.GetValue(null) as HashSet<string>)?.Clear();
        Field("_processing")?.SetValue(null, false);
        Field("_activePaths")?.SetValue(null, null);
        Field("_stopped")?.SetValue(null, false);
        AssetDatabase.Imports = UnityEditor.Compilation.CompilationPipeline.Requests = 0;
        AssetDatabase.OnImport = null;
        UnityEditor.Compilation.CompilationPipeline.OnRequest = null;
        EditorStateCache.ActualCompiling = false;
        TestRunnerService.HasOwnedNativeRun = false;
        ManageScript.Consent = true;
        CompilationJobTracker.Reservation = new OwnedCompilationReservation();
        try
        {
            run();
            Console.WriteLine("PASS: " + name);
        }
        catch (Exception error)
        {
            failures++;
            Console.WriteLine("FAIL: " + name + " -- " + error.Message);
        }
    }
}

namespace UnityEditor
{
    internal enum PlayModeStateChange
    {
        ExitingEditMode,
        EnteredPlayMode,
        ExitingPlayMode,
        EnteredEditMode,
    }

    [Flags]
    internal enum ImportAssetOptions
    {
        ForceUpdate = 1,
        ForceSynchronousImport = 2,
    }

    internal static class EditorApplication
    {
        public delegate void CallbackFunction();
        public static CallbackFunction delayCall;
        public static event Action quitting;
        public static event Action<PlayModeStateChange> playModeStateChanged;
        public static bool isUpdating,
            isPlayingOrWillChangePlaymode;
        public static double timeSinceStartup => 100;

        public static void QueuePlayerLoopUpdate() { }

        public static void Tick()
        {
            var callbacks = delayCall;
            delayCall = null;
            callbacks?.Invoke();
        }

        public static void ChangePlayMode(PlayModeStateChange state) => playModeStateChanged?.Invoke(state);

        public static void Clear()
        {
            delayCall = null;
            isPlayingOrWillChangePlaymode = isUpdating = false;
        }
    }

    internal static class AssemblyReloadEvents
    {
        public static event Action beforeAssemblyReload;

        public static void Reload() => beforeAssemblyReload?.Invoke();
    }

    internal static class AssetDatabase
    {
        internal static int Imports;
        internal static Action OnImport;
        internal static string LastPath;

        public static void ImportAsset(string path, ImportAssetOptions options)
        {
            Imports++;
            LastPath = path;
            OnImport?.Invoke();
        }
    }

    internal static class BuildPipeline
    {
        public static bool isBuildingPlayer;
    }
}

namespace UnityEditor.Compilation
{
    internal static class CompilationPipeline
    {
        internal static int Requests;
        internal static Action OnRequest;

        public static void RequestScriptCompilation()
        {
            Requests++;
            OnRequest?.Invoke();
        }
    }
}

namespace MCPForUnity.Runtime.Helpers
{
    internal static class AssetPathUtility
    {
        internal static string NormalizeSeparators(string path) => path.Replace('\\', '/');
    }
}

namespace MCPForUnity.Editor.Helpers
{
    internal static class McpLog
    {
        internal static void Error(string message) { }
    }
}

namespace MCPForUnity.Editor.Tools
{
    internal static class ManageScript
    {
        internal static bool Consent = true;

        internal static object RequireExplicitConsent() => Consent ? null : new object();
    }

    internal static class ManagePackages
    {
        internal static bool HasNativeMutation,
            HasNativeQuery,
            MutationRecoveryRequired,
            QueryRecoveryRequired;
    }
}

namespace MCPForUnity.Editor.Tools.Build
{
    internal static class BuildRunner
    {
        internal static bool NativeRecoveryRequired,
            HasNativeOwner;

        internal static bool HasForeignNativeOwner(string id, string batch) => false;
    }
}

namespace MCPForUnity.Editor.Tools.ProjectAuditor
{
    internal static class ProjectAuditorJobs
    {
        internal static NativeRecoveryDetails RecoveryDetails;
        internal static bool HasNativeOwner;
    }
}

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    internal static class UnityPlayScenarioHost
    {
        internal static bool ObservePendingSceneOperation(out bool unknown)
        {
            unknown = false;
            return false;
        }
    }

    internal static class PlayScenarioService
    {
        internal static bool IsBusy;
    }

    internal static class PlayScenarioSuiteService
    {
        internal static bool IsBusy;
    }
}

namespace MCPForUnity.Editor.Services
{
    internal static class CompilationJobTracker
    {
        internal static OwnedCompilationReservation Reservation = new();
    }

    internal static class EditorStateCache
    {
        internal static bool IsDomainReloadPending,
            IsPlayModeTransition;

        internal static bool ActualCompiling;

        internal static bool GetActualIsCompiling() => ActualCompiling;
    }

    internal static class PlayModeReadiness
    {
        internal static bool HasPendingPlayRequest;
    }

    internal static class TestRunnerService
    {
        internal static NativeRecoveryDetails RetiredRunRecoveryDetails;
        internal static bool HasOwnedNativeRun;
    }

    internal sealed class TestControl
    {
        internal bool Released;
    }

    internal static class TestJobManager
    {
        internal static NativeRecoveryDetails NativeRecoveryDetails;
        internal static bool HasRunningJob,
            NativeAdmissionBlocked;

        internal static TestControl ControlFor(string id) => null;
    }
}

namespace MCPForUnity.Editor.Services.Transport
{
    internal sealed class NativeAdmissionBlocker
    {
        internal readonly string Reason;
        internal readonly NativeRecoveryDetails Recovery;

        internal NativeAdmissionBlocker(string reason, bool retryable, NativeRecoveryDetails recovery)
        {
            Reason = reason;
            Recovery = recovery;
        }
    }

    internal sealed class NativeRecoveryDetails
    {
        internal readonly string Mode,
            JobId,
            Message,
            Reason,
            Action;

        internal NativeRecoveryDetails(
            string family,
            string phase,
            string reason,
            string mode,
            string action,
            string message,
            string id = null,
            string generation = null
        )
        {
            Mode = mode;
            JobId = id;
            Message = message;
            Reason = reason;
            Action = action;
        }
    }
}
