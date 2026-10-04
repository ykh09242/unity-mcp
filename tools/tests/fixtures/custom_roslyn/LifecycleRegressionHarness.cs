#nullable disable
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using UnityEngine;
using Object = UnityEngine.Object;

internal static class LifecycleRegressionHarness
{
    private static int failures;
    private static int checks;
    private static int Main(string[] args)
    {
        Application.temporaryCachePath = args[0];
        Application.dataPath = args[0];
        foreach (var method in new[] { "OnEnable", "OnGUI" })
            Check(method + " adopts exact ordered inactive helper among multiple objects", () =>
            {
                var later = UserHelper(); later.LookupOrder = 30;
                var inactive = UserHelper(false); inactive.LookupOrder = 10;
                var active = UserHelper(); active.LookupOrder = 20;
                var window = new RoslynRuntimeCompilerWindow(); Call(window, method);
                Require(Helper(window) == inactive, "Window did not preserve ordered inactive selection");
                Require(Object.LastFindType == typeof(RoslynRuntimeCompiler) && Object.LastFindInactive,
                    "Window lookup did not forward exact Type and Include flag");
                Call(window, "OnDestroy");
                Require(Object.DestroyedObjects.Count == 0, "Adopted helper was treated as owned");
            });
        Check("static helper preserves ordered active-only compiler selection", () =>
        {
            var later = UserHelper(); later.LookupOrder = 30;
            var inactive = UserHelper(false); inactive.LookupOrder = 10;
            var active = UserHelper(); active.LookupOrder = 20;
            typeof(RoslynMCPHelper).GetField("_compiler", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, null);
            var selected = typeof(RoslynMCPHelper).GetMethod("GetOrCreateCompiler", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
            Require(ReferenceEquals(selected, active), "Static helper changed active-only ordered selection");
            Require(Object.LastFindType == typeof(RoslynRuntimeCompiler) && !Object.LastFindInactive,
                "Static helper did not forward exact Type and Exclude flag");
        });
        foreach (bool active in new[] { true, false })
            Check("adopted " + (active ? "active" : "inactive") + " helper preserves user object and children", () =>
            {
                var helper = UserHelper(active);
                var child = new GameObject("UserChild", helper.gameObject);
                var other = helper.gameObject.AddComponent<UserComponent>();
                var window = new RoslynRuntimeCompilerWindow();
                Call(window, "OnEnable");
                Require(Helper(window) == helper, "Existing helper was not adopted");
                Call(window, "OnDisable"); Call(window, "OnDestroy");
                Require(!helper.gameObject.Destroyed && !child.Destroyed && !other.Destroyed,
                    "Closing window destroyed adopted user GameObject, child or other component");
                Require(Object.DestroyedObjects.Count == 0, "Unexpected destruction call");
            });
        Check("owned hidden helper is cleaned on close", () =>
        {
            var window = new RoslynRuntimeCompilerWindow(); Call(window, "OnEnable");
            var helper = Helper(window);
            Require(helper.gameObject.hideFlags == HideFlags.HideAndDontSave, "Temporary helper is not hidden");
            Call(window, "OnDestroy");
            Require(helper.gameObject.Destroyed, "Owned helper leaked");
            Require(Object.DestroyedObjects.Count == 1, "Owned helper cleanup did not occur exactly once");
            Call(window, "OnDestroy");
            Require(Object.DestroyedObjects.Count == 1, "Second destroy repeated cleanup");
        });
        Check("repeated enable retains exact owned helper identity", () =>
        {
            var window = new RoslynRuntimeCompilerWindow(); Call(window, "OnEnable");
            var helper = Helper(window); var user = UserHelper();
            GameObject.All.Remove(user.gameObject); GameObject.All.Insert(0, user.gameObject);
            Call(window, "OnEnable");
            Require(Helper(window) == helper, "Repeated enable switched away from owned helper");
            Call(window, "OnDestroy");
            Require(helper.gameObject.Destroyed && !user.gameObject.Destroyed, "Cleanup lost ownership identity");
        });
        Check("fallback adoption after owned helper destruction preserves replacement", () =>
        {
            var window = new RoslynRuntimeCompilerWindow(); Call(window, "OnEnable");
            Object.DestroyImmediate(Helper(window).gameObject);
            var replacement = UserHelper(false); replacement.code = "replacement-code";
            Call(window, "OnGUI");
            Require(Helper(window) == replacement, "Fallback did not adopt replacement");
            Require((string)Get(window, "codeText") == "replacement-code", "Fallback did not load replacement state");
            Object.DestroyedObjects.Clear(); Call(window, "OnDestroy");
            Require(!replacement.gameObject.Destroyed && Object.DestroyedObjects.Count == 0, "Ownership transferred to adopted replacement");
        });
        Check("fallback creation is owned and receives window state", () =>
        {
            var window = new RoslynRuntimeCompilerWindow(); Call(window, "OnEnable");
            Object.DestroyImmediate(Helper(window).gameObject); Set(window, "codeText", "new-window-code");
            Call(window, "OnGUI"); var replacement = Helper(window);
            Require(replacement.code == "new-window-code", "Fallback helper lost window state");
            Require(replacement.gameObject.hideFlags == HideFlags.HideAndDontSave, "Fallback helper is not hidden");
            Object.DestroyedObjects.Clear(); Call(window, "OnDestroy");
            Require(replacement.gameObject.Destroyed && Object.DestroyedObjects.Count == 1, "Owned fallback helper leaked");
        });
        Check("adopted helper keeps existing state synchronization", () =>
        {
            var helper = UserHelper(); helper.code = "user-code"; helper.entryTypeName = "UserType";
            helper.entryMethodName = "UserMethod"; helper.attachAsComponent = true;
            helper.targetGameObject = new GameObject("UserTarget");
            var window = new RoslynRuntimeCompilerWindow(); Call(window, "OnEnable");
            Require((string)Get(window, "codeText") == "user-code" && (string)Get(window, "typeName") == "UserType",
                "OnEnable changed helper state loading");
            Set(window, "codeText", "edited-code"); Call(window, "OnDisable");
            Require(helper.code == "edited-code" && helper.entryMethodName == "UserMethod" && helper.attachAsComponent,
                "OnDisable changed helper state saving");
            Call(window, "ApplyToHelper"); Require(helper.targetGameObject.name == "UserTarget", "ApplyToHelper changed target routing");
        });
        Check("destroyed adopted helper is replaced by owned temporary helper", () =>
        {
            var helper = UserHelper(); var window = new RoslynRuntimeCompilerWindow(); Call(window, "OnEnable");
            Object.DestroyImmediate(helper.gameObject); Call(window, "OnGUI"); var replacement = Helper(window);
            Object.DestroyedObjects.Clear(); Call(window, "OnDestroy");
            Require(replacement.gameObject.Destroyed && Object.DestroyedObjects.Count == 1, "Owned replacement leaked");
        });
        Check("missing helper on close is harmless", () => Call(new RoslynRuntimeCompilerWindow(), "OnDestroy"));
        Check("serialized ownership restoration model keeps exact owned helper", () =>
        {
            var original = new RoslynRuntimeCompilerWindow(); Call(original, "OnEnable"); var owned = Helper(original);
            var restored = new RoslynRuntimeCompilerWindow();
            foreach (var field in typeof(RoslynRuntimeCompilerWindow).GetFields(BindingFlags.Instance | BindingFlags.NonPublic)
                .Where(f => f.IsDefined(typeof(SerializeField), false))) field.SetValue(restored, field.GetValue(original));
            var user = UserHelper(); GameObject.All.Remove(user.gameObject); GameObject.All.Insert(0, user.gameObject);
            Call(restored, "OnEnable");
            Require(Helper(restored) == owned, "Restored references lost owned identity");
            Call(restored, "OnDestroy");
            Require(owned.gameObject.Destroyed && !user.gameObject.Destroyed, "Restored ownership cleanup targeted wrong object");
        });
        Check("coroutine to MonoBehaviour recompile never invokes prior program", () =>
        {
            var compiler = UserHelper();
            Require(Execute(compiler, CoroutineSource, "CoroutineProgram", compiler.gameObject, false), "Initial coroutine compile failed");
            RuntimeProbe.Calls = 0; compiler.StartedCoroutines.Clear();
            Require(Execute(compiler, ComponentSource, "GeneratedComponent", compiler.gameObject, false), "Component recompile failed");
            Require(RuntimeProbe.Calls == 0 && compiler.StartedCoroutines.Count == 0, "Prior coroutine executed after component recompile");
            Require(!compiler.HasEntryMethod && compiler.EntryType.Name == "GeneratedComponent", "Method cache belongs to previous program");
            Require(compiler.gameObject.Components.Count(c => !c.Destroyed && c.GetType().Name == "GeneratedComponent") == 1, "Current MonoBehaviour was not attached");
        });
        foreach (var invalid in new[] { "syntax", "missing-type", "missing-method", "ambiguous-method" })
            Check("failed " + invalid + " recompile clears runnable caches", () =>
            {
                var compiler = UserHelper(); Require(Execute(compiler, StaticSource, "StaticProgram", compiler.gameObject, false), "Seed compilation failed");
                RuntimeProbe.Calls = 0;
                compiler.code = invalid == "syntax" ? "public class Broken {" : invalid == "missing-type" ? "public class Other {}"
                    : invalid == "missing-method" ? "public class Broken {}" : "public class Broken { public static void Run() {} public static void Run(int x) {} }";
                compiler.entryTypeName = "Broken"; compiler.entryMethodName = "Run";
                Require(!compiler.CompileInMemory(out string diagnostics) && !string.IsNullOrEmpty(diagnostics), "Expected failed compile or entry resolution");
                Require(!compiler.InvokeEntry(compiler.gameObject, out string error) && RuntimeProbe.Calls == 0,
                    "Failed recompile left previous code callable; calls=" + RuntimeProbe.Calls);
                Require(!compiler.HasCompiledAssembly && !compiler.HasEntryType && !compiler.HasEntryMethod, "Failed recompile left published caches");
            });
        Check("same class recompile replaces prior tracked component", () =>
        {
            var compiler = UserHelper(); var target = new GameObject("AttachmentTarget");
            Require(Execute(compiler, ComponentSource, "GeneratedComponent", target, true), "First attachment failed");
            var first = target.Components.Single();
            Require(Execute(compiler, ComponentSource, "GeneratedComponent", target, true), "Second attachment failed");
            Require(first.Destroyed && target.Components.Count(c => !c.Destroyed) == 1,
                "Fresh assembly Type retained old component; live count=" + target.Components.Count(c => !c.Destroyed));
        });
        Check("same Type attachment preserves remove before add ordering", () =>
        {
            var compiler = UserHelper(); var target = new GameObject("SameTypeTarget");
            compiler.code = ComponentSource; compiler.entryTypeName = "GeneratedComponent";
            Require(compiler.CompileInMemory(out string diagnostics) && compiler.AttachMonoBehaviour(target, out string error), "Seed attachment failed");
            var first = target.Components.Single(); target.FailAdd = "same-type-present";
            Require(compiler.AttachMonoBehaviour(target, out error) && first.Destroyed && target.Components.Count(c => !c.Destroyed) == 1,
                "Same Type attempted adding while previous instance was still present");
        });
        Check("replacement preserves untracked older Type with same full name", () =>
        {
            var compiler = UserHelper(); var target = new GameObject("ExternalAttachmentTarget");
            compiler.code = ComponentSource; compiler.entryTypeName = "GeneratedComponent";
            Require(compiler.CompileInMemory(out string diagnostics), "External Type compilation failed");
            var external = target.AddComponent(compiler.EntryType);
            Require(Execute(compiler, ComponentSource, "GeneratedComponent", target, true), "Tracked attachment failed");
            Require(!external.Destroyed && target.Components.Count(c => !c.Destroyed) == 2, "Replacement removed an untracked same-name component");
        });
        Check("component replacement preserves other targets and classes", () =>
        {
            var compiler = UserHelper(); var firstTarget = new GameObject("FirstTarget"); var secondTarget = new GameObject("SecondTarget");
            Require(Execute(compiler, ComponentSource, "GeneratedComponent", firstTarget, true), "First target attachment failed");
            var first = firstTarget.Components.Single();
            Require(Execute(compiler, ComponentSource, "GeneratedComponent", secondTarget, true), "Second target attachment failed");
            var second = secondTarget.Components.Single();
            Require(Execute(compiler, "public class OtherComponent : UnityEngine.MonoBehaviour {}", "OtherComponent", secondTarget, true), "Other class attachment failed");
            Require(!first.Destroyed && !second.Destroyed && secondTarget.Components.Count(c => !c.Destroyed) == 2,
                "Replacement removed a different target or class");
        });
        foreach (bool switchTarget in new[] { true, false })
            Check("A B A component recompiles replace A across " + (switchTarget ? "targets" : "classes"), () =>
            {
                var compiler = UserHelper(); var targetA = new GameObject("TargetA");
                var targetB = switchTarget ? new GameObject("TargetB") : targetA;
                Require(Execute(compiler, ComponentSource, "GeneratedComponent", targetA, true), "Initial A attachment failed");
                var firstA = targetA.Components.Single();
                string sourceB = switchTarget ? ComponentSource : "public class OtherComponent : UnityEngine.MonoBehaviour {}";
                string typeB = switchTarget ? "GeneratedComponent" : "OtherComponent";
                Require(Execute(compiler, sourceB, typeB, targetB, true), "B attachment failed");
                var componentB = targetB.Components.Last();
                Require(Execute(compiler, ComponentSource, "GeneratedComponent", targetA, true), "Final A attachment failed");
                Require(firstA.Destroyed && !componentB.Destroyed && targetA.Components.Count(c => !c.Destroyed && c.GetType().Name == "GeneratedComponent") == 1,
                    "Returning to A leaked its prior component or destroyed B");
            });
        foreach (bool throwOnAdd in new[] { false, true })
            Check("failed replacement add " + (throwOnAdd ? "exception" : "null") + " preserves valid old component", () =>
            {
                var compiler = UserHelper(); var target = new GameObject("AttachmentTarget");
                Require(Execute(compiler, ComponentSource, "GeneratedComponent", target, true), "Seed attachment failed");
                var previous = target.Components.Single();
                target.FailAdd = throwOnAdd ? "throw" : "null";
                Require(!Execute(compiler, ComponentSource, "GeneratedComponent", target, true), "Failed AddComponent reported success");
                Require(!previous.Destroyed, "Failed addition removed prior valid component");
                target.FailAdd = null;
                Require(Execute(compiler, ComponentSource, "GeneratedComponent", target, true) && previous.Destroyed
                    && target.Components.Count(c => !c.Destroyed) == 1, "Failed addition forgot prior valid component ownership");
            });
        foreach (bool withHost in new[] { false, true })
            Check("GameObject coroutine keeps explicit target with " + (withHost ? "target host" : "compiler host fallback"), () =>
            {
                var compiler = UserHelper(); var target = new GameObject("CoroutineTarget");
                var targetHost = withHost ? target.AddComponent<LifecycleRegressionHarness.UserComponent>() : null;
                Require(Execute(compiler, CoroutineSource, "CoroutineProgram", target, false), "Coroutine compilation failed");
                Require(RuntimeProbe.Target == target, "Coroutine received " + RuntimeProbe.Target?.name + " instead of explicit target");
                Require((withHost ? targetHost.StartedCoroutines.Count : compiler.StartedCoroutines.Count) == 1, "Coroutine started on wrong host");
            });
        foreach (bool success in new[] { true, false })
            Check("window Compile and Run records " + (success ? "success" : "failure") + " history", () =>
            {
                var window = new RoslynRuntimeCompilerWindow(); Call(window, "OnEnable"); var compiler = Helper(window);
                compiler.enableHistory = true; Set(window, "codeText", success ? StaticSource : "public class Broken {");
                Set(window, "typeName", "StaticProgram"); GUI.NextButton = "Compile & Run on helper";
                Call(window, "OnGUI");
                Require(compiler.CompilationHistory.Count == 1, "GUI compile/run wrote no history");
                var entry = compiler.CompilationHistory.Single();
                Require(entry.success == success && entry.executionTarget == compiler.gameObject.name && entry.typeName == "StaticProgram", "History reported wrong result/target");
                Call(window, "OnDestroy");
            });
        foreach (bool parameterless in new[] { true, false })
            Check((parameterless ? "parameterless" : "MonoBehaviour") + " coroutine signature preserves host", () =>
            {
                var compiler = UserHelper(); var target = new GameObject("CoroutineTarget"); var host = target.AddComponent<UserComponent>();
                string source = "public class SignatureProgram { public static System.Collections.IEnumerator Run("
                    + (parameterless ? "" : "UnityEngine.MonoBehaviour host") + ") { RuntimeProbe.Calls++; "
                    + (parameterless ? "" : "RuntimeProbe.Target = host.gameObject; ") + "return new object[0].GetEnumerator(); } }";
                Require(Execute(compiler, source, "SignatureProgram", target, false), "Coroutine signature failed");
                Require(RuntimeProbe.Calls == 1 && host.StartedCoroutines.Count == 1, "Coroutine signature used wrong execution host");
                if (!parameterless) Require(RuntimeProbe.Target == target, "MonoBehaviour argument used wrong host");
            });
        Check("public coroutine overload preserves host GameObject", () =>
        {
            var compiler = UserHelper(); compiler.code = CoroutineSource; compiler.entryTypeName = "CoroutineProgram";
            Require(compiler.CompileInMemory(out string diagnostics), "Coroutine compilation failed");
            Require(compiler.InvokeCoroutine(compiler, out string error) && RuntimeProbe.Target == compiler.gameObject, "Public overload changed host argument");
        });
        Check("Compile and Run still respects disabled history", () =>
        {
            var compiler = UserHelper(); compiler.enableHistory = false; compiler.code = StaticSource; compiler.entryTypeName = "StaticProgram";
            compiler.CompileAndRunOnSelf(); Require(compiler.CompilationHistory.Count == 0 && RuntimeProbe.Calls == 1, "Disabled history or execution changed");
        });
        foreach (var invalid in new[] { "empty-code", "whitespace-code", "empty-type", "whitespace-type" })
            Check("invalid GUI " + invalid + " compile disables prior Run", () =>
            {
                var window = new RoslynRuntimeCompilerWindow(); Call(window, "OnEnable"); var compiler = Helper(window);
                compiler.code = StaticSource; compiler.entryTypeName = "StaticProgram";
                Require(compiler.CompileInMemory(out string diagnostics), "Seed compilation failed");
                Set(window, "codeText", invalid == "empty-code" ? "" : invalid == "whitespace-code" ? " \t\r\n" : StaticSource);
                Set(window, "typeName", invalid == "empty-type" ? "" : invalid == "whitespace-type" ? " \t" : "StaticProgram");
                GUI.NextButton = "Compile & Run on helper"; Call(window, "OnGUI");
                GUI.NextButton = "Run (invoke on selected)"; Call(window, "OnGUI");
                Require(RuntimeProbe.Calls == 0 && !compiler.HasCompiledAssembly && !compiler.HasEntryType && !compiler.HasEntryMethod,
                    "Invalid GUI attempt retained prior runnable code; calls=" + RuntimeProbe.Calls);
                Require(compiler.CompilationHistory.Count == 0, "Input validation changed existing history semantics");
                Call(window, "OnDestroy");
            });
        Check("simplified null source returns existing validation error", () =>
        {
            var compiler = UserHelper();
            Require(!compiler.CompileAndExecute(null, "StaticProgram", compiler.gameObject, out string error)
                && error == "Source code cannot be empty.", "Null source did not return the existing validation error");
        });
        Console.WriteLine("RESULT: " + (checks - failures) + "/" + checks + " passed; failures=" + failures);
        return failures == 0 ? 0 : 1;
    }

    private static RoslynRuntimeCompiler UserHelper(bool active = true)
    {
        var go = new GameObject("UserHelper", null, active); return go.AddComponent<RoslynRuntimeCompiler>();
    }
    private const string StaticSource = "public class StaticProgram { public static void Run() { RuntimeProbe.Calls++; } }";
    private const string ComponentSource = "public class GeneratedComponent : UnityEngine.MonoBehaviour {}";
    private const string CoroutineSource = "public class CoroutineProgram { public static System.Collections.IEnumerator Run(UnityEngine.GameObject target) { RuntimeProbe.Calls++; RuntimeProbe.Target = target; return new object[0].GetEnumerator(); } }";
    private static bool Execute(RoslynRuntimeCompiler compiler, string source, string type, GameObject target, bool attach)
    {
        bool result = compiler.CompileAndExecute(source, type, "Run", target, attach, out string error);
        if (!result) Console.WriteLine("OBSERVED_ERROR: " + error); return result;
    }
    private static void Check(string name, Action test)
    {
        checks++; GameObject.All.Clear(); Object.DestroyedObjects.Clear(); GUI.ButtonCalls = 0; GUI.NextButton = null;
        RuntimeProbe.Calls = 0; RuntimeProbe.Target = null;
        new RoslynRuntimeCompiler().ClearHistory();
        try { test(); Console.WriteLine("PASS: " + name); }
        catch (Exception ex) { failures++; Console.WriteLine("FAIL: " + name + " -- " + ex.Message); }
    }
    private static FieldInfo Field(object value, string name) => value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);
    private static object Get(object value, string name) => Field(value, name).GetValue(value);
    private static void Set(object value, string name, object data) => Field(value, name).SetValue(value, data);
    private static RoslynRuntimeCompiler Helper(object window) => (RoslynRuntimeCompiler)Get(window, "helperInScene");
    private static void Call(object value, string name)
    {
        try { value.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic).Invoke(value, null); }
        catch (TargetInvocationException ex) { throw ex.InnerException; }
    }
    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
    public sealed class UserComponent : MonoBehaviour { }
}

public static class RuntimeProbe { public static int Calls; public static GameObject Target; }

// Native Unity scene/GUI operations are seams; the whole production source is compiled verbatim.
namespace UnityEngine
{
    public class Object
    {
        public bool Destroyed;
        public long LookupOrder;
        public static Type LastFindType;
        public static bool LastFindInactive;
        public static readonly List<Object> DestroyedObjects = new List<Object>();
        public static bool operator ==(Object left, Object right)
        {
            bool ln = ReferenceEquals(left, null) || left.Destroyed;
            bool rn = ReferenceEquals(right, null) || right.Destroyed;
            return ln || rn ? ln == rn : ReferenceEquals(left, right);
        }
        public static bool operator !=(Object left, Object right) => !(left == right);
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() => base.GetHashCode();
#if UNITY_2022_3_OR_NEWER
#if UNITY_6000_5_OR_NEWER
        [Obsolete("Ordered first-object lookup is deprecated in Unity 6.5")]
#endif
        public static T FindFirstObjectByType<T>(FindObjectsInactive inactive = FindObjectsInactive.Exclude) where T : Object =>
            GameObject.All.Where(go => !go.Destroyed && (go.Active || inactive == FindObjectsInactive.Include))
                .SelectMany(go => go.Components).OfType<T>().FirstOrDefault(value => !value.Destroyed);
#endif
#if UNITY_2022_3_OR_NEWER
#if UNITY_6000_5_OR_NEWER
        [Obsolete("Ordered first-object lookup is deprecated in Unity 6.5", true)]
#endif
        public static Object FindFirstObjectByType(Type type, FindObjectsInactive inactive) => SelectFirst(type, inactive);
#endif
        private static Object SelectFirst(Type type, FindObjectsInactive inactive)
        {
            LastFindType = type; LastFindInactive = inactive == FindObjectsInactive.Include;
            return FindObjectsByType(type, inactive).OrderBy(value => value.LookupOrder).FirstOrDefault();
        }
        public static Object FindObjectOfType(Type type) => SelectFirst(type, FindObjectsInactive.Exclude);
        public static Object FindObjectOfType(Type type, bool inactive) => SelectFirst(type, inactive ? FindObjectsInactive.Include : FindObjectsInactive.Exclude);
        public static Object FindAnyObjectByType(Type type) => SelectFirst(type, FindObjectsInactive.Exclude);
        public static Object[] FindObjectsOfType(Type type) => FindObjectsByType(type, FindObjectsInactive.Exclude);
        public static Object[] FindObjectsOfType(Type type, bool inactive) => FindObjectsByType(type, inactive ? FindObjectsInactive.Include : FindObjectsInactive.Exclude);
        public static T[] FindObjectsByType<T>() where T : Object => FindObjectsByType(typeof(T), FindObjectsInactive.Exclude).Cast<T>().ToArray();
        public static T[] FindObjectsByType<T>(FindObjectsSortMode mode) where T : Object => FindObjectsByType<T>();
        public static Object[] FindObjectsByType(Type type, FindObjectsSortMode mode) => FindObjectsByType(type, FindObjectsInactive.Exclude);
        public static Object[] FindObjectsByType(Type type, FindObjectsInactive inactive, FindObjectsSortMode mode) => FindObjectsByType(type, inactive);
        public static Object[] FindObjectsByType(Type type, FindObjectsInactive inactive) => GameObject.All
            .Where(go => !go.Destroyed && (go.Active || inactive == FindObjectsInactive.Include))
            .SelectMany(go => go.Components).Where(value => !value.Destroyed && type.IsInstanceOfType(value)).ToArray();
        public static void Destroy(Object value) => DestroyImmediate(value);
        public static void DestroyImmediate(Object value)
        {
            if (value == null) return;
            DestroyedObjects.Add(value); value.Destroyed = true;
            if (value is GameObject go)
            {
                foreach (var component in go.Components) component.Destroyed = true;
                foreach (var child in go.transform.Children) MarkDestroyed(child.gameObject);
            }
        }
        private static void MarkDestroyed(GameObject go)
        {
            go.Destroyed = true; foreach (var component in go.Components) component.Destroyed = true;
            foreach (var child in go.transform.Children) MarkDestroyed(child.gameObject);
        }
    }
    public class Component : Object { public GameObject gameObject; public string name => gameObject.name; }
    public class MonoBehaviour : Component
    {
        public readonly List<System.Collections.IEnumerator> StartedCoroutines = new List<System.Collections.IEnumerator>();
        public void StartCoroutine(System.Collections.IEnumerator value) { StartedCoroutines.Add(value); }
    }
    public sealed class GameObject : Object
    {
        public static readonly List<GameObject> All = new List<GameObject>();
        public readonly string name;
        public readonly bool Active;
        public readonly Transform transform;
        public readonly List<Component> Components = new List<Component>();
        public string FailAdd;
        public HideFlags hideFlags;
        public GameObject(string name, GameObject parent = null, bool active = true)
        {
            this.name = name; Active = active; transform = new Transform { gameObject = this };
            if (parent != null) parent.transform.Children.Add(transform); All.Add(this);
        }
        public static GameObject Find(string name) => All.FirstOrDefault(go => !go.Destroyed && go.Active && go.name == name);
        public T AddComponent<T>() where T : Component, new() => (T)AddComponent(typeof(T));
        public Component AddComponent(Type type)
        {
            if (FailAdd == "null") return null;
            if (FailAdd == "throw") throw new InvalidOperationException("Injected AddComponent failure");
            if (FailAdd == "same-type-present" && GetComponent(type) != null)
                throw new InvalidOperationException("Same Type still present during AddComponent");
            var component = (Component)Activator.CreateInstance(type); component.gameObject = this; Components.Add(component); return component;
        }
        public Component GetComponent(Type type) => Components.FirstOrDefault(c => !c.Destroyed && type.IsInstanceOfType(c));
        public T GetComponent<T>() where T : Component => (T)GetComponent(typeof(T));
    }
    public sealed class Transform : Component { public readonly List<Transform> Children = new List<Transform>(); }
    public enum FindObjectsInactive { Exclude, Include }
    public enum FindObjectsSortMode { None }
    public enum HideFlags { None, HideAndDontSave }
    public sealed class SerializeField : Attribute { }
    public sealed class TextAreaAttribute : Attribute { public TextAreaAttribute(int min, int max) { } }
    public sealed class TooltipAttribute : Attribute { public TooltipAttribute(string text) { } }
    public sealed class HeaderAttribute : Attribute { public HeaderAttribute(string text) { } }
    public static class Application { public static string temporaryCachePath; public static string dataPath; public static bool isPlaying; }
    public static class Debug { public static void Log(object value) { } public static void LogWarning(object value) { } public static void LogError(object value) { } }
    public static class JsonUtility { public static string ToJson(object value, bool pretty) => "{}"; }
    public struct Vector2 { public Vector2(float x, float y) { } }
    public struct Rect { public float width; public float height; }
    public struct Color { public static Color green; public static Color red; }
    public enum TextAnchor { MiddleLeft }
    public class Texture2D { public static Texture2D grayTexture; }
    public class GUIStyleState { public Color textColor; public Texture2D background; }
    public class GUIStyle { public GUIStyle() { } public GUIStyle(GUIStyle other) { } public TextAnchor alignment; public GUIStyleState normal = new GUIStyleState(); }
    public sealed class GUISkin { public GUIStyle button = new GUIStyle(); }
    public static class GUI { public static bool enabled = true; public static int ButtonCalls; public static string NextButton; public static GUISkin skin = new GUISkin(); }
    public class GUILayoutOption { }
    public static class GUILayout
    {
        public static GUILayoutOption Width(float value) => null;
        public static GUILayoutOption Height(float value) => null;
        public static GUILayoutOption ExpandHeight(bool value) => null;
        public static bool Button(string text, params GUILayoutOption[] options)
        {
            GUI.ButtonCalls++; if (GUI.enabled && GUI.NextButton == text) { GUI.NextButton = null; return true; } return false;
        }
        public static bool Button(string text, GUIStyle style, params GUILayoutOption[] options) { GUI.ButtonCalls++; return false; }
        public static int Toolbar(int value, string[] labels) => value;
    }
}
namespace UnityEditor
{
    public class EditorWindow : UnityEngine.Object
    {
        public Vector2 minSize; public Rect position = new Rect { width = 600, height = 400 };
        public static T GetWindow<T>(string title) where T : new() => new T();
    }
    public sealed class MenuItem : Attribute { public MenuItem(string menu) { } }
    public enum MessageType { None, Info, Error }
    public static class Selection { public static GameObject activeGameObject; }
    public static class EditorStyles { public static GUIStyle boldLabel = new GUIStyle(); }
    public static class EditorUtility
    {
        public static bool DisplayDialog(string title, string message, string ok, string cancel = null) => false;
        public static void RevealInFinder(string path) { }
    }
    public static class EditorGUILayout
    {
        public static void LabelField(string text, params GUILayoutOption[] options) { }
        public static void LabelField(string text, GUIStyle style, params GUILayoutOption[] options) { }
        public static void LabelField(string text, string value) { }
        public static void Space() { }
        public static void BeginHorizontal() { } public static void EndHorizontal() { }
        public static void BeginVertical(params GUILayoutOption[] options) { } public static void EndVertical() { }
        public static string TextField(string text, params GUILayoutOption[] options) => text;
        public static string TextArea(string text, params GUILayoutOption[] options) => text;
        public static bool Toggle(string label, bool value, params GUILayoutOption[] options) => value;
        public static UnityEngine.Object ObjectField(UnityEngine.Object value, Type type, bool allowSceneObjects) => value;
        public static Vector2 BeginScrollView(Vector2 value, params GUILayoutOption[] options) => value;
        public static void EndScrollView() { } public static void HelpBox(string text, MessageType type) { }
    }
}
