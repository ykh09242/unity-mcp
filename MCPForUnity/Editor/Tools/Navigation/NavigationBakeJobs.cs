#if MCP_FOR_UNITY_AI_MODULE
using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport;
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
        private static readonly Dictionary<string, (Component Surface, string Snapshot)> RuntimeTargets = new();
        private static readonly List<JObject> Jobs = Restore();

        static NavigationBakeJobs()
        {
            EditorApplication.update += Pump;
        }

        private static List<JObject> Restore()
        {
            var jobs = NavigationBakeCheckpoint.Restore(SessionState.GetString(Key, "[]")).OfType<JObject>().ToList();
            SessionState.SetString(Key, new JArray(jobs).ToString(Formatting.None));
            return jobs;
        }

        internal static bool Busy(Component c) =>
            Jobs.Any(j =>
                ((string)j["state"] == "queued" || (string)j["state"] == "running")
                && (
                    RuntimeTargets.TryGetValue((string)j["job_id"], out var runtime) && runtime.Surface == c
                    || (string)j["checkpoint"]?["target"] == GlobalObjectId.GetGlobalObjectIdSlow(c).ToString()
                )
            );

        private static void Pump()
        {
            JObject job = Jobs.FirstOrDefault(j => (string)j["state"] == "queued");
            if (job != null)
                Run(job);
        }

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
            {
                int expired = Jobs.FindIndex(j => (string)j["state"] != "queued" && (string)j["state"] != "running");
                if (expired < 0)
                    throw new InvalidOperationException("Navigation bake history is full of active reservations.");
                Jobs.RemoveAt(expired);
            }
            JObject job = new JObject
            {
                ["job_id"] = Guid.NewGuid().ToString("N"),
                ["state"] = "queued",
                ["scene_path"] = surface.gameObject.scene.path,
                ["agent_type_id"] = (int)NavigationAccess.Get(surface, "agentTypeID"),
                ["queued_utc"] = DateTime.UtcNow.ToString("O"),
                ["native_bake_interruptible"] = false,
                ["native_invoked"] = false,
                ["deadline_utc"] = DateTime.UtcNow.AddMinutes(10).ToString("O"),
                ["detail"] =
                    "Requested only. The queue waits up to ten minutes for native admission. Native BuildNavMesh is synchronous; no mid-call cancellation is possible.",
            };
            try
            {
                job["checkpoint"] = NavigationBakeTargets.Capture(surface);
            }
            catch (Exception ex)
            {
                job["resume_refusal"] = "Unstarted bake cannot safely resume after reload: " + ex.Message;
            }
            RuntimeTargets[(string)job["job_id"]] = (surface, EditorJsonUtility.ToJson(surface));
            Jobs.Add(job);
            Save();
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
            Component surface = null;
            NavigationBakeCheckpoint.Advance(
                job,
                DateTime.UtcNow,
                () =>
                {
                    NavigationObjects.RequireEditMode();
                    if (job["checkpoint"] is JObject checkpoint)
                        surface = NavigationBakeTargets.Resolve(checkpoint);
                    else if (RuntimeTargets.TryGetValue((string)job["job_id"], out var runtime))
                    {
                        surface = runtime.Surface;
                        if (surface == null || EditorJsonUtility.ToJson(surface) != runtime.Snapshot)
                            throw new InvalidOperationException("Surface configuration changed after request; no bake performed.");
                    }
                    else
                        throw new InvalidOperationException(
                            (string)job["resume_refusal"] ?? "Safe bake target is unavailable; inspect and request another bake explicitly."
                        );
                    ValidateSurface(surface);
                },
                () => Bake(job, surface),
                () =>
                {
                    return NativeOperationBarriers.TryGetBlocker("manage_navigation", new JObject { ["action"] = "bake" }, out var blocker)
                        ? blocker.Reason
                        : null;
                },
                Save
            );
            if ((string)job["state"] != "queued")
                RuntimeTargets.Remove((string)job["job_id"]);
        }

        private static void Bake(JObject job, Component surface)
        {
            NavMeshData previous = null;
            int group = -1;
            bool nativeEntered = false;
            try
            {
                previous = NavigationAccess.Get(surface, "navMeshData") as NavMeshData;
                Undo.IncrementCurrentGroup();
                group = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName("Bake Navigation Surface");
                Undo.RecordObject(surface, "Bake Navigation Surface");
                if (NativeOperationBarriers.TryGetBlocker("manage_navigation", new JObject { ["action"] = "bake" }, out var blocker))
                    throw new InvalidOperationException("Native navigation bake admission blocked before invocation: " + blocker.Reason);
                nativeEntered = true;
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
            catch (Exception)
            {
                if (nativeEntered && surface != null && group >= 0)
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
                throw;
            }
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
                RuntimeTargets.Remove((string)job["job_id"]);
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
            result.Remove("checkpoint");
            return result;
        }

        private static void Save() => SessionState.SetString(Key, new JArray(Jobs).ToString(Formatting.None));
    }
}
#endif
