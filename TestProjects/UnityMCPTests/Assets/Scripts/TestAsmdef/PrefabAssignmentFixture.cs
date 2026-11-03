using System;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class PrefabAssignmentFixture : MonoBehaviour
    {
        public int backing = 2;
        public bool failRestore;

        public int Value
        {
            get => backing;
            set
            {
                backing = value;
                if (value == 9 || (failRestore && value == 2))
                    throw new InvalidOperationException("injected setter failure");
            }
        }
    }
}
