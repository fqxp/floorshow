// One instance per facet. Mode 0: the beam, a ribbon facing the eye from the facet to the wall.
// Mode 1: the spot where the beam lands, a quad on that wall.
in vec2 in_corner;              // x: 0 at the facet, 1 at the wall; y: -1..1 across

uniform sampler2D u_fColor;
uniform sampler2D u_fDir;
uniform sampler2D u_fPos;
uniform int u_cols;
uniform int u_mode;
uniform mat4 u_viewProj;
uniform float u_width0;         // a tile's width
uniform float u_diverge;        // how fast a beam widens, m per m

out vec3 v_world;
out vec3 v_color;
out vec3 v_dir;
out vec2 v_local;               // position across the beam (-1..1), or on the spot
out float v_along;
out float v_width;
out vec3 v_wallN;

void main() {
    ivec2 ij = ivec2(gl_InstanceID % u_cols, gl_InstanceID / u_cols);
    vec4 c = texelFetch(u_fColor, ij, 0);
    vec3 d = texelFetch(u_fDir, ij, 0).xyz;
    vec3 s = texelFetch(u_fPos, ij, 0).xyz * u_radius;
    v_color = c.rgb;
    v_dir = d;
    v_wallN = vec3(0.0);
    v_world = s;
    v_along = 0.0;
    v_width = u_width0;
    v_local = vec2(0.0);
    if (c.a < 0.5 || dot(c.rgb, vec3(1.0)) < 1e-4) {  // no light: nothing to draw
        gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
        return;
    }
    int axis;
    float len = boxExit(s, d, axis);
    vec3 p;
    if (u_mode == 0) {
        v_along = in_corner.x * len;
        v_width = u_width0 + u_diverge * v_along;
        vec3 q = s + d * v_along;
        vec3 side = cross(d, q - u_eye);
        float sl = length(side);
        side = sl > 1e-6 ? side / sl : vec3(0.0);
        p = q + side * in_corner.y * v_width * 0.5;
        v_local = vec2(in_corner.y, 0.0);
    } else {
        v_local = vec2(in_corner.x * 2.0 - 1.0, in_corner.y);
        v_along = len;
        v_width = u_width0 + u_diverge * len;
        vec3 n = vec3(0.0);
        n[axis] = -sign(d[axis]);
        vec3 t1 = abs(n.y) > 0.5 ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 1.0, 0.0);
        vec3 along = d - n * dot(d, n);
        along = length(along) > 1e-4 ? normalize(along) : t1;
        vec3 across = cross(n, along);
        float cosW = max(abs(d[axis]), 0.15);  // a beam hitting the wall at a slant makes a longer spot
        p = s + d * len + n * 0.002 + (along * v_local.x / cosW + across * v_local.y) * v_width * 0.5;
        v_wallN = n;
    }
    v_world = p;
    gl_Position = u_viewProj * vec4(p, 1.0);
}
