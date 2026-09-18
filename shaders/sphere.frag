#version 330

// Everything below runs per-pixel, per-frame, on the GPU. Python's only
// job is to compute the handful of scalars per shape (center direction,
// inner/outer radius, color) each frame and upload them as uniforms --
// see shapes.py and render.py.

#define MAX_SHAPES 32

uniform vec2 u_resolution;

// The real projector is the camera: position + orientation + lens FOV.
uniform vec3 u_projPos;
uniform mat3 u_projRotation;   // columns: right, up, forward (world space)
uniform float u_tanHalfHFov;
uniform float u_tanHalfVFov;

uniform vec3 u_ballCenter;
uniform float u_ballRadius;
uniform mat3 u_ballRotation;   // rotates hit direction into the ball's local frame (for spin)

uniform int u_numShapes;
uniform vec3 u_shapeCenter[MAX_SHAPES];  // unit direction, ball-local
uniform vec2 u_shapeRadii[MAX_SHAPES];   // (inner, outer), radians
uniform vec4 u_shapeColor[MAX_SHAPES];

uniform sampler2D u_continentsTex;
uniform bool u_continentsEnabled;

out vec4 fragColor;

// Pinhole camera: NDC-ish pixel coord -> world-space ray direction.
vec3 pixelToRayDir(vec2 uv) {
    vec3 camSpace = normalize(vec3(uv.x * u_tanHalfHFov, uv.y * u_tanHalfVFov, 1.0));
    return u_projRotation * camSpace;
}

// Nearest positive intersection of a ray with the ball, or -1.0 if it misses.
// A miss (no real root) is exactly the dark surrounding -- nothing else
// needs to be drawn for it.
float raySphereIntersect(vec3 origin, vec3 dir) {
    vec3 oc = origin - u_ballCenter;
    float b = dot(oc, dir);
    float c = dot(oc, oc) - u_ballRadius * u_ballRadius;
    float disc = b * b - c;
    if (disc < 0.0) {
        return -1.0;
    }
    float t = -b - sqrt(disc);  // nearer root == front-facing point, occlusion for free
    return t > 0.0 ? t : -1.0;
}

// The one shape primitive: a spherical annulus. Filled circles, ring
// outlines, latitude bands and meridian bands are all just different
// (center, inner_radius, outer_radius) parameterizations of this.
float annulusCoverage(vec3 d, vec3 center, vec2 radii) {
    float ang = acos(clamp(dot(d, center), -1.0, 1.0));
    float edge = 0.01; // radians, soft edge so shapes antialias instead of aliasing
    // Skip the inner soft edge for filled circles (inner_radius == 0): acos()
    // is always >= 0, so smoothstep(-edge, +edge, 0) = 0.5 at the exact
    // centre, producing a dark dot. With no hole there is nothing to fade.
    float inner = (radii.x <= 0.0) ? 1.0 : smoothstep(radii.x - edge, radii.x + edge, ang);
    float outer = 1.0 - smoothstep(radii.y - edge, radii.y + edge, ang);
    return inner * outer;
}

// Continents live natively in lat/lon, so they're a texture lookup
// instead of an angular-distance formula.
vec4 sampleContinents(vec3 d) {
    float lat = asin(clamp(d.y, -1.0, 1.0));
    float lon = atan(d.x, d.z);
    vec2 uv = vec2((lon + 3.14159265) / 6.2831853,
                    (lat + 1.57079633) / 3.14159265);
    return texture(u_continentsTex, uv);
}

void main() {
    vec2 uv = (gl_FragCoord.xy / u_resolution) * 2.0 - 1.0;

    vec3 dir = pixelToRayDir(uv);
    float t = raySphereIntersect(u_projPos, dir);
    if (t < 0.0) {
        fragColor = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    vec3 hit = u_projPos + t * dir;
    vec3 d = u_ballRotation * normalize(hit - u_ballCenter);

    vec4 color = vec4(0.0, 0.0, 0.0, 1.0);
    if (u_continentsEnabled) {
        color = sampleContinents(d);
    }

    for (int i = 0; i < u_numShapes; i++) {
        float coverage = annulusCoverage(d, u_shapeCenter[i], u_shapeRadii[i]);
        color.rgb = mix(color.rgb, u_shapeColor[i].rgb, coverage * u_shapeColor[i].a);
    }

    fragColor = vec4(color.rgb, 1.0);
}
