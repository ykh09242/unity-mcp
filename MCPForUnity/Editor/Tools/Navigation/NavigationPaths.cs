#if MCP_FOR_UNITY_AI_MODULE
using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.AI;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.Navigation
{
    internal static class NavigationPaths
    {
        internal static object Query(JObject p, bool calculatePath)
        {
            Scene scene = NavigationAccess.Scene(p);
            int agentType = NavigationAccess.Integer(p, "agent_type_id");
            NavigationAccess.AgentType(agentType);
            int areaMask = NavigationAccess.Integer(p, "area_mask", NavMesh.AllAreas);
            float distance = p["max_distance"] == null ? 2 : NavigationAccess.Number(p["max_distance"], "max_distance");
            if (distance <= 0 || distance > 100)
                throw new ArgumentException("max_distance must be greater than zero and at most 100.");
            Vector3 start = NavigationAccess.Vector(p[calculatePath ? "start" : "position"], calculatePath ? "start" : "position");
            Vector3 end = calculatePath ? NavigationAccess.Vector(p["end"], "end") : start;
            var reasons = new List<string>();
            var surfaces = new JArray();
            Type type = NavigationAccess.OptionalType("NavMeshSurface");
            bool matchingData = false;
            if (type == null)
                return new ErrorResponse(
                    "ai_navigation_package_unavailable: scene-attributed navigation queries require com.unity.ai.navigation surfaces. No package was installed."
                );
            // Known authored links in foreign scenes also contaminate a global native query.
            Type linkType = NavigationAccess.OptionalType("NavMeshLink");
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene foreign = SceneManager.GetSceneAt(i);
                if (!foreign.isLoaded || foreign == scene)
                    continue;
                if (linkType != null)
                    foreach (Component link in NavigationObjects.InScene(foreign, linkType))
                        if (link is Behaviour b && b.isActiveAndEnabled && (int)NavigationAccess.Get(link, "agentTypeID") == agentType)
                            return new ErrorResponse("foreign_scene_link: a foreign-scene navigation link prevents scene-attributed global path diagnosis.");
                foreach (OffMeshLink link in NavigationObjects.InScene(foreign, typeof(OffMeshLink)))
                    if (link.isActiveAndEnabled && link.activated)
                        return new ErrorResponse("foreign_scene_link: an active legacy OffMeshLink in another scene prevents scene attribution.");
            }
            if (type != null)
            {
                // Unity's high-level native query API has no scene argument. Refuse known foreign data instead of falsely claiming scene isolation.
                for (int i = 0; i < SceneManager.sceneCount; i++)
                {
                    Scene candidate = SceneManager.GetSceneAt(i);
                    if (!candidate.isLoaded)
                        continue;
                    foreach (Component surface in NavigationObjects.InScene(candidate, type))
                    {
                        if (!(surface is Behaviour behaviour) || !behaviour.isActiveAndEnabled)
                            continue;
                        NavMeshData data = NavigationAccess.Get(surface, "navMeshData") as NavMeshData;
                        if (data == null)
                            continue;
                        if (candidate != scene)
                            return new ErrorResponse(
                                "foreign_scene_navmesh: an active foreign-scene surface has baked data. Its baked agent type is unavailable, so global query scene attribution is refused conservatively.",
                                new
                                {
                                    scene_handle = NavigationAccess.SceneHandle(scene),
                                    foreign_scene_handle = NavigationAccess.SceneHandle(candidate),
                                    component_id = surface.GetInstanceIDCompat(),
                                    agent_type_id = agentType,
                                    surface_authoring_agent_type_id = (int)NavigationAccess.Get(surface, "agentTypeID"),
                                    baked_agent_type_available = false,
                                    scope_supported = false,
                                }
                            );
                        int authoringAgentType = (int)NavigationAccess.Get(surface, "agentTypeID");
                        bool authoringMatches = authoringAgentType == agentType;
                        matchingData |= authoringMatches;
                        surfaces.Add(
                            new JObject
                            {
                                ["component_id"] = surface.GetInstanceIDCompat(),
                                ["surface_authoring_agent_type_id"] = authoringAgentType,
                                ["matches_requested_authoring_agent_type"] = authoringMatches,
                                ["baked_agent_type_id"] = null,
                                ["baked_agent_type_available"] = false,
                            }
                        );
                    }
                }
            }
            if (!matchingData)
                reasons.Add("no_active_surface_with_data_and_requested_authoring_agent_type_in_selected_scene");
            if (surfaces.Count > 0)
                reasons.Add("baked_agent_type_unavailable_surface_authoring_configuration_only");
            if (areaMask == 0)
                reasons.Add("area_mask_excludes_all");
            var filter = new NavMeshQueryFilter { agentTypeID = agentType, areaMask = areaMask };
            NavMeshHit source = default;
            NavMeshHit destination = default;
            bool sourceFound = matchingData && areaMask != 0 && NavMesh.SamplePosition(start, out source, distance, filter);
            bool destinationFound = calculatePath && matchingData && areaMask != 0 && NavMesh.SamplePosition(end, out destination, distance, filter);
            if (!sourceFound)
                reasons.Add("start_not_sampled_for_agent_and_area");
            if (calculatePath && !destinationFound)
                reasons.Add("end_not_sampled_for_agent_and_area");
            NavMeshPathStatus status = NavMeshPathStatus.PathInvalid;
            var corners = new JArray();
            int cornerCount = 0;
            bool calculated = false;
            if (calculatePath && sourceFound && destinationFound)
            {
                var path = new NavMeshPath();
                calculated = NavMesh.CalculatePath(source.position, destination.position, filter, path);
                status = path.status;
                Vector3[] allCorners = path.corners;
                cornerCount = allCorners.Length;
                foreach (Vector3 corner in allCorners.Take(256))
                    corners.Add(NavigationAccess.Vector(corner));
                if (status == NavMeshPathStatus.PathPartial)
                    reasons.Add("disconnected_or_blocked_for_agent_and_area");
                if (status == NavMeshPathStatus.PathInvalid)
                    reasons.Add("native_path_invalid");
            }
            JObject result = new JObject
            {
                ["scene_handle"] = NavigationAccess.SceneHandle(scene),
                ["scene_path"] = scene.path,
                ["agent_type_id"] = agentType,
                ["area_mask"] = areaMask,
                ["max_distance"] = distance,
                ["surfaces"] = surfaces,
                ["start_sampled"] = sourceFound,
                ["start_position"] = sourceFound ? NavigationAccess.Vector(source.position) : null,
                ["start_area_mask"] = sourceFound ? new JValue(source.mask) : null,
                ["diagnostics"] = JArray.FromObject(reasons),
                ["query_scope"] = "global_native_mesh_with_known_surface_scene_validation",
                ["limitations"] =
                    "Baked agent-type provenance is unavailable; surface agentTypeID is current authoring configuration only. Native queries apply the requested agent filter. Runtime-added NavMeshData and NavMesh.AddLink data without authoring components cannot be attributed to scenes. Path results are observations, not proof of agent movement. No bake, simulation or settings changes were performed.",
            };
            if (calculatePath)
            {
                result["end_sampled"] = destinationFound;
                result["end_position"] = destinationFound ? NavigationAccess.Vector(destination.position) : null;
                result["end_area_mask"] = destinationFound ? new JValue(destination.mask) : null;
                result["path_status"] = status.ToString();
                result["native_calculated"] = calculated;
                result["corners"] = corners;
                result["corner_limit"] = 256;
                result["corner_count"] = cornerCount;
                result["corners_truncated"] = cornerCount > 256;
            }
            return new SuccessResponse(calculatePath ? "Navigation path diagnosis." : "Navigation position sample.", result);
        }
    }
}
#endif
