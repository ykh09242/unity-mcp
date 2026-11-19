#if MCP_FOR_UNITY_PHYSICS && MCP_FOR_UNITY_PHYSICS2D
using System;
using System.Reflection;

namespace MCPForUnity.Editor.Tools.Physics
{
    internal static class PhysicsProperty
    {
        internal static T? Read<T>(Type owner, object target, string name)
            where T : struct
        {
            try
            {
                var property = owner.GetProperty(name, BindingFlags.Public | BindingFlags.Instance, null, typeof(T), Type.EmptyTypes, null);
                if (property?.GetGetMethod() != null)
                    return (T)property.GetValue(target);
            }
            catch (Exception e) when (e is TargetInvocationException || e is AmbiguousMatchException || e is MemberAccessException)
            {
                // Diagnostics can still describe the other readable properties.
            }
            return null;
        }
    }
}
#endif
