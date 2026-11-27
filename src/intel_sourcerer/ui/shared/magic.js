"use strict";
// Balanced, compositor-friendly arcane dissolve, summon, mastery, and casting effects for Intel Sourcerer.
(() => {
  const STORAGE_KEY = "intel-magic";
  const VALID_MODES = new Set(["balanced", "subtle", "off"]);
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function getMode() {
    try {
      const value = localStorage.getItem(STORAGE_KEY);
      return VALID_MODES.has(value) ? value : "balanced";
    } catch {
      return "balanced";
    }
  }

  function applyMode() {
    document.documentElement.dataset.magic = getMode();
  }

  applyMode();

  let canvas = null;
  let ctx = null;
  let rafId = 0;
  let lastTime = 0;
  const particles = [];
  const orbs = [];

  function ensureCanvas() {
    if (canvas && canvas.isConnected) return canvas;
    if (!document.body) return null;
    canvas = document.createElement("canvas");
    canvas.className = "magic-canvas";
    canvas.setAttribute("aria-hidden", "true");
    document.body.append(canvas);
    ctx = canvas.getContext("2d");
    resizeCanvas();
    window.addEventListener("resize", resizeCanvas, { passive: true });
    return canvas;
  }

  function resizeCanvas() {
    if (!canvas || !ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = window.innerWidth;
    const height = window.innerHeight;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function isDark() {
    return document.documentElement.dataset.theme === "dark";
  }

  function paletteFor(tone) {
    const dark = isDark();
    if (tone === "smoke") {
      return dark
        ? ["#9fb0c9", "#8650b8", "#c5d4ea", "#64748b"]
        : ["#64748b", "#7946d9", "#94a0b2", "#4a2fb0"];
    }
    if (tone === "emerald") {
      return dark
        ? ["#4ade80", "#34d399", "#ffe082", "#88b5fa"]
        : ["#08734c", "#21804c", "#d98a12", "#2864ec"];
    }
    if (tone === "gold") {
      return dark
        ? ["#ffe082", "#91b2fb", "#c4a3ff", "#ffffff"]
        : ["#d98a12", "#2864ec", "#7946d9", "#b36b10"];
    }
    // Default "arcane" / "clear" palette harmonized with Intel Sourcerer cobalt + violet + gold spark.
    return dark
      ? ["#88b5fa", "#a78bfa", "#ffe082", "#c6ddff"]
      : ["#2864ec", "#7946d9", "#4a2fb0", "#d98a12"];
  }

  function drawStar(c, x, y, r) {
    c.beginPath();
    for (let i = 0; i < 4; i++) {
      const a = (i * Math.PI) / 2;
      const ox = x + Math.cos(a) * r;
      const oy = y + Math.sin(a) * r;
      const ix = x + Math.cos(a + Math.PI / 4) * (r * 0.34);
      const iy = y + Math.sin(a + Math.PI / 4) * (r * 0.34);
      if (i === 0) c.moveTo(ox, oy);
      else c.lineTo(ox, oy);
      c.lineTo(ix, iy);
    }
    c.closePath();
    c.fill();
  }

  function startLoop() {
    if (!rafId) {
      lastTime = performance.now();
      rafId = requestAnimationFrame(tick);
    }
  }

  function tick(now) {
    rafId = 0;
    if (!ctx || !canvas) return;
    const dt = Math.min(0.05, Math.max(0.008, (now - lastTime) / 1000));
    lastTime = now;

    ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);

    // Update flying summoning orbs first so their trails spawn into particles.
    for (let i = orbs.length - 1; i >= 0; i--) {
      const orb = orbs[i];
      orb.age += dt;
      const t = Math.min(1, orb.age / orb.duration);
      const ease = 1 - Math.pow(1 - t, 3);
      const inv = 1 - ease;
      const x = inv * inv * orb.sx + 2 * inv * ease * orb.cx + ease * ease * orb.tx;
      const y = inv * inv * orb.sy + 2 * inv * ease * orb.cy + ease * ease * orb.ty;

      // Spawn delicate stardust trail behind the orb.
      orb.trailAccum += dt;
      if (orb.trailAccum >= 0.018 && t < 0.96) {
        orb.trailAccum = 0;
        const colors = orb.colors;
        particles.push({
          x: x + (Math.random() - 0.5) * 4,
          y: y + (Math.random() - 0.5) * 4,
          vx: (Math.random() - 0.5) * 18,
          vy: (Math.random() - 0.5) * 18 - 8,
          gravity: -6,
          drift: 10,
          swirl: 4,
          phase: Math.random() * Math.PI * 2,
          spin: (Math.random() - 0.5) * 5,
          size: 1.5 + Math.random() * 1.6,
          alpha: 0.72,
          life: 0.28 + Math.random() * 0.16,
          age: 0,
          shape: Math.random() < 0.45 ? "star" : "dot",
          color: colors[Math.floor(Math.random() * colors.length)],
        });
      }

      // Draw luminous orb head with soft outer halo.
      ctx.save();
      ctx.globalAlpha = 0.28 * (1 - t * 0.3);
      ctx.fillStyle = orb.colors[1];
      ctx.beginPath();
      ctx.arc(x, y, 9.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 0.95;
      ctx.fillStyle = orb.colors[0];
      drawStar(ctx, x, y, 5.8);
      ctx.restore();

      if (t >= 1) {
        orbs.splice(i, 1);
        if (typeof orb.onLand === "function") orb.onLand(orb.tx, orb.ty);
      }
    }

    for (let i = particles.length - 1; i >= 0; i--) {
      const p = particles[i];
      p.age += dt;
      if (p.age >= p.life) {
        particles.splice(i, 1);
        continue;
      }
      const progress = p.age / p.life;
      p.vx += Math.sin(p.age * p.swirl + p.phase) * p.drift * dt;
      p.vy += p.gravity * dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;

      const alpha = (1 - progress) * (1 - progress) * p.alpha;
      const radius = p.size * (1 - progress * 0.45);

      ctx.save();
      ctx.globalAlpha = Math.max(0, alpha);
      ctx.fillStyle = p.color;
      if (p.shape === "star") {
        ctx.translate(p.x, p.y);
        ctx.rotate(p.age * p.spin + p.phase);
        drawStar(ctx, 0, 0, radius * 1.45);
      } else if (p.shape === "wisp") {
        const wispR = p.size * (1 + progress * 1.5);
        ctx.beginPath();
        ctx.arc(p.x, p.y, wispR, 0, Math.PI * 2);
        ctx.fill();
      } else {
        ctx.beginPath();
        ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }

    if (particles.length > 0 || orbs.length > 0) {
      rafId = requestAnimationFrame(tick);
    }
  }

  function spawnParticles(rect, tone = "arcane", customCount = null) {
    const mode = getMode();
    if (mode === "off" || reducedMotion.matches) return;
    if (!rect || (rect.width === 0 && rect.height === 0)) return;
    if (!ensureCanvas()) return;

    const scale = mode === "subtle" ? 0.5 : 1;
    const areaFactor = Math.min(2.2, Math.max(0.65, Math.sqrt((rect.width * rect.height) / 14000)));
    const base = customCount ?? Math.round((tone === "clear" ? 36 : tone === "smoke" ? 18 : 24) * areaFactor);
    const total = Math.max(6, Math.round(base * scale));
    const colors = paletteFor(tone);

    for (let i = 0; i < total; i++) {
      const rx = rect.left + Math.random() * rect.width;
      const ry = rect.top + Math.random() * rect.height;
      const isSmoke = tone === "smoke";
      const isStar = !isSmoke && Math.random() < 0.36;
      const shape = isSmoke && Math.random() < 0.5 ? "wisp" : isStar ? "star" : "dot";
      particles.push({
        x: rx,
        y: ry,
        vx: (Math.random() - 0.5) * (isSmoke ? 44 : 36),
        vy: isSmoke ? -(12 + Math.random() * 28) : -(20 + Math.random() * 44),
        gravity: isSmoke ? -12 : -18,
        drift: isSmoke ? 14 : 26,
        swirl: 3.5 + Math.random() * 3.5,
        phase: Math.random() * Math.PI * 2,
        spin: (Math.random() - 0.5) * 4,
        size: shape === "wisp" ? 3.2 + Math.random() * 3.4 : 1.7 + Math.random() * 2.2,
        alpha: shape === "wisp" ? 0.24 : 0.78,
        life: (isSmoke ? 0.42 : 0.48) + Math.random() * 0.26,
        age: 0,
        shape,
        color: colors[i % colors.length],
      });
    }
    startLoop();
  }

  function spawnRadialBurst(cx, cy, tone = "emerald", count = 24, spread = 92) {
    const mode = getMode();
    if (mode === "off" || reducedMotion.matches) return;
    if (!ensureCanvas()) return;

    const scale = mode === "subtle" ? 0.5 : 1;
    const total = Math.max(6, Math.round(count * scale));
    const colors = paletteFor(tone);

    for (let i = 0; i < total; i++) {
      const angle = (i / total) * Math.PI * 2 + (Math.random() - 0.5) * 0.45;
      const speed = spread * (0.45 + Math.random() * 0.75);
      const isStar = Math.random() < 0.52;
      particles.push({
        x: cx,
        y: cy,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed - 18,
        gravity: 34,
        drift: 16,
        swirl: 4 + Math.random() * 3,
        phase: Math.random() * Math.PI * 2,
        spin: (Math.random() - 0.5) * 6,
        size: isStar ? 2.1 + Math.random() * 1.9 : 1.8 + Math.random() * 1.8,
        alpha: 0.88,
        life: 0.46 + Math.random() * 0.28,
        age: 0,
        shape: isStar ? "star" : "dot",
        color: colors[i % colors.length],
      });
    }
    startLoop();
  }

  function pulseElement(el, attr = "data-magic-pulse") {
    if (!el || getMode() === "off") return;
    el.removeAttribute(attr);
    void el.offsetWidth;
    el.setAttribute(attr, "");
    const cleanup = () => el.removeAttribute(attr);
    el.addEventListener("animationend", cleanup, { once: true });
    setTimeout(cleanup, 520);
  }

  async function waitAnimations(el, timeoutMs = 340) {
    if (!el || typeof el.getAnimations !== "function") return;
    const running = el.getAnimations();
    if (!running.length) return;
    await Promise.race([
      Promise.allSettled(running.map((anim) => anim.finished)),
      new Promise((resolve) => setTimeout(resolve, timeoutMs)),
    ]);
  }

  /**
   * Dissolve an element out of view with arcane dust motes before removing or re-rendering it.
   */
  async function banish(el, { tone = "arcane", count = null } = {}) {
    const mode = getMode();
    if (!el || mode === "off" || el.hasAttribute("data-banishing")) return;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return;

    spawnParticles(rect, tone, count);
    el.setAttribute("data-banishing", tone);
    await waitAnimations(el, reducedMotion.matches ? 160 : 340);
  }

  /**
   * Emit a quick particle puff at an element without hiding it.
   */
  function puff(el, { tone = "arcane", count = null } = {}) {
    if (!el) return;
    const rect = el.getBoundingClientRect();
    spawnParticles(rect, tone, count ?? 16);
  }

  /**
   * Launch a luminous arcane orb along a smooth arc from `fromEl` to `toEl` (e.g. + Queue -> Reading Queue).
   */
  function fly(fromEl, toEl, { tone = "arcane", delayMs = 0 } = {}) {
    const mode = getMode();
    if (mode === "off" || !fromEl) return;
    const sRect = fromEl.getBoundingClientRect();
    if (sRect.width === 0 && sRect.height === 0) return;

    const launch = () => {
      const target = typeof toEl === "function" ? toEl() : toEl;
      if (reducedMotion.matches) {
        if (target) pulseElement(target);
        return;
      }
      if (!ensureCanvas()) return;
      const sx = sRect.left + sRect.width / 2;
      const sy = sRect.top + sRect.height / 2;
      let tx = sx;
      let ty = Math.max(72, sy - 110);
      if (target) {
        const tRect = target.getBoundingClientRect();
        if (tRect.width > 0 && tRect.height > 0) {
          tx = Math.min(window.innerWidth - 24, Math.max(24, tRect.left + Math.min(tRect.width / 2, 120)));
          ty = Math.min(window.innerHeight - 24, Math.max(24, tRect.top + Math.min(tRect.height / 2, 28)));
        }
      }
      const dx = tx - sx;
      const dy = ty - sy;
      const arcLift = Math.min(110, Math.max(36, Math.hypot(dx, dy) * 0.24));
      const cx = (sx + tx) / 2 + (dx >= 0 ? -24 : 24);
      const cy = Math.min(sy, ty) - arcLift;

      orbs.push({
        sx,
        sy,
        cx,
        cy,
        tx,
        ty,
        age: 0,
        duration: 0.34,
        trailAccum: 0,
        colors: paletteFor(tone),
        onLand(lx, ly) {
          spawnRadialBurst(lx, ly, tone, 14, 62);
          const landEl = typeof toEl === "function" ? toEl() : toEl;
          if (landEl) pulseElement(landEl);
        },
      });
      startLoop();
    };

    if (delayMs > 0) setTimeout(launch, delayMs);
    else launch();
  }

  /**
   * Mastery seal / celebratory starlight burst (e.g. marking an article read or finishing a queue).
   */
  function celebrate(el, { tone = "emerald", intensity = 1 } = {}) {
    const mode = getMode();
    if (mode === "off") return;
    if (el) pulseElement(el, "data-magic-seal");
    if (reducedMotion.matches) return;

    let cx = window.innerWidth / 2;
    let cy = window.innerHeight * 0.42;
    if (el) {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        cx = rect.left + rect.width / 2;
        cy = rect.top + rect.height / 2;
      }
    }
    spawnRadialBurst(cx, cy, tone, Math.round(24 * intensity), 84 * Math.sqrt(intensity));
  }

  /**
   * Ongoing arcane "casting" aura for source refresh / checking feeds.
   * Call `finish(foundNew)` on success or `cancel()` on error.
   */
  function casting(el) {
    const mode = getMode();
    if (!el || mode === "off") {
      return { finish() {}, cancel() {} };
    }
    const brandIcon = document.querySelector(".brand-icon");
    el.setAttribute("data-casting", "");
    brandIcon?.setAttribute("data-casting-icon", "");

    let intervalId = 0;
    if (!reducedMotion.matches) {
      const emit = () => {
        if (!el.isConnected) return stop();
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          spawnParticles(rect, "arcane", mode === "subtle" ? 4 : 7);
        }
      };
      emit();
      intervalId = window.setInterval(emit, mode === "subtle" ? 900 : 560);
    }

    function stop() {
      if (intervalId) {
        clearInterval(intervalId);
        intervalId = 0;
      }
      el.removeAttribute("data-casting");
      brandIcon?.removeAttribute("data-casting-icon");
    }

    return {
      finish(foundNew = false) {
        stop();
        if (!reducedMotion.matches) {
          const rect = el.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) {
            spawnRadialBurst(
              rect.left + rect.width / 2,
              rect.top + rect.height / 2,
              foundNew ? "gold" : "arcane",
              foundNew ? 22 : 14,
              foundNew ? 82 : 58,
            );
          }
        }
        pulseElement(el);
      },
      cancel() {
        stop();
      },
    };
  }

  /**
   * Emit a crisp mini-sparkle burst at an exact viewport coordinate (e.g. ticking a checklist item).
   */
  function sparkPoint(x, y, { tone = "emerald", count = 12, spread = 46 } = {}) {
    spawnRadialBurst(x, y, tone, count, spread);
  }

  /**
   * Brief golden-ink or arcane-ink shimmer on a newly highlighted passage, inserted citation, or copied button.
   */
  function shimmer(el, { tone = "gold" } = {}) {
    const mode = getMode();
    if (!el || mode === "off") return;
    el.removeAttribute("data-magic-shimmer");
    void el.offsetWidth;
    el.setAttribute("data-magic-shimmer", tone);
    const cleanup = () => el.removeAttribute("data-magic-shimmer");
    el.addEventListener("animationend", cleanup, { once: true });
    setTimeout(cleanup, 650);
    if (!reducedMotion.matches) {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        spawnParticles(rect, tone, mode === "subtle" ? 5 : 10);
      }
    }
  }

  /**
   * Circular radial "eclipse" reveal when switching between Light and Dark mode via View Transitions API.
   * Falls back cleanly to an immediate theme update when unsupported, disabled, or under reduced motion.
   */
  function eclipseTheme(originEl, applyFn) {
    const mode = getMode();
    if (
      mode === "off" ||
      reducedMotion.matches ||
      typeof document.startViewTransition !== "function"
    ) {
      applyFn();
      return;
    }
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let x = vw - 48;
    let y = 28;
    if (originEl && typeof originEl.getBoundingClientRect === "function") {
      const rect = originEl.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        x = rect.left + rect.width / 2;
        y = rect.top + rect.height / 2;
      }
    }
    const radius = Math.hypot(Math.max(x, vw - x), Math.max(y, vh - y));
    const root = document.documentElement;
    root.style.setProperty("--eclipse-x", `${x}px`);
    root.style.setProperty("--eclipse-y", `${y}px`);
    root.style.setProperty("--eclipse-r", `${Math.ceil(radius)}px`);
    root.dataset.eclipse = "1";

    let transition;
    try {
      transition = document.startViewTransition(() => {
        applyFn();
      });
    } catch {
      delete root.dataset.eclipse;
      applyFn();
      return;
    }

    transition.ready
      .then(() => {
        spawnRadialBurst(x, y, "gold", mode === "subtle" ? 8 : 16, 66);
      })
      .catch(() => {});

    transition.finished.finally(() => {
      delete root.dataset.eclipse;
      root.style.removeProperty("--eclipse-x");
      root.style.removeProperty("--eclipse-y");
      root.style.removeProperty("--eclipse-r");
    });
  }

  window.intelMagic = {
    getMode,
    setMode(mode) {
      const next = VALID_MODES.has(mode) ? mode : "balanced";
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {}
      applyMode();
    },
    banish,
    puff,
    fly,
    celebrate,
    casting,
    sparkPoint,
    shimmer,
    eclipseTheme,
  };
})();
