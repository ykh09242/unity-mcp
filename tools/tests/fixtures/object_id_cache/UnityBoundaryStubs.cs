using System;

namespace UnityEngine
{
    public class Object
    {
        public int GetInstanceID() => 1;
    }
}

namespace UnityEditor
{
    public enum PlayModeStateChange
    {
        EnteredEditMode,
    }

    public static class EditorApplication
    {
        public static double timeSinceStartup;
        public static event Action update;
        public static event Action quitting;
        public static event Action<PlayModeStateChange> playModeStateChanged;
        public static int Subscribers =>
            (update?.GetInvocationList().Length ?? 0) + (quitting?.GetInvocationList().Length ?? 0) + (playModeStateChanged?.GetInvocationList().Length ?? 0);

        public static void Tick() => update?.Invoke();

        public static void Quit() => quitting?.Invoke();

        public static void Transition() => playModeStateChanged?.Invoke(PlayModeStateChange.EnteredEditMode);
    }

    public static class AssemblyReloadEvents
    {
        public static event Action beforeAssemblyReload;
        public static int Subscribers => beforeAssemblyReload?.GetInvocationList().Length ?? 0;

        public static void Reload() => beforeAssemblyReload?.Invoke();
    }

    public static class EditorUtility
    {
        public static UnityEngine.Object InstanceIDToObject(int id) => null;
    }
}
