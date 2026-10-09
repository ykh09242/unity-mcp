using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class FrameDebuggerReflectionLookupTests
    {
        private static Type Ops => typeof(GameObjectSerializer).Assembly.GetType("MCPForUnity.Editor.Tools.Profiler.FrameDebuggerOps", true);

        [Test]
        public void PublicFieldsDoNotTriggerUnusedPropertyMetadataQueries()
        {
            var type = new CountingType(typeof(PublicFields));
            var instance = new PublicFields();
            for (int i = 0; i < 500; i++)
            {
                instance.Value = i;
                Assert.AreEqual(i, Read(type, instance, "Value"));
            }
            Assert.AreEqual(500, type.FieldCalls);
            Assert.AreEqual(0, type.PropertyCalls);
        }

        [Test]
        public void FieldWinsOverInheritedPropertyAndNullFieldUsesAlias()
        {
            var instance = new FieldOverProperty();
            Assert.AreEqual("field", Read(instance.GetType(), instance, "Value"));
            instance.Value = null;
            Assert.AreEqual("alias", Read(instance.GetType(), instance, "Value", "Alias"));
            Assert.IsNull(Read(instance.GetType(), instance, "Value"));
            Assert.AreEqual(0, instance.PropertyReads);
        }

        [Test]
        public void FieldAndPropertyVisibilityAndInheritanceStayUnchanged()
        {
            var instance = new VisibilityFixture();
            Assert.AreEqual(11, Read(instance.GetType(), instance, "PublicField"));
            Assert.AreEqual(12, Read(instance.GetType(), instance, "PrivateField"));
            Assert.AreEqual(13, Read(instance.GetType(), instance, "PublicProperty"));
            Assert.AreEqual(14, Read(instance.GetType(), instance, "PrivateProperty"));
            Assert.AreEqual(21, Read(instance.GetType(), instance, "InheritedField"));
            Assert.AreEqual(22, Read(instance.GetType(), instance, "InheritedProperty"));
            Assert.AreEqual(23, Read(instance.GetType(), instance, "ProtectedProperty"));
            Assert.IsNull(Read(instance.GetType(), instance, "BasePrivateField"));
            Assert.IsNull(Read(instance.GetType(), instance, "BasePrivateProperty"));
        }

        [Test]
        public void ThrowingOrMissingPropertyStillFallsBackToAlias()
        {
            var instance = new ThrowingProperty();
            Assert.AreEqual("fallback", Read(instance.GetType(), instance, "Value", "Alias"));
            Assert.AreEqual("fallback", Read(instance.GetType(), instance, "Missing", "Alias"));
            Assert.IsNull(Read(instance.GetType(), instance, "Value"));
            Assert.IsNull(Read(instance.GetType(), instance, "Missing"));
            Assert.IsNull(Read(instance.GetType(), new object(), "Alias"));
        }

        [Test]
        public void PropertyValuesRemainFreshAcrossObjectsAndCalls()
        {
            var type = new CountingType(typeof(PropertyFixture));
            var first = new PropertyFixture { Value = 31 };
            var second = new PropertyFixture { Value = 32 };
            Assert.AreEqual(31, Read(type, first, "Value"));
            Assert.AreEqual(32, Read(type, second, "Value"));
            first.Value = 33;
            Assert.AreEqual(33, Read(type, first, "Value"));
            Assert.AreEqual(3, type.PropertyCalls);
        }

        [Test]
        public void OutputRenamingEnumFormattingAndNullOmissionArePreserved()
        {
            var values = new Dictionary<string, object>();
            var add = Ops.GetMethod("TryAddField", BindingFlags.Static | BindingFlags.NonPublic);
            var instance = new EnumFixture();
            add.Invoke(null, new object[] { instance.GetType(), instance, "Value", values, "event_type", null });
            add.Invoke(null, new object[] { instance.GetType(), instance, "Missing", values, null, null });
            Assert.AreEqual("Friday", values["event_type"]);
            Assert.IsFalse(values.ContainsKey("Value"));
            Assert.IsFalse(values.ContainsKey("Missing"));
        }

        private static object Read(Type type, object instance, string name, string alias = null) =>
            Ops.GetMethod("ReadFieldOrProperty", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { type, instance, name, alias });

        private class CountingType : TypeDelegator
        {
            public int FieldCalls;
            public int PropertyCalls;

            public CountingType(Type type)
                : base(type) { }

            public override FieldInfo GetField(string name, BindingFlags flags)
            {
                FieldCalls++;
                return base.GetField(name, flags);
            }

            protected override PropertyInfo GetPropertyImpl(
                string name,
                BindingFlags flags,
                Binder binder,
                Type returnType,
                Type[] types,
                ParameterModifier[] modifiers
            )
            {
                PropertyCalls++;
                return base.GetPropertyImpl(name, flags, binder, returnType, types, modifiers);
            }
        }

        private class PublicFields
        {
            public int Value;
        }

        private class BaseProperty
        {
            public int PropertyReads;
            public string Value
            {
                get
                {
                    PropertyReads++;
                    return "property";
                }
            }
        }

        private class FieldOverProperty : BaseProperty
        {
            public new string Value = "field";
            public string Alias = "alias";
        }

        private class VisibilityBase
        {
            public int InheritedField = 21;
            public int InheritedProperty => 22;
            protected int ProtectedProperty => 23;
#pragma warning disable 0414
            private int BasePrivateField = 24;
#pragma warning restore 0414
            private int BasePrivateProperty => 25;
        }

        private class VisibilityFixture : VisibilityBase
        {
            public int PublicField = 11;
#pragma warning disable 0414
            private int PrivateField = 12;
#pragma warning restore 0414
            public int PublicProperty => 13;
            private int PrivateProperty => 14;
        }

        private class ThrowingProperty
        {
            public string Value => throw new InvalidOperationException("unavailable");
            public string Alias = "fallback";
        }

        private class PropertyFixture
        {
            public int Value { get; set; }
        }

        private class EnumFixture
        {
            public DayOfWeek Value = DayOfWeek.Friday;
        }
    }
}
