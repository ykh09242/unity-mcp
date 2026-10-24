using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
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
        internal static Action RevisionChunkForTests;
        internal static Action DirectorGuardCompletedForTests;
        internal static Action DurationForTests;

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

        internal static Object Resolve(JToken value, CancellationToken token = default)
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
            Object result = assets.SingleOrDefault(x =>
            {
                token.ThrowIfCancellationRequested();
                return Id(x) == id;
            });
            return result != null ? result : throw new ArgumentException("Source fileID is stale or mismatched.");
        }

        internal sealed class Graph
        {
            internal readonly ScriptableObject Asset;
            internal readonly string Path;
            internal readonly CancellationToken Token;
            internal IEnumerable<ScriptableObject> Tracks => Walk();
            internal int ClipCount => Tracks.Sum(track => Clips(track, Token).Count());

            internal Graph(string path, CancellationToken token = default)
            {
                token.ThrowIfCancellationRequested();
                Available();
                Path = path;
                Token = token;
                Asset = AssetDatabase.LoadAssetAtPath(path, TimelineType) as ScriptableObject;
                if (Asset == null)
                    throw new ArgumentException("No saved TimelineAsset exists at path.");
                // Validate live serialized edges before dependency hashing, native serialization or
                // duration/output getters, which can recursively enter Timeline's unguarded caches.
                ValidateStructure();
            }

            private Graph(Graph source)
            {
                Asset = source.Asset;
                Path = source.Path;
                Token = CancellationToken.None;
            }

            internal Graph DetachRequestToken() => new Graph(this);

            internal void ValidateStructure()
            {
                // Complete the raw preflight before touching a native hierarchy cache.
                var raw = new HashSet<ScriptableObject>(Tracks);
                if (!Tracks.SequenceEqual(Walk(true)))
                    throw new ArgumentException("Timeline native hierarchy cache drifted; reload the asset before continuing.");
                ValidateCachedMembers(raw, "m_CacheFlattenedTracks", "flattened", true);
                ValidateCachedMembers(raw, "m_CacheOutputTracks", "output", false);
            }

            private void ValidateCachedMembers(HashSet<ScriptableObject> raw, string field, string label, bool complete)
            {
                object cache = SerializedField(Asset, TimelineType, field);
                if (cache == null)
                    return;
                if (!(cache is IEnumerable values))
                    throw new ArgumentException("Timeline native " + label + " hierarchy cache is malformed.");
                var seen = new HashSet<ScriptableObject>();
                foreach (object value in values)
                {
                    Token.ThrowIfCancellationRequested();
                    if (!(value is ScriptableObject track) || !raw.Contains(track) || !seen.Add(track))
                        throw new ArgumentException("Timeline native " + label + " hierarchy cache drifted; reload the asset before continuing.");
                }
                // Native flattened order differs from DFS. Membership and uniqueness are the contract.
                if (complete && seen.Count != raw.Count)
                    throw new ArgumentException("Timeline native " + label + " hierarchy cache drifted; reload the asset before continuing.");
            }

            internal double Duration()
            {
                ValidateStructure();
                DurationForTests?.Invoke();
                Token.ThrowIfCancellationRequested();
                return Convert.ToDouble(Get(Asset, "duration"), CultureInfo.InvariantCulture);
            }

            private static object SerializedField(Object owner, Type declaringType, string field)
            {
                FieldInfo member = declaringType?.GetField(field, BindingFlags.Instance | BindingFlags.NonPublic);
                if (member == null)
                    throw new ArgumentException("Timeline serialized hierarchy API is unavailable: " + field + ".");
                return member.GetValue(owner);
            }

            private static IEnumerable Children(Object owner, Type declaringType, string field)
            {
                object value = SerializedField(owner, declaringType, field);
                return value == null
                    ? Array.Empty<ScriptableObject>()
                    : value as IEnumerable ?? throw new ArgumentException("Timeline hierarchy is malformed.");
            }

            private IEnumerable Roots()
            {
                var marker = Get(Asset, "markerTrack") as ScriptableObject;
                if (marker != null)
                    yield return marker;
                foreach (object track in Children(Asset, TimelineType, "m_Tracks"))
                    yield return track;
            }

            private IEnumerable<ScriptableObject> Walk(bool cached = false)
            {
                var seen = new HashSet<Object>();
                Type trackType = TimelineType.Assembly.GetType("UnityEngine.Timeline.TrackAsset");
                var stack = new Stack<KeyValuePair<Object, IEnumerator>>();
                IEnumerable roots = cached ? (IEnumerable)Call(Asset, "GetRootTracks") : Roots();
                stack.Push(new KeyValuePair<Object, IEnumerator>(Asset, roots.GetEnumerator()));
                try
                {
                    while (stack.Count != 0)
                    {
                        Token.ThrowIfCancellationRequested();
                        IEnumerator cursor = stack.Peek().Value;
                        if (!cursor.MoveNext())
                        {
                            (stack.Pop().Value as IDisposable)?.Dispose();
                            continue;
                        }
                        var track = cursor.Current as ScriptableObject;
                        if (track == null || !seen.Add(track))
                            throw new ArgumentException("Timeline graph contains a missing, repeated or cyclic track.");
                        if (AssetDatabase.GetAssetPath(track) != Path)
                            throw new ArgumentException("Timeline graph contains a track owned by another asset.");
                        if ((SerializedField(track, trackType, "m_Parent") as Object) != stack.Peek().Key)
                            throw new ArgumentException("Timeline graph contains a mismatched or cyclic track parent.");
                        yield return track;
                        // GetChildTracks caches a copied list. Inspect serialized edges directly so
                        // stale native caches cannot hide a newly introduced cycle or foreign edge.
                        IEnumerable children = cached ? (IEnumerable)Call(track, "GetChildTracks") : Children(track, trackType, "m_Children");
                        stack.Push(new KeyValuePair<Object, IEnumerator>(track, children.GetEnumerator()));
                    }
                }
                finally
                {
                    while (stack.Count != 0)
                        (stack.Pop().Value as IDisposable)?.Dispose();
                }
            }

            internal ScriptableObject Select(string id) =>
                Tracks.SingleOrDefault(x => Id(x) == id) ?? throw new ArgumentException("Exact track_id is stale or does not belong to this Timeline.");

            internal string Revision() => Revision(out _, out _);

            private string Revision(out int trackCount, out int clipCount)
            {
                ValidateStructure();
                string full = AssetPathUtility.GetFullAssetPath(Path);
                trackCount = clipCount = 0;
                using (var state = new RevisionHash(Token))
                {
                    state.Add("timeline-revision-v2");
                    state.Add(AssetDatabase.GetAssetDependencyHash(Path).ToString());
                    state.File(full);
                    state.File(full + ".meta");
                    var observed = new HashSet<Object>();
                    Action<Object> observe = value =>
                    {
                        Token.ThrowIfCancellationRequested();
                        if (value != null && observed.Add(value))
                            state.Object(value);
                    };
                    observe(Asset);
                    foreach (ScriptableObject track in Tracks)
                    {
                        trackCount = checked(trackCount + 1);
                        observe(track);
                        foreach (object clip in Clips(track, Token))
                        {
                            clipCount = checked(clipCount + 1);
                            Object playable = Get(clip, "asset") as Object;
                            if (playable != null && AssetDatabase.GetAssetPath(playable) != Path)
                                throw new ArgumentException("Timeline clip playable is owned by another asset.");
                            observe(playable);
                            observe(Source(clip));
                        }
                    }
                    // Include marker, infinite-clip and custom owned state, including unsaved edits.
                    // Unity exposes subasset enumeration as an array; never duplicate its serialized payload.
                    foreach (Object value in AssetDatabase.LoadAllAssetsAtPath(Path))
                        observe(value);
                    ValidateStructure();
                    return state.Finish();
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
                int offset = Whole(request, "offset", 0, 0, int.MaxValue),
                    size = Whole(request, "page_size", 16, 1, 32),
                    clipOffset = Whole(request, "clip_offset", 0, 0, int.MaxValue),
                    clipSize = Whole(request, "clip_page_size", 16, 1, 32);
                string selected = request["track_id"] == null ? null : Text(request, "track_id");
                if ((selected != null && offset != 0) || (clipOffset != 0 && selected == null))
                    throw new ArgumentException("Clip continuation requires track_id and does not accept a track offset.");
                // Native duration normalizes serialized clip extrapolation fields. Hash afterwards
                // so the first returned revision describes the state subsequent pages will observe.
                double duration = Duration();
                string revision = Revision(out int trackCount, out int clipCount);
                if ((offset > 0 || clipOffset > 0 || request["expected_revision"] != null) && Text(request, "expected_revision") != revision)
                    throw new ArgumentException("Timeline continuation revision drifted; restart the read.");
                if (offset > trackCount)
                    throw new ArgumentException("Timeline track offset exceeds the current revision's track count.");
                var rows = new JArray();
                var result = new JObject
                {
                    ["path"] = Path,
                    ["reference"] = Reference(Asset),
                    ["revision"] = revision,
                    ["duration"] = duration,
                    ["track_count"] = trackCount,
                    ["clip_count"] = clipCount,
                    ["tracks"] = rows,
                    ["next_offset"] = JValue.CreateNull(),
                };
                IEnumerable<ScriptableObject> page = selected == null ? Tracks.Skip(offset).Take(size) : new[] { Select(selected) };
                foreach (ScriptableObject track in page)
                {
                    Token.ThrowIfCancellationRequested();
                    JObject row = TrackRow(track, clipOffset, clipSize, Token);
                    rows.Add(row);
                    if (Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)) > 245760 && rows.Count > 1)
                    {
                        rows.RemoveAt(rows.Count - 1);
                        break;
                    }
                }
                if (selected == null && offset + rows.Count < trackCount)
                    result["next_offset"] = offset + rows.Count;
                return result;
            }
        }

        // Hash file bytes and native serialized properties incrementally. No whole-file read or
        // graph-sized JSON string is needed, even when one serialized object contains a large array.
        private sealed class RevisionHash : IDisposable
        {
            private readonly CancellationToken token;
            private readonly SHA256 hash = SHA256.Create();
            private readonly CryptoStream stream;
            private readonly StreamWriter writer;
            private static readonly PropertyInfo ContentHash = typeof(SerializedProperty).GetProperty("contentHash");

            internal RevisionHash(CancellationToken token)
            {
                this.token = token;
                stream = new CryptoStream(Stream.Null, hash, CryptoStreamMode.Write, true);
                writer = new StreamWriter(stream, new UTF8Encoding(false), 4096, true);
            }

            internal void Add(string value)
            {
                token.ThrowIfCancellationRequested();
                value = value ?? "";
                writer.Write(value.Length.ToString(CultureInfo.InvariantCulture));
                writer.Write(':');
                writer.Write(value);
                writer.Write(';');
            }

            internal void File(string path)
            {
                token.ThrowIfCancellationRequested();
                using (var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 65536))
                {
                    Add(input.Length.ToString(CultureInfo.InvariantCulture));
                    writer.Flush();
                    var buffer = new byte[65536];
                    int read;
                    while ((read = input.Read(buffer, 0, buffer.Length)) != 0)
                    {
                        token.ThrowIfCancellationRequested();
                        stream.Write(buffer, 0, read);
                        RevisionChunkForTests?.Invoke();
                        token.ThrowIfCancellationRequested();
                    }
                }
            }

            internal void Object(Object value)
            {
                Add(
                    EditorUtility.IsPersistent(value)
                        ? Reference(value).ToString(Formatting.None)
                        : value.GetInstanceIDCompat().ToString(CultureInfo.InvariantCulture)
                );
                using (var serialized = new SerializedObject(value))
                using (SerializedProperty property = serialized.GetIterator())
                {
                    var managedReferences = new HashSet<long>();
                    bool enter = true;
                    while (property.Next(enter))
                    {
                        token.ThrowIfCancellationRequested();
                        Add(property.propertyPath);
                        Add(property.type);
                        if (ContentHash != null)
                        {
                            Add(ContentHash.GetValue(property).ToString());
                            enter = false;
                        }
                        else
                        {
                            // Older Editors lack contentHash. Walk leaves instead of serializing arrays.
                            enter = property.propertyType != SerializedPropertyType.ManagedReference || managedReferences.Add(property.managedReferenceId);
                            Add(PropertyValue(property));
                        }
                    }
                }
            }

            private string PropertyValue(SerializedProperty property)
            {
                switch (property.propertyType)
                {
                    case SerializedPropertyType.Generic:
                        return property.isArray ? property.arraySize.ToString(CultureInfo.InvariantCulture) : "container";
                    case SerializedPropertyType.Integer:
                    case SerializedPropertyType.ArraySize:
                    case SerializedPropertyType.Character:
                    case SerializedPropertyType.LayerMask:
                    case SerializedPropertyType.Enum:
                        return property.longValue.ToString(CultureInfo.InvariantCulture);
                    case SerializedPropertyType.FixedBufferSize:
                        return property.fixedBufferSize.ToString(CultureInfo.InvariantCulture);
                    case SerializedPropertyType.Boolean:
                        return property.boolValue ? "true" : "false";
                    case SerializedPropertyType.Float:
                        return property.doubleValue.ToString("R", CultureInfo.InvariantCulture);
                    case SerializedPropertyType.String:
                        return property.stringValue;
                    case SerializedPropertyType.ObjectReference:
                        return property.objectReferenceInstanceIDValue.ToString(CultureInfo.InvariantCulture);
                    case SerializedPropertyType.ExposedReference:
                        return property.exposedReferenceValue == null
                            ? "null"
                            : property.exposedReferenceValue.GetInstanceIDCompat().ToString(CultureInfo.InvariantCulture);
                    case SerializedPropertyType.Color:
                        return property.colorValue.ToString("R");
                    case SerializedPropertyType.Vector2:
                        return property.vector2Value.ToString("R");
                    case SerializedPropertyType.Vector3:
                        return property.vector3Value.ToString("R");
                    case SerializedPropertyType.Vector4:
                        return property.vector4Value.ToString("R");
                    case SerializedPropertyType.Rect:
                        return property.rectValue.ToString("R");
                    case SerializedPropertyType.Bounds:
                        return property.boundsValue.ToString("R");
                    case SerializedPropertyType.Quaternion:
                        return property.quaternionValue.ToString("R");
                    case SerializedPropertyType.Vector2Int:
                        return property.vector2IntValue.ToString();
                    case SerializedPropertyType.Vector3Int:
                        return property.vector3IntValue.ToString();
                    case SerializedPropertyType.RectInt:
                        return property.rectIntValue.ToString();
                    case SerializedPropertyType.BoundsInt:
                        return property.boundsIntValue.ToString();
                    case SerializedPropertyType.AnimationCurve:
                        AnimationCurve curve = property.animationCurveValue;
                        Add(curve.preWrapMode + ":" + curve.postWrapMode + ":" + curve.length);
                        for (int index = 0; index < curve.length; index++)
                            Add(JsonConvert.SerializeObject(curve[index]));
                        return "curve";
                    case SerializedPropertyType.Gradient:
                        var gradient = (Gradient)
                            typeof(SerializedProperty)
                                .GetProperty("gradientValue", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
                                .GetValue(property);
                        Add(gradient.mode.ToString());
                        foreach (GradientColorKey key in gradient.colorKeys)
                        {
                            Add(key.color.ToString("R"));
                            Add(key.time.ToString("R", CultureInfo.InvariantCulture));
                        }
                        foreach (GradientAlphaKey key in gradient.alphaKeys)
                        {
                            Add(key.alpha.ToString("R", CultureInfo.InvariantCulture));
                            Add(key.time.ToString("R", CultureInfo.InvariantCulture));
                        }
                        return "gradient";
                    case SerializedPropertyType.ManagedReference:
                        return property.managedReferenceFullTypename + ":" + property.managedReferenceId;
                    case SerializedPropertyType.Hash128:
                        return property.hash128Value.ToString();
                    default:
                        throw new ArgumentException("Cannot validate this Editor's serialized Timeline property type: " + property.propertyType + ".");
                }
            }

            internal string Finish()
            {
                token.ThrowIfCancellationRequested();
                writer.Flush();
                stream.FlushFinalBlock();
                return Convert.ToBase64String(hash.Hash);
            }

            public void Dispose()
            {
                writer.Dispose();
                stream.Dispose();
                hash.Dispose();
            }
        }

        internal static string Kind(Object track) =>
            track.GetType() == KindType("animation") ? "animation"
            : track.GetType() == KindType("audio") ? "audio"
            : "unsupported";

        internal static IEnumerable<object> Clips(Object track, CancellationToken token = default)
        {
            foreach (object clip in (IEnumerable)Call(track, "GetClips"))
            {
                token.ThrowIfCancellationRequested();
                if (clip == null)
                    throw new ArgumentException("Timeline track contains a missing clip.");
                yield return clip;
            }
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
            string display = (string)Get(clip, "displayName");
            return new JObject
            {
                ["clip_id"] = playable != null ? Id(playable) : null,
                ["display_name"] = Bounded(display),
                ["display_name_truncated"] = display != null && display.Length > 1024,
                ["start"] = (double)Get(clip, "start"),
                ["duration"] = (double)Get(clip, "duration"),
                ["clip_in"] = (double)Get(clip, "clipIn"),
                ["time_scale"] = (double)Get(clip, "timeScale"),
                ["playable_type"] = Bounded(playable?.GetType().FullName),
                ["source"] = source != null ? Reference(source) : (JToken)JValue.CreateNull(),
            };
        }

        internal static string Bounded(string value) =>
            value != null && value.Length > 1024 ? value.Substring(0, char.IsHighSurrogate(value[1023]) ? 1023 : 1024) : value;

        internal static JObject TrackRow(ScriptableObject track, int offset = 0, int size = 16, CancellationToken token = default)
        {
            string name = track.name;
            var rows = new JArray();
            int count = 0;
            foreach (object clip in Clips(track, token))
            {
                if (count >= offset && rows.Count < size)
                {
                    JObject row = ClipRow(clip);
                    rows.Add(row);
                    if (Encoding.UTF8.GetByteCount(rows.ToString(Formatting.None)) > 65536 && rows.Count > 1)
                    {
                        rows.RemoveAt(rows.Count - 1);
                        size = rows.Count;
                    }
                }
                count = checked(count + 1);
            }
            if (offset > count)
                throw new ArgumentException("Clip offset exceeds this revision's selected track clip count.");
            return new JObject
            {
                ["track_id"] = Id(track),
                ["name"] = Bounded(name),
                ["name_truncated"] = name.Length > 1024,
                ["kind"] = Kind(track),
                ["type"] = Bounded(track.GetType().FullName),
                ["muted"] = (bool)Get(track, "muted"),
                ["clip_count"] = count,
                ["clip_offset"] = offset,
                ["clips"] = rows,
                ["next_clip_offset"] = offset + rows.Count < count ? new JValue(offset + rows.Count) : JValue.CreateNull(),
            };
        }

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
                ["name"] = Bounded(component.name),
                ["type"] = Bounded(component.GetType().FullName),
            };
        }

        internal static bool GraphValid(Component director)
        {
            object graph = Get(director, "playableGraph");
            return (bool)Call(graph, "IsValid");
        }

        internal static string BindingsRevision(Component director, CancellationToken token = default)
        {
            Object asset = Get(director, "playableAsset") as Object;
            using (var state = new RevisionHash(token))
            {
                if (asset != null && TimelineType.IsInstanceOfType(asset))
                    foreach (ScriptableObject track in new Graph(AssetDatabase.GetAssetPath(asset), token).Tracks)
                        state.Add(BindingRow(director, track).ToString(Formatting.None));
                return state.Finish();
            }
        }

        private static JObject BindingRow(Component director, ScriptableObject track) =>
            new JObject
            {
                ["track_id"] = Id(track),
                ["kind"] = Kind(track),
                ["binding"] = (JToken)SceneReference(Call(director, "GetGenericBinding", track) as Object) ?? JValue.CreateNull(),
            };

        internal static JObject DirectorRead(Component director, JObject request = null, CancellationToken token = default)
        {
            request = request ?? new JObject();
            int offset = Whole(request, "offset", 0, 0, int.MaxValue),
                size = Whole(request, "page_size", 16, 1, 32);
            Object asset = Get(director, "playableAsset") as Object;
            var bindings = new JArray();
            int count = 0;
            int bytes = 0;
            if (asset != null && TimelineType.IsInstanceOfType(asset))
                foreach (ScriptableObject track in new Graph(AssetDatabase.GetAssetPath(asset), token).Tracks)
                {
                    if (count >= offset && bindings.Count < size)
                    {
                        JObject row = BindingRow(director, track);
                        int rowBytes = Encoding.UTF8.GetByteCount(row.ToString(Formatting.None));
                        if (bindings.Count > 0 && bytes + rowBytes > 196608)
                            size = bindings.Count;
                        else
                        {
                            bindings.Add(row);
                            bytes += rowBytes;
                        }
                    }
                    count = checked(count + 1);
                }
            if (offset > count)
                throw new ArgumentException("Director binding offset exceeds the current binding count.");
            var data = new JObject
            {
                ["director"] = SceneReference(director),
                ["asset"] = asset != null ? Reference(asset) : (JToken)JValue.CreateNull(),
                ["time"] = (double)Get(director, "time"),
                ["state"] = Get(director, "state").ToString(),
                ["graph_valid"] = GraphValid(director),
                ["bindings"] = bindings,
                ["binding_count"] = count,
                ["next_offset"] = offset + bindings.Count < count ? new JValue(offset + bindings.Count) : JValue.CreateNull(),
            };
            using (var state = new RevisionHash(token))
            {
                state.Object(director);
                state.Add(BindingsRevision(director, token));
                state.Add(data["director"].ToString(Formatting.None));
                state.Add(data["asset"].ToString(Formatting.None));
                // PlayableDirector time/state are observable native state, not serialized fields.
                state.Add(data["time"].ToString(Formatting.None));
                state.Add(data["state"].ToString(Formatting.None));
                state.Add(data["graph_valid"].ToString());
                data["director_revision"] = state.Finish();
            }
            if (
                (offset > 0 || request["expected_director_revision"] != null)
                && Text(request, "expected_director_revision") != data.Value<string>("director_revision")
            )
                throw new ArgumentException("Director binding continuation revision drifted; restart the read.");
            return data;
        }

        internal static void DirectorGuard(JObject request, Component director, CancellationToken token = default)
        {
            string revision = DirectorRead(director, token: token).Value<string>("director_revision");
            DirectorGuardCompletedForTests?.Invoke();
            token.ThrowIfCancellationRequested();
            if (Text(request, "expected_director_revision") != revision)
                throw new ArgumentException("Director/binding/time revision drifted; read_director again.");
        }
    }
}
