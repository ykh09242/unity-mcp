using System;
using System.Reflection;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools
{
    public class ManageEditorDeploymentConsentTests
    {
        private object previousConsent;
        private object previousDeployment;
        private static FieldInfo ConsentField => typeof(MCPServiceLocator).GetField("_toolConsentService", BindingFlags.Static | BindingFlags.NonPublic);
        private static FieldInfo DeploymentField =>
            typeof(MCPServiceLocator).GetField("_packageDeploymentService", BindingFlags.Static | BindingFlags.NonPublic);

        [SetUp]
        public void SetUp()
        {
            previousConsent = ConsentField.GetValue(null);
            previousDeployment = DeploymentField.GetValue(null);
        }

        [TearDown]
        public void TearDown()
        {
            ConsentField.SetValue(null, previousConsent);
            DeploymentField.SetValue(null, previousDeployment);
        }

        [TestCase("deploy_package")]
        [TestCase("restore_package")]
        public void MissingRevokedDisabledOrUnconfirmedConsentNeverEntersDeployment(string action)
        {
            var deployment = new Deployment();
            var consent = new Consent();
            MCPServiceLocator.Register<IPackageDeploymentService>(deployment);
            MCPServiceLocator.Register<IToolConsentService>(consent);
            foreach (
                var status in new[]
                {
                    new ToolConsentStatus(ToolConsentScope.None, false, false, false, null),
                    new ToolConsentStatus(ToolConsentScope.None, false, true, false, "explicit_grant_required"),
                    new ToolConsentStatus(ToolConsentScope.Project, false, false, false, null),
                    new ToolConsentStatus(ToolConsentScope.EditorSession, true, false, false, "storage_unconfirmed"),
                }
            )
            {
                consent.Status = status;
                var response = JObject.FromObject(ManageEditor.HandleCommand(new JObject { ["action"] = action }));
                Assert.That(response.Value<bool>("success"), Is.False, response.ToString());
                Assert.That(deployment.Calls, Is.Zero, "Refusal must precede preference, backup and package effects.");
            }
            Assert.That(consent.CheckedName, Is.EqualTo("manage_packages"));
        }

        [TestCase("deploy_package", ToolConsentScope.Project)]
        [TestCase("restore_package", ToolConsentScope.EditorSession)]
        public void LiveGrantedConsentAllowsOneCallAndRevocationBlocksTheNext(string action, ToolConsentScope scope)
        {
            var deployment = new Deployment();
            var consent = new Consent { Status = new ToolConsentStatus(scope, true, false, false, null) };
            MCPServiceLocator.Register<IPackageDeploymentService>(deployment);
            MCPServiceLocator.Register<IToolConsentService>(consent);
            Assert.That(JObject.FromObject(ManageEditor.HandleCommand(new JObject { ["action"] = action })).Value<bool>("success"), Is.True);
            Assert.That(deployment.Calls, Is.EqualTo(1));
            Assert.That(deployment.LastAction, Is.EqualTo(action));
            consent.Status = new ToolConsentStatus(ToolConsentScope.None, false, true, false, "explicit_grant_required");
            Assert.That(JObject.FromObject(ManageEditor.HandleCommand(new JObject { ["action"] = action })).Value<bool>("success"), Is.False);
            Assert.That(deployment.Calls, Is.EqualTo(1));
        }

        private sealed class Consent : IToolConsentService
        {
            internal ToolConsentStatus Status;
            internal string CheckedName;

            public ToolConsentStatus GetStatus(string name)
            {
                CheckedName = name;
                return Status;
            }

            public ToolConsentChangeResult Grant(string name, ToolConsentScope scope) =>
                throw new InvalidOperationException("No grant may be created by a tool call.");

            public ToolConsentChangeResult Revoke(string name) => throw new InvalidOperationException();

            public ToolConsentChangeResult SetEnabled(string name, bool enabled) => throw new InvalidOperationException();
        }

        private sealed class Deployment : IPackageDeploymentService
        {
            internal int Calls;
            internal string LastAction;

            public PackageDeploymentResult DeployFromStoredSource() => Complete("deploy_package");

            public PackageDeploymentResult RestoreLastBackup() => Complete("restore_package");

            private PackageDeploymentResult Complete(string action)
            {
                Calls++;
                LastAction = action;
                return new PackageDeploymentResult { Success = true, Message = "fixture" };
            }

            public string GetStoredSourcePath() => throw new InvalidOperationException();

            public void SetStoredSourcePath(string path) => throw new InvalidOperationException();

            public void ClearStoredSourcePath() => throw new InvalidOperationException();

            public string GetTargetPath() => throw new InvalidOperationException();

            public string GetTargetDisplayPath() => throw new InvalidOperationException();

            public string GetLastBackupPath() => throw new InvalidOperationException();

            public bool HasBackup() => throw new InvalidOperationException();
        }
    }
}
