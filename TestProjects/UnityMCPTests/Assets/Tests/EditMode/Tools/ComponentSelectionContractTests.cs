using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Resources.Scene;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Resources;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ComponentSelectionContractTests
    {
        private string _assetRoot;
        private string _assetRootGuid;
        private Object[] _previousSelection;
        private Scene _previous;
        private Scene _scene;
        private Scene _otherScene;
        private GameObject _target;
        private AudioSource _first;
        private AudioSource _second;
        private object _previousToolDiscovery;
        private bool _capturedDiscovery;
        private readonly SelectionToolDiscovery _toolDiscovery = new SelectionToolDiscovery();
        private static readonly FieldInfo ToolDiscoveryField = typeof(MCPServiceLocator).GetField(
            "_toolDiscoveryService",
            BindingFlags.NonPublic | BindingFlags.Static
        );
        private static readonly FieldInfo ResourceDiscoveryField = typeof(MCPServiceLocator).GetField(
            "_resourceDiscoveryService",
            BindingFlags.NonPublic | BindingFlags.Static
        );

        [SetUp]
        public void SetUp()
        {
            _assetRootGuid = null;
            _capturedDiscovery = false;
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());
            Assert.IsNotNull(ToolDiscoveryField);
            _previousToolDiscovery = ToolDiscoveryField.GetValue(null);
            _capturedDiscovery = true;
            _toolDiscovery.ParentEnabled = true;
            _toolDiscovery.AliasEnabled = true;
            MCPServiceLocator.Register<IToolDiscoveryService>(_toolDiscovery);
            _previous = SceneManager.GetActiveScene();
            _previousSelection = Selection.objects;
            _assetRoot = "Assets/__McpComponentSelection_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(_assetRoot));
            _assetRootGuid = AssetDatabase.CreateFolder("Assets", _assetRoot.Substring(7));
            Assert.IsNotEmpty(_assetRootGuid);
            _scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            Assert.IsTrue(EditorSceneManager.SaveScene(_scene, _assetRoot + "/First.unity"));
            _otherScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            Assert.IsTrue(EditorSceneManager.SaveScene(_otherScene, _assetRoot + "/Second.unity"));
            SceneManager.SetActiveScene(_scene);
            _target = Create("SelectionTarget", _scene);
            _first = _target.AddComponent<AudioSource>();
            _second = _target.AddComponent<AudioSource>();
            _first.volume = 0.2f;
            _second.volume = 0.8f;
            CommandRegistry.Initialize();
        }

        [TearDown]
        public void TearDown()
        {
            if (_capturedDiscovery)
            {
                ToolDiscoveryField.SetValue(null, _previousToolDiscovery);
                _capturedDiscovery = false;
            }
            if (_previous.IsValid() && _previous.isLoaded)
                SceneManager.SetActiveScene(_previous);
            if (_otherScene.IsValid() && _otherScene.isLoaded)
                EditorSceneManager.CloseScene(_otherScene, true);
            if (_scene.IsValid() && _scene.isLoaded)
                EditorSceneManager.CloseScene(_scene, true);
            if (!string.IsNullOrEmpty(_assetRootGuid))
            {
                Assert.AreEqual(_assetRootGuid, AssetDatabase.AssetPathToGUID(_assetRoot));
                Assert.IsTrue(AssetDatabase.DeleteAsset(_assetRoot));
            }
            Selection.objects = _previousSelection;
        }

        [TestCase("add")]
        [TestCase("remove")]
        [TestCase("set_property")]
        public void StrictAmbiguousTargetRejectsBeforeMutation(string action)
        {
            // Given two names, including an inactive match, in the documented scope.
            GameObject other = Create(_target.name, _scene);
            AudioSource otherSource = other.AddComponent<AudioSource>();
            otherSource.volume = 0.4f;
            other.SetActive(false);
            Assert.IsTrue(EditorSceneManager.SaveScene(_scene));
            int undo = Undo.GetCurrentGroup();
            JObject request = Write(action);
            request["target"] = _target.name;
            request["search_method"] = "by_name";
            // When the existing native entry is explicitly asked for strict selection.
            JObject response = Json(ManageComponents.HandleCommand(request));
            // Then ambiguity is detected before any mutation/dirty/Undo effect.
            Code(response, "ambiguous_target");
            Assert.AreEqual(2, _target.GetComponents<AudioSource>().Length);
            Assert.AreEqual(1, other.GetComponents<AudioSource>().Length);
            Assert.AreEqual(0.2f, _first.volume);
            Assert.AreEqual(0.8f, _second.volume);
            Assert.AreEqual(0.4f, otherSource.volume);
            Assert.IsFalse(_scene.isDirty);
            Assert.AreEqual(undo, Undo.GetCurrentGroup());
        }

        [TestCase("remove")]
        [TestCase("set_property")]
        public void StrictAmbiguousComponentRejectsBeforeMutation(string action)
        {
            Assert.IsTrue(EditorSceneManager.SaveScene(_scene));
            JObject response = Json(ManageComponents.HandleCommand(Write(action)));
            Code(response, "ambiguous_component");
            Assert.AreEqual(2, _target.GetComponents<AudioSource>().Length);
            Assert.AreEqual(0.2f, _first.volume);
            Assert.AreEqual(0.8f, _second.volume);
            Assert.IsFalse(_scene.isDirty);
        }

        [Test]
        public void IndexedReadSelectsSecondAudioSource()
        {
            JObject request = Read();
            request["component_index"] = 1;
            JObject response = Json(GameObjectComponentResource.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(_second.GetInstanceIDCompat(), response.SelectToken("data.component.instanceID").Value<int>());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SelectedWriteAndReadUseExactComponent(bool byId)
        {
            JObject write = Write("set_property");
            JObject read = Read();
            string selector = byId ? "component_instance_id" : "component_index";
            int value = byId ? _second.GetInstanceIDCompat() : 1;
            write[selector] = value;
            read[selector] = value;
            JObject response = SelectedWrite(write);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(0.2f, _first.volume);
            Assert.AreEqual(0.35f, _second.volume);
            Assert.AreEqual(_second.GetInstanceIDCompat(), response.SelectToken("data.selection.componentInstanceID").Value<int>());
            JObject actual = SelectedRead(read);
            Assert.IsTrue(actual.Value<bool>("success"), actual.ToString());
            Assert.AreEqual(_second.GetInstanceIDCompat(), actual.SelectToken("data.component.instanceID").Value<int>());
            Assert.AreEqual(1, actual.SelectToken("data.selection.componentIndex").Value<int>());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SelectedRemoveDeletesOnlySecondAudioSource(bool byId)
        {
            int removedId = _second.GetInstanceIDCompat();
            JObject request = Write("remove");
            request[byId ? "component_instance_id" : "component_index"] = byId ? removedId : 1;
            JObject response = SelectedWrite(request);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(_second == null);
            Assert.IsTrue(_first != null);
            Assert.AreEqual(1, _target.GetComponents<AudioSource>().Length);
            Assert.AreEqual(removedId, response.SelectToken("data.selection.componentInstanceID").Value<int>());
        }

        [Test]
        public void SelectedAddReturnsNewIdentityWithoutSelectingAnExistingComponent()
        {
            JObject response = SelectedWrite(Write("add"));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(3, _target.GetComponents<AudioSource>().Length);
            int id = response.SelectToken("data.selection.componentInstanceID").Value<int>();
            Assert.AreNotEqual(_first.GetInstanceIDCompat(), id);
            Assert.AreNotEqual(_second.GetInstanceIDCompat(), id);
        }

        [TestCase("-1")]
        [TestCase("2")]
        public void SelectedIndexOutOfRangeDoesNotFallBack(string index)
        {
            JObject request = Write("set_property");
            request["component_index"] = JToken.Parse(index);
            Code(SelectedWrite(request), index == "-1" ? "invalid_selection" : "component_index_out_of_range");
            Assert.AreEqual(0.2f, _first.volume);
            Assert.AreEqual(0.8f, _second.volume);
        }

        [TestCase("true")]
        [TestCase("1.5")]
        [TestCase("2147483648")]
        [TestCase("[]")]
        public void SelectedMalformedIndexRejectsWithoutMutation(string json)
        {
            JObject request = Write("remove");
            request["component_index"] = JToken.Parse(json);
            Code(SelectedWrite(request), "invalid_selection");
            Assert.AreEqual(2, _target.GetComponents<AudioSource>().Length);
        }

        [Test]
        public void ForeignComponentIdCannotSelectAnotherOwnerOrFallBack()
        {
            AudioSource foreign = Create("Other", _scene).AddComponent<AudioSource>();
            JObject request = Write("set_property");
            request["component_instance_id"] = foreign.GetInstanceIDCompat();
            Code(SelectedWrite(request), "component_selector_mismatch");
            Assert.AreEqual(0.2f, _first.volume);
            Assert.AreEqual(0.8f, _second.volume);
            Assert.AreEqual(1f, foreign.volume);
        }

        [Test]
        public void NameScopeIsActiveSceneWhileExplicitIdCanSelectAnotherLoadedScene()
        {
            GameObject other = Create(_target.name, _otherScene);
            AudioSource source = other.AddComponent<AudioSource>();
            JObject byName = Write("set_property");
            byName["target"] = _target.name;
            byName["component_index"] = 1;
            Assert.IsTrue(SelectedWrite(byName).Value<bool>("success"));
            Assert.AreEqual(1f, source.volume);
            JObject byId = Write("set_property");
            byId["target"] = other.GetInstanceIDCompat();
            Assert.IsTrue(SelectedWrite(byId).Value<bool>("success"));
            Assert.AreEqual(0.35f, source.volume);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void LegacyOmittedOrFalseStrictSelectionKeepsFirstComponent(bool explicitFalse)
        {
            JObject request = Write("set_property");
            request.Remove("strict_selection");
            if (explicitFalse)
                request["strict_selection"] = false;
            JObject response = Json(ManageComponents.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(0.35f, _first.volume);
            Assert.AreEqual(0.8f, _second.volume);
            Assert.IsNull(response.SelectToken("data.selection"));
        }

        [TestCase("strict_selection", "null")]
        [TestCase("strict_selection", "1")]
        [TestCase("strict_selection", "\"true\"")]
        [TestCase("component_instance_id", "true")]
        [TestCase("component_instance_id", "1.0")]
        [TestCase("component_instance_id", "\"12\"")]
        [TestCase("component_instance_id", "0")]
        [TestCase("component_instance_id", "2147483648")]
        [TestCase("component_instance_id", "-2147483649")]
        public void MalformedSelectorsRejectIdenticallyOnReadAndWrite(string key, string json)
        {
            JObject write = Write("remove");
            JObject read = Read();
            write[key] = JToken.Parse(json);
            read[key] = JToken.Parse(json);
            Code(SelectedWrite(write), "invalid_selection");
            Code(SelectedRead(read), "invalid_selection");
            Assert.AreEqual(2, _target.GetComponents<AudioSource>().Length);
        }

        [TestCase("strictSelection", "strict_selection", "true", "false")]
        [TestCase("componentIndex", "component_index", "0", "1")]
        [TestCase("componentInstanceID", "component_instance_id", "12", "13")]
        public void ConflictingAliasesRejectOnReadAndWrite(string camel, string snake, string first, string second)
        {
            JObject write = Write("remove"),
                read = Read();
            foreach (JObject request in new[] { write, read })
            {
                request[camel] = JToken.Parse(first);
                request[snake] = JToken.Parse(second);
            }
            Code(SelectedWrite(write), "invalid_selection");
            Code(SelectedRead(read), "invalid_selection");
            Assert.AreEqual(2, _target.GetComponents<AudioSource>().Length);
        }

        [Test]
        public void AgreeingAliasesAndDecimalIndexStringsSelectSameComponent()
        {
            JObject write = Write("set_property"),
                read = Read();
            foreach (JObject request in new[] { write, read })
            {
                request["componentIndex"] = "1";
                request["component_index"] = "1";
                request["strictSelection"] = true;
                request["strict_selection"] = true;
            }
            Assert.IsTrue(SelectedWrite(write).Value<bool>("success"));
            Assert.AreEqual(_second.GetInstanceIDCompat(), SelectedRead(read).SelectToken("data.component.instanceID").Value<int>());
            Assert.AreEqual(0.2f, _first.volume);
            Assert.AreEqual(0.35f, _second.volume);
        }

        [Test]
        public void NullComponentSelectorsAreAbsentRatherThanFirstMatch()
        {
            JObject write = Write("remove"),
                read = Read();
            foreach (JObject request in new[] { write, read })
            {
                request["component_index"] = JValue.CreateNull();
                request["component_instance_id"] = JValue.CreateNull();
            }
            Code(SelectedWrite(write), "ambiguous_component");
            Code(SelectedRead(read), "ambiguous_component");
            Assert.AreEqual(2, _target.GetComponents<AudioSource>().Length);
        }

        [Test]
        public void BothSelectorsRejectBeforeMutationOrRead()
        {
            JObject write = Write("remove"),
                read = Read();
            foreach (JObject request in new[] { write, read })
            {
                request["component_index"] = 1;
                request["component_instance_id"] = _second.GetInstanceIDCompat();
            }
            Code(SelectedWrite(write), "invalid_selection");
            Code(SelectedRead(read), "invalid_selection");
            Assert.AreEqual(2, _target.GetComponents<AudioSource>().Length);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SelectedAddRejectsExistingComponentSelectors(bool byId)
        {
            JObject request = Write("add");
            request[byId ? "component_instance_id" : "component_index"] = byId ? _second.GetInstanceIDCompat() : 1;
            Code(SelectedWrite(request), "invalid_selection");
            Assert.AreEqual(2, _target.GetComponents<AudioSource>().Length);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void WrongTypeOrDestroyedComponentIdNeverFallsBack(bool destroyed)
        {
            Component selected = destroyed ? (Component)_target.AddComponent<AudioSource>() : _target.transform;
            int id = selected.GetInstanceIDCompat();
            if (destroyed)
                Object.DestroyImmediate(selected);
            JObject write = Write("set_property"),
                read = Read();
            write["component_instance_id"] = read["component_instance_id"] = id;
            string code = destroyed ? "component_not_found" : "component_selector_mismatch";
            Code(SelectedWrite(write), code);
            Code(SelectedRead(read), code);
            Assert.AreEqual(0.2f, _first.volume);
            Assert.AreEqual(0.8f, _second.volume);
        }

        [TestCase("2147483647", "target_not_found")]
        [TestCase("2147483648", "invalid_target")]
        public void NumericNamesRequireExplicitNameSearch(string name, string expected)
        {
            _target.name = name;
            JObject request = Write("set_property");
            request["target"] = name;
            request["component_index"] = 1;
            Code(SelectedWrite(request), expected);
            Assert.AreEqual(0.8f, _second.volume);
            request["search_method"] = "by_name";
            Assert.IsTrue(SelectedWrite(request).Value<bool>("success"));
            Assert.AreEqual(0.35f, _second.volume);
        }

        [TestCase("unityengine.audiosource")]
        [TestCase("NoSuchComponent_Edit02")]
        [TestCase("System.String")]
        public void CanonicalComponentTypeValidationMatchesReadAndWrite(string type)
        {
            JObject write = Write("add"),
                read = Read();
            write["componentType"] = read["componentName"] = type;
            Code(SelectedWrite(write), "invalid_component_type");
            Code(SelectedRead(read), "invalid_component_type");
            Assert.AreEqual(2, _target.GetComponents<AudioSource>().Length);
        }

        [Test]
        public void BaseTypeIndicesUseRequestedTypeArrayOnReadAndWrite()
        {
            BoxCollider box = _target.AddComponent<BoxCollider>();
            SphereCollider sphere = _target.AddComponent<SphereCollider>();
            JObject write = Write("set_property"),
                read = Read();
            write["componentType"] = read["componentName"] = "UnityEngine.Collider";
            Code(SelectedWrite(write), "ambiguous_component");
            Code(SelectedRead(read), "ambiguous_component");
            write["component_index"] = read["component_index"] = 1;
            write["property"] = "isTrigger";
            write["value"] = true;
            Assert.IsTrue(SelectedWrite(write).Value<bool>("success"));
            Assert.IsFalse(box.isTrigger);
            Assert.IsTrue(sphere.isTrigger);
            JObject response = SelectedRead(read);
            Assert.AreEqual(sphere.GetInstanceIDCompat(), response.SelectToken("data.component.instanceID").Value<int>());
            Assert.AreEqual("UnityEngine.Collider", response.SelectToken("data.selection.requestedComponentType").Value<string>());
            Assert.AreEqual("UnityEngine.SphereCollider", response.SelectToken("data.selection.actualComponentType").Value<string>());
        }

        [TestCase("add")]
        [TestCase("set_property")]
        public void PartialPropertyFailureRetainsChosenIdentityAndSuccessfulWrites(string action)
        {
            JObject request = Write(action);
            request.Remove("property");
            request.Remove("value");
            request["properties"] = new JObject { ["volume"] = 0.45f, ["NoSuchProperty_Edit02"] = 2 };
            if (action == "set_property")
                request["component_index"] = 1;
            JObject response = SelectedWrite(request);
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.IsNotEmpty((JArray)response.SelectToken("data.errors"));
            int id = response.SelectToken("data.selection.componentInstanceID").Value<int>();
            AudioSource actual = UnityObjectIdCompat.InstanceIDToObjectCompat(id) as AudioSource;
            Assert.IsNotNull(actual);
            Assert.AreEqual(0.45f, actual.volume);
            Assert.AreEqual(0.2f, _first.volume);
            Assert.AreEqual(action == "add" ? 3 : 2, _target.GetComponents<AudioSource>().Length);
            if (action == "set_property")
                Assert.AreSame(_second, actual);
        }

        [Test]
        public void ExplicitIdsRejectPersistentAndUnsupportedPreviewObjects()
        {
            GameObject asset = PrefabUtility.SaveAsPrefabAsset(_target, _assetRoot + "/Selection.prefab");
            Assert.IsNotNull(asset);
            AssertRejectedTarget(asset);
            Scene preview = EditorSceneManager.NewPreviewScene();
            try
            {
                GameObject previewObject = Create("PreviewOnly", preview);
                previewObject.AddComponent<AudioSource>();
                AssertRejectedTarget(previewObject);
            }
            finally
            {
                EditorSceneManager.ClosePreviewScene(preview);
            }
        }

        [Test]
        public void DuplicatePathsRejectWhileUniquePathSelectsExactlyOneObject()
        {
            GameObject firstRoot = Create("Parent", _scene),
                secondRoot = Create("Parent", _scene);
            GameObject firstChild = Create("Child", _scene),
                secondChild = Create("Child", _scene);
            firstChild.transform.SetParent(firstRoot.transform);
            secondChild.transform.SetParent(secondRoot.transform);
            AudioSource first = firstChild.AddComponent<AudioSource>(),
                second = secondChild.AddComponent<AudioSource>();
            secondChild.SetActive(false);
            JObject request = Write("set_property");
            request["target"] = "Parent/Child";
            request["search_method"] = "by_path";
            JObject response = SelectedWrite(request);
            Code(response, "ambiguous_target");
            Assert.AreEqual(2, response.SelectToken("data.matchCountLowerBound").Value<int>());
            Assert.IsFalse(response.SelectToken("data.countExact").Value<bool>());
            Assert.AreEqual(1f, first.volume);
            Assert.AreEqual(1f, second.volume);
            secondRoot.name = "UniqueParent";
            request["target"] = "UniqueParent/Child";
            Assert.IsTrue(SelectedWrite(request).Value<bool>("success"));
            Assert.AreEqual(1f, first.volume);
            Assert.AreEqual(0.35f, second.volume);
        }

        [Test]
        public void CurrentPrefabStageOwnsNameScopeAndAcceptsExplicitReadIds()
        {
            string path = _assetRoot + "/StageSelection.prefab";
            Assert.IsNotNull(PrefabUtility.SaveAsPrefabAsset(_target, path));
            Assert.IsTrue(EditorSceneManager.SaveScene(_scene));
            var stage = PrefabStageUtility.OpenPrefab(path);
            Assert.IsNotNull(stage);
            try
            {
                GameObject root = stage.prefabContentsRoot;
                Assert.AreNotEqual(_target.GetInstanceIDCompat(), root.GetInstanceIDCompat());
                JObject write = Write("set_property");
                write["target"] = root.name;
                write["component_index"] = 1;
                JObject response = SelectedWrite(write);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(root.GetInstanceIDCompat(), response.SelectToken("data.selection.gameObjectInstanceID").Value<int>());
                Assert.AreEqual(0.8f, _second.volume, "The same-name main-stage object is outside the stage name scope.");
                JObject read = Read();
                read["instanceID"] = root.GetInstanceIDCompat();
                read["component_index"] = 1;
                Assert.AreEqual(
                    root.GetComponents<AudioSource>()[1].GetInstanceIDCompat(),
                    SelectedRead(read).SelectToken("data.component.instanceID").Value<int>()
                );
            }
            finally
            {
                Assert.AreEqual(path, stage.assetPath);
                stage.ClearDirtiness();
                StageUtility.GoToMainStage();
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SelectedAliasHonorsDisabledParentBeforeLookupOrMutation(bool read)
        {
            Assert.IsNotNull(ResourceDiscoveryField);
            object previousResource = ResourceDiscoveryField.GetValue(null);
            var resources = new SelectionResourceDiscovery { ParentEnabled = false };
            _toolDiscovery.ParentEnabled = false;
            Type helper = typeof(ManageComponents).Assembly.GetType("MCPForUnity.Editor.Helpers.ComponentSelection");
            FieldInfo observer = helper.GetField("BeforeTargetLookupForTests", BindingFlags.NonPublic | BindingFlags.Static);
            object previousObserver = observer?.GetValue(null);
            int lookups = 0;
            observer?.SetValue(null, (Action)(() => lookups++));
            MCPServiceLocator.Register<IResourceDiscoveryService>(resources);
            var counter = _target.AddComponent<ResourceReadCounter>();
            Assert.IsTrue(EditorSceneManager.SaveScene(_scene));
            try
            {
                Assert.IsTrue(_toolDiscovery.IsToolEnabled("manage_components_selected"));
                Assert.IsTrue(resources.IsResourceEnabled("get_gameobject_component_selected"));
                JObject request = read ? Read() : Write("set_property");
                if (read)
                    request["componentName"] = typeof(ResourceReadCounter).FullName;
                else
                    request["component_index"] = 1;
                JObject response = read ? SelectedRead(request) : SelectedWrite(request);
                Code(response, read ? "resource_disabled" : "tool_disabled");
                Assert.IsNotNull(observer, "The observer counts the real selected target-lookup boundary.");
                Assert.AreEqual(0, lookups);
                Assert.AreEqual(0, counter.GetterCalls);
                Assert.AreEqual(0.2f, _first.volume);
                Assert.AreEqual(0.8f, _second.volume);
                Assert.IsFalse(_scene.isDirty);
                // Enable only the original capability; the same native selected entry now executes.
                _toolDiscovery.ParentEnabled = resources.ParentEnabled = true;
                response = read ? SelectedRead(request) : SelectedWrite(request);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(1, lookups);
                if (read)
                    Assert.Greater(counter.GetterCalls, 0);
                else
                    Assert.AreEqual(0.35f, _second.volume);
            }
            finally
            {
                observer?.SetValue(null, previousObserver);
                ResourceDiscoveryField.SetValue(null, previousResource);
                _toolDiscovery.ParentEnabled = true;
            }
        }

        [TestCase(false, true, true)]
        [TestCase(true, true, true)]
        [TestCase(false, false, true)]
        [TestCase(true, false, true)]
        [TestCase(false, true, false)]
        [TestCase(true, true, false)]
        public void RealDispatcherHonorsInjectedAliasAndParentPolicy(bool read, bool parentEnabled, bool aliasEnabled)
        {
            object previousResource = ResourceDiscoveryField.GetValue(null);
            var resources = new SelectionResourceDiscovery { ParentEnabled = parentEnabled, AliasEnabled = aliasEnabled };
            _toolDiscovery.ParentEnabled = parentEnabled;
            _toolDiscovery.AliasEnabled = aliasEnabled;
            MCPServiceLocator.Register<IResourceDiscoveryService>(resources);
            Type helper = typeof(ManageComponents).Assembly.GetType("MCPForUnity.Editor.Helpers.ComponentSelection");
            FieldInfo observer = helper.GetField("BeforeTargetLookupForTests", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(observer);
            object previousObserver = observer.GetValue(null);
            int lookups = 0;
            observer.SetValue(null, (Action)(() => lookups++));
            var firstCounter = _target.AddComponent<ResourceReadCounter>();
            var secondCounter = _target.AddComponent<ResourceReadCounter>();
            Assert.IsTrue(EditorSceneManager.SaveScene(_scene));
            using var lifetime = new CancellationTokenSource();
            try
            {
                JObject request = read ? Read() : Write("set_property");
                request["component_index"] = 1;
                if (read)
                    request["componentName"] = typeof(ResourceReadCounter).FullName;
                string command = read ? "get_gameobject_component_selected" : "manage_components_selected";
                var pending = TransportCommandDispatcher.ExecuteCommandJsonAsync(
                    new JObject { ["type"] = command, ["params"] = request }.ToString(),
                    lifetime.Token
                );
                Assert.IsTrue(pending.IsCompleted, "A synchronous selected command should complete during the real main-thread dispatcher pump.");
                JObject envelope = JObject.Parse(pending.GetAwaiter().GetResult());
                if (!aliasEnabled)
                {
                    Assert.AreEqual("error", envelope.Value<string>("status"), envelope.ToString());
                    StringAssert.Contains("disabled", envelope.ToString());
                }
                else
                {
                    Assert.AreEqual("success", envelope.Value<string>("status"), envelope.ToString());
                    JObject response = (JObject)envelope["result"];
                    if (!parentEnabled)
                        Code(response, read ? "resource_disabled" : "tool_disabled");
                    else
                    {
                        Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                        int expected = read ? secondCounter.GetInstanceIDCompat() : _second.GetInstanceIDCompat();
                        Assert.AreEqual(expected, response.SelectToken("data.selection.componentInstanceID").Value<int>());
                    }
                }
                bool allowed = parentEnabled && aliasEnabled;
                Assert.AreEqual(allowed ? 1 : 0, lookups);
                Assert.AreEqual(0, firstCounter.GetterCalls);
                if (read && allowed)
                    Assert.Greater(secondCounter.GetterCalls, 0);
                else
                    Assert.AreEqual(0, secondCounter.GetterCalls);
                Assert.AreEqual(0.2f, _first.volume);
                Assert.AreEqual(!read && allowed ? 0.35f : 0.8f, _second.volume);
                if (!allowed)
                    Assert.IsFalse(_scene.isDirty);
            }
            finally
            {
                lifetime.Cancel();
                observer.SetValue(null, previousObserver);
                ResourceDiscoveryField.SetValue(null, previousResource);
                _toolDiscovery.ParentEnabled = _toolDiscovery.AliasEnabled = true;
            }
        }

        [Test]
        public void SelectedToolMetadataIsBuiltinWithoutSecondPublicRegistration()
        {
            Type alias = typeof(ManageComponentsSelected);
            var attribute = alias.GetCustomAttribute<McpForUnityToolAttribute>();
            var parent = typeof(ManageComponents).GetCustomAttribute<McpForUnityToolAttribute>();
            MethodInfo extract = typeof(ToolDiscoveryService).GetMethod("ExtractToolMetadata", BindingFlags.NonPublic | BindingFlags.Instance);
            // Pure production metadata extraction; deliberately do not discover/initialize preferences.
            var metadata = (ToolMetadata)extract.Invoke(new ToolDiscoveryService(), new object[] { alias, attribute });
            Assert.AreEqual("manage_components_selected", metadata.Name);
            Assert.IsFalse(metadata.AutoRegister);
            Assert.IsTrue(metadata.IsBuiltIn);
            Assert.AreEqual(parent.RequiresExplicitConsent, metadata.RequiresExplicitConsent);
            Assert.AreEqual(parent.Group, metadata.Group);
            Assert.AreEqual(parent.RequiresPolling, metadata.RequiresPolling);
        }

        private sealed class SelectionToolDiscovery : IToolDiscoveryService
        {
            internal bool ParentEnabled = true;
            internal bool AliasEnabled = true;

            public bool IsToolEnabled(string name) => name == "manage_components" ? ParentEnabled : name == "manage_components_selected" && AliasEnabled;

            public ToolMetadata GetToolMetadata(string name) =>
                name == "manage_components" || name == "manage_components_selected" ? new ToolMetadata { Name = name } : null;

            public List<ToolMetadata> DiscoverAllTools() => new List<ToolMetadata>();

            public List<ToolMetadata> GetEnabledTools() => new List<ToolMetadata>();

            public void SetToolEnabled(string name, bool enabled) => throw new InvalidOperationException("Tests must not write preferences.");

            public void InvalidateCache() { }
        }

        private sealed class SelectionResourceDiscovery : IResourceDiscoveryService
        {
            internal bool ParentEnabled = true;
            internal bool AliasEnabled = true;

            public bool IsResourceEnabled(string name) =>
                name == "get_gameobject_component" ? ParentEnabled : name == "get_gameobject_component_selected" && AliasEnabled;

            public ResourceMetadata GetResourceMetadata(string name) =>
                name == "get_gameobject_component" || name == "get_gameobject_component_selected" ? new ResourceMetadata { Name = name } : null;

            public List<ResourceMetadata> DiscoverAllResources() => new List<ResourceMetadata>();

            public List<ResourceMetadata> GetEnabledResources() => new List<ResourceMetadata>();

            public void SetResourceEnabled(string name, bool enabled) => throw new InvalidOperationException("Tests must not write preferences.");

            public void InvalidateCache() { }
        }

        private void AssertRejectedTarget(GameObject target)
        {
            JObject write = Write("add"),
                read = Read();
            write["target"] = read["instanceID"] = target.GetInstanceIDCompat();
            Code(SelectedWrite(write), "invalid_target");
            Code(SelectedRead(read), "invalid_target");
        }

        private JObject Write(string action) =>
            new JObject
            {
                ["action"] = action,
                ["target"] = _target.GetInstanceIDCompat(),
                ["componentType"] = "UnityEngine.AudioSource",
                ["strict_selection"] = true,
                ["property"] = "volume",
                ["value"] = 0.35f,
            };

        private JObject Read() => new JObject { ["instanceID"] = _target.GetInstanceIDCompat(), ["componentName"] = "UnityEngine.AudioSource" };

        private static GameObject Create(string name, Scene scene)
        {
            var go = new GameObject(name);
            SceneManager.MoveGameObjectToScene(go, scene);
            return go;
        }

        private static JObject Json(object value) => JObject.FromObject(value);

        private static JObject SelectedWrite(JObject request) => Json(CommandRegistry.GetHandler("manage_components_selected")(request));

        private static JObject SelectedRead(JObject request) => Json(CommandRegistry.GetHandler("get_gameobject_component_selected")(request));

        private static void Code(JObject response, string expected)
        {
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(expected, response.Value<string>("code"), response.ToString());
            Assert.AreEqual(expected, response.SelectToken("data.reason").Value<string>());
        }
    }
}
