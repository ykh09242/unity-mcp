Shader "Hidden/MCPFixture/RequiredTint"
{
    SubShader
    {
        Pass
        {
            Name "TINT"
            Cull Off
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_local_fragment __ MCP_REQUIRED_TINT
            #include "UnityCG.cginc"

            float4 vert(float4 p : POSITION) : SV_POSITION { return UnityObjectToClipPos(p); }
            fixed4 frag() : SV_Target
            {
                #if defined(MCP_REQUIRED_TINT)
                    return fixed4(0, 1, 0, 1);
                #else
                    return fixed4(1, 0, 0, 1);
                #endif
            }
            ENDCG
        }
    }
}
