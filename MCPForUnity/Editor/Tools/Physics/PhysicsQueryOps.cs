#if MCP_FOR_UNITY_PHYSICS && MCP_FOR_UNITY_PHYSICS2D
using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Physics
{
    internal static class PhysicsQueryOps
    {
        private const int DefaultMaxResults = 200;
        private const int MaxQueryResults = 1000;

        public static object Raycast(JObject @params)
        {
            var p = new ToolParams(@params);
            string dimension = (p.Get("dimension") ?? "3d").ToLowerInvariant();

            var originArr = p.GetRaw("origin") as JArray;
            if (originArr == null)
                return new ErrorResponse("'origin' parameter is required (array of floats).");

            var dirArr = p.GetRaw("direction") as JArray;
            if (dirArr == null)
                return new ErrorResponse("'direction' parameter is required (array of floats).");

            float maxDistance = p.GetFloat("max_distance") ?? Mathf.Infinity;

            if (dimension == "2d")
                return ValidateTriggerOverride2D(p) ?? Raycast2D(originArr, dirArr, maxDistance, p);

            if (dimension != "3d")
                return new ErrorResponse($"Invalid dimension: '{dimension}'. Use '3d' or '2d'.");

            return Raycast3D(originArr, dirArr, maxDistance, p);
        }

        private static object Raycast3D(JArray originArr, JArray dirArr, float maxDistance, ToolParams p)
        {
            if (originArr.Count < 3)
                return new ErrorResponse("3D raycast 'origin' requires [x, y, z].");
            if (dirArr.Count < 3)
                return new ErrorResponse("3D raycast 'direction' requires [x, y, z].");

            var origin = new Vector3(originArr[0].ReadScalar<float>(), originArr[1].ReadScalar<float>(), originArr[2].ReadScalar<float>());
            var direction = new Vector3(dirArr[0].ReadScalar<float>(), dirArr[1].ReadScalar<float>(), dirArr[2].ReadScalar<float>());

            int layerMask = ResolveLayerMask(p.Get("layer_mask"));

            QueryTriggerInteraction triggerInteraction = QueryTriggerInteraction.UseGlobal;
            string qti = p.Get("query_trigger_interaction");
            if (!string.IsNullOrEmpty(qti))
            {
                if (!System.Enum.TryParse(qti, true, out triggerInteraction) || !System.Enum.IsDefined(typeof(QueryTriggerInteraction), triggerInteraction))
                    return new ErrorResponse($"Invalid query_trigger_interaction: '{qti}'. Valid: UseGlobal, Ignore, Collide.");
            }

            UnityEngine.Physics.SyncTransforms();

            bool hit = UnityEngine.Physics.Raycast(origin, direction, out RaycastHit hitInfo, maxDistance, layerMask, triggerInteraction);

            if (!hit)
            {
                return new
                {
                    success = true,
                    message = "Raycast did not hit anything.",
                    data = new { hit = false },
                };
            }

            var collider = hitInfo.collider;
            var hitGameObject = collider.gameObject;
            var hitGameObjectName = hitGameObject.name;

            return new
            {
                success = true,
                message = $"Raycast hit '{hitGameObjectName}'.",
                data = new
                {
                    hit = true,
                    point = new[] { hitInfo.point.x, hitInfo.point.y, hitInfo.point.z },
                    normal = new[] { hitInfo.normal.x, hitInfo.normal.y, hitInfo.normal.z },
                    distance = hitInfo.distance,
                    gameObject = hitGameObjectName,
                    instanceID = hitGameObject.GetInstanceIDCompat(),
                    collider_type = collider.GetType().Name,
                },
            };
        }

        private static object Raycast2D(JArray originArr, JArray dirArr, float maxDistance, ToolParams p)
        {
            if (originArr.Count < 2)
                return new ErrorResponse("2D raycast 'origin' requires [x, y].");
            if (dirArr.Count < 2)
                return new ErrorResponse("2D raycast 'direction' requires [x, y].");

            var origin = new Vector2(originArr[0].ReadScalar<float>(), originArr[1].ReadScalar<float>());
            var direction = new Vector2(dirArr[0].ReadScalar<float>(), dirArr[1].ReadScalar<float>());

            int layerMask = ResolveLayerMask(p.Get("layer_mask"));

            var triggerFilter = ResolveTriggerFilter2D(p, layerMask);
            Physics2D.SyncTransforms();

            var hit = triggerFilter.HasValue
                ? Physics2D.defaultPhysicsScene.Raycast(origin, direction, maxDistance, triggerFilter.Value)
                : Physics2D.Raycast(origin, direction, maxDistance, layerMask);

            var collider = hit.collider;
            if (collider == null)
            {
                return new
                {
                    success = true,
                    message = "2D Raycast did not hit anything.",
                    data = new { hit = false },
                };
            }

            var hitGameObject = collider.gameObject;
            var hitGameObjectName = hitGameObject.name;

            return new
            {
                success = true,
                message = $"2D Raycast hit '{hitGameObjectName}'.",
                data = new
                {
                    hit = true,
                    point = new[] { hit.point.x, hit.point.y },
                    normal = new[] { hit.normal.x, hit.normal.y },
                    distance = hit.distance,
                    gameObject = hitGameObjectName,
                    instanceID = hitGameObject.GetInstanceIDCompat(),
                    collider_type = collider.GetType().Name,
                },
            };
        }

        public static object Overlap(JObject @params)
        {
            var p = new ToolParams(@params);
            if (!TryResolveMaxResults(p, out int maxResults))
                return new ErrorResponse("'max_results' must be an integer from 1 to 1000.");
            string dimension = (p.Get("dimension") ?? "3d").ToLowerInvariant();
            string shape = p.Get("shape");
            if (string.IsNullOrEmpty(shape))
                return new ErrorResponse("'shape' parameter is required (sphere, box, capsule for 3D; circle, box, capsule for 2D).");

            var posArr = p.GetRaw("position") as JArray;
            if (posArr == null)
                return new ErrorResponse("'position' parameter is required (array of floats).");

            var sizeToken = p.GetRaw("size");
            if (sizeToken == null)
                return new ErrorResponse("'size' parameter is required.");

            int layerMask = ResolveLayerMask(p.Get("layer_mask"));

            if (dimension == "2d")
                return ValidateTriggerOverride2D(p) ?? Overlap2D(shape, posArr, sizeToken, layerMask, p, maxResults);

            if (dimension != "3d")
                return new ErrorResponse($"Invalid dimension: '{dimension}'. Use '3d' or '2d'.");

            return Overlap3D(shape, posArr, sizeToken, layerMask, p, maxResults);
        }

        private static object Overlap3D(string shape, JArray posArr, JToken sizeToken, int layerMask, ToolParams p, int maxResults)
        {
            if (!TryResolveOverlapTrigger(p, out QueryTriggerInteraction triggerInteraction))
                return new ErrorResponse("Invalid query_trigger_interaction. Valid: UseGlobal, Ignore, Collide.");
            if (posArr.Count < 3)
                return new ErrorResponse("3D overlap 'position' requires [x, y, z].");

            var position = new Vector3(posArr[0].ReadScalar<float>(), posArr[1].ReadScalar<float>(), posArr[2].ReadScalar<float>());

            var results = new Collider[maxResults];
            int count;

            switch (shape.ToLowerInvariant())
            {
                case "sphere":
                {
                    float radius = sizeToken.ReadScalar<float>();
                    UnityEngine.Physics.SyncTransforms();
                    count = UnityEngine.Physics.OverlapSphereNonAlloc(position, radius, results, layerMask, triggerInteraction);
                    break;
                }
                case "box":
                {
                    Vector3 halfExtents;
                    if (sizeToken is JArray sizeArr && sizeArr.Count >= 3)
                        halfExtents = new Vector3(sizeArr[0].ReadScalar<float>(), sizeArr[1].ReadScalar<float>(), sizeArr[2].ReadScalar<float>());
                    else
                        return new ErrorResponse("3D box overlap 'size' requires [halfX, halfY, halfZ].");
                    UnityEngine.Physics.SyncTransforms();
                    count = UnityEngine.Physics.OverlapBoxNonAlloc(position, halfExtents, results, Quaternion.identity, layerMask, triggerInteraction);
                    break;
                }
                case "capsule":
                {
                    if (sizeToken is JObject capsuleObj)
                    {
                        float radius = capsuleObj["radius"]?.ReadScalar<float?>() ?? 0.5f;
                        float height = capsuleObj["height"]?.ReadScalar<float?>() ?? 2f;
                        int direction = capsuleObj["direction"]?.ReadScalar<int?>() ?? 1;

                        Vector3 point0,
                            point1;
                        float halfHeight = Mathf.Max(0, height / 2f - radius);
                        switch (direction)
                        {
                            case 0: // X
                                point0 = position + Vector3.left * halfHeight;
                                point1 = position + Vector3.right * halfHeight;
                                break;
                            case 2: // Z
                                point0 = position + Vector3.back * halfHeight;
                                point1 = position + Vector3.forward * halfHeight;
                                break;
                            default: // Y (1)
                                point0 = position + Vector3.down * halfHeight;
                                point1 = position + Vector3.up * halfHeight;
                                break;
                        }

                        UnityEngine.Physics.SyncTransforms();
                        count = UnityEngine.Physics.OverlapCapsuleNonAlloc(point0, point1, radius, results, layerMask, triggerInteraction);
                    }
                    else
                    {
                        return new ErrorResponse("3D capsule overlap 'size' requires {radius, height, direction}.");
                    }
                    break;
                }
                default:
                    return new ErrorResponse($"Unknown 3D shape: '{shape}'. Valid: sphere, box, capsule.");
            }

            return FormatOverlapResults(results, count, "3D");
        }

        private static object Overlap2D(string shape, JArray posArr, JToken sizeToken, int layerMask, ToolParams p, int maxResults)
        {
            if (!TryResolveOverlapTrigger(p, out QueryTriggerInteraction triggerInteraction))
                return new ErrorResponse("Invalid query_trigger_interaction. Valid: UseGlobal, Ignore, Collide.");
            if (posArr.Count < 2)
                return new ErrorResponse("2D overlap 'position' requires [x, y].");

            var position = new Vector2(posArr[0].ReadScalar<float>(), posArr[1].ReadScalar<float>());

            var results = new Collider2D[maxResults];
            int count;
            var filter = CreateTriggerFilter2D(triggerInteraction, layerMask);

            switch (shape.ToLowerInvariant())
            {
                case "circle":
                {
                    float radius = sizeToken.ReadScalar<float>();
                    Physics2D.SyncTransforms();
                    count = Physics2D.OverlapCircle(position, radius, filter, results);
                    break;
                }
                case "box":
                {
                    Vector2 size;
                    if (sizeToken is JArray sizeArr && sizeArr.Count >= 2)
                        size = new Vector2(sizeArr[0].ReadScalar<float>(), sizeArr[1].ReadScalar<float>());
                    else
                        return new ErrorResponse("2D box overlap 'size' requires [width, height].");
                    Physics2D.SyncTransforms();
                    count = Physics2D.OverlapBox(position, size, 0f, filter, results);
                    break;
                }
                case "capsule":
                {
                    if (sizeToken is JObject capsuleObj)
                    {
                        float sizeX = capsuleObj["width"]?.ReadScalar<float?>() ?? 1f;
                        float sizeY = capsuleObj["height"]?.ReadScalar<float?>() ?? 2f;
                        var dir = CapsuleDirection2D.Vertical;
                        string dirStr = capsuleObj["direction"]?.ToString();
                        if (dirStr != null && dirStr.ToLowerInvariant() == "horizontal")
                            dir = CapsuleDirection2D.Horizontal;
                        Physics2D.SyncTransforms();
                        count = Physics2D.OverlapCapsule(position, new Vector2(sizeX, sizeY), dir, 0f, filter, results);
                    }
                    else
                    {
                        return new ErrorResponse("2D capsule overlap 'size' requires {width, height, direction}.");
                    }
                    break;
                }
                default:
                    return new ErrorResponse($"Unknown 2D shape: '{shape}'. Valid: circle, box, capsule.");
            }

            return FormatOverlapResults2D(results, count, "2D");
        }

        private static object FormatOverlapResults(Collider[] results, int count, string dimension)
        {
            var colliders = new List<object>(count);
            for (int i = 0; i < count; i++)
            {
                var col = results[i];
                var go = col.gameObject;
                colliders.Add(
                    new
                    {
                        gameObject = go.name,
                        instanceID = go.GetInstanceIDCompat(),
                        collider_type = col.GetType().Name,
                    }
                );
            }

            return new
            {
                success = true,
                message = $"Overlap query found {colliders.Count} collider(s) ({dimension}).",
                data = new
                {
                    colliders,
                    max_results = results.Length,
                    returned_count = count,
                    buffer_saturated = count == results.Length,
                    ordering = "unspecified",
                },
            };
        }

        private static object FormatOverlapResults2D(Collider2D[] results, int count, string dimension)
        {
            var colliders = new List<object>(count);
            for (int i = 0; i < count; i++)
            {
                var col = results[i];
                var go = col.gameObject;
                colliders.Add(
                    new
                    {
                        gameObject = go.name,
                        instanceID = go.GetInstanceIDCompat(),
                        collider_type = col.GetType().Name,
                    }
                );
            }

            return new
            {
                success = true,
                message = $"Overlap query found {colliders.Count} collider(s) ({dimension}).",
                data = new
                {
                    colliders,
                    max_results = results.Length,
                    returned_count = count,
                    buffer_saturated = count == results.Length,
                    ordering = "unspecified",
                },
            };
        }

        public static object Shapecast(JObject @params)
        {
            var p = new ToolParams(@params);
            string dimension = (p.Get("dimension") ?? "3d").ToLowerInvariant();
            string shape = p.Get("shape");
            if (string.IsNullOrEmpty(shape))
                return new ErrorResponse("'shape' parameter is required (sphere, box, capsule for 3D; circle, box, capsule for 2D).");

            var originArr = p.GetRaw("origin") as JArray;
            if (originArr == null)
                return new ErrorResponse("'origin' parameter is required (array of floats).");

            var dirArr = p.GetRaw("direction") as JArray;
            if (dirArr == null)
                return new ErrorResponse("'direction' parameter is required (array of floats).");

            float maxDistance = p.GetFloat("max_distance") ?? Mathf.Infinity;

            if (dimension == "2d")
                return ValidateTriggerOverride2D(p) ?? Shapecast2D(shape, originArr, dirArr, maxDistance, p);

            if (dimension != "3d")
                return new ErrorResponse($"Invalid dimension: '{dimension}'. Use '3d' or '2d'.");

            return Shapecast3D(shape, originArr, dirArr, maxDistance, p);
        }

        private static object Shapecast3D(string shape, JArray originArr, JArray dirArr, float maxDistance, ToolParams p)
        {
            if (originArr.Count < 3)
                return new ErrorResponse("3D shapecast 'origin' requires [x, y, z].");
            if (dirArr.Count < 3)
                return new ErrorResponse("3D shapecast 'direction' requires [x, y, z].");

            var origin = new Vector3(originArr[0].ReadScalar<float>(), originArr[1].ReadScalar<float>(), originArr[2].ReadScalar<float>());
            var direction = new Vector3(dirArr[0].ReadScalar<float>(), dirArr[1].ReadScalar<float>(), dirArr[2].ReadScalar<float>());

            int layerMask = ResolveLayerMask(p.Get("layer_mask"));

            QueryTriggerInteraction triggerInteraction = QueryTriggerInteraction.UseGlobal;
            string qti = p.Get("query_trigger_interaction");
            if (!string.IsNullOrEmpty(qti))
            {
                if (!System.Enum.TryParse(qti, true, out triggerInteraction) || !System.Enum.IsDefined(typeof(QueryTriggerInteraction), triggerInteraction))
                    return new ErrorResponse($"Invalid query_trigger_interaction: '{qti}'. Valid: UseGlobal, Ignore, Collide.");
            }

            var sizeToken = p.GetRaw("size");
            if (sizeToken == null)
                return new ErrorResponse("'size' parameter is required.");

            bool hit;
            RaycastHit hitInfo;

            switch (shape.ToLowerInvariant())
            {
                case "sphere":
                {
                    float radius = sizeToken.ReadScalar<float>();
                    UnityEngine.Physics.SyncTransforms();
                    hit = UnityEngine.Physics.SphereCast(origin, radius, direction, out hitInfo, maxDistance, layerMask, triggerInteraction);
                    break;
                }
                case "box":
                {
                    Vector3 halfExtents;
                    if (sizeToken is JArray sizeArr && sizeArr.Count >= 3)
                        halfExtents = new Vector3(sizeArr[0].ReadScalar<float>(), sizeArr[1].ReadScalar<float>(), sizeArr[2].ReadScalar<float>());
                    else
                        return new ErrorResponse("3D box shapecast 'size' requires [halfX, halfY, halfZ].");
                    UnityEngine.Physics.SyncTransforms();
                    hit = UnityEngine.Physics.BoxCast(
                        origin,
                        halfExtents,
                        direction,
                        out hitInfo,
                        Quaternion.identity,
                        maxDistance,
                        layerMask,
                        triggerInteraction
                    );
                    break;
                }
                case "capsule":
                {
                    float radius = sizeToken.ReadScalar<float>();
                    Vector3 pt1,
                        pt2;

                    var pt1Arr = p.GetRaw("point1") as JArray;
                    var pt2Arr = p.GetRaw("point2") as JArray;

                    if (pt1Arr != null && pt1Arr.Count >= 3 && pt2Arr != null && pt2Arr.Count >= 3)
                    {
                        pt1 = new Vector3(pt1Arr[0].ReadScalar<float>(), pt1Arr[1].ReadScalar<float>(), pt1Arr[2].ReadScalar<float>());
                        pt2 = new Vector3(pt2Arr[0].ReadScalar<float>(), pt2Arr[1].ReadScalar<float>(), pt2Arr[2].ReadScalar<float>());
                    }
                    else
                    {
                        float height = p.GetFloat("height") ?? 2f;
                        int capsuleDirection = p.GetInt("capsule_direction") ?? 1;
                        float halfHeight = Mathf.Max(0, height / 2f - radius);

                        switch (capsuleDirection)
                        {
                            case 0: // X
                                pt1 = origin + Vector3.left * halfHeight;
                                pt2 = origin + Vector3.right * halfHeight;
                                break;
                            case 2: // Z
                                pt1 = origin + Vector3.back * halfHeight;
                                pt2 = origin + Vector3.forward * halfHeight;
                                break;
                            default: // Y (1)
                                pt1 = origin + Vector3.down * halfHeight;
                                pt2 = origin + Vector3.up * halfHeight;
                                break;
                        }
                    }

                    UnityEngine.Physics.SyncTransforms();
                    hit = UnityEngine.Physics.CapsuleCast(pt1, pt2, radius, direction, out hitInfo, maxDistance, layerMask, triggerInteraction);
                    break;
                }
                default:
                    return new ErrorResponse($"Unknown 3D shape: '{shape}'. Valid: sphere, box, capsule.");
            }

            if (!hit)
            {
                return new
                {
                    success = true,
                    message = "Shapecast did not hit anything.",
                    data = new { hit = false },
                };
            }

            var collider = hitInfo.collider;
            var hitGameObject = collider.gameObject;
            var hitGameObjectName = hitGameObject.name;

            return new
            {
                success = true,
                message = $"Shapecast hit '{hitGameObjectName}'.",
                data = new
                {
                    hit = true,
                    point = new[] { hitInfo.point.x, hitInfo.point.y, hitInfo.point.z },
                    normal = new[] { hitInfo.normal.x, hitInfo.normal.y, hitInfo.normal.z },
                    distance = hitInfo.distance,
                    gameObject = hitGameObjectName,
                    instanceID = hitGameObject.GetInstanceIDCompat(),
                    collider_type = collider.GetType().Name,
                },
            };
        }

        private static object Shapecast2D(string shape, JArray originArr, JArray dirArr, float maxDistance, ToolParams p)
        {
            if (originArr.Count < 2)
                return new ErrorResponse("2D shapecast 'origin' requires [x, y].");
            if (dirArr.Count < 2)
                return new ErrorResponse("2D shapecast 'direction' requires [x, y].");

            var origin = new Vector2(originArr[0].ReadScalar<float>(), originArr[1].ReadScalar<float>());
            var direction = new Vector2(dirArr[0].ReadScalar<float>(), dirArr[1].ReadScalar<float>());

            int layerMask = ResolveLayerMask(p.Get("layer_mask"));
            float angle = p.GetFloat("angle") ?? 0f;

            var sizeToken = p.GetRaw("size");
            if (sizeToken == null)
                return new ErrorResponse("'size' parameter is required.");

            var triggerFilter = ResolveTriggerFilter2D(p, layerMask);
            RaycastHit2D hit;

            switch (shape.ToLowerInvariant())
            {
                case "circle":
                {
                    float radius = sizeToken.ReadScalar<float>();
                    Physics2D.SyncTransforms();
                    hit = triggerFilter.HasValue
                        ? Physics2D.defaultPhysicsScene.CircleCast(origin, radius, direction, maxDistance, triggerFilter.Value)
                        : Physics2D.CircleCast(origin, radius, direction, maxDistance, layerMask);
                    break;
                }
                case "box":
                {
                    Vector2 size;
                    if (sizeToken is JArray sizeArr && sizeArr.Count >= 2)
                        size = new Vector2(sizeArr[0].ReadScalar<float>(), sizeArr[1].ReadScalar<float>());
                    else
                        return new ErrorResponse("2D box shapecast 'size' requires [width, height].");
                    Physics2D.SyncTransforms();
                    hit = triggerFilter.HasValue
                        ? Physics2D.defaultPhysicsScene.BoxCast(origin, size, angle, direction, maxDistance, triggerFilter.Value)
                        : Physics2D.BoxCast(origin, size, angle, direction, maxDistance, layerMask);
                    break;
                }
                case "capsule":
                {
                    if (sizeToken is JObject sizeObj)
                    {
                        var capsuleSize = new Vector2(sizeObj["width"]?.ReadScalar<float?>() ?? 1f, sizeObj["height"]?.ReadScalar<float?>() ?? 2f);
                        var capsuleDir = CapsuleDirection2D.Vertical;
                        string dirStr = sizeObj["direction"]?.ToString();
                        if (dirStr != null && dirStr.ToLowerInvariant() == "horizontal")
                            capsuleDir = CapsuleDirection2D.Horizontal;
                        Physics2D.SyncTransforms();
                        hit = triggerFilter.HasValue
                            ? Physics2D.defaultPhysicsScene.CapsuleCast(origin, capsuleSize, capsuleDir, angle, direction, maxDistance, triggerFilter.Value)
                            : Physics2D.CapsuleCast(origin, capsuleSize, capsuleDir, angle, direction, maxDistance, layerMask);
                    }
                    else
                    {
                        return new ErrorResponse("2D capsule shapecast 'size' requires {width, height, direction}.");
                    }
                    break;
                }
                default:
                    return new ErrorResponse($"Unknown 2D shape: '{shape}'. Valid: circle, box, capsule.");
            }

            var collider = hit.collider;
            if (collider == null)
            {
                return new
                {
                    success = true,
                    message = "2D Shapecast did not hit anything.",
                    data = new { hit = false },
                };
            }

            var hitGameObject = collider.gameObject;
            var hitGameObjectName = hitGameObject.name;

            return new
            {
                success = true,
                message = $"2D Shapecast hit '{hitGameObjectName}'.",
                data = new
                {
                    hit = true,
                    point = new[] { hit.point.x, hit.point.y },
                    normal = new[] { hit.normal.x, hit.normal.y },
                    distance = hit.distance,
                    gameObject = hitGameObjectName,
                    instanceID = hitGameObject.GetInstanceIDCompat(),
                    collider_type = collider.GetType().Name,
                },
            };
        }

        public static object RaycastAll(JObject @params)
        {
            var p = new ToolParams(@params);
            if (!TryResolveMaxResults(p, out int maxResults))
                return new ErrorResponse("'max_results' must be an integer from 1 to 1000.");
            string dimension = (p.Get("dimension") ?? "3d").ToLowerInvariant();

            var originArr = p.GetRaw("origin") as JArray;
            if (originArr == null)
                return new ErrorResponse("'origin' parameter is required (array of floats).");

            var dirArr = p.GetRaw("direction") as JArray;
            if (dirArr == null)
                return new ErrorResponse("'direction' parameter is required (array of floats).");

            float maxDistance = p.GetFloat("max_distance") ?? Mathf.Infinity;

            if (dimension == "2d")
                return ValidateTriggerOverride2D(p) ?? RaycastAll2D(originArr, dirArr, maxDistance, p, maxResults);

            if (dimension != "3d")
                return new ErrorResponse($"Invalid dimension: '{dimension}'. Use '3d' or '2d'.");

            return RaycastAll3D(originArr, dirArr, maxDistance, p, maxResults);
        }

        private static object RaycastAll3D(JArray originArr, JArray dirArr, float maxDistance, ToolParams p, int maxResults)
        {
            if (originArr.Count < 3)
                return new ErrorResponse("3D RaycastAll 'origin' requires [x, y, z].");
            if (dirArr.Count < 3)
                return new ErrorResponse("3D RaycastAll 'direction' requires [x, y, z].");

            var origin = new Vector3(originArr[0].ReadScalar<float>(), originArr[1].ReadScalar<float>(), originArr[2].ReadScalar<float>());
            var direction = new Vector3(dirArr[0].ReadScalar<float>(), dirArr[1].ReadScalar<float>(), dirArr[2].ReadScalar<float>());

            int layerMask = ResolveLayerMask(p.Get("layer_mask"));

            QueryTriggerInteraction triggerInteraction = QueryTriggerInteraction.UseGlobal;
            string qti = p.Get("query_trigger_interaction");
            if (!string.IsNullOrEmpty(qti))
            {
                if (!System.Enum.TryParse(qti, true, out triggerInteraction) || !System.Enum.IsDefined(typeof(QueryTriggerInteraction), triggerInteraction))
                    return new ErrorResponse($"Invalid query_trigger_interaction: '{qti}'. Valid: UseGlobal, Ignore, Collide.");
            }

            UnityEngine.Physics.SyncTransforms();

            var hits = new RaycastHit[maxResults];
            int count = UnityEngine.Physics.RaycastNonAlloc(origin, direction, hits, maxDistance, layerMask, triggerInteraction);
            Array.Sort(hits, 0, count, Comparer<RaycastHit>.Create((a, b) => a.distance.CompareTo(b.distance)));

            var hitsArray = new List<object>(count);
            for (int i = 0; i < count; i++)
            {
                var h = hits[i];
                var collider = h.collider;
                var hitGameObject = collider.gameObject;
                hitsArray.Add(
                    new
                    {
                        point = new[] { h.point.x, h.point.y, h.point.z },
                        normal = new[] { h.normal.x, h.normal.y, h.normal.z },
                        distance = h.distance,
                        gameObject = hitGameObject.name,
                        instanceID = hitGameObject.GetInstanceIDCompat(),
                        collider_type = collider.GetType().Name,
                    }
                );
            }

            return new
            {
                success = true,
                message = $"RaycastAll returned {count} hit(s).",
                data = new
                {
                    hit_count = count,
                    hits = hitsArray,
                    max_results = maxResults,
                    returned_count = count,
                    buffer_saturated = count == maxResults,
                    ordering = "distance_ascending",
                    nearest_hits_guaranteed = count < maxResults,
                },
            };
        }

        private static object RaycastAll2D(JArray originArr, JArray dirArr, float maxDistance, ToolParams p, int maxResults)
        {
            if (originArr.Count < 2)
                return new ErrorResponse("2D RaycastAll 'origin' requires [x, y].");
            if (dirArr.Count < 2)
                return new ErrorResponse("2D RaycastAll 'direction' requires [x, y].");

            var origin = new Vector2(originArr[0].ReadScalar<float>(), originArr[1].ReadScalar<float>());
            var direction = new Vector2(dirArr[0].ReadScalar<float>(), dirArr[1].ReadScalar<float>());

            int layerMask = ResolveLayerMask(p.Get("layer_mask"));

            var triggerFilter = ResolveTriggerFilter2D(p, layerMask) ?? CreateTriggerFilter2D(QueryTriggerInteraction.UseGlobal, layerMask);
            Physics2D.SyncTransforms();

            var hits = new RaycastHit2D[maxResults];
            int count = Physics2D.Raycast(origin, direction, triggerFilter, hits, maxDistance);
            Array.Sort(hits, 0, count, Comparer<RaycastHit2D>.Create((a, b) => a.distance.CompareTo(b.distance)));

            var hitsArray = new List<object>(count);
            for (int i = 0; i < count; i++)
            {
                var h = hits[i];
                var collider = h.collider;
                var hitGameObject = collider.gameObject;
                hitsArray.Add(
                    new
                    {
                        point = new[] { h.point.x, h.point.y },
                        normal = new[] { h.normal.x, h.normal.y },
                        distance = h.distance,
                        gameObject = hitGameObject.name,
                        instanceID = hitGameObject.GetInstanceIDCompat(),
                        collider_type = collider.GetType().Name,
                    }
                );
            }

            return new
            {
                success = true,
                message = $"RaycastAll returned {count} hit(s).",
                data = new
                {
                    hit_count = count,
                    hits = hitsArray,
                    max_results = maxResults,
                    returned_count = count,
                    buffer_saturated = count == maxResults,
                    ordering = "distance_ascending",
                    nearest_hits_guaranteed = count < maxResults,
                },
            };
        }

        public static object Linecast(JObject @params)
        {
            var p = new ToolParams(@params);
            string dimension = (p.Get("dimension") ?? "3d").ToLowerInvariant();

            var startArr = p.GetRaw("start") as JArray;
            if (startArr == null)
                return new ErrorResponse("'start' parameter is required (array of floats).");

            var endArr = p.GetRaw("end") as JArray;
            if (endArr == null)
                return new ErrorResponse("'end' parameter is required (array of floats).");

            if (dimension == "2d")
                return ValidateTriggerOverride2D(p) ?? Linecast2D(startArr, endArr, p);

            if (dimension != "3d")
                return new ErrorResponse($"Invalid dimension: '{dimension}'. Use '3d' or '2d'.");

            return Linecast3D(startArr, endArr, p);
        }

        private static object Linecast3D(JArray startArr, JArray endArr, ToolParams p)
        {
            if (startArr.Count < 3)
                return new ErrorResponse("3D linecast 'start' requires [x, y, z].");
            if (endArr.Count < 3)
                return new ErrorResponse("3D linecast 'end' requires [x, y, z].");

            var start = new Vector3(startArr[0].ReadScalar<float>(), startArr[1].ReadScalar<float>(), startArr[2].ReadScalar<float>());
            var end = new Vector3(endArr[0].ReadScalar<float>(), endArr[1].ReadScalar<float>(), endArr[2].ReadScalar<float>());

            int layerMask = ResolveLayerMask(p.Get("layer_mask"));

            QueryTriggerInteraction triggerInteraction = QueryTriggerInteraction.UseGlobal;
            string qti = p.Get("query_trigger_interaction");
            if (!string.IsNullOrEmpty(qti))
            {
                if (!System.Enum.TryParse(qti, true, out triggerInteraction) || !System.Enum.IsDefined(typeof(QueryTriggerInteraction), triggerInteraction))
                    return new ErrorResponse($"Invalid query_trigger_interaction: '{qti}'. Valid: UseGlobal, Ignore, Collide.");
            }

            UnityEngine.Physics.SyncTransforms();

            bool hit = UnityEngine.Physics.Linecast(start, end, out RaycastHit hitInfo, layerMask, triggerInteraction);

            if (!hit)
            {
                return new
                {
                    success = true,
                    message = "Linecast did not hit anything.",
                    data = new { hit = false },
                };
            }

            var collider = hitInfo.collider;
            var hitGameObject = collider.gameObject;
            var hitGameObjectName = hitGameObject.name;

            return new
            {
                success = true,
                message = $"Linecast hit '{hitGameObjectName}'.",
                data = new
                {
                    hit = true,
                    point = new[] { hitInfo.point.x, hitInfo.point.y, hitInfo.point.z },
                    normal = new[] { hitInfo.normal.x, hitInfo.normal.y, hitInfo.normal.z },
                    distance = hitInfo.distance,
                    gameObject = hitGameObjectName,
                    instanceID = hitGameObject.GetInstanceIDCompat(),
                    collider_type = collider.GetType().Name,
                },
            };
        }

        private static object Linecast2D(JArray startArr, JArray endArr, ToolParams p)
        {
            if (startArr.Count < 2)
                return new ErrorResponse("2D linecast 'start' requires [x, y].");
            if (endArr.Count < 2)
                return new ErrorResponse("2D linecast 'end' requires [x, y].");

            var start = new Vector2(startArr[0].ReadScalar<float>(), startArr[1].ReadScalar<float>());
            var end = new Vector2(endArr[0].ReadScalar<float>(), endArr[1].ReadScalar<float>());

            int layerMask = ResolveLayerMask(p.Get("layer_mask"));

            var triggerFilter = ResolveTriggerFilter2D(p, layerMask);
            Physics2D.SyncTransforms();

            var hit = triggerFilter.HasValue
                ? Physics2D.defaultPhysicsScene.Linecast(start, end, triggerFilter.Value)
                : Physics2D.Linecast(start, end, layerMask);

            var collider = hit.collider;
            if (collider == null)
            {
                return new
                {
                    success = true,
                    message = "2D Linecast did not hit anything.",
                    data = new { hit = false },
                };
            }

            var hitGameObject = collider.gameObject;
            var hitGameObjectName = hitGameObject.name;

            return new
            {
                success = true,
                message = $"2D Linecast hit '{hitGameObjectName}'.",
                data = new
                {
                    hit = true,
                    point = new[] { hit.point.x, hit.point.y },
                    normal = new[] { hit.normal.x, hit.normal.y },
                    distance = hit.distance,
                    gameObject = hitGameObjectName,
                    instanceID = hitGameObject.GetInstanceIDCompat(),
                    collider_type = collider.GetType().Name,
                },
            };
        }

        private static ContactFilter2D? ResolveTriggerFilter2D(ToolParams p, int layerMask)
        {
            if (
                !Enum.TryParse(p.Get("query_trigger_interaction"), true, out QueryTriggerInteraction interaction)
                || (interaction != QueryTriggerInteraction.Ignore && interaction != QueryTriggerInteraction.Collide)
            )
                return null;

            return CreateTriggerFilter2D(interaction, layerMask);
        }

        private static object ValidateTriggerOverride2D(ToolParams p)
        {
            if (
                Physics2D.queriesHitTriggers
                || !Enum.TryParse(p.Get("query_trigger_interaction"), true, out QueryTriggerInteraction interaction)
                || interaction != QueryTriggerInteraction.Collide
            )
                return null;

            // Unity also applies the global trigger switch to queries with a local ContactFilter2D.
            return new
            {
                success = false,
                code = "unsupported_2d_trigger_override",
                error = "2D query_trigger_interaction='Collide' requires Physics2D.queriesHitTriggers=true. Unity's local ContactFilter2D cannot include triggers while that global setting is disabled; the query was not run.",
                data = new
                {
                    dimension = "2d",
                    query_trigger_interaction = "Collide",
                    queriesHitTriggers = false,
                },
            };
        }

        private static bool TryResolveOverlapTrigger(ToolParams p, out QueryTriggerInteraction interaction)
        {
            string value = p.Get("query_trigger_interaction");
            interaction = QueryTriggerInteraction.UseGlobal;
            return string.IsNullOrEmpty(value) || (Enum.TryParse(value, true, out interaction) && Enum.IsDefined(typeof(QueryTriggerInteraction), interaction));
        }

        private static bool TryResolveMaxResults(ToolParams p, out int maxResults)
        {
            JToken value = p.GetRaw("max_results");
            maxResults = DefaultMaxResults;
            if (value == null || value.Type == JTokenType.Null)
                return true;
            return value.Type == JTokenType.Integer && int.TryParse(value.ToString(), out maxResults) && maxResults >= 1 && maxResults <= MaxQueryResults;
        }

        private static ContactFilter2D CreateTriggerFilter2D(QueryTriggerInteraction interaction, int layerMask)
        {
            var filter = new ContactFilter2D
            {
                useTriggers = interaction == QueryTriggerInteraction.UseGlobal ? Physics2D.queriesHitTriggers : interaction == QueryTriggerInteraction.Collide,
            };
            filter.SetLayerMask(layerMask);
            return filter;
        }

        private static int ResolveLayerMask(string layerMaskStr)
        {
            if (string.IsNullOrEmpty(layerMaskStr))
                return ~0; // All layers

            if (int.TryParse(layerMaskStr, out int mask))
                return mask;

            int layer = LayerMask.NameToLayer(layerMaskStr);
            if (layer >= 0)
                return 1 << layer;

            throw new ArgumentException($"Unknown layer name: '{layerMaskStr}'. Use a valid layer name or integer mask.");
        }
    }
}

#endif
