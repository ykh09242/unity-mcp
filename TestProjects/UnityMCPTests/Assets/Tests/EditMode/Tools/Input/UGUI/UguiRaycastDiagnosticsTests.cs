#if MCP_INPUT_UGUI
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using UnityEngine.UI;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public sealed class Ui09FilterProbe : MonoBehaviour, ICanvasRaycastFilter
    {
        public int Calls;
        public bool Accept = true,
            Throw,
            ChangeState,
            Touched;

        public bool IsRaycastLocationValid(Vector2 point, Camera camera)
        {
            Calls++;
            if (ChangeState)
            {
                Touched = true;
                gameObject.name = "UI09UserChanged";
            }
            if (Throw)
                throw new InvalidOperationException("UI09_PRIVATE_FILTER_FAILURE");
            return Accept;
        }
    }

    public sealed class Ui09EventProbe : MonoBehaviour, IPointerClickHandler, IPointerDownHandler, ISelectHandler
    {
        public int Calls;

        public void OnPointerClick(PointerEventData data) => Calls++;

        public void OnPointerDown(PointerEventData data) => Calls++;

        public void OnSelect(BaseEventData data) => Calls++;
    }

    public sealed class Ui09ModuleProbe : BaseInputModule
    {
        public int Processes;

        public override bool IsModuleSupported() => true;

        public override bool ShouldActivateModule() => true;

        public override void Process() => Processes++;
    }

    public sealed class Ui09UnknownRaycaster : BaseRaycaster
    {
        public int Calls;
        public override Camera eventCamera
        {
            get
            {
                Calls++;
                return null;
            }
        }

        public override bool IsActive()
        {
            Calls++;
            return base.IsActive();
        }

        public override void Raycast(PointerEventData data, List<RaycastResult> results) => Calls++;
    }

    public sealed class Ui09UnknownGraphic : Image
    {
        public int Calls;

        public override bool Raycast(Vector2 point, Camera camera)
        {
            Calls++;
            return true;
        }
    }

    /// <summary>Owned native scenarios only. Source proposal: no RED/GREEN result has been executed.</summary>
    [Parallelizable(ParallelScope.None)]
    public class UguiRaycastDiagnosticsTests
    {
        private GameObject canvas,
            events,
            button,
            cover;
        private Ui09EventProbe eventProbe;
        private List<UnityEngine.UI.Graphic> throwingScratch;
        private UnityEngine.UI.Graphic throwingButtonGraphic,
            throwingCoverGraphic;
        private bool ownsPlay;
        private string exportRoot;
        private Scene ownedScene,
            previousActiveScene;
        private string ownedSceneName;
        private Scene[] preservedScenes;
        private string[] preservedScenePaths,
            preservedSceneNames;
        private int[][] preservedRootIds;
        private static readonly HashSet<string> ExportNames = new HashSet<string>(StringComparer.Ordinal)
        {
            "ui09-public-action.json",
            "ui09-sorting.json",
            "ui09-transparent-graphic.json",
            "ui09-transparent-group.json",
            "ui09-default-filter.json",
            "ui09-accept-filter.json",
            "ui09-reject-filter.json",
            "ui09-throw-filter.json",
            "ui09-state-filter.json",
            "ui09-module-absent.json",
            "ui09-module-present.json",
            "ui09-unsupported-raycaster.json",
            "ui09-unsupported-graphic.json",
            "ui09-budget-max_evaluations.json",
            "ui09-budget-max_nodes.json",
            "ui09-budget-max_depth.json",
            "ui09-budget-max_characters.json",
            "ui09-missing-events.json",
            "ui09-invalid-request.json",
            "ui09-ambiguous-events.json",
            "ui09-maximum-response.json",
            "ui09-handler-target-control.json",
            "ui09-handler-target-deep.json",
            "ui09-handler-sorting-control.json",
            "ui09-handler-sorting-deep.json",
        };

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            ownsPlay = false;
            Assert.IsFalse(EditorApplication.isPlayingOrWillChangePlaymode, "Fresh isolated Edit Mode is required before the owned transition.");
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                Assert.IsFalse(scene.isDirty, "Existing scene changes must be preserved.");
            }
            exportRoot = Environment.GetEnvironmentVariable("UNITY_MCP_UI09_EXPORT_ROOT");
            Assert.IsFalse(string.IsNullOrEmpty(exportRoot), "QA must bind an owned export root before launch.");
            exportRoot = Path.GetFullPath(exportRoot);
            Assert.IsTrue(
                string.Equals(exportRoot, Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "exports")), StringComparison.OrdinalIgnoreCase)
            );
            Assert.IsTrue(Directory.Exists(exportRoot));
            Assert.AreEqual(0, (int)(File.GetAttributes(exportRoot) & FileAttributes.ReparsePoint));
            yield return new EnterPlayMode();
            ownsPlay = true;
            exportRoot = Path.GetFullPath(Environment.GetEnvironmentVariable("UNITY_MCP_UI09_EXPORT_ROOT"));
            Assert.IsTrue(Application.isPlaying);
            CapturePreservedScenes();
            previousActiveScene = SceneManager.GetActiveScene();
            ownedSceneName = "UI09Owned-" + Guid.NewGuid().ToString("N");
            ownedScene = SceneManager.CreateScene(ownedSceneName);
            Assert.IsTrue(ownedScene.IsValid() && ownedScene.isLoaded);
            Assert.AreEqual(string.Empty, ownedScene.path, "The fixture creates no scene asset.");
            Assert.AreEqual(0, ownedScene.rootCount, "UI09 requires an empty owned case scene; never delete user objects to satisfy this prerequisite.");
            Assert.IsTrue(SceneManager.SetActiveScene(ownedScene));
            Assert.IsNull(EventSystem.current, "No existing EventSystem may be replaced.");
            Assert.AreEqual(0, RegisteredRaycasters().Count, "All query participants must be owned by this fixture.");
            canvas = new GameObject("UI09Canvas", typeof(RectTransform), typeof(Canvas), typeof(GraphicRaycaster));
            canvas.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            events = new GameObject("UI09Events", typeof(EventSystem));
            button = Graphic("UI09Button", canvas.transform);
            button.AddComponent<Button>();
            eventProbe = button.AddComponent<Ui09EventProbe>();
            cover = Graphic("UI09Cover", canvas.transform);
            yield return null;
            Canvas.ForceUpdateCanvases();
            Assert.GreaterOrEqual(cover.GetComponent<Image>().depth, 0, "Native canvas warm-up is a fixture prerequisite, before observation.");
        }

        [UnityTearDown]
        public IEnumerator TearDown()
        {
            if (!ownsPlay)
                yield break;
            Exception cleanupError = null;
            if (throwingScratch != null)
            {
                try
                {
                    for (int i = throwingScratch.Count - 1; i >= 0; i--)
                    {
                        if (ReferenceEquals(throwingScratch[i], throwingButtonGraphic) || ReferenceEquals(throwingScratch[i], throwingCoverGraphic))
                            throwingScratch.RemoveAt(i);
                    }
                    Assert.AreEqual(
                        0,
                        throwingScratch.Count,
                        "The throwing fixture must restore its empty scratch baseline; foreign entries are never removed."
                    );
                }
                catch (Exception error)
                {
                    cleanupError = error;
                }
                finally
                {
                    throwingScratch = null;
                    throwingButtonGraphic = null;
                    throwingCoverGraphic = null;
                }
            }
            try
            {
                UnityEngine.Object.DestroyImmediate(canvas);
                UnityEngine.Object.DestroyImmediate(events);
                Assert.IsNull(EventSystem.current);
                Assert.AreEqual(0, RegisteredRaycasters().Count);
            }
            catch (Exception error)
            {
                cleanupError = cleanupError == null ? error : new AggregateException(cleanupError, error);
            }
            AsyncOperation unload = null;
            try
            {
                if (ownedScene.IsValid())
                {
                    Assert.AreEqual(ownedSceneName, ownedScene.name);
                    Assert.AreEqual(string.Empty, ownedScene.path);
                    Assert.AreEqual(0, ownedScene.rootCount, "Only the explicitly owned empty fixture scene may be unloaded.");
                    Assert.IsTrue(previousActiveScene.IsValid() && previousActiveScene.isLoaded);
                    Assert.IsTrue(SceneManager.SetActiveScene(previousActiveScene));
                    unload = SceneManager.UnloadSceneAsync(ownedScene);
                    Assert.IsNotNull(unload);
                }
            }
            catch (Exception error)
            {
                cleanupError = cleanupError == null ? error : new AggregateException(cleanupError, error);
            }
            if (unload != null)
                yield return unload;
            try
            {
                Assert.IsFalse(SceneManager.GetSceneByName(ownedSceneName).IsValid());
                AssertPreservedScenes();
            }
            catch (Exception error)
            {
                cleanupError = cleanupError == null ? error : new AggregateException(cleanupError, error);
            }
            ownsPlay = false;
            yield return new ExitPlayMode();
            if (cleanupError != null)
                throw cleanupError;
        }

        private void CapturePreservedScenes()
        {
            int count = SceneManager.sceneCount;
            preservedScenes = new Scene[count];
            preservedScenePaths = new string[count];
            preservedSceneNames = new string[count];
            preservedRootIds = new int[count][];
            for (int i = 0; i < count; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                preservedScenes[i] = scene;
                preservedScenePaths[i] = scene.path;
                preservedSceneNames[i] = scene.name;
                preservedRootIds[i] = scene.GetRootGameObjects().Select(item => item.GetInstanceIDCompat()).OrderBy(id => id).ToArray();
            }
        }

        private void AssertPreservedScenes()
        {
            Assert.AreEqual(preservedScenes.Length, SceneManager.sceneCount);
            for (int i = 0; i < preservedScenes.Length; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                Assert.AreEqual(preservedScenes[i].handle, scene.handle, "A preexisting scene must not be replaced or reordered.");
                Assert.AreEqual(preservedScenePaths[i], scene.path);
                Assert.AreEqual(preservedSceneNames[i], scene.name);
                Assert.IsTrue(scene.isLoaded);
                CollectionAssert.AreEqual(
                    preservedRootIds[i],
                    scene.GetRootGameObjects().Select(item => item.GetInstanceIDCompat()).OrderBy(id => id).ToArray(),
                    "Framework and other preexisting roots must remain untouched."
                );
            }
            Assert.AreEqual(previousActiveScene.handle, SceneManager.GetActiveScene().handle);
        }

        private static List<BaseRaycaster> RegisteredRaycasters()
        {
            var manager = typeof(BaseRaycaster).Assembly.GetType("UnityEngine.EventSystems.RaycasterManager", false);
            Assert.IsNotNull(manager, "The fixture requires the native raycaster registry type.");
            var method = manager.GetMethod(
                "GetRaycasters",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.DeclaredOnly,
                null,
                Type.EmptyTypes,
                null
            );
            Assert.IsNotNull(method, "The fixture requires the native zero-argument raycaster registry accessor.");
            Assert.AreEqual(manager, method.DeclaringType);
            Assert.IsTrue(method.IsStatic);
            Assert.IsFalse(method.IsGenericMethod);
            Assert.AreEqual(0, method.GetParameters().Length);
            Assert.AreEqual(typeof(List<BaseRaycaster>), method.ReturnType);
            var result = method.Invoke(null, null);
            Assert.IsNotNull(result);
            Assert.AreEqual(typeof(List<BaseRaycaster>), result.GetType());
            return (List<BaseRaycaster>)result;
        }

        private static GameObject Graphic(string name, Transform parent)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Image));
            go.transform.SetParent(parent, false);
            ((RectTransform)go.transform).sizeDelta = new Vector2(160, 100);
            return go;
        }

        private Vector2 Point() =>
            RectTransformUtility.WorldToScreenPoint(null, ((RectTransform)button.transform).TransformPoint(((RectTransform)button.transform).rect.center));

        private JObject Request(bool allow = false)
        {
            Vector2 point = Point();
            return new JObject
            {
                ["action"] = "diagnose_raycast",
                ["target"] = button.GetInstanceIDCompat(),
                ["screen_position"] = new JArray(point.x, point.y),
                ["allow_custom_filters"] = allow,
            };
        }

        private JObject Observe(JObject request = null, GameObject observedTarget = null)
        {
            var type = typeof(ManageUGUI).Assembly.GetType("MCPForUnity.Editor.Tools.UguiRaycastDiagnostics", true);
            var method = type.GetMethod("Diagnose", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            var selection = EventSystem.current == null ? null : EventSystem.current.currentSelectedGameObject;
            int calls = eventProbe.Calls;
            bool canvasDirty = EditorUtility.IsDirty(canvas),
                buttonDirty = EditorUtility.IsDirty(button);
            GameObject target = observedTarget == null ? button : observedTarget;
            JObject admittedRequest = request ?? Request();
            if (request == null && target != button)
                admittedRequest["target"] = target.GetInstanceIDCompat();
            var result = (JObject)method.Invoke(null, new object[] { target, admittedRequest });
            Assert.AreEqual(calls, eventProbe.Calls, "No package pointer or selection dispatch.");
            if (EventSystem.current != null)
                Assert.AreSame(selection, EventSystem.current.currentSelectedGameObject);
            Assert.AreEqual(canvasDirty, EditorUtility.IsDirty(canvas));
            Assert.AreEqual(buttonDirty, EditorUtility.IsDirty(button));
            return result;
        }

        private List<RaycastResult> Oracle()
        {
            var hits = new List<RaycastResult>();
            EventSystem.current.RaycastAll(new PointerEventData(EventSystem.current) { position = Point() }, hits);
            return hits;
        }

        private void EqualNativeOrder(JObject result)
        {
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("observed", (string)result["data"]["status"], result.ToString());
            CollectionAssert.AreEqual(
                Oracle().Select(h => h.gameObject.GetInstanceIDCompat()).ToArray(),
                result["data"]["hits"].Select(h => h.Value<int>("game_object_id")).ToArray()
            );
        }

        private void Export(string name, JObject reply)
        {
            Assert.IsTrue(ExportNames.Contains(name));
            byte[] bytes = new UTF8Encoding(false, true).GetBytes(reply.ToString(Formatting.None));
            using (var output = new FileStream(Path.Combine(exportRoot, name), FileMode.CreateNew, FileAccess.Write, FileShare.None))
                output.Write(bytes, 0, bytes.Length);
        }

        [Test]
        public void PublicActionRequiresDeferredSharedIntegration()
        {
            // UNRUN shared integration assertions; all targets and metadata participants are owned.
            var direct = Observe();
            var reply = JObject.FromObject(ManageUGUI.HandleCommand(Request()));
            Assert.IsTrue(reply.Value<bool>("success"), reply.ToString());
            Assert.AreEqual("ugui_raycast_diagnostics", (string)reply["data"]["kind"]);
            int resolverWork = reply["data"]["budget"].Value<int>("evaluations") - direct["data"]["budget"].Value<int>("evaluations");
            Assert.Greater(resolverWork, 0, "Public work includes target metadata instead of resetting a second full budget.");
            var exhaustedRequest = Request();
            exhaustedRequest["max_evaluations"] = resolverWork + 1;
            var exhausted = JObject.FromObject(ManageUGUI.HandleCommand(exhaustedRequest));
            Assert.AreEqual("unknown", (string)exhausted["data"]["status"], exhausted.ToString());
            Assert.AreEqual("max_evaluations", (string)exhausted["data"]["reason"]);
            Assert.AreEqual(resolverWork + 1, exhausted["data"]["budget"].Value<int>("max_evaluations"));
            Assert.LessOrEqual(exhausted["data"]["budget"].Value<int>("evaluations"), resolverWork + 1);
            Assert.IsFalse(exhausted["data"]["policy"].Value<bool>("native_query_attempted"));
            var namedRequest = Request();
            namedRequest["target"] = button.name;
            var named = JObject.FromObject(ManageUGUI.HandleCommand(namedRequest));
            Assert.IsTrue(named.Value<bool>("success"), named.ToString());
            Assert.AreEqual(button.GetInstanceIDCompat(), named["data"].Value<int>("target_id"));
            Assert.AreEqual(canvas.transform, button.transform.parent, "The target exists only below the owned canvas root.");
            var rootedRequest = Request();
            rootedRequest["target"] = "/" + canvas.name + "/" + button.name;
            var rooted = JObject.FromObject(ManageUGUI.HandleCommand(rootedRequest));
            Assert.IsTrue(rooted.Value<bool>("success"), rooted.ToString());
            Assert.AreEqual(button.GetInstanceIDCompat(), rooted["data"].Value<int>("target_id"));
            foreach (string selector in new[] { "/" + button.name, "UI09MissingPublicTarget", "UI09DuplicatePublicTarget" })
            {
                if (selector == "UI09DuplicatePublicTarget")
                {
                    for (int i = 0; i < 2; i++)
                    {
                        var duplicate = new GameObject(selector, typeof(RectTransform));
                        duplicate.transform.SetParent(canvas.transform, false);
                    }
                }
                var invalidRequest = Request();
                invalidRequest["target"] = selector;
                var invalid = JObject.FromObject(ManageUGUI.HandleCommand(invalidRequest));
                Assert.IsFalse(invalid.Value<bool>("success"), invalid.ToString());
                Assert.AreEqual("invalid_raycast_request", (string)invalid["message"]);
                Assert.AreEqual(JTokenType.Null, invalid["data"].Type);
            }
            Assert.AreEqual(0, eventProbe.Calls);
            Assert.IsNull(EventSystem.current.currentSelectedGameObject);
            Export("ui09-public-action.json", reply);
        }

        [UnityTest]
        public IEnumerator NestedSortingOrderMatchesNativeRatherThanSiblingOrder()
        {
            cover.AddComponent<Canvas>().overrideSorting = true;
            cover.GetComponent<Canvas>().sortingOrder = -10;
            cover.AddComponent<GraphicRaycaster>();
            yield return null;
            Canvas.ForceUpdateCanvases();
            var reply = Observe();
            EqualNativeOrder(reply);
            Assert.AreEqual(button.GetInstanceIDCompat(), reply["data"]["hits"][0].Value<int>("game_object_id"));
            Export("ui09-sorting.json", reply);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void TransparentBlockerUsesActualCullAndHitFacts(bool group)
        {
            if (group)
                cover.AddComponent<CanvasGroup>().alpha = 0;
            else
                cover.GetComponent<Image>().color = Color.clear;
            // Deliberately use the already-generated native state; do not force layout inside observation.
            var reply = Observe();
            EqualNativeOrder(reply);
            Assert.AreEqual(Oracle()[0].gameObject.GetInstanceIDCompat(), reply["data"]["hits"][0].Value<int>("game_object_id"));
            Export(group ? "ui09-transparent-group.json" : "ui09-transparent-graphic.json", reply);
        }

        [Test]
        public void DefaultRefusesBeforeEvenDisabledCustomFilterCallbacks()
        {
            var filter = cover.AddComponent<Ui09FilterProbe>();
            filter.enabled = false;
            var reply = Observe();
            Assert.AreEqual("custom_filters_not_allowed", (string)reply["data"]["reason"]);
            Assert.IsFalse(reply["data"]["policy"].Value<bool>("native_query_attempted"));
            Assert.AreEqual(0, filter.Calls);
            Export("ui09-default-filter.json", reply);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void OptedInRejectingAndAcceptingFilterMatchesActualOrder(bool accept)
        {
            var filter = cover.AddComponent<Ui09FilterProbe>();
            filter.Accept = accept;
            var reply = Observe(Request(true));
            Assert.Greater(filter.Calls, 0, "Scenario counter proves only this owned filter ran.");
            EqualNativeOrder(reply);
            Assert.IsTrue(reply["data"]["policy"].Value<bool>("user_code_may_have_run"));
            Assert.AreEqual("not_instrumented", (string)reply["data"]["policy"]["callback_invocations"]);
            Assert.AreEqual((accept ? cover : button).GetInstanceIDCompat(), reply["data"]["hits"][0].Value<int>("game_object_id"));
            Export(accept ? "ui09-accept-filter.json" : "ui09-reject-filter.json", reply);
        }

        [Test]
        public void ThrowingFilterKeepsAttemptEvidenceWithoutExceptionEchoOrRetry()
        {
            var sortedField = typeof(GraphicRaycaster).GetField("s_SortedGraphics", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(sortedField, "The fixture must identify the exact native scratch field before its intentional throwing query.");
            var sortedGraphics = sortedField.GetValue(null) as List<UnityEngine.UI.Graphic>;
            Assert.IsNotNull(sortedGraphics, "The native scratch field must retain its exact List<Graphic> contract.");
            Assert.AreEqual(0, sortedGraphics.Count, "Unexpected preexisting scratch state must fail before the fixture query; never clear foreign state.");
            var ownedButtonGraphic = button.GetComponent<UnityEngine.UI.Graphic>();
            var ownedCoverGraphic = cover.GetComponent<UnityEngine.UI.Graphic>();
            Assert.IsTrue(ownedButtonGraphic != null && ownedCoverGraphic != null, "Capture both live fixture Graphics before the throwing query.");
            throwingButtonGraphic = ownedButtonGraphic;
            throwingCoverGraphic = ownedCoverGraphic;
            throwingScratch = sortedGraphics;
            var filter = cover.AddComponent<Ui09FilterProbe>();
            filter.Throw = true;
            var reply = Observe(Request(true));
            Assert.IsFalse(reply.Value<bool>("success"));
            Assert.AreEqual("native_query_threw", (string)reply["data"]["reason"]);
            Assert.IsTrue(reply["data"]["policy"].Value<bool>("user_code_may_have_run"));
            Assert.AreEqual(1, filter.Calls, "One owned candidate filter; no automatic query retry.");
            StringAssert.DoesNotContain("UI09_PRIVATE_FILTER_FAILURE", reply.ToString());
            Export("ui09-throw-filter.json", reply);
        }

        [Test]
        public void StateChangingFilterIsReportedWithoutRollbackPromise()
        {
            var filter = cover.AddComponent<Ui09FilterProbe>();
            filter.ChangeState = true;
            var reply = Observe(Request(true));
            Assert.IsTrue(filter.Touched);
            Assert.AreEqual("UI09UserChanged", cover.name);
            Assert.IsTrue(reply["data"]["policy"].Value<bool>("user_code_may_have_run"));
            Assert.IsFalse(reply["data"]["policy"].Value<bool>("package_explicit_source_edits"));
            Export("ui09-state-filter.json", reply);
        }

        [UnityTest]
        public IEnumerator ModulePresenceIsSeparateAndQueryDoesNotProcessIt()
        {
            var absent = Observe();
            EqualNativeOrder(absent);
            Assert.IsNull(absent["data"]["input"].Value<string>("current_module_type"));
            Export("ui09-module-absent.json", absent);
            var module = events.AddComponent<Ui09ModuleProbe>();
            yield return null;
            Assert.AreSame(module, EventSystem.current.currentInputModule);
            int before = module.Processes;
            var present = Observe();
            EqualNativeOrder(present);
            Assert.AreEqual(before, module.Processes);
            Assert.IsTrue(present["data"]["input"].Value<bool>("current_module_active"));
            Assert.AreEqual("not_attempted", (string)present["data"]["input"]["delivery"]);
            Export("ui09-module-present.json", present);
        }

        [Test]
        public void UnknownRaycasterRefusesBeforeItsIsActiveOrCameraOrRaycast()
        {
            var unknown = canvas.AddComponent<Ui09UnknownRaycaster>();
            var reply = Observe(Request(true));
            Assert.AreEqual("unsupported_raycaster", (string)reply["data"]["reason"]);
            Assert.AreEqual(0, unknown.Calls);
            Export("ui09-unsupported-raycaster.json", reply);
        }

        [UnityTest]
        public IEnumerator TmpSelectionCaretDoesNotPoisonUnrelatedButtonObservation()
        {
            Type caretType = AppDomain
                .CurrentDomain.GetAssemblies()
                .Where(a => a.GetName().Name == "Unity.TextMeshPro")
                .Select(a => a.GetType("TMPro.TMP_SelectionCaret", false))
                .FirstOrDefault(t => t != null);
            Assert.IsNotNull(caretType, "Installed TMP caret is required for this regression witness.");
            cover.GetComponent<Image>().raycastTarget = false;
            var go = new GameObject("UI09UnrelatedCaret", typeof(RectTransform), caretType);
            go.transform.SetParent(canvas.transform, false);
            ((RectTransform)go.transform).anchoredPosition = new Vector2(10000, 10000);
            var caret = go.GetComponent<UnityEngine.UI.Graphic>();
            Assert.IsTrue(caret.raycastTarget, "Exercise a registered candidate, not an inert replacement.");
            foreach (bool active in new[] { true, false, true })
            {
                go.SetActive(active);
                yield return null;
                Canvas.ForceUpdateCanvases();
                var reply = Observe();
                EqualNativeOrder(reply);
                Assert.IsTrue(reply["data"].Value<bool>("first_hit_targets_handler"), reply.ToString());
            }
            Assert.AreEqual(0, eventProbe.Calls);
        }

        [Test]
        public void UnknownGraphicOverrideRefusesBeforeUserRaycast()
        {
            var go = new GameObject("UI09CustomGraphic", typeof(RectTransform), typeof(Ui09UnknownGraphic));
            go.transform.SetParent(canvas.transform, false);
            var graphic = go.GetComponent<Ui09UnknownGraphic>();
            var reply = Observe(Request(true));
            Assert.AreEqual("unsupported_graphic", (string)reply["data"]["reason"]);
            Assert.AreEqual(0, graphic.Calls);
            Assert.AreEqual(typeof(Ui09UnknownGraphic).FullName, (string)reply["data"]["unsupported_component"]["component_type"]);
            StringAssert.EndsWith("/UI09CustomGraphic", (string)reply["data"]["unsupported_component"]["path"]);
            Assert.IsNotNull(reply["data"]["input"]["event_system_id"]);
            Assert.IsFalse(reply["data"]["policy"].Value<bool>("native_query_attempted"));
            Export("ui09-unsupported-graphic.json", reply);
        }

        [TestCase("max_evaluations", 1)]
        [TestCase("max_nodes", 1)]
        [TestCase("max_depth", 0)]
        [TestCase("max_characters", 1)]
        public void WorkBudgetRefusesBeforeOptedInCallbacks(string key, int limit)
        {
            var filter = cover.AddComponent<Ui09FilterProbe>();
            var request = Request(true);
            request[key] = limit;
            var reply = Observe(request);
            Assert.AreEqual(key, (string)reply["data"]["reason"]);
            Assert.IsFalse(reply["data"]["policy"].Value<bool>("native_query_attempted"));
            Assert.AreEqual(0, filter.Calls);
            Export("ui09-budget-" + key + ".json", reply);
        }

        [Test]
        public void InvalidAdmissionDoesNotAttemptUserQuery()
        {
            var filter = cover.AddComponent<Ui09FilterProbe>();
            JObject reply = null;
            foreach (
                JObject invalid in new[]
                {
                    new JObject { ["screen_position"] = new JArray(1), ["allow_custom_filters"] = true },
                    new JObject { ["screen_position"] = new JArray(1, 1), ["allow_custom_filters"] = "yes" },
                    new JObject { ["screen_position"] = new JArray(1, 1), ["properties"] = new JObject() },
                }
            )
            {
                reply = Observe(invalid);
                Assert.IsFalse(reply.Value<bool>("success"));
                Assert.AreEqual("invalid_raycast_request", (string)reply["message"]);
                Assert.AreEqual(0, filter.Calls);
            }
            Export("ui09-invalid-request.json", reply);
        }

        [Test]
        public void MultipleEventSystemsAreUnknownBeforeAnyQuery()
        {
            var other = new GameObject("UI09OtherEvents", typeof(EventSystem));
            other.transform.SetParent(canvas.transform, false);
            var reply = Observe();
            Assert.AreEqual("ambiguous_event_system", (string)reply["data"]["reason"]);
            Assert.IsFalse(reply["data"]["policy"].Value<bool>("native_query_attempted"));
            Export("ui09-ambiguous-events.json", reply);
        }

        [UnityTest]
        public IEnumerator MaximumUnicodeHitPrefixExportsActualLimiterInput()
        {
            for (int i = 0; i < 128; i++)
                Graphic(new string('\u754c', 254) + "\U0001F600", canvas.transform);
            yield return null;
            Canvas.ForceUpdateCanvases();
            Assert.Greater(Oracle().Count, 128, "Actual generated hit count is the positive control.");
            var reply = Observe();
            Assert.AreEqual("observed", (string)reply["data"]["status"], reply.ToString());
            Assert.AreEqual(128, ((JArray)reply["data"]["hits"]).Count);
            Assert.IsTrue(reply["data"].Value<bool>("hits_truncated"));
            Assert.Greater(reply["data"].Value<int>("hits_total"), 128);
            Assert.LessOrEqual(Encoding.UTF8.GetByteCount(reply.ToString(Formatting.None)), 2 * 1024 * 1024);
            Export("ui09-maximum-response.json", reply);
        }

        [UnityTest]
        public IEnumerator DeepNonGraphicTargetHasUnknownHandlerRelationshipWithAdmittedControl()
        {
            cover.GetComponent<Image>().raycastTarget = false;
            var shallow = new GameObject("UI09HandlerTargetControl", typeof(RectTransform));
            shallow.transform.SetParent(button.transform, false);
            yield return null;
            Canvas.ForceUpdateCanvases();
            Assert.IsNull(shallow.GetComponent<UnityEngine.UI.Graphic>());
            Assert.AreSame(button, ExecuteEvents.GetEventHandler<IPointerClickHandler>(shallow));
            var control = Observe(observedTarget: shallow);
            EqualNativeOrder(control);
            Assert.IsTrue(control["data"].Value<bool>("first_hit_targets_handler"));
            Export("ui09-handler-target-control.json", control);

            Transform parent = shallow.transform;
            for (int i = 0; i < 70; i++)
            {
                var child = new GameObject("UI09NonGraphicHandlerAncestor" + i, typeof(RectTransform));
                child.transform.SetParent(parent, false);
                parent = child.transform;
            }
            Assert.IsNull(parent.GetComponent<UnityEngine.UI.Graphic>());
            Assert.AreSame(
                button,
                ExecuteEvents.GetEventHandler<IPointerClickHandler>(parent.gameObject),
                "Owned finite oracle confirms an actual handler beyond admission."
            );
            var deep = Observe(observedTarget: parent.gameObject);
            EqualNativeOrder(deep);
            Assert.AreEqual(JTokenType.Null, deep["data"]["first_hit_targets_handler"].Type);
            Assert.AreEqual(control["data"]["budget"]["max_depth"], deep["data"]["budget"]["max_depth"], "Filter depth retains its public meaning.");
            Assert.Greater(deep["data"]["budget"].Value<int>("evaluations"), control["data"]["budget"].Value<int>("evaluations"));
            Assert.LessOrEqual(deep["data"]["budget"].Value<int>("evaluations"), deep["data"]["budget"].Value<int>("max_evaluations"));
            Assert.AreEqual(0, eventProbe.Calls);
            Export("ui09-handler-target-deep.json", deep);
        }

        [UnityTest]
        public IEnumerator HandlerAdmissionContinuesBeyondOverrideSortingWithoutChangingFilterDepth()
        {
            button.GetComponent<Image>().raycastTarget = false;
            cover.GetComponent<Image>().raycastTarget = false;
            var receiver = canvas.AddComponent<Ui09EventProbe>();
            var boundary = new GameObject("UI09HandlerSortingBoundary", typeof(RectTransform), typeof(Canvas), typeof(GraphicRaycaster));
            boundary.transform.SetParent(canvas.transform, false);
            boundary.GetComponent<Canvas>().overrideSorting = true;
            var graphic = Graphic("UI09HandlerSortingGraphic", boundary.transform);
            yield return null;
            Canvas.ForceUpdateCanvases();
            var request = Request();
            request["target"] = graphic.GetInstanceIDCompat();
            request["max_depth"] = 1;
            Assert.AreSame(canvas, ExecuteEvents.GetEventHandler<IPointerClickHandler>(graphic), "Handler lookup crosses the sorting boundary.");
            var control = Observe(request, graphic);
            EqualNativeOrder(control);
            Assert.AreEqual(graphic.GetInstanceIDCompat(), control["data"]["hits"][0].Value<int>("game_object_id"));
            Assert.IsTrue(control["data"].Value<bool>("first_hit_targets_handler"));
            Export("ui09-handler-sorting-control.json", control);

            Transform parent = canvas.transform;
            for (int i = 0; i < 70; i++)
            {
                var child = new GameObject("UI09AboveSortingBoundary" + i, typeof(RectTransform));
                child.transform.SetParent(parent, false);
                parent = child.transform;
            }
            boundary.transform.SetParent(parent, false);
            yield return null;
            Canvas.ForceUpdateCanvases();
            Assert.AreSame(canvas, ExecuteEvents.GetEventHandler<IPointerClickHandler>(graphic));
            var deep = Observe(request, graphic);
            EqualNativeOrder(deep);
            Assert.AreEqual(JTokenType.Null, deep["data"]["first_hit_targets_handler"].Type);
            Assert.AreEqual(1, deep["data"]["budget"].Value<int>("max_depth"));
            Assert.LessOrEqual(deep["data"]["budget"].Value<int>("evaluations"), deep["data"]["budget"].Value<int>("max_evaluations"));
            Assert.AreEqual(0, receiver.Calls);
            Assert.AreEqual(0, eventProbe.Calls);
            Export("ui09-handler-sorting-deep.json", deep);
        }

        [Test]
        public void MissingEventSystemIsUnknownWithoutCreatingOne()
        {
            UnityEngine.Object.DestroyImmediate(events);
            var reply = Observe();
            Assert.AreEqual("missing_event_system", (string)reply["data"]["reason"]);
            Assert.IsNull(EventSystem.current);
            Export("ui09-missing-events.json", reply);
        }
    }
}
#endif
