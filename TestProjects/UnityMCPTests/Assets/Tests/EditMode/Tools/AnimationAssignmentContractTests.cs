using System.IO;
using MCPForUnity.Editor.Tools.Animation;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    public class AnimationAssignmentContractTests
    {
        private const string Root = "Assets/Temp/AnimationAssignmentContractTests";
        private GameObject target;
        private Scene ownedScene;

        [SetUp]
        public void SetUp() => EnsureFolder(Root);

        [TearDown]
        public void TearDown()
        {
            if (target != null)
                Object.DestroyImmediate(target);
            if (ownedScene.IsValid() && ownedScene.isLoaded)
                EditorSceneManager.CloseScene(ownedScene, true);
            AssetDatabase.DeleteAsset(Root);
            CleanupEmptyParentFolders(Root);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void AnimatorOnly_RefusesWithoutChangingObjectControllerOrClip(bool clipExists)
        {
            const string clipPath = Root + "/Candidate.anim";
            var clip = new AnimationClip();
            AssetDatabase.CreateAsset(clip, Root + "/Original.anim");
            if (clipExists)
                AssetDatabase.CopyAsset(Root + "/Original.anim", clipPath);
            var controller = AnimatorController.CreateAnimatorControllerAtPath(Root + "/Existing.controller");
            var state = controller.layers[0].stateMachine.AddState("Existing");
            state.motion = clip;
            target = new GameObject("AnimationAssignment_AnimatorOnly");
            var animator = target.AddComponent<Animator>();
            animator.runtimeAnimatorController = controller;
            animator.speed = 2f;
            AssetDatabase.SaveAssets();
            string objectBefore = EditorJsonUtility.ToJson(target);
            string animatorBefore = EditorJsonUtility.ToJson(animator);
            string controllerBefore = EditorJsonUtility.ToJson(controller);
            string clipBefore = clipExists ? EditorJsonUtility.ToJson(AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath)) : null;

            var result = ToJObject(
                ManageAnimation.HandleCommand(
                    new JObject
                    {
                        ["action"] = "clip_assign",
                        ["target"] = target.name,
                        ["clip_path"] = clipPath,
                    }
                )
            );

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.That(result.Value<string>("message"), Does.Contain(clipExists ? "Animator Controller" : "not found"));
            Assert.AreEqual(objectBefore, EditorJsonUtility.ToJson(target));
            Assert.AreEqual(animatorBefore, EditorJsonUtility.ToJson(animator));
            Assert.AreEqual(controllerBefore, EditorJsonUtility.ToJson(controller));
            Assert.AreSame(clip, state.motion);
            Assert.IsNull(target.GetComponent<Animation>());
            if (clipExists)
                Assert.AreEqual(clipBefore, EditorJsonUtility.ToJson(AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath)));
            else
                Assert.IsNull(AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath));
        }

        [TestCase(true)]
        [TestCase(false)]
        public void LegacyAssignment_SuccessActuallyAssignsClip(bool existingComponent)
        {
            const string path = Root + "/Legacy.anim";
            var clip = new AnimationClip();
            AssetDatabase.CreateAsset(clip, path);
            target = new GameObject("AnimationAssignment_Legacy");
            if (existingComponent)
                target.AddComponent<Animation>();

            var result = ToJObject(
                ManageAnimation.HandleCommand(
                    new JObject
                    {
                        ["action"] = "clip_assign",
                        ["target"] = target.name,
                        ["clip_path"] = path,
                    }
                )
            );

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var animation = target.GetComponent<Animation>();
            Assert.IsNotNull(animation);
            Assert.AreSame(clip, animation.clip);
            Assert.AreSame(clip, animation.GetClip(clip.name));
            Assert.IsTrue(clip.legacy);
            Assert.IsTrue(animation.playAutomatically);
        }

        [Test]
        public void LegacyPrefabAssignmentPersistsExactInstanceOverridesAfterSceneReopen()
        {
            var animation = CreateLegacyPrefabInstance(out var original, out var replacement, out string prefabPath);
            string scenePath = Root + "/Assignment.unity";
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, scenePath));
            byte[] prefabBefore = File.ReadAllBytes(prefabPath);
            byte[] metaBefore = File.ReadAllBytes(prefabPath + ".meta");

            var response = AssignReplacement(replacement);

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            AssertLegacyAssignment(animation, original, replacement);
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene));
            Assert.IsTrue(EditorSceneManager.CloseScene(ownedScene, true));
            target = null;
            ownedScene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Additive);
            var roots = ownedScene.GetRootGameObjects();
            Assert.AreEqual(1, roots.Length);
            target = roots[0];
            Assert.IsTrue(PrefabUtility.IsPartOfPrefabInstance(target));
            AssertLegacyAssignment(target.GetComponent<Animation>(), original, replacement);
            AssertPrefabSourceUnchanged(prefabPath, prefabBefore, metaBefore, original);
        }

        [Test]
        public void LegacyPrefabAssignmentUndoRestoresExactComponentAndLeavesSourceUnchanged()
        {
            var animation = CreateLegacyPrefabInstance(out var original, out var replacement, out string prefabPath);
            byte[] prefabBefore = File.ReadAllBytes(prefabPath);
            byte[] metaBefore = File.ReadAllBytes(prefabPath + ".meta");
            string animationBefore = EditorJsonUtility.ToJson(animation);
            Undo.IncrementCurrentGroup();

            var response = AssignReplacement(replacement);

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            AssertLegacyAssignment(animation, original, replacement);
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            Assert.AreEqual(animationBefore, EditorJsonUtility.ToJson(animation));
            Assert.AreSame(original, animation.clip);
            Assert.AreSame(original, animation.GetClip(original.name));
            Assert.IsNull(animation.GetClip(replacement.name));
            Assert.AreEqual(1, animation.GetClipCount());
            Assert.IsFalse(animation.playAutomatically);
            AssertPrefabSourceUnchanged(prefabPath, prefabBefore, metaBefore, original);
        }

        private Animation CreateLegacyPrefabInstance(out AnimationClip original, out AnimationClip replacement, out string prefabPath)
        {
            original = new AnimationClip { name = "Original", legacy = true };
            replacement = new AnimationClip { name = "Replacement", legacy = true };
            AssetDatabase.CreateAsset(original, Root + "/Original.anim");
            AssetDatabase.CreateAsset(replacement, Root + "/Replacement.anim");
            target = new GameObject("AnimationAssignment_Prefab");
            var animation = target.AddComponent<Animation>();
            animation.clip = original;
            animation.AddClip(original, original.name);
            animation.playAutomatically = false;
            prefabPath = Root + "/Legacy.prefab";
            var prefab = PrefabUtility.SaveAsPrefabAsset(target, prefabPath);
            Assert.IsNotNull(prefab);
            Object.DestroyImmediate(target);
            target = null;
            AssetDatabase.SaveAssets();
            Scene previousActive = SceneManager.GetActiveScene();
            ownedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            if (previousActive.IsValid() && previousActive.isLoaded)
                SceneManager.SetActiveScene(previousActive);
            target = (GameObject)PrefabUtility.InstantiatePrefab(prefab, ownedScene);
            return target.GetComponent<Animation>();
        }

        private JObject AssignReplacement(AnimationClip replacement)
        {
            return ToJObject(
                ManageAnimation.HandleCommand(
                    new JObject
                    {
                        ["action"] = "clip_assign",
                        ["target"] = target.name,
                        ["clip_path"] = AssetDatabase.GetAssetPath(replacement),
                    }
                )
            );
        }

        private static void AssertLegacyAssignment(Animation animation, AnimationClip original, AnimationClip replacement)
        {
            Assert.IsNotNull(animation);
            Assert.AreSame(replacement, animation.clip);
            Assert.AreEqual(2, animation.GetClipCount());
            Assert.AreSame(original, animation.GetClip(original.name));
            Assert.AreSame(replacement, animation.GetClip(replacement.name));
            Assert.IsTrue(animation.playAutomatically);
            using var serialized = new SerializedObject(animation);
            using var clips = serialized.FindProperty("m_Animations");
            Assert.AreEqual(2, clips.arraySize);
            using var first = clips.GetArrayElementAtIndex(0);
            using var second = clips.GetArrayElementAtIndex(1);
            Assert.AreSame(original, first.objectReferenceValue);
            Assert.AreSame(replacement, second.objectReferenceValue);
        }

        private static void AssertPrefabSourceUnchanged(string prefabPath, byte[] prefabBefore, byte[] metaBefore, AnimationClip original)
        {
            CollectionAssert.AreEqual(prefabBefore, File.ReadAllBytes(prefabPath));
            CollectionAssert.AreEqual(metaBefore, File.ReadAllBytes(prefabPath + ".meta"));
            var source = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath).GetComponent<Animation>();
            Assert.AreSame(original, source.clip);
            Assert.AreEqual(1, source.GetClipCount());
            Assert.AreSame(original, source.GetClip(original.name));
            Assert.IsFalse(source.playAutomatically);
        }
    }
}
