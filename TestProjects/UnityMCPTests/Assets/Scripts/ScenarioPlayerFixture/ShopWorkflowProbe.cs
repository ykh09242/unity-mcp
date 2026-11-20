using System;
using System.Linq;
using MCPForUnity.Runtime;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;
using UnityEngine.UI;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public sealed class ShopWorkflowProbe : MonoBehaviour, IPlayScenarioStateProvider
    {
        public const string BannerKey = "VerifiedShop/UI/ShopBanner";
        public GameObject Banner;
        private IDisposable registration;
        private GameObject displayed;

        [Serializable]
        private sealed class Observation
        {
            public string key;
            public string expected;
            public string loaded;
            public bool same_asset;
            public int visible_banners;
        }

        public static GameObject LoadBanner()
        {
            GameObject loaded = Resources.Load<GameObject>(BannerKey);
            ReferenceGraphRuntime.Load("resources", BannerKey, loaded, "Assets/Scripts/ScenarioPlayerFixture/ShopWorkflowProbe.cs");
            return loaded;
        }

        // Preview keys come from callers and need a runtime observation to resolve.
        public static GameObject LoadPreview(string runtimeKey) => Resources.Load<GameObject>(runtimeKey);

        private void OnEnable() => registration = PlayScenarioStateRegistry.Register("shop.ready", this);

        private void OnDisable() => registration?.Dispose();

        private void Start()
        {
            GameObject loaded = LoadBanner();
            if (loaded != null)
                displayed = Instantiate(loaded, transform);
            var observation = new Observation
            {
                key = BannerKey,
                expected = Banner == null ? "" : Banner.name,
                loaded = loaded == null ? "" : loaded.name,
                same_asset = loaded != null && loaded == Banner,
                visible_banners = GetComponentsInChildren<Image>().Count(surface => surface.isActiveAndEnabled),
            };
            Debug.Log("SHOP_WORKFLOW_OBSERVATION " + JsonUtility.ToJson(observation));
        }

        public bool TryRead(out PlayScenarioStateValue value)
        {
            value = PlayScenarioStateValue.FromBoolean(displayed != null && displayed.activeInHierarchy);
            return true;
        }
    }
}
