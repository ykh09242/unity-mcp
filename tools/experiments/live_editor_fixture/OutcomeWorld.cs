using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

// The lane host loads this separate observer from outside the project.
public static class LiveWorld
{
    private const string Folder = "Assets/Harbor";
    private const string ScenePath = Folder + "/Harbor.unity";
    private static string task;
    private static int seed;
    private static string configuration;
    private static Component target;

    public static JObject Dispatch(JObject request)
    {
        string operation = (string)request["op"];
        string requested = (string)request["task"];
        int requestedSeed = (int)request["seed"];
        if (operation == "prepare")
        {
            string current = Configuration();
            if (configuration != null && current != configuration)
                throw new InvalidOperationException("Lane configuration changed.");
            task = requested;
            seed = requestedSeed;
            Prepare();
            configuration = Configuration();
        }
        else if (operation != "snapshot" || requested != task || requestedSeed != seed)
            throw new ArgumentException("No matching prepared case.");
        return Snapshot();
    }

    private static void Prepare()
    {
        Selection.activeObject = null;
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        if (AssetDatabase.IsValidFolder(Folder) && !AssetDatabase.DeleteAsset(Folder))
            throw new IOException("Could not reset the disposable scene assets.");
        AssetDatabase.CreateFolder("Assets", "Harbor");
        Undo.ClearAll();
        int variant = seed % 5;
        var guard = new GameObject("Old Quay").AddComponent<Light>();
        guard.intensity = 1.125f;
        guard.range = 6.75f;
        new GameObject("Signal Chime").AddComponent<AudioSource>().volume = .1875f;
        switch (task)
        {
            case "beacon_light":
            case "lower_lamp":
                var light = new GameObject("Harbor Lamp").AddComponent<Light>();
                light.type = LightType.Point;
                light.range = 12.25f + variant;
                light.intensity = 4.125f + variant;
                target = light;
                break;
            case "hatch_surface":
                var hatch = GameObject.CreatePrimitive(PrimitiveType.Cube);
                hatch.name = "Dock Hatch";
                var renderer = hatch.GetComponent<MeshRenderer>();
                var mat = new Material(Shader.Find("Unlit/Color")) { name = "Harbor Slate " + (57 + variant) };
                AssetDatabase.CreateAsset(mat, Folder + "/" + mat.name + ".mat");
                renderer.sharedMaterial = mat;
                target = renderer;
                break;
            case "bell_audio":
            case "lower_bell":
                var source = new GameObject("Wharf Bell").AddComponent<AudioSource>();
                source.volume = .5625f + variant * .0625f;
                source.pitch = 1.125f + variant * .125f;
                target = source;
                break;
            case "mist_particles":
                var ps = new GameObject("Pier Mist").AddComponent<ParticleSystem>();
                ps.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
                var main = ps.main;
                main.playOnAwake = false;
                main.maxParticles = 417 + variant;
                var emission = ps.emission;
                emission.rateOverTime = 21.25f + variant;
                target = ps;
                break;
            case "hoist_animation":
                var animator = new GameObject("Cargo Hoist").AddComponent<Animator>();
                animator.runtimeAnimatorController = AnimatorController.CreateAnimatorControllerAtPath(
                    Folder + "/Blue Cable " + (41 + variant) + ".controller"
                );
                target = animator;
                break;
            case "float_body":
                var body = new GameObject("Channel Float").AddComponent<Rigidbody>();
                body.mass = 5.375f + variant;
                body.angularDamping = .1875f + variant * .0625f;
                target = body;
                break;
            default:
                throw new ArgumentException("Unknown outcome case: " + task);
        }
        if (!EditorSceneManager.SaveScene(scene, ScenePath))
            throw new IOException("Could not save the initial scene.");
        AssetDatabase.SaveAssets();
    }

    private static JObject Facts()
    {
        if (!target)
            return new JObject();
        switch (target)
        {
            case Light light:
                return JObject.FromObject(new { light.range, light.intensity });
            case MeshRenderer renderer:
                var material = renderer.sharedMaterial;
                return JObject.FromObject(new { material = material?.name, shader = material?.shader?.name });
            case AudioSource source:
                return JObject.FromObject(new { source.volume, source.pitch });
            case ParticleSystem ps:
                return JObject.FromObject(new { rate = ps.emission.rateOverTime.constant, maximum = ps.main.maxParticles });
            case Animator animator:
                return JObject.FromObject(new { controller = AssetDatabase.GetAssetPath(animator.runtimeAnimatorController) });
            case Rigidbody body:
                return JObject.FromObject(new { body.mass, body.angularDamping });
            default:
                throw new InvalidOperationException("Target component unavailable.");
        }
    }

    private static string Hash(byte[] bytes)
    {
        using var sha = SHA256.Create();
        return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "");
    }

    private static string FileDigest(System.Collections.Generic.IEnumerable<string> paths) =>
        Hash(Encoding.UTF8.GetBytes(string.Join("\n", paths.OrderBy(p => p, StringComparer.Ordinal).Select(p => p + ":" + Hash(File.ReadAllBytes(p))))));

    private static string Configuration() =>
        FileDigest(
            Directory.GetFiles("ProjectSettings", "*", SearchOption.AllDirectories).Concat(new[] { "Packages/manifest.json", "Packages/packages-lock.json" })
        );

    private static JObject Snapshot()
    {
        string omit =
            task == "lower_lamp" ? "m_Intensity"
            : task == "lower_bell" ? "m_Volume"
            : null;
        var objects = new JArray();
        foreach (var scene in Enumerable.Range(0, SceneManager.sceneCount).Select(SceneManager.GetSceneAt))
        {
            objects.Add(new JArray(scene.path, scene.handle, scene.isLoaded));
            foreach (var trans in scene.GetRootGameObjects().SelectMany(o => o.GetComponentsInChildren<Transform>(true)))
            {
                var go = trans.gameObject;
                objects.Add(
                    new JArray(
                        go.GetInstanceID(),
                        go.name,
                        go.activeSelf,
                        go.tag,
                        go.layer,
                        trans.GetSiblingIndex(),
                        (int)GameObjectUtility.GetStaticEditorFlags(go)
                    )
                );
                foreach (var component in go.GetComponents<Component>())
                {
                    var serialized = JObject.Parse(EditorJsonUtility.ToJson(component));
                    if (component == target && omit != null && !(serialized.Properties().Single().Value is JObject fields && fields.Remove(omit)))
                        throw new InvalidOperationException("Missing target field " + omit);
                    objects.Add(new JArray(component.GetInstanceID(), component.GetType().Name, serialized));
                }
            }
        }
        var assetFiles = Directory
            .GetFiles("Assets", "*", SearchOption.AllDirectories)
            .Select(p => p.Replace('\\', '/'))
            .Where(p => omit == null || p != ScenePath);
        return new JObject
        {
            ["facts"] = Facts(),
            ["activeScene"] = SceneManager.GetActiveScene().path,
            ["selection"] = new JArray(Selection.instanceIDs),
            ["playing"] = EditorApplication.isPlayingOrWillChangePlaymode,
            ["sceneCollateral"] = Hash(Encoding.UTF8.GetBytes(objects.ToString(Formatting.None))),
            ["assetCollateral"] = FileDigest(assetFiles),
            ["configDigest"] = Configuration(),
        };
    }
}
