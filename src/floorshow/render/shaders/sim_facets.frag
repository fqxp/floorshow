// One fragment per facet: what light it catches from the projector and where it sends it.
uniform sampler2D u_normals;   // ball-frame facet normals; w = 1 for a facet, 0 for padding
uniform sampler2D u_proj;      // the projector image (mipmapped)
uniform mat3 u_spinRoom;       // the ball's turn: ball frame -> room
uniform mat3 u_roomToImg;      // room -> image frame (the calibration's roll and tilt)
uniform vec4 u_cal;            // cx, cy (y up), rx, ry; normalised to the window
uniform float u_lod;           // mip level about one tile across

layout(location = 0) out vec4 o_color;  // rgb: irradiance from the projector; a: 1 lit, 0 dark
layout(location = 1) out vec4 o_dir;    // xyz: reflected direction (room)
layout(location = 2) out vec4 o_pos;    // xyz: facet normal (room), i.e. its position on the unit ball

void main() {
    vec4 nb = texelFetch(u_normals, ivec2(gl_FragCoord.xy), 0);
    vec3 nRoom = u_spinRoom * nb.xyz;
    vec3 ni = u_roomToImg * nRoom;
    o_pos = vec4(nRoom, 1.0);
    if (nb.w < 0.5 || ni.z <= 0.0) {  // padding, or facing away from the projector
        o_color = vec4(0.0);
        o_dir = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }
    vec2 uv = vec2(u_cal.x + ni.x * u_cal.z, u_cal.y + ni.y * u_cal.w);
    vec3 c = textureLod(u_proj, uv, u_lod).rgb;
    vec3 r = vec3(0.0, 0.0, -1.0) + 2.0 * ni.z * ni;  // reflection of the light travelling along -z
    o_color = vec4(c, 1.0);
    o_dir = vec4(transpose(u_roomToImg) * r, 1.0);
}
