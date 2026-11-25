using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

// This editor-only precompiled host is the only fixture code installed in the project.
public static class LaneHost
{
    private const BindingFlags All = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;
    private static string control,
        status,
        project,
        projectHash,
        statusFile,
        generation;
    private static IDisposable publication;
    private static Type bridge;
    private static MethodInfo dispatch;
    private static double nextHeartbeat,
        nextPoll;
    private static int seq;
    public static int InputImports { get; internal set; }

    public static void Start()
    {
        project = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
        string lane = Path.GetDirectoryName(project);
        control = Path.Combine(lane, "control");
        status = Path.Combine(lane, "status");
        if (
            !string.Equals(project, Environment.GetEnvironmentVariable("LIVE_LANE_PROJECT"), StringComparison.OrdinalIgnoreCase)
            || !string.Equals(status, Environment.GetEnvironmentVariable("UNITY_MCP_STATUS_DIR"), StringComparison.OrdinalIgnoreCase)
            || !File.Exists(Path.Combine(lane, "owned-lane.json"))
            || !Application.isBatchMode
        )
            throw new InvalidOperationException("Lane identity mismatch.");
        try
        {
            var assembly = AppDomain.CurrentDomain.GetAssemblies().Single(a => a.GetName().Name == "MCPForUnity.Editor");
            string ns = "MCPForUnity.Editor.Services.Transport.Transports.";
            bridge = assembly.GetType(ns + "StdioBridgeHost", true);
            string ephemeral = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            bridge.GetMethod("StartOwned", All).Invoke(null, new object[] { 0, ephemeral });
            var auth = bridge.GetField("authentication", All).GetValue(null);
            generation = (string)auth.GetType().GetField("Generation", All).GetValue(auth);
            publication = (IDisposable)
                Activator.CreateInstance(assembly.GetType(ns + "StdioLaunchCredential", true), All, null, new object[] { generation, ephemeral, null }, null);
            using (var hash = SHA1.Create())
                projectHash = BitConverter
                    .ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(Application.dataPath)))
                    .Replace("-", "")
                    .ToLowerInvariant()
                    .Substring(0, 8);
            statusFile = Path.Combine(status, "unity-mcp-status-" + projectHash + ".json");
            EditorApplication.update += Poll;
            EditorApplication.quitting += Cleanup;
            Poll();
        }
        catch (Exception e)
        {
            File.WriteAllText(Path.Combine(control, "startup-error.txt"), e.GetBaseException().Message);
            EditorApplication.Exit(2);
        }
    }

    private static void Cleanup()
    {
        if (statusFile != null && File.Exists(statusFile))
            File.Delete(statusFile);
        bridge?.GetMethod("Stop", All, null, Type.EmptyTypes, null)?.Invoke(null, null);
        publication?.Dispose();
    }

    private static void Put(string path, JObject doc)
    {
        string temp = path + ".tmp";
        File.WriteAllText(temp, doc.ToString(Formatting.None), new UTF8Encoding(false));
        if (File.Exists(path))
            File.Replace(temp, path, null);
        else
            File.Move(temp, path);
    }

    private static void Poll()
    {
        double now = EditorApplication.timeSinceStartup;
        if (now >= nextHeartbeat)
        {
            nextHeartbeat = now + 2;
            Put(
                statusFile,
                JObject.FromObject(
                    new
                    {
                        unity_port = bridge.GetMethod("GetCurrentPort", All).Invoke(null, null),
                        listener_generation = generation,
                        reloading = false,
                        reason = "ready",
                        seq = ++seq,
                        project_path = Application.dataPath,
                        project_name = Path.GetFileName(project),
                        unity_version = Application.unityVersion,
                        last_heartbeat = DateTime.UtcNow.ToString("O"),
                        project_scoped_tools = false,
                    }
                )
            );
        }
        if (now < nextPoll)
            return;
        nextPoll = now + .05;
        string path = Path.Combine(control, "request.json");
        if (!File.Exists(path))
            return;
        var req = JObject.Parse(File.ReadAllText(path));
        File.Delete(path);
        string op = (string)req["op"];
        var resp = new JObject
        {
            ["id"] = req["id"],
            ["ok"] = true,
            ["task"] = req["task"],
            ["language"] = req["language"],
            ["seed"] = req["seed"],
            ["instance"] = Path.GetFileName(project) + "@" + projectHash,
            ["project_root"] = project,
            ["pid"] = System.Diagnostics.Process.GetCurrentProcess().Id,
            ["availability"] = JObject.FromObject(new { ready = true, reason = "" }),
        };
        try
        {
            if (op == "prepare" || op == "snapshot")
            {
                if (dispatch == null)
                    dispatch = Assembly.Load(File.ReadAllBytes(Path.Combine(control, "World.dll"))).GetType("LiveWorld", true).GetMethod("Dispatch");
                resp["state"] = (JObject)dispatch.Invoke(null, new object[] { req });
            }
            else if (op != "ping" && op != "stop")
                throw new ArgumentException("Unknown operation.");
        }
        catch (Exception e)
        {
            resp["ok"] = false;
            resp["error"] = e.GetBaseException().ToString();
            resp["availability"] = JObject.FromObject(new { ready = false, reason = e.GetBaseException().Message });
        }
        Put(Path.Combine(control, "response.json"), resp);
        if (op == "stop")
            EditorApplication.Exit(0);
    }
}

internal sealed class LaneImportObserver : AssetPostprocessor
{
    private static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
    {
        LaneHost.InputImports += imported.Count(path => path == "Assets/Controls/GameControls.inputactions");
    }
}
