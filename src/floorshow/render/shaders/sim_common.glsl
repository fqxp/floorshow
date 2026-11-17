// Shared by the haze simulation shaders (concatenated after the #version line). See render/sim.py.
// Room frame = ball frame: +y up, origin at the ball centre, metres.

uniform float u_radius;     // ball radius
uniform vec3 u_boxMin;      // the room
uniform vec3 u_boxMax;
uniform float u_sigma;      // haze scattering, 1/m
uniform float u_ext;        // extinction, 1/m
uniform float u_g;          // Henyey-Greenstein anisotropy (forward scattering > 0)
uniform vec3 u_eye;
uniform sampler2D u_proj;   // the projector image
uniform mat3 u_roomToImg;   // room -> image frame (the calibration's roll and tilt)
uniform vec4 u_cal;         // cx, cy (y up), rx, ry; normalised to the window
uniform float u_projDist;   // where the projector is, along its axis

vec3 toProjector() { return transpose(u_roomToImg) * vec3(0.0, 0.0, 1.0); }

// The projector's light at p (room): its colour, or black outside the picture and in the ball's shadow.
vec3 projectorLight(vec3 p) {
    vec3 q = u_roomToImg * p;
    if (q.z > u_projDist) return vec3(0.0);
    // The shadow is a little wider than the ball: the pixels at its rim mostly land on it, and the thin shell of
    // light they would otherwise leave behind it is too thin to march through without speckling.
    if (q.z < 0.0 && dot(q.xy, q.xy) < 1.1 * u_radius * u_radius) return vec3(0.0);
    vec2 uv = vec2(u_cal.x + q.x / u_radius * u_cal.z, u_cal.y + q.y / u_radius * u_cal.w);
    if (any(lessThan(uv, vec2(0.0))) || any(greaterThan(uv, vec2(1.0)))) return vec3(0.0);
    return textureLod(u_proj, uv, 1.0).rgb;
}

// Phase function, normalised so that isotropic scattering is 1. cosT: between the light's direction of
// travel and the direction towards the eye.
float phase(float cosT) {
    float g2 = u_g * u_g;
    return (1.0 - g2) / pow(max(1.0 + g2 - 2.0 * u_g * cosT, 1e-4), 1.5);
}

// Distance along unit d from o (inside the room) to the wall; axis gets which wall (0 x, 1 y, 2 z).
float boxExit(vec3 o, vec3 d, out int axis) {
    vec3 safe = mix(vec3(-1e-6), vec3(1e-6), step(0.0, d));
    vec3 dd = mix(d, safe, step(abs(d), vec3(1e-6)));
    vec3 t = (mix(u_boxMin, u_boxMax, step(0.0, dd)) - o) / dd;
    axis = 0;
    float best = t.x;
    if (t.y < best) { best = t.y; axis = 1; }
    if (t.z < best) { best = t.z; axis = 2; }
    return best;
}

// First hit of the ray o + t d (unit d) with the ball, or -1.
float sphereHit(vec3 o, vec3 d, float r) {
    float b = dot(o, d);
    float c = dot(o, o) - r * r;
    float h = b * b - c;
    if (h < 0.0) return -1.0;
    float t = -b - sqrt(h);
    return t > 0.0 ? t : -1.0;
}

// Is p hidden behind the ball, seen from eye? Points on the ball's near surface are not.
bool behindBall(vec3 eye, vec3 p) {
    vec3 d = p - eye;
    float len = length(d);
    float t = sphereHit(eye, d / len, u_radius);
    return t > 0.0 && t < len - 1e-3;
}
