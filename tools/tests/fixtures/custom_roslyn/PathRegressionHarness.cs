#nullable disable
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEngine;

#if !COMPANION_PROBE
internal static class PathRegressionHarness
{
    private static int failures;

    private static int Main(string[] args)
    {
        Application.temporaryCachePath = args[0];
        foreach (var path in new[] { "MissingRoot/ExistingChild", "/MissingRoot/ExistingChild", "MissingRoot/Root/InactiveChild" })
        {
            Check("helper rejects missing first segment: " + path, () =>
                Require(Find(path) == null, "Resolved an unrelated suffix object"));
            Check("execute rejects missing first segment: " + path, () =>
            {
                var response = Execute(path);
                Require(response is ErrorResponse, "Expected not-found error, got " + response.GetType().Name);
                Require(((ErrorResponse)response).Message == "Target GameObject '" + path + "' not found", "Wrong not-found error");
                Require(RoslynRuntimeCompiler.Executions == 0, "Compiler executed against unrelated object");
            });
            Check("attachment rejects missing first segment: " + path, () =>
            {
                var response = Compile(path);
                Require(response is SuccessResponse, Describe(response));
                var data = JObject.FromObject(((SuccessResponse)response).Data);
                Require(data["attached_to"].Type == JTokenType.Null, "Attached to unrelated suffix object");
                Require(data["attached_type"].Type == JTokenType.Null, "Reported an unrelated attached type");
                Require(GameObject.All.Sum(go => go.AttachmentCount) == 0, "AddComponent was called on a scene object");
                Require(Debug.Warnings.Contains("[MCP] GameObject '" + path + "' not found"), "Missing attachment warning");
            });
        }

        foreach (var path in new[] { "ExistingChild", "Root/Panel/ActiveChild", "Root/InactiveChild", "/Root/InactiveChild" })
        {
            Check("helper preserves valid target: " + path, () =>
                Require(Find(path) == Expected(path), "Wrong helper target"));
            Check("execute preserves valid target: " + path, () =>
            {
                var response = Execute(path);
                Require(response is SuccessResponse, Describe(response));
                Require(RoslynRuntimeCompiler.Executions == 1, "Compiler not called exactly once");
                Require(RoslynRuntimeCompiler.LastTarget == Expected(path), "Compiler received wrong target");
            });
            Check("attachment preserves valid target: " + path, () =>
            {
                var response = Compile(path);
                Require(response is SuccessResponse, Describe(response));
                Require(Expected(path).AttachmentCount == 1, "Expected target was not attached exactly once");
                Require(GameObject.All.Sum(go => go.AttachmentCount) == 1, "Attached to an extra object");
            });
        }
        Check("missing child never switches to global suffix", () =>
            Require(Find("Root/MissingChild/ExistingChild") == null, "Resolved global suffix after missing child"));
        Check("interior empty segment is not skipped", () =>
            Require(Find("Root//InactiveChild") == null, "Interior empty path segment was normalized"));
        Console.WriteLine("RESULT: " + (23 - failures) + "/23 passed; failures=" + failures);
        return failures == 0 ? 0 : 1;
    }

    private static void Check(string name, Action test)
    {
        GameObject.Reset();
        new GameObject("ExistingChild");
        var root = new GameObject("Root");
        var panel = new GameObject("Panel", root);
        new GameObject("ActiveChild", panel);
        new GameObject("InactiveChild", root, false);
        try { test(); Console.WriteLine("PASS: " + name); }
        catch (Exception ex) { failures++; Console.WriteLine("FAIL: " + name + " -- " + ex.Message); }
    }

    private static GameObject Expected(string path) => GameObject.All.Single(go => go.name == path.Split('/').Last());
    private static GameObject Find(string path) => (GameObject)typeof(ManageRuntimeCompilation)
        .GetMethod("FindGameObjectByPath", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { path });
    private static object Execute(string path) => ManageRuntimeCompilation.HandleCommand(new JObject
    {
        ["action"] = "execute_with_roslyn", ["code"] = "public class Example {}", ["target_object"] = path
    });
    private static object Compile(string path) => ManageRuntimeCompilation.HandleCommand(new JObject
    {
        ["action"] = "compile_and_load", ["code"] = "public class Attachment : UnityEngine.MonoBehaviour {}",
        ["assembly_name"] = "PathRegression_" + Guid.NewGuid().ToString("N"), ["attach_to"] = path
    });
    private static string Describe(object response) => response is ErrorResponse error
        ? error.Message + " " + (error.Data == null ? "" : JObject.FromObject(error.Data).ToString()) : response.GetType().Name;
    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
}
#else
internal static class CompanionRegressionHarness
{
    public static global::RoslynRuntimeCompiler Compiler;
    private static int failures;
    private static int Main(string[] args)
    {
        foreach (var target in new[] { "Missing", "MissingRoot/ExistingChild" })
        {
            Check("static helper rejects explicit missing target: " + target, () =>
            {
                var success = RoslynMCPHelper.CompileAndExecuteStatic("public class Example {}", "Example", "Run", target, out string result);
                Require(!success && result == "GameObject '" + target + "' not found.", "Expected not-found; selected target="
                    + (Compiler.targetGameObject == null ? "<null>" : Compiler.targetGameObject.name) + "; history target="
                    + (Compiler.CompilationHistory.Count == 0 ? "<none>" : Compiler.CompilationHistory.Last().executionTarget) + "; observed " + result);
                Require(Compiler.targetGameObject == null && Compiler.CompilationHistory.Count == 0, "CompileAndExecute received compiler host");
            });
        }
        foreach (var target in new string[] { null, "", "ExistingChild" })
        {
            Check("static helper preserves target routing: " + (target ?? "<null>"), () =>
            {
                RoslynMCPHelper.CompileAndExecuteStatic("public class Example {}", "Example", "Run", target, out string result);
                var expected = string.IsNullOrEmpty(target) ? Compiler.gameObject : GameObject.Find(target);
                Require(Compiler.targetGameObject == expected, "Wrong target at real compiler boundary");
                Require(Compiler.CompilationHistory.Single().executionTarget == expected.name, "Wrong history target");
                Require(result.StartsWith("Failed: Compilation failed:", StringComparison.Ordinal), "Expected non-Editor compilation boundary");
            });
        }
        Check("attach helper rejects missing target", () =>
        {
            Require(!RoslynMCPHelper.CompileAndAttach("public class Example {}", "Example", "Missing", out string result)
                && result == "GameObject 'Missing' not found.", "Sibling not-found contract changed");
        });
        Console.WriteLine("RESULT: " + (6 - failures) + "/6 passed; failures=" + failures);
        return failures == 0 ? 0 : 1;
    }
    private static void Check(string name, Action test)
    {
        GameObject.Reset();
        new GameObject("ExistingChild");
        Compiler = new global::RoslynRuntimeCompiler { gameObject = new GameObject("CompilerHost") };
        Compiler.ClearHistory();
        typeof(RoslynMCPHelper).GetField("_compiler", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, null);
        try { test(); Console.WriteLine("PASS: " + name); }
        catch (Exception ex) { failures++; Console.WriteLine("FAIL: " + name + " -- " + ex.Message); }
    }
    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
}
#endif

// These seams model documented scene lookup only; the production tool is compiled verbatim.
namespace UnityEngine
{
    public class Object
    {
        public static T FindFirstObjectByType<T>() where T : class =>
#if COMPANION_PROBE
            CompanionRegressionHarness.Compiler as T;
#else
            MCPForUnity.Editor.Tools.RoslynRuntimeCompiler.Instance as T;
#endif
        public static void Destroy(Object value) { }
        public static void DestroyImmediate(Object value) { }
    }
    public class Component : Object { public GameObject gameObject; public string name => gameObject.name; }
    public class MonoBehaviour : Component { public void StartCoroutine(System.Collections.IEnumerator value) { } }
    public sealed class TextAreaAttribute : Attribute { public TextAreaAttribute(int min, int max) { } }
    public sealed class TooltipAttribute : Attribute { public TooltipAttribute(string text) { } }
    public sealed class HeaderAttribute : Attribute { public HeaderAttribute(string text) { } }
    public static class JsonUtility { public static string ToJson(object value, bool pretty) => Newtonsoft.Json.JsonConvert.SerializeObject(value); }
    public enum HideFlags { HideAndDontSave }
    public static class Application { public static string temporaryCachePath; public static string dataPath; public static bool isPlaying; }
    public static class Debug
    {
        public static readonly List<string> Warnings = new List<string>();
        public static void Log(object message) { }
        public static void LogWarning(object message) => Warnings.Add(message.ToString());
        public static void LogError(object message) { }
    }
    public sealed class GameObject : Object
    {
        public static readonly List<GameObject> All = new List<GameObject>();
        public readonly string name;
        public readonly Transform transform;
        public readonly bool Active;
        public HideFlags hideFlags;
        public int AttachmentCount;
        public GameObject(string name, GameObject parent = null, bool active = true)
        {
            this.name = name;
            Active = active;
            transform = new Transform(this, parent == null ? null : parent.transform);
            if (parent != null) parent.transform.Children.Add(transform);
            All.Add(this);
        }
        private bool ActiveInHierarchy => Active && (transform.Parent == null || transform.Parent.gameObject.ActiveInHierarchy);
        private string Path => transform.Parent == null ? name : transform.Parent.gameObject.Path + "/" + name;
        public static GameObject Find(string path)
        {
            if (string.IsNullOrEmpty(path)) return null;
            if (path.StartsWith("/", StringComparison.Ordinal))
                return All.FirstOrDefault(go => go.ActiveInHierarchy && "/" + go.Path == path);
            if (path.Contains("/"))
                return All.FirstOrDefault(go => go.ActiveInHierarchy && (go.Path == path || go.Path.EndsWith("/" + path, StringComparison.Ordinal)));
            return All.FirstOrDefault(go => go.ActiveInHierarchy && go.name == path);
        }
        public T AddComponent<T>() where T : new() => new T();
        public Component GetComponent(Type type) => null;
        public T GetComponent<T>() where T : class => null;
        public Component AddComponent(Type type) { AttachmentCount++; return (Component)Activator.CreateInstance(type); }
        public static void Reset()
        {
            All.Clear(); Debug.Warnings.Clear();
            MCPForUnity.Editor.Tools.RoslynRuntimeCompiler.Instance = new MCPForUnity.Editor.Tools.RoslynRuntimeCompiler();
            MCPForUnity.Editor.Tools.RoslynRuntimeCompiler.Executions = 0;
            MCPForUnity.Editor.Tools.RoslynRuntimeCompiler.LastTarget = null;
        }
    }
    public sealed class Transform
    {
        public readonly GameObject gameObject;
        public readonly Transform Parent;
        public readonly List<Transform> Children = new List<Transform>();
        public Transform(GameObject owner, Transform parent) { gameObject = owner; Parent = parent; }
        public Transform Find(string name) => Children.FirstOrDefault(child => child.gameObject.name == name);
    }
}
namespace UnityEditor { public class Editor { } }
namespace MCPForUnity.Editor.Helpers
{
    public sealed class ErrorResponse
    {
        public readonly string Message;
        public readonly object Data;
        public ErrorResponse(string message, object data = null) { Message = message; Data = data; }
    }
    public sealed class SuccessResponse
    {
        public readonly object Data;
        public SuccessResponse(string message, object data = null) { Data = data; }
    }
}
namespace MCPForUnity.Editor.Tools
{
    [AttributeUsage(AttributeTargets.Class)]
    public sealed class McpForUnityToolAttribute : Attribute
    {
        public string Description { get; set; }
        public McpForUnityToolAttribute(string name) { }
    }
    public sealed class RoslynRuntimeCompiler : MonoBehaviour
    {
        public static RoslynRuntimeCompiler Instance;
        public static int Executions;
        public static GameObject LastTarget;
        public bool enableHistory;
        public string lastCompileDiagnostics;
        public readonly List<HistoryEntry> CompilationHistory = new List<HistoryEntry>();
        public bool CompileAndExecute(string code, string className, string methodName, GameObject target, bool attach, out string error)
        {
            Executions++; LastTarget = target; error = null; return true;
        }
        public bool SaveHistoryToFile(out string path, out string error) { path = null; error = null; return true; }
        public void ClearHistory() => CompilationHistory.Clear();
    }
    public sealed class HistoryEntry
    {
        public string timestamp, typeName, methodName, diagnostics, executionTarget, sourceCode;
        public bool success;
    }
}
