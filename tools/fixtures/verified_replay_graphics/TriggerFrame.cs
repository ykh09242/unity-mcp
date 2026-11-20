using UnityEngine;

namespace VerifiedWorkflows
{
    // Independent visible result of contact with the controlled mover.
    public sealed class TriggerFrame : MonoBehaviour
    {
        public Camera View;
        public Rigidbody Mover;

        private void Awake()
        {
            Screen.SetResolution(640, 360, FullScreenMode.Windowed);
            View.backgroundColor = Color.red;
        }

        private void OnTriggerEnter(Collider other)
        {
            if (other.attachedRigidbody == Mover)
                View.backgroundColor = Color.green;
        }
    }
}
