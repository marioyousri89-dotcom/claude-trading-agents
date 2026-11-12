// JARVIS: hologram avatar (three.js), voice in (Web Speech recognition) and voice out (speech synthesis).
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = { talk: 0, listening: false, speaking: false, unlocked: false, enabled: false };

  // ---------- Hologram ----------
  function rand(a, b) { return a + Math.random() * (b - a); }

  function buildFigure() {
    const pos = [], seed = [], kind = []; // kind: 0 body, 1 mouth, 2 eyes
    const add = (x, y, z, k) => { pos.push(x, y, z); seed.push(Math.random()); kind.push(k || 0); };
    // Head: an ellipsoid shaped into a face (narrow jaw, nose, eye sockets, lips, cheekbones).
    for (let i = 0; i < 9000; i++) {
      const u = Math.random() * 2 - 1, t = Math.random() * Math.PI * 2, r = Math.sqrt(1 - u * u);
      let x = r * Math.cos(t), y = u, z = r * Math.sin(t);
      const jaw = y < 0 ? 1 - 0.38 * Math.pow(-y, 1.4) : 1 - 0.08 * y * y;
      x *= 0.62 * jaw; z *= 0.74 * (y < 0 ? 1 - 0.2 * -y : 1); y *= 0.9;
      if (z > 0.3) {
        const nose = Math.exp(-(x * x) / 0.006) * Math.exp(-Math.pow(y + 0.05, 2) / 0.05) * 0.16;
        const brow = Math.exp(-Math.pow(y - 0.22, 2) / 0.004) * 0.03;
        const eyes = Math.exp(-(Math.pow(Math.abs(x) - 0.22, 2) + Math.pow(y - 0.12, 2)) / 0.006) * -0.07;
        const cheek = Math.exp(-(Math.pow(Math.abs(x) - 0.3, 2) + Math.pow(y + 0.05, 2)) / 0.02) * 0.04;
        const lips = Math.exp(-(x * x) / 0.03 - Math.pow(y + 0.36, 2) / 0.003) * 0.05;
        z += nose + brow + eyes + cheek + lips;
      }
      add(x, y + 1.62, z);
    }
    // Hair: a fuller shell over the top and back, falling past the shoulders.
    for (let i = 0; i < 9000; i++) {
      const u = Math.random() * 2 - 1, t = Math.random() * Math.PI * 2, r = Math.sqrt(1 - u * u);
      let x = r * Math.cos(t) * 0.7, y = u * 0.98, z = r * Math.sin(t) * 0.82;
      if (z > 0.25 && y < 0.5) continue; // leave the face open
      if (y < -0.2 && z > -0.1 && Math.abs(x) < 0.5) continue;
      add(x * 1.04, y + 1.66, z * 1.02 - 0.02);
    }
    for (let i = 0; i < 4000; i++) { // long hair falling behind the shoulders
      const y = rand(0.3, 1.3), a = rand(0.15, Math.PI - 0.15), spread = 0.6 + (1.3 - y) * 0.22;
      add(Math.cos(a) * spread, y, -Math.sin(a) * spread * 0.55 - 0.12);
    }
    // Neck.
    for (let i = 0; i < 1500; i++) {
      const t = Math.random() * Math.PI * 2, y = rand(0.55, 1.05);
      add(Math.cos(t) * 0.2, y, Math.sin(t) * 0.2 + 0.02);
    }
    // Shoulders and upper chest.
    for (let i = 0; i < 11000; i++) {
      const t = rand(0, Math.PI * 2), y = rand(-1.0, 0.62);
      const w = y > 0.25 ? 0.24 + 0.88 * Math.sqrt((0.62 - y) / 0.37) : 1.12 - (0.25 - y) * 0.12;
      const d = y > 0.25 ? 0.2 + 0.2 * (0.62 - y) / 0.37 : 0.4 + 0.04 * Math.exp(-Math.pow(y + 0.15, 2) / 0.05);
      add(Math.cos(t) * w, y, Math.sin(t) * d);
    }
    // Lips (move when she speaks) and eyes (glow).
    for (let i = 0; i < 500; i++) { const x = rand(-0.11, 0.11); add(x, 1.26 + rand(-0.012, 0.012), 0.69 - x * x * 2, 1); }
    for (let i = 0; i < 200; i++) {
      const s = i % 2 ? 1 : -1, a = rand(0, Math.PI * 2), r = rand(0, 0.035);
      add(s * 0.21 + Math.cos(a) * r * 1.4, 1.73 + Math.sin(a) * r * 0.7, 0.62, 2);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("seed", new THREE.Float32BufferAttribute(seed, 1));
    g.setAttribute("kind", new THREE.Float32BufferAttribute(kind, 1));
    return g;
  }

  const vertex = `
    uniform float uTime; uniform float uTalk; uniform float uListen;
    attribute float seed; attribute float kind;
    varying float vAlpha; varying float vKind;
    void main() {
      vec3 p = position;
      if (kind == 1.0) { p.y += (seed - 0.5) * uTalk * 0.07; }
      p.x += sin(uTime * 7.0 + p.y * 40.0) * 0.004 * (1.0 + uListen);
      vec4 mv = modelViewMatrix * vec4(p, 1.0);
      float scan = smoothstep(0.08, 0.0, abs(fract(p.y * 0.35 - uTime * 0.25) - 0.5));
      float flicker = 0.55 + 0.45 * sin(uTime * 3.0 + seed * 40.0);
      vAlpha = (0.25 + 0.45 * flicker + scan * 0.8) * (kind > 0.0 ? 1.6 : 1.0) * smoothstep(-1.0, -0.15, p.y);
      vKind = kind;
      gl_PointSize = (kind == 2.0 ? 3.2 : 2.2) * (6.0 / -mv.z);
      gl_Position = projectionMatrix * mv;
    }`;
  const fragment = `
    uniform vec3 uColor; varying float vAlpha; varying float vKind;
    void main() {
      vec2 c = gl_PointCoord - 0.5; float d = dot(c, c);
      if (d > 0.25) discard;
      vec3 col = vKind == 2.0 ? vec3(0.6, 1.0, 1.0) : uColor;
      gl_FragColor = vec4(col, vAlpha * (1.0 - d * 4.0));
    }`;

  function startHologram() {
    const host = $("holo");
    if (!window.THREE) { $("state").textContent = "hologram unavailable"; return; }
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    host.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100);
    camera.position.set(0, 1.3, 7.2);
    camera.lookAt(0, 0.85, 0);

    const uniforms = { uTime: { value: 0 }, uTalk: { value: 0 }, uListen: { value: 0 },
                       uColor: { value: new THREE.Color(0x4ff3ff) } };
    const mat = new THREE.ShaderMaterial({ uniforms, vertexShader: vertex, fragmentShader: fragment,
                                           transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
    const figure = new THREE.Points(buildFigure(), mat);
    figure.position.y = -0.1;
    scene.add(figure);

    // Projector base: glowing rings and a faint light cone.
    const ringMat = new THREE.LineBasicMaterial({ color: 0x4ff3ff, transparent: true, opacity: 0.6,
                                                  blending: THREE.AdditiveBlending });
    const rings = [];
    [0.9, 1.25, 1.6].forEach((r, i) => {
      const pts = [];
      for (let a = 0; a <= 128; a++) { const t = a / 128 * Math.PI * 2; pts.push(new THREE.Vector3(Math.cos(t) * r, 0, Math.sin(t) * r)); }
      const ring = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), ringMat.clone());
      ring.position.y = -1.05 - i * 0.02; scene.add(ring); rings.push(ring);
    });
    const cone = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 1.6, 3.4, 48, 1, true),
      new THREE.MeshBasicMaterial({ color: 0x4ff3ff, transparent: true, opacity: 0.05, side: THREE.DoubleSide,
                                    blending: THREE.AdditiveBlending, depthWrite: false }));
    cone.position.y = 0.65; scene.add(cone);
    const dust = new THREE.BufferGeometry(), dp = [];
    for (let i = 0; i < 600; i++) dp.push(rand(-2.5, 2.5), rand(-1, 3), rand(-2, 2));
    dust.setAttribute("position", new THREE.Float32BufferAttribute(dp, 3));
    const dustPts = new THREE.Points(dust, new THREE.PointsMaterial({ color: 0x4ff3ff, size: 0.015, transparent: true, opacity: 0.5 }));
    scene.add(dustPts);

    let mx = 0, my = 0;
    host.addEventListener("pointermove", (e) => {
      const r = host.getBoundingClientRect();
      mx = (e.clientX - r.left) / r.width - 0.5; my = (e.clientY - r.top) / r.height - 0.5;
    });
    function resize() {
      const w = host.clientWidth, h = host.clientHeight;
      renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
    }
    window.addEventListener("resize", resize); resize();

    const clock = new THREE.Clock();
    (function frame() {
      const t = clock.getElapsedTime();
      if (state.speaking) state.talk = Math.max(state.talk, 0.35 + 0.35 * Math.abs(Math.sin(t * 11)));
      state.talk *= 0.9;
      uniforms.uTime.value = t; uniforms.uTalk.value = state.talk;
      uniforms.uListen.value += ((state.listening ? 1 : 0) - uniforms.uListen.value) * 0.1;
      figure.rotation.y += (mx * 0.6 + Math.sin(t * 0.4) * 0.12 - figure.rotation.y) * 0.05;
      figure.rotation.x += (my * 0.15 - figure.rotation.x) * 0.05;
      figure.position.y = -0.1 + Math.sin(t * 1.2) * 0.02;
      rings.forEach((r, i) => { r.rotation.y = t * (0.3 + i * 0.15) * (i % 2 ? -1 : 1);
        r.material.opacity = 0.35 + 0.3 * Math.sin(t * (state.listening ? 6 : 2) + i); });
      dustPts.rotation.y = t * 0.03;
      renderer.render(scene, camera);
      requestAnimationFrame(frame);
    })();
  }

  // ---------- Voice out ----------
  let voice = null;
  const PREFERRED = ["samantha", "victoria", "karen", "moira", "tessa", "serena", "female", "zira", "aria", "jenny",
                     "libby", "sonia", "natasha", "google uk english female", "google us english"];
  function pickVoice() {
    if (!("speechSynthesis" in window)) return;
    const voices = speechSynthesis.getVoices().filter((v) => /^en/i.test(v.lang));
    for (const name of PREFERRED) { const v = voices.find((x) => x.name.toLowerCase().includes(name)); if (v) { voice = v; return; } }
    voice = voices[0] || null;
  }
  if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }

  function speak(text) {
    if (!$("voiceOn").checked || !("speechSynthesis" in window)) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    if (voice) u.voice = voice;
    u.rate = 1.0; u.pitch = 1.08;
    u.onstart = () => { state.speaking = true; setState("speaking"); pauseListening(true); };
    u.onboundary = () => { state.talk = 1; };
    u.onend = u.onerror = () => { state.speaking = false; setState(state.listening ? "listening" : "ready"); pauseListening(false); };
    speechSynthesis.speak(u);
  }

  // ---------- Voice in ----------
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null, wantListen = false, paused = false;

  function setState(s) { $("state").textContent = s; }

  function makeRecognizer(onText, continuous) {
    const r = new Recognition();
    r.lang = "en-US"; r.continuous = continuous; r.interimResults = false;
    r.onresult = (e) => { const res = e.results[e.results.length - 1]; if (res.isFinal) onText(res[0].transcript.trim()); };
    return r;
  }

  function startListening() {
    if (!Recognition) { note("Voice input isn't supported in this browser. Chrome, Edge and Safari support it; you can still type."); return; }
    wantListen = true;
    rec = makeRecognizer(onHeard, true);
    rec.onstart = () => { state.listening = true; setState("listening"); };
    rec.onend = () => { state.listening = false; if (wantListen && !paused) { try { rec.start(); } catch (e) { /* already started */ } } else setState("ready"); };
    rec.onerror = (e) => { if (e.error === "not-allowed") { wantListen = false; note("Microphone permission was refused."); } };
    try { rec.start(); } catch (e) { /* ignore */ }
    $("mic").setAttribute("aria-pressed", "true"); $("mic").textContent = "■ Stop";
  }
  function stopListening() {
    wantListen = false; if (rec) rec.stop();
    $("mic").setAttribute("aria-pressed", "false"); $("mic").textContent = "🎙 Listen"; setState("ready");
  }
  function pauseListening(p) { // don't hear her own voice
    paused = p;
    if (!rec || !wantListen) return;
    if (p) rec.stop(); else { try { rec.start(); } catch (e) { /* ignore */ } }
  }

  function onHeard(text) {
    if (!text) return;
    if ($("wake").checked) {
      const m = text.match(/\b(jarvis|jervis|travis)\b[\s,.:!?]*(.*)$/i);
      if (!m) return; // not for her
      text = m[2] || "hello";
    }
    ask(text);
  }

  // ---------- Server ----------
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                  credentials: "same-origin", body: JSON.stringify(body) });
    let data = {}; try { data = await r.json(); } catch (e) { /* ignore */ }
    return { ok: r.ok, status: r.status, data };
  }

  function add(cls, html) {
    const d = document.createElement("div"); d.className = "msg " + cls; d.innerHTML = html;
    $("log").prepend(d);
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function note(text) { add("her", '<span class="err">' + esc(text) + "</span>"); }

  async function ask(q) {
    add("me", esc(q)); setState("thinking");
    const r = await post("/api/jarvis/ask", { question: q });
    if (r.status === 403 || r.status === 401) { await refresh(); note(r.data.error || "Locked."); return; }
    if (!r.ok) { note(r.data.error || "Something went wrong."); setState("ready"); return; }
    add("her", "<b>JARVIS</b> " + esc(r.data.reply));
    speak(r.data.reply);
    if (!state.speaking) setState(state.listening ? "listening" : "ready");
  }

  async function unlock(pass) {
    $("lockMsg").textContent = "checking…";
    const r = await post("/api/jarvis/unlock", { passphrase: pass });
    $("pass").value = "";
    if (r.ok) { $("lockMsg").textContent = ""; await refresh(); speak("Hello. I'm here. What would you like to know?"); }
    else $("lockMsg").textContent = r.data.error || "That didn't work.";
  }

  async function refresh() {
    const r = await fetch("/api/jarvis", { credentials: "same-origin" });
    if (r.status === 401) { location.href = "/login"; return; }
    const s = await r.json();
    state.enabled = s.enabled; state.unlocked = s.unlocked;
    $("off").classList.toggle("hidden", s.enabled);
    $("offMsg").textContent = s.reason || "";
    $("setup").classList.toggle("hidden", !s.needs_setup);
    $("lock").classList.toggle("hidden", !s.enabled || s.unlocked || s.needs_setup);
    $("talk").classList.toggle("hidden", !s.unlocked);
    $("brain").textContent = s.unlocked ? (s.brain === "claude" ? "Brain: Claude" : "Brain: built-in handbook (add ANTHROPIC_API_KEY for Claude)") : "";
    if (!s.unlocked && wantListen) stopListening();
    setState(!s.enabled ? "offline" : s.unlocked ? "ready" : s.needs_setup ? "choose a passphrase" : "locked");
  }

  // ---------- Wire up ----------
  $("setupForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const a = $("newPass").value, b = $("newPass2").value;
    if (a !== b) { $("setupMsg").textContent = "The two don't match."; return; }
    const r = await post("/api/jarvis/setup", { passphrase: a });
    $("newPass").value = $("newPass2").value = "";
    if (r.ok) { $("setupMsg").textContent = ""; await refresh(); speak("Thank you. I'll answer only to that phrase. What would you like to know?"); }
    else $("setupMsg").textContent = r.data.error || "That didn't work.";
  });
  $("unlockForm").addEventListener("submit", (e) => { e.preventDefault(); if ($("pass").value) unlock($("pass").value); });
  $("sayPass").addEventListener("click", () => {
    if (!Recognition) { $("lockMsg").textContent = "Voice isn't supported in this browser; type it instead."; return; }
    const r = makeRecognizer((t) => unlock(t), false);
    r.onstart = () => { $("lockMsg").textContent = "listening for your passphrase…"; state.listening = true; };
    r.onend = () => { state.listening = false; };
    r.start();
  });
  $("askForm").addEventListener("submit", (e) => { e.preventDefault(); const q = $("q").value.trim(); if (q) { $("q").value = ""; ask(q); } });
  $("mic").addEventListener("click", () => (wantListen ? stopListening() : startListening()));
  $("lockBtn").addEventListener("click", async () => { stopListening(); await post("/api/jarvis/lock", {}); refresh(); });

  try { startHologram(); } catch (e) { $("state").textContent = "hologram unavailable"; }
  refresh();
})();
