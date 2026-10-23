using System;
using System.Collections.Generic;
using System.Reflection;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Field inspection must not enter user getters, converters or serialization callbacks.</summary>
    internal sealed class SafeComponentContractResolver : DefaultContractResolver
    {
        private readonly bool includePrivate;

        internal SafeComponentContractResolver(bool includePrivate) => this.includePrivate = includePrivate;

        protected override JsonContract CreateContract(Type type)
        {
            // These exact runtime shapes have framework-owned serialization/enumeration.
            // User collection subclasses use the field contract below instead of their enumerator.
            Type scalar = Nullable.GetUnderlyingType(type) ?? type;
            if (
                scalar.IsPrimitive
                || scalar.IsEnum
                || scalar == typeof(string)
                || scalar == typeof(decimal)
                || scalar == typeof(DateTime)
                || scalar == typeof(DateTimeOffset)
                || scalar == typeof(Guid)
                || scalar == typeof(TimeSpan)
                || scalar == typeof(Uri)
                || scalar == typeof(System.Numerics.BigInteger)
            )
                return base.CreateContract(type);
            if (typeof(JToken).IsAssignableFrom(type))
                return new JsonLinqContract(type);
            if (type.IsArray || (type.IsGenericType && type.GetGenericTypeDefinition() == typeof(List<>)))
                return base.CreateArrayContract(type);
            if (type.IsGenericType && type.GetGenericTypeDefinition() == typeof(Dictionary<,>))
            {
                Type key = type.GetGenericArguments()[0];
                if (key == typeof(string) || key.IsEnum || key.IsPrimitive || key == typeof(Guid))
                    return base.CreateDictionaryContract(type);
            }

            // Construct directly: DefaultContractResolver would discover user callbacks,
            // ShouldSerialize methods, custom converters, ISerializable and extension data.
            var contract = new JsonObjectContract(type) { MemberSerialization = MemberSerialization.Fields };
            var names = new HashSet<string>(StringComparer.Ordinal);
            for (Type current = type; current != null && current != typeof(object); current = current.BaseType)
            {
                foreach (FieldInfo field in current.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
                {
                    bool serialized = field.IsDefined(typeof(SerializeField), true) || field.IsDefined(typeof(SerializeReference), true);
                    if (
                        field.IsStatic
                        || field.IsNotSerialized
                        || (!field.IsPublic && !(includePrivate && serialized))
                        || (field.Name.EndsWith("k__BackingField", StringComparison.Ordinal) && !serialized)
                        || GameObjectSerializer.IsUnsafeInspectionType(field.FieldType)
                        || !names.Add(field.Name)
                    )
                        continue;
                    contract.Properties.Add(
                        new JsonProperty
                        {
                            PropertyName = field.Name,
                            UnderlyingName = field.Name,
                            PropertyType = field.FieldType,
                            DeclaringType = field.DeclaringType,
                            Readable = true,
                            Writable = false,
                            ValueProvider = new ReflectionValueProvider(field),
                        }
                    );
                }
            }
            return contract;
        }
    }
}
