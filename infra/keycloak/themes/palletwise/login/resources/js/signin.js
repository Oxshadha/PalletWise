// PalletWise sign-in: the floor animation, the password toggle and the demo account cards.
(function () {
  var reveal = document.getElementById("pw-reveal");
  var password = document.getElementById("password");
  if (reveal && password) {
    reveal.addEventListener("click", function () {
      var shown = password.type === "text";
      password.type = shown ? "password" : "text";
      reveal.textContent = shown ? "Show" : "Hide";
      reveal.setAttribute("aria-pressed", String(!shown));
      reveal.setAttribute("aria-label", shown ? "Show password" : "Hide password");
    });
  }

  // Demo accounts: written by the setup script into demo-accounts.js on this installation only.
  var accounts = window.PW_DEMO_ACCOUNTS || [];
  var cards = document.getElementById("pw-demo-cards");
  if (accounts.length && cards) {
    document.getElementById("pw-demo").hidden = false;
    accounts.forEach(function (a) {
      var card = document.createElement("button");
      card.type = "button";
      card.className = "pw-demo-card";
      var role = document.createElement("strong");
      role.textContent = a.role;
      var user = document.createElement("span");
      user.textContent = "User: " + a.username;
      var pass = document.createElement("span");
      pass.textContent = "Password: " + a.password;
      var hint = document.createElement("em");
      hint.textContent = "Use this account";
      card.append(role, user, pass, hint);
      card.addEventListener("click", function () {
        document.getElementById("username").value = a.username;
        password.value = a.password;
        document.getElementById("kc-login").focus();
      });
      cards.appendChild(card);
    });
  }

  // A dot grid shaped like a warehouse floor with picking routes moving across it.
  var canvas = document.getElementById("pw-floor");
  var ctx = canvas && canvas.getContext("2d");
  if (!ctx) return;
  var still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var routes = [
    [[0.1, 0.12], [0.46, 0.26], 0], [[0.46, 0.26], [0.88, 0.14], 2],
    [[0.14, 0.88], [0.52, 0.76], 1], [[0.86, 0.9], [0.6, 0.78], 0.5]
  ];
  var dots = [], w = 0, h = 0;
  function layout() {
    var r = window.devicePixelRatio || 1;
    w = canvas.clientWidth; h = canvas.clientHeight;
    canvas.width = w * r; canvas.height = h * r;
    ctx.setTransform(r, 0, 0, r, 0, 0);
    dots = [];
    for (var x = 12; x < w; x += 12)
      for (var y = 12; y < h; y += 12)
        if (Math.floor(y / 48) % 2 === 0 && x > w * 0.06 && x < w * 0.94 && Math.random() > 0.25)
          dots.push([x, y, 0.15 + Math.random() * 0.35]);
  }
  function draw(t) {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#0875ed"; ctx.strokeStyle = "#0875ed"; ctx.lineWidth = 1.5;
    dots.forEach(function (d) { ctx.globalAlpha = d[2]; ctx.beginPath(); ctx.arc(d[0], d[1], 1, 0, 7); ctx.fill(); });
    ctx.globalAlpha = 1;
    routes.forEach(function (r) {
      var p = still ? 1 : Math.min(Math.max((t - r[2]) / 3, 0), 1);
      if (p <= 0) return;
      var fx = r[0][0] * w, fy = r[0][1] * h, tx = r[1][0] * w, ty = r[1][1] * h;
      var x = fx + (tx - fx) * p, y = fy + (ty - fy) * p;
      ctx.beginPath(); ctx.moveTo(fx, fy); ctx.lineTo(x, y); ctx.stroke();
      [[fx, fy, 3, 1], [x, y, 3, 1], [x, y, 7, 0.25]].forEach(function (c) {
        ctx.globalAlpha = c[3]; ctx.beginPath(); ctx.arc(c[0], c[1], c[2], 0, 7); ctx.fill();
      });
      ctx.globalAlpha = 1;
    });
  }
  layout();
  window.addEventListener("resize", layout);
  var start = performance.now();
  (function tick(now) {
    draw(((now - start) / 1000) % 12);
    if (!still) requestAnimationFrame(tick);
  })(start);
})();
