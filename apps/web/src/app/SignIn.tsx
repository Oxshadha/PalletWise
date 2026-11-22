import { useEffect, useRef } from "react";

import { signIn } from "../shared/api/client";
import { Icon } from "../shared/ui/Icon";

interface Route {
  from: [number, number];
  to: [number, number];
  delay: number;
}

/* Picks between rack aisles of a warehouse floor, in fractions of the panel. */
const ROUTES: Route[] = [
  { from: [0.1, 0.12], to: [0.46, 0.26], delay: 0 },
  { from: [0.46, 0.26], to: [0.88, 0.14], delay: 2 },
  { from: [0.14, 0.88], to: [0.52, 0.76], delay: 1 },
  { from: [0.86, 0.9], to: [0.6, 0.78], delay: 0.5 },
];
const CYCLE_SECONDS = 12;
const TRAVEL_SECONDS = 3;

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * A dot grid shaped like a warehouse floor (rack rows with aisles) with picking routes moving
 * across it. Decorative: hidden from assistive technology; still when reduced motion is preferred.
 */
function FloorRoutes() {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext("2d");
    if (!element || !context) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let frame = 0;
    let width = 0;
    let height = 0;
    let dots: { x: number; y: number; alpha: number }[] = [];
    const accent = token("--pw-color-accent") || "currentColor";

    const layout = () => {
      const ratio = window.devicePixelRatio || 1;
      width = element.clientWidth;
      height = element.clientHeight;
      element.width = width * ratio;
      element.height = height * ratio;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      dots = [];
      for (let x = 12; x < width; x += 12) {
        for (let y = 12; y < height; y += 12) {
          // Rack rows: bands of dots with aisles between them.
          const inRack = Math.floor(y / 48) % 2 === 0 && x > width * 0.06 && x < width * 0.94;
          if (inRack && Math.random() > 0.25)
            dots.push({ x, y, alpha: 0.15 + Math.random() * 0.35 });
        }
      }
    };

    const draw = (seconds: number) => {
      context.clearRect(0, 0, width, height);
      context.fillStyle = accent;
      for (const dot of dots) {
        context.globalAlpha = dot.alpha;
        context.beginPath();
        context.arc(dot.x, dot.y, 1, 0, Math.PI * 2);
        context.fill();
      }
      context.globalAlpha = 1;
      context.strokeStyle = accent;
      context.lineWidth = 1.5;
      for (const route of ROUTES) {
        const progress = still
          ? 1
          : Math.min(Math.max((seconds - route.delay) / TRAVEL_SECONDS, 0), 1);
        if (progress <= 0) continue;
        const [fx, fy] = [route.from[0] * width, route.from[1] * height];
        const [tx, ty] = [route.to[0] * width, route.to[1] * height];
        const [x, y] = [fx + (tx - fx) * progress, fy + (ty - fy) * progress];
        context.beginPath();
        context.moveTo(fx, fy);
        context.lineTo(x, y);
        context.stroke();
        for (const [px, py, r, a] of [
          [fx, fy, 3, 1],
          [x, y, 3, 1],
          [x, y, 7, 0.25],
        ] as const) {
          context.globalAlpha = a;
          context.beginPath();
          context.arc(px, py, r, 0, Math.PI * 2);
          context.fill();
        }
        context.globalAlpha = 1;
      }
    };

    layout();
    const resize = new ResizeObserver(() => {
      layout();
      if (still) draw(0);
    });
    resize.observe(element);
    const started = performance.now();
    const tick = (now: number) => {
      draw(((now - started) / 1000) % CYCLE_SECONDS);
      frame = requestAnimationFrame(tick);
    };
    if (still) draw(0);
    else frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
    };
  }, []);

  return <canvas ref={canvas} className="pw-signin-canvas" aria-hidden="true" />;
}

/**
 * The sign-in screen (identity spec 4): shown when there is no session or a sign-in failed. The
 * button starts sign-in at the API, which hands over to the identity provider's page for the email
 * and password; this page never sees a password or a token.
 */
export function SignIn({ error }: { error: string | null }) {
  return (
    <main className="pw-signin">
      <section className="pw-signin-card" aria-labelledby="pw-signin-title">
        <div className="pw-signin-art">
          <FloorRoutes />
          <div className="pw-signin-brand">
            <span
              className="pw-brand-logo pw-signin-logo"
              role="img"
              aria-label="PalletWise logo"
            />
            <p className="pw-signin-name">PalletWise</p>
            <p className="pw-muted">
              The warehouse copilot: every location, order and exception, answered on the map.
            </p>
          </div>
        </div>
        <div className="pw-signin-form">
          <h1 id="pw-signin-title">Welcome back</h1>
          <p className="pw-muted">Sign in to your warehouse</p>
          {error && (
            <p className="pw-signin-error" role="alert">
              {error}
            </p>
          )}
          <button type="button" className="pw-button pw-signin-button" onClick={signIn}>
            Sign in <Icon name="arrow" size={16} />
          </button>
          <p className="pw-signin-note">
            You sign in with your organisation account on the secure sign-in page; PalletWise never
            sees your password.
          </p>
        </div>
      </section>
    </main>
  );
}
