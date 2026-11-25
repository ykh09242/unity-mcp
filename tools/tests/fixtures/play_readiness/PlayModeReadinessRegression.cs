using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

internal static class PlayModeReadinessRegression
{
    private static int failures;
    private static int cases;

    private static int Main()
    {
        try
        {
            return RunFixture();
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }

    private static int RunFixture()
    {
        Check("deferred raw compilation flag allows a loaded scene", () =>
        {
            EditorApplication.isCompiling = true;
            EditorStateCache.ActualIsCompiling = false;
            string id = Start("scene_loaded");
            Require(Status(id) == "succeeded", "A deferred raw compilation flag blocked readiness.");
            Require(EditorStateCache.Reads > 0, "The actual-compilation predicate was not read.");
        });
        Check("true compilation blocks until the actual pipeline finishes", () =>
        {
            EditorApplication.isCompiling = true;
            EditorStateCache.ActualIsCompiling = true;
            string id = Start("scene_loaded");
            Require(Status(id) == "running", "Active compilation was ignored.");
            EditorStateCache.ActualIsCompiling = false;
            Require(Status(id) == "succeeded", "The job did not finish after compilation while reload remained deferred.");
        });
        Check("first frame still requires a subsequent frame", () =>
        {
            EditorApplication.isCompiling = true;
            string id = Start("first_frame");
            Require(Status(id) == "running", "Entry frame was accepted as a subsequent frame.");
            Time.frameCount++;
            Require(Status(id) == "succeeded", "Subsequent frame remained blocked by deferred compilation.");
        });
        Check("asset updating and unloaded scene still block readiness", () =>
        {
            EditorApplication.isUpdating = true;
            string id = Start("scene_loaded");
            Require(Status(id) == "running", "Asset updating was ignored.");
            EditorApplication.isUpdating = false;
            SceneManager.Loaded = false;
            Require(Status(id) == "running", "Unloaded scene was ignored.");
            SceneManager.Loaded = true;
            Require(Status(id) == "succeeded", "Loaded scene did not finish.");
        });
        Check("unconfirmed entry remains pending", () =>
        {
            SessionState.SetBool("MCPForUnity.PlayModeReadiness.EnteredV1", false);
            string id = Start("scene_loaded");
            Require(Status(id) == "running", "An unobserved entry was accepted.");
        });
        Check("a pending domain reload or Play exit cannot confirm readiness", () =>
        {
            EditorStateCache.IsDomainReloadPending = true;
            string id = Start("scene_loaded");
            Require(Status(id) == "running", "Readiness completed while a domain reload was pending.");
            EditorStateCache.IsDomainReloadPending = false;
            EditorStateCache.IsPlayModeTransition = true;
            Require(Status(id) == "running", "Readiness completed while exiting Play Mode.");
            EditorStateCache.IsPlayModeTransition = false;
            Require(Status(id) == "succeeded", "Readiness did not resume when stable.");
        });
        Check("cancelling observation preserves play ownership", () =>
        {
            EditorStateCache.ActualIsCompiling = true;
            EditorApplication.isCompiling = true;
            string id = Start("scene_loaded");
            int assignments = EditorApplication.PlayAssignments;
            JObject response = JObject.FromObject(PlayModeReadiness.Get(new JObject { ["job_id"] = id }, true));
            Require((string)response["data"]["status"] == "cancelled", "Observation did not cancel.");
            Require(EditorApplication.isPlaying && assignments == EditorApplication.PlayAssignments, "Cancellation changed Play Mode.");
        });
        Check("deferred Play rechecks a foreign native owner", () =>
        {
            EditorApplication.isPlaying = false;
            string id = Start("scene_loaded");
            int assignments = EditorApplication.PlayAssignments;
            MCPForUnity.Editor.Services.Transport.NativeOperationBarriers.Blocked = true;
            EditorMainThreadWakeup.RunPending();
            Require(Status(id) == "failed", "Readiness did not report the new native blocker.");
            Require(EditorApplication.PlayAssignments == assignments, "Play was requested while another owner was active.");
        });
        Check("undispatched Play waits for compilation and import across reload", () =>
        {
            EditorApplication.isPlaying = false;
            string id = Start("scene_loaded");
            int assignments = EditorApplication.PlayAssignments;
            EditorStateCache.ActualIsCompiling = true;
            EditorMainThreadWakeup.RunPending();
            EditorApplication.RaiseUpdate();
            Require(Status(id) == "running" && PlayModeReadiness.HasPendingPlayRequest, "A transient compilation retired the admitted request.");
            Require(EditorApplication.PlayAssignments == assignments, "Play was requested during compilation.");
            EditorStateCache.ActualIsCompiling = false;
            EditorApplication.isUpdating = true;
            typeof(PlayModeReadiness).GetMethod("Restore", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic).Invoke(null, null);
            EditorMainThreadWakeup.RunPending();
            EditorApplication.RaiseUpdate();
            Require(Status(id) == "running" && EditorApplication.PlayAssignments == assignments, "Import after reload did not retain the queued request.");
            EditorApplication.isUpdating = false;
            EditorApplication.RaiseUpdate();
            EditorApplication.RaiseUpdate();
            Require(EditorApplication.PlayAssignments == assignments + 1, "The settled Editor did not receive exactly one Play request.");
        });
        Check("cancellation while compilation is pending cannot enter Play later", () =>
        {
            EditorApplication.isPlaying = false;
            string id = Start("first_frame");
            int assignments = EditorApplication.PlayAssignments;
            EditorStateCache.ActualIsCompiling = true;
            EditorMainThreadWakeup.RunPending();
            PlayModeReadiness.Get(new JObject { ["job_id"] = id }, true);
            EditorStateCache.ActualIsCompiling = false;
            EditorApplication.RaiseUpdate();
            Require(Status(id) == "cancelled" && EditorApplication.PlayAssignments == assignments, "Cancelled readiness dispatched after compilation.");
        });
        Check("compilation cannot extend the original Play deadline", () =>
        {
            EditorApplication.isPlaying = false;
            double now = 100;
            PlayModeReadiness._clockOverride = () => now;
            string id = Start("first_frame");
            int assignments = EditorApplication.PlayAssignments;
            EditorStateCache.ActualIsCompiling = true;
            EditorMainThreadWakeup.RunPending();
            now += 301;
            EditorApplication.RaiseUpdate();
            EditorStateCache.ActualIsCompiling = false;
            EditorApplication.RaiseUpdate();
            Require(Status(id) == "timed_out" && EditorApplication.PlayAssignments == assignments, "An expired Play request was dispatched.");
        });
        Check("an owner appearing during compilation still blocks the resumed request", () =>
        {
            EditorApplication.isPlaying = false;
            string id = Start("first_frame");
            int assignments = EditorApplication.PlayAssignments;
            EditorStateCache.ActualIsCompiling = true;
            EditorMainThreadWakeup.RunPending();
            EditorStateCache.ActualIsCompiling = false;
            MCPForUnity.Editor.Services.Transport.NativeOperationBarriers.Blocked = true;
            EditorApplication.RaiseUpdate();
            Require(Status(id) == "failed" && EditorApplication.PlayAssignments == assignments, "The resumed request bypassed foreign native work.");
        });
        Check("deferred Play observes entry already requested elsewhere", () =>
        {
            EditorApplication.isPlaying = false;
            string id = Start("first_frame");
            EditorApplication.isPlaying = true;
            int assignments = EditorApplication.PlayAssignments;
            EditorMainThreadWakeup.RunPending();
            Require(Status(id) == "running", "Observation ended before the entry frame was confirmed.");
            Require(EditorApplication.PlayAssignments == assignments, "The queued callback requested Play a second time.");
        });
        Check("deferred Play enters once when still admitted", () =>
        {
            EditorApplication.isPlaying = false;
            string id = Start("scene_loaded");
            int assignments = EditorApplication.PlayAssignments;
            EditorMainThreadWakeup.RunPending();
            Require(EditorApplication.PlayAssignments == assignments + 1, "The admitted callback did not request Play exactly once.");
            Require(Status(id) == "running", "The setter alone confirmed entry.");
        });
        Check("replacement preserves the previous job and cannot replay its ID", () =>
        {
            string first = Start("scene_loaded");
            Require(Status(first) == "succeeded", "First job did not complete.");
            string next = Start("first_frame");
            JObject historical = JObject.FromObject(PlayModeReadiness.Get(new JObject { ["job_id"] = first }, true));
            Require((string)historical["data"]?["status"] == "succeeded", "The replaced job lost its terminal result.");
            Require(Status(next) == "running", "Cancelling the historical ID cancelled the new observation.");
            Time.frameCount++;
            Require(Status(next) == "succeeded", "Replacement did not complete.");
            EditorApplication.isPlaying = false;
            JObject retry = JObject.FromObject(PlayModeReadiness.Start(new JObject { ["job_id"] = first, ["wait_until"] = "scene_loaded" }));
            Require((string)retry["data"]?["status"] == "succeeded" && !PlayModeReadiness.HasPendingPlayRequest, "Retry queued another Play request.");
            JObject conflict = JObject.FromObject(PlayModeReadiness.Start(new JObject { ["job_id"] = first, ["wait_until"] = "first_frame" }));
            Require((string)conflict["error"] == "play_readiness_job_id_conflict", "A different request reused a retained ID.");
        });
        Check("the eight preceding terminal jobs survive reload", () =>
        {
            var ids = new List<string>();
            for (int i = 0; i < 10; i++)
            {
                string id = Start("scene_loaded");
                Require(Status(id) == "succeeded", "A history fixture job did not complete.");
                ids.Add(id);
            }
            typeof(PlayModeReadiness).GetMethod("Restore", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic).Invoke(null, null);
            JObject oldest = JObject.FromObject(PlayModeReadiness.Get(new JObject { ["job_id"] = ids[0] }, false));
            Require((string)oldest["error"] == "play_readiness_job_not_found", "The ninth prior job exceeded the retention bound.");
            for (int i = 1; i < ids.Count; i++)
                Require(Status(ids[i]) == "succeeded", "A retained terminal result was lost across reload.");
        });
        Console.WriteLine("RESULT: " + (cases - failures) + "/" + cases + " passed; failures=" + failures);
        return failures == 0 ? 0 : 1;
    }

    private static string Start(string target)
    {
        JObject response = JObject.FromObject(PlayModeReadiness.Start(new JObject { ["wait_until"] = target }));
        Require((bool)response["success"], response.ToString());
        return (string)response["data"]["job_id"];
    }

    private static string Status(string id) => (string)JObject.FromObject(PlayModeReadiness.Get(new JObject { ["job_id"] = id }, false))["data"]["status"];

    private static void Check(string name, Action run)
    {
        cases++;
        EditorApplication.isPlaying = true;
        EditorApplication.isCompiling = EditorApplication.isUpdating = false;
        EditorStateCache.ActualIsCompiling = false;
        EditorStateCache.IsDomainReloadPending = EditorStateCache.IsPlayModeTransition = false;
        EditorStateCache.Reads = 0;
        MCPForUnity.Editor.Services.Transport.NativeOperationBarriers.Blocked = false;
        Time.frameCount = 10;
        SceneManager.Loaded = true;
        SessionState.SetBool("MCPForUnity.PlayModeReadiness.EnteredV1", true);
        try
        {
            run();
            Console.WriteLine("PASS: " + name);
        }
        catch (Exception exception)
        {
            failures++;
            Console.WriteLine("FAIL: " + name + " -- " + exception.Message);
        }
        finally
        {
            PlayModeReadiness.Cancel("fixture complete");
            PlayModeReadiness._clockOverride = null;
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }
}

// Mutable boundaries model native Editor signals; the readiness implementation is real.
namespace UnityEditor
{
    internal sealed class InitializeOnLoadAttribute : Attribute { }

    internal enum PlayModeStateChange
    {
        EnteredPlayMode,
        ExitingPlayMode,
        EnteredEditMode,
    }

    internal static class EditorApplication
    {
        internal static event Action update;

        internal static void RaiseUpdate() => update?.Invoke();

        internal static event Action<PlayModeStateChange> playModeStateChanged;
        internal static int PlayAssignments;
        private static bool playing;
        internal static bool isPlaying
        {
            get => playing;
            set
            {
                playing = value;
                PlayAssignments++;
            }
        }
        internal static bool isPlayingOrWillChangePlaymode => isPlaying;
        internal static bool isCompiling,
            isUpdating;
        internal static bool isPaused => false;
        internal static double timeSinceStartup => 100;
    }

    internal static class SessionState
    {
        private static readonly Dictionary<string, object> Values = new Dictionary<string, object>();

        internal static string GetString(string key, string fallback) => Values.TryGetValue(key, out var value) ? (string)value : fallback;

        internal static bool GetBool(string key, bool fallback) => Values.TryGetValue(key, out var value) ? (bool)value : fallback;

        internal static void SetString(string key, string value) => Values[key] = value;

        internal static void SetBool(string key, bool value) => Values[key] = value;

        internal static void EraseString(string key) => Values.Remove(key);
    }
}

namespace UnityEngine
{
    internal static class Time
    {
        internal static int frameCount;
    }
}

namespace UnityEngine.SceneManagement
{
    internal struct Scene
    {
        internal bool IsValid() => true;

        internal bool isLoaded => SceneManager.Loaded;
    }

    internal static class SceneManager
    {
        internal static bool Loaded;

        internal static Scene GetActiveScene() => new Scene();
    }
}

namespace MCPForUnity.Editor.Services
{
    internal static class EditorStateCache
    {
        internal static bool ActualIsCompiling;
        internal static bool IsDomainReloadPending;
        internal static bool IsPlayModeTransition;
        internal static int Reads;

        internal static bool GetActualIsCompiling()
        {
            Reads++;
            return ActualIsCompiling;
        }
    }

    internal static class EditorMainThreadWakeup
    {
        private static Action pending;

        internal static IDisposable Schedule(Action callback)
        {
            if (EditorApplication.isPlaying || pending != null)
                throw new InvalidOperationException("Readiness scheduled redundant work.");
            pending = callback;
            return new Callback();
        }

        internal static void RunPending() => (pending ?? throw new InvalidOperationException("Missing Play request."))();

        private sealed class Callback : IDisposable
        {
            public void Dispose() => pending = null;
        }
    }
}

namespace MCPForUnity.Editor.Services.Transport
{
    internal static class NativeOperationBarriers
    {
        internal static bool Blocked;

        internal static bool TryGetBlocker(string commandName, JObject parameters, out NativeAdmissionBlocker blocker)
        {
            if (PlayModeReadiness.HasPendingPlayRequest)
                throw new InvalidOperationException("The readiness callback treated itself as a foreign owner.");
            blocker = new NativeAdmissionBlocker
            {
                Reason =
                    EditorStateCache.ActualIsCompiling ? "editor_compiling"
                    : EditorApplication.isUpdating ? "editor_importing"
                    : "native_job_active",
            };
            return Blocked || EditorStateCache.ActualIsCompiling || EditorApplication.isUpdating || EditorStateCache.IsDomainReloadPending;
        }
    }

    internal sealed class NativeAdmissionBlocker
    {
        internal string Reason;
    }
}

namespace MCPForUnity.Editor.Helpers
{
    internal static class PaginationBounds
    {
        internal static bool TryRead(JToken token, int fallback, int minimum, int maximum, string name, out int value, out string error)
        {
            value = token == null ? fallback : (int)token;
            error = null;
            return value >= minimum && value <= maximum;
        }
    }
}
