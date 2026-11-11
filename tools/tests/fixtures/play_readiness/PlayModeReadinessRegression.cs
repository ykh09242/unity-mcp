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
        EditorStateCache.Reads = 0;
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
        internal static int Reads;

        internal static bool GetActualIsCompiling()
        {
            Reads++;
            return ActualIsCompiling;
        }
    }

    internal static class EditorMainThreadWakeup
    {
        internal static IDisposable Schedule(Action callback) => throw new InvalidOperationException("Readiness must not request Play while already playing.");
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
