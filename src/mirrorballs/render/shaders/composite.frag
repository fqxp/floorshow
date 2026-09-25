uniform sampler2D u_image;      // the blended scene with its trail (blend.frag's output)
uniform float u_master;         // brightness incl. blackout fade

uniform vec4 u_lines;           // calibration lines L, R, T, B as fractions (top-down); x<0 = off
uniform vec4 u_lineLevels;      // brightness of each line (last-moved one brighter)
uniform float u_lineMark;       // brightness of the tangent marks at the middle of each line (0 = blinked off)
uniform int u_grid;             // verification grid on/off
uniform float u_gridSpin;       // degrees the grid is turned about the pole axis (step 4's spinning ball)
uniform vec2 u_cross;           // pole crosshair centre (x, y as fractions, top-down); x < 0 = off
uniform vec4 u_rows;            // tile rows: mode (0 off, 1 zebra, 2 single row), pitch deg, offset deg, zebra limit deg

out vec4 fragColor;

const float MARK_HALF_PX = 5.0;  // the tangent marks are ten pixels long

float lineCoverage(float distPx) { return 1.0 - smoothstep(0.5, 1.5, distPx); }

// Exactly the one pixel column/row that holds `at`. The tangent marks must be one pixel and no more:
// what is being judged is whether that very pixel still reaches the wall past the ball.
float hardLine(float p, float at) { return step(abs(floor(p) - floor(at)), 0.5); }

float gridLine(float v) {       // v in cell units; 1 px wide via derivatives
    float w = max(fwidth(v), 1e-4);
    return 1.0 - smoothstep(0.5, 1.5, abs(fract(v + 0.5) - 0.5) / w);
}

void main() {
    vec2 uv = gl_FragCoord.xy / u_resolution;
    vec3 col = texture(u_image, uv).rgb;
    col *= u_master;

    // Calibration overlay: lines are drawn straight onto the projector image.
    if (u_lines.x >= 0.0) {
        vec2 px = gl_FragCoord.xy;
        vec4 at = vec4(u_lines.x * u_resolution.x, u_lines.y * u_resolution.x,
                       (1.0 - u_lines.z) * u_resolution.y, (1.0 - u_lines.w) * u_resolution.y);
        float l = 0.0;
        l = max(l, lineCoverage(abs(px.x - at.x)) * u_lineLevels.x);
        l = max(l, lineCoverage(abs(px.x - at.y)) * u_lineLevels.y);
        l = max(l, lineCoverage(abs(px.y - at.z)) * u_lineLevels.z);
        l = max(l, lineCoverage(abs(px.y - at.w)) * u_lineLevels.w);
        // Where each line grazes the ball it is at its most telling, so a short mark sits there: the ball's
        // centre height for the upright lines, its centre width for the flat ones.
        vec2 c = vec2(u_cal.x * u_resolution.x, u_cal.y * u_resolution.y);
        float m = 0.0;
        m = max(m, max(hardLine(px.x, at.x), hardLine(px.x, at.y)) * step(abs(px.y - c.y), MARK_HALF_PX));
        m = max(m, max(hardLine(px.y, at.z), hardLine(px.y, at.w)) * step(abs(px.x - c.x), MARK_HALF_PX));
        col = max(col, vec3(max(l, m * u_lineMark)));
    }
    if (u_grid != 0) {
        vec3 dImg;
        if (pixelToImageDir(gl_FragCoord.xy, dImg)) {
            vec3 d = u_imgToBall * dImg;
            float lat = degrees(asin(clamp(d.y, -1.0, 1.0)));
            float lon = degrees(atan(d.x, d.z)) - u_gridSpin;
            float g = max(gridLine(lat / 30.0), gridLine(lon / 30.0)) * 0.55;
            float equator = 1.0 - smoothstep(0.5, 1.5, abs(lat) / max(fwidth(lat), 1e-4));
            vec2 q = vec2((uv.x - u_cal.x) / u_cal.z, (uv.y - u_cal.y) / u_cal.w);
            float rim = lineCoverage((1.0 - length(q)) * min(u_cal.z * u_resolution.x, u_cal.w * u_resolution.y));
            col = max(col, vec3(0.0, g, g));
            col = max(col, vec3(equator, equator, 0.0));
            col = max(col, vec3(rim * 0.9));
        }
    }
    if (u_rows.x > 0.5) {
        vec3 dImg;
        if (pixelToImageDir(gl_FragCoord.xy, dImg)) {
            vec3 d = u_imgToBall * dImg;
            float lat = degrees(asin(clamp(d.y, -1.0, 1.0)));
            float pos = (lat - u_rows.z) / u_rows.y;      // integer = a row boundary
            // Signed distance to the nearest stripe edge (in row units, positive inside a lit stripe), and a
            // ramp centred on the edge and one pixel wide, so edges do not shift where latitude changes fast.
            float lit;
            if (u_rows.x < 1.5) {                          // zebra: every other row, within the limit
                float v = pos * 0.5;                        // one stripe = two rows
                float sd = 0.25 - abs(fract(v - 0.25 + 0.5) - 0.5);
                float hw = 0.5 * max(fwidth(v), 1e-4);
                lit = smoothstep(-hw, hw, sd) * step(abs(lat), u_rows.w);
            } else {                                        // one row: [offset, offset + pitch)
                float sd = 0.5 - abs(pos - 0.5);
                float hw = 0.5 * max(fwidth(pos), 1e-4);
                lit = smoothstep(-hw, hw, sd);
            }
            col = max(col, vec3(lit));
        }
    }
    if (u_cross.x >= 0.0) {
        vec2 c = vec2(u_cross.x * u_resolution.x, (1.0 - u_cross.y) * u_resolution.y);
        vec2 dp = gl_FragCoord.xy - c;
        float arm = 16.0;
        float h = lineCoverage(abs(dp.y)) * step(abs(dp.x), arm);
        float v = lineCoverage(abs(dp.x)) * step(abs(dp.y), arm);
        float ring = lineCoverage(abs(length(dp) - 9.0));
        col = max(col, vec3(max(max(h, v), ring)));
    }
    fragColor = vec4(col, 1.0);
}
