using System;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Animation;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class AnimationRoundTripTests
    {
        private string folder;
        private string folderGuid;
        private string clipPath;
        private AnimationClip clip;
        private readonly EditorCurveBinding x = EditorCurveBinding.FloatCurve("", typeof(Transform), "m_LocalPosition.x");
        private readonly EditorCurveBinding y = EditorCurveBinding.FloatCurve("", typeof(Transform), "m_LocalPosition.y");
        private readonly EditorCurveBinding sprites = EditorCurveBinding.PPtrCurve("", typeof(SpriteRenderer), "m_Sprite");

        [SetUp]
        public void SetUp()
        {
            folder = "Assets/__McpAnimationRoundTrip_" + Guid.NewGuid().ToString("N");
            folderGuid = AssetDatabase.CreateFolder("Assets", folder.Substring(7));
            Assert.IsNotEmpty(folderGuid);
            clipPath = folder + "/Clip.asset";
            clip = new AnimationClip { name = "Clip" };
            AssetDatabase.CreateAsset(clip, clipPath);
            AnimationUtility.SetEditorCurve(clip, x, new AnimationCurve(new Keyframe(0, 1), new Keyframe(1, 2)));
            AnimationUtility.SetEditorCurve(clip, y, new AnimationCurve(new Keyframe(0, 42)));
            AssetDatabase.SaveAssets();
        }

        [TearDown]
        public void TearDown()
        {
            if (!string.IsNullOrEmpty(folderGuid) && AssetDatabase.AssetPathToGUID(folder) == folderGuid)
            {
                foreach (string guid in AssetDatabase.FindAssets("", new[] { folder }))
                foreach (UnityEngine.Object asset in AssetDatabase.LoadAllAssetsAtPath(AssetDatabase.GUIDToAssetPath(guid)))
                    Undo.ClearUndo(asset);
                Assert.IsTrue(AssetDatabase.DeleteAsset(folder));
            }
        }

        private static JObject Send(string action, JObject properties, string clipPath = null, string controllerPath = null)
        {
            var request = new JObject { ["action"] = action, ["properties"] = properties };
            if (clipPath != null)
                request["clip_path"] = clipPath;
            if (controllerPath != null)
                request["controller_path"] = controllerPath;
            return JObject.FromObject(ManageAnimation.HandleCommand(request));
        }

        private static JObject Data(JObject response)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response["data"];
        }

        private JObject Curves() => Data(Send("clip_get_curves", new JObject { ["page_size"] = 100 }, clipPath));

        private JObject Binding(string property) => (JObject)((JArray)Curves()["curves"]).Single(row => row.Value<string>("property") == property);

        private JObject Read(string id, int offset = 0, int pageSize = 100) =>
            Data(
                Send(
                    "clip_get_curve",
                    new JObject
                    {
                        ["binding_id"] = id,
                        ["offset"] = offset,
                        ["page_size"] = pageSize,
                    },
                    clipPath
                )
            );

        private JObject Replace(string id, string revision, JArray keys) =>
            Send(
                "clip_replace_curve",
                new JObject
                {
                    ["binding_id"] = id,
                    ["expected_revision"] = revision,
                    ["keys"] = keys,
                },
                clipPath
            );

        private static string Id(UnityEngine.Object asset)
        {
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(asset, out string guid, out long id));
            return id.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }

        [Test]
        public void PageCreatesOnlySelectedDetailsAndValidatesBeforeCreation()
        {
            var type = typeof(ManageAnimation).Assembly.GetType("MCPForUnity.Editor.Tools.Animation.AnimationAssetAccess");
            var lazy = type.GetMethod(
                "Page",
                BindingFlags.Static | BindingFlags.NonPublic,
                null,
                new[] { typeof(int), typeof(Func<int, JToken>), typeof(JObject), typeof(string), typeof(string) },
                null
            );
            int created = 0;
            Func<int, JToken> detail = index =>
            {
                created++;
                return new JObject { ["index"] = index };
            };
            Func<JObject, JObject> page = request =>
            {
                object response;
                if (lazy != null)
                    response = lazy.Invoke(null, new object[] { 16384, detail, request, "revision", "keys" });
                else
                {
                    var eager = type.GetMethod("Page", BindingFlags.Static | BindingFlags.NonPublic);
                    response = eager.Invoke(null, new object[] { new JArray(Enumerable.Range(0, 16384).Select(detail)), request, "revision", "keys" });
                }
                return Data(JObject.FromObject(response));
            };
            var first = page(new JObject { ["pageSize"] = 1 });
            Assert.AreEqual(16384, first.Value<int>("total"));
            Assert.AreEqual(1, created, "A one-row page must construct one detail, not all 16,384 rows.");
            Assert.AreEqual(0, first["keys"][0].Value<int>("index"));
            created = 0;
            Assert.Throws<TargetInvocationException>(() =>
                page(
                    new JObject
                    {
                        ["offset"] = 1,
                        ["pageSize"] = 1,
                        ["expectedRevision"] = "stale",
                    }
                )
            );
            Assert.AreEqual(0, created, "Revision validation precedes detail creation.");
            Assert.Throws<TargetInvocationException>(() => page(new JObject { ["offset"] = 1 }));
            Assert.AreEqual(0, created, "Continuation validation precedes detail creation.");
            Assert.Throws<TargetInvocationException>(() => page(new JObject { ["offset"] = 16385 }));
            Assert.AreEqual(0, created, "Out-of-range validation precedes detail creation.");
            var end = page(new JObject { ["offset"] = 16384, ["expectedRevision"] = "revision" });
            Assert.AreEqual(0, end["keys"].Count());
            Assert.AreEqual(JTokenType.Null, end["next_offset"].Type);
            Assert.AreEqual(0, created);
        }

        private void MeasurePage(string shape, Func<JObject> read)
        {
            var expected = read();
            var method = typeof(GC).GetMethod("GetAllocatedBytesForCurrentThread", BindingFlags.Public | BindingFlags.Static);
            var allocated = method == null ? null : (Func<long>)Delegate.CreateDelegate(typeof(Func<long>), method);
            if (allocated != null)
            {
                long before = allocated();
                var probe = new byte[4096];
                long after = allocated();
                GC.KeepAlive(probe);
                if (after <= before)
                    allocated = null;
            }
            var process = System.Diagnostics.Process.GetCurrentProcess();
            for (int sample = 0; sample < 5; sample++)
            {
                long bytes = allocated?.Invoke() ?? 0;
                long heapBefore = GC.GetTotalMemory(false);
                int gc0 = GC.CollectionCount(0),
                    gc1 = GC.CollectionCount(1),
                    gc2 = GC.CollectionCount(2);
                var cpu = process.TotalProcessorTime;
                var watch = System.Diagnostics.Stopwatch.StartNew();
                var actual = read();
                watch.Stop();
                double cpuMs = (process.TotalProcessorTime - cpu).TotalMilliseconds;
                long? allocation = allocated == null ? (long?)null : allocated() - bytes;
                long heapAfter = GC.GetTotalMemory(false);
                int gc0Delta = GC.CollectionCount(0) - gc0,
                    gc1Delta = GC.CollectionCount(1) - gc1,
                    gc2Delta = GC.CollectionCount(2) - gc2;
                Assert.IsTrue(JToken.DeepEquals(expected, actual));
                TestContext.WriteLine(
                    "BPA12_MEASURE "
                        + new JObject
                        {
                            ["shape"] = shape,
                            ["sample"] = sample,
                            ["elapsed_ms"] = watch.Elapsed.TotalMilliseconds,
                            ["cpu_ms"] = cpuMs,
                            ["allocated_bytes"] = allocation,
                            ["managed_heap_after_bytes"] = heapAfter,
                            ["managed_heap_delta_bytes"] = heapAfter - heapBefore,
                            ["gc0"] = gc0Delta,
                            ["gc1"] = gc1Delta,
                            ["gc2"] = gc2Delta,
                        }.ToString(Newtonsoft.Json.Formatting.None)
                );
            }
        }

        [Test]
        public void LargeCurveOneKeyPageRetainsFullRevisionAndNativeTangents()
        {
            int count = 4096;
            JObject curves;
            while (true)
            {
                var keys = Enumerable
                    .Range(0, count)
                    .Select(i => new Keyframe(i / 60f, i % 11, .25f, -.5f)
                    {
                        inWeight = .2f,
                        outWeight = .4f,
                        weightedMode = WeightedMode.Both,
                    })
                    .ToArray();
                AnimationUtility.SetEditorCurve(clip, x, new AnimationCurve(keys));
                AssetDatabase.SaveAssets();
                // Revision contains GUID:base64(SHA256(file)), then newline + ID:JSON.
                int stateCharacters = AssetDatabase.AssetPathToGUID(clipPath).Length + 1 + 44 + 1 + Id(clip).Length + 1 + EditorJsonUtility.ToJson(clip).Length;
                var response = Send("clip_get_curves", new JObject { ["page_size"] = 100 }, clipPath);
                bool admitted = response.Value<bool>("success");
                TestContext.WriteLine(
                    "BPA12_ADMISSION "
                        + new JObject
                        {
                            ["keys"] = count,
                            ["serialized_state_characters"] = stateCharacters,
                            ["existing_revision_cap_characters"] = 2097152,
                            ["admitted"] = admitted,
                        }.ToString(Newtonsoft.Json.Formatting.None)
                );
                if (admitted)
                {
                    Assert.LessOrEqual(stateCharacters, 2097152);
                    curves = Data(response);
                    break;
                }
                StringAssert.Contains("Round-trip serialized state exceeds 2 MiB", response.ToString());
                Assert.Greater(stateCharacters, 2097152);
                count /= 2;
                Assert.GreaterOrEqual(count, 512, "The public revision fixture must still cover a representative admitted curve.");
            }
            string id = curves["curves"].Single(row => row.Value<string>("property") == x.propertyName).Value<string>("binding_id");
            var first = Read(id, 0, 1);
            Assert.AreEqual(count, first.Value<int>("total"));
            Assert.AreEqual(1, first["keys"].Count());
            AnimationUtility.SetEditorCurve(clip, y, new AnimationCurve(new Keyframe(0, 100)));
            var stale = Send(
                "clip_get_curve",
                new JObject
                {
                    ["binding_id"] = id,
                    ["offset"] = 1,
                    ["page_size"] = 1,
                    ["expected_revision"] = first["revision"],
                },
                clipPath
            );
            Assert.IsFalse(stale.Value<bool>("success"), "An off-page curve mutation still invalidates the complete revision.");
            StringAssert.Contains("revision drifted", stale.ToString());
        }

        [Test]
        public void MaximumNativeCurveDetailsArePagedSeparatelyFromPublicRevisionAdmission()
        {
            // The original dense public fixture exceeded its existing 2 MiB revision limit.
            // Exercise the native detail reader separately; keep an admitted end-to-end
            // fixture above, and retain the existing public revision bound unchanged.
            var keys = Enumerable.Range(0, 16384).Select(i => new Keyframe(i, i % 11)).ToArray();
            AnimationUtility.SetEditorCurve(clip, x, new AnimationCurve(keys));
            var assembly = typeof(ManageAnimation).Assembly;
            var type = assembly.GetType("MCPForUnity.Editor.Tools.Animation.ClipRoundTrip");
            var bindingType = type.GetNestedType("Binding", BindingFlags.NonPublic);
            var binding = Activator.CreateInstance(bindingType, true);
            bindingType.GetField("Value", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(binding, x);
            var reader = type.GetMethod("Read", BindingFlags.Static | BindingFlags.NonPublic);
            Func<JObject> read = () =>
            {
                var request = new JObject { ["pageSize"] = 1 };
                object response;
                if (reader.GetParameters().Length == 4)
                    response = reader.Invoke(null, new object[] { clip, binding, request, "detail-fixture" });
                else
                {
                    var rows = (JArray)reader.Invoke(null, new object[] { clip, binding });
                    var page = assembly
                        .GetType("MCPForUnity.Editor.Tools.Animation.AnimationAssetAccess")
                        .GetMethod("Page", BindingFlags.Static | BindingFlags.NonPublic);
                    response = page.Invoke(null, new object[] { rows, request, "detail-fixture", "keys" });
                }
                return Data(JObject.FromObject(response));
            };
            var first = read();
            Assert.AreEqual(16384, first.Value<int>("total"));
            Assert.AreEqual(1, first["keys"].Count());
            MeasurePage("float_keys_16384_page_1_native_details_only", read);
        }

        [Test]
        public void ConcatenatedCurvePagesMatchEveryNativeKeyAndTangent()
        {
            var keys = Enumerable
                .Range(0, 257)
                .Select(i => new Keyframe(i, i % 7, .25f, -.5f)
                {
                    inWeight = .2f,
                    outWeight = .4f,
                    weightedMode = WeightedMode.Both,
                })
                .ToArray();
            var curve = new AnimationCurve(keys);
            for (int i = 0; i < curve.length; i++)
            {
                AnimationUtility.SetKeyBroken(curve, i, true);
                AnimationUtility.SetKeyLeftTangentMode(curve, i, AnimationUtility.TangentMode.Free);
                AnimationUtility.SetKeyRightTangentMode(curve, i, AnimationUtility.TangentMode.Free);
            }
            AnimationUtility.SetEditorCurve(clip, x, curve);
            AssetDatabase.SaveAssets();
            string id = Binding(x.propertyName).Value<string>("binding_id");
            var all = new JArray();
            string revision = null;
            for (int offset = 0; offset < curve.length; offset += 19)
            {
                var properties = new JObject
                {
                    ["binding_id"] = id,
                    ["offset"] = offset,
                    ["page_size"] = 19,
                };
                if (revision != null)
                    properties["expected_revision"] = revision;
                var page = Data(Send("clip_get_curve", properties, clipPath));
                revision = page.Value<string>("revision");
                foreach (var row in page["keys"])
                    all.Add(row.DeepClone());
            }
            var native = AnimationUtility.GetEditorCurve(clip, x);
            Assert.AreEqual(native.length, all.Count);
            for (int i = 0; i < native.length; i++)
            {
                var key = native[i];
                Assert.AreEqual(key.time, all[i].Value<float>("time"));
                Assert.AreEqual(key.value, all[i].Value<float>("value"));
                Assert.AreEqual(key.inTangent, all[i].Value<float>("in_tangent"));
                Assert.AreEqual(key.outTangent, all[i].Value<float>("out_tangent"));
                Assert.AreEqual(key.inWeight, all[i].Value<float>("in_weight"));
                Assert.AreEqual(key.outWeight, all[i].Value<float>("out_weight"));
                Assert.AreEqual((int)key.weightedMode, all[i].Value<int>("weighted_mode"));
                Assert.AreEqual(AnimationUtility.GetKeyBroken(native, i), all[i].Value<bool>("broken"));
                Assert.AreEqual((int)AnimationUtility.GetKeyLeftTangentMode(native, i), all[i].Value<int>("left_tangent_mode"));
                Assert.AreEqual((int)AnimationUtility.GetKeyRightTangentMode(native, i), all[i].Value<int>("right_tangent_mode"));
            }
        }

        [Test]
        public void LargeControllerOneEntryPagePreservesCompleteGraphRevision()
        {
            var controller = AnimatorController.CreateAnimatorControllerAtPath(folder + "/Large.controller");
            var machine = controller.layers[0].stateMachine;
            AnimatorState previous = null;
            for (int i = 0; i < 768; i++)
            {
                var state = machine.AddState("State" + i);
                if (previous != null)
                    previous.AddTransition(state);
                previous = state;
            }
            AssetDatabase.SaveAssets();
            string path = AssetDatabase.GetAssetPath(controller);
            Func<JObject> read = () => Data(Send("controller_get_graph", new JObject { ["page_size"] = 1 }, controllerPath: path));
            var first = read();
            Assert.AreEqual(1536, first.Value<int>("total"));
            Assert.AreEqual(1, first["entries"].Count());
            MeasurePage("controller_entries_1536_page_1", read);
            previous.speed = 2;
            var stale = Send(
                "controller_get_graph",
                new JObject
                {
                    ["offset"] = 1,
                    ["page_size"] = 1,
                    ["expected_revision"] = first["revision"],
                },
                controllerPath: path
            );
            Assert.IsFalse(stale.Value<bool>("success"));
        }

        [Test]
        public void FloatCurvePageReplaceDelete_PreservesSiblingGuidAndSubassetIdentity()
        {
            var subclip = new AnimationClip { name = "UnrelatedSubclip" };
            AssetDatabase.AddObjectToAsset(subclip, clipPath);
            AssetDatabase.SaveAssets();
            string guid = AssetDatabase.AssetPathToGUID(clipPath);
            string localId = Id(clip);
            string subId = Id(subclip);
            var binding = Binding(x.propertyName);
            string id = binding.Value<string>("binding_id");
            var page = Read(id, 0, 1);
            Assert.AreEqual(2, page.Value<int>("total"));
            Assert.AreEqual(1, ((JArray)page["keys"]).Count);
            Assert.AreEqual(1, page.Value<int>("next_offset"));
            JObject applied = Replace(
                id,
                page.Value<string>("revision"),
                new JArray(
                    new JObject
                    {
                        ["time"] = 0,
                        ["value"] = 7,
                        ["in_tangent"] = 2,
                        ["out_tangent"] = 3,
                        ["in_weight"] = .2f,
                        ["out_weight"] = .3f,
                        ["weighted_mode"] = 3,
                    }
                )
            );
            Data(applied);
            var written = AnimationUtility.GetEditorCurve(clip, x).keys[0];
            Assert.AreEqual(2, written.inTangent);
            Assert.AreEqual(3, written.outTangent);
            Assert.AreEqual(.2f, written.inWeight, .0001f);
            Assert.AreEqual(.3f, written.outWeight, .0001f);
            Assert.AreEqual(WeightedMode.Both, written.weightedMode);
            Assert.AreEqual(7, AnimationUtility.GetEditorCurve(clip, x).keys[0].value);
            Assert.AreEqual(42, AnimationUtility.GetEditorCurve(clip, y).keys[0].value);
            Undo.PerformUndo();
            Assert.AreEqual(1, AnimationUtility.GetEditorCurve(clip, x).keys[0].value);
            var current = Curves();
            string yid = Binding(y.propertyName).Value<string>("binding_id");
            Data(Send("clip_delete_curve", new JObject { ["binding_id"] = yid, ["expected_revision"] = current["revision"] }, clipPath));
            Assert.IsNull(AnimationUtility.GetEditorCurve(clip, y));
            Assert.IsNotNull(AnimationUtility.GetEditorCurve(clip, x));
            Undo.PerformUndo();
            AssetDatabase.SaveAssets();
            AssetDatabase.ImportAsset(clipPath, ImportAssetOptions.ForceUpdate);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(clipPath));
            CollectionAssert.Contains(AssetDatabase.LoadAllAssetsAtPath(clipPath).Select(Id).ToArray(), localId);
            CollectionAssert.Contains(AssetDatabase.LoadAllAssetsAtPath(clipPath).Select(Id).ToArray(), subId);
        }

        [Test]
        public void ObjectReferenceCurve_ReadReplaceDelete_SurvivesImportAndPreservesUnrelatedReferences()
        {
            var texture = new Texture2D(2, 2);
            string path = folder + "/Sprites.asset";
            AssetDatabase.CreateAsset(texture, path);
            var a = Sprite.Create(texture, new Rect(0, 0, 1, 1), Vector2.zero);
            a.name = "A";
            AssetDatabase.AddObjectToAsset(a, path);
            var b = Sprite.Create(texture, new Rect(1, 0, 1, 1), Vector2.zero);
            b.name = "B";
            AssetDatabase.AddObjectToAsset(b, path);
            AnimationUtility.SetObjectReferenceCurve(
                clip,
                sprites,
                new[]
                {
                    new ObjectReferenceKeyframe { time = 0, value = a },
                    new ObjectReferenceKeyframe { time = 1, value = b },
                }
            );
            AssetDatabase.SaveAssets();
            string id = Binding("m_Sprite").Value<string>("binding_id");
            var read = Read(id);
            Assert.AreEqual(Id(a), read["keys"][0]["value"].Value<string>("file_id"));
            var replacement = (JArray)read["keys"].DeepClone();
            replacement[0]["value"] = replacement[1]["value"].DeepClone();
            Data(Replace(id, read.Value<string>("revision"), replacement));
            AssetDatabase.ImportAsset(clipPath, ImportAssetOptions.ForceUpdate);
            var loaded = AssetDatabase.LoadAllAssetsAtPath(clipPath).OfType<AnimationClip>().Single(c => c.name == "Clip");
            Assert.AreEqual(Id(b), Id(AnimationUtility.GetObjectReferenceCurve(loaded, sprites)[0].value));
            Assert.AreEqual(42, AnimationUtility.GetEditorCurve(loaded, y).keys[0].value);
            var current = Curves();
            Data(Send("clip_delete_curve", new JObject { ["binding_id"] = id, ["expected_revision"] = current["revision"] }, clipPath));
            Assert.IsFalse(
                AnimationUtility
                    .GetObjectReferenceCurveBindings(loaded)
                    .Any(binding => binding.path == sprites.path && binding.type == sprites.type && binding.propertyName == sprites.propertyName)
            );
            Assert.IsNotNull(AssetDatabase.LoadAllAssetsAtPath(path).OfType<Sprite>().Single(sprite => Id(sprite) == Id(a)));
        }

        [Test]
        public void StaleClipRevision_RefusesBeforeMutation()
        {
            var binding = Binding(x.propertyName);
            string revision = Curves().Value<string>("revision");
            AnimationUtility.SetEditorCurve(clip, y, new AnimationCurve(new Keyframe(0, 99)));
            Assert.IsFalse(
                Replace(binding.Value<string>("binding_id"), revision, new JArray(new JObject { ["time"] = 0, ["value"] = 8 })).Value<bool>("success")
            );
            Assert.AreEqual(1, AnimationUtility.GetEditorCurve(clip, x).keys[0].value);
            Assert.AreEqual(99, AnimationUtility.GetEditorCurve(clip, y).keys[0].value);
        }

        private AnimatorController Controller(
            out AnimatorState first,
            out AnimatorState second,
            out AnimatorState unrelated,
            out AnimatorStateTransition selected,
            out AnimatorStateTransition sibling
        )
        {
            var controller = AnimatorController.CreateAnimatorControllerAtPath(folder + "/Graph.controller");
            var root = controller.layers[0].stateMachine;
            var left = root.AddStateMachine("Left");
            var right = root.AddStateMachine("Right");
            first = left.AddState("Idle");
            second = left.AddState("Run");
            unrelated = right.AddState("Idle");
            right.AddState("Run");
            selected = first.AddTransition(second);
            selected.duration = .1f;
            sibling = first.AddTransition(second);
            sibling.duration = .2f;
            var other = unrelated.AddTransition(right.states.Single(s => s.state.name == "Run").state);
            other.duration = .3f;
            AssetDatabase.SaveAssets();
            return controller;
        }

        private JObject Graph(AnimatorController controller) =>
            Data(Send("controller_get_graph", new JObject { ["page_size"] = 100 }, controllerPath: AssetDatabase.GetAssetPath(controller)));

        [Test]
        public void ControllerConcatenatedPagesMatchCompleteNestedGraph()
        {
            var controller = Controller(out _, out _, out _, out _, out _);
            var complete = Graph(controller);
            var joined = new JArray();
            string path = AssetDatabase.GetAssetPath(controller);
            for (int offset = 0; offset < complete.Value<int>("total"); offset += 2)
            {
                var page = Data(
                    Send(
                        "controller_get_graph",
                        new JObject
                        {
                            ["offset"] = offset,
                            ["page_size"] = 2,
                            ["expected_revision"] = complete["revision"],
                        },
                        controllerPath: path
                    )
                );
                foreach (var row in page["entries"])
                    joined.Add(row.DeepClone());
            }
            Assert.IsTrue(JToken.DeepEquals(complete["entries"], joined), "Every nested state, transition detail and ordering survives concatenation.");
            Assert.IsFalse(
                Send(
                        "controller_get_graph",
                        new JObject { ["offset"] = complete.Value<int>("total") + 1, ["expected_revision"] = complete["revision"] },
                        controllerPath: path
                    )
                    .Value<bool>("success")
            );
        }

        [Test]
        public void ExactNestedTransitionModifyDeleteAndUndo_PreservesSimilarlyNamedGraph()
        {
            var controller = Controller(out var first, out var second, out var other, out var selected, out var sibling);
            string selectedId = Id(selected);
            string siblingId = Id(sibling);
            var graph = Graph(controller);
            Assert.IsTrue(((JArray)graph["entries"]).Any(row => row.Value<string>("id") == selectedId && row.Value<string>("source_id") == Id(first)));
            Data(
                Send(
                    "controller_modify_transition",
                    new JObject
                    {
                        ["transition_id"] = selectedId,
                        ["expected_revision"] = graph["revision"],
                        ["changes"] = new JObject { ["duration"] = .75f },
                    },
                    controllerPath: AssetDatabase.GetAssetPath(controller)
                )
            );
            Assert.AreEqual(.75f, selected.duration);
            Assert.AreEqual(.2f, sibling.duration);
            Assert.AreEqual(.3f, other.transitions[0].duration);
            Undo.PerformUndo();
            Assert.AreEqual(.1f, selected.duration);
            Data(
                Send(
                    "controller_delete_transition",
                    new JObject { ["transition_id"] = selectedId, ["expected_revision"] = Graph(controller)["revision"] },
                    controllerPath: AssetDatabase.GetAssetPath(controller)
                )
            );
            CollectionAssert.AreEqual(new[] { siblingId }, first.transitions.Select(Id).ToArray());
            Assert.AreEqual(1, other.transitions.Length);
            Undo.PerformUndo();
            Assert.IsTrue(first.transitions.Any(t => Id(t) == selectedId));
            Assert.AreEqual(2, first.transitions.Length);
            foreach (var asset in AssetDatabase.LoadAllAssetsAtPath(AssetDatabase.GetAssetPath(controller)))
                Undo.ClearUndo(asset);
        }

        [Test]
        public void NestedStateModifyAndUnreferencedDelete_UsesStableIdentityAndUndo()
        {
            var controller = Controller(out var first, out var second, out var other, out var selected, out var sibling);
            var machine = controller.layers[0].stateMachine.stateMachines.Single(m => m.stateMachine.name == "Right").stateMachine;
            var state = machine.AddState("Unused");
            AssetDatabase.SaveAssets();
            string id = Id(state);
            Data(
                Send(
                    "controller_modify_state",
                    new JObject
                    {
                        ["state_id"] = id,
                        ["expected_revision"] = Graph(controller)["revision"],
                        ["changes"] = new JObject { ["speed"] = 2 },
                    },
                    controllerPath: AssetDatabase.GetAssetPath(controller)
                )
            );
            Assert.AreEqual(2, state.speed);
            Assert.AreEqual(1, first.speed);
            Undo.PerformUndo();
            Assert.AreEqual(1, state.speed);
            Data(
                Send(
                    "controller_delete_state",
                    new JObject { ["state_id"] = id, ["expected_revision"] = Graph(controller)["revision"] },
                    controllerPath: AssetDatabase.GetAssetPath(controller)
                )
            );
            Assert.IsFalse(machine.states.Any(s => Id(s.state) == id));
            Assert.AreEqual(2, first.transitions.Length);
            Undo.PerformUndo();
            Assert.IsTrue(machine.states.Any(s => Id(s.state) == id));
            foreach (var asset in AssetDatabase.LoadAllAssetsAtPath(AssetDatabase.GetAssetPath(controller)))
                Undo.ClearUndo(asset);
        }

        [Test]
        public void AmbiguousNamesAndUnsavedControllerDrift_RefuseWithoutChangingGraph()
        {
            var controller = Controller(out var first, out var second, out var other, out var selected, out var sibling);
            string path = AssetDatabase.GetAssetPath(controller);
            var graph = Graph(controller);
            other.speed = 3;
            JObject rejected = Send(
                "controller_modify_transition",
                new JObject
                {
                    ["transition_id"] = Id(selected),
                    ["expected_revision"] = graph["revision"],
                    ["changes"] = new JObject { ["duration"] = .9f },
                },
                controllerPath: path
            );
            Assert.IsFalse(rejected.Value<bool>("success"));
            Assert.AreEqual(.1f, selected.duration);
            Assert.AreEqual(3, other.speed);
            rejected = Send(
                "controller_delete_transition",
                new JObject
                {
                    ["from_state"] = "Idle",
                    ["to_state"] = "Run",
                    ["expected_revision"] = Graph(controller)["revision"],
                },
                controllerPath: path
            );
            Assert.IsFalse(rejected.Value<bool>("success"));
            Assert.AreEqual(2, first.transitions.Length);
        }

        [Test]
        public void InvalidLateObjectReference_RejectsEntireReplacementBeforeUndo()
        {
            var texture = new Texture2D(2, 2);
            string path = folder + "/ObjectRefs.asset";
            AssetDatabase.CreateAsset(texture, path);
            var sprite = Sprite.Create(texture, new Rect(0, 0, 1, 1), Vector2.zero);
            AssetDatabase.AddObjectToAsset(sprite, path);
            AnimationUtility.SetObjectReferenceCurve(
                clip,
                sprites,
                new[]
                {
                    new ObjectReferenceKeyframe { time = 0, value = sprite },
                }
            );
            AssetDatabase.SaveAssets();
            string id = Binding("m_Sprite").Value<string>("binding_id");
            JObject read = Read(id);
            int undo = Undo.GetCurrentGroup();
            var keys = new JArray(
                ((JArray)read["keys"])[0].DeepClone(),
                new JObject
                {
                    ["time"] = 1,
                    ["value"] = new JObject { ["guid"] = AssetDatabase.AssetPathToGUID(path), ["file_id"] = "9223372036854775807" },
                }
            );
            JObject rejected = Replace(id, read.Value<string>("revision"), keys);
            Assert.IsFalse(rejected.Value<bool>("success"));
            Assert.AreEqual(undo, Undo.GetCurrentGroup());
            Assert.AreEqual(1, AnimationUtility.GetObjectReferenceCurve(clip, sprites).Length);
            Assert.AreEqual(Id(sprite), Id(AnimationUtility.GetObjectReferenceCurve(clip, sprites)[0].value));
        }

        [Test]
        public void ExactClipSubassetSelection_ModifiesOnlySelectedClipAndRetainsMotionReference()
        {
            var subclip = new AnimationClip { name = "SelectedSubclip" };
            AssetDatabase.AddObjectToAsset(subclip, clipPath);
            AnimationUtility.SetEditorCurve(subclip, x, new AnimationCurve(new Keyframe(0, 5)));
            var controller = AnimatorController.CreateAnimatorControllerAtPath(folder + "/Refs.controller");
            var state = controller.layers[0].stateMachine.AddState("UsesSubclip");
            state.motion = subclip;
            AssetDatabase.SaveAssets();
            string id = Id(subclip);
            string guid = AssetDatabase.AssetPathToGUID(clipPath);
            var rows = Data(Send("clip_get_curves", new JObject { ["clip_file_id"] = id }, clipPath));
            string binding = rows["curves"][0].Value<string>("binding_id");
            Data(
                Send(
                    "clip_replace_curve",
                    new JObject
                    {
                        ["clip_file_id"] = id,
                        ["binding_id"] = binding,
                        ["expected_revision"] = rows["revision"],
                        ["keys"] = new JArray(new JObject { ["time"] = 0, ["value"] = 9 }),
                    },
                    clipPath
                )
            );
            AssetDatabase.ImportAsset(clipPath, ImportAssetOptions.ForceUpdate);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(clipPath));
            Assert.AreEqual(id, Id(state.motion));
            var selectedClip = AssetDatabase.LoadAllAssetsAtPath(clipPath).OfType<AnimationClip>().Single(candidate => Id(candidate) == id);
            Assert.AreEqual(9, AnimationUtility.GetEditorCurve(selectedClip, x).keys[0].value);
            Assert.AreEqual(1, AnimationUtility.GetEditorCurve(clip, x).keys[0].value);
        }
    }
}
