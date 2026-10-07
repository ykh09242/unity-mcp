using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public class MaterialPropertyIntegrityTests
    {
        private Shader _shader;
        private Material _material;
        private Texture2D _texture;

        [SetUp]
        public void SetUp()
        {
            _shader = null;
            _material = null;
            _texture = null;
            string name = "Hidden/McpMaterialPropertyIntegrity_" + Guid.NewGuid().ToString("N");
            _shader = ShaderUtil.CreateShaderAsset(
                "Shader \""
                    + name
                    + "\" { Properties { "
                    + "_Color (\"Color\", Color) = (0,0,0,0) "
                    + "_Vector (\"Vector\", Vector) = (0,0,0,0) "
                    + "_Float (\"Float\", Float) = 0 "
                    + "_Metallic (\"Metallic\", Float) = 0 "
                    + "_Smoothness (\"Smoothness\", Range(0,1)) = 0 "
                    + "_Int (\"Integer\", Integer) = 17 "
                    + "_MainTex (\"Texture\", 2D) = \"white\" {} } SubShader { Pass {} } }",
                false
            );
            Assert.That(_shader, Is.Not.Null);
            Assert.That(EditorUtility.IsPersistent(_shader), Is.False);
            Assert.That(_shader.GetPropertyType(_shader.FindPropertyIndex("_Int")), Is.EqualTo(ShaderPropertyType.Int));
            _material = new Material(_shader);
            _texture = new Texture2D(1, 1);
            _material.SetTexture("_MainTex", _texture);
            _material.SetInteger("_Int", 17);
        }

        [TearDown]
        public void TearDown()
        {
            if (_material != null)
                UnityEngine.Object.DestroyImmediate(_material);
            if (_texture != null)
                UnityEngine.Object.DestroyImmediate(_texture);
            if (_shader != null && !EditorUtility.IsPersistent(_shader))
                UnityEngine.Object.DestroyImmediate(_shader);
        }

        [TestCase("[2,3,4,5]", 2f, 3f, 4f, 5f)]
        [TestCase("[2,3,4]", 2f, 3f, 4f, 0f)]
        [TestCase("[2,3]", 2f, 3f, 0f, 0f)]
        [TestCase("{\"x\":2,\"y\":3,\"z\":4,\"w\":5}", 2f, 3f, 4f, 5f)]
        [TestCase("\"[0,-1,2,0]\"", 0f, -1f, 2f, 0f)]
        public void VectorValueIsPreparedWithoutMutation(string json, float x, float y, float z, float w)
        {
            var before = _material.GetVector("_Vector");
            int dirty = EditorUtility.GetDirtyCount(_material);
            Assert.That(
                MaterialOps.TryPrepareShaderProperty(_material, "_Vector", JToken.Parse(json), UnityJsonSerializer.Instance, out Action apply),
                Is.True
            );
            Assert.That(_material.GetVector("_Vector"), Is.EqualTo(before));
            Assert.That(EditorUtility.GetDirtyCount(_material), Is.EqualTo(dirty));
            apply();
            Assert.That(_material.GetVector("_Vector"), Is.EqualTo(new Vector4(x, y, z, w)));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("16777217", 16777217)]
        [TestCase("2147483647", int.MaxValue)]
        [TestCase("-3", -3)]
        [TestCase("0", 0)]
        [TestCase("false", 0)]
        [TestCase("3.0", 3)]
        public void TrueIntegerPropertyUsesIntegerStorage(string json, int expected)
        {
            Assert.That(MaterialOps.TrySetShaderProperty(_material, "_Int", JToken.Parse(json), UnityJsonSerializer.Instance), Is.True);
            Assert.That(_material.GetInteger("_Int"), Is.EqualTo(expected));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("1.5")]
        [TestCase("2147483648")]
        [TestCase("-2147483649")]
        public void IntegerCannotSilentlyLoseRequestedValue(string json)
        {
            int dirty = EditorUtility.GetDirtyCount(_material);
            Assert.That(MaterialOps.TryPrepareShaderProperty(_material, "_Int", JToken.Parse(json), UnityJsonSerializer.Instance, out Action apply), Is.False);
            Assert.That(apply, Is.Null);
            Assert.That(_material.GetInteger("_Int"), Is.EqualTo(17));
            Assert.That(EditorUtility.GetDirtyCount(_material), Is.EqualTo(dirty));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("_Color", "3")]
        [TestCase("_MainTex", "3")]
        [TestCase("_Float", "[1,2,3,4]")]
        [TestCase("_Vector", "null")]
        [TestCase("_Unknown", "3")]
        public void IncompatiblePropertyValueDoesNotWrite(string property, string json)
        {
            string before = EditorJsonUtility.ToJson(_material);
            int dirty = EditorUtility.GetDirtyCount(_material);
            Assert.That(
                MaterialOps.TryPrepareShaderProperty(_material, property, JToken.Parse(json), UnityJsonSerializer.Instance, out Action apply),
                Is.False
            );
            Assert.That(apply, Is.Null);
            Assert.That(EditorJsonUtility.ToJson(_material), Is.EqualTo(before));
            Assert.That(EditorUtility.GetDirtyCount(_material), Is.EqualTo(dirty));
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void MissingTexturePreservesExistingReference()
        {
            string path = "Assets/__McpMissingTexture_" + Guid.NewGuid().ToString("N") + ".png";
            Assert.That(MaterialOps.TrySetShaderProperty(_material, "_MainTex", new JValue(path), UnityJsonSerializer.Instance), Is.False);
            Assert.That(_material.GetTexture("_MainTex"), Is.SameAs(_texture));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("[0.25,0.5,0.75,0.4]", 0.4f)]
        [TestCase("[0.25,0.5,0.75]", 1f)]
        [TestCase("{\"r\":0.25,\"g\":0.5,\"b\":0.75,\"a\":0.4}", 0.4f)]
        [TestCase("\"{\\\"r\\\":0.25,\\\"g\\\":0.5,\\\"b\\\":0.75,\\\"a\\\":0.4}\"", 0.4f)]
        public void ColorFormatsUseTheSameNativeColorSetter(string json, float alpha)
        {
            using (var control = new OwnedMaterial(_shader))
            {
                control.Value.SetColor("_Color", new Color(0.25f, 0.5f, 0.75f, alpha));
                Assert.That(MaterialOps.TrySetShaderProperty(_material, "_Color", JToken.Parse(json), UnityJsonSerializer.Instance), Is.True);
                Assert.That(_material.GetColor("_Color"), Is.EqualTo(control.Value.GetColor("_Color")));
            }
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void LegacyTwoComponentColorRetainsVectorSetterSemantics()
        {
            using (var control = new OwnedMaterial(_shader))
            {
                control.Value.SetVector("_Color", new Vector4(0.25f, 0.5f, 0f, 0f));
                Assert.That(MaterialOps.TrySetShaderProperty(_material, "_Color", JArray.Parse("[0.25,0.5]"), UnityJsonSerializer.Instance), Is.True);
                Assert.That(_material.GetVector("_Color"), Is.EqualTo(control.Value.GetVector("_Color")));
            }
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase(0f, true)]
        [TestCase(1f, false)]
        public void StructuredColorOnVectorRetainsAlphaAndConsistentNoOp(float originalW, bool expectedChange)
        {
            _material.SetVector("_Vector", new Vector4(2, 3, 4, originalW));
            int dirty = EditorUtility.GetDirtyCount(_material);
            Assert.That(
                MaterialOps.ApplyProperties(_material, JObject.Parse("{\"color\":{\"name\":\"_Vector\",\"value\":[2,3,4]}}"), UnityJsonSerializer.Instance),
                Is.EqualTo(expectedChange)
            );
            Assert.That(_material.GetVector("_Vector"), Is.EqualTo(new Vector4(2, 3, 4, 1)));
            if (!expectedChange)
                Assert.That(EditorUtility.GetDirtyCount(_material), Is.EqualTo(dirty));
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void BulkPropertiesRemainBestEffortAndSetStructuredIntegerZero()
        {
            Assert.That(
                MaterialOps.ApplyProperties(
                    _material,
                    JObject.Parse("{\"_Unknown\":3,\"float\":{\"name\":\"_Int\",\"value\":0},\"metallic\":0.5}"),
                    UnityJsonSerializer.Instance
                ),
                Is.True
            );
            Assert.That(_material.GetInteger("_Int"), Is.Zero);
            Assert.That(_material.GetFloat("_Metallic"), Is.EqualTo(0.5f));
            Assert.That(_material.GetTexture("_MainTex"), Is.SameAs(_texture));
        }

        private sealed class OwnedMaterial : IDisposable
        {
            public readonly Material Value;

            public OwnedMaterial(Shader shader)
            {
                Value = new Material(shader);
            }

            public void Dispose()
            {
                UnityEngine.Object.DestroyImmediate(Value);
            }
        }
    }
}
