using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Tools.Fixtures;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class SerializedObjectInspectionTests
    {
        private const string NativeCommand = "inspect_serialized_object_v1";
        private static readonly FieldInfo DiscoveryField = typeof(MCPServiceLocator).GetField(
            "_toolDiscoveryService",
            BindingFlags.NonPublic | BindingFlags.Static
        );
        private object _previousDiscovery;
        private bool _discoveryCaptured;
        private string _root;
        private string _rootGuid;
        private Scene _scene;
        private Scene _previousScene;
        private Object[] _previousSelection;
        private SerializedInspectionAsset _asset;
        private SerializedInspectionComponent _first;
        private SerializedInspectionComponent _second;
        private readonly InspectionDiscovery _discovery = new InspectionDiscovery();

        [SetUp]
        public void SetUp()
        {
            _rootGuid = null;
            _root = null;
            _scene = default;
            _previousScene = default;
            _previousSelection = null;
            _discoveryCaptured = false;
            _asset = null;
            _first = null;
            _second = null;
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage(), "Run only in the isolated owned QA Editor.");
            for (int i = 0; i < SceneManager.sceneCount; i++)
                Assert.IsNotEmpty(
                    SceneManager.GetSceneAt(i).path,
                    "Fixture prerequisite: the approved owned-project Editor.Tools bootstrap must save its fresh empty initial scene. Never save unknown scene contents for this test."
                );
            _previousScene = SceneManager.GetActiveScene();
            _previousSelection = Selection.objects;
            Assert.IsNotNull(DiscoveryField);
            _previousDiscovery = DiscoveryField.GetValue(null);
            _discoveryCaptured = true;
            _discovery.Enabled = true;
            MCPServiceLocator.Register<IToolDiscoveryService>(_discovery);
            _root = "Assets/__McpSerializedInspect_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(_root));
            _rootGuid = AssetDatabase.CreateFolder("Assets", _root.Substring(7));
            Assert.IsNotEmpty(_rootGuid);
            _scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            Assert.IsTrue(EditorSceneManager.SaveScene(_scene, _root + "/Scene.unity"));
            SceneManager.SetActiveScene(_scene);
            var owner = new GameObject("SerializedInspectionTarget");
            SceneManager.MoveGameObjectToScene(owner, _scene);
            _first = owner.AddComponent<SerializedInspectionComponent>();
            Assert.IsNotNull(_first, "Fixture prerequisite: SerializedInspectionComponent must be compiled in runtime-eligible TestAsmdef.");
            _second = owner.AddComponent<SerializedInspectionComponent>();
            Assert.IsNotNull(_second, "Fixture prerequisite: both runtime component instances must attach before configuring serialized values.");
            _second.ConfigureSecret(22);
            _asset = ScriptableObject.CreateInstance<SerializedInspectionAsset>();
            AssetDatabase.CreateAsset(_asset, _root + "/Values.asset");
            AssetDatabase.SaveAssetIfDirty(_asset);
            CommandRegistry.Initialize();
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (_scene.IsValid() && _scene.isLoaded)
                    EditorSceneManager.CloseScene(_scene, true);
            }
            finally
            {
                if (_previousScene.IsValid() && _previousScene.isLoaded)
                    SceneManager.SetActiveScene(_previousScene);
                if (_previousSelection != null)
                    Selection.objects = _previousSelection;
                if (_discoveryCaptured)
                    DiscoveryField.SetValue(null, _previousDiscovery);
                if (!string.IsNullOrEmpty(_rootGuid) && AssetDatabase.AssetPathToGUID(_root) == _rootGuid)
                    Assert.IsTrue(AssetDatabase.DeleteAsset(_root));
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ActualPrivateSerializedFieldsAreReadWithoutArbitraryGetters(bool component)
        {
            Object target = component ? (Object)_second : _asset;
            var data = Data(Request(target));
            var records = (JArray)data["records"];
            Assert.AreEqual(component ? 22 : 7, Record(records, "secret")["value"].Value<int>());
            Assert.AreEqual(component ? 17 : 19, Record(records, "hidden")["value"].Value<int>());
            Assert.AreEqual(component ? 31 : 37, Record(records, "inherited")["value"].Value<int>());
            Assert.IsFalse(records.Any(row => row.Value<string>("property_path") == "DangerousGetter"));
            Assert.AreEqual(0, component ? _second.GetterCalls : _asset.GetterCalls);
            Assert.AreEqual("Integer", Record(records, "secret").Value<string>("property_type"));
            Assert.AreEqual("supported_shape", Record(records, "secret").SelectToken("write_contract.status").Value<string>());
            Assert.AreEqual("instance_id_may_load", data.Value<string>("target_resolution"));
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void ActualLargeArrayReturnsOnlyRequestedTailValues()
        {
            _asset.items = Enumerable.Range(0, 100000).ToArray();
            var request = Request(_asset, "array", "items");
            request["start_index"] = 99997;
            request["page_size"] = 3;
            var data = Data(request);
            CollectionAssert.AreEqual(new[] { 99997, 99998, 99999 }, data["records"].Select(row => row["value"].Value<int>()).ToArray());
            CollectionAssert.AreEqual(
                new[] { "items.Array.data[99997]", "items.Array.data[99998]", "items.Array.data[99999]" },
                data["records"].Select(row => row.Value<string>("property_path")).ToArray()
            );
            Assert.AreEqual(100000, data.Value<int>("array_size"));
            Assert.IsTrue(data.Value<bool>("complete"));
            Assert.AreEqual(3, data.SelectToken("work.array_elements").Value<int>());
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void NestedFieldContinuationStopsAtParentBoundary()
        {
            var request = Request(_asset, "fields", "nested");
            request["page_size"] = 1;
            var first = Data(request);
            Assert.AreEqual("nested.marker", first["records"][0].Value<string>("property_path"));
            Assert.IsFalse(first.Value<bool>("complete"));
            Assert.AreEqual("nested.marker", first.Value<string>("next_after_path"));
            request["after_path"] = first["next_after_path"];
            var second = Data(request);
            Assert.AreEqual("nested.values", second["records"][0].Value<string>("property_path"));
            Assert.IsTrue(second.Value<bool>("complete"));
            Assert.AreEqual(1, second["records"].Count());
            Assert.AreEqual(3, second["records"][0].Value<int>("array_size"));
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void LoadedDirtyAssetReadPreservesBytesAndDoesNotEnableAgain()
        {
            string path = _root + "/Values.asset";
            byte[] before = File.ReadAllBytes(path);
            byte[] meta = File.ReadAllBytes(path + ".meta");
            _asset.ConfigureSecret(4321);
            EditorUtility.SetDirty(_asset);
            int enabled = SerializedInspectionAsset.OnEnableCalls;
            int undo = Undo.GetCurrentGroup();
            var data = Data(Request(_asset, "value", "secret"));
            Assert.AreEqual(4321, data["records"][0]["value"].Value<int>());
            Assert.AreEqual(enabled, SerializedInspectionAsset.OnEnableCalls);
            Assert.IsTrue(EditorUtility.IsDirty(_asset));
            Assert.AreEqual(undo, Undo.GetCurrentGroup());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            CollectionAssert.AreEqual(meta, File.ReadAllBytes(path + ".meta"));
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void OwnedUnloadedAssetLookupRecordsActualOnEnableLoading()
        {
            int id = _asset.GetInstanceIDCompat();
            string path = _root + "/Values.asset";
            Assert.IsTrue(AssetDatabase.IsMainAssetAtPathLoaded(path));
            UnityEngine.Resources.UnloadAsset(_asset);
            _asset = null;
            Assert.IsFalse(AssetDatabase.IsMainAssetAtPathLoaded(path), "The fixture must actually unload the one owned SO before lookup.");
            int enabled = SerializedInspectionAsset.OnEnableCalls;
            var response = Dispatch(
                new JObject
                {
                    ["instance_id"] = id,
                    ["mode"] = "value",
                    ["property_path"] = "secret",
                }
            );
#if UNITY_6000_3_OR_NEWER
            if (!response.Value<bool>("success"))
            {
                int lookupEnableCalls = SerializedInspectionAsset.OnEnableCalls;
                Assert.AreEqual("target_not_found", response.Value<string>("code"), response.ToString());
                Assert.IsNull(UnityObjectIdCompat.InstanceIDToObjectCompat(id), "A resolvable native ID must not be rejected by the inspector.");
                TestContext.WriteLine("EDIT05_UNLOADED_LOOKUP_NATIVE_UNAVAILABLE: inspector OnEnable delta=" + (lookupEnableCalls - enabled));
                return;
            }
#endif
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.Greater(SerializedInspectionAsset.OnEnableCalls, enabled, "The documented legacy lookup should enable the unloaded asset.");
            Assert.IsTrue(AssetDatabase.IsMainAssetAtPathLoaded(path));
            Assert.AreEqual(7, response.SelectToken("data.records[0].value").Value<int>());
            TestContext.WriteLine("EDIT05_UNLOADED_LOOKUP_LOADED: actual OnEnable delta=" + (SerializedInspectionAsset.OnEnableCalls - enabled));
            LogAssert.NoUnexpectedReceived();
        }

        private static FieldInfo ObserverField =>
            typeof(ManageScriptableObject)
                .Assembly.GetType("MCPForUnity.Editor.Helpers.SerializedObjectInspection", true)
                .GetField("OperationForTests", BindingFlags.Static | BindingFlags.NonPublic);

        private static void Observe(Action<string, string> observer, Action action)
        {
            FieldInfo field = ObserverField;
            object previous = field.GetValue(null);
            try
            {
                field.SetValue(null, observer);
                action();
            }
            finally
            {
                field.SetValue(null, previous);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CanonicalPrivateNestedEnumAndReferenceShapesAgreeWithRealSetters(bool component)
        {
            Object target = component ? (Object)_second : _asset;
            var shader = Shader.Find("Hidden/InternalErrorShader");
            Assert.IsNotNull(shader);
            var material = new Material(shader);
            AssetDatabase.CreateAsset(material, _root + "/Reference.mat");
            string guid = AssetDatabase.AssetPathToGUID(_root + "/Reference.mat");
            var changes = new JObject
            {
                ["secret"] = 91,
                ["nested.values.Array.data[1]"] = 812,
                ["mode"] = "Hot",
                ["reference"] = new JObject { ["guid"] = guid },
            };
            foreach (var change in changes.Properties())
            {
                var descriptor = Data(Request(target, "value", change.Name))["records"][0];
                Assert.AreEqual(change.Name, descriptor.SelectToken("write_contract.property_key").Value<string>());
                Assert.AreEqual("supported_shape", descriptor.SelectToken("write_contract.status").Value<string>(), descriptor.ToString());
                JObject response;
                if (component)
                {
                    response = JObject.FromObject(
                        ManageComponentsSelected.HandleCommand(
                            new JObject
                            {
                                ["action"] = "set_property",
                                ["target"] = _second.gameObject.GetInstanceIDCompat(),
                                ["componentType"] = typeof(SerializedInspectionComponent).FullName,
                                ["strict_selection"] = true,
                                ["component_instance_id"] = _second.GetInstanceIDCompat(),
                                ["property"] = change.Name,
                                ["value"] = change.Value.DeepClone(),
                            }
                        )
                    );
                }
                else
                {
                    response = JObject.FromObject(
                        ManageScriptableObject.HandleCommand(
                            new JObject
                            {
                                ["action"] = "modify",
                                ["target"] = new JObject { ["guid"] = AssetDatabase.AssetPathToGUID(_root + "/Values.asset") },
                                ["patches"] = new JArray(
                                    new JObject
                                    {
                                        ["op"] = "set",
                                        ["path"] = change.Name,
                                        ["value"] = change.Value.DeepClone(),
                                    }
                                ),
                            }
                        )
                    );
                }
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            }
            Assert.AreEqual(component ? 91 : 22, _second.SecretValue);
            Assert.AreEqual(11, _first.SecretValue, "Inspection and explicit writes must retain exact same-type component identity.");
            Assert.AreEqual(91, Data(Request(target, "value", "secret"))["records"][0]["value"].Value<int>());
            Assert.AreEqual(812, Data(Request(target, "value", "nested.values.Array.data[1]"))["records"][0]["value"].Value<int>());
            var enumeration = Data(Request(target, "value", "mode"))["records"][0];
            Assert.AreEqual(2, enumeration.SelectToken("value.index").Value<int>());
            Assert.AreEqual(42, enumeration.SelectToken("value.raw32").Value<int>());
            Assert.AreEqual("Hot", enumeration.SelectToken("value.name").Value<string>());
            CollectionAssert.AreEqual(
                component ? new[] { "enum_name" } : new[] { "enum_name", "enum_value_index" },
                enumeration.SelectToken("write_contract.input_shapes").Values<string>()
            );
            using var serialized = new SerializedObject(target);
            AssertSameUnityAsset(material, serialized.FindProperty("reference").objectReferenceValue);
            var reference = Data(Request(target, "value", "reference"))["records"][0];
            Assert.AreEqual("nonzero", reference.SelectToken("value.state").Value<string>());
#if UNITY_6000_4_OR_NEWER
            Assert.AreEqual(
                EntityId.ToULong(material.GetEntityId()).ToString(System.Globalization.CultureInfo.InvariantCulture),
                reference.SelectToken("value.raw_id").Value<string>()
            );
            Assert.AreEqual("entity_id_raw64", reference.SelectToken("value.id_kind").Value<string>());
#else
            Assert.AreEqual(
                material.GetInstanceIDCompat().ToString(System.Globalization.CultureInfo.InvariantCulture),
                reference.SelectToken("value.raw_id").Value<string>()
            );
            Assert.AreEqual("instance_id_raw32", reference.SelectToken("value.id_kind").Value<string>());
#endif
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void RootAndNestedPagesCoverStableImmediateFieldsExactlyOnce()
        {
            var expected = new List<string>();
            using (var source = new SerializedObject(_asset))
            using (var iterator = source.GetIterator())
            {
                bool enter = true;
                while (iterator.Next(enter))
                {
                    expected.Add(iterator.propertyPath);
                    enter = false;
                }
            }
            var actual = new List<string>();
            var request = Request(_asset);
            request["page_size"] = 2;
            int pages = 0;
            while (true)
            {
                var data = Data(request);
                Assert.AreEqual(JTokenType.Null, data["total"].Type);
                actual.AddRange(data["records"].Values<JObject>().Select(row => row.Value<string>("property_path")));
                Assert.Less(++pages, 50);
                if (data.Value<bool>("complete"))
                    break;
                Assert.AreEqual(actual.Last(), data.Value<string>("next_after_path"));
                request["after_path"] = data["next_after_path"];
            }
            CollectionAssert.AreEqual(expected, actual);
            Assert.AreEqual(actual.Count, actual.Distinct().Count());
            var nested = Data(Request(_asset, "fields", "groups.Array.data[0]"));
            CollectionAssert.AreEqual(
                new[] { "groups.Array.data[0].marker", "groups.Array.data[0].values" },
                nested["records"].Select(x => x.Value<string>("property_path")).ToArray()
            );
        }

        [Test]
        public void ActualIndexAndIteratorCallsStayWithinRequestedProjection()
        {
            _asset.items = Enumerable.Range(0, 100000).ToArray();
            var calls = new List<string>();
            JObject data = null;
            Observe(
                (kind, path) => calls.Add(kind + ":" + path),
                () =>
                {
                    var request = Request(_asset, "array", "items");
                    request["start_index"] = 99990;
                    request["page_size"] = 3;
                    data = Data(request);
                }
            );
            CollectionAssert.AreEqual(
                new[] { "index:items.Array.data[99990]", "index:items.Array.data[99991]", "index:items.Array.data[99992]" },
                calls.Where(x => x.StartsWith("index:")).ToArray()
            );
            Assert.AreEqual(0, calls.Count(x => x.StartsWith("next:")));
            Assert.AreEqual(1, calls.Count(x => x.StartsWith("lookup:")));
            Assert.AreEqual(99993, data.Value<int>("next_start_index"));
            Assert.LessOrEqual(data.SelectToken("work.used").Value<int>(), 2048);
            calls.Clear();
            Observe(
                (kind, path) => calls.Add(kind),
                () =>
                {
                    var request = Request(_asset);
                    request["page_size"] = 2;
                    Data(request);
                }
            );
            Assert.AreEqual(3, calls.Count(x => x == "next"), "Two returned fields permit only one actual sibling lookahead.");
        }

        [Test]
        public void OutputBudgetStopsBeforeUnemittedArrayItemAndContinuationIsComplete()
        {
            _asset.texts = Enumerable.Range(0, 80).Select(i => i.ToString("D2") + new string('\u4e00', 510)).ToArray();
            var request = Request(_asset, "array", "texts");
            request["page_size"] = 64;
            var paths = new List<string>();
            bool budgetStop = false;
            int start = 0;
            while (true)
            {
                int indices = 0;
                JObject response = null;
                Observe(
                    (kind, path) =>
                    {
                        if (kind == "index")
                            indices++;
                    },
                    () => response = Dispatch(request)
                );
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(response.ToString(Newtonsoft.Json.Formatting.None)), 65536);
                var data = (JObject)response["data"];
                int returned = data.Value<int>("returned");
                Assert.Greater(returned, 0);
                Assert.LessOrEqual(returned, 64);
                Assert.LessOrEqual(indices, returned + 1);
                Assert.LessOrEqual(data.SelectToken("work.used").Value<int>(), 2048);
                foreach (var row in data["records"])
                {
                    paths.Add(row.Value<string>("property_path"));
                    Assert.AreEqual(512, row["value"].Value<string>().Length);
                }
                start += returned;
                budgetStop |= data.Value<string>("stop_reason") == "output_budget";
                if (data.Value<bool>("complete"))
                    break;
                Assert.AreEqual(start, data.Value<int>("next_start_index"));
                request["start_index"] = start;
                Assert.Less(start, 80);
            }
            Assert.IsTrue(budgetStop, "The fixture must exercise an actual output-budget continuation.");
            CollectionAssert.AreEqual(Enumerable.Range(0, 80).Select(i => "texts.Array.data[" + i + "]").ToArray(), paths);
        }

        [Test]
        public void MidProjectionCancellationStopsAfterObservedRealAccess()
        {
            using var cancellation = new CancellationTokenSource();
            int operations = 0;
            Observe(
                (kind, path) =>
                {
                    if (kind == "meta" && ++operations == 5)
                        cancellation.Cancel();
                },
                () =>
                    Assert.Throws<OperationCanceledException>(() =>
                        InspectSerializedObject.HandleCommand(Request(_asset), cancellation.Token).GetAwaiter().GetResult()
                    )
            );
            Assert.AreEqual(5, operations);
        }

        [Test]
        public void CancellationAfterActualElementAllocationDoesNotContinueOrMutate()
        {
            using var cancellation = new CancellationTokenSource();
            int indices = 0;
            int laterOperations = 0;
            Observe(
                (kind, path) =>
                {
                    if (indices != 0)
                        laterOperations++;
                    if (kind == "index")
                    {
                        indices++;
                        cancellation.Cancel();
                    }
                },
                () =>
                    Assert.Throws<OperationCanceledException>(() =>
                        InspectSerializedObject.HandleCommand(Request(_asset, "array", "items"), cancellation.Token).GetAwaiter().GetResult()
                    )
            );
            Assert.AreEqual(1, indices);
            Assert.AreEqual(0, laterOperations);
            CollectionAssert.AreEqual(new[] { 101, 102, 103 }, _asset.items);
            Assert.AreEqual(102, Data(Request(_asset, "value", "items.Array.data[1]"))["records"][0]["value"].Value<int>());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SchemaOnlyAvoidsEveryLeafReadAndManagedCyclesRemainTerminal(bool crossCycle)
        {
            _asset.graph = new SerializedInspectionNode();
            _asset.graph.next = crossCycle ? new SerializedInspectionNode { next = _asset.graph } : _asset.graph;
            int values = 0;
            var request = Request(_asset);
            request["include_values"] = false;
            JObject data = null;
            Observe(
                (kind, path) =>
                {
                    if (kind == "value")
                        values++;
                },
                () => data = Data(request)
            );
            Assert.AreEqual(0, values);
            var rows = (JArray)data["records"];
            Assert.AreEqual("managed_reference_not_expanded", Record(rows, "graph").Value<string>("value_status"));
            Assert.IsNull(Record(rows, "graph")["managed_reference_id"]);
            Assert.AreEqual(3, Record(rows, "mode")["enum_choices"].Count());
            Assert.IsNull(Record(rows, "mode")["value"]);
            Assert.IsFalse(rows.Any(r => r.Value<string>("property_path").StartsWith("graph.")));
            Assert.AreEqual("managed_reference_boundary", Dispatch(Request(_asset, "value", "graph.next.value")).Value<string>("code"));
            Assert.AreEqual("managed_reference_boundary", Dispatch(Request(_asset, "fields", "graph")).Value<string>("code"));
            Assert.AreEqual("unsupported", Data(Request(_asset, "value", "curve"))["records"][0].Value<string>("value_status"));
            Assert.AreEqual(0, _asset.GetterCalls);
            LogAssert.NoUnexpectedReceived();
        }

        private static IEnumerable<string> InvalidRequests()
        {
            yield return "{}";
            foreach (string value in new[] { "null", "true", "\"2\"", "2.0", "0", "2147483648", "-2147483649" })
                yield return "{\"instance_id\":" + value + "}";
            foreach (
                string pair in new[]
                {
                    "\"mode\":null",
                    "\"mode\":\"VALUE\"",
                    "\"property_path\":null",
                    "\"include_values\":null",
                    "\"include_values\":1",
                    "\"page_size\":0",
                    "\"page_size\":65",
                    "\"page_size\":true",
                    "\"page_size\":null",
                    "\"after_path\":null",
                    "\"after_path\":\"\"",
                    "\"start_index\":0",
                    "\"unknown\":1",
                    "\"instanceID\":1",
                    "\"mode\":\"value\",\"property_path\":\"secret\",\"page_size\":1",
                    "\"mode\":\"value\",\"property_path\":\"\"",
                    "\"mode\":\"array\",\"property_path\":\"items\",\"after_path\":\"items\"",
                    "\"mode\":\"array\",\"property_path\":\"items\",\"start_index\":-1",
                    "\"mode\":\"array\",\"property_path\":\"items\",\"start_index\":2147483648",
                    "\"mode\":\"array\",\"property_path\":\"items\",\"start_index\":false",
                }
            )
                yield return "{\"instance_id\":1," + pair + "}";
            foreach (
                string path in new[]
                {
                    "nested..marker",
                    "items.Array.data[01]",
                    "items.Array.data[-1]",
                    "items.Array.data[2147483648]",
                    " secret",
                    new string('x', 513),
                    string.Concat(Enumerable.Repeat("\U0001F600", 257)),
                    string.Join(".", Enumerable.Repeat("x", 17)),
                }
            )
                yield return new JObject { ["instance_id"] = 1, ["property_path"] = path }.ToString();
        }

        [TestCaseSource(nameof(InvalidRequests))]
        public void InvalidParametersFailBeforeThePotentiallyLoadingLookup(string encoded)
        {
            int lookups = 0;
            JObject response = null;
            Observe(
                (kind, path) =>
                {
                    if (kind == "lookup")
                        lookups++;
                },
                () => response = JObject.FromObject(InspectSerializedObject.HandleCommand(JObject.Parse(encoded)))
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("invalid_parameters", response.Value<string>("code"));
            Assert.AreEqual(0, lookups);
        }

        [TestCase("fields", "items", "invalid_projection")]
        [TestCase("array", "secret", "invalid_projection")]
        [TestCase("value", "missing", "property_not_found")]
        [TestCase("value", "items.Array.data[3]", "invalid_range")]
        [TestCase("value", "reference.name", "invalid_projection")]
        public void WrongProjectionFailsExplicitly(string mode, string path, string code)
        {
            Assert.AreEqual(code, Dispatch(Request(_asset, mode, path)).Value<string>("code"));
        }

        [TestCase("secret")]
        [TestCase("nested.values.Array.data[0]")]
        [TestCase("groups.Array.data[0].marker")]
        [TestCase("nested.missing")]
        public void AfterPathMustBeTheExactImmediateChild(string after)
        {
            var request = Request(_asset, "fields", "nested");
            request["after_path"] = after;
            Assert.AreEqual("invalid_after_path", Dispatch(request).Value<string>("code"));
        }

        [Test]
        public void EmptyEndSliceAndOutOfRangeDoNotCallElementAccess()
        {
            int indices = 0;
            Observe(
                (kind, path) =>
                {
                    if (kind == "index")
                        indices++;
                },
                () =>
                {
                    var request = Request(_asset, "array", "items");
                    request["start_index"] = 3;
                    var data = Data(request);
                    Assert.IsTrue(data.Value<bool>("complete"));
                    Assert.AreEqual(0, data.Value<int>("returned"));
                    request["start_index"] = 4;
                    Assert.AreEqual("invalid_range", Dispatch(request).Value<string>("code"));
                }
            );
            Assert.AreEqual(0, indices);
        }

        [Test]
        public void ScalarPrecisionStringBoundsAndNullReferenceAreExplicit()
        {
            Assert.AreEqual(9007199254740993L, Data(Request(_asset, "value", "wide"))["records"][0]["value"].Value<long>());
            Assert.AreEqual(1.0000000000000002, Data(Request(_asset, "value", "precise"))["records"][0]["value"].Value<double>());
            _asset.text = new string('x', 511) + "\U0001F600" + "tail";
            var text = Data(Request(_asset, "value", "text"))["records"][0];
            Assert.AreEqual(511, text["value"].Value<string>().Length, "A surrogate pair must never be split.");
            Assert.IsTrue(text.Value<bool>("value_truncated"));
            Assert.AreEqual(_asset.text.Length, text.Value<int>("string_length"));
            var reference = Data(Request(_asset, "value", "reference"))["records"][0];
            Assert.AreEqual("0", reference.SelectToken("value.raw_id").Value<string>());
            Assert.AreEqual("null_or_unresolved", reference.SelectToken("value.state").Value<string>());
            _asset.precise = double.PositiveInfinity;
            Assert.AreEqual("nonfinite", Data(Request(_asset, "value", "precise"))["records"][0].Value<string>("value_status"));
        }

        [Test]
        public void EnumMetadataHasPerRecordAndWholeRequestLimits()
        {
            _asset.manyEnums = Enumerable.Repeat(SerializedInspectionMany.Last, 64).ToArray();
            var request = Request(_asset, "array", "manyEnums");
            request["page_size"] = 64;
            var data = Data(request);
            Assert.AreEqual(64, data.Value<int>("returned"));
            Assert.AreEqual(64, data["records"].Sum(row => row["enum_choices"].Count()));
            Assert.IsTrue(data["records"].All(row => row["enum_choices"].Count() <= 16));
            Assert.IsTrue(data["records"].All(row => !row.Value<bool>("enum_names_complete")));
            Assert.LessOrEqual(data.SelectToken("work.used").Value<int>(), 2048);
        }

        [Test]
        public void TransientAndSubassetInspectionDoesNotAdvertiseMainAssetWrites()
        {
            var transient = ScriptableObject.CreateInstance<SerializedInspectionAsset>();
            try
            {
                Assert.AreEqual(
                    "no_applicable_target",
                    Data(Request(transient, "value", "secret")).SelectToken("records[0].write_contract.status").Value<string>()
                );
                var child = ScriptableObject.CreateInstance<SerializedInspectionAsset>();
                AssetDatabase.AddObjectToAsset(child, _asset);
                AssetDatabase.SaveAssetIfDirty(_asset);
                Assert.IsFalse(AssetDatabase.IsMainAsset(child));
                var data = Data(Request(child, "value", "secret"));
                Assert.AreEqual("no_applicable_target", data.SelectToken("records[0].write_contract.status").Value<string>());
                Assert.AreEqual(child.GetInstanceIDCompat(), data.SelectToken("target.instance_id").Value<int>());
            }
            finally
            {
                Object.DestroyImmediate(transient);
            }
        }

        [Test]
        public void InvalidObjectTypeAndPersistentPrefabComponentAreRefused()
        {
            Assert.AreEqual("invalid_target", Dispatch(Request(_first.gameObject)).Value<string>("code"));
            GameObject prefab = PrefabUtility.SaveAsPrefabAsset(_first.gameObject, _root + "/Target.prefab");
            Assert.IsNotNull(prefab);
            Assert.AreEqual("invalid_target", Dispatch(Request(prefab.GetComponent<SerializedInspectionComponent>())).Value<string>("code"));
        }

        [Test]
        public void ActualDispatcherEnforcesIndependentCapabilityBeforeLookup()
        {
            _discovery.Enabled = false;
            int lookups = 0;
            string response = null;
            Observe(
                (kind, path) =>
                {
                    if (kind == "lookup")
                        lookups++;
                },
                () =>
                    response = TransportCommandDispatcher
                        .ExecuteCommandJsonAsync(new JObject { ["type"] = NativeCommand, ["params"] = Request(_asset) }.ToString(), CancellationToken.None)
                        .GetAwaiter()
                        .GetResult()
            );
            Assert.AreEqual("error", JObject.Parse(response).Value<string>("status"), response);
            Assert.AreEqual(0, lookups);
            _discovery.Enabled = true;
            Assert.AreEqual(7, Data(Request(_asset, "value", "secret"))["records"][0]["value"].Value<int>());
        }

        [Test]
        public void ExistingAssetDiscoverySuppliesExactInspectorId()
        {
            var info = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "get_info",
                        ["path"] = _root + "/Values.asset",
                        ["generatePreview"] = false,
                    }
                )
            );
            Assert.IsTrue(info.Value<bool>("success"), info.ToString());
            int id = info.SelectToken("data.instanceID").Value<int>();
            Assert.AreEqual(_asset.GetInstanceIDCompat(), id);
            var data = Data(
                new JObject
                {
                    ["instance_id"] = id,
                    ["mode"] = "value",
                    ["property_path"] = "secret",
                }
            );
            Assert.AreEqual(7, data["records"][0]["value"].Value<int>());
            Assert.AreEqual(id, data.SelectToken("target.instance_id").Value<int>());
        }

        [Test]
        public void ScriptableEnumIndexAndReferenceInstanceIdShapesUseExistingSetter()
        {
            var material = new Material(Shader.Find("Hidden/InternalErrorShader"));
            AssetDatabase.CreateAsset(material, _root + "/InstanceReference.mat");
            var patches = new JArray(
                new JObject
                {
                    ["op"] = "set",
                    ["path"] = "mode",
                    ["value"] = 2,
                },
                new JObject
                {
                    ["op"] = "set",
                    ["path"] = "reference",
                    ["value"] = new JObject { ["instanceID"] = material.GetInstanceIDCompat() },
                }
            );
            JObject Apply(JArray changes) =>
                JObject.FromObject(
                    ManageScriptableObject.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify",
                            ["target"] = new JObject { ["path"] = _root + "/Values.asset" },
                            ["patches"] = changes,
                        }
                    )
                );
            var response = Apply(patches);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(SerializedInspectionMode.Hot, _asset.mode);
            AssertSameUnityAsset(material, _asset.reference);
            Assert.AreEqual(42, Data(Request(_asset, "value", "mode")).SelectToken("records[0].value.raw32").Value<int>());
            response = Apply(
                new JArray(
                    new JObject
                    {
                        ["op"] = "set",
                        ["path"] = "reference",
                        ["value"] = JValue.CreateNull(),
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(_asset.reference);
            Assert.AreEqual("null_or_unresolved", Data(Request(_asset, "value", "reference")).SelectToken("records[0].value.state").Value<string>());
        }

        [Test]
        public void SixteenHopSyntaxIsAdmittedButSeventeenRefusesBeforeLookup()
        {
            int lookups = 0;
            string path = "nested." + string.Join(".", Enumerable.Repeat("missing", 15));
            Observe(
                (kind, ignored) =>
                {
                    if (kind == "lookup")
                        lookups++;
                },
                () =>
                {
                    Assert.AreEqual("property_not_found", Dispatch(Request(_asset, "value", path)).Value<string>("code"));
                    Assert.AreEqual(1, lookups, "Sixteen hops pass parameter admission and reach the actual lookup/FindProperty path.");
                    Assert.AreEqual("invalid_parameters", Dispatch(Request(_asset, "value", path + ".missing")).Value<string>("code"));
                    Assert.AreEqual(1, lookups, "Seventeen hops must not enter the loading lookup.");
                }
            );
        }

        [Test]
        public void DeletedOwnedTransientIdDoesNotResolveAnotherTarget()
        {
            var transient = ScriptableObject.CreateInstance<SerializedInspectionAsset>();
            var request = Request(transient, "value", "secret");
            Object.DestroyImmediate(transient);
            int lookups = 0;
            Observe(
                (kind, path) =>
                {
                    if (kind == "lookup")
                        lookups++;
                },
                () => Assert.AreEqual("target_not_found", Dispatch(request).Value<string>("code"))
            );
            Assert.AreEqual(1, lookups);
            Assert.AreEqual(7, _asset.SecretValue);
        }

        [TestCase("Packages/com.example.fixture/Owned.asset")]
        [TestCase("Assets/../Outside.asset")]
        [TestCase("Assets/./Owned.asset")]
        [TestCase("Assets2/Owned.asset")]
        public void WriteRouteContainmentRejectsPackageAndNoncanonicalPaths(string path)
        {
            var helper = typeof(ManageScriptableObject).Assembly.GetType("MCPForUnity.Editor.Helpers.SerializedObjectInspection", true);
            var method = helper.GetMethod("EligibleAssetPath", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsFalse((bool)method.Invoke(null, new object[] { path }));
            Assert.IsTrue((bool)method.Invoke(null, new object[] { _root + "/Values.asset" }));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void IncompatibleTypedReferenceWritePreservesOriginal(bool component)
        {
            var material = new Material(Shader.Find("Hidden/InternalErrorShader"));
            AssetDatabase.CreateAsset(material, _root + "/KeepReference.mat");
            Object target = component ? (Object)_second : _asset;
            if (component)
                _second.reference = material;
            else
                _asset.reference = material;
            string before = Data(Request(target, "value", "reference")).SelectToken("records[0].value.raw_id").Value<string>();
            var incompatible = new JObject { ["instanceID"] = _asset.GetInstanceIDCompat() };
            JObject response;
            if (component)
            {
                LogAssert.Expect(
                    LogType.Warning,
                    "<b><color=#cc7a00>Unity MCP (ykh09242)</color></b>: [ManageComponents] Object 'Values' (type: SerializedInspectionAsset) is not compatible with the property type."
                );
                response = JObject.FromObject(
                    ManageComponentsSelected.HandleCommand(
                        new JObject
                        {
                            ["action"] = "set_property",
                            ["target"] = _second.gameObject.GetInstanceIDCompat(),
                            ["componentType"] = typeof(SerializedInspectionComponent).FullName,
                            ["component_instance_id"] = _second.GetInstanceIDCompat(),
                            ["property"] = "reference",
                            ["value"] = incompatible,
                        }
                    )
                );
            }
            else
                response = JObject.FromObject(
                    ManageScriptableObject.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify",
                            ["target"] = new JObject { ["path"] = _root + "/Values.asset" },
                            ["patches"] = new JArray(
                                new JObject
                                {
                                    ["op"] = "set",
                                    ["path"] = "reference",
                                    ["value"] = incompatible,
                                }
                            ),
                        }
                    )
                );
            TestContext.WriteLine("EDIT05_INCOMPATIBLE_WRITE_RESPONSE " + response.ToString(Newtonsoft.Json.Formatting.None));
            if (component)
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            else
            {
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(1, ((JArray)response.SelectToken("data.results")).Count, response.ToString());
                Assert.AreEqual("reference", response.SelectToken("data.results[0].propertyPath").Value<string>());
                Assert.IsFalse(response.SelectToken("data.results[0].ok").Value<bool>(), response.ToString());
            }
            Assert.AreSame(material, component ? _second.reference : _asset.reference);
            Assert.AreEqual(before, Data(Request(target, "value", "reference")).SelectToken("records[0].value.raw_id").Value<string>());
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void NestedListAtLargeArrayTailChargesOnlyAncestorAndSelectedIndices()
        {
            _asset.groups = Enumerable.Range(0, 100000).Select(i => new SerializedInspectionGroup { marker = i }).ToList();
            var indices = new List<string>();
            JObject data = null;
            Observe(
                (kind, path) =>
                {
                    if (kind == "index")
                        indices.Add(path);
                },
                () =>
                {
                    var request = Request(_asset, "array", "groups.Array.data[99999].values");
                    request["page_size"] = 3;
                    data = Data(request);
                }
            );
            Assert.AreEqual(4, indices.Count, "One explicitly requested ancestor index plus three actual slice indices.");
            Assert.AreEqual(4, data.SelectToken("work.array_elements").Value<int>());
            Assert.AreEqual(3, data.SelectToken("work.value_reads").Value<int>());
            CollectionAssert.AreEqual(new[] { 31, 32, 33 }, data["records"].Select(row => row["value"].Value<int>()).ToArray());
        }

        [Test]
        public void FieldOutputStopResumesFromLastEmittedFieldWithoutOmissions()
        {
            foreach (var field in typeof(SerializedInspectionWide).GetFields(BindingFlags.Instance | BindingFlags.Public))
                field.SetValue(_asset.wideFields, new string('\u4e00', 512));
            var request = Request(_asset, "fields", "wideFields");
            request["page_size"] = 64;
            var paths = new List<string>();
            bool limited = false;
            while (true)
            {
                var response = Dispatch(request);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(response.ToString(Newtonsoft.Json.Formatting.None)), 65536);
                var data = (JObject)response["data"];
                paths.AddRange(data["records"].Select(row => row.Value<string>("property_path")));
                limited |= data.Value<string>("stop_reason") == "output_budget";
                if (data.Value<bool>("complete"))
                    break;
                Assert.AreEqual(paths.Last(), data.Value<string>("next_after_path"));
                request["after_path"] = data["next_after_path"];
                Assert.Less(paths.Count, 40);
            }
            Assert.IsTrue(limited);
            CollectionAssert.AreEqual(Enumerable.Range(0, 40).Select(i => "wideFields.f" + i.ToString("D2")).ToArray(), paths);
        }

        [Test]
        public void SubsequentRequestsExposeLiveArrayReorderAndMissingFieldContinuation()
        {
            var request = Request(_asset, "array", "items");
            request["page_size"] = 1;
            var first = Data(request);
            Assert.AreEqual(101, first["records"][0]["value"].Value<int>());
            _asset.items = new[] { 103, 101, 102 };
            request["start_index"] = first["next_start_index"];
            var second = Data(request);
            Assert.AreEqual("live_serialized", second.Value<string>("consistency"));
            Assert.AreEqual(
                101,
                second["records"][0]["value"].Value<int>(),
                "A live continuation intentionally observes reorder rather than pretending snapshot identity."
            );
            var fields = Request(_asset, "fields", "nested");
            fields["after_path"] = "nested.no_longer_present";
            Assert.AreEqual("invalid_after_path", Dispatch(fields).Value<string>("code"));
        }

        [Test]
        public void RetainedTargetDestroyedAfterActualValueReadFailsWithoutAnotherLookup()
        {
            int lookups = 0;
            JObject response = null;
            var request = Request(_second, "value", "secret");
            Observe(
                (kind, path) =>
                {
                    if (kind == "lookup")
                        lookups++;
                    if (kind == "value")
                        Object.DestroyImmediate(_second);
                },
                () => response = Dispatch(request)
            );
            Assert.AreEqual("target_not_found", response.Value<string>("code"), response.ToString());
            Assert.AreEqual(1, lookups);
            Assert.AreEqual(11, _first.SecretValue);
            LogAssert.NoUnexpectedReceived();
        }

        private static void AssertSameUnityAsset(Object expected, Object actual)
        {
            Assert.IsTrue(expected != null, "The expected Unity asset must remain alive.");
            Assert.IsTrue(actual != null, "The assigned Unity asset must remain alive.");
            Assert.IsTrue(expected == actual, "The reference must identify the same live native Unity asset.");
            Assert.AreEqual(GlobalObjectId.GetGlobalObjectIdSlow(expected), GlobalObjectId.GetGlobalObjectIdSlow(actual));
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(expected, out string expectedGuid, out long expectedFileId));
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(actual, out string actualGuid, out long actualFileId));
            Assert.AreEqual(expectedGuid, actualGuid);
            Assert.AreEqual(expectedFileId, actualFileId);
        }

        private static JObject Request(Object target, string mode = "fields", string path = "") =>
            new JObject
            {
                ["instance_id"] = target.GetInstanceIDCompat(),
                ["mode"] = mode,
                ["property_path"] = path,
            };

        private static JObject Record(JArray records, string path) => (JObject)records.Single(row => row.Value<string>("property_path") == path);

        private static JObject Data(JObject request)
        {
            var response = Dispatch(request);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response["data"];
        }

        private static JObject Dispatch(JObject request)
        {
            using var lifetime = new CancellationTokenSource();
            var task = TransportCommandDispatcher.ExecuteCommandJsonAsync(
                new JObject { ["type"] = NativeCommand, ["params"] = request }.ToString(),
                lifetime.Token
            );
            Assert.IsTrue(task.IsCompleted, "The synchronous inspector must finish in the real main-thread dispatcher pump.");
            var envelope = JObject.Parse(task.GetAwaiter().GetResult());
            Assert.AreEqual("success", envelope.Value<string>("status"), "The versioned inspector must be registered: " + envelope);
            return (JObject)envelope["result"];
        }

        private sealed class InspectionDiscovery : IToolDiscoveryService
        {
            internal bool Enabled = true;

            public bool IsToolEnabled(string name) =>
                name == NativeCommand ? Enabled : name == "manage_components_selected" || name == "manage_components" || name == "manage_scriptable_object";

            public ToolMetadata GetToolMetadata(string name) => new ToolMetadata { Name = name };

            public List<ToolMetadata> DiscoverAllTools() => new List<ToolMetadata>();

            public List<ToolMetadata> GetEnabledTools() => new List<ToolMetadata>();

            public void SetToolEnabled(string name, bool enabled) => throw new InvalidOperationException("No preference writes in this fixture.");

            public void InvalidateCache() { }
        }
    }
}
