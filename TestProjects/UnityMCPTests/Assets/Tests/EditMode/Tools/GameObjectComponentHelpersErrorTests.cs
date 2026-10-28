using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Runtime.Serialization;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    [Serializable]
    public struct NestedMutationValue
    {
        public float X;
    }

    [Serializable]
    public struct NestedMutationOuter
    {
        public NestedMutationValue Inner;
    }

    public class NestedMutationComponent : MonoBehaviour
    {
        public NestedMutationValue State;
        public NestedMutationValue[] Values = new NestedMutationValue[1];
        public NestedMutationValue[] ReadOnlyArray => Values;
        public NestedMutationOuter Deep;
        public AnimationCurve Curve;
        public Gradient GradientField;
        public List<NestedMutationValue> ValuesList = new List<NestedMutationValue> { new NestedMutationValue() };
        public ReadOnlyCollection<NestedMutationValue> ReadOnlyList => ValuesList.AsReadOnly();
        public int ReadOnlyGetterCalls;
        public NestedMutationValue ReadOnlyState
        {
            get
            {
                ReadOnlyGetterCalls++;
                return State;
            }
        }
        public readonly NestedMutationValue ReadOnlyField;
    }

    /// <summary>
    /// Tests for GameObjectComponentHelpers.SetComponentPropertiesInternal error reporting.
    /// Reproduces issue #765: conversion failures incorrectly reported as "Property not found".
    /// </summary>
    public class GameObjectComponentHelpersErrorTests
    {
        private GameObject testGo;

        [SetUp]
        public void SetUp()
        {
            testGo = new GameObject("ErrorTestGO");
            CommandRegistry.Initialize();
        }

        [TearDown]
        public void TearDown()
        {
            if (testGo != null)
            {
                foreach (var component in testGo.GetComponents<Component>())
                    Undo.ClearUndo(component);
                Undo.ClearUndo(testGo);
                Object.DestroyImmediate(testGo);
            }
        }

        /// <summary>
        /// When a property exists but conversion fails, the error should say
        /// "Failed to convert" rather than "Property not found. Did you mean: X?"
        /// </summary>
        [Test]
        public void SetComponentProperties_ConversionFailure_ReportsConversionError_NotPropertyNotFound()
        {
            // Expect conversion error log from PropertyConversion (ComponentOps reflection attempt)
            LogAssert.Expect(LogType.Error, new Regex("Error converting token"));
            // Expect the warning log from SetComponentPropertiesInternal
            LogAssert.Expect(LogType.Warning, new Regex("Failed to set"));

            var audioSource = testGo.AddComponent<AudioSource>();

            // spatialBlend is a float property — passing an array triggers conversion failure
            var props = new JObject { ["spatialBlend"] = JArray.Parse("[1, 2, 3]") };

            var result = GameObjectComponentHelpers.SetComponentPropertiesInternal(testGo, "AudioSource", props, audioSource);

            Assert.IsNotNull(result, "Should return an error response");
            Assert.IsInstanceOf<ErrorResponse>(result);

            var errorResponse = (ErrorResponse)result;

            // The error message must NOT say "not found" for a property that exists
            Assert.IsFalse(errorResponse.Error.Contains("not found"), $"Error should report conversion failure, not 'not found'. Got: {errorResponse.Error}");
        }

        /// <summary>
        /// When a property genuinely doesn't exist, the error should still say "not found" with suggestions.
        /// </summary>
        [Test]
        public void SetComponentProperties_NonexistentProperty_ReportsNotFound()
        {
            // Expect the "not found" warning
            LogAssert.Expect(LogType.Warning, new Regex("not found"));

            var audioSource = testGo.AddComponent<AudioSource>();

            var props = new JObject { ["totallyFakeProperty"] = 42 };

            var result = GameObjectComponentHelpers.SetComponentPropertiesInternal(testGo, "AudioSource", props, audioSource);

            Assert.IsNotNull(result);
            Assert.IsInstanceOf<ErrorResponse>(result);

            var errorResponse = (ErrorResponse)result;

            Assert.IsTrue(
                errorResponse.Error.Contains("not found") || errorResponse.Error.Contains("failed"),
                $"Error for nonexistent property should say 'not found'. Got: {errorResponse.Error}"
            );
        }

        /// <summary>
        /// Valid property setting should still succeed.
        /// </summary>
        [Test]
        public void SetComponentProperties_ValidProperty_Succeeds()
        {
            var audioSource = testGo.AddComponent<AudioSource>();

            var props = new JObject { ["volume"] = 0.42f };

            var result = GameObjectComponentHelpers.SetComponentPropertiesInternal(testGo, "AudioSource", props, audioSource);

            Assert.IsNull(result, "Should return null on success (no errors)");
            Assert.AreEqual(0.42f, audioSource.volume, 0.001f);
        }

        [TestCase("State.X")]
        [TestCase("Deep.Inner.X")]
        [TestCase("Values[0].X")]
        [TestCase("ReadOnlyArray[0].X")]
        [TestCase("ValuesList[0].X")]
        public void SetNestedProperty_ValueType_ReadbackAndUndoRedo(string path)
        {
            var component = testGo.AddComponent<NestedMutationComponent>();
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Assert.IsNull(GameObjectComponentHelpers.SetComponentPropertiesInternal(testGo, component.GetType().Name, new JObject { [path] = 5f }, component));
            Assert.AreEqual(5f, ReadNestedValue(component, path));
            Undo.FlushUndoRecordObjects();
            Undo.CollapseUndoOperations(group);
            Undo.PerformUndo();
            Assert.AreEqual(0f, ReadNestedValue(component, path));
            Undo.PerformRedo();
            Assert.AreEqual(5f, ReadNestedValue(component, path));
        }

        private static float ReadNestedValue(NestedMutationComponent component, string path) =>
            path == "State.X" ? component.State.X
            : path == "Deep.Inner.X" ? component.Deep.Inner.X
            : path == "ValuesList[0].X" ? component.ValuesList[0].X
            : component.Values[0].X;

        [TestCase("main.loop")]
        [TestCase("emission.enabled")]
        public void PrepareNestedProperty_ParticleModule_AcceptsNativeProxyFromMetadata(string path)
        {
            var method = typeof(GameObjectComponentHelpers).GetMethod("TryPrepareNestedProperty", BindingFlags.Static | BindingFlags.NonPublic);
            object[] arguments = { typeof(ParticleSystem), path, new JValue(false), null, false, null, null, null };
            Assert.IsTrue((bool)method.Invoke(null, arguments), arguments[5] as string);
            Assert.IsTrue((bool)arguments[4]);
        }

        [Test]
        public void NestedPropertyWritebackRules_ExemptOnlyVerifiedNativeContracts()
        {
            var flags = BindingFlags.Static | BindingFlags.NonPublic;
            var moduleAccessor = typeof(GameObjectComponentHelpers).GetMethod("IsParticleSystemModuleAccessor", flags);
            Assert.IsTrue((bool)moduleAccessor.Invoke(null, new object[] { typeof(ParticleSystem).GetProperty("main") }));
            Assert.IsTrue((bool)moduleAccessor.Invoke(null, new object[] { typeof(ParticleSystem).GetProperty("emission") }));
            Assert.IsFalse((bool)moduleAccessor.Invoke(null, new object[] { typeof(ParticleSystem.MainModule).GetProperty("startLifetime") }));
            Assert.IsFalse((bool)moduleAccessor.Invoke(null, new object[] { typeof(NestedMutationComponent).GetProperty("ReadOnlyState") }));
            var detachedArray = typeof(GameObjectComponentHelpers).GetMethod("IsDetachedNativeArrayMember", flags);
            Assert.IsTrue((bool)detachedArray.Invoke(null, new object[] { typeof(AnimationCurve).GetProperty("keys") }));
            Assert.IsTrue((bool)detachedArray.Invoke(null, new object[] { typeof(Gradient).GetProperty("colorKeys") }));
            Assert.IsTrue((bool)detachedArray.Invoke(null, new object[] { typeof(Gradient).GetProperty("alphaKeys") }));
            Assert.IsFalse((bool)detachedArray.Invoke(null, new object[] { typeof(NestedMutationComponent).GetProperty("ReadOnlyArray") }));
        }

        [TestCase("main.loop")]
        [TestCase("emission.enabled")]
        public void SetNestedProperty_ParticleModule_UpdatesNativeOwnerAndSupportsUndoRedo(string path)
        {
            var particles = testGo.AddComponent<ParticleSystem>();
            particles.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
            bool before = path == "main.loop" ? particles.main.loop : particles.emission.enabled;
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Assert.IsNull(GameObjectComponentHelpers.SetComponentPropertiesInternal(testGo, "ParticleSystem", new JObject { [path] = !before }, particles));
            Assert.AreEqual(!before, path == "main.loop" ? particles.main.loop : particles.emission.enabled);
            Undo.FlushUndoRecordObjects();
            Undo.CollapseUndoOperations(group);
            Undo.PerformUndo();
            Assert.AreEqual(before, path == "main.loop" ? particles.main.loop : particles.emission.enabled);
            Undo.PerformRedo();
            Assert.AreEqual(!before, path == "main.loop" ? particles.main.loop : particles.emission.enabled);
        }

        [Test]
        public void SetNestedProperty_AnimationCurveKeys_WritesCopyBackAndSupportsComponentUndoRedo()
        {
            var component = testGo.AddComponent<NestedMutationComponent>();
            component.Curve = new AnimationCurve(new Keyframe(0f, 1f), new Keyframe(1f, 2f));
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Assert.IsNull(
                GameObjectComponentHelpers.SetComponentPropertiesInternal(
                    testGo,
                    component.GetType().Name,
                    new JObject { ["Curve.keys[0].value"] = 5f },
                    component
                )
            );
            Assert.AreEqual(5f, component.Curve.keys[0].value);
            Assert.AreEqual(2f, component.Curve.keys[1].value);
            Undo.FlushUndoRecordObjects();
            Undo.CollapseUndoOperations(group);
            Undo.PerformUndo();
            Assert.AreEqual(1f, component.Curve.keys[0].value);
            Undo.PerformRedo();
            Assert.AreEqual(5f, component.Curve.keys[0].value);
        }

        [TestCase("GradientField.alphaKeys[0].alpha")]
        [TestCase("GradientField.colorKeys[0].color.r")]
        public void SetNestedProperty_GradientKeys_WritesCopyBackAndSupportsComponentUndoRedo(string path)
        {
            var component = testGo.AddComponent<NestedMutationComponent>();
            component.GradientField = new Gradient();
            component.GradientField.SetKeys(
                new[] { new GradientColorKey(Color.red, 0f), new GradientColorKey(Color.blue, 1f) },
                new[] { new GradientAlphaKey(0.25f, 0f), new GradientAlphaKey(0.75f, 1f) }
            );
            float ReadValue() => path.Contains("alphaKeys") ? component.GradientField.alphaKeys[0].alpha : component.GradientField.colorKeys[0].color.r;
            float before = ReadValue();
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Assert.IsNull(
                GameObjectComponentHelpers.SetComponentPropertiesInternal(testGo, component.GetType().Name, new JObject { [path] = 0.5f }, component)
            );
            Assert.AreEqual(0.5f, ReadValue());
            Assert.AreEqual(Color.blue, component.GradientField.colorKeys[1].color);
            Assert.AreEqual(0.75f, component.GradientField.alphaKeys[1].alpha);
            Undo.FlushUndoRecordObjects();
            Undo.CollapseUndoOperations(group);
            Undo.PerformUndo();
            Assert.AreEqual(before, ReadValue());
            Undo.PerformRedo();
            Assert.AreEqual(0.5f, ReadValue());
        }

        [TestCase("ReadOnlyState.X")]
        [TestCase("ReadOnlyField.X")]
        [TestCase("ReadOnlyList[0].X")]
        public void SetNestedProperty_ReadOnlyValueParent_RejectsBeforeMutation(string path)
        {
            var component = testGo.AddComponent<NestedMutationComponent>();
            var method = typeof(GameObjectComponentHelpers).GetMethod("SetNestedProperty", BindingFlags.Static | BindingFlags.NonPublic);
            object[] arguments = { component, path, new JValue(5f), UnityJsonSerializer.Instance, null };
            Assert.IsFalse((bool)method.Invoke(null, arguments));
            StringAssert.Contains("writ", (string)arguments[4]);
            Assert.AreEqual(0f, component.State.X);
            Assert.AreEqual(0f, component.ReadOnlyField.X);
            Assert.AreEqual(0f, component.ValuesList[0].X);
            Assert.AreEqual(0, component.ReadOnlyGetterCalls, "Read-only value parents must reject from metadata before evaluating their getters.");
        }
    }
}
