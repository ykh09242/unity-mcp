using System.Collections;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public sealed class ShopAndroidProbe : MonoBehaviour
    {
        public const string Scenario = "verified-shop";
        public const string ApplicationId = "com.ykh09242.mcpverifiedshopfixture";

        public string BuildId;
        public string SourceRevision;

#if UNITY_ANDROID && !UNITY_EDITOR
        [System.Serializable]
        private sealed class Result
        {
            public int schema_version = 1;
            public string app_id;
            public string scenario;
            public string build_id;
            public string source_revision;
            public string launch_id;
            public string unity_version;
            public bool ready;
        }

        private IEnumerator Start()
        {
            // Read after ShopWorkflowProbe.Start has loaded and instantiated the banner.
            yield return null;
            string launchId;
            using (var unity = new AndroidJavaClass("com.unity3d.player.UnityPlayer"))
            using (var activity = unity.GetStatic<AndroidJavaObject>("currentActivity"))
            using (var intent = activity.Call<AndroidJavaObject>("getIntent"))
                launchId = intent.Call<string>("getStringExtra", "mcp_fixture_launch_id");
            if (!System.Guid.TryParseExact(launchId, "N", out _))
                yield break;
            var shop = GetComponent<ShopWorkflowProbe>();
            var loaded = ShopWorkflowProbe.LoadBanner();
            bool ready =
                shop != null
                && shop.Banner != null
                && loaded == shop.Banner
                && shop.transform.childCount == 1
                && shop.transform.GetChild(0).gameObject.activeInHierarchy;
            var result = new Result
            {
                app_id = Application.identifier,
                scenario = Scenario,
                build_id = BuildId,
                source_revision = SourceRevision,
                launch_id = launchId,
                unity_version = Application.unityVersion,
                ready = ready,
            };
            Debug.Log("MCP_ANDROID_FIXTURE_RESULT " + JsonUtility.ToJson(result));
        }
#endif
    }
}
