namespace MCPForUnityTests
{
    // Deliberate PAC2002 source fixture; no scene object invokes this method.
    public static class ProjectAuditorAuditFixture
    {
        public static object AllocateForAudit()
        {
            return new object();
        }
    }
}
