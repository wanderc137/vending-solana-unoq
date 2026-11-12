/* Comunidad del proyecto: FAQ, preguntas a Discussions y monedas anónimas. */
(() => {
  "use strict";

  const CFG = window.PORTFOLIO_COMMUNITY || {};
  const account = CFG.account || "wanderc137";
  const repo = CFG.repo || location.pathname.split("/").filter(Boolean)[0] || "proyecto";
  const category = CFG.discussionCategory || "q-a";
  const coinNamespace = CFG.coinNamespace || `${account}-${repo}`;
  const coinApi = (CFG.coinApi || "https://abacus.jasoncameron.dev").replace(/\/$/, "");
  const faqs = Array.isArray(CFG.faqs) ? CFG.faqs : [];
  const COOLDOWN_MS = 24 * 60 * 60 * 1000;
  const STORE_KEY = `psc-coin-${account}-${repo}`;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

  const discussionUrl = (name, question) => {
    const clean = question.trim().replace(/\r/g, "");
    const title = clean.replace(/\s+/g, " ").slice(0, 92);
    const body = [
      `**Pregunta de:** ${name || "Anónimo"}`,
      "",
      clean,
      "",
      "---",
      `_Enviada desde la página de ${repo}._`,
    ].join("\n");
    return `https://github.com/${account}/${repo}/discussions/new?category=${encodeURIComponent(category)}&title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
  };

  const faqHtml = faqs.map((item, index) => `
    <details class="psc-faq-item"${index === 0 ? " open" : ""}>
      <summary>
        <span>${esc(item.q)}</span>
        <span class="psc-plus" aria-hidden="true">+</span>
      </summary>
      <div class="psc-faq-body">${item.a}</div>
    </details>`).join("");

  const section = document.createElement("section");
  section.className = "psc-section";
  section.id = "comunidad";
  section.innerHTML = `
    <div class="wrap">
      <div class="psc-head">
        <div class="psc-kicker">Comunidad · ${esc(repo)}</div>
        <h2>Preguntas reales.<br><span class="psc-grad">Reconocimiento anónimo.</span></h2>
        <p>Lo que más se pregunta sobre este proyecto, un canal para dejar tu pregunta y una moneda simbólica que no queda atada a tu cuenta.</p>
      </div>

      <div class="psc-faq" aria-label="Preguntas frecuentes">
        ${faqHtml}
      </div>

      <div class="psc-grid">
        <div class="psc-card">
          <h3>Dejá tu pregunta</h3>
          <p class="psc-sub">Se abre GitHub Discussions con el texto precargado. Necesitás una cuenta de GitHub para publicarla; si no tenés, podés copiar el texto y enviarlo por otro canal.</p>
          <form id="psc-form" novalidate>
            <div class="psc-field">
              <label for="psc-name">Nombre o pseudónimo <span>opcional</span></label>
              <input id="psc-name" name="name" type="text" maxlength="60" placeholder="¿Cómo querés firmar?" autocomplete="nickname">
            </div>
            <div class="psc-field">
              <label for="psc-question">Tu pregunta <span>obligatoria</span></label>
              <textarea id="psc-question" name="question" maxlength="1200" required placeholder="¿Cómo se valida el pago antes de dispensar? ¿Qué pasa si el RPC no responde?"></textarea>
            </div>
            <div class="psc-form-foot">
              <span class="psc-count" id="psc-count">0 / 1200</span>
              <div class="psc-actions">
                <button class="psc-btn" type="button" id="psc-clear">Limpiar</button>
                <button class="psc-btn primary" type="submit" id="psc-send">Enviar pregunta
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
                </button>
              </div>
            </div>
            <p class="psc-status" id="psc-status" role="status" aria-live="polite"></p>
            <p class="psc-note">La pregunta se publica en <a href="https://github.com/${account}/${repo}/discussions" target="_blank" rel="noopener">GitHub Discussions</a>. El repositorio puede notificarte por correo cuando se crea una discusión nueva.</p>
          </form>
        </div>

        <aside class="psc-card psc-coin" aria-labelledby="psc-coin-title">
          <div class="psc-coin-top">
            <div class="psc-coin-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="#5a3c00" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="9"></circle>
                <path d="M12 6.5v11M9 9.2h4.2a2.3 2.3 0 0 1 0 4.6H9.8a2.3 2.3 0 0 0 0 4.6H14"></path>
              </svg>
            </div>
            <div class="psc-coin-count" id="psc-coin-count" aria-live="polite">—</div>
            <div class="psc-coin-label" id="psc-coin-title">monedas anónimas</div>
          </div>
          <p class="psc-coin-copy">No es una estrella: no queda asociada a tu usuario. Es un contador global y anónimo para reconocer el proyecto.</p>
          <button class="psc-btn primary psc-coin-btn" type="button" id="psc-coin-btn">Regalar 1 moneda</button>
          <p class="psc-coin-msg" id="psc-coin-msg" role="status" aria-live="polite">Cargando el contador…</p>
          <p class="psc-coin-small">Una moneda por navegador cada 24 h. <strong>Sin cuenta, sin correo, sin tracking.</strong></p>
        </aside>
      </div>
    </div>`;

  const main = document.querySelector("main");
  const cta = main && main.querySelector(".cta");
  if (main && cta) main.insertBefore(section, cta);
  else if (main) main.appendChild(section);
  else document.body.appendChild(section);

  const form = section.querySelector("#psc-form");
  const nameInput = section.querySelector("#psc-name");
  const questionInput = section.querySelector("#psc-question");
  const count = section.querySelector("#psc-count");
  const status = section.querySelector("#psc-status");
  const clear = section.querySelector("#psc-clear");
  const send = section.querySelector("#psc-send");
  const coinCount = section.querySelector("#psc-coin-count");
  const coinBtn = section.querySelector("#psc-coin-btn");
  const coinMsg = section.querySelector("#psc-coin-msg");

  const setStatus = (text, kind = "") => {
    status.textContent = text;
    status.className = `psc-status${kind ? ` ${kind}` : ""}`;
  };

  const updateCount = () => {
    count.textContent = `${questionInput.value.length} / 1200`;
  };

  questionInput.addEventListener("input", updateCount);
  updateCount();

  clear.addEventListener("click", () => {
    nameInput.value = "";
    questionInput.value = "";
    updateCount();
    setStatus("");
    questionInput.focus();
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = questionInput.value.trim();
    const name = nameInput.value.trim();
    if (question.length < 8) {
      setStatus("Escribí una pregunta un poco más completa (mínimo 8 caracteres).", "bad");
      questionInput.focus();
      return;
    }
    const url = discussionUrl(name, question);
    const win = window.open(url, "_blank", "noopener,noreferrer");
    if (win) {
      setStatus("Abrimos GitHub Discussions con tu pregunta precargada. Revisala y publicala.", "ok");
    } else {
      setStatus("El navegador bloqueó la ventana. Abrí GitHub Discussions manualmente.", "bad");
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = " Abrir la pregunta en GitHub →";
      link.style.color = "var(--accent)";
      link.style.textDecoration = "underline";
      status.appendChild(link);
    }
  });

  const setCoinMessage = (text, kind = "") => {
    coinMsg.textContent = text;
    coinMsg.className = `psc-coin-msg${kind ? ` ${kind}` : ""}`;
  };

  const formatCoins = (n) => {
    try { return new Intl.NumberFormat("es-CL").format(n); }
    catch { return String(n); }
  };

  const readCooldown = () => {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (!raw) return 0;
      const ts = Number(JSON.parse(raw).ts || 0);
      return Number.isFinite(ts) ? ts : 0;
    } catch { return 0; }
  };

  const writeCooldown = () => {
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ ts: Date.now() })); }
    catch { /* modo privado del navegador */ }
  };

  const cooldownLeft = () => {
    const elapsed = Date.now() - readCooldown();
    return elapsed < COOLDOWN_MS ? COOLDOWN_MS - elapsed : 0;
  };

  const applyCooldown = () => {
    const left = cooldownLeft();
    if (!left) {
      coinBtn.disabled = false;
      coinBtn.textContent = "Regalar 1 moneda";
      return false;
    }
    const hours = Math.max(1, Math.ceil(left / 3600000));
    coinBtn.disabled = true;
    coinBtn.textContent = `Gracias · volvé en ${hours} h`;
    setCoinMessage("Ya regalaste una moneda desde este navegador. Gracias por el gesto.", "ok");
    return true;
  };

  const loadCoins = async () => {
    try {
      const res = await fetch(`${coinApi}/get/${encodeURIComponent(coinNamespace)}/coins`, { cache: "no-store" });
      if (res.status === 404) {
        coinCount.textContent = "0";
        setCoinMessage("Todavía no hay monedas. Podés ser la primera persona en regalar una.");
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (typeof data.value !== "number") throw new Error("respuesta inválida");
      coinCount.textContent = formatCoins(data.value);
      setCoinMessage(`${formatCoins(data.value)} ${data.value === 1 ? "reconocimiento anónimo" : "reconocimientos anónimos"}.`);
    } catch {
      coinCount.textContent = "—";
      setCoinMessage("El contador no está disponible ahora. Podés intentar de nuevo en un momento.", "bad");
    }
  };

  coinBtn.addEventListener("click", async () => {
    if (applyCooldown()) return;
    coinBtn.disabled = true;
    setCoinMessage("Registrando tu moneda…");
    try {
      const res = await fetch(`${coinApi}/hit/${encodeURIComponent(coinNamespace)}/coins`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (typeof data.value !== "number") throw new Error("respuesta inválida");
      coinCount.textContent = formatCoins(data.value);
      writeCooldown();
      setCoinMessage("Moneda registrada. Gracias por reconocer el proyecto.", "ok");
      coinBtn.textContent = "Gracias · volvé en 24 h";
      coinCount.animate(
        [{ transform: "scale(1)" }, { transform: "scale(1.12)" }, { transform: "scale(1)" }],
        { duration: 420, easing: "ease-out" }
      );
    } catch {
      coinBtn.disabled = false;
      setCoinMessage("No pudimos registrar la moneda. Revisá la conexión e intentá otra vez.", "bad");
    }
  });

  applyCooldown();
  loadCoins();
})();
