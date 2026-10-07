using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;

// These stand-ins model hierarchy and stage boundaries; they do not execute Unity native APIs.
namespace UnityEngine
{
    public class Object { }
}

public sealed class GameObject : UnityEngine.Object
{
    public static readonly Dictionary<int, GameObject> Objects = new Dictionary<int, GameObject>();
    public readonly Transform transform;
    private readonly int id;
    public string name;
    public bool activeSelf = true;
    public bool activeInHierarchy => activeSelf && (transform.parent == null || transform.parent.gameObject.activeInHierarchy);

    public GameObject(string name, int id, GameObject parent = null)
    {
        this.name = name;
        this.id = id;
        transform = new Transform(this, parent?.transform);
        parent?.transform.children.Add(transform);
        Objects[id] = this;
    }

    public T[] GetComponentsInChildren<T>(bool includeInactive)
        where T : class => transform.Descendants().Where(t => includeInactive || t.gameObject.activeInHierarchy).Cast<T>().ToArray();

    public T[] GetComponents<T>() => new T[0];

    public int GetInstanceIDCompat() => id;
}

public class Component { }

public sealed class Transform
{
    public readonly GameObject gameObject;
    public readonly Transform parent;
    public readonly List<Transform> children = new List<Transform>();
    public string name => gameObject.name;
    public int childCount => children.Count;

    public Transform(GameObject go, Transform parent)
    {
        gameObject = go;
        this.parent = parent;
    }

    public IEnumerable<Transform> Descendants()
    {
        yield return this;
        foreach (var child in children)
        foreach (var t in child.Descendants())
            yield return t;
    }

    public bool IsChildOf(Transform ancestor) => this == ancestor || (parent != null && parent.IsChildOf(ancestor));
}

public sealed class Scene
{
    public GameObject[] roots = new GameObject[0];

    public GameObject[] GetRootGameObjects() => roots;
}

public static class SceneManager
{
    public static readonly Scene Active = new Scene();

    public static Scene GetActiveScene() => Active;
}

public sealed class PrefabStage
{
    public GameObject prefabContentsRoot;
}

public static class PrefabStageUtility
{
    public static PrefabStage Current;

    public static PrefabStage GetCurrentPrefabStage() => Current;
}

public static class GameObjectLookup
{
    public static GameObject FindById(int id) => GameObject.Objects.TryGetValue(id, out var go) ? go : null;

    public static string GetGameObjectPath(GameObject go) =>
        go.transform.parent == null ? go.name : GetGameObjectPath(go.transform.parent.gameObject) + "/" + go.name;
}

// Asset persistence is a controlled boundary. Response assembly and target lookup
// above it are the actual ManagePrefabs production methods, not replicas.
public static class AssetDatabase
{
    public static GameObject Saved;

    public static T LoadAssetAtPath<T>(string path)
        where T : class => Saved as T;

    public static string GenerateUniqueAssetPath(string path) => path;
}

public static class Selection
{
    public static GameObject activeGameObject;
}

public enum PrefabUnpackMode
{
    Completely,
}

public enum InteractionMode
{
    AutomatedAction,
}

public static class PrefabUtility
{
    public static GameObject GetOutermostPrefabInstanceRoot(GameObject go) => go;

    public static void UnpackPrefabInstance(GameObject go, PrefabUnpackMode mode, InteractionMode action) { }
}

public static class McpLog
{
    public static void Info(string message) { }

    public static void Error(string message) { }
}

public sealed class ErrorResponse
{
    public bool success = false;
    public string error;

    public ErrorResponse(string error)
    {
        this.error = error;
    }
}

public sealed class SuccessResponse
{
    public bool success = true;
    public object data;

    public SuccessResponse(string message, object data)
    {
        this.data = data;
    }
}

public static partial class ProductionResolver
{
    private static (
        bool isValid,
        string errorMessage,
        string targetName,
        string finalPath,
        bool includeInactive,
        bool replaceExisting,
        bool unlinkIfInstance
    ) ValidateCreatePrefabParams(JObject p) => (true, null, p["target"].ToString(), p["prefabPath"].ToString(), false, false, false);

    private static (bool isValid, string errorMessage, bool shouldUnlink) ValidateSourceObjectForPrefab(GameObject source, bool unlink) => (true, null, false);

    private static void EnsureAssetDirectoryExists(string path) { }

    private static (int count, string error) PersistRuntimeMaterials(GameObject source, string path) => (0, null);

    private static bool CreatePrefabAsset(GameObject source, string path, bool replace)
    {
        AssetDatabase.Saved = new GameObject("ActualSavedRoot", 123);
        return true;
    }
}

public static class PrefabTargetRegression
{
    private static int failures;

    private static GameObject Resolve(string target, bool includeInactive = false, bool expectAmbiguous = false)
    {
        var method = typeof(ProductionResolver).GetMethod("FindSceneObjectByName", BindingFlags.NonPublic | BindingFlags.Static);
        var args = method.GetParameters().Length == 2 ? new object[] { target, includeInactive } : new object[] { target, includeInactive, null };
        var result = (GameObject)method.Invoke(null, args);
        if (expectAmbiguous && (args.Length < 3 || args[2] == null || !args[2].ToString().Contains("ambiguous")))
            throw new Exception("Expected actionable ambiguity error");
        return result;
    }

    private static void Check(string name, Action check)
    {
        try
        {
            check();
            Console.WriteLine("PASS: " + name);
        }
        catch (Exception e)
        {
            failures++;
            Console.WriteLine("FAIL: " + name + " — " + e.GetBaseException().Message);
        }
    }

    private static void Equal(GameObject expected, GameObject actual)
    {
        if (expected != actual)
            throw new Exception("Wrong object resolved");
    }

    public static int Main()
    {
        var root = new GameObject("Parent", 1);
        var cube = new GameObject("McpProbeCube", -19340, root);
        SceneManager.Active.roots = new[] { root };
        Check("negative instance ID", () => Equal(cube, Resolve("-19340")));
        Check("unique name", () => Equal(cube, Resolve("McpProbeCube")));
        Check("full hierarchy path", () => Equal(cube, Resolve("Parent/McpProbeCube")));
        Check("absolute hierarchy path", () => Equal(cube, Resolve("/Parent/McpProbeCube")));
        Check(
            "missing ID does not select numeric name",
            () =>
            {
                var numeric = new GameObject("-123", 2, root);
                Equal(null, Resolve("-123"));
            }
        );
        Check(
            "overflow ID does not select numeric name",
            () =>
            {
                var numeric = new GameObject("2147483648", 6, root);
                Equal(null, Resolve("2147483648"));
            }
        );
        var elsewhere = new GameObject("OtherScene", -42);
        Check("ID outside active scene/stage", () => Equal(null, Resolve("-42")));
        cube.activeSelf = false;
        Check("inactive ID excluded", () => Equal(null, Resolve("-19340")));
        Check("inactive ID explicitly included", () => Equal(cube, Resolve("-19340", true)));
        cube.activeSelf = true;
        root.activeSelf = false;
        Check("inactive ancestor excluded", () => Equal(null, Resolve("Parent")));
        root.activeSelf = true;
        var duplicate = new GameObject("McpProbeCube", 3, root);
        Check("duplicate name rejected", () => Equal(null, Resolve("McpProbeCube", false, true)));
        Check("duplicate hierarchy path rejected", () => Equal(null, Resolve("Parent/McpProbeCube", false, true)));
        Check("ID disambiguates duplicate name", () => Equal(cube, Resolve("-19340")));
        var stageRoot = new GameObject("StageRoot", 4);
        var stageChild = new GameObject("McpProbeCube", 5, stageRoot);
        PrefabStageUtility.Current = new PrefabStage { prefabContentsRoot = stageRoot };
        Check("prefab stage name precedence", () => Equal(stageChild, Resolve("McpProbeCube")));
        Check("prefab stage ID", () => Equal(stageChild, Resolve("5")));
        Check("prefab stage path", () => Equal(stageChild, Resolve("StageRoot/McpProbeCube")));
        var metadataSource = new GameObject("MetadataSource", -9000, root);
        Check(
            "create reports actual saved root identity without renaming source",
            () =>
            {
                var create = typeof(ProductionResolver).GetMethod("CreatePrefabFromGameObject", BindingFlags.NonPublic | BindingFlags.Static);
                var result = JObject.FromObject(
                    create.Invoke(
                        null,
                        new object[]
                        {
                            new JObject { ["target"] = "MetadataSource", ["prefabPath"] = "Assets/ProbeCube.prefab" },
                        }
                    )
                );
                if (!result.Value<bool>("success"))
                    throw new Exception(result.ToString());
                var data = result["data"];
                if (data.Value<string>("rootObjectName") != "ActualSavedRoot" || data.Value<string>("rootObjectPath") != "ActualSavedRoot")
                    throw new Exception("Saved root identity was omitted or guessed from source/file name");
                if (
                    data.Value<string>("instanceName") != "MetadataSource"
                    || metadataSource.name != "MetadataSource"
                    || metadataSource.transform.parent != root.transform
                )
                    throw new Exception("Source instance name or hierarchy changed");
            }
        );
        Console.WriteLine("RESULT: " + (17 - failures) + "/17 passed");
        return failures == 0 ? 0 : 1;
    }
}
