using UnityEngine;

namespace MCPForUnityTests.Editor.Tools.Fixtures
{
    public class PrefabPreviewLifecycleProbe : MonoBehaviour
    {
        public static bool DriftOnEdit;
        public Transform Reference;
        public int[] Values = new int[0];

        public Transform DrivenReference
        {
            get => Reference;
            set
            {
                Reference = value;
                if (DriftOnEdit && transform is RectTransform rect)
                    rect.anchoredPosition = new Vector2(777, 888);
            }
        }
    }
}
