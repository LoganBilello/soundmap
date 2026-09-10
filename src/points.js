/**
 * GPU dot layer — one draw call for every city.
 *
 * globe.gl's built-in `pointsData` builds a separate sphere MESH per city, so
 * 3,000 cities means 3,000 objects transformed and hit-tested every frame.
 * That is what makes the globe drag.
 *
 * radio.garden avoids this by rendering its places as a MapLibre GL circle
 * layer: every dot drawn by one shader in a single instanced call. This is the
 * three.js equivalent — a single THREE.Points whose vertices are the cities,
 * with per-vertex colour and size, drawn as soft round sprites.
 *
 * Hit-testing uses one raycast against that single object rather than a walk
 * over thousands of meshes.
 */

import * as THREE from "three";

const VERT = /* glsl */ `
  attribute vec3 aColor;
  attribute float aSize;
  uniform float uScale;
  varying vec3 vColor;
  varying float vAlpha;

  void main() {
    vColor = aColor;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    // CONSTANT screen size, deliberately not divided by distance. Scaling dots
    // perspectively makes them balloon into blurry discs as you approach;
    // MapLibre circle layers (what radio.garden uses) size in pixels instead,
    // and clustering already handles density when zoomed out.
    gl_PointSize = aSize * uScale;
    vAlpha = 1.0;
    gl_Position = projectionMatrix * mv;
  }
`;

const FRAG = /* glsl */ `
  varying vec3 vColor;
  varying float vAlpha;

  void main() {
    // gl_PointCoord runs 0..1 across the sprite; carve a soft circle from it.
    float d = length(gl_PointCoord - vec2(0.5));
    if (d > 0.5) discard;
    // Tight antialiased edge, not a glow: a wide falloff reads as blur.
    float edge = smoothstep(0.5, 0.42, d);
    gl_FragColor = vec4(vColor, edge * vAlpha);
  }
`;

export class DotLayer {
  /**
   * @param {object} globe   the globe.gl instance
   * @param {{onClick:Function, onHover:Function}} handlers
   */
  constructor(globe, { onClick, onHover }) {
    this.globe = globe;
    this.nodes = [];
    this._hovered = null;

    this.material = new THREE.ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      // uScale is now pixels-per-aSize-unit, not a perspective constant.
      uniforms: { uScale: { value: 3.2 } },
      transparent: true,
      depthWrite: false,   // dots must not occlude each other
      depthTest: true,     // but the globe itself must still hide the far side
    });

    this.object = new THREE.Points(new THREE.BufferGeometry(), this.material);
    this.object.frustumCulled = false;
    // Draw after the country polygons. Those use a sub-1.0 alpha, so three.js
    // sorts them into the transparent bucket with these dots, and whichever
    // draws last wins the blend. Without this the landmasses paint over every
    // city no matter how far the dots are raised off the sphere.
    this.object.renderOrder = 10;

    this.raycaster = new THREE.Raycaster();
    this._bindPointer(onClick, onHover);
  }

  /**
   * Allocate once, at the largest size we will ever draw.
   *
   * Reallocating a BufferGeometry per zoom band cost up to 450ms in GC pauses.
   * Instead the buffers are sized for every city up front and each band just
   * memcpys into them and moves the draw range — no allocation, no dispose.
   */
  allocate(maxCount, globeRadius) {
    this._pos = new Float32Array(maxCount * 3);
    this._col = new Float32Array(maxCount * 3);
    this._siz = new Float32Array(maxCount);

    const g = this.object.geometry;
    g.setAttribute("position", new THREE.BufferAttribute(this._pos, 3));
    g.setAttribute("aColor", new THREE.BufferAttribute(this._col, 3));
    g.setAttribute("aSize", new THREE.BufferAttribute(this._siz, 1));

    // Fixed bounds. computeBoundingSphere() would walk stale vertices past the
    // draw range; every dot sits on the globe, so the sphere is known anyway.
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), globeRadius * 1.2);
  }

  /** Swap in a precomputed band. Arrays are copied, never adopted. */
  setBand(nodes, positions, sizes) {
    this.nodes = nodes;
    this._pos.set(positions);
    this._siz.set(sizes);
    const g = this.object.geometry;
    g.getAttribute("position").needsUpdate = true;
    g.getAttribute("aSize").needsUpdate = true;
    g.setDrawRange(0, nodes.length);
  }

  /** Colours change independently of layout, e.g. when a genre is selected. */
  setColors(colors) {
    this._col.set(colors);
    this.object.geometry.getAttribute("aColor").needsUpdate = true;
  }

  /** gl_PointSize is in physical pixels, so honour the display's ratio. */
  setPixelRatio(ratio) {
    this.material.uniforms.uScale.value = 3.2 * (ratio || 1);
  }

  /** Nearest node under the pointer, or null. */
  pick(event) {
    const canvas = this.globe.renderer().domElement;
    const r = canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((event.clientX - r.left) / r.width) * 2 - 1,
      -((event.clientY - r.top) / r.height) * 2 + 1
    );

    // Threshold is in world units, so it must grow with camera distance or
    // dots become unclickable when zoomed out.
    const alt = this.globe.pointOfView()?.altitude ?? 2.5;
    this.raycaster.params.Points.threshold = 0.9 + alt * 0.9;
    this.raycaster.setFromCamera(ndc, this.globe.camera());

    const hits = this.raycaster.intersectObject(this.object, false);
    if (!hits.length) return null;

    // The ray passes straight through the globe, so it also finds dots on the
    // FAR side. Those are hidden behind the planet but can sit nearer the ray
    // than the one actually under the cursor -- clicking Nigeria could select
    // Cape Town. Keep only dots on the hemisphere facing the camera.
    const camDir = this.globe.camera().position.clone().normalize();
    const front = hits.filter((h) => h.point.clone().normalize().dot(camDir) > 0);
    if (!front.length) return null;

    front.sort((a, b) => a.distanceToRay - b.distanceToRay);
    return this.nodes[front[0].index] ?? null;
  }

  _bindPointer(onClick, onHover) {
    const canvas = this.globe.renderer().domElement;
    let downAt = null;

    canvas.addEventListener("pointermove", (e) => {
      const hit = this.pick(e);
      if (hit !== this._hovered) {
        this._hovered = hit;
        canvas.style.cursor = hit ? "pointer" : "grab";
        onHover(hit, e);
      } else if (hit) {
        onHover(hit, e);          // keep the tooltip glued to the cursor
      }
    });

    canvas.addEventListener("pointerleave", () => {
      this._hovered = null;
      onHover(null);
    });

    // Only treat it as a click if the pointer barely moved, so dragging the
    // globe past a dot never opens it.
    canvas.addEventListener("pointerdown", (e) => (downAt = [e.clientX, e.clientY]));
    canvas.addEventListener("pointerup", (e) => {
      if (!downAt) return;
      const moved = Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]);
      downAt = null;
      if (moved > 4) return;
      // Always reported, hit or miss, so the caller can fall through to
      // picking the country under the cursor.
      onClick(this.pick(e), e);
    });
  }
}
