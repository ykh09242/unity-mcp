using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.TimelineTools
{
    internal static class TimelineAccess
    {
        internal static Type TimelineType =>
            AppDomain.CurrentDomain.GetAssemblies().Select(x => x.GetType("UnityEngine.Timeline.TimelineAsset")).FirstOrDefault(x => x != null);

        internal static Type KindType(string kind) =>
            TimelineType?.Assembly.GetType(
                "UnityEngine.Timeline."
                    + (
                        kind == "animation" ? "AnimationTrack"
                        : kind == "audio" ? "AudioTrack"
                        : ""
                    )
            );

        internal static void Available()
        {
            if (TimelineType == null || KindType("animation") == null || KindType("audio") == null)
                throw new ArgumentException("Optional com.unity.timeline animation/audio APIs are unavailable; no package was installed implicitly.");
        }

        internal static void Stable()
        {
            if (
                EditorApplication.isPlayingOrWillChangePlaymode
                || EditorApplication.isCompiling
                || EditorApplication.isUpdating
                || BuildPipeline.isBuildingPlayer
            )
                throw new ArgumentException("Timeline authoring/preview requires stable Edit Mode outside a Player build.");
        }

        internal static object Get(object target, string name)
        {
            PropertyInfo property = target?.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
            return property != null && property.CanRead
                ? property.GetValue(target)
                : throw new ArgumentException("Timeline API property is unavailable: " + name + ".");
        }

        internal static void Set(object target, string name, object value)
        {
            PropertyInfo property = target?.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
            if (property == null || !property.CanWrite)
                throw new ArgumentException("Timeline API setter is unavailable: " + name + ".");
            property.SetValue(target, value);
        }

        internal static object Call(object target, string name, params object[] args)
        {
            MethodInfo method = target
                .GetType()
                .GetMethods(BindingFlags.Public | BindingFlags.Instance)
                .SingleOrDefault(x =>
                    x.Name == name
                    && !x.IsGenericMethodDefinition
                    && x.GetParameters().Length == args.Length
                    && x.GetParameters().Select((parameter, index) => args[index] == null || parameter.ParameterType.IsInstanceOfType(args[index])).All(x => x)
                );
            return method != null ? method.Invoke(target, args) : throw new ArgumentException("Timeline API method is unavailable or ambiguous: " + name + ".");
        }

        internal static string Text(JObject values, string key)
        {
            if (values[key]?.Type != JTokenType.String || string.IsNullOrWhiteSpace(values[key].Value<string>()) || values[key].Value<string>().Length > 1024)
                throw new ArgumentException(key + " requires a nonempty string of at most 1024 characters.");
            return values[key].Value<string>();
        }

        internal static double Number(JToken value, string key, double min, double max)
        {
            if (value == null || (value.Type != JTokenType.Integer && value.Type != JTokenType.Float))
                throw new ArgumentException(key + " requires a finite number without bool/string coercion.");
            double number = value.Value<double>();
            if (double.IsNaN(number) || double.IsInfinity(number) || number < min || number > max)
                throw new ArgumentException(key + " is outside the finite supported range.");
            return number;
        }

        internal static int Whole(JObject values, string key, int fallback, int min, int max)
        {
            if (values[key] == null)
                return fallback;
            if (values[key].Type != JTokenType.Integer || !int.TryParse(values[key].ToString(), out int number) || number < min || number > max)
                throw new ArgumentException(key + " requires an integer between " + min + " and " + max + ".");
            return number;
        }

        internal static string Path(JObject request)
        {
            string path = AssetPathUtility.GetContainedAssetPath(Text(request, "path"));
            if (string.IsNullOrEmpty(path) || !path.EndsWith(".playable", StringComparison.Ordinal))
                throw new ArgumentException("Timeline path must be contained Assets/*.playable.");
            AssetPathUtility.GetFullAssetPath(path);
            return path;
        }

        internal static JObject Reference(Object value)
        {
            if (value == null || !AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long id) || id == 0)
                throw new ArgumentException("An exact saved asset GUID/fileID is required.");
            return new JObject { ["guid"] = guid, ["file_id"] = id.ToString(CultureInfo.InvariantCulture) };
        }

        internal static string Id(Object value) => Reference(value).Value<string>("file_id");

        internal static Object Resolve(JToken value)
        {
            if (!(value is JObject reference) || reference.Count != 2)
                throw new ArgumentException("source requires exact {guid,file_id}; names/paths are not asset selectors.");
            string guid = Text(reference, "guid"),
                id = Text(reference, "file_id");
            string path = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrEmpty(path) || AssetPathUtility.GetContainedAssetPath(path) == null)
                throw new ArgumentException("Source GUID must identify an existing contained Assets asset.");
            AssetPathUtility.GetFullAssetPath(path);
            Object[] assets = AssetDatabase.LoadAllAssetsAtPath(path);
            if (assets.Length > 4096)
                throw new ArgumentException("Source asset exceeds 4096 subassets.");
            Object result = assets.SingleOrDefault(x => Id(x) == id);
            return result != null ? result : throw new ArgumentException("Source fileID is stale or mismatched.");
        }

        internal sealed class Graph
        {
            internal readonly ScriptableObject Asset;
            internal readonly string Path;
            internal readonly List<ScriptableObject> Tracks = new List<ScriptableObject>();
            internal int ClipCount;

            internal Graph(string path)
            {
                Available();
                Path = path;
                Asset = AssetDatabase.LoadAssetAtPath(path, TimelineType) as ScriptableObject;
                if (Asset == null)
                    throw new ArgumentException("No saved TimelineAsset exists at path.");
                var seen = new HashSet<Object>();
                Visit((IEnumerable)Call(Asset, "GetRootTracks"), seen, 0);
            }

            private void Visit(IEnumerable roots, HashSet<Object> seen, int depth)
            {
                foreach (ScriptableObject track in roots)
                {
                    if (track == null || !seen.Add(track) || depth > 8 || Tracks.Count >= 64)
                        throw new ArgumentException("Timeline graph exceeds 64 unique tracks or depth8.");
                    Tracks.Add(track);
                    int count = Clips(track).Length;
                    ClipCount += count;
                    if (ClipCount > 256)
                        throw new ArgumentException("Timeline graph exceeds 256 clips.");
                    Visit((IEnumerable)Call(track, "GetChildTracks"), seen, depth + 1);
                }
            }

            internal ScriptableObject Select(string id) =>
                Tracks.SingleOrDefault(x => Id(x) == id) ?? throw new ArgumentException("Exact track_id is stale or does not belong to this Timeline.");

            internal string Revision()
            {
                string full = AssetPathUtility.GetFullAssetPath(Path);
                if (!File.Exists(full) || !File.Exists(full + ".meta") || new FileInfo(full).Length > 8388608 || new FileInfo(full + ".meta").Length > 1048576)
                    throw new ArgumentException("Timeline source/metadata must fit 8 MiB/1 MiB.");
                Object[] owned = AssetDatabase.LoadAllAssetsAtPath(Path);
                if (owned.Length > 512)
                    throw new ArgumentException("Timeline exceeds 512 owned subassets.");
                var sources = Tracks.SelectMany(track => Clips(track)).Select(clip => Source(clip)).Where(x => x != null).Distinct();
                var state = new StringBuilder(Reference(Asset).ToString(Formatting.None) + AssetDatabase.GetAssetDependencyHash(Path));
                foreach (Object value in owned.Concat(sources).Distinct().OrderBy(x => AssetDatabase.GetAssetPath(x) + ":" + Id(x), StringComparer.Ordinal))
                {
                    state.Append(Reference(value).ToString(Formatting.None)).Append(EditorJsonUtility.ToJson(value));
                    if (state.Length > 2097152)
                        throw new ArgumentException("Timeline/source serialized state exceeds 2 MiB.");
                }
                using (var sha = SHA256.Create())
                {
                    state.Append(Convert.ToBase64String(sha.ComputeHash(File.ReadAllBytes(full))));
                    state.Append(Convert.ToBase64String(sha.ComputeHash(File.ReadAllBytes(full + ".meta"))));
                    return Convert.ToBase64String(sha.ComputeHash(Encoding.UTF8.GetBytes(state.ToString())));
                }
            }

            internal void Guard(JObject request)
            {
                if (
                    Text(request, "guid") != Reference(Asset).Value<string>("guid")
                    || Text(request, "file_id") != Id(Asset)
                    || Text(request, "expected_revision") != Revision()
                )
                    throw new ArgumentException("Timeline identity/source revision drifted; read again before authoring or preview.");
            }

            internal JObject Read(JObject request)
            {
                int offset = Whole(request, "offset", 0, 0, Tracks.Count),
                    size = Whole(request, "page_size", 16, 1, 32);
                string revision = Revision();
                if (offset > 0 && Text(request, "expected_revision") != revision)
                    throw new ArgumentException("Timeline continuation revision drifted; restart the read.");
                var result = new JObject
                {
                    ["path"] = Path,
                    ["reference"] = Reference(Asset),
                    ["revision"] = revision,
                    ["duration"] = Convert.ToDouble(Get(Asset, "duration"), CultureInfo.InvariantCulture),
                    ["track_count"] = Tracks.Count,
                    ["clip_count"] = ClipCount,
                    ["tracks"] = new JArray(Tracks.Skip(offset).Take(size).Select(TrackRow)),
                    ["next_offset"] = offset + size < Tracks.Count ? new JValue(offset + size) : JValue.CreateNull(),
                };
                if (Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)) > 262144)
                    throw new ArgumentException("Timeline page exceeds 256 KiB; reduce page_size.");
                return result;
            }
        }

        internal static string Kind(Object track) =>
            track.GetType() == KindType("animation") ? "animation"
            : track.GetType() == KindType("audio") ? "audio"
            : "unsupported";

        internal static object[] Clips(Object track)
        {
            var result = new List<object>();
            foreach (object clip in (IEnumerable)Call(track, "GetClips"))
            {
                if (result.Count >= 64)
                    throw new ArgumentException("One Timeline track exceeds 64 clips.");
                result.Add(clip);
            }
            return result.ToArray();
        }

        internal static Object Source(object clip)
        {
            var playable = Get(clip, "asset") as Object;
            PropertyInfo source = playable?.GetType().GetProperty("clip");
            return source?.GetValue(playable) as Object;
        }

        internal static JObject ClipRow(object clip)
        {
            var playable = Get(clip, "asset") as Object;
            Object source = Source(clip);
            return new JObject
            {
                ["clip_id"] = playable != null ? Id(playable) : null,
                ["display_name"] = (string)Get(clip, "displayName"),
                ["start"] = (double)Get(clip, "start"),
                ["duration"] = (double)Get(clip, "duration"),
                ["clip_in"] = (double)Get(clip, "clipIn"),
                ["time_scale"] = (double)Get(clip, "timeScale"),
                ["playable_type"] = playable?.GetType().FullName,
                ["source"] = source != null ? Reference(source) : (JToken)JValue.CreateNull(),
            };
        }

        internal static JObject TrackRow(ScriptableObject track) =>
            new JObject
            {
                ["track_id"] = Id(track),
                ["name"] = track.name,
                ["kind"] = Kind(track),
                ["type"] = track.GetType().FullName,
                ["muted"] = (bool)Get(track, "muted"),
                ["clips"] = new JArray(Clips(track).Select(ClipRow)),
            };

        internal static JValue SceneHandle(Scene scene)
        {
#if UNITY_6000_4_OR_NEWER
            return new JValue(scene.handle.GetRawData());
#else
            return new JValue((int)scene.handle);
#endif
        }

        internal static Component Director(JObject request)
        {
            int target = Whole(request, "target", 0, int.MinValue, int.MaxValue);
            var director = UnityObjectIdCompat.InstanceIDToObjectCompat(target) as Component;
            if (
                director == null
                || director.GetType().FullName != "UnityEngine.Playables.PlayableDirector"
                || !director.gameObject.scene.IsValid()
                || !director.gameObject.scene.isLoaded
                || EditorSceneManager.IsPreviewScene(director.gameObject.scene)
            )
                throw new ArgumentException("target requires an exact loaded main-scene PlayableDirector component instance ID.");
            if (
                request["scene_handle"]?.Type != JTokenType.Integer
                || request["scene_handle"].ToString(Formatting.None) != SceneHandle(director.gameObject.scene).ToString(Formatting.None)
            )
                throw new ArgumentException("scene_handle must exactly identify the director's loaded scene.");
            return director;
        }

        internal static JObject SceneReference(Object value)
        {
            if (!(value is Component component) || !component.gameObject.scene.IsValid())
                return null;
            return new JObject
            {
                ["instance_id"] = component.GetInstanceIDCompat(),
                ["scene_handle"] = SceneHandle(component.gameObject.scene),
                ["name"] = component.name,
                ["type"] = component.GetType().FullName,
            };
        }

        internal static bool GraphValid(Component director)
        {
            object graph = Get(director, "playableGraph");
            return (bool)Call(graph, "IsValid");
        }

        internal static JObject DirectorRead(Component director)
        {
            Object asset = Get(director, "playableAsset") as Object;
            var bindings = new JArray();
            if (asset != null && TimelineType.IsInstanceOfType(asset))
            {
                var graph = new Graph(AssetDatabase.GetAssetPath(asset));
                foreach (var track in graph.Tracks)
                    bindings.Add(
                        new JObject
                        {
                            ["track_id"] = Id(track),
                            ["kind"] = Kind(track),
                            ["binding"] = (JToken)SceneReference(Call(director, "GetGenericBinding", track) as Object) ?? JValue.CreateNull(),
                        }
                    );
            }
            var data = new JObject
            {
                ["director"] = SceneReference(director),
                ["asset"] = asset != null ? Reference(asset) : (JToken)JValue.CreateNull(),
                ["time"] = (double)Get(director, "time"),
                ["state"] = Get(director, "state").ToString(),
                ["graph_valid"] = GraphValid(director),
                ["bindings"] = bindings,
            };
            using (var sha = SHA256.Create())
                data["director_revision"] = Convert.ToBase64String(
                    sha.ComputeHash(Encoding.UTF8.GetBytes(EditorJsonUtility.ToJson(director) + data.ToString(Formatting.None)))
                );
            return data;
        }

        internal static void DirectorGuard(JObject request, Component director)
        {
            if (Text(request, "expected_director_revision") != DirectorRead(director).Value<string>("director_revision"))
                throw new ArgumentException("Director/binding/time revision drifted; read_director again.");
        }
    }
}
