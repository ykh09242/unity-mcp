using UnityEngine;
using UnityEngine.Events;
using UnityEngine.Serialization;

namespace MCPForUnityTests.Editor.Tools
{
    public sealed class SerializedRenameFixture : MonoBehaviour
    {
        [FormerlySerializedAs("oldCount")]
        public int newCount;
        public UnityEvent clicked = new UnityEvent();

        public void RenamedClick() { }
    }
}
