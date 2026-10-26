using System;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using NUnit.Framework;

namespace MCPForUnity.Editor.Tests.EditMode.Services
{
    public class ToolOperationIdentityTests
    {
        [TestCase("get_selection", "MCPForUnity.Editor.Resources.Editor.Selection")]
        [TestCase("get_editor_state", "MCPForUnity.Editor.Resources.Editor.EditorState")]
        [TestCase("export_test_job", "MCPForUnity.Editor.Tools.ExportTestJob")]
        [TestCase("manage_packages", "MCPForUnity.Editor.Tools.ManagePackages")]
        [TestCase("manage_script", "MCPForUnity.Editor.Tools.ManageScript")]
        [TestCase("query_physics", "MCPForUnity.Editor.Tools.Capabilities.QueryPhysics")]
        [TestCase("batch_execute", "MCPForUnity.Editor.Tools.BatchExecute")]
        public void OnlyTheCanonicalTypeFromThePackageAssemblyIsAccepted(string command, string owner)
        {
            // Given
            Type canonical = typeof(ToolOperationIdentity).Assembly.GetType(owner, true);
            // When / Then
            Assert.That(ToolOperationIdentity.IsCanonicalHandler(command, canonical), Is.True);
        }

        [TestCase("get_selection")]
        [TestCase("get_editor_state")]
        [TestCase("export_test_job")]
        [TestCase("query_physics")]
        [TestCase("batch_execute")]
        public void AnotherTypeInTheRealPackageAssemblyDoesNotInheritTrustedEffects(string command)
        {
            // Given: both types belong to the real Editor assembly, unlike ordinary test fixtures.
            Type wrongOwner = typeof(NativeCommandArbitrator);
            // When / Then
            Assert.That(wrongOwner.Assembly, Is.SameAs(typeof(ToolOperationIdentity).Assembly));
            Assert.That(ToolOperationIdentity.IsCanonicalHandler(command, wrongOwner), Is.False);
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase("GET_SELECTION")]
        [TestCase("ping")]
        [TestCase("execute_code")]
        [TestCase("future_read")]
        public void MissingOrUnreviewedOwnersFailClosed(string command)
        {
            // Given
            Type selection = typeof(ToolOperationIdentity).Assembly.GetType("MCPForUnity.Editor.Resources.Editor.Selection", true);
            // When / Then
            Assert.That(ToolOperationIdentity.IsCanonicalHandler(command, selection), Is.False);
        }

        [Test]
        public void NullAndForeignAssemblyTypesFailClosed()
        {
            // Given / When / Then
            Assert.That(ToolOperationIdentity.IsCanonicalHandler("get_selection", null), Is.False);
            Assert.That(ToolOperationIdentity.IsCanonicalHandler("get_selection", GetType()), Is.False);
        }
    }
}
