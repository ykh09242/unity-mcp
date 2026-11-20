using UnityEngine;

namespace TestNamespace
{
    public sealed class BuildConditionRewardProbe : MonoBehaviour
    {
        public int CollectReward(int coins)
        {
#if !UNITY_EDITOR && MCP_FIXTURE_REWARD_DISABLED
            return coins;
#else
            return coins + 1;
#endif
        }

#if !UNITY_EDITOR
        private void Start()
        {
            int balance = CollectReward(0);
            Debug.Log("MCP_BUILD_CONDITION_REWARD " + Application.buildGUID + " " + balance);
            Application.Quit(balance == 1 ? 0 : 42);
        }
#endif
    }
}
