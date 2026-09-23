// Transition between the two scene targets, plus the trail: what this pass wrote last frame,
// dimmed, shows through wherever the scene is darker.
uniform sampler2D u_a;          // outgoing scene
uniform sampler2D u_b;          // incoming (or only) scene
uniform sampler2D u_hist;       // what this pass wrote last frame
uniform float u_mix;            // transition progress 0..1 (1 = only B)
uniform int u_trans;            // 0 cut, 1 crossfade, 2 wipe_lat, 3 iris, 4 flash
uniform float u_decay;          // fraction of the previous frame that survives; 0 = no trail

out vec4 fragColor;

const float PI = 3.14159265358979;

void main() {
    vec2 uv = gl_FragCoord.xy / u_resolution;
    vec3 a = texture(u_a, uv).rgb;
    vec3 b = texture(u_b, uv).rgb;
    vec3 col = b;

    if (u_mix < 1.0 && u_trans != 0) {
        float p = u_mix;
        vec3 dImg;
        bool inside = pixelToImageDir(gl_FragCoord.xy, dImg);
        vec3 dBall = u_imgToBall * dImg;
        if (u_trans == 1) {
            col = mix(a, b, p);
        } else if (u_trans == 2) {                       // wipe from north pole to south pole
            float lat = asin(clamp(dBall.y, -1.0, 1.0));
            float edge = mix(PI / 2.0 + 0.05, -PI / 2.0 - 0.05, p);
            col = mix(a, b, smoothstep(edge - 0.05, edge + 0.05, lat));
        } else if (u_trans == 3) {                       // iris opening from the centre of the ball
            float ang = acos(clamp(dImg.z, -1.0, 1.0));
            float edge = p * (PI / 2.0 + 0.1);
            col = mix(a, b, 1.0 - smoothstep(edge - 0.05, edge + 0.05, ang));
        } else {                                          // flash through white
            col = p < 0.5 ? mix(a, vec3(1.0), p * 2.0) : mix(vec3(1.0), b, (p - 0.5) * 2.0);
        }
        if (!inside) col = vec3(0.0);
    }

    // max(), not mix(): a pixel that lights up is instantly at full brightness, so hits stay on
    // the beat. Only the disappearance is slowed.
    fragColor = vec4(max(col, texture(u_hist, uv).rgb * u_decay), 1.0);
}
