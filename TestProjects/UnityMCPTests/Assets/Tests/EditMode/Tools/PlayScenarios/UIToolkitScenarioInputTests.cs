using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using MCPForUnity.Runtime.PlayScenarios;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.TestTools;
using UnityEngine.UIElements;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class UIToolkitScenarioInputTests
    {
        private readonly PrefabTestSceneFixture scene = new PrefabTestSceneFixture();
        private GameObject owner;
        private UIDocument document;
        private PanelSettings settings;
        private EditorWindow window;
        private VisualElement root;
        private Button button;
        private int clicks;

        private sealed class TestWindow : EditorWindow { }

        [UnitySetUp]
        public IEnumerator Setup()
        {
            bool completed = false;
            try
            {
                // Fail before changing scenes when the profile cannot create a real Editor view.
                // This fixture still requires graphics; it never substitutes detached fake panels.
                Assert.AreNotEqual(
                    GraphicsDeviceType.Null,
                    SystemInfo.graphicsDeviceType,
                    "A graphics-enabled Editor profile is required for real UI Toolkit panel geometry."
                );
                clicks = 0;
                scene.PrepareRunnerBootstrap();
                scene.Create("UIToolkitScenario_", Guid.NewGuid().ToString("N"));
                owner = new GameObject("OwnedDocument");
                settings = ScriptableObject.CreateInstance<PanelSettings>();
                settings.themeStyleSheet = AssetDatabase.LoadAssetAtPath<ThemeStyleSheet>("Assets/UI Toolkit/UnityThemes/UnityDefaultRuntimeTheme.tss");
                Assert.IsNotNull(settings.themeStyleSheet, "Existing preseeded runtime theme is required; no theme is installed implicitly.");
                document = owner.AddComponent<UIDocument>();
                document.panelSettings = settings;
                root = document.rootVisualElement;
                Assert.IsNotNull(root);
                button = Seed(root, () => clicks++);
                window = ScriptableObject.CreateInstance<TestWindow>();
                window.position = new Rect(0, 0, 640, 480);
                window.Show();
                window.rootVisualElement.Add(root);
                // Drive the nested iterator here so its assertion failures unwind this finally.
                IEnumerator settle = Settle();
                while (settle.MoveNext())
                    yield return settle.Current;
                Assert.AreSame(root.panel, button.panel);
                completed = true;
            }
            finally
            {
                // NUnit does not guarantee TearDown after a partially failed UnitySetUp.
                if (!completed)
                    Cleanup();
            }
        }

        private Button Seed(VisualElement tree, Action callback)
        {
            tree.Clear();
            tree.style.width = 600;
            tree.style.height = 400;
            var control = new Button(callback) { name = "Activate", text = "Activate" };
            control.style.width = 200;
            control.style.height = 40;
            tree.Add(control);
            return control;
        }

        private IEnumerator Settle()
        {
            for (int index = 0; index < 6; index++)
            {
                window.Repaint();
                yield return null;
            }
            Assert.IsNotNull(root.panel, "The real UIDocument root must attach to the owned test panel.");
            Assert.Greater(button.worldBound.width, 0);
        }

        [TearDown]
        public void Cleanup()
        {
            var failures = new List<Exception>();
            void Release(Action action)
            {
                try
                {
                    action();
                }
                catch (Exception error)
                {
                    failures.Add(error);
                }
            }
            // A failed native window creation must not prevent owned object/scene cleanup.
            Release(() =>
            {
                if (window != null)
                    window.Close();
            });
            Release(() =>
            {
                if (window != null)
                    Object.DestroyImmediate(window);
            });
            Release(() =>
            {
                if (owner != null)
                    Object.DestroyImmediate(owner);
            });
            Release(() =>
            {
                if (settings != null)
                    Object.DestroyImmediate(settings);
            });
            Release(scene.Close);
            Release(scene.RestoreRunnerBootstrap);
            window = null;
            owner = null;
            settings = null;
            document = null;
            root = null;
            button = null;
            if (failures.Count != 0)
                throw new AggregateException("Owned UI Toolkit fixture cleanup failed.", failures);
        }

        private static Type Adapter()
        {
            Type type = AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(a => a.GetType("MCPForUnity.Runtime.PlayScenarios.UIToolkit.UIToolkitScenarioInputAdapter"))
                .FirstOrDefault(t => t != null);
            Assert.IsNotNull(type, "The UI Toolkit scenario input adapter is absent from this baseline.");
            return type;
        }

        private PlayScenarioObservation Activate(string name = "Activate", string path = null, string mode = "direct")
        {
            MethodInfo method = Adapter().GetMethod("Evaluate", BindingFlags.Public | BindingFlags.Static);
            Assert.IsNotNull(method);
            try
            {
                return (PlayScenarioObservation)method.Invoke(null, new object[] { owner, name, path, mode });
            }
            catch (TargetInvocationException error)
            {
                throw error.InnerException ?? error;
            }
        }

        [Test]
        public void DirectActivationDispatchesOneRealButtonCallback()
        {
            Assert.IsTrue(Activate().Ready);
            Assert.AreEqual(1, clicks);
        }

        [UnityTest]
        public IEnumerator DetachedDocumentWaitsAndAttachedPanelDispatchesOnce()
        {
            root.RemoveFromHierarchy();
            Assert.IsNull(root.panel);
            Assert.IsFalse(Activate().Ready);
            Assert.AreEqual(0, clicks);
            window.rootVisualElement.Add(root);
            yield return Settle();
            Assert.IsTrue(Activate().Ready);
            Assert.AreEqual(1, clicks);
        }

        [UnityTest]
        public IEnumerator RecreatedDocumentRootIsResolvedForTheNextActivation()
        {
            Assert.IsTrue(Activate().Ready);
            Assert.AreEqual(1, clicks);
            VisualElement originalRoot = root;
            Button originalButton = button;
            document.enabled = false;
            Assert.IsFalse(Activate().Ready);
            document.enabled = true;
            root = document.rootVisualElement;
            Assert.AreNotSame(originalRoot, root, "UIDocument must actually recreate its runtime root.");
            int replacementClicks = 0;
            button = Seed(root, () => replacementClicks++);
            window.rootVisualElement.Add(root);
            yield return Settle();
            Assert.IsTrue(Activate().Ready);
            Assert.AreEqual(1, clicks, "A stale element must never be dispatched again.");
            Assert.AreEqual(1, replacementClicks);
            Assert.AreNotSame(originalButton, button);
        }

        [Test]
        public void DuplicatePhysicalNamesRefuseBeforeDispatch()
        {
            // The strict selector includes physical control internals, as UI02/05 do.
            button.hierarchy.Add(new VisualElement { name = "Activate" });
            var error = Assert.Throws<PlayScenarioException>(() => Activate());
            Assert.AreEqual("target_ambiguous", error.Failure.Code);
            Assert.AreEqual(0, clicks);
        }

        [Test]
        public void CanonicalContentPathAndNameGuardSelectTheButton()
        {
            Assert.IsTrue(Activate("Activate", "/0").Ready);
            Assert.AreEqual(1, clicks);
            Assert.Throws<ArgumentException>(() => Activate("Wrong", "/0"));
            Assert.Throws<ArgumentException>(() => Activate("Activate", "/00"));
            Assert.AreEqual(1, clicks);
        }

        [Test]
        public void DisabledButtonWaitsWithoutDispatchAndEnabledButtonActivatesOnce()
        {
            button.SetEnabled(false);
            Assert.IsFalse(Activate().Ready);
            Assert.AreEqual(0, clicks);
            button.SetEnabled(true);
            Assert.IsTrue(Activate().Ready);
            Assert.AreEqual(1, clicks);
        }

        [Test]
        public void ToolkitRaycastBackendIsExplicitlyUnsupported()
        {
            var error = Assert.Throws<PlayScenarioException>(() => Activate(mode: "raycast"));
            Assert.AreEqual("capability_unavailable", error.Failure.Code);
            Assert.AreEqual(0, clicks);
        }

        [Test]
        public void EditorAndPlayerHostsUseTheToolkitAdapterWithoutAnUguiBackend()
        {
            var request = new JObject
            {
                ["name"] = "toolkit",
                ["action"] = "click_ui",
                ["target"] = owner.name,
                ["click_mode"] = "direct",
                ["ui_backend"] = "uitoolkit",
                ["element_name"] = "Activate",
            };
            PlayScenarioStep step = request.ToObject<PlayScenarioStep>();
            var editor = new UnityPlayScenarioHost();
            var player = new PlayScenarioPlayerHost(Array.Empty<string>());
            var prior = ManageInput.UguiBackend;
            try
            {
                ManageInput.UguiBackend = null;
                Assert.IsTrue(editor.Evaluate(step, true).Ready);
                Assert.AreEqual(1, clicks);
                Assert.IsTrue(player.Evaluate(step, true).Ready);
                Assert.AreEqual(2, clicks, "Each host must issue exactly one actual button activation.");
            }
            finally
            {
                ManageInput.UguiBackend = prior;
                editor.Release();
                player.Release();
            }
        }
    }
}
