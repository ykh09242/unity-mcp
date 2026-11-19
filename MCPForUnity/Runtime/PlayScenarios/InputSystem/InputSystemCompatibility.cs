#if MCP_INPUT_SYSTEM
using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEngine.Experimental.Input;
using UnityEngine.Experimental.Input.LowLevel;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;

// Native device and state types moved together. Empty namespaces let the C# compiler
// resolve the installed definitions without guessing the namespace from a package version.
namespace UnityEngine.Experimental.Input.LowLevel { }

namespace UnityEngine.Experimental.Input.Controls { }

namespace UnityEngine.Experimental.Input.Plugins.Users { }

namespace UnityEngine.InputSystem.LowLevel { }

namespace UnityEngine.InputSystem.Controls { }

namespace UnityEngine.InputSystem.Users { }

namespace UnityEngine.InputSystem.Plugins.Users { }

namespace MCPForUnity.Editor.Tools.Input
{
    public static class InputSystemCompatibility
    {
        private static readonly Assembly Package = typeof(InputDevice).Assembly;
        private static readonly PropertyInfo IdProperty = typeof(InputDevice).GetProperty("deviceId") ?? typeof(InputDevice).GetProperty("id");
        private static readonly Func<InputDevice, int> ReadDeviceId =
            IdProperty?.PropertyType == typeof(int) ? (Func<InputDevice, int>)IdProperty.GetGetMethod().CreateDelegate(typeof(Func<InputDevice, int>)) : null;
        private static readonly PropertyInfo CurrentUpdate = Package
            .GetType(typeof(InputDevice).Namespace + ".LowLevel.InputState")
            ?.GetProperty("currentUpdateType");
        private static readonly Func<InputUpdateType> ReadUpdate =
            CurrentUpdate == null ? null : (Func<InputUpdateType>)Delegate.CreateDelegate(typeof(Func<InputUpdateType>), CurrentUpdate.GetGetMethod());
        private static readonly InputSettings.UpdateMode? FixedMode =
            Enum.TryParse("ProcessEventsInFixedUpdate", out InputSettings.UpdateMode mode) || Enum.TryParse("ProcessEventsInFixedUpdateOnly", out mode)
                ? mode
                : (InputSettings.UpdateMode?)null;

        public static int? DeviceId(InputDevice device) => device == null ? null : ReadDeviceId?.Invoke(device);

        public static InputUpdateType CurrentUpdateType => ReadUpdate?.Invoke() ?? default;

        public static bool IsSimulationUpdate(InputUpdateType update) =>
            update == (InputSystem.settings.updateMode == FixedMode ? InputUpdateType.Fixed : InputUpdateType.Dynamic);

        public static string Setting(string property) => typeof(InputSettings).GetProperty(property)?.GetValue(InputSystem.settings)?.ToString();

        public enum TouchTransition
        {
            Ignore,
            Press,
            Move,
            Release,
        }

        // The native phase type changed from PointerPhase to TouchPhase and the
        // cancellation name changed spelling. Resolve the enum once per native type.
        private static class Phases<T>
            where T : struct, Enum
        {
            internal static readonly T Began = (T)Enum.Parse(typeof(T), "Began");
            internal static readonly T Moved = (T)Enum.Parse(typeof(T), "Moved");
            internal static readonly T Ended = (T)Enum.Parse(typeof(T), "Ended");
            internal static readonly T Canceled = Enum.TryParse("Canceled", out T value) ? value : (T)Enum.Parse(typeof(T), "Cancelled");
        }

        public static TouchTransition ReadTouchPhase<T>(T phase)
            where T : struct, Enum
        {
            var comparer = EqualityComparer<T>.Default;
            if (comparer.Equals(phase, Phases<T>.Began))
                return TouchTransition.Press;
            if (comparer.Equals(phase, Phases<T>.Moved))
                return TouchTransition.Move;
            return comparer.Equals(phase, Phases<T>.Ended) || comparer.Equals(phase, Phases<T>.Canceled) ? TouchTransition.Release : TouchTransition.Ignore;
        }

        internal static T WriteTouchPhase<T>(T _, TouchTransition transition)
            where T : struct, Enum =>
            transition == TouchTransition.Press ? Phases<T>.Began
            : transition == TouchTransition.Move ? Phases<T>.Moved
            : Phases<T>.Ended;

        public static Type PlayerInputType =>
            Package.GetType(typeof(InputDevice).Namespace + ".PlayerInput")
            ?? Package.GetType(typeof(InputDevice).Namespace + ".PlayerInput.PlayerInput")
            ?? Package.GetType(typeof(InputDevice).Namespace + ".Plugins.PlayerInput.PlayerInput");
    }
}
#endif
