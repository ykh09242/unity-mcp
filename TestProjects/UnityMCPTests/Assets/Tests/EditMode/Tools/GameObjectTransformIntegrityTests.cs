using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class GameObjectTransformIntegrityTests
    {
        private readonly List<GameObject> objects = new List<GameObject>();
        private Scene originalActive;
        private Scene activeScene;
        private Scene targetScene;
        private GameObject target;
        private GameObject reference;

        [SetUp]
        public void SetUp()
        {
            originalActive = SceneManager.GetActiveScene();
            activeScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            targetScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            SceneManager.SetActiveScene(targetScene);
            target = Owned("Target", targetScene);
            reference = Owned("Reference", targetScene);
            reference.transform.position = new Vector3(10, 20, 30);
            Assert.AreSame(target, ManageGameObjectCommon.FindObjectInternal(target.GetInstanceIDCompat().ToString(), "by_id"));
            Assert.AreSame(reference, ManageGameObjectCommon.FindObjectInternal(reference.GetInstanceIDCompat().ToString(), "by_id"));
            ClearOwnedSceneDirtiness(activeScene);
            ClearOwnedSceneDirtiness(targetScene);
        }

        private static void ClearOwnedSceneDirtiness(Scene scene)
        {
            var clear = typeof(EditorSceneManager).GetMethod("ClearSceneDirtiness", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(clear, "The test requires the Editor scene dirtiness reset API.");
            clear.Invoke(null, new object[] { scene });
        }

        private GameObject Owned(string label, Scene scene)
        {
            var go = new GameObject("TransformIntegrity_" + label + "_" + Guid.NewGuid().ToString("N"));
            objects.Add(go);
            SceneManager.MoveGameObjectToScene(go, scene);
            return go;
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var go in objects)
            {
                if (go == null)
                    continue;
                Undo.ClearUndo(go.transform);
                Undo.ClearUndo(go);
                UnityEngine.Object.DestroyImmediate(go);
            }
            objects.Clear();
            if (originalActive.IsValid() && originalActive.isLoaded)
                SceneManager.SetActiveScene(originalActive);
            if (targetScene.IsValid() && targetScene.isLoaded)
                EditorSceneManager.CloseScene(targetScene, true);
            if (activeScene.IsValid() && activeScene.isLoaded)
                EditorSceneManager.CloseScene(activeScene, true);
        }

        private JObject Send(string action, JObject options)
        {
            options["action"] = action;
            options["target"] = target.GetInstanceIDCompat().ToString();
            options["searchMethod"] = "by_id";
            return JObject.FromObject(ManageGameObject.HandleCommand(options));
        }

        private JObject Snapshot()
        {
            return new JObject
            {
                ["name"] = target.name,
                ["parent"] = target.transform.parent == null ? 0 : target.transform.parent.GetInstanceIDCompat(),
                ["active"] = target.activeSelf,
                ["tag"] = target.tag,
                ["layer"] = target.layer,
                ["static"] = (int)GameObjectUtility.GetStaticEditorFlags(target),
                ["position"] = new JArray(target.transform.localPosition.x, target.transform.localPosition.y, target.transform.localPosition.z),
                ["rotation"] = new JArray(target.transform.localEulerAngles.x, target.transform.localEulerAngles.y, target.transform.localEulerAngles.z),
                ["scale"] = new JArray(target.transform.localScale.x, target.transform.localScale.y, target.transform.localScale.z),
                ["goDirty"] = EditorUtility.GetDirtyCount(target),
                ["transformDirty"] = EditorUtility.GetDirtyCount(target.transform),
                ["targetSceneDirty"] = targetScene.isDirty,
                ["activeSceneDirty"] = activeScene.isDirty,
            };
        }

        [TestCase("rename")]
        [TestCase("parent")]
        [TestCase("active")]
        [TestCase("transform")]
        public void InvalidStatic_PreservesEarlierRequestedFields(string earlierField)
        {
            var options = new JObject { ["isStatic"] = "bad" };
            if (earlierField == "rename")
                options["name"] = target.name + "Renamed";
            if (earlierField == "parent")
                options["parent"] = reference.GetInstanceIDCompat();
            if (earlierField == "active")
                options["setActive"] = false;
            if (earlierField == "transform")
                options["position"] = new JArray(1, 2, 3);
            var before = Snapshot();
            LogAssert.Expect(LogType.Error, new Regex("\\[ManageGameObject\\] Action 'modify' failed:"));
            var response = Send("modify", options);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot()));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void StaticBoolean_PreservesExistingConversion(bool value)
        {
            GameObjectUtility.SetStaticEditorFlags(target, StaticEditorFlags.BatchingStatic);
            var response = Send("modify", new JObject { ["isStatic"] = value });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(value ? (StaticEditorFlags)~0 : 0, GameObjectUtility.GetStaticEditorFlags(target));
        }

        [Test]
        public void StaticNull_PreservesSeededFlags()
        {
            GameObjectUtility.SetStaticEditorFlags(target, StaticEditorFlags.BatchingStatic);
            var response = Send("modify", new JObject { ["isStatic"] = JValue.CreateNull() });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(StaticEditorFlags.BatchingStatic, GameObjectUtility.GetStaticEditorFlags(target));
        }

        [Test]
        public void MissingMovement_PreservesTransformAndDirtiness()
        {
            var before = Snapshot();
            var response = Send("move_relative", new JObject { ["reference_object"] = reference.GetInstanceIDCompat() });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot()));
        }

        [TestCase("modify")]
        [TestCase("move_relative")]
        public void TransformMutation_DirtiesTargetSceneOnly(string action)
        {
            SceneManager.MoveGameObjectToScene(reference, activeScene);
            SceneManager.SetActiveScene(activeScene);
            string targetPath = "/" + target.name;
            Assert.AreSame(target, ManageGameObjectCommon.FindObjectInternal(targetPath, "by_path"), "The cross-scene target must resolve before mutation.");
            Assert.AreSame(
                reference,
                ManageGameObjectCommon.FindObjectInternal(reference.GetInstanceIDCompat(), "by_id_or_name_or_path"),
                "The movement reference must resolve in the active scene."
            );
            ClearOwnedSceneDirtiness(activeScene);
            ClearOwnedSceneDirtiness(targetScene);
            var options =
                action == "modify"
                    ? new JObject { ["position"] = new JArray(1, 2, 3) }
                    : new JObject
                    {
                        ["reference_object"] = reference.GetInstanceIDCompat(),
                        ["direction"] = "right",
                        ["distance"] = 2,
                    };
            options["action"] = action;
            options["target"] = targetPath;
            options["searchMethod"] = "by_path";
            var response = JObject.FromObject(ManageGameObject.HandleCommand(options));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(action == "modify" ? new Vector3(1, 2, 3) : new Vector3(12, 20, 30), target.transform.position);
            Assert.IsTrue(targetScene.isDirty);
            Assert.IsFalse(activeScene.isDirty);
        }

        [TestCase("by_name")]
        [TestCase("by_path")]
        public void LookAt_SecondaryReferenceIndependentOfMainId(string secondaryMethod)
        {
            string secondary = reference.name;
            if (secondaryMethod == "by_path")
            {
                var root = Owned("Root", targetScene);
                reference.transform.SetParent(root.transform, true);
                secondary = "/" + root.name + "/" + reference.name;
            }
            var response = Send("look_at", new JObject { ["look_at_target"] = secondary });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.Less(Vector3.Angle(target.transform.forward, reference.transform.position - target.transform.position), 0.01f);
        }

        [Test]
        public void LookAt_ExistingNumericNameSelectorTakesPrecedence()
        {
            string numericName = reference.GetInstanceIDCompat().ToString();
            Assert.IsNull(GameObject.Find(numericName), "Numeric-name collision must be owned by this test.");
            var named = Owned("Numeric", targetScene);
            named.name = numericName;
            named.transform.position = new Vector3(-10, 0, 0);
            var response = JObject.FromObject(
                ManageGameObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "look_at",
                        ["target"] = target.name,
                        ["searchMethod"] = "by_name",
                        ["look_at_target"] = numericName,
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.Less(Vector3.Angle(target.transform.forward, named.transform.position - target.transform.position), 0.01f);
        }

        [Test]
        public void LookAt_MissingReferencePreservesRotation()
        {
            var before = Snapshot();
            var response = Send("look_at", new JObject { ["look_at_target"] = "Missing_" + Guid.NewGuid().ToString("N") });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot()));
        }

        [TestCase("tr-TR", "action", true)]
        [TestCase("tr-TR", "selector", true)]
        [TestCase("tr-TR", "direction", true)]
        [TestCase("tr-TR", "direction", false)]
        [TestCase("tr-TR", "lowercase", true)]
        [TestCase("az-Latn-AZ", "action", true)]
        [TestCase("az-Latn-AZ", "selector", true)]
        [TestCase("az-Latn-AZ", "direction", true)]
        [TestCase("az-Latn-AZ", "direction", false)]
        [TestCase("az-Latn-AZ", "lowercase", true)]
        [TestCase("", "direction", true)]
        public void ProtocolIdentifiers_AreIndependentOfCurrentCulture(string culture, string identifier, bool worldSpace)
        {
            var originalCulture = CultureInfo.CurrentCulture;
            try
            {
                CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo(culture);
                reference.transform.rotation = Quaternion.Euler(0, 90, 0);
                var response = JObject.FromObject(
                    ManageGameObject.HandleCommand(
                        new JObject
                        {
                            ["action"] = identifier == "action" ? "MOVE_RELATIVE" : "move_relative",
                            ["target"] = target.GetInstanceIDCompat(),
                            ["searchMethod"] = identifier == "selector" ? "BY_ID" : "by_id",
                            ["reference_object"] = reference.GetInstanceIDCompat(),
                            ["direction"] = identifier == "direction" ? "RIGHT" : "right",
                            ["distance"] = 2,
                            ["world_space"] = worldSpace,
                        }
                    )
                );
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                var axis = worldSpace ? Vector3.right : reference.transform.right;
                Assert.AreEqual(reference.transform.position + axis * 2, target.transform.position);
            }
            finally
            {
                CultureInfo.CurrentCulture = originalCulture;
            }
        }

        [TestCase("first", 1)]
        [TestCase("all", 3)]
        [TestCase("empty", 0)]
        public void MatchCollection_EnumeratesOnlyRequestedMatches(string mode, int expectedVisits)
        {
            var candidates = mode == "empty" ? new GameObject[0] : new[] { target, target, reference };
            int visited = 0;
            IEnumerable<GameObject> CountedMatches()
            {
                foreach (var candidate in candidates)
                {
                    visited++;
                    yield return candidate;
                }
            }
            var results = new List<GameObject>();
            ManageGameObjectCommon.AddMatches(results, CountedMatches(), mode == "all");
            Assert.AreEqual(expectedVisits, visited);
            var expected = mode == "first" ? new[] { target } : candidates;
            CollectionAssert.AreEqual(expected, results);
        }

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        public void NameLookup_PreservesFirstAllAndInactiveSelection(bool findAll, bool inactiveFirst)
        {
            reference.name = target.name;
            target.transform.SetSiblingIndex(0);
            reference.transform.SetSiblingIndex(1);
            target.SetActive(!inactiveFirst);
            var results = ManageGameObjectCommon.FindObjectsInternal(target.name, "by_name", findAll);
            var expected = findAll ? new[] { target, reference } : new[] { inactiveFirst ? reference : target };
            CollectionAssert.AreEqual(expected, results);
        }

        [TestCase(null)]
        [TestCase("by_id")]
        [TestCase("by_name")]
        public void NumericStringTarget_UsesDocumentedSelector(string method)
        {
            string id = target.GetInstanceIDCompat().ToString();
            Assert.IsNull(GameObject.Find(id), "Numeric-name collision must be owned by this test.");
            var named = Owned("Numeric", targetScene);
            named.name = id;
            var options = new JObject
            {
                ["action"] = "modify",
                ["target"] = id,
                ["setActive"] = false,
            };
            if (method != null)
                options["searchMethod"] = method;
            var response = JObject.FromObject(ManageGameObject.HandleCommand(options));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(method == "by_name", target.activeSelf);
            Assert.AreEqual(method != "by_name", named.activeSelf);
        }

        [Test]
        public void MissingNumericId_DoesNotFallBackToNumericName()
        {
            var removed = Owned("Removed", targetScene);
            int missingId = removed.GetInstanceIDCompat();
            UnityEngine.Object.DestroyImmediate(removed);
            Assert.IsNull(GameObject.Find(missingId.ToString()), "Numeric-name collision must be owned by this test.");
            var named = Owned("Numeric", targetScene);
            Assert.AreNotEqual(missingId, named.GetInstanceIDCompat(), "The removed ID must not have been reused.");
            named.name = missingId.ToString();
            var response = JObject.FromObject(
                ManageGameObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = missingId.ToString(),
                        ["setActive"] = false,
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(named.activeSelf);
        }

        [Test]
        public void KnownId_CanReactivateInactiveTarget()
        {
            target.SetActive(false);
            var response = Send("modify", new JObject { ["setActive"] = true });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(target.activeSelf);
        }

        [Test]
        public void MalformedVector_KeepsExistingIgnoreBehavior()
        {
            var response = Send("modify", new JObject { ["position"] = new JArray(1, "bad", 3) });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(Vector3.zero, target.transform.localPosition);
        }
    }
}
