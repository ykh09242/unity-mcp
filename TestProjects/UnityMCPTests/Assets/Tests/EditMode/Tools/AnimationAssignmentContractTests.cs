using MCPForUnity.Editor.Tools.Animation;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    public class AnimationAssignmentContractTests
    {
        private const string Root = "Assets/Temp/AnimationAssignmentContractTests";
        private GameObject target;

        [SetUp]
        public void SetUp() => EnsureFolder(Root);

        [TearDown]
        public void TearDown()
        {
            if (target != null)
                Object.DestroyImmediate(target);
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
    }
}
