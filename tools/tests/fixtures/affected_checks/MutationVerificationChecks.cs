using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.SceneManagement;
using UnityEngine.UI;
using Object = UnityEngine.Object;

namespace VerificationFixtures
{
    internal static class MutationFixture
    {
        private const string Root = "Assets/__McpMutationChecks";
        private const string Installed = "Assets/VerificationMutationTests";
        private const string ScenePath = Root + "/Checkout.unity";
        private const string CatalogPath = Root + "/Resources/McpMutationCatalog.txt";
        private const string PrimaryKeyPath = Root + "/PrimaryKey.txt";
        private const string FallbackKeyPath = Root + "/FallbackKey.txt";
        private const string SceneKeyPath = Root + "/SceneKey.txt";
        private const string PointerRoutePath = Root + "/PointerRoute.txt";
        private const string Anchor = "CheckoutAnchor";
        private const string CatalogKey = "McpMutationCatalog";
        public const string CatalogText = "catalog-fixture-v1";

        public static readonly string[] Inputs =
        {
            Installed + "/MutationVerificationChecks.cs",
            Installed + "/VerificationMutationTests.asmdef",
            ScenePath,
            CatalogPath,
            PrimaryKeyPath,
            FallbackKeyPath,
            SceneKeyPath,
            PointerRoutePath,
        };

        public static void CreateBaseline()
        {
            if (Directory.Exists(Root))
                return;

            Directory.CreateDirectory(Root + "/Resources");
            File.WriteAllText(CatalogPath, CatalogText);
            File.WriteAllText(PrimaryKeyPath, CatalogKey);
            File.WriteAllText(FallbackKeyPath, CatalogKey);
            File.WriteAllText(SceneKeyPath, "Checkout");
            File.WriteAllText(PointerRoutePath, "direct");

            Scene previous = SceneManager.GetActiveScene();
            Scene fixture = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                var anchor = new GameObject(Anchor);
                if (anchor.scene != fixture)
                    SceneManager.MoveGameObjectToScene(anchor, fixture);
                Assert.That(EditorSceneManager.SaveScene(fixture, ScenePath), Is.True);
            }
            finally
            {
                EditorSceneManager.CloseScene(fixture, true);
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
            }
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        }

        public static TextAsset LoadCatalog(bool useFallback = false)
        {
            string key = File.ReadAllText(useFallback ? FallbackKeyPath : PrimaryKeyPath).Trim();
            return Resources.Load<TextAsset>(key);
        }

        public static string ResolveCheckoutAnchor()
        {
            string key = File.ReadAllText(SceneKeyPath).Trim();
            string path = Root + "/" + key + ".unity";
            if (key != Path.GetFileName(key) || AssetDatabase.LoadAssetAtPath<SceneAsset>(path) == null)
                return null;

            Scene previous = SceneManager.GetActiveScene();
            Scene scene = SceneManager.GetSceneByPath(path);
            bool opened = !scene.isLoaded;
            if (opened)
                scene = EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
            try
            {
                return scene.GetRootGameObjects().FirstOrDefault(root => root.name == Anchor)?.name;
            }
            finally
            {
                if (opened)
                    EditorSceneManager.CloseScene(scene, true);
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
            }
        }

        public static (int clicks, string receiver) DeliverPointerClick()
        {
            var owner = new GameObject("FixturePurchaseButton", typeof(RectTransform), typeof(Button));
            GameObject ownedEventSystem = null;
            try
            {
                var label = new GameObject("FixturePurchaseLabel", typeof(RectTransform));
                label.transform.SetParent(owner.transform, false);
                if (File.ReadAllText(PointerRoutePath).Trim() == "intercept")
                    label.AddComponent<EventTrigger>();
                int clicks = 0;
                owner.GetComponent<Button>().onClick.AddListener(() => clicks++);
                EventSystem events = EventSystem.current;
                if (events == null)
                {
                    ownedEventSystem = new GameObject("FixtureEventSystem", typeof(EventSystem));
                    events = ownedEventSystem.GetComponent<EventSystem>();
                }
                var pointer = new PointerEventData(events) { button = PointerEventData.InputButton.Left };
                var receiver = ExecuteEvents.ExecuteHierarchy(label, pointer, ExecuteEvents.pointerClickHandler);
                return (clicks, receiver != null ? receiver.name : null);
            }
            finally
            {
                Object.DestroyImmediate(owner);
                if (ownedEventSystem != null)
                    Object.DestroyImmediate(ownedEventSystem);
            }
        }
    }

    public abstract class MutationRun
    {
        private Guid executionId;
        protected abstract string[] ExpectedTests { get; }

        [OneTimeSetUp]
        public void Prepare()
        {
            if (Environment.GetEnvironmentVariable("UNITY_MCP_MUTATION_FIXTURE") != "1")
                Assert.Ignore("Enable this fixture in an owned disposable Unity project.");
            executionId = Guid.NewGuid();
            MutationFixture.CreateBaseline();
        }

        [SetUp]
        public void IdentifyInputs()
        {
            foreach (string path in MutationFixture.Inputs)
            {
                using var hash = SHA256.Create();
                string digest = BitConverter.ToString(hash.ComputeHash(File.ReadAllBytes(path))).Replace("-", "").ToLowerInvariant();
                TestContext.WriteLine($"{path} {AssetDatabase.AssetPathToGUID(path)} {digest}");
            }
            TestContext.WriteLine($"MCP_VERIFICATION_EXECUTION {executionId:N}");
            TestContext.WriteLine(
                $"MCP_VERIFICATION_CONTEXT {Application.unityVersion}|{EditorUserBuildSettings.activeBuildTarget}|EditMode|{SystemInfo.operatingSystemFamily}"
            );
            foreach (string test in ExpectedTests)
                TestContext.WriteLine($"MCP_VERIFICATION_EXPECTED_CASE {GetType().FullName}.{test}");
        }

        protected static void Require(bool observed, string fault, string message)
        {
            if (!observed)
                Assert.Fail($"MCP_MUTATION_WITNESS {fault}\n{message}");
        }
    }

    public sealed class MutationSuite : MutationRun
    {
        protected override string[] ExpectedTests => new[] { nameof(PrimaryCatalogLoads), nameof(SceneTargetResolves), nameof(PurchaseReceivesClick) };

        [Test]
        public void PrimaryCatalogLoads() =>
            Require(MutationFixture.LoadCatalog()?.text == MutationFixture.CatalogText, "loader-key", "Resources must return the expected catalog.");

        [Test]
        public void SceneTargetResolves() =>
            Require(MutationFixture.ResolveCheckoutAnchor() != null, "scene-reference", "The configured scene must contain a checkout anchor.");

        [Test]
        public void PurchaseReceivesClick() =>
            Require(MutationFixture.DeliverPointerClick().clicks == 1, "routed-input", "A click on the label must reach the purchase button exactly once.");
    }

    public sealed class MutationWitnesses : MutationRun
    {
        protected override string[] ExpectedTests =>
            new[] { nameof(PrimaryCatalogResource), nameof(CheckoutSceneAnchor), nameof(PurchaseRoute), nameof(FallbackCatalogResource) };

        [Test]
        public void PrimaryCatalogResource() =>
            Require(MutationFixture.LoadCatalog()?.name == "McpMutationCatalog", "loader-key", "The configured primary key must resolve the catalog asset.");

        [Test]
        public void CheckoutSceneAnchor() =>
            Require(MutationFixture.ResolveCheckoutAnchor() == "CheckoutAnchor", "scene-reference", "The configured Unity scene must expose CheckoutAnchor.");

        [Test]
        public void PurchaseRoute() =>
            Require(
                MutationFixture.DeliverPointerClick().receiver == "FixturePurchaseButton",
                "routed-input",
                "ExecuteHierarchy must route the click to the button."
            );

        [Test]
        public void FallbackCatalogResource() =>
            Require(
                MutationFixture.LoadCatalog(useFallback: true)?.text == MutationFixture.CatalogText,
                "fallback-loader-key",
                "The fallback key must load the expected catalog."
            );
    }
}
