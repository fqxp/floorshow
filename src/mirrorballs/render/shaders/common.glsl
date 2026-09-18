// Shared by scene.frag and composite.frag (concatenated after the #version line).
// The calibrated silhouette ellipse maps a pixel to a direction on the ball (orthographic).
// Keep in sync with mirrorballs/common/geometry.py (Calibration.pixel_to_image_dir).

uniform vec2 u_resolution;
uniform vec4 u_cal;          // cx, cy (y up), rx, ry; normalised to the window
uniform mat3 u_imgToBall;    // roll/tilt: image-frame direction -> ball-frame direction

// Direction (image frame: +x right, +y up, +z toward the projector) of the sphere point seen at
// fragCoord; false outside the silhouette.
bool pixelToImageDir(vec2 fragCoord, out vec3 d) {
    vec2 uv = fragCoord / u_resolution;
    vec2 q = vec2((uv.x - u_cal.x) / u_cal.z, (uv.y - u_cal.y) / u_cal.w);
    float r2 = dot(q, q);
    if (r2 > 1.0) {
        d = vec3(0.0, 0.0, 1.0);
        return false;
    }
    d = vec3(q, sqrt(1.0 - r2));
    return true;
}
