#if MCP_FOR_UNITY_AI_MODULE
using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.AI;

namespace MCPForUnity.Editor.Tools.Navigation
{
    [InitializeOnLoad]
    internal static class NavigationBakeJobs
    {
        private const string Key = "MCPForUnity.Navigation.BakeJobs.v1";
        private const int MaxJobs = 32;
        private static readonly List<JObject> Jobs = Restore();

        static NavigationBakeJobs() { }

        private static List<JObject> Restore()
        {
            var jobs = new List<JObject>();
            try
            {
                jobs = JArray.Parse(SessionState.GetString(Key, "[]")).OfType<JObject>().TakeLast(MaxJobs).ToList();
                foreach (JObject job in jobs)
                    if ((string)job["state"] == "queued" || (string)job["state"] == "running")
                    {
                        job["state"] = "interrupted";
                        job["detail"] = "Domain reload interrupted tracking. Native completion is unconfirmed; inspect surface data before another bake.";
                    }
                SessionState.SetString(Key, new JArray(jobs).ToString(Formatting.None));
            }
            catch (JsonException)
            {
                // Session-local corrupt job metadata never grants permission to resume a native bake.
                jobs.Add(
                    new JObject
                    {
                        ["job_id"] = "recovery",
                        ["state"] = "unavailable",
                        ["detail"] = "Bake job metadata was corrupt; no work resumed.",
                    }
                );
            }
            return jobs;
        }

        internal static bool Busy(Component c) =>
            Jobs.Any(j => (int?)j["component_id"] == c.GetInstanceIDCompat() && ((string)j["state"] == "queued" || (string)j["state"] == "running"));

        internal static object Request(JObject p)
        {
            NavigationObjects.RequireEditMode();
            if ((string)p["kind"] != "surface")
                throw new ArgumentException("bake requires kind=surface.");
            Component surface = NavigationAccess.Component(p);
            ValidateSurface(surface);
            if (Busy(surface))
                throw new InvalidOperationException("A bake is already queued or running for this surface.");
            if (Jobs.Count(j => (string)j["state"] == "queued") >= 8)
                throw new InvalidOperationException("Navigation bake queue is full (8 pending jobs).");
            if (Jobs.Count >= MaxJobs)
                Jobs.RemoveAt(Jobs.FindIndex(j => (string)j["state"] != "queued" && (string)j["state"] != "running"));
            JObject job = new JObject
            {
                ["job_id"] = Guid.NewGuid().ToString("N"),
                ["state"] = "queued",
                ["component_id"] = surface.GetInstanceIDCompat(),
                ["scene_handle"] = NavigationAccess.SceneHandle(surface.gameObject.scene),
                ["scene_path"] = surface.gameObject.scene.path,
                ["agent_type_id"] = (int)NavigationAccess.Get(surface, "agentTypeID"),
                ["queued_utc"] = DateTime.UtcNow.ToString("O"),
                ["native_bake_interruptible"] = false,
                ["detail"] =
                    "Requested only. One synchronous native BuildNavMesh call runs on the next editor update; no deadline or mid-call cancellation is possible.",
                ["surface_snapshot"] = EditorJsonUtility.ToJson(surface),
            };
            Jobs.Add(job);
            Save();
            bool executed = false;
            void RunOnce()
            {
                if (executed)
                    return;
                executed = true;
                EditorApplication.update -= RunOnce;
                Run(job);
            }
            // Baking is editor-update work; delayCall depends on inspector updates, which headless editors need not perform.
            EditorApplication.update += RunOnce;
            return new SuccessResponse("Navigation bake requested.", Public(job));
        }

        private static void ValidateSurface(Component surface)
        {
            if (!(surface is Behaviour behaviour) || !behaviour.isActiveAndEnabled)
                throw new ArgumentException("Bake requires an active, enabled surface.");
            if (NavigationAccess.Get(surface, "collectObjects")?.ToString() != "Children")
                throw new ArgumentException(
                    "Bake requires collectObjects=Children to bound source selection to this surface hierarchy. Configure it explicitly; global All/Volume bakes are refused."
                );
            NavigationAccess.AgentType((int)NavigationAccess.Get(surface, "agentTypeID"));
            if (surface.GetType().GetMethod("BuildNavMesh", Type.EmptyTypes) == null)
                throw new InvalidOperationException("Installed AI Navigation version does not expose BuildNavMesh.");
        }

        private static void Run(JObject job)
        {
            if ((string)job["state"] != "queued")
                return;
            Component surface = null;
            NavMeshData previous = null;
            int group = -1;
            try
            {
                NavigationObjects.RequireEditMode();
                surface = NavigationAccess.Component(
                    new JObject
                    {
                        ["scene_handle"] = job["scene_handle"],
                        ["kind"] = "surface",
                        ["target"] = job["component_id"],
                    }
                );
                ValidateSurface(surface);
                if (EditorJsonUtility.ToJson(surface) != (string)job["surface_snapshot"])
                    throw new InvalidOperationException("Surface configuration changed after request; no bake performed.");
                job["state"] = "running";
                job["started_utc"] = DateTime.UtcNow.ToString("O");
                Save();
                previous = NavigationAccess.Get(surface, "navMeshData") as NavMeshData;
                Undo.IncrementCurrentGroup();
                group = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName("Bake Navigation Surface");
                Undo.RecordObject(surface, "Bake Navigation Surface");
                surface.GetType().GetMethod("BuildNavMesh", Type.EmptyTypes).Invoke(surface, null);
                NavMeshData data = NavigationAccess.Get(surface, "navMeshData") as NavMeshData;
                if (data == null)
                    throw new InvalidOperationException("Native BuildNavMesh returned without NavMeshData.");
                Undo.FlushUndoRecordObjects();
                Undo.CollapseUndoOperations(group);
                EditorUtility.SetDirty(surface);
                PrefabUtility.RecordPrefabInstancePropertyModifications(surface);
                EditorSceneManager.MarkSceneDirty(surface.gameObject.scene);
                job["state"] = "completed";
                job["has_baked_data"] = true;
                job["detail"] =
                    "Native BuildNavMesh returned with data. Path connectivity must be queried separately. Save scene/data explicitly for persistence; configuration Undo does not guarantee native data registration rollback.";
            }
            catch (Exception ex)
            {
                job["state"] = "failed";
                job["detail"] = ex.InnerException?.Message ?? ex.Message;
                if (surface != null && group >= 0)
                {
                    try
                    {
                        surface.GetType().GetMethod("RemoveData").Invoke(surface, null);
                        surface.GetType().GetProperty("navMeshData").SetValue(surface, previous);
                        surface.GetType().GetMethod("AddData").Invoke(surface, null);
                        Undo.FlushUndoRecordObjects();
                        Undo.CollapseUndoOperations(group);
                        job["previous_data_restored"] = true;
                    }
                    catch (Exception restore)
                    {
                        job["previous_data_restored"] = false;
                        job["restoration_error"] = restore.InnerException?.Message ?? restore.Message;
                    }
                }
            }
            job["finished_utc"] = DateTime.UtcNow.ToString("O");
            job.Remove("surface_snapshot");
            Save();
        }

        internal static object Status(JObject p)
        {
            if (p["job_id"] == null)
                return new SuccessResponse("Retained navigation bake jobs (session only, maximum 32).", new { jobs = Jobs.Select(Public).ToArray() });
            return new SuccessResponse("Navigation bake status.", Public(Find(p)));
        }

        internal static object Cancel(JObject p)
        {
            JObject job = Find(p);
            if ((string)job["state"] == "queued")
            {
                job["state"] = "cancelled";
                job["finished_utc"] = DateTime.UtcNow.ToString("O");
                job["detail"] = "Cancelled before native BuildNavMesh started; no bake performed.";
                job.Remove("surface_snapshot");
                Save();
            }
            else
                return new ErrorResponse(
                    "Only queued bakes can be cancelled. Native BuildNavMesh is synchronous and indivisible; completed work is not reverted.",
                    Public(job)
                );
            return new SuccessResponse("Queued navigation bake cancelled.", Public(job));
        }

        private static JObject Find(JObject p) =>
            Jobs.FirstOrDefault(j => (string)j["job_id"] == (string)p["job_id"])
            ?? throw new ArgumentException("Unknown or expired job_id; only the last 32 editor-session jobs are retained.");

        private static JObject Public(JObject job)
        {
            JObject result = (JObject)job.DeepClone();
            result.Remove("surface_snapshot");
            return result;
        }

        private static void Save() => SessionState.SetString(Key, new JArray(Jobs).ToString(Formatting.None));
    }
}
#endif
