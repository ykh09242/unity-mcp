using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.TimelineTools
{
    [McpForUnityTool("manage_timeline", AutoRegister = false, Group = "animation")]
    public static class ManageTimeline
    {
        public static object HandleCommand(JObject request) => HandleCommandCore(request, CancellationToken.None);

        public static Task<object> HandleCommand(JObject request, CancellationToken cancellationToken) =>
            Task.FromResult(HandleCommandCore(request, cancellationToken));

        private static object HandleCommandCore(JObject request, CancellationToken cancellationToken)
        {
            try
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (request == null || System.Text.Encoding.UTF8.GetByteCount(request.ToString(Newtonsoft.Json.Formatting.None)) > 65536)
                    throw new ArgumentException("Timeline request is required and must fit 64 KiB.");
                string action = TimelineAccess.Text(request, "action");
                if (action == "capabilities")
                    return new SuccessResponse("Timeline package capabilities.", Capabilities());
                TimelineAccess.Available();
                TimelineAccess.Stable();
                if (action == "seek")
                    return TimelinePreview.Seek(request, cancellationToken);
                if (action == "preview_stop")
                    return TimelinePreview.Stop(request);
                if (action == "read_director")
                    return new SuccessResponse(
                        "Exact director/binding state.",
                        TimelineAccess.DirectorRead(TimelineAccess.Director(request), request, cancellationToken)
                    );
                string path = TimelineAccess.Path(request);
                if (action == "create")
                    return Create(request, path, cancellationToken);
                var graph = new TimelineAccess.Graph(path, cancellationToken);
                if (action == "read")
                    return new SuccessResponse("Bounded Timeline graph.", graph.Read(request));
                graph.Guard(request);
                if (action == "preview_start")
                {
                    Component director = TimelineAccess.Director(request);
                    TimelineAccess.DirectorGuard(request, director, cancellationToken);
                    return TimelinePreview.Start(request, graph, director);
                }
                if (TimelinePreview.Active)
                    throw new ArgumentException("Stop the owned Timeline preview before authoring assets/director bindings.");
                if (action == "assign" || action == "bind")
                    return DirectorEdit(request, graph, action);
                JObject values = request["properties"] as JObject ?? throw new ArgumentException("Authoring requires a properties object.");
                if (action == "add_track")
                    return AddTrack(request, graph, values);
                if (action == "add_clip")
                    return AddClip(request, graph, values);
                throw new ArgumentException("Unknown Timeline action.");
            }
            catch (Exception error)
                when (error is ArgumentException
                    || error is InvalidOperationException
                    || error is IOException
                    || error is UnauthorizedAccessException
                    || error is OverflowException
                    || error is UnityException
                    || error is System.Reflection.TargetInvocationException
                )
            {
                return new ErrorResponse(error.GetBaseException().Message);
            }
        }

        private static void Keys(JObject values, params string[] allowed)
        {
            if (values.Properties().Any(value => !allowed.Contains(value.Name)))
                throw new ArgumentException("Unknown supplied Timeline property; unsupported values are not silently ignored.");
        }

        private static object Create(JObject request, string path, CancellationToken token)
        {
            if (TimelinePreview.Active)
                throw new ArgumentException("Stop the owned preview before asset creation.");
            JObject values = request["properties"] as JObject ?? new JObject();
            Keys(values, "name");
            string name = values["name"] == null ? System.IO.Path.GetFileNameWithoutExtension(path) : TimelineAccess.Text(values, "name");
            string parent = System.IO.Path.GetDirectoryName(path)?.Replace('\\', '/'),
                full = AssetPathUtility.GetFullAssetPath(path);
            if (
                !AssetDatabase.IsValidFolder(parent)
                || File.Exists(full)
                || File.Exists(full + ".meta")
                || Directory.Exists(full)
                || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets))
            )
                throw new ArgumentException("Create requires an existing Assets parent folder and an absent asset/metadata destination; no overwrite.");
            var asset = ScriptableObject.CreateInstance(TimelineAccess.TimelineType);
            asset.name = name;
            try
            {
                token.ThrowIfCancellationRequested();
                AssetDatabase.CreateAsset(asset, path);
            }
            catch
            {
                if (!EditorUtility.IsPersistent(asset))
                    Object.DestroyImmediate(asset);
                throw;
            }
            var graph = new TimelineAccess.Graph(path);
            JObject data = graph.Read(new JObject());
            data["asset_creation_undo_supported"] = false;
            return new SuccessResponse("Timeline asset created; subsequent track/clip/binding edits use native Undo.", data);
        }

        private static int Begin(string label, params Object[] values)
        {
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName(label);
            Undo.RegisterCompleteObjectUndo(values, label);
            return group;
        }

        private static object AddTrack(JObject request, TimelineAccess.Graph graph, JObject values)
        {
            Keys(values, "kind", "name");
            string kind = TimelineAccess.Text(values, "kind"),
                name = TimelineAccess.Text(values, "name");
            if (kind != "animation" && kind != "audio")
                throw new ArgumentException("Track kind must be animation or audio; arbitrary/custom tracks are unsupported.");
            if (graph.Tracks.Any(track => track.name == name))
                throw new ArgumentException("Duplicate exact track name; no native automatic renaming.");
            graph.Guard(request);
            int group = Begin("Add Timeline track", graph.Asset);
            try
            {
                graph.Token.ThrowIfCancellationRequested();
                var track = TimelineAccess.Call(graph.Asset, "CreateTrack", TimelineAccess.KindType(kind), null, name) as ScriptableObject;
                if (track == null || track.GetType() != TimelineAccess.KindType(kind))
                    throw new InvalidOperationException("Native Timeline did not create the requested track kind.");
                EditorUtility.SetDirty(graph.Asset);
                EditorUtility.SetDirty(track);
                AssetDatabase.SaveAssetIfDirty(graph.Asset);
                Undo.CollapseUndoOperations(group);
                JObject data = new TimelineAccess.Graph(graph.Path).Read(new JObject());
                data["created_track"] = TimelineAccess.TrackRow(track);
                data["undo_group"] = group;
                return new SuccessResponse("Native Timeline track authored with Undo.", data);
            }
            catch
            {
                Undo.RevertAllDownToGroup(group);
                throw;
            }
            finally
            {
                Undo.IncrementCurrentGroup();
            }
        }

        private static object AddClip(JObject request, TimelineAccess.Graph graph, JObject values)
        {
            Keys(values, "track_id", "source", "start", "duration", "clip_in", "time_scale", "display_name");
            ScriptableObject track = graph.Select(TimelineAccess.Text(values, "track_id"));
            string kind = TimelineAccess.Kind(track);
            Object source = TimelineAccess.Resolve(values["source"], graph.Token);
            if (
                (kind != "animation" && kind != "audio")
                || (kind == "animation" && (!(source is AnimationClip animation) || animation.legacy))
                || (kind == "audio" && !(source is AudioClip))
            )
                throw new ArgumentException("Exact source type must match a built-in animation/audio track; legacy/custom playable clips are unsupported.");
            double start = TimelineAccess.Number(values["start"], "start", 0, 86400),
                duration = TimelineAccess.Number(values["duration"], "duration", .001, 86400);
            double clipIn = values["clip_in"] == null ? 0 : TimelineAccess.Number(values["clip_in"], "clip_in", 0, 86400);
            double speed = values["time_scale"] == null ? 1 : TimelineAccess.Number(values["time_scale"], "time_scale", .001, 8);
            double length = source is AnimationClip motion ? motion.length : ((AudioClip)source).length;
            if (start + duration > 86400 || clipIn + duration * speed > length + .00001)
                throw new ArgumentException("Clip range must fit the source duration and86400-second Timeline bound; implicit looping is unsupported.");
            string display = values["display_name"] == null ? source.name : TimelineAccess.Text(values, "display_name");
            graph.Guard(request);
            int group = Begin("Add Timeline clip", graph.Asset, track);
            try
            {
                graph.Token.ThrowIfCancellationRequested();
                object clip = TimelineAccess.Call(track, "CreateDefaultClip");
                Object playable = TimelineAccess.Get(clip, "asset") as Object;
                string expected = "UnityEngine.Timeline." + (kind == "animation" ? "AnimationPlayableAsset" : "AudioPlayableAsset");
                if (playable == null || playable.GetType().FullName != expected)
                    throw new InvalidOperationException("Native default clip did not create the exact supported playable type.");
                TimelineAccess.Set(playable, "clip", source);
                TimelineAccess.Set(clip, "start", start);
                TimelineAccess.Set(clip, "duration", duration);
                TimelineAccess.Set(clip, "clipIn", clipIn);
                TimelineAccess.Set(clip, "timeScale", speed);
                TimelineAccess.Set(clip, "displayName", display);
                EditorUtility.SetDirty(playable);
                EditorUtility.SetDirty(track);
                EditorUtility.SetDirty(graph.Asset);
                AssetDatabase.SaveAssetIfDirty(graph.Asset);
                Undo.CollapseUndoOperations(group);
                JObject data = new TimelineAccess.Graph(graph.Path).Read(new JObject());
                data["created_clip"] = TimelineAccess.ClipRow(clip);
                data["undo_group"] = group;
                return new SuccessResponse("Native source clip wrapper authored with Undo; source identity retained.", data);
            }
            catch
            {
                Undo.RevertAllDownToGroup(group);
                throw;
            }
            finally
            {
                Undo.IncrementCurrentGroup();
            }
        }

        private static object DirectorEdit(JObject request, TimelineAccess.Graph graph, string action)
        {
            Component director = TimelineAccess.Director(request);
            TimelineAccess.DirectorGuard(request, director, graph.Token);
            if (TimelineAccess.GraphValid(director) || TimelineAccess.Get(director, "state").ToString() == "Playing")
                throw new ArgumentException("Director authoring requires an idle director without an existing graph.");
            ScriptableObject track = null;
            Component binding = null;
            if (action == "bind")
            {
                if (TimelineAccess.Get(director, "playableAsset") as Object != graph.Asset)
                    throw new ArgumentException("Director must already reference the exact Timeline before binding.");
                JObject values = request["properties"] as JObject ?? throw new ArgumentException("bind requires properties.");
                Keys(values, "track_id", "binding_target");
                track = graph.Select(TimelineAccess.Text(values, "track_id"));
                int target = TimelineAccess.Whole(values, "binding_target", 0, int.MinValue, int.MaxValue);
                binding = MCPForUnity.Runtime.Helpers.UnityObjectIdCompat.InstanceIDToObjectCompat(target) as Component;
                string kind = TimelineAccess.Kind(track);
                if (
                    binding == null
                    || binding.gameObject.scene != director.gameObject.scene
                    || (
                        kind == "animation" ? !(binding is Animator)
                        : kind == "audio" ? !(binding is AudioSource)
                        : true
                    )
                )
                    throw new ArgumentException("Binding must be the exact matching Animator/AudioSource component in the selected director scene.");
            }
            graph.Guard(request);
            TimelineAccess.DirectorGuard(request, director, graph.Token);
            graph.Token.ThrowIfCancellationRequested();
            int group = Begin("Author Timeline director binding", director);
            try
            {
                graph.Token.ThrowIfCancellationRequested();
                if (action == "assign")
                    TimelineAccess.Set(director, "playableAsset", graph.Asset);
                else
                    TimelineAccess.Call(director, "SetGenericBinding", track, binding);
                EditorUtility.SetDirty(director);
                EditorSceneManager.MarkSceneDirty(director.gameObject.scene);
                Undo.CollapseUndoOperations(group);
                JObject data = TimelineAccess.DirectorRead(director);
                data["undo_group"] = group;
                return new SuccessResponse("Director authoring recorded with native Undo; save the scene explicitly.", data);
            }
            catch
            {
                Undo.RevertAllDownToGroup(group);
                throw;
            }
            finally
            {
                Undo.IncrementCurrentGroup();
            }
        }

        private static object Capabilities()
        {
            Type type = TimelineAccess.TimelineType;
            string version = null;
            if (type != null)
            {
                var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(type.Assembly);
                version = package?.version;
            }
            return new
            {
                package_available = type != null,
                package_name = "com.unity.timeline",
                package_version = version,
                supported_track_kinds = new[] { "animation", "audio" },
                preview = "owned manual native director evaluation; no realtime playback or audibility proof",
                preview_idle_expiry_seconds = 120,
                preview_available = type != null,
                native_undo_scope = "track/clip/director binding authoring; asset creation excluded",
                custom_tracks_supported = false,
                read_page_tracks = 32,
                read_page_clips_per_track = 32,
                read_page_bytes = 262144,
                large_graph_paging = true,
                preview_transform_component_limit = 128,
            };
        }
    }
}
