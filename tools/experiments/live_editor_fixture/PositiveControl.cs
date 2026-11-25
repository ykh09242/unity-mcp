using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

[InitializeOnLoad]
public static class PositiveControl
{
    static readonly string dir;
    static double next;

    static PositiveControl()
    {
        string project = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
        if (!string.Equals(Environment.GetEnvironmentVariable("LIVE_LANE_PROJECT"), project, StringComparison.OrdinalIgnoreCase))
            return;
        dir = Path.Combine(Path.GetDirectoryName(project), "control");
        if (!File.Exists(Path.Combine(dir, "positive-control-only")) || !Application.isBatchMode)
            return;
        EditorApplication.update += Poll;
    }

    static JObject Components()
    {
        var result = new JObject();
        for (int i = 0; i < SceneManager.sceneCount; i++)
            foreach (var o in SceneManager.GetSceneAt(i).GetRootGameObjects())
            foreach (var c in o.GetComponents<Component>())
                result[o.name + "/" + c.GetType().Name] = JObject.Parse(EditorJsonUtility.ToJson(c));
        return result;
    }

    static void Poll()
    {
        if (EditorApplication.timeSinceStartup < next)
            return;
        next = EditorApplication.timeSinceStartup + .10;
        var path = Path.Combine(dir, "positive-control.request");
        if (!File.Exists(path))
            return;
        var req = JObject.Parse(File.ReadAllText(path));
        File.Delete(path);
        var r = new JObject { ["id"] = req["id"] };
        try
        {
            r["before"] = Components();
            string op = (string)req["op"];
            if (op.StartsWith("box"))
            {
                var box = GameObject.Find("GateSensor").GetComponent<BoxCollider>();
                r["sharedMaterialBefore"] = box.sharedMaterial ? box.sharedMaterial.GetInstanceID() : 0;
                if (op == "box-getter")
                {
                    var material = box.material;
                    r["getterMaterial"] = material ? material.GetInstanceID() : 0;
                }
                if (op != "box-save-only")
                    box.isTrigger = true;
                if (op == "box-save" || op == "box-save-only" || op == "box-getter")
                {
                    EditorSceneManager.MarkSceneDirty(box.gameObject.scene);
                    if (!EditorSceneManager.SaveScene(box.gameObject.scene))
                        throw new IOException("save failed");
                }
                r["sharedMaterialAfter"] = box.sharedMaterial ? box.sharedMaterial.GetInstanceID() : 0;
            }
            else if (op == "scene")
            {
                if (!SceneManager.SetActiveScene(SceneManager.GetSceneByName((string)req["targetScene"])))
                    throw new Exception("set active failed");
            }
            else if (op == "audio")
            {
                var imp = (AudioImporter)AssetImporter.GetAtPath((string)req["audioPath"]);
                if (!imp.ClearSampleSettingOverride((string)req["audioPlatform"]))
                    throw new Exception("clear failed");
                imp.SaveAndReimport();
            }
            else if (op == "input")
            {
                string asset = (string)req["inputPath"];
                var json = JObject.Parse(File.ReadAllText(asset));
                foreach (string name in (JArray)req["targetMaps"])
                    ((JArray)json["maps"]).Add(
                        new JObject
                        {
                            ["name"] = name,
                            ["id"] = Guid.NewGuid().ToString(),
                            ["actions"] = new JArray(),
                            ["bindings"] = new JArray(),
                        }
                    );
                File.WriteAllText(asset, json.ToString(Newtonsoft.Json.Formatting.Indented) + "\n", new System.Text.UTF8Encoding(false));
                AssetDatabase.ImportAsset(asset, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            }
            else if (op == "terrain")
            {
                var td = AssetDatabase.LoadAssetAtPath<TerrainData>("Assets/Landscape.asset");
                td.SetHoles(
                    (int)req["x"],
                    (int)req["y"],
                    new bool[,]
                    {
                        { true },
                    }
                );
                EditorUtility.SetDirty(td);
                AssetDatabase.SaveAssets();
            }
            else
                throw new ArgumentException("Unknown control.");
            r["after"] = Components();
            r["ok"] = true;
        }
        catch (Exception e)
        {
            r["ok"] = false;
            r["error"] = e.ToString();
        }
        string outPath = Path.Combine(dir, "positive-control.json");
        File.WriteAllText(outPath + ".tmp", r.ToString());
        if (File.Exists(outPath))
            File.Replace(outPath + ".tmp", outPath, null);
        else
            File.Move(outPath + ".tmp", outPath);
    }
}
