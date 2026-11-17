// The projector's own beam in the haze, at half resolution (haze is soft, and the march is the costly part).
uniform vec2 u_res;
uniform mat4 u_invViewProj;
uniform float u_projGain;

out vec4 fragColor;

float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

void main() {
    vec2 ndc = gl_FragCoord.xy / u_res * 2.0 - 1.0;
    vec4 far = u_invViewProj * vec4(ndc, 1.0, 1.0);
    vec3 rd = normalize(far.xyz / far.w - u_eye);
    vec3 toProj = toProjector();
    int axis;
    float tBall = sphereHit(u_eye, rd, u_radius);
    float tEnd = tBall > 0.0 ? tBall : boxExit(u_eye, rd, axis);

    // March through the part of the ray inside the projector's picture: a box in the image frame, since the light
    // is parallel. Jittered start, so the steps don't show as bands.
    const int STEPS = 48;
    vec3 qo = u_roomToImg * u_eye;
    vec3 qd = u_roomToImg * rd;
    vec3 bmin = vec3(-u_cal.x / u_cal.z * u_radius, -u_cal.y / u_cal.w * u_radius, -1e3);
    vec3 bmax = vec3((1.0 - u_cal.x) / u_cal.z * u_radius, (1.0 - u_cal.y) / u_cal.w * u_radius, u_projDist);
    vec3 inv = 1.0 / mix(qd, vec3(1e-6), step(abs(qd), vec3(1e-6)));
    vec3 ta = (bmin - qo) * inv, tb = (bmax - qo) * inv;
    vec3 tlo = min(ta, tb), thi = max(ta, tb);
    float t0 = max(0.0, max(max(tlo.x, tlo.y), tlo.z));
    float t1 = min(tEnd, min(min(thi.x, thi.y), thi.z));
    if (t1 <= t0) {
        fragColor = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }
    float dt = (t1 - t0) / float(STEPS);
    float t = t0 + dt * hash(gl_FragCoord.xy);
    float ph = phase(dot(toProj, rd));   // light travels along -toProj, the eye looks back along -rd
    vec3 acc = vec3(0.0);
    for (int i = 0; i < STEPS; i++) {
        vec3 p = u_eye + rd * t;
        float fromProj = max(0.0, u_projDist - dot(p, toProj));
        acc += projectorLight(p) * exp(-u_ext * (t + fromProj));
        t += dt;
    }
    fragColor = vec4(acc * dt * u_sigma * ph * u_projGain, 1.0);
}
