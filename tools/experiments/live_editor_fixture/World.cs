using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.AI;
using UnityEngine.SceneManagement;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

// Loaded from the private control directory, outside AssetDatabase and the project.
public static class LiveWorld
{
    private const string InputPath = "Assets/Controls/GameControls.inputactions";
    private const string TerrainPath = "Assets/Landscape.asset";
    private static readonly string[] Languages = { "lv", "sk", "ur", "te", "ca", "nb" };
    private static readonly string[] ReadClips = { "zvans.wav", "hlas.ogg", "Rain-بارش.wav", "Rain-వాన.wav", "Campana.wav", "Fuglekvitter.wav" };
    private static readonly string[] WriteClips = { "Vējš.wav", "krok.wav", "Bell-گھنٹی.wav", "Bell-గంట.wav", "Veu del narrador.wav", "Bølgeskvulp.wav" };
    private static readonly string[] ReadPlatforms = { "Android", "iOS", "Android", "Android", "iOS", "Android" };
    private static readonly string[] WritePlatforms = { "iOS", "Android", "iOS", "iOS", "Android", "iOS" };
    private static readonly string[] InputActions = { "OpenMap", "Jump", "OpenDoor", "Jump", "OpenInventory", "Crouch" };
    private static readonly string[] Keys = { "m", "space", "e", "space", "i", "c" };
    private static readonly string[][] MapNames =
    {
        new[] { "Laiva", "Krasts" },
        new[] { "Pešo", "Vozidlo" },
        new[] { "کشتی", "ساحل" },
        new[] { "నడక", "ఈత" },
        new[] { "Rem", "Vela" },
        new[] { "Kjøring", "Gange" },
    };
    private static readonly string[] ReadLand = { "Cave-Ala", "Bridge-Most", "Tunnel-سرنگ", "Cave-గుహ", "Pati", "Kai" };
    private static readonly string[] WriteLand = { "Cave Entrance-Alas ieeja", "Tower-Veža", "Courtyard-صحن", "Bridge-వంతెన", "Bosc", "Porten" };
    private static readonly int[,] ReadRegion =
    {
        { 18, 24, 2, 1 },
        { 40, 36, 2, 2 },
        { 27, 18, 2, 1 },
        { 31, 26, 2, 2 },
        { 14, 28, 2, 1 },
        { 20, 30, 3, 1 },
    };
    private static readonly int[,] WriteCell =
    {
        { 18, 24 },
        { 33, 21 },
        { 17, 22 },
        { 23, 19 },
        { 35, 42 },
        { 26, 32 },
    };
    private static string task,
        language,
        seed,
        clip,
        platform,
        targetScene,
        control,
        immutableState;
    private static int locale,
        variant,
        initialImports;
    private static int gateColliderId;
    private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false);

    private static string Hash(byte[] bytes)
    {
        using var sha = SHA256.Create();
        return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
    }

    private static string Hash(string value) => Hash(Utf8.GetBytes(value));

    private static string Hash(JToken value) => Hash(value.ToString(Formatting.None));

    private static string Id(string name) => Guid.ParseExact(Hash(seed + "/" + language + "/" + name).Substring(0, 32), "N").ToString();

    private static bool IsInput => task == "saved_controls_identity" || task == "author_two_control_maps";
    private static bool IsAudio => task == "clip_platform_inheritance" || task == "restore_clip_inheritance";
    private static bool IsTerrain => task == "sample_surface_mask" || task == "fill_surface_gap";

    public static JObject Dispatch(JObject request)
    {
        string requestedTask = (string)request["task"];
        string requestedLanguage = (string)request["language"];
        string requestedSeed = request["seed"].ToString();
        if ((string)request["op"] == "prepare")
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling)
                throw new InvalidOperationException("Editor is busy.");
            string project = Path.GetDirectoryName(Application.dataPath);
            control = Path.Combine(Path.GetDirectoryName(project), "control");
            string fixedState = ImmutableState();
            if (immutableState != null && fixedState != immutableState)
                throw new InvalidOperationException("Project configuration changed; restore this lane before reuse.");
            task = requestedTask;
            language = requestedLanguage;
            seed = requestedSeed;
            locale = Array.IndexOf(Languages, language);
            variant = Convert.ToInt32(Hash(seed + "/" + language + "/" + task).Substring(0, 6), 16);
            targetScene = clip = platform = null;
            Prepare();
            immutableState = ImmutableState();
            initialImports = LaneHost.InputImports;
        }
        else if (requestedTask != task || requestedLanguage != language || requestedSeed != seed)
            throw new InvalidOperationException("No matching prepared cell.");
        return Snapshot();
    }

    private static string ImmutableState()
    {
        var files = Directory
            .GetFiles("ProjectSettings", "*", SearchOption.AllDirectories)
            .Concat(new[] { "Packages/manifest.json", "Packages/packages-lock.json", "Assets/Editor/LaneHost.dll" });
        return Hash(string.Join("\n", files.OrderBy(p => p, StringComparer.Ordinal).Select(p => p + ":" + Hash(File.ReadAllBytes(p)))));
    }

    private static void Prepare()
    {
        foreach (var window in Resources.FindObjectsOfTypeAll<EditorWindow>().Where(w => w.GetType().Name == "FrameDebuggerWindow"))
            window.Close();
        Selection.activeObject = null;
        var first = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        foreach (string path in Directory.GetFileSystemEntries("Assets"))
        {
            string relative = path.Replace('\\', '/');
            if (relative == "Assets/Editor" || relative.EndsWith(".meta", StringComparison.Ordinal))
                continue;
            if (!AssetDatabase.DeleteAsset(relative))
                throw new IOException("Failed to reset " + relative);
        }
        Undo.ClearAll();
        AssetDatabase.CreateFolder("Assets", "Scenes");
        string[] names = { "West Harbor", "Signal Hill" };
        if (task == "activate_scene" || task == "activate_named_scene")
        {
            if (language == "ja")
                names = task == "activate_scene" ? new[] { "丘陵", "朝霧の埠頭" } : new[] { "灯台", "潮だまり" };
            else if (language == "es")
                names = task == "activate_scene" ? new[] { "Puerto", "Cantera" } : new[] { "Invernadero", "Patio", "Estación del Norte" };
            else
                throw new ArgumentException("Unknown activation language.");
            targetScene = names.Last();
        }
        var scenes = new List<Scene> { first };
        for (int i = 0; i < names.Length; ++i)
        {
            var scene = i == 0 ? first : EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            if (i != 0)
                scenes.Add(scene);
            SceneManager.SetActiveScene(scene);
            var marker = new GameObject(i == 0 ? "Quay" : "Beacon" + i);
            marker.transform.localPosition = new Vector3(2 + i, 0, 9 - i);
            if (!EditorSceneManager.SaveScene(scene, "Assets/Scenes/" + names[i] + ".unity"))
                throw new IOException("Scene save failed.");
        }
        var active = targetScene != null || task == "agent_list" || task == "selected_agent" ? first : scenes[variant % scenes.Count];
        SceneManager.SetActiveScene(active);
        if (IsAudio)
            PrepareAudio();
        else if (IsInput)
            PrepareInput();
        else if (IsTerrain)
            PrepareTerrain();
        else if (task == "selected_agent" || task == "agent_list")
        {
            var guard = new GameObject(language == "ja" ? "門番" : "순찰병").AddComponent<NavMeshAgent>();
            guard.speed = 4.25f;
            guard.acceleration = 9.5f;
            guard.stoppingDistance = .625f;
            guard.angularSpeed = 137;
            guard.radius = .4375f;
            guard.height = 1.875f;
            new GameObject(language == "ja" ? "伝令" : "전령").AddComponent<NavMeshAgent>().speed = 5.5f;
            Selection.activeGameObject = guard.gameObject;
        }
        else if (task == "lookout_fov")
        {
            var cam = new GameObject("Lookout").AddComponent<Camera>();
            cam.orthographic = false;
            cam.fieldOfView = 38.25f + (variant % 38) * .75f;
            new GameObject("Courtyard Camera").AddComponent<Camera>().fieldOfView = 72;
        }
        else if (task == "gate_sensor_trigger")
        {
            var box = new GameObject("GateSensor").AddComponent<BoxCollider>();
            gateColliderId = box.GetInstanceID();
            box.transform.position = new Vector3(7, 0, 3);
            box.size = new Vector3(1.5f, 4, .75f);
            box.center = new Vector3(.125f, 1.125f, -.25f);
            box.isTrigger = false;
            new GameObject("GateFrame").AddComponent<BoxCollider>();
        }
        else if (
            !new[]
            {
                "current_scene",
                "short_current_scene",
                "short_active_scene",
                "scene_list",
                "activate_scene",
                "activate_named_scene",
                "installed_package",
                "package_list",
                "menu-path",
            }.Contains(task)
        )
            throw new ArgumentException("Unsupported fixture task: " + task);
        EditorSceneManager.SaveScene(active);
        AssetDatabase.SaveAssets();
    }

    private static void PrepareAudio()
    {
        bool write = task == "restore_clip_inheritance";
        string name = (write ? WriteClips : ReadClips)[locale];
        clip = "Assets/Audio/" + name;
        platform = (write ? WritePlatforms : ReadPlatforms)[locale];
        AssetDatabase.CreateFolder("Assets", "Audio");
        File.Copy(Path.Combine(control, "tone" + Path.GetExtension(name)), clip);
        AssetDatabase.ImportAsset(clip, ImportAssetOptions.ForceSynchronousImport);
        var imp = AssetImporter.GetAtPath(clip) as AudioImporter ?? throw new IOException("No audio importer.");
        var settings = imp.defaultSampleSettings;
        settings.quality = .45f;
        if (write || variant % 2 == 0)
            imp.SetOverrideSampleSettings(platform, settings);
        settings.quality = .61f;
        imp.SetOverrideSampleSettings(platform == "Android" ? "iOS" : "Android", settings);
        imp.SaveAndReimport();
        if (write && !imp.ContainsSampleSettingsOverride(platform))
            throw new IOException("Target override missing.");
    }

    private static void PrepareInput()
    {
        AssetDatabase.CreateFolder("Assets", "Controls");
        string action = InputActions[locale];
        var map = new
        {
            name = "Gameplay",
            id = Id("gameplay"),
            actions = new[]
            {
                new
                {
                    name = action,
                    type = "Button",
                    id = Id("action"),
                    expectedControlType = "Button",
                    processors = "",
                    interactions = "",
                    initialStateCheck = false,
                },
            },
            bindings = new[]
            {
                new
                {
                    name = "",
                    id = Id("binding"),
                    path = "<Keyboard>/" + Keys[locale],
                    interactions = "",
                    processors = "",
                    groups = "",
                    action,
                    isComposite = false,
                    isPartOfComposite = false,
                },
            },
        };
        var doc = new
        {
            name = "GameControls",
            maps = new[] { map },
            controlSchemes = new object[0],
        };
        File.WriteAllText(InputPath, JsonConvert.SerializeObject(doc, Formatting.Indented) + "\n", Utf8);
        AssetDatabase.ImportAsset(InputPath, ImportAssetOptions.ForceSynchronousImport);
        if (AssetDatabase.LoadMainAssetAtPath(InputPath)?.GetType().Name != "InputActionAsset")
            throw new IOException("Input System importer unavailable.");
    }

    private static void PrepareTerrain()
    {
        var data = new TerrainData { heightmapResolution = 65, size = new Vector3(64, 10, 64) };
        int n = data.holesResolution;
        bool[,] holes = new bool[n, n];
        bool write = task == "fill_surface_gap";
        for (int y = 0; y < n; ++y)
        for (int x = 0; x < n; ++x)
            holes[y, x] = (x + 3 * y + variant) % 17 != 0;
        if (write)
            holes[WriteCell[locale, 1], WriteCell[locale, 0]] = false;
        else
            for (int dy = 0; dy < ReadRegion[locale, 3]; ++dy)
            for (int dx = 0; dx < ReadRegion[locale, 2]; ++dx)
                holes[ReadRegion[locale, 1] + dy, ReadRegion[locale, 0] + dx] = (dx + dy + variant) % 2 == 0;
        data.SetHoles(0, 0, holes);
        AssetDatabase.CreateAsset(data, TerrainPath);
        var go = Terrain.CreateTerrainGameObject(data);
        go.name = (write ? WriteLand : ReadLand)[locale];
    }

    private static JObject Snapshot()
    {
        var scenes = Enumerable.Range(0, SceneManager.sceneCount).Select(SceneManager.GetSceneAt).Where(s => s.isLoaded).ToArray();
        var state = new JObject
        {
            ["activeScene"] = SceneManager.GetActiveScene().name,
            ["loadedScenes"] = new JArray(scenes.Select(s => s.name)),
            ["sceneIdentities"] = JArray.FromObject(
                scenes.Select(s => new
                {
                    s.name,
                    s.path,
                    s.handle,
                })
            ),
            ["sceneCollateral"] = SceneDigest(scenes),
            ["assetCollateral"] = AssetDigest(),
            ["selection"] = Selection.activeObject?.name ?? "",
            ["frameDebuggerWindows"] = Resources.FindObjectsOfTypeAll<EditorWindow>().Count(w => w.GetType().Name == "FrameDebuggerWindow"),
            ["playMode"] = EditorApplication.isPlayingOrWillChangePlaymode,
            ["configDigest"] = ImmutableState(),
        };
        if (targetScene != null)
            state["targetScene"] = targetScene;
        if (IsAudio)
            AudioSnapshot(state);
        else if (IsInput)
            InputSnapshot(state);
        else if (IsTerrain)
            TerrainSnapshot(state);
        else if (task == "selected_agent" || task == "agent_list")
        {
            var agents = scenes.SelectMany(s => s.GetRootGameObjects()).SelectMany(o => o.GetComponentsInChildren<NavMeshAgent>(true)).ToArray();
            state["agentNames"] = new JArray(agents.Select(a => a.name).OrderBy(n => n, StringComparer.Ordinal));
            var a = Selection.activeGameObject?.GetComponent<NavMeshAgent>();
            if (a != null)
                state["selectedAgent"] = JObject.FromObject(
                    new
                    {
                        a.name,
                        a.speed,
                        a.angularSpeed,
                        a.acceleration,
                        a.stoppingDistance,
                        a.autoBraking,
                        a.radius,
                        a.height,
                        a.baseOffset,
                        a.areaMask,
                        a.avoidancePriority,
                        obstacleAvoidanceType = a.obstacleAvoidanceType.ToString(),
                        a.autoTraverseOffMeshLink,
                        a.autoRepath,
                        a.agentTypeID,
                        a.updatePosition,
                        a.updateRotation,
                        a.updateUpAxis,
                        a.enabled,
                    }
                );
        }
        else if (task == "lookout_fov")
        {
            var cam = GameObject.Find("Lookout")?.GetComponent<Camera>();
            if (cam != null)
            {
                state["cameraFov"] = cam.fieldOfView;
                state["cameraOrthographic"] = cam.orthographic;
            }
        }
        else if (task == "gate_sensor_trigger")
        {
            state["gateObserverVersion"] = 2;
            var box = GameObject.Find("GateSensor")?.GetComponent<BoxCollider>();
            if (box != null)
            {
                var sharedMaterial = box.sharedMaterial;
                state["boxSharedMaterial"] = sharedMaterial ? sharedMaterial.name : "";
                state["boxSharedMaterialId"] = sharedMaterial ? sharedMaterial.GetInstanceID() : 0;
                state["boxTrigger"] = box.isTrigger;
                state["boxId"] = box.GetInstanceID();
                state["boxSize"] = Vector(box.size);
                state["boxCenter"] = Vector(box.center);
                state["boxEnabled"] = box.enabled;
            }
        }
        else if (task == "menu-path")
        {
            var methods = TypeCache
                .GetMethodsWithAttribute<MenuItem>()
                .SelectMany(m => m.GetCustomAttributes(typeof(MenuItem), false).Cast<MenuItem>().Select(i => new { method = m, item = i }))
                .Where(v => !v.item.validate && v.item.menuItem.IndexOf("Frame Debugger", StringComparison.OrdinalIgnoreCase) >= 0)
                .ToArray();
            state["menuPaths"] = new JArray(methods.Select(v => v.item.menuItem).Distinct().OrderBy(n => n, StringComparer.Ordinal));
            state["menuMethods"] = new JArray(methods.Select(v => v.method.DeclaringType.FullName + "." + v.method.Name));
            if (methods.Length == 0)
                throw new IOException("No Frame Debugger declaration.");
        }
        else if (task == "package_list" || task == "installed_package")
        {
            var packages = PackageInfo.GetAllRegisteredPackages();
            state["packages"] = JObject.FromObject(packages.ToDictionary(p => p.name, p => p.version));
            if (task == "installed_package")
            {
                string name = language == "ja" ? "com.unity.inputsystem" : "com.unity.cinemachine";
                var p = packages.Single(v => v.name == name);
                state["targetPackage"] = JObject.FromObject(
                    new
                    {
                        p.name,
                        p.version,
                        p.displayName,
                        p.description,
                        source = p.source.ToString(),
                        registry = p.registry?.url,
                        author = p.author?.name,
                    }
                );
            }
        }
        return state;
    }

    private static JArray Vector(Vector3 v) => new JArray(v.x, v.y, v.z);

    private static string SceneDigest(Scene[] scenes)
    {
        var rows = new JArray();
        foreach (var scene in scenes)
        foreach (var t in scene.GetRootGameObjects().SelectMany(root => root.GetComponentsInChildren<Transform>(true)))
        {
            var comps = new JArray();
            foreach (var c in t.GetComponents<Component>())
            {
                if (!c)
                {
                    comps.Add("missing");
                    continue;
                }
                var obj = JObject.Parse(EditorJsonUtility.ToJson(c));
                if (task == "gate_sensor_trigger" && c is Collider collider)
                {
                    if (c.GetInstanceID() == gateColliderId)
                    {
                        if (obj[nameof(BoxCollider)] is not JObject fields || !fields.Remove("m_IsTrigger"))
                            throw new InvalidDataException("Unity did not expose the target BoxCollider trigger field.");
                    }
                    var sharedMaterial = collider.sharedMaterial;
                    obj["runtimeSharedMaterialId"] = sharedMaterial ? sharedMaterial.GetInstanceID() : 0;
                }
                comps.Add(new JArray(c.GetType().FullName, c.GetInstanceID(), obj));
            }
            rows.Add(
                new JArray(
                    scene.path,
                    t.gameObject.GetInstanceID(),
                    t.name,
                    t.gameObject.activeSelf,
                    t.gameObject.layer,
                    t.gameObject.tag,
                    t.GetSiblingIndex(),
                    comps
                )
            );
        }
        return Hash(rows);
    }

    private static string AssetDigest()
    {
        var rows = new List<string>();
        var paths = Directory.GetFiles("Assets", "*", SearchOption.AllDirectories).Select(p => p.Replace('\\', '/')).OrderBy(p => p, StringComparer.Ordinal);
        foreach (string path in paths)
        {
            if (
                task == "restore_clip_inheritance" && path == clip + ".meta"
                || task == "author_two_control_maps" && path == InputPath
                || task == "fill_surface_gap" && path == TerrainPath
                || task == "gate_sensor_trigger" && path == GameObject.Find("GateSensor")?.scene.path
            )
                continue;
            rows.Add(path + ":" + Hash(File.ReadAllBytes(path)));
        }
        return Hash(string.Join("\n", rows));
    }

    private static void AudioSnapshot(JObject state)
    {
        var imp = AssetImporter.GetAtPath(clip) as AudioImporter ?? throw new IOException("Target audio asset missing.");
        state["audioPath"] = clip;
        state["audioPlatform"] = platform;
        state["audioOverride"] = imp.ContainsSampleSettingsOverride(platform);
        var other = new JObject();
        foreach (var name in new[] { "Android", "iOS", "Standalone", "WebGL" }.Where(n => n != platform))
            other[name] = JObject.FromObject(new { has = imp.ContainsSampleSettingsOverride(name), settings = imp.GetOverrideSampleSettings(name) });
        state["audioCollateral"] = Hash(
            JObject.FromObject(
                new
                {
                    imp.defaultSampleSettings,
                    imp.forceToMono,
                    imp.ambisonic,
                    imp.loadInBackground,
                    other,
                    guid = AssetDatabase.AssetPathToGUID(clip),
                }
            )
        );
    }

    private static void InputSnapshot(JObject state)
    {
        var bytes = File.ReadAllBytes(InputPath);
        var doc = JObject.Parse(Utf8.GetString(bytes));
        var maps = (JArray)doc["maps"];
        state["inputPath"] = InputPath;
        state["fileHash"] = Hash(bytes);
        state["bindingId"] = maps.Children<JObject>()
            .SelectMany(m => (JArray)m["bindings"])
            .FirstOrDefault(b => (string)b["action"] == InputActions[locale])
            ?["id"];
        state["inputMaps"] = JObject.FromObject(maps.Children<JObject>().ToDictionary(m => (string)m["name"], m => m));
        state["targetMaps"] = new JArray(MapNames[locale]);
        state["importCount"] = LaneHost.InputImports - initialImports;
        doc.Remove("maps");
        state["inputCollateral"] = Hash(doc);
    }

    private static void TerrainSnapshot(JObject state)
    {
        var data = AssetDatabase.LoadAssetAtPath<TerrainData>(TerrainPath);
        if (data == null)
            throw new IOException("Target terrain asset missing.");
        int n = data.holesResolution;
        var holes = data.GetHoles(0, 0, n, n);
        var rows = new JArray();
        bool write = task == "fill_surface_gap";
        int x0 = write ? WriteCell[locale, 0] - 1 : ReadRegion[locale, 0];
        int y0 = write ? WriteCell[locale, 1] - 1 : ReadRegion[locale, 1];
        for (int y = y0; y < y0 + (write ? 3 : ReadRegion[locale, 3]); ++y)
        for (int x = x0; x < x0 + (write ? 3 : ReadRegion[locale, 2]); ++x)
            rows.Add(
                JObject.FromObject(
                    new
                    {
                        x,
                        y,
                        surface = holes[y, x],
                    }
                )
            );
        var mask = new byte[n * n];
        for (int y = 0; y < n; ++y)
        for (int x = 0; x < n; ++x)
            mask[y * n + x] = (byte)(holes[y, x] && !(write && x == WriteCell[locale, 0] && y == WriteCell[locale, 1]) ? 1 : 0);
        var heights = data.GetHeights(0, 0, data.heightmapResolution, data.heightmapResolution);
        var bytes = new byte[heights.Length * sizeof(float)];
        Buffer.BlockCopy(heights, 0, bytes, 0, bytes.Length);
        state["surfaceSamples"] = rows;
        state["terrainCollateral"] = Hash(
            new JArray(n, Hash(mask), Hash(bytes), Vector(data.size), data.treeInstanceCount, data.terrainLayers.Length, data.detailResolution)
        );
    }
}
