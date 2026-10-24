using System;
using System.Collections;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Tools.Sprite2D;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class TimelineAuthoringTests
    {
        private readonly PrefabTestSceneFixture scenes = new PrefabTestSceneFixture();
        private string folder;
        private string folderGuid;
        private string path;
        private ScriptableObject timeline;
        private AnimationClip animation;
        private AudioClip audio;
        private Component director;
        private Animator animator;
        private AudioSource speaker;
        private Transform child;
        private Scene scene;
        private string preview;
        private static Type TimelineType =>
            AppDomain.CurrentDomain.GetAssemblies().Select(x => x.GetType("UnityEngine.Timeline.TimelineAsset")).FirstOrDefault(x => x != null);

        private static object Get(object target, string name) => target.GetType().GetProperty(name).GetValue(target);

        private static void Set(object target, string name, object value) => target.GetType().GetProperty(name).SetValue(target, value);

        [SetUp]
        public void SetUp()
        {
            scenes.PrepareRunnerBootstrap();
            scene = scenes.Create("McpTimeline_", Guid.NewGuid().ToString("N"));
            folder = "Assets/__McpTimeline_" + Guid.NewGuid().ToString("N");
            folderGuid = AssetDatabase.CreateFolder("Assets", folder.Substring(7));
            path = folder + "/Cutscene.playable";
            Assert.IsNotNull(TimelineType, "Root must enable the real Timeline package before the native fixture.");
            timeline = ScriptableObject.CreateInstance(TimelineType);
            timeline.name = "Cutscene";
            AssetDatabase.CreateAsset(timeline, path);
            animation = new AnimationClip { name = "Move" };
            AnimationUtility.SetEditorCurve(
                animation,
                EditorCurveBinding.FloatCurve("Child", typeof(Transform), "m_LocalPosition.x"),
                AnimationCurve.Linear(0, 0, 1, 10)
            );
            AssetDatabase.CreateAsset(animation, folder + "/Move.anim");
            WriteWav(folder + "/Tone.wav");
            AssetDatabase.ImportAsset(folder + "/Tone.wav", ImportAssetOptions.ForceSynchronousImport);
            audio = AssetDatabase.LoadAssetAtPath<AudioClip>(folder + "/Tone.wav");
            Assert.IsNotNull(audio, "Owned PCM WAV must import as a native AudioClip.");
            var root = new GameObject("Director");
            Type directorType = AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(x => x.GetType("UnityEngine.Playables.PlayableDirector"))
                .FirstOrDefault(x => x != null);
            Assert.IsNotNull(directorType);
            director = root.AddComponent(directorType);
            Set(director, "playOnAwake", false);
            Set(director, "playableAsset", timeline);
            Set(director, "time", .125d);
            var actor = new GameObject("Actor");
            animator = actor.AddComponent<Animator>();
            child = new GameObject("Child").transform;
            child.SetParent(actor.transform, false);
            child.localPosition = new Vector3(3, 0, 0);
            speaker = new GameObject("Speaker").AddComponent<AudioSource>();
            speaker.playOnAwake = false;
            speaker.volume = .75f;
            speaker.mute = false;
            AssetDatabase.SaveAssets();
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (!string.IsNullOrEmpty(preview))
                    Send("preview_stop", new JObject { ["preview_id"] = preview });
                if (!string.IsNullOrEmpty(folderGuid) && AssetDatabase.AssetPathToGUID(folder) == folderGuid)
                {
                    foreach (string guid in AssetDatabase.FindAssets("", new[] { folder }))
                    foreach (Object asset in AssetDatabase.LoadAllAssetsAtPath(AssetDatabase.GUIDToAssetPath(guid)))
                        Undo.ClearUndo(asset);
                    Assert.IsTrue(AssetDatabase.DeleteAsset(folder));
                }
            }
            finally
            {
                scenes.Close();
                scenes.RestoreRunnerBootstrap();
                preview = null;
            }
            LogAssert.NoUnexpectedReceived();
        }

        private static string Full(string asset) => Path.Combine(Directory.GetParent(Application.dataPath).FullName, asset);

        private static void WriteWav(string asset)
        {
            const int frames = 8000;
            using (var stream = File.Create(Full(asset)))
            using (var writer = new BinaryWriter(stream))
            {
                writer.Write(Encoding.ASCII.GetBytes("RIFF"));
                writer.Write(36 + frames * 2);
                writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
                writer.Write(16);
                writer.Write((short)1);
                writer.Write((short)1);
                writer.Write(8000);
                writer.Write(16000);
                writer.Write((short)2);
                writer.Write((short)16);
                writer.Write(Encoding.ASCII.GetBytes("data"));
                writer.Write(frames * 2);
                for (int i = 0; i < frames; i++)
                    writer.Write((short)0);
            }
        }

        private static JObject Ref(Object value)
        {
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long id));
            return new JObject { ["guid"] = guid, ["file_id"] = id.ToString(CultureInfo.InvariantCulture) };
        }

        private JValue SceneId()
        {
#if UNITY_6000_4_OR_NEWER
            return new JValue(scene.handle.GetRawData());
#else
            return new JValue((int)scene.handle);
#endif
        }

        private static JObject Send(string action, JObject values = null)
        {
            Type tool = typeof(ManageSprite).Assembly.GetType("MCPForUnity.Editor.Tools.TimelineTools.ManageTimeline");
            Assert.IsNotNull(tool, "Public manage_timeline domain is missing.");
            var request = values == null ? new JObject() : (JObject)values.DeepClone();
            request["action"] = action;
            return JObject.FromObject(tool.GetMethod("HandleCommand", new[] { typeof(JObject) }).Invoke(null, new object[] { request }));
        }

        private static JObject Data(JObject result)
        {
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            return (JObject)result["data"];
        }

        private JObject Read() => Data(Send("read", new JObject { ["path"] = path }));

        private JObject Guard(JObject properties = null)
        {
            JObject state = Read();
            return new JObject
            {
                ["path"] = path,
                ["guid"] = state["reference"]["guid"],
                ["file_id"] = state["reference"]["file_id"],
                ["expected_revision"] = state["revision"],
                ["properties"] = properties,
            };
        }

        private JObject DirectorRead() =>
            Data(Send("read_director", new JObject { ["target"] = director.GetInstanceIDCompat(), ["scene_handle"] = SceneId() }));

        private JObject DirectorGuard(JObject properties = null)
        {
            JObject request = Guard(properties);
            request["target"] = director.GetInstanceIDCompat();
            request["scene_handle"] = SceneId();
            request["expected_director_revision"] = DirectorRead()["director_revision"];
            return request;
        }

        private JObject AddTrack(string kind, string name) =>
            Data(Send("add_track", Guard(new JObject { ["kind"] = kind, ["name"] = name })))["created_track"] as JObject;

        private JObject AddClip(JObject track, Object source) =>
            Data(
                Send(
                    "add_clip",
                    Guard(
                        new JObject
                        {
                            ["track_id"] = track["track_id"],
                            ["source"] = Ref(source),
                            ["start"] = 0,
                            ["duration"] = 1,
                        }
                    )
                )
            )["created_clip"] as JObject;

        private void Bind(JObject track, Component target) =>
            Data(Send("bind", DirectorGuard(new JObject { ["track_id"] = track["track_id"], ["binding_target"] = target.GetInstanceIDCompat() })));

        private void PrepareTracks()
        {
            JObject animationTrack = AddTrack("animation", "Animation");
            AddClip(animationTrack, animation);
            Bind(animationTrack, animator);
            JObject audioTrack = AddTrack("audio", "Audio");
            AddClip(audioTrack, audio);
            Bind(audioTrack, speaker);
        }

        private void StartPreview()
        {
            var state = Data(Send("preview_start", DirectorGuard()));
            preview = state.Value<string>("preview_id");
            Assert.IsNotEmpty(preview);
        }

        [Test]
        public void AnimationAudioTracksClipsBindings_PreserveAssetAndSourceIdentities()
        {
            // Given a real saved Timeline and source clips.
            var identity = Ref(timeline);
            var animationId = Ref(animation);
            var audioId = Ref(audio);
            // When public authoring creates both native track/clip/binding kinds.
            PrepareTracks();
            // Then native graph and bindings read back without changing original assets.
            var state = Read();
            Assert.IsTrue(JToken.DeepEquals(identity, state["reference"]));
            Assert.AreEqual(2, state.Value<int>("track_count"));
            Assert.AreEqual(2, state.Value<int>("clip_count"));
            var tracks = ((JArray)state["tracks"]).OfType<JObject>().ToArray();
            Assert.IsTrue(JToken.DeepEquals(animationId, tracks.Single(x => x.Value<string>("kind") == "animation")["clips"][0]["source"]));
            Assert.IsTrue(JToken.DeepEquals(audioId, tracks.Single(x => x.Value<string>("kind") == "audio")["clips"][0]["source"]));
            var bindings = (JArray)DirectorRead()["bindings"];
            Assert.AreEqual(animator.GetInstanceIDCompat(), bindings.Single(x => x.Value<string>("kind") == "animation")["binding"].Value<int>("instance_id"));
            Assert.AreEqual(speaker.GetInstanceIDCompat(), bindings.Single(x => x.Value<string>("kind") == "audio")["binding"].Value<int>("instance_id"));
        }

        [Test]
        public void CreateAssign_ReadsPersistedIdentityAndUndoRestoresPreviousDirectorAsset()
        {
            // Given an absent owned destination and an existing director asset assignment.
            string createdPath = folder + "/Created.playable";
            JObject original = Ref(timeline);
            // When the public tool creates a saved Timeline and assigns that exact identity.
            JObject created = Data(Send("create", new JObject { ["path"] = createdPath }));
            Object persisted = AssetDatabase.LoadAssetAtPath(createdPath, TimelineType);
            Assert.IsNotNull(persisted);
            Assert.IsTrue(JToken.DeepEquals(Ref(persisted), created["reference"]));
            Assert.AreEqual(0, created.Value<int>("track_count"));
            JObject request = new JObject
            {
                ["path"] = createdPath,
                ["guid"] = created["reference"]["guid"],
                ["file_id"] = created["reference"]["file_id"],
                ["expected_revision"] = created["revision"],
                ["target"] = director.GetInstanceIDCompat(),
                ["scene_handle"] = SceneId(),
                ["expected_director_revision"] = DirectorRead()["director_revision"],
            };
            Assert.IsTrue(JToken.DeepEquals(created["reference"], Data(Send("assign", request))["asset"]));
            Assert.AreSame(persisted, Get(director, "playableAsset"));
            // Then native Undo restores the previous assignment and a repeated create cannot overwrite.
            Undo.PerformUndo();
            Assert.IsTrue(JToken.DeepEquals(original, DirectorRead()["asset"]));
            byte[] bytes = File.ReadAllBytes(Full(createdPath));
            Assert.IsFalse(Send("create", new JObject { ["path"] = createdPath }).Value<bool>("success"));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Full(createdPath)));
            Assert.IsTrue(JToken.DeepEquals(created["reference"], Ref(persisted)));
        }

        [Test]
        public void ClipUndo_RemovesOwnedPlayableSubassetAndRetainsTrackAndSourceIdentities()
        {
            // Given one saved track and a real source AnimationClip.
            JObject track = AddTrack("animation", "Animation");
            JObject source = Ref(animation);
            JObject identity = Ref(timeline);
            int count = AssetDatabase.LoadAllAssetsAtPath(path).Length;
            // When the public tool creates a native playable clip and native Undo reverses it.
            AddClip(track, animation);
            Assert.AreEqual(1, Read().Value<int>("clip_count"));
            Undo.PerformUndo();
            // Then only that clip/subasset disappears; the existing Timeline, track and source remain.
            JObject state = Read();
            Assert.AreEqual(0, state.Value<int>("clip_count"));
            Assert.AreEqual(1, state.Value<int>("track_count"));
            Assert.AreEqual(track.Value<string>("track_id"), state["tracks"][0].Value<string>("track_id"));
            Assert.AreEqual(count, AssetDatabase.LoadAllAssetsAtPath(path).Length);
            Assert.IsTrue(JToken.DeepEquals(identity, state["reference"]));
            Assert.IsTrue(JToken.DeepEquals(source, Ref(animation)));
        }

        [Test]
        public void PreviewSeekStop_RestoresNativeDirectorTransformAndAudioState()
        {
            // Given supported native tracks, binding targets and an unplayed director.
            PrepareTracks();
            var identity = Ref(timeline);
            double time = (double)Get(director, "time");
            // When the owned preview seeks and then stops.
            StartPreview();
            Data(Send("seek", new JObject { ["preview_id"] = preview, ["time"] = .5 }));
            Assert.AreEqual(5, child.localPosition.x, .05f, "The real Timeline graph must evaluate the animation curve.");
            Assert.AreEqual(.5d, (double)Get(director, "time"), .0001);
            var stopped = Data(Send("preview_stop", new JObject { ["preview_id"] = preview }));
            preview = null;
            // Then only captured tool-owned state returns to its pre-preview values.
            Assert.IsTrue(stopped.Value<bool>("restored"), stopped.ToString());
            Assert.AreEqual(3, child.localPosition.x, .0001f);
            Assert.AreEqual(time, (double)Get(director, "time"), .0001);
            Assert.IsFalse(speaker.mute);
            Assert.AreEqual(.75f, speaker.volume);
            Assert.IsNull(speaker.clip);
            object graph = Get(director, "playableGraph");
            Assert.IsFalse((bool)graph.GetType().GetMethod("IsValid").Invoke(graph, null));
            Assert.IsTrue(JToken.DeepEquals(identity, Ref(timeline)));
        }

        [Test]
        public void PreviewStop_PreservesExternalTransformWriterAndReportsConflict()
        {
            // Given a real sampled preview, followed by another writer changing its transform.
            PrepareTracks();
            StartPreview();
            Data(Send("seek", new JObject { ["preview_id"] = preview, ["time"] = .5 }));
            child.localPosition = new Vector3(77, 0, 0);
            // When the preview stops, then that writer is preserved while owned resources stop.
            var stopped = Data(Send("preview_stop", new JObject { ["preview_id"] = preview }));
            preview = null;
            Assert.AreEqual(77, child.localPosition.x);
            Assert.Greater(((JArray)stopped["conflicts"]).Count, 0);
            Assert.IsFalse(speaker.mute);
        }

        [Test]
        public void PreviewSeek_SourceRevisionExceptionStopsOwnedGraphAndPreservesExternalWriter()
        {
            // Given a real sampled preview and an external transform writer.
            PrepareTracks();
            double originalTime = (double)Get(director, "time");
            StartPreview();
            Data(Send("seek", new JObject { ["preview_id"] = preview, ["time"] = .5 }));
            Assert.IsTrue(speaker.mute);
            child.localPosition = new Vector3(77, 0, 0);
            JObject identity = Ref(timeline);
            string movedPath = folder + "/Moved.playable";
            Assert.IsEmpty(AssetDatabase.MoveAsset(path, movedPath));
            Assert.IsFalse(File.Exists(Full(path)), "The cached revision path must actually be absent.");
            // When revision inspection throws before evaluation, the error must still release preview ownership.
            JObject result = Send("seek", new JObject { ["preview_id"] = preview, ["time"] = .75 });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.IsFalse(speaker.mute, "Failed drift inspection must restore the tool-owned audio mute immediately.");
            Assert.AreEqual(.75f, speaker.volume);
            Assert.IsNull(speaker.clip);
            Assert.AreEqual(originalTime, (double)Get(director, "time"), .0001);
            Assert.AreEqual(77, child.localPosition.x, "Failed guard cleanup must preserve the external writer.");
            object graph = Get(director, "playableGraph");
            Assert.IsFalse((bool)graph.GetType().GetMethod("IsValid").Invoke(graph, null));
            Assert.IsFalse(Send("preview_stop", new JObject { ["preview_id"] = preview }).Value<bool>("success"), "The failed seek must retire its token.");
            preview = null;
            Assert.IsTrue(JToken.DeepEquals(identity, Ref(AssetDatabase.LoadAssetAtPath(movedPath, TimelineType))));
        }

        [Test]
        public void PreviewSeek_ParentCycleStopsOwnedGraphBeforeNativeDuration()
        {
            PrepareTracks();
            StartPreview();
            Type trackType = TimelineType.Assembly.GetType("UnityEngine.Timeline.TrackAsset");
            var roots = (IEnumerable)TimelineType.GetMethod("GetRootTracks").Invoke(timeline, null);
            ScriptableObject track = roots.Cast<ScriptableObject>().First(value => value.GetType().Name == "AnimationTrack");
            FieldInfo parent = trackType.GetField("m_Parent", BindingFlags.Instance | BindingFlags.NonPublic);
            parent.SetValue(track, track);
            try
            {
                JObject result = Send("seek", new JObject { ["preview_id"] = preview, ["time"] = .5 });
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                StringAssert.Contains("parent", result.Value<string>("error"));
            }
            finally
            {
                parent.SetValue(track, timeline);
            }
            object graph = Get(director, "playableGraph");
            Assert.IsFalse((bool)graph.GetType().GetMethod("IsValid").Invoke(graph, null));
            Assert.IsFalse(speaker.mute);
            Assert.AreEqual(3, child.localPosition.x, .0001f);
            Assert.IsFalse(Send("preview_stop", new JObject { ["preview_id"] = preview }).Value<bool>("success"));
            preview = null;
        }

        [Test]
        public void BindingAndTrackUndo_RestoreNativePriorStateAndKeepTimelineGuid()
        {
            // Given one saved native track and no director binding.
            JObject track = AddTrack("animation", "Animation");
            string guid = AssetDatabase.AssetPathToGUID(path);
            Object native = AssetDatabase.LoadAllAssetsAtPath(path).Single(x => Ref(x).Value<string>("file_id") == track.Value<string>("track_id"));
            // When a tool binding is undone using Unity's native Undo stack.
            Bind(track, animator);
            Undo.PerformUndo();
            // Then the actual native binding is absent and a subsequent track undo removes only that track.
            Assert.IsNull(director.GetType().GetMethod("GetGenericBinding").Invoke(director, new object[] { native }));
            Undo.PerformUndo();
            Assert.AreEqual(0, Read().Value<int>("track_count"));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.IsTrue(JToken.DeepEquals(Ref(animation), Ref(AssetDatabase.LoadAssetAtPath<AnimationClip>(AssetDatabase.GetAssetPath(animation)))));
        }

        [TestCase("duration", "-1")]
        [TestCase("start", "true")]
        [TestCase("time_scale", "0")]
        public void InvalidClipPreflight_PreservesSerializedTimelineAndSubassets(string field, string json)
        {
            // Given a valid exact track and an invalid supplied timing field.
            JObject track = AddTrack("animation", "Animation");
            var properties = new JObject
            {
                ["track_id"] = track["track_id"],
                ["source"] = Ref(animation),
                ["start"] = 0,
                ["duration"] = 1,
                [field] = JToken.Parse(json),
            };
            var request = Guard(properties);
            byte[] source = File.ReadAllBytes(Full(path));
            int count = AssetDatabase.LoadAllAssetsAtPath(path).Length;
            // When public preflight runs, then no clip/subasset/source write occurs.
            var result = Send("add_clip", request);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(source, File.ReadAllBytes(Full(path)));
            Assert.AreEqual(count, AssetDatabase.LoadAllAssetsAtPath(path).Length);
        }

        [Test]
        public void AssetDrift_RefusesBeforeTrackMutation()
        {
            // Given a stale revision and another writer's saved timeline name change.
            var request = Guard(new JObject { ["kind"] = "audio", ["name"] = "Audio" });
            timeline.name = "External";
            EditorUtility.SetDirty(timeline);
            AssetDatabase.SaveAssetIfDirty(timeline);
            byte[] bytes = File.ReadAllBytes(Full(path));
            // When a stale track request is dispatched, then the writer's asset remains unchanged.
            Assert.IsFalse(Send("add_track", request).Value<bool>("success"));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Full(path)));
            Assert.AreEqual(0, Read().Value<int>("track_count"));
        }

        private ScriptableObject NativeTrack(string name, ScriptableObject parent = null)
        {
            Type kind = TimelineType.Assembly.GetType("UnityEngine.Timeline." + (parent == null ? "AnimationTrack" : "GroupTrack"));
            return (ScriptableObject)
                TimelineType
                    .GetMethod("CreateTrack", new[] { typeof(Type), kind.BaseType, typeof(string) })
                    .Invoke(timeline, new object[] { kind, parent, name });
        }

        private void SaveTimeline()
        {
            EditorUtility.SetDirty(timeline);
            foreach (Object value in AssetDatabase.LoadAllAssetsAtPath(path))
                EditorUtility.SetDirty(value);
            AssetDatabase.SaveAssets();
        }

        [Test]
        public void LargeGraph_ReadAndAuthorBeyondFormerTrackLimitWithRevisionBoundPages()
        {
            for (int index = 0; index < 80; index++)
                NativeTrack("Track " + index);
            SaveTimeline();
            var elapsed = System.Diagnostics.Stopwatch.StartNew();
            long before = GC.GetTotalMemory(false);
            JObject first = Read();
            Assert.AreEqual(80, first.Value<int>("track_count"));
            Assert.AreEqual(16, ((JArray)first["tracks"]).Count);
            JObject page = Data(
                Send(
                    "read",
                    new JObject
                    {
                        ["path"] = path,
                        ["offset"] = 64,
                        ["page_size"] = 32,
                        ["expected_revision"] = first["revision"],
                    }
                )
            );
            Assert.AreEqual(16, ((JArray)page["tracks"]).Count);
            Assert.IsNull(page["next_offset"].Value<int?>());
            CollectionAssert.AreEqual(
                Enumerable.Range(64, 16).Select(index => "Track " + index).ToArray(),
                ((JArray)page["tracks"]).Select(row => row.Value<string>("name")).ToArray()
            );
            JObject added = AddTrack("audio", "Track 81");
            Assert.IsNotNull(added["track_id"]);
            Assert.AreEqual(81, Read().Value<int>("track_count"));
            Assert.IsFalse(
                Send(
                        "read",
                        new JObject
                        {
                            ["path"] = path,
                            ["offset"] = 64,
                            ["expected_revision"] = first["revision"],
                        }
                    )
                    .Value<bool>("success")
            );
            Undo.PerformUndo();
            Assert.AreEqual(80, Read().Value<int>("track_count"));
            string benchmark =
                "TimelineLargeGraphBenchmark tracks=80 page_tracks=16 elapsed_ms="
                + elapsed.ElapsedMilliseconds
                + " retained_delta="
                + (GC.GetTotalMemory(false) - before);
            LogAssert.Expect(LogType.Log, benchmark);
            Debug.Log(benchmark);
        }

        [Test]
        public void LargeGraph_ClipPagesRemainBoundedAndReachEveryOwnedClip()
        {
            ScriptableObject track = NativeTrack("Many clips");
            MethodInfo create = track.GetType().GetMethod("CreateDefaultClip");
            for (int index = 0; index < 600; index++)
            {
                object clip = create.Invoke(track, null);
                Set(clip, "start", (double)index);
                Set(clip, "duration", 1d);
                Set(Get(clip, "asset"), "clip", animation);
            }
            SaveTimeline();
            JObject state = Read();
            Assert.AreEqual(600, state.Value<int>("clip_count"));
            Assert.LessOrEqual(((JArray)state["tracks"][0]["clips"]).Count, 32);
            string id = Ref(track).Value<string>("file_id");
            int offset = 0,
                seen = 0;
            do
            {
                JObject page = Data(
                    Send(
                        "read",
                        new JObject
                        {
                            ["path"] = path,
                            ["track_id"] = id,
                            ["clip_offset"] = offset,
                            ["clip_page_size"] = 32,
                            ["expected_revision"] = state["revision"],
                        }
                    )
                );
                JObject row = (JObject)page["tracks"][0];
                int count = ((JArray)row["clips"]).Count;
                Assert.Greater(count, 0);
                Assert.LessOrEqual(count, 32);
                Assert.LessOrEqual(Encoding.UTF8.GetByteCount(page.ToString(Newtonsoft.Json.Formatting.None)), 262144);
                seen += count;
                offset = row["next_clip_offset"].Value<int?>() ?? -1;
            } while (offset >= 0);
            Assert.AreEqual(600, seen);
            Assert.IsNotNull(AddClip(new JObject { ["track_id"] = id }, animation)["clip_id"]);
            Undo.PerformUndo();
            Assert.AreEqual(600, Read().Value<int>("clip_count"));
        }

        [Test]
        public void LargeGraph_DeepGroupsAndLargeSerializedAssetRemainReadableAndStaleWritesFail()
        {
            Type group = TimelineType.Assembly.GetType("UnityEngine.Timeline.GroupTrack");
            MethodInfo create = TimelineType.GetMethod("CreateTrack", new[] { typeof(Type), group.BaseType, typeof(string) });
            ScriptableObject parent = null;
            for (int index = 0; index < 16; index++)
                parent = (ScriptableObject)create.Invoke(timeline, new object[] { group, parent, "Group " + index });
            timeline.name = new string('x', 9 * 1024 * 1024);
            SaveTimeline();
            Assert.Greater(new FileInfo(Full(path)).Length, 8388608);
            JObject state = Read();
            Assert.AreEqual(16, state.Value<int>("track_count"));
            JObject stale = Guard(new JObject { ["kind"] = "audio", ["name"] = "Must not write" });
            parent.name = "External unsaved edit";
            EditorUtility.SetDirty(parent);
            Assert.IsFalse(Send("add_track", stale).Value<bool>("success"));
            Assert.AreEqual(16, Read().Value<int>("track_count"));
            Assert.IsFalse(
                Send(
                        "read",
                        new JObject
                        {
                            ["path"] = path,
                            ["offset"] = int.MaxValue,
                            ["expected_revision"] = state["revision"],
                        }
                    )
                    .Value<bool>("success")
            );
        }

        [Test]
        public void LargeGraph_CyclesAndForeignOwnedTracksFailWithoutWriting()
        {
            ScriptableObject track = NativeTrack("Owned");
            SaveTimeline();
            Type trackType = TimelineType.Assembly.GetType("UnityEngine.Timeline.TrackAsset");
            var children = (IList)trackType.GetField("m_Children", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(track);
            FieldInfo cache = trackType.GetField("m_ChildTrackCache", BindingFlags.NonPublic | BindingFlags.Instance);
            // Prime the public native cache with a valid empty child list. The tool must validate
            // the later serialized cycle even if GetChildTracks still returns that old empty list.
            trackType.GetMethod("GetChildTracks").Invoke(track, null);
            byte[] before = File.ReadAllBytes(Full(path));
            children.Add(track);
            try
            {
                JObject result = Send("read", new JObject { ["path"] = path });
                Assert.IsFalse(result.Value<bool>("success"));
                StringAssert.Contains("cyclic", result.Value<string>("error"));
            }
            finally
            {
                children.Clear();
                cache.SetValue(track, null);
            }
            // Reproduce the inverse stale-cache shape: valid repaired serialized edges but a
            // cached self-cycle left by an earlier reader. Native duration must not see it.
            children.Add(track);
            trackType.GetMethod("GetChildTracks").Invoke(track, null);
            children.Clear();
            try
            {
                JObject result = Send("read", new JObject { ["path"] = path });
                Assert.IsFalse(result.Value<bool>("success"));
                StringAssert.Contains("cyclic", result.Value<string>("error"));
            }
            finally
            {
                cache.SetValue(track, null);
            }
            FieldInfo parent = trackType.GetField("m_Parent", BindingFlags.NonPublic | BindingFlags.Instance);
            parent.SetValue(track, track);
            try
            {
                JObject result = Send("read", new JObject { ["path"] = path });
                Assert.IsFalse(result.Value<bool>("success"));
                StringAssert.Contains("parent", result.Value<string>("error"));
            }
            finally
            {
                parent.SetValue(track, timeline);
            }
            ScriptableObject foreign = ScriptableObject.CreateInstance(TimelineType);
            AssetDatabase.CreateAsset(foreign, folder + "/Foreign.playable");
            MethodInfo create = TimelineType.GetMethod("CreateTrack", new[] { typeof(Type), trackType, typeof(string) });
            var foreignTrack = (ScriptableObject)create.Invoke(foreign, new object[] { track.GetType(), null, "Foreign" });
            children.Add(foreignTrack);
            cache.SetValue(track, null);
            try
            {
                JObject result = Send("read", new JObject { ["path"] = path });
                Assert.IsFalse(result.Value<bool>("success"));
                StringAssert.Contains("owned by another asset", result.Value<string>("error"));
            }
            finally
            {
                children.Clear();
                cache.SetValue(track, null);
            }
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Full(path)));

            // Native flattened tracks are an independent cache. A detached cached track can
            // otherwise reach mutedInHierarchy's parent loop even when both root walks are empty.
            Get(timeline, "duration");
            var rootTracks = (IList)TimelineType.GetField("m_Tracks", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(timeline);
            FieldInfo rootCache = TimelineType.GetField("m_CacheRootTracks", BindingFlags.Instance | BindingFlags.NonPublic);
            FieldInfo flatCache = TimelineType.GetField("m_CacheFlattenedTracks", BindingFlags.Instance | BindingFlags.NonPublic);
            rootTracks.Clear();
            rootCache.SetValue(timeline, null);
            parent.SetValue(track, track);
            try
            {
                JObject result = Send("read", new JObject { ["path"] = path });
                Assert.IsFalse(result.Value<bool>("success"));
                StringAssert.Contains("flattened", result.Value<string>("error"));
            }
            finally
            {
                parent.SetValue(track, timeline);
                rootTracks.Add(track);
                rootCache.SetValue(timeline, null);
                flatCache.SetValue(timeline, null);
            }
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Full(path)));
        }

        [TestCase("assign")]
        [TestCase("bind")]
        [TestCase("preview_start")]
        public void CancellationAfterFinalDirectorGuard_PreventsNativeMutation(string action)
        {
            PrepareTracks();
            var roots = (IEnumerable)TimelineType.GetMethod("GetRootTracks").Invoke(timeline, null);
            ScriptableObject track = roots.Cast<ScriptableObject>().First(value => value.GetType().Name == "AnimationTrack");
            var replacement = new GameObject("Replacement binding").AddComponent<Animator>();
            if (action == "assign")
                Set(director, "playableAsset", null);
            JObject request = DirectorGuard(
                action == "bind" ? new JObject { ["track_id"] = Ref(track)["file_id"], ["binding_target"] = replacement.GetInstanceIDCompat() } : null
            );
            request["action"] = action;
            Type tool = AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(assembly => assembly.GetType("MCPForUnity.Editor.Tools.TimelineTools.ManageTimeline"))
                .First(type => type != null);
            FieldInfo hook = tool
                .Assembly.GetType("MCPForUnity.Editor.Tools.TimelineTools.TimelineAccess")
                .GetField("DirectorGuardCompletedForTests", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(hook);
            MethodInfo handle = tool.GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken) });
            int calls = 0;
            using (var cancellation = new CancellationTokenSource())
            {
                hook.SetValue(
                    null,
                    new Action(() =>
                    {
                        if (++calls == 2)
                            cancellation.Cancel();
                    })
                );
                try
                {
                    var error = Assert.Throws<TargetInvocationException>(() => handle.Invoke(null, new object[] { request, cancellation.Token }));
                    Assert.IsInstanceOf<OperationCanceledException>(error.GetBaseException());
                    Assert.AreEqual(2, calls);
                }
                finally
                {
                    hook.SetValue(null, null);
                }
            }
            Assert.AreSame(action == "assign" ? null : timeline, Get(director, "playableAsset"));
            if (action == "bind")
                Assert.AreSame(animator, director.GetType().GetMethod("GetGenericBinding").Invoke(director, new object[] { track }));
            object graph = Get(director, "playableGraph");
            Assert.IsFalse((bool)graph.GetType().GetMethod("IsValid").Invoke(graph, null));
            Assert.IsFalse(speaker.mute);
        }

        [Test]
        public void CancellationDuringSeekDuration_StopsOwnedPreviewBeforeEvaluation()
        {
            PrepareTracks();
            StartPreview();
            Type tool = AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(assembly => assembly.GetType("MCPForUnity.Editor.Tools.TimelineTools.ManageTimeline"))
                .First(type => type != null);
            FieldInfo hook = tool
                .Assembly.GetType("MCPForUnity.Editor.Tools.TimelineTools.TimelineAccess")
                .GetField("DurationForTests", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(hook);
            MethodInfo handle = tool.GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken) });
            using (var cancellation = new CancellationTokenSource())
            {
                hook.SetValue(null, new Action(cancellation.Cancel));
                try
                {
                    JObject request = new JObject
                    {
                        ["action"] = "seek",
                        ["preview_id"] = preview,
                        ["time"] = .5,
                    };
                    var error = Assert.Throws<TargetInvocationException>(() => handle.Invoke(null, new object[] { request, cancellation.Token }));
                    Assert.IsInstanceOf<OperationCanceledException>(error.GetBaseException());
                }
                finally
                {
                    hook.SetValue(null, null);
                }
            }
            object graph = Get(director, "playableGraph");
            Assert.IsFalse((bool)graph.GetType().GetMethod("IsValid").Invoke(graph, null));
            Assert.IsFalse(speaker.mute);
            Assert.AreEqual(3, child.localPosition.x, .0001f);
            preview = null;
        }

        [Test]
        public void LargeGraph_CancelledReadAndWriteLeaveOwnedAssetUnchanged()
        {
            JObject request = Guard(new JObject { ["kind"] = "audio", ["name"] = "Cancelled" });
            byte[] before = File.ReadAllBytes(Full(path));
            Type tool = AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(assembly => assembly.GetType("MCPForUnity.Editor.Tools.TimelineTools.ManageTimeline"))
                .First(type => type != null);
            MethodInfo handle = tool.GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken) });
            Assert.IsNotNull(handle, "The transport must be able to cancel long Timeline validation.");
            using (var cancellation = new CancellationTokenSource())
            {
                cancellation.Cancel();
                foreach (string action in new[] { "read", "add_track" })
                {
                    request["action"] = action;
                    var error = Assert.Throws<TargetInvocationException>(() => handle.Invoke(null, new object[] { request, cancellation.Token }));
                    Assert.IsInstanceOf<OperationCanceledException>(error.GetBaseException());
                }
            }
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Full(path)));
            Assert.AreEqual(0, Read().Value<int>("track_count"));
        }

        [Test]
        public void LargeGraph_CancellationDuringStreamingHashPreventsAuthoring()
        {
            JObject request = Guard(new JObject { ["kind"] = "audio", ["name"] = "Cancelled during revision" });
            request["action"] = "add_track";
            byte[] before = File.ReadAllBytes(Full(path));
            Type tool = AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(assembly => assembly.GetType("MCPForUnity.Editor.Tools.TimelineTools.ManageTimeline"))
                .First(type => type != null);
            FieldInfo hook = tool
                .Assembly.GetType("MCPForUnity.Editor.Tools.TimelineTools.TimelineAccess")
                .GetField("RevisionChunkForTests", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(hook);
            MethodInfo handle = tool.GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken) });
            using (var cancellation = new CancellationTokenSource())
            {
                hook.SetValue(null, new Action(cancellation.Cancel));
                try
                {
                    var error = Assert.Throws<TargetInvocationException>(() => handle.Invoke(null, new object[] { request, cancellation.Token }));
                    Assert.IsInstanceOf<OperationCanceledException>(error.GetBaseException());
                }
                finally
                {
                    hook.SetValue(null, null);
                }
            }
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Full(path)));
            Assert.AreEqual(0, Read().Value<int>("track_count"));
        }

        [Test]
        public void LargeGraph_DirectorPagesAndLongLabelsStayBoundedAndRevisionBound()
        {
            for (int index = 0; index < 80; index++)
                NativeTrack(new string('\uD55C', 4096) + index);
            SaveTimeline();
            JObject state = Read();
            Assert.IsTrue(state["tracks"].All(row => row.Value<string>("name").Length <= 1024));
            Assert.LessOrEqual(Encoding.UTF8.GetByteCount(state.ToString(Newtonsoft.Json.Formatting.None)), 262144);
            JObject first = DirectorRead();
            Assert.AreEqual(80, first.Value<int>("binding_count"));
            Assert.AreEqual(16, ((JArray)first["bindings"]).Count);
            JObject page = Data(
                Send(
                    "read_director",
                    new JObject
                    {
                        ["target"] = director.GetInstanceIDCompat(),
                        ["scene_handle"] = SceneId(),
                        ["offset"] = 64,
                        ["expected_director_revision"] = first["director_revision"],
                    }
                )
            );
            Assert.AreEqual(16, ((JArray)page["bindings"]).Count);
            Set(director, "time", .75d);
            Assert.IsFalse(
                Send(
                        "read_director",
                        new JObject
                        {
                            ["target"] = director.GetInstanceIDCompat(),
                            ["scene_handle"] = SceneId(),
                            ["offset"] = 64,
                            ["expected_director_revision"] = first["director_revision"],
                        }
                    )
                    .Value<bool>("success")
            );
        }
    }
}
