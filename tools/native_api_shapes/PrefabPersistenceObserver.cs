using System;

namespace MCPForUnity.Editor.Tools.Addressables
{
    internal static partial class AddressablePrefabWorkflow
    {
        internal static Action<string> PersistenceObserver;

        static partial void ObserveDurableWrite(string boundary) => PersistenceObserver?.Invoke(boundary);
    }
}
